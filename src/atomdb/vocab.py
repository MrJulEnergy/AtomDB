"""Canonical vocabulary for searchable scientific fields.

Raw values are always kept alongside; canonical values make ``r2scan``, ``R2SCAN``,
``r²SCAN`` and CP2K's ``MGGA_X_R2SCAN + MGGA_C_R2SCAN`` the same thing.
"""

from __future__ import annotations

import re

FUNCTIONALS = {
    # combinations (CP2K/libxc exchange + correlation) are keyed in sorted order
    "r2scan": "r2SCAN", "r²scan": "r2SCAN", "mggacr2scan+mggaxr2scan": "r2SCAN",
    "mggaxr2scan": "r2SCAN", "rscan": "rSCAN", "scan": "SCAN", "mggacrscan+mggaxrscan": "rSCAN",
    "mggacscan+mggaxscan": "SCAN", "pbe": "PBE", "ggacpbe+ggaxpbe": "PBE", "pbesol": "PBEsol",
    "revpbe": "revPBE", "rpbe": "RPBE", "blyp": "BLYP", "b3lyp": "B3LYP", "pbe0": "PBE0",
    "hse06": "HSE06", "b97mv": "B97M-V", "wb97xd": "ωB97X-D", "wb97mv": "ωB97M-V",
    "ωb97xd": "ωB97X-D", "ωb97mv": "ωB97M-V", "tpss": "TPSS", "optb88vdw": "optB88-vdW",
    "lda": "LDA", "pade": "LDA", "r2scan3c": "r2SCAN-3c",
}
DISPERSION = {
    "": "none", "none": "none", "no": "none", "false": "none", "off": "none",
    "d2": "D2", "dftd2": "D2",
    "d3": "D3", "dftd3": "D3", "d3zero": "D3", "dftd3zero": "D3",
    "d3bj": "D3BJ", "dftd3bj": "D3BJ", "d3becke-johnson": "D3BJ",
    "d4": "D4", "dftd4": "D4",
    "ts": "TS", "mbd": "MBD", "vv10": "VV10", "rvv10": "rVV10", "xdm": "XDM",
}
CODES = {
    "cp2k": "CP2K", "orca": "ORCA", "vasp": "VASP", "qe": "Quantum ESPRESSO",
    "quantumespresso": "Quantum ESPRESSO", "pw.x": "Quantum ESPRESSO", "gpaw": "GPAW",
    "fhiaims": "FHI-aims", "aims": "FHI-aims", "psi4": "Psi4", "gaussian": "Gaussian",
    "turbomole": "TURBOMOLE", "siesta": "SIESTA", "castep": "CASTEP", "abinit": "ABINIT",
}
# helper programs that add a correction to existing labels; not the electronic-structure code
POSTHOC_CODES = {"dftd4", "dftd3", "sdftd3", "simpledftd3"}
ENSEMBLES = {"nve": "NVE", "nvt": "NVT", "npt": "NPT", "nph": "NPH", "muvt": "μVT"}
ES_METHODS = {
    "metad": "metadynamics", "metadynamics": "metadynamics", "wtmetad": "metadynamics",
    "opes": "OPES", "opesmetad": "OPES", "opes_metad": "OPES", "umbrella": "umbrella sampling",
    "us": "umbrella sampling", "abf": "ABF", "steered": "steered MD", "smd": "steered MD",
    "remd": "replica exchange", "replicaexchange": "replica exchange", "tamd": "TAMD",
    "temperatureaccelerated": "TAMD", "custom": "custom bias",
}


def _key(value: str) -> str:
    v = value.strip().lower()
    return re.sub(r"[\s\-_()/]", "", v)


def functional(value: str | None) -> str | None:
    if value is None:
        return None
    key = "+".join(sorted(_key(p) for p in value.split("+"))) if "+" in value else _key(value)
    return FUNCTIONALS.get(key, value.strip())


def dispersion(value: str | None) -> str | None:
    if value is None:
        return None
    return DISPERSION.get(_key(value), value.strip())


def code(value: str | None) -> str | None:
    """Canonical electronic-structure code name; ``None`` for post-hoc correction tools."""
    if value is None or _key(value) in POSTHOC_CODES:
        return None
    return CODES.get(_key(value), value.strip())


def ensemble(value: str | None) -> str | None:
    if value is None:
        return None
    return ENSEMBLES.get(_key(value), value.strip().upper())


def es_method(value: str | None) -> str | None:
    if value is None:
        return None
    return ES_METHODS.get(_key(value), value.strip())


def match_key(value: str) -> str:
    """Comparison key used by search (case/punctuation-insensitive)."""
    return _key(value)
