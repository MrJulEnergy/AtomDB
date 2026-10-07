"""Format adapter abstraction.

Every file format is handled by a :class:`FormatAdapter`. Adapters expose frames as
ASE ``Atoms`` via a :class:`FrameSource`, which must never require loading the whole
file into memory. New formats are added by registering an adapter (see registry.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import ase

# Standard properties every adapter reports support for (or not).
CORE_PROPERTIES = ("numbers", "positions", "cell", "pbc")


@dataclass(frozen=True)
class Capabilities:
    """What a format can represent. Used to compute conversion losses."""

    cell: bool = True
    pbc: bool = True
    energy: bool = False
    forces: bool = False
    stress: bool = False
    calc_other: bool = False  # arbitrary calculator results (dipole, charges, ...)
    info: bool = False  # arbitrary per-frame metadata
    arrays: bool = False  # arbitrary per-atom arrays
    variable_natoms: bool = True
    multi_frame: bool = True
    velocities: bool = False
    nested_info: bool = False  # dict-valued info entries
    float32: bool = False  # stores reduced precision

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass
class PropertyInfo:
    """One field found in a file, independent of format."""

    name: str
    level: str  # "frame" | "atom" | "cell"
    origin: str  # "atoms" | "info" | "arrays" | "calc"
    dtype: str
    shape: list[int] = field(default_factory=list)  # per-frame shape, -1 for n_atoms
    unit: str | None = None
    count: int = 0  # number of frames that have it

    @property
    def key(self) -> str:
        return f"{self.origin}.{self.name}"


class FrameSource(ABC):
    """Lazy access to the frames of a file."""

    #: True if ``get(i)`` is cheap (random access)
    random_access: bool = False
    #: options actually used to read (incl. inferred ones) -> recorded in provenance
    resolved_options: dict
    #: non-fatal problems/assumptions the importer must report
    warnings: list[str]

    def __new__(cls, *args, **kwargs):
        obj = super().__new__(cls)
        obj.resolved_options = {}
        obj.warnings = []
        return obj

    @abstractmethod
    def __len__(self) -> int: ...

    @abstractmethod
    def iter_chunks(self, chunk_size: int = 256) -> Iterator[list[ase.Atoms]]: ...

    def __iter__(self) -> Iterator[ase.Atoms]:
        for chunk in self.iter_chunks():
            yield from chunk

    def get(self, index: int) -> ase.Atoms:
        for i, atoms in enumerate(self):
            if i == index:
                return atoms
        raise IndexError(index)

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FormatAdapter(ABC):
    name: str
    extensions: tuple[str, ...] = ()
    description: str = ""
    capabilities: Capabilities = Capabilities()
    readable: bool = True
    writable: bool = False
    #: options the reader needs (e.g. LAMMPS type map); documented in FILE_FORMATS.md
    read_options: tuple[str, ...] = ()

    def sniff(self, path: Path) -> float:
        """Confidence 0..1 that ``path`` is in this format. Default: by extension."""
        name = path.name.lower()
        return 0.6 if any(name.endswith(ext) for ext in self.extensions) else 0.0

    @abstractmethod
    def open(self, path: Path, **options: Any) -> FrameSource: ...

    def write(self, frames: Iterable[ase.Atoms], path: Path, **options: Any) -> None:
        raise NotImplementedError(f"{self.name}: writing is not supported")
