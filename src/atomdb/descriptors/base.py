from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from functools import cache
from importlib.metadata import entry_points

import ase
import numpy as np

from ..errors import InvalidInput


class Descriptor(ABC):
    """Maps frames to fixed-length vectors (one per frame).

    ``fit(elements)`` is called first with the union of elements of all frames that
    will be described, so that vectors from different datasets are comparable.
    """

    name: str
    description: str = ""
    #: bump when the computed values change, so cached vectors are not reused
    version: int = 1
    #: how columns depend on ``fit(elements)`` (see services/desccache.py):
    #: "independent", "remap" (per-element feature_names) or "keyed" (safe default)
    element_layout: str = "keyed"

    def config(self) -> dict:
        """Constructor options that change the values (part of the cache key)."""
        return {}

    def cache_key(self) -> str:
        cfg = json.dumps({"v": self.version, **self.config()}, sort_keys=True, default=str)
        return f"{self.name}-{hashlib.sha256(cfg.encode()).hexdigest()[:10]}"

    def fit(self, elements: list[str]) -> None:
        self.elements = sorted(elements)

    @abstractmethod
    def compute(self, frames: list[ase.Atoms]) -> np.ndarray: ...

    def feature_names(self) -> list[str]:
        return []

    def feature_groups(self) -> np.ndarray | None:
        """Group id per feature; groups get equal total weight in distances (optional)."""
        return None


class AtomDescriptor(Descriptor):
    """Maps frames to one vector per *atom* (local environments), e.g. MLIP node features."""

    per_atom = True
    extra = "mlip"

    @abstractmethod
    def compute_atoms(self, frames: list[ase.Atoms]) -> np.ndarray:
        """``(sum of n_atoms, d)``; rows in frame order, then atom order."""

    def compute(self, frames: list[ase.Atoms]) -> np.ndarray:
        x = self.compute_atoms(frames)
        out, start = [], 0
        for a in frames:
            out.append(x[start : start + len(a)].mean(0))
            start += len(a)
        return np.array(out)


@cache
def _registry() -> dict[str, type[Descriptor]]:
    from .mace import MACEAtomic
    from .simple import CompositionRDF
    from .soap import SOAPAverage

    reg: dict[str, type[Descriptor]] = {"composition-rdf": CompositionRDF, "soap": SOAPAverage,
                                        MACEAtomic.name: MACEAtomic}
    for ep in entry_points(group="atomdb.descriptors"):
        cls = ep.load()
        reg[cls.name] = cls
    return reg


def list_descriptors() -> list[dict]:
    out = []
    for name, cls in _registry().items():
        out.append({"name": name, "description": cls.description,
                    "available": getattr(cls, "available", lambda: True)(),
                    "per_atom": getattr(cls, "per_atom", False)})
    return out


def get_descriptor(name: str, **options) -> Descriptor:
    reg = _registry()
    if name not in reg:
        raise InvalidInput(f"unknown descriptor {name!r}; known: {', '.join(reg)}")
    cls = reg[name]
    if not getattr(cls, "available", lambda: True)():
        raise InvalidInput(f"descriptor {name!r} needs an optional dependency "
                           f"(uv sync --extra {getattr(cls, 'extra', 'descriptors')})")
    return cls(**options)
