"""Format conversion with explicit loss accounting.

Before writing, the source's property catalog is compared with the target format's
capabilities. Anything that cannot be represented is listed; the conversion is refused
unless ``allow_loss`` is set, and the list is stored with the conversion record.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import numpy as np
from sqlalchemy import select

from ..db.models import Calculation, File
from ..db.types import utcnow
from ..errors import DataLossError, InvalidInput
from ..formats.base import Capabilities, FormatAdapter
from ..formats.registry import detect, format_for_output, get_adapter
from ..frames import Catalog
from ..ids import is_id, new_id
from ..reproducibility import command_line, software_versions
from ..store.hashing import sha256_file
from .context import AtomDB
from .provenance import add_edge

CALC_CAP = {"energy": "energy", "free_energy": "energy", "forces": "forces", "stress": "stress"}
VELOCITY_KEYS = {"momenta", "velocities"}


def compute_losses(catalog: Catalog, caps: Capabilities, any_pbc: bool) -> list[str]:
    lost: list[str] = []
    for p in catalog.to_list():
        key = p.key
        if p.origin == "atoms":
            if p.name == "cell" and not caps.cell:
                lost.append(key)
            if p.name == "pbc" and not caps.pbc and any_pbc:
                lost.append(key)
        elif p.origin == "calc":
            cap = CALC_CAP.get(p.name, "calc_other")
            if not getattr(caps, cap):
                lost.append(key)
        elif p.origin == "info":
            if not caps.info or (p.dtype == "dict" and not caps.nested_info):
                lost.append(key)
        elif p.origin == "arrays":
            if p.name in VELOCITY_KEYS:
                if not caps.velocities:
                    lost.append(key)
            elif not caps.arrays:
                lost.append(key)
    return lost


def resolve_input(adb: AtomDB | None, src: str) -> tuple[Path, str | None]:
    """Accept a path or a dataset ID (uses the dataset's canonical H5MD)."""
    if is_id(src.upper()) and adb is not None:
        from .datasets import get_dataset

        with adb.session() as s:
            ds = get_dataset(s, src)
            return adb.store.abspath(ds.primary_file.store_relpath), ds.id
    p = Path(src).expanduser()
    if not p.is_file():
        raise InvalidInput(f"input not found: {src}")
    return p, None


def convert(
    adb: AtomDB | None,
    src: str,
    out: str | Path,
    to: str | None = None,
    frm: str | None = None,
    allow_loss: bool = False,
    overwrite: bool = False,
    read_options: dict | None = None,
    record: bool = True,
) -> dict:
    in_path, ds_id = resolve_input(adb, src)
    out = Path(out).expanduser()
    if out.exists() and not overwrite:
        raise InvalidInput(f"output exists: {out} (use --overwrite)")
    if out.resolve() == in_path.resolve():
        raise InvalidInput("output would overwrite the input")
    src_adapter: FormatAdapter = get_adapter(frm) if frm else detect(in_path)
    dst_adapter = format_for_output(out, to)
    if not dst_adapter.writable:
        raise InvalidInput(f"format {dst_adapter.name} is read-only")
    started = utcnow()

    # pass 1: catalog (streaming)
    source = src_adapter.open(in_path, **dict(read_options or {}))
    catalog = Catalog()
    n_frames = 0
    any_pbc = False
    natoms = set()
    for chunk in source.iter_chunks():
        for a in chunk:
            catalog.add(a)
            any_pbc |= bool(np.any(a.pbc))
            natoms.add(len(a))
            n_frames += 1
    caps = dst_adapter.capabilities
    if n_frames > 1 and not caps.multi_frame:
        raise InvalidInput(
            f"{dst_adapter.name} holds one structure but the input has {n_frames} frames"
        )
    lost = compute_losses(catalog, caps, any_pbc)
    if len(natoms) > 1 and not caps.variable_natoms:
        lost.append("variable atom count")
    if lost and not allow_loss:
        raise DataLossError(
            f"converting {src_adapter.name} → {dst_adapter.name} would lose: {', '.join(lost)}. "
            "Re-run with --allow-loss to accept.",
            lost=lost,
        )

    # pass 2: write to a temp file next to the target, then atomically rename
    tmp = out.with_name(f".{out.name}.atomdb-{uuid.uuid4().hex[:8]}")
    try:
        source = src_adapter.open(in_path, **dict(read_options or {}))
        dst_adapter.write((a for chunk in source.iter_chunks() for a in chunk), tmp)
        # sanity read-back: frame count
        n_back = len(dst_adapter.open(tmp)) if dst_adapter.readable else n_frames
        if n_back != n_frames:
            raise DataLossError(f"wrote {n_frames} frames but read back {n_back}")
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()

    result = {
        "input": str(in_path),
        "input_dataset": ds_id,
        "output": str(out.resolve()),
        "from": src_adapter.name,
        "to": dst_adapter.name,
        "n_frames": n_frames,
        "lost_fields": lost,
        "input_sha256": sha256_file(in_path),
        "output_sha256": sha256_file(out),
        "conversion_id": None,
    }
    if adb is not None and record:
        result["conversion_id"] = _record(adb, result, started, read_options or {})
    return result


def _record(adb: AtomDB, r: dict, started, read_options: dict) -> str:
    from .files import store_file

    with adb.session() as s:
        cnv = Calculation(
            id=new_id(s, "conversion"), name=f"{r['from']} → {r['to']}: {Path(r['output']).name}",
            calc_type="conversion", started_at=started, ended_at=utcnow(),
            command=command_line(), workdir=str(Path.cwd()),
            parameters={k: r[k] for k in ("from", "to", "lost_fields", "input_sha256",
                                           "output_sha256", "input", "output")}
            | {"read_options": read_options},
            software_versions=software_versions(),
        )
        s.add(cnv)
        s.flush()
        f_in = s.execute(select(File).where(File.sha256 == r["input_sha256"])).scalar_one_or_none()
        f_out, _ = store_file(adb, s, r["output"], fmt=r["to"], import_id=cnv.id)
        if f_in is not None:
            add_edge(s, cnv.id, f_in.id, "used")
            add_edge(s, f_out.id, f_in.id, "converted_from")
        if r["input_dataset"]:
            add_edge(s, cnv.id, r["input_dataset"], "used")
        add_edge(s, cnv.id, f_out.id, "generated")
        adb.audit(s, "file.convert", cnv.id, **{k: r[k] for k in ("input", "output", "lost_fields")})
        return cnv.id
