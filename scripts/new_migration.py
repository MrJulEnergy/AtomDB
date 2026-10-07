"""Autogenerate an Alembic migration from the ORM models.

    uv run python scripts/new_migration.py "add frame table"
"""

import sys
import tempfile
from pathlib import Path

from alembic import command

from atomdb.db.engine import alembic_config, make_engine, upgrade

with tempfile.TemporaryDirectory() as tmp:
    engine = make_engine(Path(tmp) / "m.sqlite")
    upgrade(engine)
    command.revision(alembic_config(engine), message=sys.argv[1], autogenerate=True)
