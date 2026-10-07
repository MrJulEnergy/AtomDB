"""Structure-averaged SOAP via dscribe (optional extra ``descriptors``)."""

from __future__ import annotations

import ase
import numpy as np

from .base import Descriptor


class SOAPAverage(Descriptor):
    name = "soap"
    description = "SOAP power spectrum averaged over atoms (dscribe; optional dependency)"

    @staticmethod
    def available() -> bool:
        try:
            import dscribe  # noqa: F401
        except ImportError:
            return False
        return True

    def __init__(self, r_cut: float = 5.0, n_max: int = 6, l_max: int = 4, sigma: float = 0.5):
        self.params = {"r_cut": r_cut, "n_max": n_max, "l_max": l_max, "sigma": sigma}

    def fit(self, elements: list[str]) -> None:
        from dscribe.descriptors import SOAP

        super().fit(elements)
        self.soap = SOAP(species=self.elements, periodic=True, average="inner", **self.params)

    def compute(self, frames: list[ase.Atoms]) -> np.ndarray:
        out = []
        for a in frames:
            a2 = a.copy()
            if not (a2.pbc.all() and a2.cell.rank == 3):
                a2.pbc = False
                self.soap.periodic = False
            else:
                self.soap.periodic = True
            out.append(self.soap.create(a2))
        return np.array(out)
