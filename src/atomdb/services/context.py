"""The service entry point shared by CLI and API."""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import StorePaths, get_paths
from ..db.engine import Database, init_store, open_store
from ..db.models import AuditLog
from ..store.filestore import FileStore


class AtomDB:
    def __init__(self, db: Database):
        self.db = db
        self.paths: StorePaths = db.paths
        self.store = FileStore(self.paths.files, self.paths.tmp)

    @classmethod
    def open(cls, home: str | Path | None = None) -> AtomDB:
        return cls(open_store(get_paths(home)))

    @classmethod
    def init(cls, home: str | Path | None = None) -> AtomDB:
        return cls(init_store(get_paths(home)))

    def session(self):
        return self.db.session()

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def audit(s: Session, action: str, node_id: str | None = None, **payload) -> None:
        s.add(
            AuditLog(
                action=action,
                node_id=node_id,
                command=" ".join(sys.argv) if sys.argv else None,
                actor=getpass.getuser(),
                payload=payload or None,
            )
        )
