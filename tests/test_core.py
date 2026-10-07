from __future__ import annotations

import os
import stat

import pytest

from atomdb.errors import InvalidInput, NotFound, NotInitialized
from atomdb.ids import normalize_id
from atomdb.services import AtomDB
from atomdb.services import datasets as dsvc
from atomdb.services.importer import import_file
from atomdb.store.hashing import sha256_file


def _write(p, text="0"):
    """A minimal valid one-atom xyz file whose content depends on ``text``."""
    x = sum(map(ord, text)) * 0.01
    p.write_text(f"1\n\nH {x} 0 0\n")
    return p


def test_open_uninitialized_raises(tmp_path):
    with pytest.raises(NotInitialized):
        AtomDB.open(tmp_path / "nope")


def test_init_is_idempotent(home):
    a = AtomDB.init(home)
    uid = a.db.instance_uuid()
    a.close()
    b = AtomDB.init(home)
    assert b.db.instance_uuid() == uid
    b.close()


def test_ids_are_sequential_and_stable(adb, tmp_path):
    r1 = import_file(adb, _write(tmp_path / "a.xyz", "a"))
    r2 = import_file(adb, _write(tmp_path / "b.xyz", "b"))
    y = r1.dataset.created_at.year
    assert r1.dataset.id == f"DS-{y}-000001"
    assert r2.dataset.id == f"DS-{y}-000002"
    assert normalize_id(f"ds-{y}-2") == r2.dataset.id


def test_import_copies_and_preserves_source(adb, tmp_path):
    src = _write(tmp_path / "traj.xyz")
    before = sha256_file(src)
    mtime = src.stat().st_mtime
    r = import_file(adb, src, tags=["test"])
    assert src.exists() and sha256_file(src) == before and src.stat().st_mtime == mtime
    assert r.sha256 == before
    stored = adb.store.path_for(before)
    assert stored.exists()
    assert not (stored.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    assert r.dataset.tags == ["test"]


def test_duplicate_detection(adb, tmp_path):
    a = _write(tmp_path / "one.xyz", "same bytes")
    b = _write(tmp_path / "other_name.xyz", "same bytes")
    r1 = import_file(adb, a)
    r2 = import_file(adb, b)
    assert r2.duplicate
    assert r2.dataset.id == r1.dataset.id
    assert r2.file_id == r1.file_id
    with adb.session() as s:
        d = dsvc.detail(adb, s, dsvc.get_dataset(s, r1.dataset.id))
    origins = {o.original_name for o in d.files[0].file.origins}
    assert origins == {"one.xyz", "other_name.xyz"}


def test_persistence_across_restart(home, tmp_path):
    a = AtomDB.init(home)
    ids = [import_file(a, _write(tmp_path / f"f{i}.xyz", str(i))).dataset.id for i in range(5)]
    a.close()
    b = AtomDB.open(home)
    with b.session() as s:
        total, rows = dsvc.list_datasets(s)
    assert total == 5 and {r.id for r in rows} == set(ids)
    for r in rows:
        assert b.store.verify(r.files[0].file.sha256)
    b.close()


def test_missing_file_and_unknown_id(adb, tmp_path):
    with pytest.raises(InvalidInput):
        import_file(adb, tmp_path / "missing.xyz")
    with adb.session() as s, pytest.raises(NotFound):
        dsvc.get_dataset(s, "DS-2026-999999")


def test_store_files_are_not_writable_through_symlink(adb, tmp_path):
    src = _write(tmp_path / "x.xyz")
    r = import_file(adb, src)
    stored = adb.store.path_for(r.sha256)
    if os.geteuid() == 0:  # root ignores permissions
        pytest.skip("running as root")
    with pytest.raises(PermissionError), open(stored, "a"):
        pass
