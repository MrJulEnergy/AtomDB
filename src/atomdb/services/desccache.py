"""On-disk cache of descriptor vectors per (descriptor, dataset, frame).

PCA projections depend on the whole selection and are cheap; the descriptors are what
costs (MACE runs a model per frame). They are cached under
``cache/descriptors/<descriptor key>/<dataset>-<file sha>[-<elements>].npz`` so adding a
dataset to a plot, or raising ``max_frames``, only describes the frames not seen before.

How a descriptor's columns depend on ``fit(elements)`` decides the cache key
(``Descriptor.element_layout``):

* ``independent`` — never (MLIP features): one file per dataset.
* ``remap`` — columns are named per element; computed with the dataset's own elements
  and scattered into the selection's layout (absent elements are zero columns).
* ``keyed`` (default) — the selection's element set is part of the key.

Deletable at any time (``atomdb cache --clear``); never part of backups.
"""

from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import ase
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Dataset, DatasetElement
from ..descriptors.base import Descriptor
from .context import AtomDB


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def dataset_elements(s: Session, ds_id: str) -> list[str]:
    return sorted(s.execute(select(DatasetElement.symbol)
                            .where(DatasetElement.dataset_id == ds_id)).scalars())


class FrameCache:
    """Per-frame descriptor rows of one dataset (2-D per frame: atoms × features,
    or 1 × features for frame descriptors)."""

    def __init__(self, path: Path):
        self.path = path
        self.rows: dict[int, np.ndarray] = {}
        self.dirty = False
        self.hits = self.computed = 0
        if path.exists():
            try:
                with np.load(path) as z:
                    idx, counts, x = z["idx"], z["counts"], z["x"]
                starts = np.concatenate([[0], np.cumsum(counts)])
                for k, i in enumerate(idx):
                    self.rows[int(i)] = x[starts[k] : starts[k + 1]]
            except (OSError, KeyError, ValueError, EOFError, zipfile.BadZipFile):
                self.rows = {}  # corrupt / old format: start over

    def get(self, idx: list[int], frames: Callable[[list[int]], list[ase.Atoms]],
            compute: Callable[[list[ase.Atoms]], list[np.ndarray]]) -> list[np.ndarray]:
        missing = [i for i in idx if i not in self.rows]
        self.hits += len(idx) - len(missing)
        if missing:
            for i, x in zip(missing, compute(frames(missing)), strict=True):
                self.rows[i] = x
            self.computed += len(missing)
            self.dirty = True
        return [self.rows[i] for i in idx]

    def save(self) -> None:
        if not self.dirty or not self.rows:
            return
        idx = sorted(self.rows)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".npz.part")
        try:
            with os.fdopen(fd, "wb") as f:
                np.savez(f, idx=np.array(idx, dtype=np.int64),
                         counts=np.array([len(self.rows[i]) for i in idx], dtype=np.int64),
                         x=np.concatenate([self.rows[i] for i in idx]))
            os.replace(tmp, self.path)  # concurrent writers: last one wins, nothing breaks
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.dirty = False


def cache_path(adb: AtomDB, ds: Dataset, desc: Descriptor, elements: list[str] | None) -> Path:
    sha = ds.primary_file.sha256[:16] if ds.primary_file else "none"
    name = f"{ds.id}-{sha}" + (f"-{_h(','.join(elements))}" if elements is not None else "")
    return adb.paths.cache / "descriptors" / desc.cache_key() / f"{name}.npz"


def _layout(desc: Descriptor, own: list[str]) -> tuple[Descriptor, np.ndarray] | None:
    """For ``remap`` descriptors: a copy fitted on ``own`` elements and the column of each
    of its features in ``desc``'s (fitted) layout; None if the names do not line up."""
    d = copy.copy(desc)
    d.fit(own)
    target = {n: k for k, n in enumerate(desc.feature_names())}
    names = d.feature_names()
    if not names or any(n not in target for n in names):
        return None
    return d, np.array([target[n] for n in names], dtype=int)


def frame_vectors(adb: AtomDB, s: Session, ds: Dataset, desc: Descriptor, idx: list[int],
                  src, chunk: int = 128) -> tuple[np.ndarray, FrameCache]:
    """Descriptor rows (in ``desc``'s fitted layout) for frames ``idx`` of ``ds``."""
    lay = None
    if desc.element_layout == "remap":
        lay = _layout(desc, dataset_elements(s, ds.id))
    if desc.element_layout == "independent":
        cache, d = FrameCache(cache_path(adb, ds, desc, None)), desc
    elif lay is not None:
        cache, d = FrameCache(cache_path(adb, ds, desc, None)), lay[0]
    else:
        cache, d = FrameCache(cache_path(adb, ds, desc, desc.elements)), desc

    def compute(frames):
        return [r[None] for r in d.compute(frames)]

    try:
        rows = []
        for start in range(0, len(idx), chunk):
            part = idx[start : start + chunk]
            rows += cache.get(part, lambda ii: [src.get(i) for i in ii], compute)
    finally:
        cache.save()
    n = len(desc.feature_names()) if lay is not None else None
    x = np.vstack(rows) if rows else np.zeros((0, n or 0))
    if lay is not None:
        out = np.zeros((len(x), n))
        out[:, lay[1]] = x
        x = out
    return x, cache


def atom_cache(adb: AtomDB, ds: Dataset, desc: Descriptor) -> FrameCache:
    elements = None if desc.element_layout == "independent" else desc.elements
    return FrameCache(cache_path(adb, ds, desc, elements))


def atom_compute(desc: Descriptor) -> Callable[[list[ase.Atoms]], list[np.ndarray]]:
    def compute(frames):
        x = np.asarray(desc.compute_atoms(frames), dtype=np.float32)
        out, pos = [], 0
        for a in frames:
            out.append(x[pos : pos + len(a)])
            pos += len(a)
        return out

    return compute


def usage(adb: AtomDB) -> dict:
    root = adb.paths.cache
    out = {}
    if root.exists():
        for sub in sorted(p for p in root.iterdir() if p.is_dir()):
            files = [f for f in sub.rglob("*") if f.is_file()]
            out[sub.name] = {"files": len(files), "bytes": sum(f.stat().st_size for f in files)}
    return {"path": str(root), "caches": out}


def clear(adb: AtomDB, which: list[str] | None = None) -> dict:
    import shutil

    before = usage(adb)
    for name in before["caches"]:
        if which is None or name in which:
            shutil.rmtree(adb.paths.cache / name)
    return {"path": before["path"],
            "removed": {k: v for k, v in before["caches"].items() if which is None or k in which}}
