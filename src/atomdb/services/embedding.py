"""Exploratory descriptor embedding (PCA) and descriptor-space dataset similarity.

These results are diagnostics for "what does my data cover?" — not a persistent
scientific record. They are never stored in the database.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DatasetElement, Frame
from ..descriptors import get_descriptor
from ..descriptors.pca import pca, standardize, weight_groups
from ..errors import InvalidInput
from .context import AtomDB
from .datasets import get_dataset
from .framedata import open_dataset

NOTE = ("EXPLORATORY: unsupervised projection of a generic descriptor; distances and axes "
        "have no direct physical meaning and depend on the descriptor and frame sample.")


def _sample(n: int, k: int, rng: np.random.Generator) -> np.ndarray:
    if n <= k:
        return np.arange(n)
    return np.sort(rng.choice(n, size=k, replace=False))


def describe_frames(adb: AtomDB, s: Session, ds_ids: list[str], descriptor: str = "composition-rdf",
                    max_frames: int = 500, seed: int = 0) -> dict:
    if not ds_ids:
        raise InvalidInput("select at least one dataset")
    rng = np.random.default_rng(seed)
    dss = [get_dataset(s, i) for i in ds_ids]
    elements = sorted(set(s.execute(select(DatasetElement.symbol).where(
        DatasetElement.dataset_id.in_([d.id for d in dss]))).scalars()))
    desc = get_descriptor(descriptor)
    desc.fit(elements)
    blocks, meta = [], []
    for ds in dss:
        idx = _sample(ds.n_frames or 0, max_frames, rng)
        src = open_dataset(adb, ds)
        rows = {f.idx: f for f in s.execute(select(Frame).where(
            Frame.dataset_id == ds.id, Frame.idx.in_(idx.tolist()))).scalars()}
        for start in range(0, len(idx), 128):
            chunk = idx[start : start + 128]
            frames = [src.get(int(i)) for i in chunk]
            blocks.append(desc.compute(frames))
            for i in chunk:
                f = rows.get(int(i))
                meta.append({"dataset": ds.id, "frame": int(i),
                             "energy_per_atom": f.energy_per_atom if f else None,
                             "fmax": f.fmax if f else None,
                             "n_atoms": f.n_atoms if f else None,
                             "temperature": f.temperature if f else None})
    x = np.vstack(blocks) if blocks else np.zeros((0, 0))
    return {"x": x, "meta": meta, "datasets": [{"id": d.id, "name": d.name, "n_frames": d.n_frames}
                                              for d in dss],
            "descriptor": descriptor, "elements": elements, "groups": desc.feature_groups()}


def embed(adb: AtomDB, s: Session, ds_ids: list[str], descriptor: str = "composition-rdf",
          max_frames: int = 500, seed: int = 0) -> dict:
    d = describe_frames(adb, s, ds_ids, descriptor, max_frames, seed)
    p = pca(d["x"], 2, groups=d["groups"])
    points = [{**m, "pc1": float(c[0]), "pc2": float(c[1])} for m, c in zip(d["meta"], p["coords"], strict=True)]
    return {"exploratory": True, "note": NOTE, "descriptor": descriptor,
            "n_features": int(d["x"].shape[1]) if d["x"].size else 0,
            "explained_variance": p["explained"], "datasets": d["datasets"],
            "elements": d["elements"], "max_frames": max_frames, "seed": seed, "points": points}


def _nn_dist(a: np.ndarray, b: np.ndarray, exclude_self: bool = False, block: int = 512) -> np.ndarray:
    """For each row of b, Euclidean distance to the nearest row of a."""
    out = np.empty(len(b))
    a2 = (a**2).sum(1)
    for s in range(0, len(b), block):
        bb = b[s : s + block]
        d2 = (bb**2).sum(1)[:, None] + a2[None, :] - 2 * bb @ a.T
        if exclude_self:
            idx = np.arange(s, s + len(bb))
            d2[np.arange(len(bb)), idx] = np.inf
        out[s : s + len(bb)] = np.sqrt(np.maximum(d2.min(1), 0))
    return out


def similarity(adb: AtomDB, s: Session, reference: str, other: str,
               descriptor: str = "composition-rdf", max_frames: int = 1000, seed: int = 0) -> dict:
    """Is ``other`` genuinely different from ``reference`` in descriptor space?

    novelty = fraction of ``other`` frames whose nearest reference frame is farther away
    than the 95th percentile of reference-to-reference nearest-neighbour distances.
    """
    d = describe_frames(adb, s, [reference, other], descriptor, max_frames, seed)
    x = d["x"]
    ids = np.array([m["dataset"] for m in d["meta"]])
    ref_id, oth_id = d["datasets"][0]["id"], d["datasets"][1]["id"]
    if ref_id == oth_id:
        raise InvalidInput("compare two different datasets")
    xr, xo = x[ids == ref_id], x[ids == oth_id]
    # standardise in the pooled space: features constant within each dataset but
    # different between them (e.g. a new element) must count as distance
    z, keep, _ = standardize(x)
    z = weight_groups(z, keep, d["groups"])
    zr, zo = z[ids == ref_id], z[ids == oth_id]
    if len(xr) < 2:
        raise InvalidInput("reference needs at least 2 frames")
    rr = _nn_dist(zr, zr, exclude_self=True)
    ro = _nn_dist(zr, zo)
    thr = float(np.percentile(rr, 95))
    novel = ro > thr
    q = np.linspace(0, 1, 11)
    from .stats import compare

    dist = compare(adb, s, [reference, other])["summary"]
    return {
        "exploratory": True, "note": NOTE, "descriptor": descriptor,
        "reference": ref_id, "other": oth_id,
        "n_reference_frames": len(xr), "n_other_frames": len(xo),
        "novelty_threshold": thr,
        "novel_fraction": float(novel.mean()) if len(ro) else None,
        "nn_distance_other_to_reference": {"quantiles": np.quantile(ro, q).tolist() if len(ro) else []},
        "nn_distance_reference_internal": {"quantiles": np.quantile(rr, q).tolist()},
        "most_novel_frames": [d["meta"][int(i)] | {"nn_distance": float(ro[k])}
                              for k, i in sorted(
                                  ((k, np.flatnonzero(ids == oth_id)[k]) for k in range(len(ro))),
                                  key=lambda t: -ro[t[0]])[:10]],
        "composition": _element_diff(s, ref_id, oth_id),
        "distributions": {k: v["vs"][0] for k, v in dist.items() if k != "elements" and v.get("vs")},
        "verdict": _verdict(float(novel.mean()) if len(ro) else 0.0),
    }


def _element_diff(s: Session, a: str, b: str) -> dict:
    def els(i):
        return set(s.execute(select(DatasetElement.symbol)
                             .where(DatasetElement.dataset_id == i)).scalars())

    ea, eb = els(a), els(b)
    return {"elements_only_in_other": sorted(eb - ea), "elements_only_in_reference": sorted(ea - eb)}


def _verdict(frac: float) -> str:
    if frac < 0.05:
        return "largely covered by the reference (few novel configurations)"
    if frac < 0.3:
        return "partly new: a minority of configurations lie outside the reference"
    return "substantially different: many configurations lie outside the reference"
