"""Discover existing data in a directory tree and turn it into an editable import plan.

Scanning is strictly read-only. The plan is YAML so it can be reviewed and annotated
(names, tags, descriptions, metadata, provenance links) before anything is imported.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import select

from ..db.models import Dataset, DatasetFile, File, Frame
from ..errors import AtomDBError, InvalidInput
from ..formats.h5md import is_h5md, is_hdf5
from ..formats.registry import adapters
from ..frames import fingerprint
from ..store.hashing import sha256_file
from .context import AtomDB

DEFAULT_EXCLUDE = (".venv", "venv", "node_modules", ".git", "__pycache__", ".dvc", ".cache",
                   "site-packages", ".ipynb_checkpoints", ".pytest_cache")
MODEL_EXTENSIONS = (".model", ".pt", ".pth", ".ckpt")
RUN_MARKERS = ("slurm-", ".inp", "plumed")
GENERIC_STEMS = {"frames", "structures", "data", "output", "out", "traj", "trajectory",
                 "dataset", "md", "atoms", "result", "results"}


def _classify(p: Path) -> tuple[str, str | None]:
    """Return (kind, format) for a file: dataset | model | skip."""
    name = p.name.lower()
    if name.endswith((".h5", ".h5md", ".hdf5")):
        if is_h5md(p):
            return "dataset", "h5md"
        return ("skip", None) if not is_hdf5(p) else ("skip", "hdf5")
    if name.endswith(MODEL_EXTENSIONS):
        return "model", None
    best, fmt = 0.0, None
    for a in adapters().values():
        if a.name == "h5md":
            continue
        try:
            score = a.sniff(p)
        except OSError:
            score = 0.0
        if score > best:
            best, fmt = score, a.name
    return ("dataset", fmt) if best >= 0.5 else ("skip", None)


def _looks_like_model_dir(d: Path, filenames: list[str]) -> bool:
    """Directory checkpoints, e.g. apax/NequIP run dirs: '*model*' with a config file."""
    return "model" in d.name.lower() and any(
        f in filenames for f in ("config.yaml", "config.yml", "config.json", "params.yaml")
    )


def _suggest_name(p: Path) -> str:
    stem = p.stem
    if stem.lower() in GENERIC_STEMS or len(stem) < 4:
        return f"{p.parent.name}/{stem}"
    return stem


def _first_frame_matches(adb: AtomDB, p: Path, fmt: str) -> list[str]:
    try:
        src = adapters()[fmt].open(p)
        atoms = src.get(0) if src.random_access else next(iter(src))
    except (AtomDBError, StopIteration, IndexError, OSError):
        return []
    with adb.session() as s:
        rows = s.execute(
            select(Frame.dataset_id).where(Frame.fingerprint == fingerprint(atoms)).distinct()
        ).scalars()
        return sorted(rows)


def scan(
    adb: AtomDB,
    root: str | Path,
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE,
    include_models: bool = True,
    content_check: bool = True,
    max_depth: int | None = None,
) -> dict:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise InvalidInput(f"not a directory: {root}")
    items: list[dict] = []
    run_dirs: set[str] = set()
    model_dirs: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = sorted(d for d in dirnames if d not in exclude and not d.startswith(".")
                             and (max_depth is None or depth < max_depth))
        if include_models and _looks_like_model_dir(Path(dirpath), filenames):
            model_dirs.append(dirpath)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if p.is_symlink() or not p.is_file():
                continue
            low = fn.lower()
            if any(low.startswith(m) or low.endswith(m) for m in RUN_MARKERS):
                run_dirs.add(dirpath)
            kind, fmt = _classify(p)
            if kind == "skip" or (kind == "model" and not include_models):
                continue
            items.append(_item(adb, p, kind, fmt, content_check))
    return {
        "root": str(root),
        "scanned_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "items": items,
        "run_dirs": sorted(run_dirs),
        "model_dirs": model_dirs,
        "summary": {
            "new": sum(1 for i in items if i["status"] == "new"),
            "in_db": sum(1 for i in items if i["status"] == "in_db"),
            "possible_duplicate": sum(1 for i in items if i["status"] == "possible_duplicate"),
        },
    }


def _item(adb: AtomDB, p: Path, kind: str, fmt: str | None, content_check: bool) -> dict:
    sha = sha256_file(p)
    item = {
        "path": str(p), "kind": kind, "format": fmt, "sha256": sha,
        "size_bytes": p.stat().st_size, "status": "new", "known_as": [],
        "import": True, "name": _suggest_name(p), "tags": [], "description": None,
        "metadata": {}, "links": [],
    }
    with adb.session() as s:
        f = s.execute(select(File).where(File.sha256 == sha)).scalar_one_or_none()
        if f is not None:
            ds_ids = s.execute(
                select(Dataset.id).join(DatasetFile, DatasetFile.dataset_id == Dataset.id)
                .where(DatasetFile.file_id == f.id)
            ).scalars().all()
            item.update(status="in_db", known_as=[f.id, *ds_ids], **{"import": False})
            return item
    if content_check and kind == "dataset" and fmt:
        matches = _first_frame_matches(adb, p, fmt)
        if matches:
            item.update(status="possible_duplicate", known_as=matches)
    return item


PLAN_HEADER = """\
# atomdb import plan
# Review and edit, then run:  atomdb import-plan {plan_name}
#  - set `import: false` to skip an item
#  - kind: dataset | raw | model
#  - name / description / tags are applied to the new dataset
#  - metadata: e.g. {{temperature: 400, model: MACE-MH-1, functional: r2SCAN,
#              dispersion: D4, code: CP2K, sampled_from: DS-2026-000003, source: /work/run}}
#    MD/DFT/sampling keys create structured provenance records; others become metadata
#  - links: [{{relation: derived_from, target: DS-2026-000003}}]
#  - read_options: e.g. {{type_map: "1=K,2=Cl"}} for LAMMPS files
# Items with status `in_db` are already stored (identical bytes).
"""


def write_plan(result: dict, out: Path) -> Path:
    body = {"root": result["root"], "scanned_at": result["scanned_at"],
            "items": result["items"], "run_dirs": result["run_dirs"],
            "model_dirs": result.get("model_dirs", [])}
    out = Path(out)
    if out.exists():
        raise InvalidInput(f"plan file exists: {out}")
    out.write_text(PLAN_HEADER.format(plan_name=out.name)
                   + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100))
    return out


def load_plan(path: Path) -> dict:
    try:
        plan = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as e:
        raise InvalidInput(f"invalid plan file: {e}") from e
    if not isinstance(plan, dict) or not isinstance(plan.get("items"), list):
        raise InvalidInput("plan file must contain an `items` list")
    return plan


def execute_plan(adb: AtomDB, plan: dict, dry_run: bool = False) -> list[dict]:
    from .importer import import_file, import_raw

    results = []
    for item in plan["items"]:
        if not item.get("import", True) or item.get("status") == "in_db":
            results.append({"path": item["path"], "action": "skip"})
            continue
        if dry_run:
            results.append({"path": item["path"], "action": f"would import as {item['kind']}"})
            continue
        try:
            if item["kind"] == "dataset":
                r = import_file(
                    adb, item["path"], name=item.get("name"), description=item.get("description"),
                    tags=item.get("tags") or [], fmt=item.get("format"),
                    read_options=item.get("read_options") or {},
                )
                if (item.get("metadata") or item.get("links")) and not r.duplicate:
                    from .annotate import apply_annotations

                    with adb.session() as s:
                        apply_annotations(s, r.dataset.id, item.get("metadata") or {},
                                          item.get("links") or [])
            else:
                r = import_raw(adb, item["path"], tags=item.get("tags") or [])
            results.append({"path": item["path"], "action": "imported",
                            "id": r.dataset.id if r.dataset else r.file_id,
                            "duplicate": r.duplicate, "warnings": r.warnings})
        except AtomDBError as e:
            results.append({"path": item["path"], "action": "error", "error": e.to_dict()})
    return results
