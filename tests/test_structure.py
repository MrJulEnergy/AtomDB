from __future__ import annotations

import ase
import ase.io
import numpy as np
from ase.calculators.singlepoint import SinglePointCalculator

from atomdb.services.importer import import_file
from atomdb.services.search import search


def _facets(adb, ds_id):
    from atomdb.services.search import facets_for

    with adb.session() as s:
        return facets_for(s, [ds_id])[ds_id]


def _write(path, frames):
    ase.io.write(path, frames)
    return path


def _water(cell, pbc=True, n=3, momenta=False, stress=False, rng=None):
    rng = rng or np.random.default_rng(0)
    out = []
    for k in range(n):
        a = ase.Atoms("OH2" * 2, positions=rng.random((6, 3)) * 3, cell=cell, pbc=pbc)
        if momenta:
            a.set_momenta(rng.normal(size=(6, 3)))
        res = {"energy": -1.0 - k, "forces": rng.normal(size=(6, 3))}
        if stress:
            res["stress"] = rng.normal(size=6)
        a.calc = SinglePointCalculator(a, **res)
        out.append(a)
    return out


def test_categories_from_frames(adb, tmp_path):
    bulk = import_file(adb, _write(tmp_path / "bulk.extxyz", _water(
        [8, 8, 8], momenta=True))).dataset.id
    slab = import_file(adb, _write(tmp_path / "slab.extxyz", _water(
        [8, 8, 30], pbc=[True, True, False]))).dataset.id
    npt = _water([[8, 0, 0], [1, 8, 0], [0, 0, 9]], stress=True)
    for k, a in enumerate(npt):
        a.set_cell(np.asarray(a.cell) * (1 + 0.01 * k), scale_atoms=True)
    npt.append(ase.Atoms("KCl", positions=[[0, 0, 0], [3, 0, 0]], cell=[9, 9, 9], pbc=True))
    npt[-1].calc = SinglePointCalculator(npt[-1], energy=0.0, forces=np.zeros((2, 3)),
                                         stress=np.zeros(6))
    npt_id = import_file(adb, _write(tmp_path / "npt.extxyz", npt)).dataset.id

    f = _facets(adb, bulk)
    assert f["geometry"] == ["pbc"] and f["cell"] == ["fixed"]
    assert f["cell_shape"] == ["cubic"] and "velocities" in f["atom_data"]
    assert "charges" not in f.get("atom_data", [])  # all-zero initial_charges are ignored
    assert set(f["labels"]) == {"energy", "forces"}
    assert f["composition"] == ["fixed"] and f["n_elements"] == ["binary"]
    assert f["size"] == ["small"]
    f = _facets(adb, slab)
    assert f["geometry"] == ["partial-pbc"] and "cell" not in f
    f = _facets(adb, npt_id)
    assert f["cell"] == ["variable"] and f["cell_shape"] == ["triclinic"]  # most general shape
    assert "stress" in f["labels"] and f["composition"] == ["variable"]
    assert f["n_elements"] == ["quaternary"]

    with adb.session() as s:
        assert {i.id for i in search(s, "labels:stress")["items"]} == {npt_id}
        assert {i.id for i in search(s, "pbc:slab,periodic cell:fixed")["items"]} == {bulk}
        assert {i.id for i in search(s, "atom_data:velocities")["items"]} == {bulk}


def test_backfill_matches_import(adb, tmp_path):
    from atomdb.db.models import Dataset
    from atomdb.services.structure import backfill

    frames = _water([[8, 0, 0], [0, 8, 0], [0, 0, 9]], momenta=True, n=5)
    frames.append(ase.Atoms("OH2", positions=np.eye(3)))  # cluster frame -> mixed
    ds_id = import_file(adb, _write(tmp_path / "w.extxyz", frames)).dataset.id
    with adb.session() as s:
        ds = s.get(Dataset, ds_id)
        imported = ds.structure
        ds.structure = None
    assert _facets(adb, ds_id)["geometry"] == ["unknown"]
    with adb.session() as s:
        assert backfill(adb, s) == [ds_id]
    with adb.session() as s:
        filled = s.get(Dataset, ds_id).structure
    assert filled["pbc"] == imported["pbc"] == {"TTT": 5, "FFF": 1}
    assert filled["cell_shape"] == imported["cell_shape"] == {"orthorhombic": 5}
    np.testing.assert_allclose(filled["volume"], imported["volume"])
    assert filled["n_formulas"] == imported["n_formulas"] == 2
    assert filled["arrays"]["momenta"] and imported["arrays"]["momenta"]
    assert _facets(adb, ds_id)["geometry"] == ["mixed"]
