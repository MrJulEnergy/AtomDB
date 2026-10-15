"""Per-atom MACE-MP-0 (small) invariant node features (optional extra ``mlip``).

A cheap, chemically meaningful embedding of local atomic environments: the invariant part
of the node features of all interaction layers of the foundation model. The weights are
downloaded once by mace-torch (to ~/.cache/mace) on first use.
"""

from __future__ import annotations

import logging
from functools import cache

import ase
import numpy as np

from .base import AtomDescriptor


log = logging.getLogger(__name__)


@cache
def _calculator(model: str, device: str):
    from mace.calculators import mace_mp

    return mace_mp(model=model, device=device, default_dtype="float32")


def _default_device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


class MACEAtomic(AtomDescriptor):
    name = "mace-mp0-small"
    description = ("MACE-MP-0 small invariant node features, one vector per atom "
                   "(local environments; mace-torch, optional extra `mlip`)")
    element_layout = "independent"

    @staticmethod
    def available() -> bool:
        try:
            import mace  # noqa: F401
        except ImportError:
            return False
        return True

    def __init__(self, model: str = "small"):
        self.model = model

    def config(self) -> dict:
        return {"model": self.model}

    def compute_atoms(self, frames: list[ase.Atoms]) -> np.ndarray:
        import torch

        device = _default_device()
        out = []
        for a in frames:
            a2 = a.copy()
            a2.calc = None
            if not (a2.pbc.all() and a2.cell.rank == 3):
                a2.pbc = False  # clusters / partial cells: no periodic images
            try:
                x = _calculator(self.model, device).get_descriptors(
                    a2, invariants_only=True, num_layers=-1)
            except torch.OutOfMemoryError:
                # GPU shared with training jobs: finish this call on the CPU (the next
                # call tries the GPU again)
                torch.cuda.empty_cache()
                log.warning("MACE descriptor: CUDA out of memory, falling back to CPU")
                device = "cpu"
                x = _calculator(self.model, device).get_descriptors(
                    a2, invariants_only=True, num_layers=-1)
            out.append(np.asarray(x))
        return np.vstack(out) if out else np.zeros((0, 0))
