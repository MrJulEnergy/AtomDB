from __future__ import annotations

import ase
import ase.io
import numpy as np
import pytest
from sqlalchemy import func, select

from atomdb.db.models import Dataset, Edge, File, Frame
from atomdb.errors import InvalidInput
from atomdb.services.importer import import_file
from atomdb.services.records import record_sampling
from atomdb.services.remove import remove_dataset, vacuum
from atomdb.services.search import category_counts, search
from atomdb.services.validate import validate


def _frames(n, seed):
    rng = np.random.default_rng(seed)
    return [ase.Atoms("OH2", positions=rng.random((3, 3)) * 3, cell=[6, 6, 6], pbc=True)
            for _ in range(n)]


def test_remove_keeps_stub_and_lineage(adb, tmp_path):
    ase.io.write(tmp_path / "big.extxyz", _frames(20, 1))
    ase.io.write(tmp_path / "sub.extxyz", _frames(3, 2))
    big = import_file(adb, tmp_path / "big.extxyz", name="big parent").dataset.id
    sub = import_file(adb, tmp_path / "sub.extxyz").dataset.id
    with adb.session() as s:
        record_sampling(s, big, sub, "random")
        paths = [adb.store.abspath(df.file.store_relpath) for df in s.get(Dataset, big).files]
    assert all(p.exists() for p in paths)

    with adb.session() as s:
        r = remove_dataset(adb, s, big, reason="too large")
    assert r["removed"] and r["bytes"] > 0
    assert not any(p.exists() for p in paths)
    with adb.session() as s:
        ds = s.get(Dataset, big)
        assert ds.status == "removed"
        attrs = {a.key: a.value for a in ds.attrs}
        assert attrs["removed_reason"] == "too large" and attrs["removed_n_frames"] == 20
        assert attrs["removed_files"][0]["sha256"]
        assert s.execute(select(func.count()).select_from(Frame)
                         .where(Frame.dataset_id == big)).scalar_one() == 0
        assert all(f.status == "removed" for f in s.execute(select(File)).scalars()
                   if f.id in {x["id"] for x in attrs["removed_files"]})
        # lineage of the subset is kept
        assert s.execute(select(Edge).where(Edge.src == sub, Edge.dst == big)).first() is not None
        # hidden from search and the sidebar, still findable on purpose
        assert {i.id for i in search(s, "")["items"]} == {sub}
        assert sum(x["count"] for x in category_counts(s)["geometry"]) == 1
        assert {i.id for i in search(s, "status:removed")["items"]} == {big}
        with pytest.raises(InvalidInput, match="already removed"):
            remove_dataset(adb, s, big)
    rep = validate(adb)
    assert not [p for p in rep["problems"] if p["severity"] == "error"], rep["problems"]
    before, after = vacuum(adb)
    assert after <= before


def test_remove_refuses_shared_file(adb, tmp_path, data_dir):
    a = import_file(adb, data_dir / "kcl_znh5md.h5").dataset.id
    with adb.session() as s:
        ds = s.get(Dataset, a)
        from atomdb.db.models import DatasetFile

        other = Dataset(id="DS-2099-000001", name="shares the file")
        s.add(other)
        s.flush()
        s.add(DatasetFile(dataset_id=other.id, file_id=ds.primary_file_id, role="canonical_h5md"))
    with adb.session() as s, pytest.raises(InvalidInput, match="also a file of"):
        remove_dataset(adb, s, a)
