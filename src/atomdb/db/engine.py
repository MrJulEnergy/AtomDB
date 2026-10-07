"""Engine/session creation and schema migrations."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from ..config import StorePaths
from ..errors import NotInitialized
from .models import Meta
from .types import utcnow

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def make_engine(db_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{db_path}", future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


def alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    cfg.attributes["engine"] = engine
    return cfg


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision(engine: Engine) -> str | None:
    return ScriptDirectory.from_config(alembic_config(engine)).get_current_head()


def upgrade(engine: Engine) -> None:
    command.upgrade(alembic_config(engine), "head")


class Database:
    """Opened atomdb store: paths + engine + session factory."""

    def __init__(self, paths: StorePaths, engine: Engine):
        self.paths = paths
        self.engine = engine
        self._sessionmaker = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def session(self):
        s: Session = self._sessionmaker()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()

    def instance_uuid(self) -> str:
        with self.session() as s:
            return s.get(Meta, "instance_uuid").value

    def close(self) -> None:
        self.engine.dispose()


def init_store(paths: StorePaths) -> Database:
    """Create (or upgrade) a store. Idempotent; never deletes anything."""
    paths.ensure()
    engine = make_engine(paths.db)
    upgrade(engine)
    db = Database(paths, engine)
    with db.session() as s:
        if s.get(Meta, "instance_uuid") is None:
            s.add(Meta(key="instance_uuid", value=str(uuid.uuid4())))
            s.add(Meta(key="created_at", value=utcnow().isoformat()))
    return db


def open_store(paths: StorePaths, auto_upgrade: bool = True) -> Database:
    if not paths.initialized:
        raise NotInitialized(
            f"no atomdb store at {paths.root}; run `atomdb init` or set ATOMDB_HOME",
            root=str(paths.root),
        )
    engine = make_engine(paths.db)
    if current_revision(engine) != head_revision(engine):
        if not auto_upgrade:
            raise NotInitialized("database schema is out of date; run `atomdb init`")
        upgrade(engine)
    db = Database(paths, engine)
    with db.session() as s:
        s.execute(select(Meta).limit(1))
    return db
