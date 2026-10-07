"""Adapters built on ``ase.io`` (xyz, extxyz, CIF, POSCAR, LAMMPS, PDB, GRO, ...)."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import ase
import ase.io
import numpy as np

from ..errors import FormatError, InvalidInput
from .base import Capabilities, FormatAdapter, FrameSource


class AseFrameSource(FrameSource):
    """Streams frames with ``ase.io.iread``; length is determined by a counting pass."""

    def __init__(self, path: Path, ase_format: str, read_kwargs: dict | None = None):
        self.path = path
        self.ase_format = ase_format
        self.read_kwargs = read_kwargs or {}
        self._len: int | None = None

    def _iter(self) -> Iterator[ase.Atoms]:
        try:
            yield from ase.io.iread(
                self.path, index=":", format=self.ase_format, **self.read_kwargs
            )
        except FormatError:
            raise
        except Exception as e:  # ase raises many different exception types
            raise FormatError(
                f"cannot read {self.path.name} as {self.ase_format}: {type(e).__name__}: {e}",
                path=str(self.path),
                format=self.ase_format,
            ) from e

    def __len__(self) -> int:
        if self._len is None:
            self._len = sum(1 for _ in self._iter())
        return self._len

    def iter_chunks(self, chunk_size: int = 256) -> Iterator[list[ase.Atoms]]:
        it = self._iter()
        n = 0
        while chunk := list(itertools.islice(it, chunk_size)):
            n += len(chunk)
            yield chunk
        self._len = n


class AseAdapter(FormatAdapter):
    ase_format: str

    def __init__(
        self,
        name: str,
        ase_format: str,
        extensions: tuple[str, ...],
        capabilities: Capabilities,
        description: str = "",
        writable: bool = True,
        filename_patterns: tuple[str, ...] = (),
    ):
        self.name = name
        self.ase_format = ase_format
        self.extensions = extensions
        self.capabilities = capabilities
        self.description = description
        self.writable = writable
        self.filename_patterns = filename_patterns

    def sniff(self, path: Path) -> float:
        name = path.name.lower()
        if any(name.startswith(p) for p in self.filename_patterns):
            return 0.8
        return super().sniff(path)

    def open(self, path: Path, **options: Any) -> FrameSource:
        return AseFrameSource(path, self.ase_format, options)

    def write(self, frames: Iterable[ase.Atoms], path: Path, **options: Any) -> None:
        frames = list(frames) if not self.capabilities.multi_frame else frames
        if not self.capabilities.multi_frame:
            if len(frames) != 1:
                raise InvalidInput(
                    f"{self.name} holds a single structure; got {len(frames)} frames "
                    "(select one frame first)"
                )
            ase.io.write(path, frames[0], format=self.ase_format, **options)
            return
        ase.io.write(path, list(frames), format=self.ase_format, **options)


class ExtxyzAdapter(AseAdapter):
    """Extended XYZ. Distinguished from plain XYZ by a ``Lattice=``/``Properties=`` header."""

    def sniff(self, path: Path) -> float:
        if not path.name.lower().endswith((".xyz", ".extxyz")):
            return 0.0
        try:
            with open(path, errors="replace") as fh:
                fh.readline()
                comment = fh.readline()
        except OSError:
            return 0.0
        if "Properties=" in comment or "Lattice=" in comment or "=" in comment:
            return 0.9
        return 0.5 if path.name.lower().endswith(".extxyz") else 0.3


class PlainXyzAdapter(AseAdapter):
    def sniff(self, path: Path) -> float:
        if not path.name.lower().endswith(".xyz"):
            return 0.0
        try:
            with open(path, errors="replace") as fh:
                fh.readline()
                comment = fh.readline()
        except OSError:
            return 0.0
        return 0.2 if "=" in comment else 0.7


class LammpsDumpAdapter(AseAdapter):
    """LAMMPS text dump. Atom types carry no element info → ``type_map`` is required."""

    read_options = ("type_map",)

    def open(self, path: Path, **options: Any) -> FrameSource:
        type_map = options.pop("type_map", None)
        if not type_map and _dump_has_element_column(path):
            return AseFrameSource(path, self.ase_format, options)
        if not type_map:
            raise InvalidInput(
                "LAMMPS dump needs an explicit type map, e.g. --type-map 1=K,2=Cl,3=O,4=H",
                path=str(path),
            )
        mapping = parse_type_map(type_map)
        src = AseFrameSource(path, self.ase_format, options)
        out = _TypeMappedSource(src, mapping)
        out.resolved_options = {"type_map": _type_map_str(mapping)}
        return out


class LammpsDataAdapter(AseAdapter):
    """LAMMPS data file. Types → elements from ``type_map`` or, failing that, inferred
    from the ``Masses`` section (reported as a warning and recorded in provenance)."""

    read_options = ("type_map",)

    def open(self, path: Path, **options: Any) -> FrameSource:
        type_map = options.pop("type_map", None)
        warnings: list[str] = []
        if type_map:
            mapping = parse_type_map(type_map)
        else:
            mapping = _masses_to_elements(path)
            if mapping is None:
                raise InvalidInput(
                    "LAMMPS data file has no usable Masses section; pass a type map, "
                    "e.g. --type-map 1=K,2=Cl",
                    path=str(path),
                )
            warnings.append(
                f"elements inferred from LAMMPS masses: {_type_map_str(mapping)}"
            )
        src = AseFrameSource(path, self.ase_format, {"Z_of_type": mapping, **options})
        src.resolved_options = {"type_map": _type_map_str(mapping)}
        src.warnings = warnings
        return src


def _masses_to_elements(path: Path) -> dict[int, int] | None:
    from ase.data import atomic_masses

    lines = path.read_text(errors="replace").splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().split("#")[0].strip() == "Masses")
    except StopIteration:
        return None
    mapping: dict[int, int] = {}
    for line in lines[start + 1 :]:
        body = line.split("#")[0].strip()
        if not body:
            if mapping:
                break
            continue
        parts = body.split()
        if len(parts) < 2 or not parts[0].isdigit():
            break
        mass = float(parts[1])
        z = int(np.argmin(np.abs(atomic_masses[1:] - mass))) + 1
        if abs(atomic_masses[z] - mass) > 0.1:
            return None
        mapping[int(parts[0])] = z
    return mapping or None


def _type_map_str(mapping: dict[int, int]) -> str:
    from ase.data import chemical_symbols

    return ",".join(f"{t}={chemical_symbols[z]}" for t, z in sorted(mapping.items()))


class _TypeMappedSource(FrameSource):
    def __init__(self, inner: FrameSource, mapping: dict[int, int]):
        self.inner = inner
        self.mapping = mapping

    def __len__(self) -> int:
        return len(self.inner)

    def iter_chunks(self, chunk_size: int = 256):
        for chunk in self.inner.iter_chunks(chunk_size):
            for atoms in chunk:
                # ase stores LAMMPS types as atomic numbers (type 1 -> H, ...)
                types = atoms.numbers.copy()
                missing = sorted(set(types.tolist()) - set(self.mapping))
                if missing:
                    raise InvalidInput(f"type map has no entry for LAMMPS types {missing}")
                atoms.arrays["lammps_type"] = types
                atoms.numbers = np.array([self.mapping[t] for t in types])
            yield chunk


def _dump_has_element_column(path: Path) -> bool:
    with open(path, errors="replace") as fh:
        for _, line in zip(range(200), fh):
            if line.startswith("ITEM: ATOMS"):
                return "element" in line.split()
    return False


def parse_type_map(spec: str | dict) -> dict[int, int]:
    from ase.data import atomic_numbers

    if isinstance(spec, dict):
        items = spec.items()
    else:
        items = (part.split("=") for part in spec.split(",") if part.strip())
    out: dict[int, int] = {}
    for k, v in items:
        sym = str(v).strip()
        if sym not in atomic_numbers:
            raise InvalidInput(f"unknown element in type map: {sym!r}")
        out[int(k)] = atomic_numbers[sym]
    return out


FULL = Capabilities(
    energy=True, forces=True, stress=True, calc_other=True, info=True, arrays=True,
    velocities=True,
)
STRUCTURE_ONLY = Capabilities(multi_frame=False)


def ase_adapters() -> list[FormatAdapter]:
    return [
        ExtxyzAdapter(
            "extxyz", "extxyz", (".extxyz", ".xyz"), FULL,
            "Extended XYZ: per-frame info, per-atom arrays, calculator results",
        ),
        PlainXyzAdapter(
            "xyz", "xyz", (".xyz",), Capabilities(cell=False, pbc=False),
            "Plain XYZ: elements and positions only",
        ),
        AseAdapter("cif", "cif", (".cif",), Capabilities(), "Crystallographic Information File"),
        AseAdapter(
            "vasp", "vasp", (".poscar", ".contcar", ".vasp"), STRUCTURE_ONLY,
            "VASP POSCAR/CONTCAR", filename_patterns=("poscar", "contcar"),
        ),
        AseAdapter(
            "vasp-xdatcar", "vasp-xdatcar", (".xdatcar",), Capabilities(), "VASP XDATCAR",
            writable=False, filename_patterns=("xdatcar",),
        ),
        AseAdapter(
            "vasp-out", "vasp-out", (".outcar",),
            Capabilities(energy=True, forces=True, stress=True),
            "VASP OUTCAR", writable=False, filename_patterns=("outcar",),
        ),
        LammpsDataAdapter(
            "lammps-data", "lammps-data", (".lmp", ".data", ".lammps"),
            Capabilities(multi_frame=False, arrays=False), "LAMMPS data file",
        ),
        LammpsDumpAdapter(
            "lammps-dump", "lammps-dump-text", (".dump", ".lammpstrj"),
            Capabilities(forces=True, velocities=True),
            "LAMMPS text dump (requires type map)", writable=False,
        ),
        AseAdapter("proteindatabank", "proteindatabank", (".pdb",), Capabilities(),
                   "Protein Data Bank"),
        AseAdapter("gromacs", "gromacs", (".gro",), Capabilities(velocities=True),
                   "GROMACS .gro"),
        AseAdapter("traj", "traj", (".traj",), Capabilities(
            energy=True, forces=True, stress=True, calc_other=True, velocities=True),
            "ASE trajectory"),
    ]
