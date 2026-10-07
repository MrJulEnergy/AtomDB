from __future__ import annotations

import shutil

import ase.io

from atomdb.formats.h5md import H5MDAdapter
from atomdb.services.converter import convert
from atomdb.services.importer import import_file
from atomdb.services.whatis import whatis


def test_exact_copy_identified(adb, data_dir, tmp_path):
    ds = import_file(adb, data_dir / "kcl_znh5md.h5").dataset
    copy = tmp_path / "mystery_renamed.h5"
    shutil.copy(data_dir / "kcl_znh5md.h5", copy)
    r = whatis(adb, copy)
    assert r["exact_match"]["datasets"][0]["id"] == ds.id
    assert r["exact_match"]["file"]["origins"][0]["original_name"] == "kcl_znh5md.h5"


def test_resaved_subset_identified_by_content(adb, data_dir, tmp_path):
    ds = import_file(adb, data_dir / "kcl_labeled.extxyz").dataset
    frames = ase.io.read(data_dir / "kcl_labeled.extxyz", "1:3")
    out = tmp_path / "subset.h5"
    H5MDAdapter().write(frames, out)  # re-encoded, different bytes
    r = whatis(adb, out)
    assert r["exact_match"] is None
    m = r["content_matches"][0]
    assert m["dataset"]["id"] == ds.id
    assert m["matched_frames"] == 2 and m["dataset_frames"] == "1-2"
    assert m["coordinates_confirmed"] is True


def test_converted_output_has_stamp(adb, data_dir, tmp_path):
    ds = import_file(adb, data_dir / "kcl_labeled.extxyz").dataset
    out = tmp_path / "exported.h5"
    convert(adb, ds.id, out)
    r = whatis(adb, out)
    assert r["stamp"]["exported_from_dataset"] == ds.id
    assert r["content_matches"][0]["dataset"]["id"] == ds.id


def test_unknown_file(adb, data_dir):
    r = whatis(adb, data_dir / "POSCAR")
    assert r["verdict"] == "unknown" and r["content_matches"] == []
