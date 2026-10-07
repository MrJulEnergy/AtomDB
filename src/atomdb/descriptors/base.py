from __future__ import annotations

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

    def fit(self, elements: list[str]) -> None:
        self.elements = sorted(elements)

    @abstractmethod
    def compute(self, frames: list[ase.Atoms]) -> np.ndarray: ...

    def feature_names(self) -> list[str]:
        return []

    def feature_groups(self) -> np.ndarray | None:
        """Group id per feature; groups get equal total weight in distances (optional)."""
        return None


@cache
def _registry() -> dict[str, type[Descriptor]]:
    from .simple import CompositionRDF
    from .soap import SOAPAverage

    reg: dict[str, type[Descriptor]] = {"composition-rdf": CompositionRDF, "soap": SOAPAverage}
    for ep in entry_points(group="atomdb.descriptors"):
        cls = ep.load()
        reg[cls.name] = cls
    return reg


def list_descriptors() -> list[dict]:
    out = []
    for name, cls in _registry().items():
        out.append({"name": name, "description": cls.description,
                    "available": getattr(cls, "available", lambda: True)()})
    return out


def get_descriptor(name: str, **options) -> Descriptor:
    reg = _registry()
    if name not in reg:
        raise InvalidInput(f"unknown descriptor {name!r}; known: {', '.join(reg)}")
    cls = reg[name]
    if not getattr(cls, "available", lambda: True)():
        raise InvalidInput(f"descriptor {name!r} needs an optional dependency "
                           "(uv sync --extra descriptors)")
    return cls(**options)
