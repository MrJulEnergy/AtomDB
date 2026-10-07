"""Pure functions on single ASE frames: extracting labels, fingerprints, element masks.

Conventions (canonical units): energy eV, forces eV/Å, stress eV/Å³, time fs, T in K.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass

import ase
import numpy as np
from ase.data import chemical_symbols

from .formats.base import PropertyInfo

ENERGY_INFO_KEYS = ("energy", "total_energy", "potential_energy", "free_energy", "REF_energy")
FORCE_ARRAY_KEYS = ("forces", "force", "REF_forces")
STRESS_INFO_KEYS = ("stress", "REF_stress")
TEMPERATURE_INFO_KEYS = ("temperature", "T", "temp")
CALC_UNITS = {
    "energy": "eV", "free_energy": "eV", "forces": "eV/Å", "stress": "eV/Å³",
    "energies": "eV", "dipole": "e·Å", "charges": "e", "magmom": "μB", "magmoms": "μB",
}
LO_BITS = 62  # Z 1..62 in the low word, 63..118 in the high word (keeps values < 2**63)


def calc_results(atoms: ase.Atoms) -> dict:
    calc = atoms.calc
    return dict(getattr(calc, "results", {}) or {}) if calc is not None else {}


def get_energy(atoms: ase.Atoms) -> float | None:
    res = calc_results(atoms)
    for k in ("energy", "free_energy"):
        if k in res and res[k] is not None:
            return float(np.asarray(res[k]).reshape(-1)[0])
    for k in ENERGY_INFO_KEYS:
        v = atoms.info.get(k)
        if v is not None and np.ndim(v) == 0:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def get_forces(atoms: ase.Atoms) -> np.ndarray | None:
    res = calc_results(atoms)
    if res.get("forces") is not None:
        return np.asarray(res["forces"], dtype=float)
    for k in FORCE_ARRAY_KEYS:
        if k in atoms.arrays:
            return np.asarray(atoms.arrays[k], dtype=float)
    return None


def get_stress(atoms: ase.Atoms) -> np.ndarray | None:
    res = calc_results(atoms)
    if res.get("stress") is not None:
        return np.asarray(res["stress"], dtype=float)
    for k in STRESS_INFO_KEYS:
        if k in atoms.info:
            return np.asarray(atoms.info[k], dtype=float)
    return None


def get_temperature(atoms: ase.Atoms) -> tuple[float | None, str | None]:
    """Temperature from metadata, else instantaneous kinetic temperature from momenta."""
    for k in TEMPERATURE_INFO_KEYS:
        v = atoms.info.get(k)
        if v is not None and np.ndim(v) == 0:
            try:
                return float(v), f"info.{k}"
            except (TypeError, ValueError):
                pass
    if "momenta" in atoms.arrays and len(atoms) > 0 and np.any(atoms.arrays["momenta"]):
        return float(atoms.get_temperature()), "kinetic"
    return None, None


def volume(atoms: ase.Atoms) -> float | None:
    if atoms.cell.rank != 3:
        return None
    v = abs(float(atoms.cell.volume))
    return v if v > 0 else None


def formula(numbers: np.ndarray) -> str:
    """Hill-order formula, e.g. ``ClH2KO`` (counts of 1 are omitted)."""
    counts = Counter(int(z) for z in numbers)
    syms = sorted(counts, key=lambda z: chemical_symbols[z])
    if 6 in counts:  # Hill order: C, H, then alphabetical
        syms = [6] + ([1] if 1 in counts else []) + [z for z in syms if z not in (1, 6)]
    return "".join(f"{chemical_symbols[z]}{counts[z] if counts[z] > 1 else ''}" for z in syms)


def element_mask(numbers) -> tuple[int, int]:
    lo = hi = 0
    for z in {int(z) for z in numbers}:
        if 1 <= z <= LO_BITS:
            lo |= 1 << (z - 1)
        elif z > LO_BITS:
            hi |= 1 << (z - LO_BITS - 1)
    return lo, hi


def mask_for_symbols(symbols: list[str]) -> tuple[int, int]:
    from ase.data import atomic_numbers

    return element_mask([atomic_numbers[s] for s in symbols])


def fingerprint(atoms: ase.Atoms) -> str:
    """Structure identity key, robust to float32 round-off and file re-encoding.

    Uses atom order, coarse aggregate geometry and cell, so the same frame stored in a
    different file/format (or with different precision) gets the same key. Candidate
    matches are confirmed by comparing coordinates (see services/whatis.py).
    """
    pos = np.asarray(atoms.positions, dtype=np.float64)
    h = hashlib.sha1()
    h.update(np.asarray(atoms.numbers, dtype=np.int16).tobytes())
    if len(pos):
        agg = np.concatenate([pos.mean(axis=0), pos.std(axis=0), [np.abs(pos).sum() / len(pos)]])
        h.update(np.round(agg, 2).astype(np.float64).tobytes())
    h.update(np.round(np.asarray(atoms.cell, dtype=np.float64), 2).tobytes())
    return h.hexdigest()[:20]


@dataclass
class FrameRow:
    idx: int
    n_atoms: int
    formula: str
    elem_mask_lo: int
    elem_mask_hi: int
    energy: float | None
    energy_per_atom: float | None
    fmax: float | None
    temperature: float | None
    volume: float | None
    time: float | None
    step: int | None
    fingerprint: str


def frame_row(idx: int, atoms: ase.Atoms) -> FrameRow:
    n = len(atoms)
    e = get_energy(atoms)
    f = get_forces(atoms)
    t, _ = get_temperature(atoms)
    lo, hi = element_mask(atoms.numbers)
    time = atoms.info.get("time")
    step = atoms.info.get("step")
    return FrameRow(
        idx=idx,
        n_atoms=n,
        formula=formula(atoms.numbers),
        elem_mask_lo=lo,
        elem_mask_hi=hi,
        energy=e,
        energy_per_atom=e / n if e is not None and n else None,
        fmax=float(np.linalg.norm(f, axis=1).max()) if f is not None and len(f) else None,
        temperature=t,
        volume=volume(atoms),
        time=float(time) if time is not None and np.ndim(time) == 0 else None,
        step=int(step) if step is not None and np.ndim(step) == 0 else None,
        fingerprint=fingerprint(atoms),
    )


# --------------------------------------------------------------------------- catalog


def _describe(value, n_atoms: int, per_atom: bool) -> tuple[str, list[int]]:
    if isinstance(value, dict):
        return "dict", []
    if isinstance(value, str):
        return "str", []
    arr = np.asarray(value)
    shape = list(arr.shape)
    if per_atom and shape and shape[0] == n_atoms:
        shape[0] = -1
    return str(arr.dtype) if arr.dtype != object else "object", shape


class Catalog:
    """Accumulates the set of properties present across frames."""

    def __init__(self):
        self.props: dict[str, PropertyInfo] = {}
        self.n_frames = 0

    def _add(self, name, level, origin, value, n_atoms, unit=None):
        key = f"{origin}.{name}"
        p = self.props.get(key)
        dtype, shape = _describe(value, n_atoms, level == "atom")
        if p is None:
            p = PropertyInfo(name=name, level=level, origin=origin, dtype=dtype, shape=shape,
                             unit=unit)
            self.props[key] = p
        p.count += 1

    def add(self, atoms: ase.Atoms) -> None:
        self.n_frames += 1
        n = len(atoms)
        for k, v in atoms.info.items():
            self._add(k, "frame", "info", v, n)
        for k, v in atoms.arrays.items():
            if k in ("numbers", "positions"):
                continue
            self._add(k, "atom", "arrays", v, n)
        for k, v in calc_results(atoms).items():
            if v is None:
                continue
            arr = np.asarray(v)
            level = "atom" if arr.ndim >= 1 and arr.shape[0] == n and k not in ("stress",) else (
                "cell" if k in ("stress", "virial") else "frame")
            self._add(k, level, "calc", v, n, unit=CALC_UNITS.get(k))
        if atoms.cell.rank > 0:
            self._add("cell", "cell", "atoms", np.asarray(atoms.cell), n, unit="Å")
        self._add("pbc", "cell", "atoms", atoms.pbc, n)

    def keys(self) -> set[str]:
        return set(self.props)

    def to_list(self) -> list[PropertyInfo]:
        return sorted(self.props.values(), key=lambda p: (p.origin, p.name))
