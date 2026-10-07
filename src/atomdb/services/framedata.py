"""Lazy access to frame data in a dataset's canonical H5MD (one frame at a time)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Dataset, Frame
from ..errors import IntegrityProblem, NotFound
from ..formats.h5md import H5MDFrameSource
from ..frames import calc_results, get_temperature
from .context import AtomDB
from .datasets import get_dataset


@lru_cache(maxsize=32)
def _source(path: str, mtime: float) -> H5MDFrameSource:
    return H5MDFrameSource(Path(path))


def open_dataset(adb: AtomDB, ds: Dataset) -> H5MDFrameSource:
    if ds.primary_file is None:
        raise IntegrityProblem(f"{ds.id} has no canonical file")
    p = adb.store.abspath(ds.primary_file.store_relpath)
    if not p.exists():
        raise IntegrityProblem(f"store file missing for {ds.id}: {p}")
    return _source(str(p), p.stat().st_mtime)


def _jsonable(v):
    if isinstance(v, np.ndarray):
        return v.tolist()
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def get_frame(adb: AtomDB, s: Session, ds_id: str, idx: int) -> dict:
    ds = get_dataset(s, ds_id)
    if not 0 <= idx < (ds.n_frames or 0):
        raise NotFound(f"{ds.id} has no frame {idx} (0..{(ds.n_frames or 1) - 1})")
    atoms = open_dataset(adb, ds).get(idx)
    res = calc_results(atoms)
    t, t_src = get_temperature(atoms)
    row = s.get(Frame, (ds.id, idx))
    return {
        "dataset": ds.id, "index": idx, "n_atoms": len(atoms),
        "formula": row.formula if row else atoms.get_chemical_formula(),
        "symbols": atoms.get_chemical_symbols(),
        "numbers": atoms.numbers.tolist(),
        "positions": atoms.positions.tolist(),
        "cell": np.asarray(atoms.cell).tolist(),
        "pbc": [bool(x) for x in atoms.pbc],
        "energy": row.energy if row else None,
        "energy_per_atom": row.energy_per_atom if row else None,
        "fmax": row.fmax if row else None,
        "temperature": t, "temperature_source": t_src,
        "volume": row.volume if row else None,
        "time": row.time if row else None, "step": row.step if row else None,
        "calc": {k: _jsonable(v) for k, v in res.items()},
        "info": {k: _jsonable(v) for k, v in atoms.info.items()},
        "arrays": {k: _jsonable(v) for k, v in atoms.arrays.items()
                   if k not in ("numbers", "positions")},
    }


def list_frames(s: Session, ds_id: str, offset: int = 0, limit: int = 200) -> dict:
    ds = get_dataset(s, ds_id)
    total = s.execute(select(func.count()).select_from(Frame).where(Frame.dataset_id == ds.id)
                      ).scalar_one()
    rows = s.execute(select(Frame).where(Frame.dataset_id == ds.id).order_by(Frame.idx)
                     .offset(offset).limit(limit)).scalars()
    cols = ("idx", "n_atoms", "formula", "energy", "energy_per_atom", "fmax", "temperature",
            "volume", "time", "step")
    return {"total": total, "offset": offset, "limit": limit,
            "items": [{c: getattr(r, c) for c in cols} for r in rows]}
