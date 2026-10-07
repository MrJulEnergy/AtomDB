"""Import/conversion fidelity: nothing may be lost or altered on the way into H5MD."""

from __future__ import annotations

import ase.io
import numpy as np
import pytest

from atomdb.errors import FormatError, InvalidInput
from atomdb.formats.h5md import H5MDFrameSource, read_stamp
from atomdb.formats.registry import detect
from atomdb.frames import calc_results, element_mask, fingerprint, mask_for_symbols
from atomdb.services import datasets as dsvc
from atomdb.services.importer import import_file
from atomdb.store.hashing import sha256_file


def canonical_frames(adb, ds_id):
    with adb.session() as s:
        ds = dsvc.get_dataset(s, ds_id)
        path = adb.store.abspath(ds.primary_file.store_relpath)
    src = H5MDFrameSource(path)
    return [src.get(i) for i in range(len(src))], path


@pytest.mark.parametrize(
    "name,fmt",
    [
        ("kcl_plain.xyz", "xyz"),
        ("kcl_labeled.extxyz", "extxyz"),
        ("kcl_znh5md.h5", "h5md"),
        ("POSCAR", "vasp"),
        ("kcl.cif", "cif"),
        ("kcl.pdb", "proteindatabank"),
        ("kcl.gro", "gromacs"),
        ("kcl.lmp", "lammps-data"),
        ("kcl.dump", "lammps-dump"),
    ],
)
def test_detect(data_dir, name, fmt):
    assert detect(data_dir / name).name == fmt


def test_extxyz_full_preservation(adb, data_dir):
    path = data_dir / "kcl_labeled.extxyz"
    ref = ase.io.read(path, ":")
    r = import_file(adb, path)
    assert r.dataset.n_frames == len(ref) == 4
    assert r.dataset.has_energy and r.dataset.has_forces and r.dataset.has_stress
    assert r.dataset.elements == ["H", "O", "Cl", "K"]
    got, _ = canonical_frames(adb, r.dataset.id)
    for a, b in zip(ref, got):
        assert a.get_chemical_symbols() == b.get_chemical_symbols()
        np.testing.assert_allclose(a.positions, b.positions)
        np.testing.assert_allclose(a.cell[:], b.cell[:])
        assert list(a.pbc) == list(b.pbc)
        ra, rb = calc_results(a), calc_results(b)
        np.testing.assert_allclose(ra["energy"], rb["energy"])
        np.testing.assert_allclose(ra["forces"], rb["forces"])
        np.testing.assert_allclose(ra["stress"], rb["stress"])
        assert b.info["config_type"] == a.info["config_type"]
        np.testing.assert_allclose(b.info["dipole_custom"], a.info["dipole_custom"])
        assert float(b.info["temperature"]) == a.info["temperature"]
        np.testing.assert_allclose(b.arrays["charges_custom"], a.arrays["charges_custom"])


def test_property_catalog_records_everything(adb, data_dir):
    r = import_file(adb, data_dir / "kcl_labeled.extxyz")
    from sqlalchemy import select

    from atomdb.db.models import PropertyDef

    with adb.session() as s:
        props = {
            (p.origin, p.name): p
            for p in s.execute(
                select(PropertyDef).where(PropertyDef.dataset_id == r.dataset.id)
            ).scalars()
        }
    for key in [("info", "config_type"), ("info", "dipole_custom"), ("info", "temperature"),
                ("arrays", "charges_custom"), ("calc", "energy"), ("calc", "forces"),
                ("calc", "stress")]:
        assert key in props, key
    assert props[("calc", "forces")].unit == "eV/Å"
    assert props[("calc", "forces")].level == "atom"
    assert props[("arrays", "charges_custom")].shape == [-1]


def test_variable_atoms_and_partial_pbc(adb, data_dir):
    path = data_dir / "kcl_variable.extxyz"
    ref = ase.io.read(path, ":")
    r = import_file(adb, path)
    assert (r.dataset.n_atoms_min, r.dataset.n_atoms_max) == (5, 11)
    got, _ = canonical_frames(adb, r.dataset.id)
    assert [len(a) for a in got] == [len(a) for a in ref]
    assert list(got[1].pbc) == [True, True, False]
    np.testing.assert_allclose(calc_results(got[2])["forces"], calc_results(ref[2])["forces"])


def test_existing_h5md_stored_as_is(adb, data_dir):
    path = data_dir / "kcl_znh5md.h5"
    r = import_file(adb, path)
    with adb.session() as s:
        d = dsvc.detail(adb, s, dsvc.get_dataset(s, r.dataset.id))
    files = {f.file.id for f in d.files}
    assert len(files) == 1  # canonical == original, no re-encoding
    assert d.files[0].file.sha256 == sha256_file(path)
    assert read_stamp(adb.store.path_for(r.sha256)) is None  # imported files never modified


