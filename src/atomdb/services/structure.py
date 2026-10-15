"""Per-dataset structure summary from the frames themselves (``Dataset.structure``).

Feeds the data-derived browse categories (geometry from ``atoms.pbc``, cell shape and
variation, per-atom extras, composition). Computed while importing; ``backfill`` fills it
for datasets imported before it existed, reading the canonical H5MD (``atomdb reindex``).
"""

from __future__ import annotations

from collections import Counter

import ase
import h5py
import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Dataset, Frame
from .context import AtomDB

VERSION = 1
ANGLE_TOL = 1e-3  # degrees
LENGTH_TOL = 1e-6  # relative
#: arrays that are geometry/labels, not "extra per-atom data"
SKIP_ARRAYS = {"numbers", "positions", "forces"}


def pbc_key(pbc) -> str:
    return "".join("T" if p else "F" for p in np.asarray(pbc, dtype=bool))


def cell_shapes(cells: np.ndarray) -> list[str]:
    """cubic / orthorhombic / triclinic for each (3, 3) cell in ``cells`` (n, 3, 3)."""
    lengths = np.linalg.norm(cells, axis=2)
    safe = np.where(lengths > 0, lengths, 1.0)
    unit = cells / safe[:, :, None]
    cosines = np.stack([(unit[:, 1] * unit[:, 2]).sum(1), (unit[:, 0] * unit[:, 2]).sum(1),
                        (unit[:, 0] * unit[:, 1]).sum(1)], axis=1)
    right = np.all(np.abs(np.degrees(np.arccos(np.clip(cosines, -1, 1))) - 90) < ANGLE_TOL, axis=1)
    equal = np.ptp(lengths, axis=1) <= LENGTH_TOL * lengths.max(1)
    return np.where(right & equal, "cubic", np.where(right, "orthorhombic", "triclinic")).tolist()


class StructureSummary:
    def __init__(self):
        self.pbc: Counter[str] = Counter()
        self.shapes: Counter[str] = Counter()
        self.vmin = self.vmax = None
        self.arrays: dict[str, bool] = {}  # name -> any non-zero value seen

    def add_box(self, pbc: np.ndarray, cells: np.ndarray) -> None:
        """Vectorised: ``pbc`` (n, 3) bool, ``cells`` (n, 3, 3)."""
        pbc = np.asarray(pbc, dtype=bool).reshape(-1, 3)
        cells = np.asarray(cells, dtype=float).reshape(-1, 3, 3)
        for k, n in Counter(map(pbc_key, pbc)).items():
            self.pbc[k] += n
        per = pbc.all(1) & (np.abs(np.linalg.det(cells)) > 1e-9)
        if per.any():
            self.shapes.update(cell_shapes(cells[per]))
            v = np.abs(np.linalg.det(cells[per]))
            self.vmin = float(v.min()) if self.vmin is None else min(self.vmin, float(v.min()))
            self.vmax = float(v.max()) if self.vmax is None else max(self.vmax, float(v.max()))

    def add_arrays(self, atoms: ase.Atoms) -> None:
        for k, v in atoms.arrays.items():
            if k in SKIP_ARRAYS:
                continue
            if not self.arrays.get(k):
                arr = np.asarray(v)
                # NaN = padding of variable-size frames, not data
                self.arrays[k] = bool(arr.dtype.kind not in "biufc"
                                      or np.any(np.nan_to_num(arr, nan=0.0)))

    def add(self, atoms: ase.Atoms) -> None:
        self.add_box(atoms.pbc[None], np.asarray(atoms.cell)[None])
        self.add_arrays(atoms)

    def to_json(self, n_formulas: int | None) -> dict:
        return {"v": VERSION, "pbc": dict(self.pbc), "cell_shape": dict(self.shapes),
                "volume": None if self.vmin is None else [self.vmin, self.vmax],
                "arrays": self.arrays, "n_formulas": n_formulas}


def summarize_h5md(path, n_sample: int = 20, chunk: int = 100_000) -> StructureSummary:
    """Summary from a canonical H5MD file: box arrays read whole (chunked), per-atom
    arrays checked on up to ``n_sample`` evenly spaced frames."""
    from ..formats.h5md import H5MDFrameSource, particles_group

    out = StructureSummary()
    src = H5MDFrameSource(path)
    n = len(src)
    with h5py.File(path, "r") as f:
        box = f["particles"][particles_group(f)].get("box")
        pbc = box.get("pbc/value") if box is not None else None
        edges = box.get("edges/value") if box is not None else None
        boxed = pbc is not None and edges is not None and len(pbc) == n == len(edges)
        if boxed:
            for start in range(0, n, chunk):
                out.add_box(pbc[start : start + chunk], edges[start : start + chunk])
    for i in sorted({int(x) for x in np.linspace(0, n - 1, min(n, n_sample))}) if n else []:
        a = src.get(i)
        out.add_arrays(a)
    if not boxed:  # unusual layout: read every frame
        for frames in src.iter_chunks(1024):
            for a in frames:
                out.add_box(a.pbc[None], np.asarray(a.cell)[None])
    return out


def n_formulas(s: Session, ds_id: str) -> int:
    return s.execute(select(func.count(func.distinct(Frame.formula)))
                     .where(Frame.dataset_id == ds_id)).scalar_one()


def backfill(adb: AtomDB, s: Session, force: bool = False) -> list[str]:
    """Fill ``Dataset.structure`` where missing (all datasets with ``force``)."""
    done = []
    for ds in s.execute(select(Dataset)).scalars().all():
        # (a JSON column set to None stores JSON null, not SQL NULL: test in Python)
        if (ds.structure and not force) or ds.primary_file is None:
            continue
        p = adb.store.abspath(ds.primary_file.store_relpath)
        if not p.exists():
            continue
        ds.structure = summarize_h5md(p).to_json(n_formulas(s, ds.id))
        done.append(ds.id)
    return done
