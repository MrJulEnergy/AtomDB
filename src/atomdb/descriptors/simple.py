"""Lightweight default descriptor: composition + element-pair radial distributions."""

from __future__ import annotations

import itertools

import ase
import numpy as np
from ase.data import atomic_numbers
from ase.neighborlist import neighbor_list

from .base import Descriptor


class CompositionRDF(Descriptor):
    name = "composition-rdf"
    description = ("element fractions + per element-pair radial distribution histograms "
                   "(PBC-aware, normalised per atom); fast, no extra dependencies")
    element_layout = "remap"

    def __init__(self, cutoff: float = 6.0, bins: int = 22, r_min: float = 0.5):
        self.cutoff = cutoff
        self.options = {"cutoff": cutoff, "bins": bins, "r_min": r_min}
        self.edges = np.linspace(r_min, cutoff, bins + 1)

    def config(self) -> dict:
        return self.options

    def fit(self, elements: list[str]) -> None:
        super().fit(elements)
        self.z = [atomic_numbers[e] for e in self.elements]
        self.pairs = list(itertools.combinations_with_replacement(self.z, 2))
        self.pair_index = {p: i for i, p in enumerate(self.pairs)}

    def feature_names(self) -> list[str]:
        from ase.data import chemical_symbols as cs

        names = [f"x_{e}" for e in self.elements]
        for a, b in self.pairs:
            names += [f"g_{cs[a]}{cs[b]}_{r:.2f}" for r in self.edges[:-1]]
        return names

    def feature_groups(self) -> np.ndarray:
        nb = len(self.edges) - 1
        return np.concatenate([np.zeros(len(self.z), int),
                               np.repeat(np.arange(1, len(self.pairs) + 1), nb)])

    def compute(self, frames: list[ase.Atoms]) -> np.ndarray:
        nb = len(self.edges) - 1
        out = np.zeros((len(frames), len(self.z) + len(self.pairs) * nb))
        zindex = {z: i for i, z in enumerate(self.z)}
        for k, atoms in enumerate(frames):
            n = len(atoms)
            if n == 0:
                continue
            for z in atoms.numbers:
                out[k, zindex[int(z)]] += 1.0 / n
            if not atoms.pbc.any() or atoms.cell.rank == 3:
                i, j, d = neighbor_list("ijd", atoms, self.cutoff)
            else:  # partial PBC without full cell: fall back to non-periodic
                a2 = atoms.copy()
                a2.pbc = False
                i, j, d = neighbor_list("ijd", a2, self.cutoff)
            mask = i < j
            zi, zj, d = atoms.numbers[i[mask]], atoms.numbers[j[mask]], d[mask]
            lo, hi = np.minimum(zi, zj), np.maximum(zi, zj)
            for p, idx in self.pair_index.items():
                sel = (lo == p[0]) & (hi == p[1])
                if sel.any():
                    h, _ = np.histogram(d[sel], bins=self.edges)
                    base = len(self.z) + idx * nb
                    out[k, base : base + nb] = h / n
        return out
