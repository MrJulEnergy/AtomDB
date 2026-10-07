from __future__ import annotations

import ase.io

from atomdb.services import datasets as dsvc
from atomdb.services.annotate import add_link
from atomdb.services.completeness import completeness, suggest_relations
from atomdb.services.importer import import_file
from atomdb.services.records import record_dft, record_md


def test_completeness_flags_missing_and_resolves(adb, data_dir):
    ds = import_file(adb, data_dir / "kcl_labeled.extxyz").dataset.id
    with adb.session() as s:
        c = completeness(s, dsvc.get_dataset(s, ds))
        assert not c["complete"] and set(c["missing"]) == {"origin", "label method"}
        record_dft(s, [ds], dft={"code": "CP2K", "functional": "r2SCAN", "dispersion": "D4",
                                 "basis": "TZV2P", "code_version": "2025.1"},
                   workdir="/work/run")
        c = completeness(s, dsvc.get_dataset(s, ds))
        assert c["complete"], c


def test_md_with_placeholder_model(adb, data_dir):
    ds = import_file(adb, data_dir / "kcl_plain.xyz").dataset.id
    with adb.session() as s:
        record_md(s, [ds], model="MACE-MH-1", md={"temperature_k": 400}, workdir="/w")
        c = completeness(s, dsvc.get_dataset(s, ds))
        assert c["missing"] == ["model details"]


def test_suggestions_from_shared_frames(adb, data_dir, tmp_path):
    parent = import_file(adb, data_dir / "kcl_labeled.extxyz").dataset.id
    ase.io.write(tmp_path / "subset.extxyz",
                 ase.io.read(data_dir / "kcl_labeled.extxyz", "0:2"))
    child = import_file(adb, tmp_path / "subset.extxyz").dataset.id
    with adb.session() as s:
        sug = suggest_relations(s)
        assert [(x["child"], x["relation"], x["parent"]) for x in sug] == [
            (child, "sampled_from", parent)]
        add_link(s, child, "sampled_from", parent)
        assert suggest_relations(s) == []


def test_starting_structure_suggestion(adb, data_dir, tmp_path):
    frames = ase.io.read(data_dir / "kcl_labeled.extxyz", ":")
    ase.io.write(tmp_path / "start.extxyz", frames[0])
    traj = import_file(adb, data_dir / "kcl_labeled.extxyz").dataset.id
    start = import_file(adb, tmp_path / "start.extxyz").dataset.id
    with adb.session() as s:
        sug = suggest_relations(s)
    assert [(x["child"], x["relation"], x["parent"]) for x in sug] == [
        (traj, "derived_from", start)]
    assert "starting structure" in sug[0]["reason"]
