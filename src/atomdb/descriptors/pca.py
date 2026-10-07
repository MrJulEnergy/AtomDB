from __future__ import annotations

import numpy as np


def standardize(x: np.ndarray, ref: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """z-score columns (statistics from ``ref`` if given); constant columns dropped."""
    base = x if ref is None else ref
    mu, sd = base.mean(0), base.std(0)
    keep = sd > 1e-12
    return (x[:, keep] - mu[keep]) / sd[keep], keep, sd


def weight_groups(z: np.ndarray, keep: np.ndarray, groups: np.ndarray | None) -> np.ndarray:
    """Give every feature group the same total weight (divide by sqrt(group size))."""
    if groups is None:
        return z
    g = groups[keep]
    sizes = {k: (g == k).sum() for k in np.unique(g)}
    return z / np.sqrt(np.array([sizes[k] for k in g], dtype=float))


def pca(x: np.ndarray, n_components: int = 2, groups: np.ndarray | None = None) -> dict:
    z, keep, _ = standardize(x)
    z = weight_groups(z, keep, groups)
    if z.shape[1] == 0 or z.shape[0] < 2:
        return {"coords": np.zeros((x.shape[0], n_components)), "explained": [0.0] * n_components}
    u, s, _ = np.linalg.svd(z, full_matrices=False)
    k = min(n_components, len(s))
    coords = u[:, :k] * s[:k]
    if k < n_components:
        coords = np.hstack([coords, np.zeros((x.shape[0], n_components - k))])
    var = s**2 / max(1, (z.shape[0] - 1))
    explained = (var / var.sum()).tolist() if var.sum() > 0 else [0.0] * len(s)
    return {"coords": coords, "explained": (explained + [0.0] * n_components)[:n_components]}
