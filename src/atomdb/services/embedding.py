"""Exploratory descriptor embedding (PCA) and descriptor-space dataset similarity.

These results are diagnostics for "what does my data cover?" — not a persistent
scientific record. They are never stored in the database; the descriptor vectors are
cached on disk (services/desccache.py), the projection is recomputed every time.
"""

from __future__ import annotations

import zlib

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DatasetElement, Frame
from ..descriptors import get_descriptor
from ..descriptors.pca import pca, standardize, weight_groups
from ..errors import InvalidInput
from .context import AtomDB
from .datasets import get_dataset
from .desccache import atom_cache, atom_compute, frame_vectors
from .framedata import open_dataset

NOTE = ("EXPLORATORY: unsupervised projection of a generic descriptor; distances and axes "
        "have no direct physical meaning and depend on the descriptor and frame sample.")


def _sample(n: int, k: int, rng: np.random.Generator) -> np.ndarray:
    """k frames in random order; a prefix of one fixed permutation, so a larger k keeps
    the frames of a smaller one (and their cached descriptors)."""
    return rng.permutation(n)[:k]


def _rng(seed: int, ds_id: str) -> np.random.Generator:
    """Per-dataset stream: a dataset samples the same frames whatever else is selected
    (so cached descriptors are reused across selections)."""
    return np.random.default_rng([seed, zlib.crc32(ds_id.encode())])


def describe_frames(adb: AtomDB, s: Session, ds_ids: list[str], descriptor: str = "composition-rdf",
                    max_frames: int = 500, seed: int = 0) -> dict:
    if not ds_ids:
        raise InvalidInput("select at least one dataset")
    dss = [get_dataset(s, i) for i in ds_ids]
    elements = sorted(set(s.execute(select(DatasetElement.symbol).where(
        DatasetElement.dataset_id.in_([d.id for d in dss]))).scalars()))
    desc = get_descriptor(descriptor)
    desc.fit(elements)
    blocks, meta, hits, computed = [], [], 0, 0
    for ds in dss:
        idx = np.sort(_sample(ds.n_frames or 0, max_frames, _rng(seed, ds.id))).tolist()
        rows = {f.idx: f for f in s.execute(select(Frame).where(
            Frame.dataset_id == ds.id, Frame.idx.in_(idx))).scalars()}
        x, cache = frame_vectors(adb, s, ds, desc, idx, open_dataset(adb, ds))
        hits, computed = hits + cache.hits, computed + cache.computed
        blocks.append(x)
        for i in idx:
            f = rows.get(i)
            meta.append({"dataset": ds.id, "frame": i,
                         "energy_per_atom": f.energy_per_atom if f else None,
                         "fmax": f.fmax if f else None,
                         "n_atoms": f.n_atoms if f else None,
                         "temperature": f.temperature if f else None})
    x = np.vstack(blocks) if blocks else np.zeros((0, 0))
    return {"x": x, "meta": meta, "cache": {"frames_cached": hits, "frames_computed": computed}, "datasets": [{"id": d.id, "name": d.name, "label": d.label,
                                               "n_frames": d.n_frames} for d in dss],
            "descriptor": descriptor, "elements": elements, "groups": desc.feature_groups()}


def embed(adb: AtomDB, s: Session, ds_ids: list[str], descriptor: str = "composition-rdf",
          max_frames: int = 500, seed: int = 0) -> dict:
    d = describe_frames(adb, s, ds_ids, descriptor, max_frames, seed)
    p = pca(d["x"], 2, groups=d["groups"])
    points = [{**m, "pc1": float(c[0]), "pc2": float(c[1])} for m, c in zip(d["meta"], p["coords"], strict=True)]
    return {"exploratory": True, "note": NOTE, "descriptor": descriptor,
            "n_features": int(d["x"].shape[1]) if d["x"].size else 0,
            "explained_variance": p["explained"], "datasets": d["datasets"],
            "elements": d["elements"], "max_frames": max_frames, "seed": seed,
            "cache": d["cache"], "points": points}


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


# --------------------------------------------------------------------------- per-atom

ATOM_NOTE = ("EXPLORATORY: PCA of per-atom MLIP features (local environments). Overlapping "
             "clouds mean similar environments as seen by that model; axes have no physical "
             "meaning and the plot depends on the frame/atom sample.")
#: skipped unless allow_large: MPtrj-sized collections (slow random access, mostly
#: unrelated chemistry) and huge boxes (one frame alone is a big GPU job)
LARGE_FRAMES = 100_000
LARGE_FRAME_ATOMS = 5_000


def is_large(ds) -> bool:
    return (ds.n_frames or 0) > LARGE_FRAMES or (ds.n_atoms_max or 0) > LARGE_FRAME_ATOMS


def _stratified(symbols: np.ndarray, k: int, rng: np.random.Generator) -> np.ndarray:
    """Pick up to k indices, sharing the budget equally between elements (rare elements
    such as K/Cl in water keep all their atoms while O/H are thinned)."""
    if len(symbols) <= k:
        return np.arange(len(symbols))
    groups = {el: np.flatnonzero(symbols == el) for el in np.unique(symbols)}
    order = sorted(groups, key=lambda el: len(groups[el]))
    picked, left = [], k
    for n, el in enumerate(order):
        share = left // (len(order) - n)
        g = groups[el]
        take = g if len(g) <= share else rng.choice(g, size=share, replace=False)
        picked.append(take)
        left -= len(take)
    return np.sort(np.concatenate(picked))