def test_converted_h5md_is_stamped(adb, data_dir):
    path = data_dir / "kcl_labeled.extxyz"
    r = import_file(adb, path)
    _, h5 = canonical_frames(adb, r.dataset.id)
    stamp = read_stamp(h5)
    assert stamp["dataset_id"] == r.dataset.id
    assert stamp["source_sha256"] == sha256_file(path)
    assert stamp["software"]["ase"]


def test_import_never_modifies_source(adb, data_dir):
    for name in ["kcl_labeled.extxyz", "kcl_znh5md.h5", "POSCAR"]:
        p = data_dir / name
        before = (sha256_file(p), p.stat().st_mtime_ns)
        import_file(adb, p)
        assert (sha256_file(p), p.stat().st_mtime_ns) == before


def test_lammps_dump_requires_type_map(adb, data_dir):
    with pytest.raises(InvalidInput, match="type map"):
        import_file(adb, data_dir / "kcl.dump")
    r = import_file(adb, data_dir / "kcl.dump", read_options={"type_map": "1=K,2=Cl"})
    assert r.dataset.elements == ["Cl", "K"]
    assert r.dataset.n_frames == 2
    assert r.dataset.has_forces


@pytest.mark.parametrize(
    "name", ["corrupt_truncated.extxyz", "corrupt_garbage.xyz", "corrupt_truncated.h5"]
)
def test_corrupted_input_is_rejected_cleanly(adb, data_dir, name):
    with pytest.raises(FormatError):
        import_file(adb, data_dir / name)
    with adb.session() as s:
        assert dsvc.list_datasets(s)[0] == 0  # nothing half-imported


def test_lammps_data_types(adb, data_dir):
    with pytest.raises(InvalidInput, match="type map"):
        import_file(adb, data_dir / "kcl_nomass.lmp")
    r = import_file(adb, data_dir / "kcl_nomass.lmp", read_options={"type_map": "1=K,2=Cl"})
    assert r.dataset.elements == ["Cl", "K"]
    r2 = import_file(adb, data_dir / "kcl.lmp")
    assert r2.dataset.elements == ["Cl", "K"]
    assert any("inferred from LAMMPS masses" in w for w in r2.warnings)


def test_single_structure_formats(adb, data_dir):
    for name in ["POSCAR", "kcl.cif", "kcl.pdb", "kcl.gro", "kcl.lmp"]:
        r = import_file(adb, data_dir / name)
        assert r.dataset.n_frames == 1, name
        assert r.dataset.elements == ["Cl", "K"], name


def test_frame_index_and_masks(adb, data_dir):
    from sqlalchemy import select

    from atomdb.db.models import Frame

    r = import_file(adb, data_dir / "kcl_labeled.extxyz")
    with adb.session() as s:
        frames = s.execute(
            select(Frame).where(Frame.dataset_id == r.dataset.id).order_by(Frame.idx)
        ).scalars().all()
    assert [f.idx for f in frames] == [0, 1, 2, 3]
    lo, _ = mask_for_symbols(["K", "Cl"])
    assert all((f.elem_mask_lo & lo) == lo for f in frames)
    assert frames[0].temperature == 400.0
    assert frames[0].energy_per_atom == pytest.approx(frames[0].energy / frames[0].n_atoms)


def test_element_mask_high_z():
    lo, hi = element_mask([1, 62, 63, 118])
    assert lo == (1 << 0) | (1 << 61)
    assert hi == (1 << 0) | (1 << 55)
    assert lo < 2**63 and hi < 2**63


def test_fingerprint_stable_under_float32(data_dir):
    a = ase.io.read(data_dir / "kcl_labeled.extxyz", 0)
    b = a.copy()
    b.positions = b.positions.astype(np.float32).astype(np.float64)
    assert fingerprint(a) == fingerprint(b)
    c = a.copy()
    c.positions[0] += 0.5
    assert fingerprint(a) != fingerprint(c)


def test_binary_md_with_topology(adb, data_dir):
    pytest.importorskip("MDAnalysis")
    with pytest.raises(InvalidInput, match="topology"):
        import_file(adb, data_dir / "kcl_md.dcd")
    topo = data_dir / "kcl_md.gro"
    # reference is the .gro (0.01 Å precision); frame 0 of the trajectories equals it
    for ext, atol in (("dcd", 6e-3), ("xtc", 6e-3)):
        r = import_file(adb, data_dir / f"kcl_md.{ext}", read_options={"topology": str(topo)})
        assert r.dataset.n_frames == 3
        assert r.dataset.elements == ["H", "O", "Cl", "K"]
        got, _ = canonical_frames(adb, r.dataset.id)
        ref = ase.io.read(topo)
        np.testing.assert_allclose(got[0].positions, ref.positions, atol=atol)
        with adb.session() as s:
            roles = {df.role for df in dsvc.get_dataset(s, r.dataset.id).files}
        assert "topology" in roles
