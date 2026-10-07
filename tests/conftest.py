from __future__ import annotations

from pathlib import Path

import pytest

from atomdb.services import AtomDB

DATA = Path(__file__).parent / "data"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "store"
    monkeypatch.setenv("ATOMDB_HOME", str(h))
    return h


@pytest.fixture
def adb(home) -> AtomDB:
    db = AtomDB.init(home)
    yield db
    db.close()


@pytest.fixture
def data_dir() -> Path:
    return DATA
