"""Integrity checks. Read-only: problems are reported, never "fixed" by deleting data."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..db.engine import current_revision, head_revision
from ..db.models import Dataset, Edge, File, Frame, FrameLink, Node, PropertyDef, SymLink
from ..errors import AtomDBError
from ..formats.h5md import H5MDFrameSource
from ..store.hashing import sha256_file
from .context import AtomDB
from .datasets import get_dataset


class Report:
    def __init__(self):
        self.problems: list[dict] = []
        self.checked: dict[str, int] = {}

    def add(self, severity: str, code: str, message: str, node: str | None = None) -> None:
        self.problems.append({"severity": severity, "code": code, "node": node, "message": message})

    def count(self, key: str, n: int = 1) -> None:
        self.checked[key] = self.checked.get(key, 0) + n

    def to_dict(self) -> dict:
        errors = sum(1 for p in self.problems if p["severity"] == "error")
        return {"ok": errors == 0, "errors": errors,
                "warnings": sum(1 for p in self.problems if p["severity"] == "warning"),
                "checked": self.checked, "problems": self.problems}


def check_file(adb: AtomDB, f: File, rep: Report, quick: bool) -> bool:
    p = adb.store.abspath(f.store_relpath)
    rep.count("files")
    if not p.exists():
        rep.add("error", "file_missing", f"store file missing: {p}", f.id)
        return False
    if p.stat().st_size != f.size_bytes:
        rep.add("error", "size_mismatch", f"size {p.stat().st_size} != recorded {f.size_bytes}", f.id)
        return False
    if not quick and sha256_file(p) != f.sha256:
        rep.add("error", "checksum_mismatch", "sha256 differs from the recorded checksum", f.id)
        return False
    if os.access(p, os.W_OK) and os.geteuid() != 0:
        rep.add("warning", "writable", "store file is writable (expected read-only)", f.id)
    return True


def check_dataset(adb: AtomDB, s: Session, ds: Dataset, rep: Report, quick: bool) -> None:
    rep.count("datasets")
    if not ds.name:
        rep.add("warning", "no_name", "dataset has no name", ds.id)
    for df in ds.files:
        check_file(adb, df.file, rep, quick)
    if ds.primary_file is None:
        rep.add("error", "no_canonical_file", "dataset has no canonical H5MD file", ds.id)
        return
    n_rows = s.execute(select(func.count()).select_from(Frame).where(Frame.dataset_id == ds.id)
                       ).scalar_one()
    if n_rows != ds.n_frames:
        rep.add("error", "frame_index", f"frame index has {n_rows} rows, dataset says {ds.n_frames}",
                ds.id)
    p = adb.store.abspath(ds.primary_file.store_relpath)
    if not p.exists():
        return
    try:
        src = H5MDFrameSource(p)
        if len(src) != ds.n_frames:
            rep.add("error", "frame_count", f"file has {len(src)} frames, dataset says {ds.n_frames}",
                    ds.id)
        else:
            for idx in {0, len(src) - 1}:
                row = s.get(Frame, (ds.id, idx))
                n = len(src.get(idx))
                if row is not None and row.n_atoms != n:
                    rep.add("error", "atom_count",
                            f"frame {idx}: file has {n} atoms, index says {row.n_atoms}", ds.id)
    except AtomDBError as e:
        rep.add("error", "unreadable", f"canonical file cannot be read: {e.message}", ds.id)
    if not s.execute(select(PropertyDef).where(PropertyDef.dataset_id == ds.id).limit(1)
                     ).first():
        rep.add("warning", "no_catalog", "no property catalog", ds.id)


def validate(adb: AtomDB, ds_id: str | None = None, quick: bool = False,
             orphans: bool = True) -> dict:
    rep = Report()
    with adb.session() as s:
        if ds_id:
            ds = get_dataset(s, ds_id)
            check_dataset(adb, s, ds, rep, quick)
            for e in s.execute(select(Edge).where((Edge.src == ds.id) | (Edge.dst == ds.id))
                               ).scalars():
                rep.count("edges")
                for end in (e.src, e.dst):
                    if s.get(Node, end) is None:
                        rep.add("error", "broken_edge", f"{e.relation} references missing {end}",
                                ds.id)
            return rep.to_dict()

        # database-level
        ic = s.execute(text("PRAGMA integrity_check")).scalar()
        if ic != "ok":
            rep.add("error", "sqlite_integrity", f"SQLite integrity_check: {ic}")
        for row in s.execute(text("PRAGMA foreign_key_check")).all():
            rep.add("error", "foreign_key", f"foreign key violation in {row[0]} rowid {row[1]}")
        if current_revision(adb.db.engine) != head_revision(adb.db.engine):
            rep.add("error", "schema", "database schema not at the latest migration")
        node_ids = set(s.execute(select(Node.id)).scalars())
        for e in s.execute(select(Edge)).scalars():
            rep.count("edges")
            for end in (e.src, e.dst):
                if end not in node_ids:
                    rep.add("error", "broken_edge",
                            f"edge {e.src} --{e.relation}--> {e.dst}: {end} does not exist")
        for fl in s.execute(select(FrameLink.child_dataset, FrameLink.parent_dataset).distinct()):
            for end in fl:
                if end not in node_ids:
                    rep.add("error", "broken_frame_link", f"frame link references missing {end}")
        datasets = s.execute(select(Dataset)).scalars().all()
        seen_files = set()
        for ds in datasets:
            check_dataset(adb, s, ds, rep, quick)
            seen_files |= {df.file_id for df in ds.files}
        known_sha = set()
        for f in s.execute(select(File)).scalars():
            known_sha.add(f.sha256)
            if f.id not in seen_files:
                check_file(adb, f, rep, quick)
        for lk in s.execute(select(SymLink)).scalars():
            rep.count("symlinks")
            p = Path(lk.path)
            if not p.is_symlink():
                rep.add("warning", "link_gone", f"handed-out symlink no longer exists: {p}", lk.node_id)
            elif not p.exists():
                rep.add("warning", "link_dangling", f"symlink is dangling: {p}", lk.node_id)
        n_placeholders = s.execute(select(func.count()).select_from(Node)
                                   .where(Node.status == "placeholder")).scalar_one()
        if n_placeholders:
            rep.add("info", "placeholders", f"{n_placeholders} placeholder nodes awaiting details")
    if orphans:
        root = adb.store.root / "sha256"
        if root.exists():
            for p in root.glob("*/*"):
                rep.count("store_objects")
                if p.name not in known_sha:
                    rep.add("warning", "orphan_file",
                            f"store object not referenced by the database (kept): {p}")
    return rep.to_dict()
