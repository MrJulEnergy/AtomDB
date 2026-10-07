"""The central use case: trace data back to its origin and forward to its uses."""

from __future__ import annotations

import ase.io
import pytest
from ase.calculators.singlepoint import SinglePointCalculator

from atomdb.services import datasets as dsvc
from atomdb.services.importer import import_file
from atomdb.services.lineage import frame_lineage
from atomdb.services.provenance import provenance_graph, render_tree
from atomdb.services.records import (
    ModelSpec,
    add_model,
    record_dft,
    record_md,
    record_sampling,
    record_training,
)


@pytest.fixture
def chain(adb, data_dir, tmp_path):
    """MD(MACE-MH-1) → traj → sampling → selected → DFT(r2SCAN/D4) → labeled → model → MD."""
    traj = ase.io.read(data_dir / "kcl_labeled.extxyz", ":")
    for a in traj:
        a.calc = None
    ase.io.write(tmp_path / "md.extxyz", traj)
    ase.io.write(tmp_path / "selected.extxyz", [traj[1], traj[3]])
    labeled = [traj[1].copy(), traj[3].copy()]
    for a in labeled:
        a.calc = SinglePointCalculator(a, energy=-5.0, forces=a.positions * 0.0)
    ase.io.write(tmp_path / "labeled.extxyz", labeled)
    ase.io.write(tmp_path / "md2.extxyz", ase.io.read(data_dir / "kcl_variable.extxyz", ":"))

    ids = {}
    ids["traj"] = import_file(adb, tmp_path / "md.extxyz").dataset.id
    ids["sel"] = import_file(adb, tmp_path / "selected.extxyz").dataset.id
    ids["lab"] = import_file(adb, tmp_path / "labeled.extxyz").dataset.id
    ids["md2"] = import_file(adb, tmp_path / "md2.extxyz").dataset.id
    with adb.session() as s:
        ids["md"] = record_md(s, [ids["traj"]], model="MACE-MH-1",
                              md={"temperature_k": 400, "ensemble": "nvt", "timestep_fs": 0.5}).id
        ids["smp"] = record_sampling(s, ids["traj"], ids["sel"], "uncertainty",
                                     criterion={"threshold": 0.1}).id
        ids["dft"] = record_dft(s, [ids["lab"]], inputs=[ids["sel"]],
                                dft={"code": "CP2K", "functional": "R2SCAN", "dispersion": "dftd4",
                                     "basis": "TZV2P-MOLOPT", "cutoff_ry": 1000}).id
        m = add_model(adb, s, ModelSpec(name="KCl-MACE-v1", family="MACE", cutoff=5.0))
        ids["model"] = m.id
        ids["trn"] = record_training(s, m.id, [ids["lab"]]).id
        ids["md_new"] = record_md(s, [ids["md2"]], model=m.id, md={"temperature_k": 330}).id
    return ids


def test_upstream_of_labeled_dataset(adb, chain):
    with adb.session() as s:
        g = provenance_graph(adb, s, chain["lab"], "up")
    found = {n["id"] for n in g["nodes"]}
    for key in ("dft", "sel", "smp", "traj", "md"):
        assert chain[key] in found, key
    names = {n["name"] for n in g["nodes"]}
    assert "MACE-MH-1" in names
    dft = next(n for n in g["nodes"] if n["id"] == chain["dft"])
    assert dft["subtitle"] == "CP2K · r2SCAN/D4 · TZV2P-MOLOPT"
    tree = render_tree(g)
    assert "driven_by → MDL-" in tree and "MACE-MH-1 [placeholder]" in tree


def test_downstream_of_trajectory(adb, chain):
    with adb.session() as s:
        g = provenance_graph(adb, s, chain["traj"], "down")
    found = {n["id"] for n in g["nodes"]}
    for key in ("smp", "sel", "dft", "lab", "trn", "model", "md_new", "md2"):
        assert chain[key] in found, key


def test_frame_lineage(adb, chain):
    with adb.session() as s:
        lin = frame_lineage(s, chain["lab"], 1)
    assert [(x["dataset"], x["frame"]) for x in lin] == [
        (chain["lab"], 1), (chain["sel"], 1), (chain["traj"], 3)]
    assert lin[1]["via"] == chain["dft"] and lin[2]["via"] == chain["smp"]


def test_labels_marked(adb, chain):
    from sqlalchemy import select

    from atomdb.db.models import PropertyDef

    with adb.session() as s:
        props = s.execute(select(PropertyDef).where(
            PropertyDef.dataset_id == chain["lab"], PropertyDef.origin == "calc")).scalars().all()
    assert {p.name: p.label_calc_id for p in props}["forces"] == chain["dft"]


def test_placeholder_model_upgraded_by_add(adb, chain):
    with adb.session() as s:
        m = add_model(adb, s, ModelSpec(name="mace-mh-1", family="MACE", version="1"))
        assert m.status == "active" and m.family == "MACE"
        g = provenance_graph(adb, s, chain["traj"], "up")
        assert m.id in {n["id"] for n in g["nodes"]}


def test_persistence_of_graph(home, chain):
    from atomdb.services import AtomDB

    db = AtomDB.open(home)
    with db.session() as s:
        g = provenance_graph(db, s, chain["lab"], "up")
        assert chain["md"] in {n["id"] for n in g["nodes"]}
        assert dsvc.get_dataset(s, chain["lab"]).n_frames == 2
    db.close()
