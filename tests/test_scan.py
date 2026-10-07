from __future__ import annotations

import shutil

import yaml

from atomdb.services.importer import import_file
from atomdb.services.scan import execute_plan, load_plan, scan, write_plan
from atomdb.store.hashing import sha256_file


def make_tree(root, data_dir):
    (root / "proj/nodes/MD/Packmol").mkdir(parents=True)
    (root / "proj/.venv/lib").mkdir(parents=True)
    (root / "proj/run_042").mkdir(parents=True)
    shutil.copy(data_dir / "kcl_znh5md.h5", root / "proj/nodes/MD/Packmol/frames.h5")
    shutil.copy(data_dir / "kcl_labeled.extxyz", root / "proj/train.extxyz")
    shutil.copy(data_dir / "kcl_labeled.extxyz", root / "proj/.venv/lib/ignored.extxyz")
    (root / "proj/run_042/input.inp").write_text("&GLOBAL\n&END GLOBAL\n")
    (root / "proj/notes.txt").write_text("not atomistic")
    (root / "proj/model.pt").write_bytes(b"\x00checkpoint")


def test_scan_is_read_only_and_finds_data(adb, data_dir, tmp_path):
    make_tree(tmp_path, data_dir)
    before = {p: sha256_file(p) for p in (tmp_path / "proj").rglob("*") if p.is_file()}
    r = scan(adb, tmp_path / "proj")
    after = {p: sha256_file(p) for p in (tmp_path / "proj").rglob("*") if p.is_file()}
    assert before == after
    paths = {i["path"].split("proj/")[1]: i for i in r["items"]}
    assert set(paths) == {"nodes/MD/Packmol/frames.h5", "train.extxyz", "model.pt"}
    assert paths["nodes/MD/Packmol/frames.h5"]["name"] == "Packmol/frames"
    assert paths["model.pt"]["kind"] == "model"
    assert any(d.endswith("run_042") for d in r["run_dirs"])
    assert r["summary"]["new"] == 3


def test_plan_roundtrip_and_rescan(adb, data_dir, tmp_path):
    make_tree(tmp_path, data_dir)
    import_file(adb, data_dir / "kcl_labeled.extxyz")  # identical to train.extxyz
    r = scan(adb, tmp_path / "proj")
    status = {i["path"].rsplit("/", 1)[1]: i["status"] for i in r["items"]}
    assert status["train.extxyz"] == "in_db"
    plan_path = write_plan(r, tmp_path / "plan.yaml")
    plan = yaml.safe_load(plan_path.read_text())
    for item in plan["items"]:
        if item["path"].endswith("frames.h5"):
            item["tags"] = ["legacy"]
            item["description"] = "packmol start structures"
    plan_path.write_text(yaml.safe_dump(plan))
    results = execute_plan(adb, load_plan(plan_path))
    actions = {x["path"].rsplit("/", 1)[1]: x["action"] for x in results}
    assert actions == {"frames.h5": "imported", "train.extxyz": "skip", "model.pt": "imported"}
    r2 = scan(adb, tmp_path / "proj")
    assert {i["status"] for i in r2["items"]} == {"in_db"}


def test_possible_duplicate_by_content(adb, data_dir, tmp_path):
    import ase.io

    import_file(adb, data_dir / "kcl_labeled.extxyz")
    (tmp_path / "d").mkdir()
    ase.io.write(tmp_path / "d/resaved.extxyz", ase.io.read(data_dir / "kcl_labeled.extxyz", ":2"))
    r = scan(adb, tmp_path / "d")
    assert r["items"][0]["status"] == "possible_duplicate"
