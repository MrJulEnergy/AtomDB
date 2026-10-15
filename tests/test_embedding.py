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


class _StubAtoms:
    """Per-atom descriptor without torch: one-hot element + distance to the cell centre."""

    name = "stub-atoms"
    description = "test"
    per_atom = True

    def fit(self, elements):
        self.elements = sorted(elements)

    def compute_atoms(self, frames):
        rows = []
        for a in frames:
            d = np.linalg.norm(a.positions - a.cell.sum(0) / 2, axis=1)
            for sym, r in zip(a.get_chemical_symbols(), d, strict=True):
                rows.append([float(sym == e) for e in self.elements] + [r])
        return np.array(rows)


@pytest.fixture
def stub_atoms(monkeypatch):
    from atomdb.descriptors import base

    class Stub(_StubAtoms, base.AtomDescriptor):
        pass

    reg = {**base._registry(), "stub-atoms": Stub}
    monkeypatch.setattr(base, "_registry", lambda: reg)
    return "stub-atoms"


def test_embed_atoms_stratified_and_filtered(adb, tmp_path, stub_atoms):
    from atomdb.services.embedding import _stratified, embed_atoms

    water_kcl = ["K", "Cl"] + ["O", "H", "H"] * 10
    ase.io.write(tmp_path / "a.extxyz", _md_like(water_kcl, 20, 1))
    ase.io.write(tmp_path / "b.extxyz", _md_like(["O", "H", "H"] * 8, 20, 2))
    a, b = (import_file(adb, tmp_path / f"{n}.extxyz").dataset.id for n in ("a", "b"))
    with adb.session() as s:
        e = embed_atoms(adb, s, [a, b], stub_atoms, max_frames=10, max_atoms=40)
        assert e["per_atom"] and {p["dataset"] for p in e["points"]} == {a, b}
        ds_a = next(d for d in e["datasets"] if d["id"] == a)
        # rare K/Cl keep all their atoms; the budget is shared between elements
        assert ds_a["n_atoms"] == 40 and ds_a["elements"]["K"] == 10 and ds_a["elements"]["Cl"] == 10
        o = embed_atoms(adb, s, [a, b], stub_atoms, max_frames=5, max_atoms=30, elements=["O"])
        assert {p["element"] for p in o["points"]} == {"O"} and o["element_filter"] == ["O"]
        with pytest.raises(Exception, match="per-atom"):
            embed_atoms(adb, s, [a], "composition-rdf")
    rng = np.random.default_rng(0)
    pick = _stratified(np.array(["H"] * 90 + ["O"] * 9 + ["K"]), 21, rng)
    assert len(pick) == 21 and 99 in pick


def test_embed_atoms_skips_large(adb, tmp_path, stub_atoms, monkeypatch):
    from atomdb.services import embedding

    ase.io.write(tmp_path / "a.extxyz", _md_like(["O", "H", "H"] * 4, 12, 1))
    ase.io.write(tmp_path / "big.extxyz", _md_like(["O", "H", "H"] * 4, 30, 2))
    a, big = (import_file(adb, tmp_path / f"{n}.extxyz").dataset.id for n in ("a", "big"))
    monkeypatch.setattr(embedding, "LARGE_FRAMES", 20)
    with adb.session() as s:
        e = embedding.embed_atoms(adb, s, [a, big], stub_atoms, max_atoms=10)
        assert [d["id"] for d in e["datasets"]] == [a] and e["skipped"][0]["id"] == big
        e = embedding.embed_atoms(adb, s, [a, big], stub_atoms, max_atoms=10, allow_large=True)
        assert len(e["datasets"]) == 2 and not e["skipped"]


@pytest.mark.skipif(not __import__("importlib").util.find_spec("mace"), reason="mace-torch not installed")
def test_mace_descriptor_per_atom(data_dir):
    frames = ase.io.read(data_dir / "kcl_labeled.extxyz", ":2")
    d = get_descriptor("mace-mp0-small")
    d.fit(["Cl", "H", "K", "O"])
    x = d.compute_atoms(frames)
    assert x.shape[0] == sum(len(a) for a in frames) and x.shape[1] > 0
    assert d.compute(frames).shape == (2, x.shape[1])


def test_descriptor_cache_reused_across_selections(adb, tmp_path, stub_atoms, monkeypatch):
    from atomdb.descriptors.simple import CompositionRDF
    from atomdb.services import embedding

    kcl = ["K", "Cl"] + ["O", "H", "H"] * 4
    ase.io.write(tmp_path / "a.extxyz", _md_like(kcl, 30, 1))
    ase.io.write(tmp_path / "b.extxyz", _md_like(["O", "H", "H"] * 4, 20, 2))
    ase.io.write(tmp_path / "c.extxyz", _md_like(["Na", "Cl"] + ["O", "H", "H"] * 4, 20, 3))
    a, b, c = (import_file(adb, tmp_path / f"{n}.extxyz").dataset.id for n in "abc")
    with adb.session() as s:
        e1 = embedding.embed(adb, s, [a, b], max_frames=10)
        assert e1["cache"] == {"frames_cached": 0, "frames_computed": 20}
        # different selection (and element set): a and b come from the cache, only c is new
        e2 = embedding.embed(adb, s, [a, c, b], max_frames=10)
        assert e2["cache"] == {"frames_cached": 20, "frames_computed": 10}
        # a dataset samples the same frames whatever else is selected
        assert ([p["frame"] for p in e1["points"] if p["dataset"] == a]
                == [p["frame"] for p in e2["points"] if p["dataset"] == a])
        # cached vectors (computed with b's own elements) equal a direct computation
        d = embedding.describe_frames(adb, s, [a, c, b], max_frames=10)
        frames = ase.io.read(tmp_path / "b.extxyz", ":")
        desc = CompositionRDF()
        desc.fit(d["elements"])
        rows = [k for k, m in enumerate(d["meta"]) if m["dataset"] == b]
        np.testing.assert_allclose(
            d["x"][rows], desc.compute([frames[d["meta"][k]["frame"]] for k in rows]), atol=1e-12)
        # a larger sample keeps the smaller one: only the extra frames are computed
        assert embedding.embed(adb, s, [a], max_frames=15)["cache"] == {
            "frames_cached": 10, "frames_computed": 5}

        x1 = embedding.embed_atoms(adb, s, [a, b], stub_atoms, max_frames=5, max_atoms=20)
        assert x1["cache"]["frames_computed"] == 10
        x2 = embedding.embed_atoms(adb, s, [a, b], stub_atoms, max_frames=5, max_atoms=20,
                                   elements=["O"])
        assert x2["cache"] == {"frames_cached": 10, "frames_computed": 0}

    from atomdb.services import desccache

    assert desccache.usage(adb)["caches"]["descriptors"]["files"] >= 5
    desccache.clear(adb, ["descriptors"])
    assert "descriptors" not in desccache.usage(adb)["caches"]
