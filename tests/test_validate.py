from __future__ import annotations

import os
import sqlite3
import stat

from atomdb.services.importer import import_file
from atomdb.services.validate import validate


def test_clean_store_validates(adb, data_dir):
    import_file(adb, data_dir / "kcl_labeled.extxyz")
    import_file(adb, data_dir / "kcl_znh5md.h5")
    r = validate(adb)
    assert r["ok"], r["problems"]
    assert r["checked"]["datasets"] == 2


def test_detects_corruption_missing_and_broken_refs(adb, data_dir):
    ds = import_file(adb, data_dir / "kcl_labeled.extxyz").dataset.id
    ds2 = import_file(adb, data_dir / "kcl_znh5md.h5")
    # corrupt one stored file
    p = adb.store.path_for(ds2.sha256)
    os.chmod(p, stat.S_IWUSR | stat.S_IRUSR)
    with open(p, "r+b") as fh:
        fh.seek(100)
        fh.write(b"XXXX")
    # delete the canonical file of the other
    with adb.session() as s:
        from atomdb.services.datasets import get_dataset

        canon = adb.store.abspath(get_dataset(s, ds).primary_file.store_relpath)
    os.chmod(canon, stat.S_IWUSR | stat.S_IRUSR)
    canon.unlink()
    # broken edge (bypassing the ORM)
    con = sqlite3.connect(adb.paths.db)
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("INSERT INTO edge (src, dst, relation, role, source, created_at) "
                "VALUES (?, 'DS-2026-999999', 'derived_from', '', 'user', '2026-01-01')", (ds,))
    con.commit()
    con.close()
    (adb.store.root / "sha256" / "ff").mkdir(parents=True, exist_ok=True)
    (adb.store.root / "sha256" / "ff" / ("f" * 64)).write_text("orphan")
    r = validate(adb)
    codes = {p["code"] for p in r["problems"]}
    assert not r["ok"]
    assert {"checksum_mismatch", "file_missing", "broken_edge", "orphan_file"} <= codes
    one = validate(adb, ds)
    assert {p["code"] for p in one["problems"]} >= {"file_missing", "broken_edge"}
