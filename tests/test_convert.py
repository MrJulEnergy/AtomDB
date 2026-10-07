from __future__ import annotations

import ase.io
import numpy as np
import pytest

from atomdb.errors import DataLossError, InvalidInput
from atomdb.formats.h5md import H5MDFrameSource
from atomdb.frames import calc_results
from atomdb.services.converter import convert
from atomdb.services.importer import import_file


def test_extxyz_to_h5md_lossless(adb, data_dir, tmp_path):
    out = tmp_path / "out.h5"
    r = convert(adb, str(data_dir / "kcl_labeled.extxyz"), out)
    assert r["lost_fields"] == [] and r["n_frames"] == 4
    ref = ase.io.read(data_dir / "kcl_labeled.extxyz", ":")
    got = H5MDFrameSource(out)
    for i, a in enumerate(ref):
        b = got.get(i)
        np.testing.assert_allclose(calc_results(a)["forces"], calc_results(b)["forces"])
        assert b.info["config_type"] == a.info["config_type"]
    assert r["conversion_id"].startswith("CNV-")


def test_lossy_conversion_refused_then_allowed(adb, data_dir, tmp_path):
    out = tmp_path / "out.xyz"
    with pytest.raises(DataLossError) as exc:
        convert(adb, str(data_dir / "kcl_labeled.extxyz"), out, to="xyz")
    lost = exc.value.details["lost"]
    assert "calc.forces" in lost and "calc.energy" in lost and "info.config_type" in lost
    assert not out.exists()
    r = convert(adb, str(data_dir / "kcl_labeled.extxyz"), out, to="xyz", allow_loss=True)
    assert set(lost) == set(r["lost_fields"])
    assert out.exists()


def test_never_overwrites(adb, data_dir, tmp_path):
    out = tmp_path / "exists.extxyz"
    out.write_text("keep me")
    with pytest.raises(InvalidInput, match="exists"):
        convert(adb, str(data_dir / "kcl_znh5md.h5"), out)
    assert out.read_text() == "keep me"


def test_multi_frame_into_single_structure_format(adb, data_dir, tmp_path):
    with pytest.raises(InvalidInput, match="one structure"):
        convert(adb, str(data_dir / "kcl_labeled.extxyz"), tmp_path / "POSCAR", allow_loss=True)


def test_convert_dataset_id_back_to_extxyz(adb, data_dir, tmp_path):
    ds = import_file(adb, data_dir / "kcl_labeled.extxyz").dataset
    out = tmp_path / "roundtrip.extxyz"
    r = convert(adb, ds.id, out)
    assert r["lost_fields"] == [] and r["input_dataset"] == ds.id
    ref = ase.io.read(data_dir / "kcl_labeled.extxyz", ":")
    back = ase.io.read(out, ":")
    for a, b in zip(ref, back):
        np.testing.assert_allclose(calc_results(a)["stress"], calc_results(b)["stress"])
        np.testing.assert_allclose(a.arrays["charges_custom"], b.arrays["charges_custom"])


def test_convert_without_database(data_dir, tmp_path):
    r = convert(None, str(data_dir / "kcl_znh5md.h5"), tmp_path / "x.extxyz")
    assert r["conversion_id"] is None and r["n_frames"] == 5
