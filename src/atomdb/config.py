"""Location and layout of the atomdb data store.

The store root (``ATOMDB_HOME``) holds the SQLite database and the content-addressed
file store. It is deliberately separate from the code repository.
"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "ATOMDB_HOME"
DB_FILENAME = "atomdb.sqlite"
SUBDIRS = ("files", "cache", "thumbnails", "exports", "backups", "logs", "tmp")


def default_home() -> Path:
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser().resolve()
    work = Path("/work") / getpass.getuser()
    if work.is_dir():
        return work / "atomdb_store"
    return Path.home() / "atomdb_store"


@dataclass(frozen=True)
class StorePaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / DB_FILENAME

    @property
    def files(self) -> Path:
        return self.root / "files"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in SUBDIRS:
            (self.root / sub).mkdir(exist_ok=True)

    @property
    def initialized(self) -> bool:
        return self.db.exists()


def get_paths(home: str | Path | None = None) -> StorePaths:
    return StorePaths(Path(home).expanduser().resolve() if home else default_home())
