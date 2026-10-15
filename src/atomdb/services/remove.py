"""Removing a dataset's data while keeping its provenance stub.

The frames, index rows and store files go; the node stays with ``status = "removed"`` and
``removed_*`` attributes (checksums, sizes, original path), so datasets derived from it
still say where they came from and the file can be recognised if it is imported again.
Removed datasets are hidden from search and skipped by validate/backup.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db.models import (
    DatasetElement,
    DatasetFile,
    File,
    Frame,
    NodeAttr,
    PropertyDef,
)
from ..db.types import utcnow
from ..errors import InvalidInput
from .context import AtomDB
from .datasets import get_dataset

REMOVED = "removed"


def plan(s: Session, ds_id: str) -> dict:
    """What ``remove_dataset`` would delete (no changes)."""
    ds = get_dataset(s, ds_id)
    if ds.status == REMOVED:
        raise InvalidInput(f"{ds.id} was already removed")
    files = {df.file.id: df.file for df in ds.files}
    shared = s.execute(select(DatasetFile.file_id, DatasetFile.dataset_id).where(
        DatasetFile.file_id.in_(files), DatasetFile.dataset_id != ds.id)).all()
    if shared:
        fid, other = shared[0]
        raise InvalidInput(f"{fid} is also a file of {other}; remove or re-link that first")
    return {"dataset": ds.id, "name": ds.name, "n_frames": ds.n_frames,
            "files": [{"id": f.id, "name": f.name, "sha256": f.sha256, "size": f.size_bytes,
                       "store_path": f.store_relpath} for f in files.values()],
            "bytes": sum(f.size_bytes or 0 for f in files.values())}


def remove_dataset(adb: AtomDB, s: Session, ds_id: str, reason: str | None = None) -> dict:
    p = plan(s, ds_id)
    ds = get_dataset(s, ds_id)
    origins = {f["id"]: sorted({o.original_path for o in s.get(File, f["id"]).origins})
               for f in p["files"]}
    record = {
        "removed_at": utcnow().isoformat(),
        "removed_reason": reason or "",
        "removed_files": [{k: f[k] for k in ("id", "name", "sha256", "size")}
                          | {"origins": origins.get(f["id"], [])} for f in p["files"]],
        "removed_n_frames": ds.n_frames,
    }
    for k, v in record.items():
        s.merge(NodeAttr(node_id=ds.id, key=k, value=v, source="auto"))
    for model in (Frame, DatasetElement, PropertyDef):
        s.execute(delete(model).where(model.dataset_id == ds.id))
    ds.structure = None
    ds.status = REMOVED
    for f in p["files"]:
        fl = s.get(File, f["id"])
        fl.status = REMOVED
        adb.store.abspath(fl.store_relpath).unlink(missing_ok=True)
    _drop_caches(adb, ds.id)
    AtomDB.audit(s, "dataset.remove", ds.id, reason=reason, files=[f["id"] for f in p["files"]],
                 bytes=p["bytes"])
    return p | {"removed": True}


def _drop_caches(adb: AtomDB, ds_id: str) -> None:
    root = adb.paths.cache
    for pattern in (f"stats/{ds_id}-*", f"descriptors/*/{ds_id}-*"):
        for f in root.glob(pattern):
            f.unlink(missing_ok=True)


def vacuum(adb: AtomDB) -> tuple[int, int]:
    """Give freed SQLite pages back to the file system; (bytes before, bytes after)."""
    db = adb.paths.db

    def size() -> int:  # WAL mode: recent pages live in the -wal file
        return sum(p.stat().st_size for p in (db, db.with_name(db.name + "-wal")) if p.exists())

    before = size()
    with adb.db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.exec_driver_sql("VACUUM")
        c.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    return before, size()
