"""Storing files and recording where they came from."""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import File, FileOrigin
from ..errors import InvalidInput
from ..ids import new_id
from .context import AtomDB


def _ts(t: float) -> datetime:
    return datetime.fromtimestamp(t, UTC)


def store_file(
    adb: AtomDB,
    s: Session,
    path: str | Path,
    fmt: str | None = None,
    import_id: str | None = None,
    record_origin: bool = True,
) -> tuple[File, bool]:
    """Copy a file into the store and return its File node.

    Returns ``(file, is_duplicate)``. A duplicate (same sha256) reuses the existing node
    and only gains an extra origin record.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise InvalidInput(f"not a regular file: {p}", path=str(p))
    st = p.stat()
    stored = adb.store.ingest(p)
    f = s.execute(select(File).where(File.sha256 == stored.sha256)).scalar_one_or_none()
    duplicate = f is not None
    if f is None:
        f = File(
            id=new_id(s, "file"),
            name=p.name,
            sha256=stored.sha256,
            size_bytes=stored.size,
            format=fmt,
            store_relpath=stored.relpath,
        )
        s.add(f)
        s.flush()
        adb.audit(s, "file.store", f.id, sha256=stored.sha256, path=str(p.resolve()))
    elif fmt and not f.format:
        f.format = fmt
    if record_origin:
        add_origin(s, f, p, import_id=import_id, mtime=st.st_mtime, ctime=st.st_ctime)
    return f, duplicate


def add_origin(
    s: Session,
    f: File,
    p: Path,
    import_id: str | None = None,
    mtime: float | None = None,
    ctime: float | None = None,
) -> None:
    resolved = str(p.resolve()) if p.exists() else str(p)
    host = socket.gethostname()
    for o in f.origins:
        if o.original_path == resolved and o.hostname == host:
            return
    f.origins.append(
        FileOrigin(
            original_path=resolved,
            original_name=p.name,
            hostname=host,
            mtime=_ts(mtime) if mtime else None,
            ctime=_ts(ctime) if ctime else None,
            import_id=import_id,
        )
    )
