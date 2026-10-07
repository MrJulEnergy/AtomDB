"""Binary MD trajectories via MDAnalysis (optional extra: ``uv sync --extra md``).

DCD/XTC/TRR/NetCDF carry no element information → a topology file (GRO/PDB/PSF/TPR/...)
is required (``topology=`` read option, ``--topology`` on the CLI). Units are converted
to atomdb conventions: forces kJ/(mol·Å) → eV/Å, time ps → fs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import ase
import MDAnalysis as mda
import numpy as np
from ase.calculators.singlepoint import SinglePointCalculator
from ase.data import atomic_numbers

from ..errors import FormatError, InvalidInput
from .base import Capabilities, FormatAdapter, FrameSource

KJMOL_TO_EV = 0.010364269656262174


def _numbers(u: mda.Universe) -> tuple[np.ndarray, list[str]]:
    warnings = []
    if hasattr(u.atoms, "elements") and all(e for e in u.atoms.elements):
        syms = [str(e).capitalize() for e in u.atoms.elements]
    else:
        from MDAnalysis.guesser.default_guesser import DefaultGuesser

        syms = [str(s).capitalize() for s in DefaultGuesser(None).guess_types(u.atoms.names)]
        warnings.append("elements guessed from atom names by MDAnalysis")
    bad = sorted({s for s in syms if s not in atomic_numbers})
    if bad:
        raise InvalidInput(f"cannot map topology atom types to elements: {bad}")
    return np.array([atomic_numbers[s] for s in syms]), warnings


class MDAFrameSource(FrameSource):
    random_access = True

    def __init__(self, path: Path, topology: Path, mda_format: str):
        try:
            # explicit format: files in the store have no extension
            self.u = mda.Universe(str(topology), str(path), format=mda_format)
        except Exception as e:
            raise FormatError(f"MDAnalysis cannot read {path.name}: {e}", path=str(path)) from e
        self.numbers, self.warnings = _numbers(self.u)
        self.resolved_options = {"topology": str(topology)}

    def __len__(self) -> int:
        return len(self.u.trajectory)

    def _atoms(self, ts) -> ase.Atoms:
        cell = None
        pbc = False
        if ts.dimensions is not None and np.all(ts.dimensions[:3] > 0):
            cell = ts.dimensions  # a, b, c, alpha, beta, gamma
            pbc = True
        atoms = ase.Atoms(numbers=self.numbers, positions=ts.positions.astype(float),
                          cell=cell, pbc=pbc)
        atoms.info["time"] = float(ts.time) * 1000.0  # ps → fs
        atoms.info["step"] = int(ts.frame)
        if ts.has_velocities:
            atoms.arrays["velocities"] = ts.velocities.astype(float)  # Å/ps
        if ts.has_forces:
            atoms.calc = SinglePointCalculator(atoms, forces=ts.forces.astype(float) * KJMOL_TO_EV)
        return atoms

    def get(self, index: int) -> ase.Atoms:
        return self._atoms(self.u.trajectory[index])

    def iter_chunks(self, chunk_size: int = 256) -> Iterator[list[ase.Atoms]]:
        chunk: list[ase.Atoms] = []
        for ts in self.u.trajectory:
            chunk.append(self._atoms(ts))
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []
        if chunk:
            yield chunk


class MDAAdapter(FormatAdapter):
    read_options = ("topology",)

    def __init__(self, name: str, extensions: tuple[str, ...], caps: Capabilities, desc: str,
                 mda_format: str | None = None):
        self.name = name
        self.mda_format = mda_format or name.upper()
        self.extensions = extensions
        self.capabilities = caps
        self.description = desc

    def open(self, path: Path, **options: Any) -> FrameSource:
        topology = options.get("topology")
        if not topology:
            raise InvalidInput(
                f"{self.name} files need a topology for element information: "
                "pass --topology system.gro (or .pdb/.psf/.tpr)",
                path=str(path),
            )
        return MDAFrameSource(path, Path(topology), self.mda_format)


def mdanalysis_adapters() -> list[FormatAdapter]:
    return [
        MDAAdapter("dcd", (".dcd",), Capabilities(float32=True), "CHARMM/NAMD/LAMMPS DCD"),
        MDAAdapter("xtc", (".xtc",), Capabilities(float32=True),
                   "GROMACS XTC (lossy compressed positions)"),
        MDAAdapter("trr", (".trr",), Capabilities(forces=True, velocities=True, float32=True),
                   "GROMACS TRR"),
        MDAAdapter("netcdf", (".nc", ".ncdf"), Capabilities(forces=True, velocities=True),
                   "AMBER NetCDF trajectory", mda_format="NCDF"),
    ]
