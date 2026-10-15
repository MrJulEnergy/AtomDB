"""Read-only symlinks into the store (or plain copies), so data can be used anywhere."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import File, SymLink
from ..errors import InvalidInput


def make_link(s: Session, store_path: Path, f: File, dest: Path, node_id: str | None) -> Path:
    dest = dest.expanduser()
    if dest.is_dir():
        dest = dest / f.name
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() and dest.resolve() == store_path.resolve():
            return dest
        raise InvalidInput(f"refusing to replace existing path: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.symlink_to(store_path)
    existing = s.execute(select(SymLink).where(SymLink.path == str(dest.absolute()))).scalar_one_or_none()
    if existing is None:
        s.add(SymLink(path=str(dest.absolute()), file_id=f.id, node_id=node_id))
    return dest


def make_copy(store_path: Path, f: File, dest: Path) -> Path:
    """A plain, writable copy for sharing or editing; not tracked like symlinks."""
    dest = dest.expanduser()
    if dest.is_dir():
        dest = dest / f.name
    if dest.exists() or dest.is_symlink():
        raise InvalidInput(f"refusing to replace existing path: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(store_path, dest)
    return dest
