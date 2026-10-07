"""The search examples from the specification."""

from __future__ import annotations

import ase
import ase.io
import pytest

from atomdb.services.importer import import_file
from atomdb.services.records import record_dft, record_md, record_sampling
from atomdb.services.search import parse, search, search_frames


@pytest.fixture
def db(adb, data_dir, tmp_path):
    ids = {}
    ids["traj"] = import_file(adb, data_dir / "kcl_plain.xyz", name="KCl MD 400K").dataset.id
    ase.io.write(tmp_path / "sel.extxyz", ase.io.read(data_dir / "kcl_plain.xyz", "0:2"))
    ids["sel"] = import_file(adb, tmp_path / "sel.extxyz").dataset.id
    ids["lab"] = import_file(adb, data_dir / "kcl_labeled.extxyz", name="r2scan labels").dataset.id
    ase.io.write(tmp_path / "nacl.extxyz", ase.Atoms("NaCl", positions=[[0, 0, 0], [2.8, 0, 0]]))
    ids["nacl"] = import_file(adb, tmp_path / "nacl.extxyz").dataset.id
    ids["pbe"] = import_file(adb, data_dir / "kcl_znh5md.h5", name="pbe set").dataset.id
    with adb.session() as s:
        ids["md"] = record_md(s, [ids["traj"]], model="MACE-MH-1",
                              md={"temperature_k": 400, "ensemble": "NVT"},
                              enhanced={"method": "metad", "collective_variables": ["d1"]}).id
        record_sampling(s, ids["traj"], ids["sel"], "uncertainty")
        record_dft(s, [ids["lab"]], inputs=[ids["sel"]],
                   dft={"code": "CP2K", "functional": "r2SCAN", "dispersion": "D4"})
        record_dft(s, [ids["pbe"]], dft={"code": "VASP", "functional": "PBE", "dispersion": "D3"})
        record_md(s, [ids["nacl"]], model="NequIP-x", md={"temperature_k": 300})
    return adb, ids


def q(adb, text, **kw):
    with adb.session() as s:
        return {i.id for i in search(s, text, **kw)["items"]}


def test_functional(db):
    adb, ids = db
    assert q(adb, "functional:r2scan") == {ids["lab"]}
    assert q(adb, "r2scan") == {ids["lab"]}           # free text hits facet values and names
    assert q(adb, "xc:R2SCAN") == {ids["lab"]}
    assert q(adb, "functional:pbe") == {ids["pbe"]}


def test_functional_plus_dispersion(db):
    adb, ids = db
    assert q(adb, "r2scan+d4") == {ids["lab"]}
    assert q(adb, "r2SCAN+D3") == set()


def test_elements(db):
    adb, ids = db
    kcl = {ids["traj"], ids["sel"], ids["lab"], ids["pbe"]}
    assert q(adb, "el:K,Cl") == kcl
    assert q(adb, "contains K and Cl") == kcl
    assert q(adb, "el:K|Na") == kcl | {ids["nacl"]}
    assert q(adb, "only:Na,Cl") == {ids["nacl"]}
    assert q(adb, "", elements=["Na", "K"], any_elements=True) == kcl | {ids["nacl"]}
    assert q(adb, "contains K + O + H") == kcl


def test_temperature(db):
    adb, ids = db
    downstream_of_400k = {ids["traj"], ids["sel"], ids["lab"]}
    assert q(adb, "T:400") == downstream_of_400k
    assert q(adb, "400 K") == downstream_of_400k
    assert q(adb, "T:250..350") == {ids["nacl"]}
    assert q(adb, "T>350") == downstream_of_400k


def test_model_and_ancestry(db):
    adb, ids = db
    assert q(adb, "model:MACE-MH-1") == {ids["traj"], ids["sel"], ids["lab"]}
    assert q(adb, "model:mace-mh-1 r2scan+d4") == {ids["lab"]}
    assert q(adb, f"from:{ids['traj']}") == {ids["sel"], ids["lab"]}
    assert q(adb, f"from:{ids['md']}") == {ids["traj"], ids["sel"], ids["lab"]}


def test_enhanced_sampling_and_misc(db):
    adb, ids = db
    assert q(adb, "es:any") == {ids["traj"], ids["sel"], ids["lab"]}
    assert q(adb, "es:metadynamics") == {ids["traj"], ids["sel"], ids["lab"]}
    assert q(adb, "has:stress") == {ids["lab"], ids["pbe"]}
    assert q(adb, "frames>3") == {ids["lab"], ids["pbe"]}
    assert q(adb, "code:vasp disp:d3") == {ids["pbe"]}


def test_facets_follow_later_provenance(db):
    """Facets update automatically when provenance is added after import."""
    adb, ids = db
    from atomdb.services.annotate import add_link

    with adb.session() as s:
        add_link(s, ids["nacl"], "derived_from", ids["lab"])
    assert ids["nacl"] in q(adb, "r2scan")


def test_unknown_element_rejected():
    from atomdb.errors import InvalidInput

    with pytest.raises(InvalidInput):
        parse("el:Kx")


def test_frame_search(db):
    adb, ids = db
    with adb.session() as s:
        r = search_frames(s, all_elements=["K", "Cl"])
        assert set(r["per_dataset"]) == {ids["traj"], ids["sel"], ids["lab"], ids["pbe"]}
        r = search_frames(s, any_elements=["Na"])
        assert r["total"] == 1 and r["items"][0]["dataset"] == ids["nacl"]


def test_generations_do_not_leak(adb, data_dir, tmp_path):
    """A trajectory from a model trained on r2SCAN data is not itself 'r2SCAN' or '400 K'."""
    from atomdb.services.records import ModelSpec, add_model, record_training

    lab = import_file(adb, data_dir / "kcl_labeled.extxyz").dataset.id
    traj = import_file(adb, data_dir / "kcl_plain.xyz").dataset.id
    new = import_file(adb, data_dir / "kcl_variable.extxyz").dataset.id
    with adb.session() as s:
        record_md(s, [lab], model="MACE-MH-1", md={"temperature_k": 400})
        record_dft(s, [lab], dft={"functional": "r2SCAN", "dispersion": "D4"})
        m = add_model(adb, s, ModelSpec(name="own-model"))
        record_training(s, m.id, [lab])
        record_md(s, [new], model=m.id, md={"temperature_k": 330})
    assert q(adb, "T:400") == {lab}
    assert q(adb, "r2scan") == {lab}
    assert q(adb, "model:MACE-MH-1") == {lab}
    assert q(adb, "model:own-model") == {new}
    assert q(adb, "lineage:r2scan") == {lab, new}
    assert q(adb, f"from:{lab}") == {new}
    assert traj not in q(adb, "lineage:r2scan")