def describe_atoms(adb: AtomDB, s: Session, ds_ids: list[str], descriptor: str = "mace-mp0-small",
                   max_frames: int = 200, max_atoms: int = 5000, elements: list[str] | None = None,
                   seed: int = 0, allow_large: bool = False) -> dict:
    """Per-atom descriptors for a sample of atoms from each dataset.

    Frames are visited in random order (at most ``max_frames``) until about 3×``max_atoms``
    atoms of the requested elements were described, then ``max_atoms`` are kept per dataset,
    stratified by element.
    """
    from ..descriptors.base import AtomDescriptor

    if not ds_ids:
        raise InvalidInput("select at least one dataset")
    desc = get_descriptor(descriptor)
    if not isinstance(desc, AtomDescriptor):
        raise InvalidInput(f"{descriptor!r} is not a per-atom descriptor")
    dss, skipped = [], []
    for i in ds_ids:
        ds = get_dataset(s, i)
        if is_large(ds) and not allow_large:
            skipped.append({"id": ds.id, "name": ds.label or ds.name, "n_frames": ds.n_frames,
                            "reason": f"large dataset ({ds.n_frames} frames, up to "
                                      f"{ds.n_atoms_max} atoms/frame); pass allow_large"})
        else:
            dss.append(ds)
    if not dss:
        raise InvalidInput("all selected datasets are large; pass allow_large to include them")
    wanted = {e.strip() for e in elements or [] if e.strip()} or None
    all_el = set(s.execute(select(DatasetElement.symbol).where(
        DatasetElement.dataset_id.in_([d.id for d in dss]))).scalars())
    desc.fit(sorted(all_el))
    compute = atom_compute(desc)
    blocks, meta, hits, computed = [], [], 0, 0
    for ds in dss:
        rng = _rng(seed, ds.id)
        idx = _sample(ds.n_frames or 0, max_frames, rng)
        src = open_dataset(adb, ds)
        cache = atom_cache(adb, ds, desc)
        xs, rows = [], []
        budget = 3 * max_atoms
        try:
            for start in range(0, len(idx), 16):
                chunk = [int(i) for i in idx[start : start + 16]]
                frames = {i: src.get(i) for i in chunk}
                feats = cache.get(chunk, lambda ii, fr=frames: [fr[i] for i in ii], compute)
                for fi, x in zip(chunk, feats, strict=True):
                    for ai, el in enumerate(frames[fi].get_chemical_symbols()):
                        if wanted is None or el in wanted:
                            rows.append((fi, ai, el))
                            xs.append(x[ai])
                if len(rows) >= budget:
                    break
        finally:
            cache.save()
        hits, computed = hits + cache.hits, computed + cache.computed
        if not rows:
            continue
        keep = _stratified(np.array([r[2] for r in rows]), max_atoms, rng)
        blocks.append(np.asarray(xs)[keep])
        for k in keep:
            fi, ai, el = rows[k]
            meta.append({"dataset": ds.id, "frame": fi, "atom": ai, "element": el})
    x = np.vstack(blocks) if blocks else np.zeros((0, 0))
    datasets = []
    for d in dss:
        pts = [m for m in meta if m["dataset"] == d.id]
        datasets.append({"id": d.id, "name": d.name, "label": d.label, "n_frames": d.n_frames,
                         "n_atoms": len(pts),
                         "elements": {el: sum(m["element"] == el for m in pts)
                                      for el in sorted({m["element"] for m in pts})}})
    return {"x": x, "meta": meta, "datasets": datasets, "skipped": skipped,
            "cache": {"frames_cached": hits, "frames_computed": computed},
            "descriptor": descriptor, "elements": sorted(all_el)}


def embed_atoms(adb: AtomDB, s: Session, ds_ids: list[str], descriptor: str = "mace-mp0-small",
                max_frames: int = 200, max_atoms: int = 5000, elements: list[str] | None = None,
                seed: int = 0, allow_large: bool = False) -> dict:
    d = describe_atoms(adb, s, ds_ids, descriptor, max_frames, max_atoms, elements, seed,
                       allow_large)
    if not d["meta"]:
        raise InvalidInput("no atoms of the requested elements in the selected datasets")
    p = pca(d["x"], 2)
    points = [{**m, "pc1": float(c[0]), "pc2": float(c[1])}
              for m, c in zip(d["meta"], p["coords"], strict=True)]
    return {"exploratory": True, "note": ATOM_NOTE, "per_atom": True, "descriptor": descriptor,
            "n_features": int(d["x"].shape[1]), "explained_variance": p["explained"],
            "datasets": d["datasets"], "skipped": d["skipped"], "elements": d["elements"],
            "element_filter": sorted(elements) if elements else None,
            "max_frames": max_frames, "max_atoms": max_atoms, "seed": seed,
            "cache": d["cache"], "points": points}
