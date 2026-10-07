"""Dataset statistics computed by streaming over the canonical H5MD in chunks.

Nothing is loaded whole: frame-level quantities come from the SQL frame index, per-atom
quantities (forces per component / per element, cell, density) are read chunk-wise with
h5py (znh5md layout, NaN padding masked). Results are cached in ``cache/stats`` keyed by
the canonical file's sha256 and the stats version.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import h5py
import numpy as np
from ase.data import atomic_masses, chemical_symbols
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Dataset, Frame
from ..errors import IntegrityProblem
from .context import AtomDB
from .datasets import get_dataset

STATS_VERSION = 2
CHUNK = 256
BINS = 60
AMU_A3_TO_G_CM3 = 1.66053906660


class Running:
    """Streaming count/min/max/mean/std (Chan's parallel Welford)."""

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.min = math.inf
        self.max = -math.inf

    def update(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=float).ravel()
        x = x[np.isfinite(x)]
        if not x.size:
            return
        n, mean, m2 = x.size, float(x.mean()), float(((x - x.mean()) ** 2).sum())
        delta = mean - self.mean
        tot = self.n + n
        self.mean += delta * n / tot
        self.m2 += m2 + delta**2 * self.n * n / tot
        self.n = tot
        self.min = min(self.min, float(x.min()))
        self.max = max(self.max, float(x.max()))

    def summary(self) -> dict:
        if not self.n:
            return {"count": 0}
        return {"count": self.n, "min": self.min, "max": self.max, "mean": self.mean,
                "std": math.sqrt(self.m2 / self.n) if self.n > 1 else 0.0}


def edges_for(lo: float, hi: float, bins: int = BINS) -> np.ndarray:
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return np.array([0.0, 1.0])
    if hi <= lo:
        pad = abs(lo) * 1e-6 or 0.5
        lo, hi = lo - pad, hi + pad
    return np.linspace(lo, hi, bins + 1)


class Hist:
    def __init__(self, edges: np.ndarray):
        self.edges = edges
        self.counts = np.zeros(len(edges) - 1, dtype=np.int64)

    def update(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=float).ravel()
        x = x[np.isfinite(x)]
        # include the right edge in the last bin
        c, _ = np.histogram(np.clip(x, self.edges[0], self.edges[-1]), bins=self.edges)
        self.counts += c

    def to_dict(self) -> dict:
        return {"edges": self.edges.tolist(), "counts": self.counts.tolist()}


def _h5_layout(f: h5py.File) -> dict:
    grp = "atoms" if "atoms" in f["particles"] else next(iter(f["particles"]))
    p = f["particles"][grp]

    def val(g, name):
        return g[name]["value"] if name in g and "value" in g[name] else None

    return {"species": val(p, "species"), "position": val(p, "position"),
            "force": val(p, "force") or val(p, "forces"),
            "box": p["box"]["edges"]["value"] if "box" in p and "edges" in p["box"]
            and "value" in p["box"]["edges"] else None}


def _cell_params(cells: np.ndarray) -> dict[str, np.ndarray]:
    """a, b, c [Å] and α, β, γ [deg] for a stack of 3x3 cells."""
    a, b, c = cells[:, 0], cells[:, 1], cells[:, 2]
    la, lb, lc = (np.linalg.norm(v, axis=1) for v in (a, b, c))
    def ang(u, v, nu, nv):
        return np.degrees(np.arccos(np.clip((u * v).sum(1) / (nu * nv), -1, 1)))

    with np.errstate(invalid="ignore", divide="ignore"):
        return {"a": la, "b": lb, "c": lc, "alpha": ang(b, c, lb, lc), "beta": ang(a, c, la, lc),
                "gamma": ang(a, b, la, lb)}


def _stream_h5(path: Path, ranges: dict | None, bins: int):
    """Two passes over per-atom data: (1) ranges + moments, (2) histograms."""
    with h5py.File(path, "r") as f:
        lay = _h5_layout(f)
        sp, frc, box = lay["species"], lay["force"], lay["box"]
        n = sp.shape[0] if sp is not None else 0

        def chunks():
            for s in range(0, n, CHUNK):
                e = min(n, s + CHUNK)
                z = np.asarray(sp[s:e]) if sp.ndim == 2 else np.broadcast_to(sp[...], (e - s, sp.shape[-1]))
                z = np.nan_to_num(z, nan=0).astype(int)
                fr = np.asarray(frc[s:e], dtype=float) if frc is not None else None
                bx = None
                if box is not None:
                    per_frame = box.ndim >= 2 and box.shape[0] == n and box.shape != (3, 3)
                    bv = np.asarray(box[s:e] if per_frame else box[...], dtype=float)
                    if bv.shape == (3,):  # orthorhombic edges, constant
                        bv = np.diag(bv)
                    if bv.shape == (3, 3):  # constant cell
                        bv = np.broadcast_to(bv, (e - s, 3, 3))
                    elif bv.ndim == 2:  # per-frame orthorhombic edges (n, 3)
                        bv = np.stack([np.diag(row) for row in bv])
                    bx = np.nan_to_num(bv)
                yield z, fr, bx

        run: dict[str, Running] = {}

        def r(key):
            return run.setdefault(key, Running())

        elements = set()
        for z, fr, bx in chunks():
            mask = z > 0
            elements |= set(np.unique(z[mask]).tolist())
            if fr is not None:
                fv = fr[mask]
                r("force_x").update(fv[:, 0])
                r("force_y").update(fv[:, 1])
                r("force_z").update(fv[:, 2])
                mag = np.linalg.norm(fv, axis=1)
                r("force_norm").update(mag)
                for el in np.unique(z[mask]):
                    r(f"force_norm:{chemical_symbols[el]}").update(np.linalg.norm(fr[z == el], axis=1))
            if bx is not None and np.any(bx):
                vol = np.abs(np.linalg.det(bx))
                ok = vol > 0
                if ok.any():
                    for k, v in _cell_params(bx[ok]).items():
                        r(f"cell_{k}").update(v)
                    mass = np.where(mask, atomic_masses[np.clip(z, 0, len(atomic_masses) - 1)], 0
                                    ).sum(1)
                    r("density").update(mass[ok] / vol[ok] * AMU_A3_TO_G_CM3)
        hists = {}
        for key, rr in run.items():
            lo, hi = (ranges or {}).get(key, (rr.min, rr.max))
            hists[key] = Hist(edges_for(lo, hi, bins))
        for z, fr, bx in chunks():
            mask = z > 0
            if fr is not None:
                fv = fr[mask]
                hists["force_x"].update(fv[:, 0])
                hists["force_y"].update(fv[:, 1])
                hists["force_z"].update(fv[:, 2])
                hists["force_norm"].update(np.linalg.norm(fv, axis=1))
                for el in np.unique(z[mask]):
                    hists[f"force_norm:{chemical_symbols[el]}"].update(
                        np.linalg.norm(fr[z == el], axis=1))
            if bx is not None and np.any(bx):
                vol = np.abs(np.linalg.det(bx))
                ok = vol > 0
                if ok.any():
                    for k, v in _cell_params(bx[ok]).items():
                        hists[f"cell_{k}"].update(v)
                    mass = np.where(mask, atomic_masses[np.clip(z, 0, len(atomic_masses) - 1)], 0
                                    ).sum(1)
                    hists["density"].update(mass[ok] / vol[ok] * AMU_A3_TO_G_CM3)
    return run, hists


UNITS = {"energy": "eV", "energy_per_atom": "eV/atom", "fmax": "eV/Å", "n_atoms": "",
         "volume": "Å³", "temperature": "K", "force_x": "eV/Å", "force_y": "eV/Å",
         "force_z": "eV/Å", "force_norm": "eV/Å", "density": "g/cm³", "cell_a": "Å",
         "cell_b": "Å", "cell_c": "Å", "cell_alpha": "°", "cell_beta": "°", "cell_gamma": "°"}
FRAME_COLS = ("energy", "energy_per_atom", "fmax", "n_atoms", "volume", "temperature")


def compute(adb: AtomDB, s: Session, ds: Dataset, ranges: dict | None = None,
            bins: int = BINS) -> dict:
    out: dict = {"dataset": ds.id, "name": ds.name, "n_frames": ds.n_frames,
                 "version": STATS_VERSION, "quantities": {}, "composition": {}}
    # frame-level: from the SQL index, in chunks
    run = {c: Running() for c in FRAME_COLS}
    for chunk in _frame_chunks(s, ds.id):
        for c in FRAME_COLS:
            run[c].update(np.array([r[c] for r in chunk if r[c] is not None], dtype=float))
    hists = {}
    for c, rr in run.items():
        if rr.n:
            lo, hi = (ranges or {}).get(c, (rr.min, rr.max))
            if c == "n_atoms":
                lo, hi = math.floor(lo) - 0.5, math.ceil(hi) + 0.5
                b = int(min(bins, hi - lo))
                hists[c] = Hist(np.linspace(lo, hi, max(b, 1) + 1))
            else:
                hists[c] = Hist(edges_for(lo, hi, bins))
    for chunk in _frame_chunks(s, ds.id):
        for c, h in hists.items():
            h.update(np.array([r[c] for r in chunk if r[c] is not None], dtype=float))
    for c in FRAME_COLS:
        if run[c].n:
            out["quantities"][c] = {"unit": UNITS[c], "level": "frame", **run[c].summary(),
                                    "hist": hists[c].to_dict()}
    out["composition"] = dict(s.execute(
        select(Frame.formula, func.count()).where(Frame.dataset_id == ds.id)
        .group_by(Frame.formula).order_by(func.count().desc())
    ).all())
    # per-atom: stream the H5MD
    if ds.primary_file is None:
        raise IntegrityProblem(f"{ds.id} has no canonical file")
    path = adb.store.abspath(ds.primary_file.store_relpath)
    prun, phist = _stream_h5(path, ranges, bins)
    per_el = {}
    for key, rr in prun.items():
        entry = {"unit": UNITS.get(key.split(":")[0], "eV/Å"), "level": "atom", **rr.summary(),
                 "hist": phist[key].to_dict()}
        if key.startswith("force_norm:"):
            per_el[key.split(":")[1]] = entry
        else:
            entry["level"] = "atom" if key.startswith("force") else "frame"
            out["quantities"][key] = entry
    z_order = {s_: i for i, s_ in enumerate(chemical_symbols)}
    out["force_per_element"] = dict(sorted(per_el.items(), key=lambda kv: z_order[kv[0]]))
    return out


def _frame_chunks(s: Session, ds_id: str, size: int = 50_000):
    last = -1
    cols = [getattr(Frame, c) for c in FRAME_COLS]
    while True:
        rows = s.execute(select(Frame.idx, *cols).where(Frame.dataset_id == ds_id, Frame.idx > last)
                         .order_by(Frame.idx).limit(size)).all()
        if not rows:
            return
        yield [dict(zip(FRAME_COLS, r[1:], strict=True)) for r in rows]
        last = rows[-1][0]


def cache_path(adb: AtomDB, ds: Dataset) -> Path:
    sha = ds.primary_file.sha256 if ds.primary_file else "none"
    return adb.paths.cache / "stats" / f"{ds.id}-{sha[:16]}-v{STATS_VERSION}.json"


def dataset_stats(adb: AtomDB, s: Session, ds_id: str, recompute: bool = False) -> dict:
    ds = get_dataset(s, ds_id)
    p = cache_path(adb, ds)
    if p.exists() and not recompute:
        return json.loads(p.read_text())
    data = compute(adb, s, ds)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(p)
    return data


def compare(adb: AtomDB, s: Session, ds_ids: list[str], bins: int = BINS) -> dict:
    """Statistics of several datasets histogrammed on common bin edges."""
    base = [dataset_stats(adb, s, i) for i in ds_ids]
    ranges: dict[str, tuple[float, float]] = {}
    for st in base:
        for key, q in list(st["quantities"].items()) + [
                (f"force_norm:{el}", q) for el, q in st.get("force_per_element", {}).items()]:
            if q.get("count"):
                lo, hi = ranges.get(key, (math.inf, -math.inf))
                ranges[key] = (min(lo, q["min"]), max(hi, q["max"]))
    datasets = [compute(adb, s, get_dataset(s, i), ranges=ranges, bins=bins) for i in ds_ids]
    return {"datasets": datasets, "summary": _compare_summary(datasets)}


def _compare_summary(datasets: list[dict]) -> dict:
    """Simple per-quantity differences: mean shift in units of pooled std, overlap of
    normalised histograms (1 = identical distributions, 0 = disjoint)."""
    out: dict = {}
    if len(datasets) < 2:
        return out
    ref = datasets[0]
    for key, q0 in ref["quantities"].items():
        rows = []
        for d in datasets[1:]:
            q1 = d["quantities"].get(key)
            if not q1 or not q1.get("count") or not q0.get("count"):
                continue
            pooled = math.sqrt((q0["std"] ** 2 + q1["std"] ** 2) / 2) or 1.0
            c0, c1 = np.array(q0["hist"]["counts"], float), np.array(q1["hist"]["counts"], float)
            overlap = float(np.minimum(c0 / c0.sum(), c1 / c1.sum()).sum()) if c0.sum() and c1.sum() else None
            rows.append({"dataset": d["dataset"], "mean_shift_std": (q1["mean"] - q0["mean"]) / pooled,
                         "hist_overlap": overlap})
        if rows:
            out[key] = {"reference": ref["dataset"], "vs": rows}
    els0 = set(ref.get("force_per_element", {}))
    out["elements"] = {d["dataset"]: sorted(set(d.get("force_per_element", {})) ^ els0)
                       for d in datasets[1:]}
    return out
