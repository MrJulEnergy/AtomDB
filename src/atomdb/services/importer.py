"""Importing files into the store as datasets.

Pipeline: detect format → copy original into the store (dedup by sha256) → stream frames
once (property catalog + frame index + H5MD writer) → stamp and store the canonical H5MD
→ read it back and verify → record IMP/CNV activities and provenance edges.
Original files are only ever read.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import numpy as np
from ase.data import chemical_symbols
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from ..db.models import Calculation, Dataset, DatasetElement, DatasetFile, File, Frame, PropertyDef
from ..db.types import utcnow
from ..errors import FormatError, InvalidInput
from ..formats.base import FormatAdapter, FrameSource
from ..formats.h5md import H5MDAdapter, H5MDFrameSource, h5md_units, write_stamp
from ..formats.registry import detect, get_adapter
from ..frames import Catalog, frame_row, get_stress
from ..ids import new_id
from ..reproducibility import command_line, software_versions
from ..schemas import ImportResult
from .context import AtomDB
from .datasets import add_tags, summary
from .files import store_file
from .provenance import add_edge

CHUNK = 256
#: read options that name auxiliary input files; they are stored and linked as inputs
FILE_OPTIONS = ("topology",)


class _Indexer:
    """Accumulates per-frame rows and dataset aggregates while streaming."""

    def __init__(self, s: Session, ds_id: str):
        self.s = s
        self.ds_id = ds_id
        self.catalog = Catalog()
        self.n = 0
        self.n_atoms_total = 0
        self.n_atoms_min: int | None = None
        self.n_atoms_max: int | None = None
        self.formulas: Counter[str] = Counter()
        self.elem_frames: Counter[int] = Counter()
        self.elem_atoms: Counter[int] = Counter()
        self.e_min = self.e_max = self.fmax_max = None
        self.has_stress = False
        self.fingerprints = hashlib.sha256()
        self.fp_list: list[str] = []
        self.energies: list[float | None] = []

    def add_chunk(self, chunk) -> None:
        rows = []
        for atoms in chunk:
            r = frame_row(self.n, atoms)
            self.catalog.add(atoms)
            rows.append({"dataset_id": self.ds_id, **asdict(r)})
            self.n += 1
            self.n_atoms_total += r.n_atoms
            self.n_atoms_min = r.n_atoms if self.n_atoms_min is None else min(self.n_atoms_min, r.n_atoms)
            self.n_atoms_max = r.n_atoms if self.n_atoms_max is None else max(self.n_atoms_max, r.n_atoms)
            self.formulas[r.formula] += 1
            zs, counts = np.unique(atoms.numbers, return_counts=True)
            for z, c in zip(zs.tolist(), counts.tolist()):
                self.elem_frames[z] += 1
                self.elem_atoms[z] += c
            if r.energy is not None:
                self.e_min = r.energy if self.e_min is None else min(self.e_min, r.energy)
                self.e_max = r.energy if self.e_max is None else max(self.e_max, r.energy)
            if r.fmax is not None:
                self.fmax_max = r.fmax if self.fmax_max is None else max(self.fmax_max, r.fmax)
            if not self.has_stress and get_stress(atoms) is not None:
                self.has_stress = True
            self.fingerprints.update(r.fingerprint.encode())
            self.fp_list.append(r.fingerprint)
            self.energies.append(r.energy)
        if rows:
            self.s.execute(insert(Frame), rows)

    def finish(self, ds: Dataset, units: dict[str, str] | None = None) -> None:
        if self.n == 0:
            raise FormatError("file contains no frames")
        ds.n_frames = self.n
        ds.n_atoms_min = self.n_atoms_min
        ds.n_atoms_max = self.n_atoms_max
        ds.n_atoms_total = self.n_atoms_total
        if len(self.formulas) == 1:
            ds.formula_summary = next(iter(self.formulas))
        else:
            top = self.formulas.most_common(1)[0][0]
            ds.formula_summary = f"{len(self.formulas)} compositions (most common {top})"
        ds.has_energy = self.e_min is not None
        ds.has_forces = self.fmax_max is not None
        ds.has_stress = self.has_stress
        ds.energy_min, ds.energy_max, ds.fmax_max = self.e_min, self.e_max, self.fmax_max
        ds.content_fingerprint = self.fingerprints.hexdigest()[:32]
        for z in sorted(self.elem_frames):
            self.s.add(DatasetElement(
                dataset_id=ds.id, z=z, symbol=chemical_symbols[z],
                n_frames=self.elem_frames[z], n_atoms=self.elem_atoms[z],
            ))
        units = units or {}
        h5_names = {"energy": "potential_energy", "forces": "force", "positions": "position"}
        for p in self.catalog.to_list():
            unit = p.unit or units.get(h5_names.get(p.name, p.name))
            self.s.add(PropertyDef(
                dataset_id=ds.id, origin=p.origin, name=p.name, level=p.level, dtype=p.dtype,
                shape=p.shape, unit=unit, count=p.count,
            ))


def _verify_roundtrip(h5_path: Path, idx: _Indexer) -> list[str]:
    """Re-read the written H5MD and compare against what was streamed in."""
    problems: list[str] = []
    src = H5MDFrameSource(h5_path)
    if len(src) != idx.n:
        return [f"frame count mismatch: wrote {idx.n}, read back {len(src)}"]
    back = Catalog()
    i = 0
    for chunk in src.iter_chunks(CHUNK):
        for atoms in chunk:
            back.add(atoms)
            row = frame_row(i, atoms)
            if row.fingerprint != idx.fp_list[i]:
                problems.append(f"frame {i}: structure changed during conversion")
            e0 = idx.energies[i]
            if (e0 is None) != (row.energy is None) or (
                e0 is not None and not np.isclose(e0, row.energy, rtol=1e-9, atol=1e-9)
            ):
                problems.append(f"frame {i}: energy changed during conversion")
            i += 1
            if len(problems) > 10:
                return problems
    missing = idx.catalog.keys() - back.keys()
    if missing:
        problems.append(f"fields lost in H5MD conversion: {sorted(missing)}")
    return problems


def _existing_dataset_for(s: Session, f: File) -> Dataset | None:
    return s.execute(
        select(Dataset)
        .join(DatasetFile, DatasetFile.dataset_id == Dataset.id)
        .where(DatasetFile.file_id == f.id, DatasetFile.role.in_(("original", "canonical_h5md")))
    ).scalars().first()


def import_raw(adb: AtomDB, path: str | Path, tags: list[str] | None = None) -> ImportResult:
    """Store a file without interpreting it (inputs, logs, scripts, checkpoints)."""
    p = Path(path).expanduser()
    with adb.session() as s:
        f, dup = store_file(adb, s, p)
        add_tags(s, f, tags or [])
        return ImportResult(
            dataset=None, file_id=f.id, sha256=f.sha256, duplicate=dup,
            message=f"{'already stored' if dup else 'stored'} {p.name} as {f.id}",
        )


def import_file(
    adb: AtomDB,
    path: str | Path,
    name: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    fmt: str | None = None,
    read_options: dict | None = None,
    session: Session | None = None,
) -> ImportResult:
    p = Path(path).expanduser()
    if not p.is_file():
        raise InvalidInput(f"file not found: {p}", path=str(p))
    adapter = get_adapter(fmt) if fmt else detect(p)
    if session is not None:
        return _import(adb, session, p, adapter, name, description, tags, read_options)
    with adb.session() as s:
        return _import(adb, s, p, adapter, name, description, tags, read_options)


def _import(
    adb: AtomDB,
    s: Session,
    p: Path,
    adapter: FormatAdapter,
    name: str | None,
    description: str | None,
    tags: list[str] | None,
    read_options: dict | None,
) -> ImportResult:
    read_options = dict(read_options or {})
    started = utcnow()
    imp_id = new_id(s, "import")
    f_orig, duplicate = store_file(adb, s, p, fmt=adapter.name, import_id=imp_id)
    if duplicate:
        existing = _existing_dataset_for(s, f_orig)
        if existing is not None:
            return ImportResult(
                dataset=summary(s, existing), file_id=f_orig.id, sha256=f_orig.sha256,
                duplicate=True,
                message=f"already known: identical bytes stored as {f_orig.id}, "
                        f"dataset {existing.id} (new location recorded)",
            )
    ds = Dataset(id=new_id(s, "dataset"), name=name or p.stem, description=description,
                 source_text=str(p.resolve()))
    s.add(ds)
    s.flush()
    idx = _Indexer(s, ds.id)
    warnings: list[str] = []
    stored_orig = adb.store.path_for(f_orig.sha256)
    is_h5md = isinstance(adapter, H5MDAdapter)

    if is_h5md:
        # already in the preferred format: the original bytes are the canonical file
        src = adapter.open(stored_orig)
        warnings += src.warnings
        for chunk in src.iter_chunks(CHUNK):
            idx.add_chunk(chunk)
        idx.finish(ds, units=h5md_units(stored_orig))
        canonical = f_orig
    else:
        tmp_h5 = adb.paths.tmp / f"convert-{uuid.uuid4().hex}.h5"
        try:
            src: FrameSource = adapter.open(stored_orig, **read_options)
            read_options.update(src.resolved_options)
            warnings += src.warnings

            def stream():
                for chunk in src.iter_chunks(CHUNK):
                    idx.add_chunk(chunk)
                    yield from chunk

            H5MDAdapter().write(stream(), tmp_h5)
            idx.finish(ds)
            problems = _verify_roundtrip(tmp_h5, idx)
            if problems:
                raise FormatError(
                    "H5MD conversion could not be verified; nothing was imported: "
                    + "; ".join(problems),
                    path=str(p),
                )
            write_stamp(tmp_h5, {
                "dataset_id": ds.id,
                "instance_uuid": adb.db.instance_uuid(),
                "source_sha256": f_orig.sha256,
                "source_name": p.name,
                "source_path": str(p.resolve()),
                "source_format": adapter.name,
                "created_at": utcnow().isoformat(),
                "software": software_versions(),
            })
            canonical, _ = store_file(adb, s, tmp_h5, fmt="h5md", record_origin=False)
            canonical.name = f"{p.stem}.h5"
        finally:
            if tmp_h5.exists():
                tmp_h5.unlink()
        cnv = Calculation(
            id=new_id(s, "conversion"), name=f"{adapter.name} → h5md: {p.name}",
            calc_type="conversion", started_at=started, ended_at=utcnow(),
            command=command_line(),
            parameters={"from": adapter.name, "to": "h5md", "read_options": read_options,
                        "lost_fields": [], "verified": True,
                        "source_sha256": f_orig.sha256, "output_sha256": canonical.sha256},
            software_versions=software_versions(),
        )
        s.add(cnv)
        s.flush()
        add_edge(s, cnv.id, f_orig.id, "used")
        add_edge(s, cnv.id, canonical.id, "generated")
        add_edge(s, canonical.id, f_orig.id, "converted_from")

    ds.primary_file_id = canonical.id
    ds.files.append(DatasetFile(file_id=f_orig.id, role="original"))
    ds.files.append(DatasetFile(file_id=canonical.id, role="canonical_h5md"))
    add_tags(s, ds, tags or [])

    imp = Calculation(
        id=imp_id, name=f"import {p.name}", calc_type="import", started_at=started,
        ended_at=utcnow(), command=command_line(), workdir=str(Path.cwd()),
        parameters={"format": adapter.name, "read_options": read_options,
                    "source_path": str(p.resolve())},
        software_versions=software_versions(),
    )
    s.add(imp)
    s.flush()
    add_edge(s, imp.id, f_orig.id, "used")
    for key in FILE_OPTIONS:
        if read_options.get(key) and Path(read_options[key]).is_file():
            f_aux, _ = store_file(adb, s, read_options[key], import_id=imp.id)
            add_edge(s, imp.id, f_aux.id, "used", role=key)
            ds.files.append(DatasetFile(file_id=f_aux.id, role=key))
            imp.parameters = {**imp.parameters, f"{key}_sha256": f_aux.sha256}
    add_edge(s, imp.id, ds.id, "generated")
    adb.audit(s, "dataset.import", ds.id, file_id=f_orig.id, path=str(p.resolve()))
    return ImportResult(
        dataset=summary(s, ds), file_id=f_orig.id, sha256=f_orig.sha256, duplicate=duplicate,
        message=f"imported {p.name} ({adapter.name}, {ds.n_frames} frames) as {ds.id}",
        warnings=warnings,
    )
