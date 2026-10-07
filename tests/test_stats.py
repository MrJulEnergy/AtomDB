from __future__ import annotations

import ase.io
import numpy as np
import pytest

from atomdb.frames import calc_results
from atomdb.services.importer import import_file
from atomdb.services.stats import Running, compare, dataset_stats


def test_running_matches_numpy():
    rng = np.random.default_rng(1)
    data = rng.normal(3, 2, size=10_000)
    r = Running()
    for chunk in np.array_split(data, 37):
        r.update(chunk)
    s = r.summary()
    assert s["count"] == data.size
    assert s["mean"] == pytest.approx(data.mean())
    assert s["std"] == pytest.approx(data.std())
    assert (s["min"], s["max"]) == (data.min(), data.max())


@pytest.mark.parametrize("name", ["kcl_labeled.extxyz", "kcl_variable.extxyz"])
def test_streaming_stats_equal_reference(adb, data_dir, name):
    frames = ase.io.read(data_dir / name, ":")
    ds = import_file(adb, data_dir / name).dataset.id
    with adb.session() as s:
        st = dataset_stats(adb, s, ds)
    forces = np.concatenate([calc_results(a)["forces"] for a in frames])
    mags = np.linalg.norm(forces, axis=1)
    q = st["quantities"]
    assert q["force_norm"]["count"] == len(mags)  # NaN padding excluded
    assert q["force_norm"]["mean"] == pytest.approx(mags.mean())
    assert q["force_x"]["std"] == pytest.approx(forces[:, 0].std())
    assert sum(q["force_norm"]["hist"]["counts"]) == len(mags)
    e = np.array([calc_results(a)["energy"] for a in frames])
    assert q["energy"]["mean"] == pytest.approx(e.mean())
    assert q["energy_per_atom"]["min"] == pytest.approx(min(e / [len(a) for a in frames]))
    k_mask = np.concatenate([a.numbers == 19 for a in frames])
    assert st["force_per_element"]["K"]["count"] == k_mask.sum()
    assert st["force_per_element"]["K"]["mean"] == pytest.approx(mags[k_mask].mean())
    assert list(st["force_per_element"]) == ["H", "O", "Cl", "K"]
    vol = frames[0].get_volume()
    assert q["cell_a"]["mean"] == pytest.approx(8.0)
    dens = frames[0].get_masses().sum() / vol * 1.66053906660
    assert q["density"]["min"] <= dens + 1e-9
    # cached second call
    with adb.session() as s:
        assert dataset_stats(adb, s, ds) == st


def test_compare_common_bins(adb, data_dir):
    a = import_file(adb, data_dir / "kcl_labeled.extxyz").dataset.id
    b = import_file(adb, data_dir / "kcl_znh5md.h5").dataset.id
    with adb.session() as s:
        c = compare(adb, s, [a, b])
    e0 = c["datasets"][0]["quantities"]["force_norm"]["hist"]["edges"]
    e1 = c["datasets"][1]["quantities"]["force_norm"]["hist"]["edges"]
    assert e0 == e1
    assert "force_norm" in c["summary"]
    assert 0 <= c["summary"]["force_norm"]["vs"][0]["hist_overlap"] <= 1
