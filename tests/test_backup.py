from __future__ import annotations

import pytest

from atomdb.errors import IntegrityProblem, InvalidInput
from atomdb.services import AtomDB
from atomdb.services.backup import backup, restore, verify_archive
from atomdb.services.importer import import_file
from atomdb.services.provenance import provenance_graph
from atomdb.services.records import record_dft


def test_backup_restore_roundtrip(adb, data_dir, tmp_path):
    ds = import_file(adb, data_dir / "kcl_labeled.extxyz").dataset.id
    with adb.session() as s:
        dft = record_dft(s, [ds], dft={"functional": "r2SCAN", "dispersion": "D4"}).id
    r = backup(adb)
    assert r["archive"].endswith(".tar.zst") and r["counts"]["datasets"] == 1
    assert verify_archive(r["archive"])["ok"]
    with pytest.raises(InvalidInput):
        backup(adb, out=r["archive"])
    res = restore(r["archive"], tmp_path / "restored")
    assert res["validation"]["ok"]
    b = AtomDB.open(tmp_path / "restored")
    with b.session() as s:
        g = provenance_graph(b, s, ds, "up")
        assert dft in {n["id"] for n in g["nodes"]}
    assert b.db.instance_uuid() == adb.db.instance_uuid()
    b.close()
    with pytest.raises(InvalidInput, match="not empty"):
        restore(r["archive"], tmp_path / "restored")


def test_tampered_archive_rejected(adb, data_dir, tmp_path):
    import_file(adb, data_dir / "kcl_labeled.extxyz")
    r = backup(adb, out=tmp_path / "b.tar.zst")
    from pathlib import Path

    arc = Path(r["archive"])
    data = bytearray(arc.read_bytes())
    data[len(data) // 2] ^= 0xFF
    arc.write_bytes(bytes(data))
    assert not verify_archive(r["archive"])["ok"]
    with pytest.raises((IntegrityProblem, Exception)):
        restore(r["archive"], tmp_path / "x")
