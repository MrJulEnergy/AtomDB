from __future__ import annotations

import ase
import ase.io
import numpy as np
import pytest

from atomdb.descriptors import get_descriptor
from atomdb.descriptors.pca import pca
from atomdb.services.embedding import embed, similarity
from atomdb.services.importer import import_file


def test_composition_rdf_shapes_and_pbc(data_dir):
    frames = ase.io.read(data_dir / "kcl_labeled.extxyz", ":")
    d = get_descriptor("composition-rdf")
    d.fit(["Cl", "H", "K", "O"])
    x = d.compute(frames)
    assert x.shape == (4, 4 + 10 * 22) and len(d.feature_names()) == x.shape[1]
    np.testing.assert_allclose(x[:, :4].sum(1), 1.0)
    # translation invariance
    shifted = [a.copy() for a in frames]
    for a in shifted:
        a.positions += 1.3
        a.wrap()
    np.testing.assert_allclose(d.compute(shifted), x, atol=1e-9)


def test_pca_explained_variance():
    rng = np.random.default_rng(0)
    x = np.c_[rng.normal(0, 5, 200), rng.normal(0, 1, 200), np.zeros(200)]
    p = pca(x, 2)
    assert p["coords"].shape == (200, 2)
    assert 0.45 < p["explained"][0] < 0.6  # standardized → ~equal variance


def _md_like(symbols, n, seed, noise=0.05):
    """Thermal fluctuations around one structure (what an MD trajectory looks like)."""
    rng = np.random.default_rng(seed)
    base = ase.Atoms(symbols, positions=np.random.default_rng(0).random((len(symbols), 3)) * 7,
                     cell=[8, 8, 8], pbc=True)
    out = []
    for _ in range(n):
        a = base.copy()
        a.positions += rng.normal(0, noise, a.positions.shape)
        out.append(a)
    return out


def test_embed_and_similarity(adb, tmp_path):
    kcl = ["K", "Cl"] + ["O", "H", "H"] * 4
    ase.io.write(tmp_path / "ref.extxyz", _md_like(kcl, 40, 1))
    ase.io.write(tmp_path / "same.extxyz", _md_like(kcl, 15, 2))
    ase.io.write(tmp_path / "new.extxyz", _md_like(["Na", "Cl"] + ["O", "H", "H"] * 4, 15, 3))
    ref, same, new = (import_file(adb, tmp_path / f"{n}.extxyz").dataset.id
                      for n in ("ref", "same", "new"))
    with adb.session() as s:
        e = embed(adb, s, [ref, new], max_frames=10)
        assert e["exploratory"] and len(e["points"]) == 20
        assert {p["dataset"] for p in e["points"]} == {ref, new}
        sim_new = similarity(adb, s, ref, new)
        assert sim_new["novel_fraction"] == 1.0
        assert "Na" in sim_new["composition"]["elements_only_in_other"]
        sim_same = similarity(adb, s, ref, same)
        assert sim_same["novel_fraction"] <= 0.2
        assert sim_same["novel_fraction"] < sim_new["novel_fraction"]


def test_soap_optional():
    from atomdb.descriptors import list_descriptors

    names = {d["name"]: d for d in list_descriptors()}
    assert "soap" in names and "composition-rdf" in names
    if not names["soap"]["available"]:
        from atomdb.errors import InvalidInput

        with pytest.raises(InvalidInput, match="optional"):
            get_descriptor("soap")
