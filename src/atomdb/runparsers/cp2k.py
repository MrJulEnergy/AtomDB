"""CP2K input (.inp) and output (.out/.log) parsers.

The input is parsed into a flat ``{"FORCE_EVAL/DFT/SCF/EPS_SCF": "1e-6", ...}`` tree
(repeated sections get their parameter in brackets: ``FORCE_EVAL/SUBSYS/KIND[K]/BASIS_SET``),
honouring ``@SET`` variables and ``@INCLUDE``. Key DFT/MD settings are then extracted.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from .. import vocab

HARTREE_TO_EV = 27.211386245988


def _preprocess(text: str, base: Path | None, variables: dict[str, str], depth: int = 0) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.split("!", 1)[0].split("#", 1)[0].strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith("@SET"):
            parts = line.split(None, 2)
            if len(parts) == 3:
                variables[parts[1]] = parts[2].strip()
            continue
        for k, v in variables.items():
            line = line.replace("${" + k + "}", v).replace("$" + k, v)
        if up.startswith("@INCLUDE"):
            name = line.split(None, 1)[1].strip().strip("'\"") if " " in line else ""
            inc = (base / name) if base else Path(name)
            if depth < 5 and inc.is_file():
                lines += _preprocess(inc.read_text(errors="replace"), inc.parent, variables,
                                     depth + 1)
            continue
        if up.startswith(("@IF", "@ENDIF", "@PRINT")):
            continue
        lines.append(line)
    return lines


def parse_input(text: str, base: Path | None = None) -> dict[str, str]:
    flat: dict[str, str] = {}
    stack: list[str] = []
    for line in _preprocess(text, base, {}):
        if line.startswith("&"):
            head = line[1:].split(None, 1)
            name = head[0].upper()
            if name == "END":
                if stack:
                    stack.pop()
                continue
            param = head[1].strip() if len(head) > 1 else ""
            # repeated sections are disambiguated by their parameter
            key = f"{name}[{param}]" if name == "KIND" and param else name
            stack.append(key)
            # record every section, even empty ones (e.g. &MGGA_X_R2SCAN ... &END)
            flat.setdefault("/".join(stack), "" if name == "KIND" else param)
            continue
        parts = line.split(None, 1)
        kw = parts[0].upper()
        val = parts[1].strip() if len(parts) > 1 else "TRUE"
        path = "/".join([*stack, kw])
        flat[path] = f"{flat[path]} | {val}" if path in flat and kw in ("BASIS_SET",) else val
    return flat


def _get(flat: dict, *suffixes: str) -> str | None:
    for suffix in suffixes:
        for k, v in flat.items():
            if k.endswith(suffix) or k == suffix:
                return v
    return None


def _float(v: str | None) -> float | None:
    if v is None:
        return None
    m = re.search(r"[-+]?\d*\.?\d+(?:[eEdD][-+]?\d+)?", v)
    return float(m.group(0).replace("d", "e").replace("D", "e")) if m else None


def functional_from(flat: dict) -> str | None:
    base = None
    subs: list[str] = []
    for k, v in flat.items():
        if k.endswith("DFT/XC/XC_FUNCTIONAL"):
            base = v.upper()
        m = re.search(r"DFT/XC/XC_FUNCTIONAL/([A-Z0-9_]+)$", k)
        if m and m.group(1) not in ("SECTION_PARAMETERS",):
            subs.append(m.group(1))
    for k in flat:
        m = re.match(r".*DFT/XC/XC_FUNCTIONAL/([A-Z0-9_]+)/", k)
        if m and m.group(1) not in subs:
            subs.append(m.group(1))
    if subs:
        subs = [s for s in subs if s not in ("LIBXC",)]
        return " + ".join(sorted(set(subs)))
    return base


def dispersion_from(flat: dict) -> str | None:
    t = _get(flat, "VDW_POTENTIAL/PAIR_POTENTIAL/TYPE")
    if t:
        return t
    nl = _get(flat, "VDW_POTENTIAL/NON_LOCAL/TYPE")
    if nl:
        return nl
    pt = _get(flat, "VDW_POTENTIAL/POTENTIAL_TYPE", "VDW_POTENTIAL/DISPERSION_FUNCTIONAL")
    return None if pt is None or pt.upper() == "NONE" else pt


def summarize_input(flat: dict) -> dict:
    kinds = {}
    for k, v in flat.items():
        m = re.search(r"SUBSYS/KIND\[([^\]]+)\]/(BASIS_SET|POTENTIAL|ELEMENT)$", k)
        if m:
            kinds.setdefault(m.group(1), {})[m.group(2)] = v
    basis = sorted({x.split()[-1] for k in kinds.values() for x in
                    k.get("BASIS_SET", "").split(" | ") if x})
    pseudo = sorted({k["POTENTIAL"] for k in kinds.values() if "POTENTIAL" in k})
    run_type = (_get(flat, "GLOBAL/RUN_TYPE") or "ENERGY_FORCE").upper()
    func_raw = functional_from(flat)
    smear = None
    if _get(flat, "SCF/SMEAR") not in (None, "OFF", ".FALSE.", "F"):
        smear = " ".join(x for x in (_get(flat, "SCF/SMEAR/METHOD"),
                                     _get(flat, "SCF/SMEAR/ELECTRONIC_TEMPERATURE")) if x) or "ON"
    uks = _get(flat, "DFT/UKS", "DFT/LSD", "DFT/UNRESTRICTED_KOHN_SHAM")
    labels = ["energy"]
    if run_type in ("ENERGY_FORCE", "MD", "GEO_OPT", "CELL_OPT") or "FORCE" in run_type:
        labels.append("forces")
    if _get(flat, "FORCE_EVAL/STRESS_TENSOR") not in (None, "NONE"):
        labels.append("stress")
    dft = {
        "code": "CP2K",
        "functional": vocab.functional(func_raw) if func_raw else None,
        "functional_raw": func_raw,
        "dispersion": vocab.dispersion(dispersion_from(flat) or "none"),
        "basis": ", ".join(basis) or None,
        "pseudopotential": ", ".join(pseudo) or None,
        "cutoff_ry": _float(_get(flat, "DFT/MGRID/CUTOFF")),
        "rel_cutoff_ry": _float(_get(flat, "DFT/MGRID/REL_CUTOFF")),
        "eps_scf": _float(_get(flat, "DFT/SCF/EPS_SCF")),
        "smearing": smear,
        "charge": int(_float(_get(flat, "DFT/CHARGE"))) if _get(flat, "DFT/CHARGE") else None,
        "multiplicity": int(_float(_get(flat, "DFT/MULTIPLICITY")))
        if _get(flat, "DFT/MULTIPLICITY") else None,
        "spin_polarized": None if uks is None else uks.upper() not in ("F", ".FALSE.", "FALSE"),
        "kpoints": _get(flat, "DFT/KPOINTS/SCHEME"),
    }
    md = None
    if run_type == "MD":
        md = {
            "engine": "CP2K",
            "ensemble": vocab.ensemble(_get(flat, "MOTION/MD/ENSEMBLE") or "NVE"),
            "temperature_k": _float(_get(flat, "MOTION/MD/TEMPERATURE")),
            "timestep_fs": _float(_get(flat, "MOTION/MD/TIMESTEP")),
            "n_steps": int(_float(_get(flat, "MOTION/MD/STEPS")) or 0) or None,
            "thermostat": _get(flat, "MOTION/MD/THERMOSTAT/TYPE"),
            "barostat": "CP2K" if _get(flat, "MOTION/MD/BAROSTAT") is not None else None,
            "pressure_bar": _float(_get(flat, "MOTION/MD/BAROSTAT/PRESSURE")),
        }
    coord = _get(flat, "SUBSYS/TOPOLOGY/COORD_FILE_NAME")
    return {
        "run_type": run_type,
        "project": _get(flat, "GLOBAL/PROJECT", "GLOBAL/PROJECT_NAME"),
        "dft": {k: v for k, v in dft.items() if v is not None},
        "md": {k: v for k, v in (md or {}).items() if v is not None} or None,
        "labels": labels,
        "coord_file": coord,
        "plumed": _get(flat, "FREE_ENERGY/METADYN/PLUMED_INPUT_FILE"),
        "basis_file": _get(flat, "DFT/BASIS_SET_FILE_NAME"),
        "potential_file": _get(flat, "DFT/POTENTIAL_FILE_NAME"),
    }


_TS = re.compile(r"PROGRAM (STARTED|ENDED) AT\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def is_output(text_head: str) -> bool:
    return "CP2K|" in text_head or "PROGRAM STARTED AT" in text_head


def parse_output(text: str) -> dict:
    out: dict = {"n_force_evals": 0, "scf_converged": 0, "scf_not_converged": 0,
                 "started_at": None, "ended_at": None}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("CP2K| version string:"):
            out["code_version"] = s.split(":", 1)[1].strip().replace("CP2K version ", "")
        elif s.startswith("CP2K| source code revision number:"):
            out["git_revision"] = s.split(":", 1)[1].strip()
        elif s.startswith("GLOBAL| Run type"):
            out["run_type"] = s.split()[-1]
        elif "PROGRAM STARTED ON" in s:
            out.setdefault("hosts", []).append(s.split()[-1])
        elif "PROGRAM STARTED BY" in s:
            out["user"] = s.split()[-1]
        elif "PROGRAM STARTED IN" in s:
            out["workdir"] = s.split("PROGRAM STARTED IN", 1)[1].strip()
        elif "ENERGY| Total FORCE_EVAL" in s:
            out["n_force_evals"] += 1
            out["last_energy_ev"] = float(s.split()[-1]) * HARTREE_TO_EV
        elif "SCF run converged" in s:
            out["scf_converged"] += 1
        elif "SCF run NOT converged" in s or "SCF run not converged" in s.lower():
            out["scf_not_converged"] += 1
        m = _TS.search(s)
        if m:
            ts = datetime.strptime(m.group(2), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            key = "started_at" if m.group(1) == "STARTED" else "ended_at"
            if key == "started_at":
                out[key] = min(out[key], ts) if out[key] else ts
            else:
                out[key] = max(out[key], ts) if out[key] else ts
    out["completed"] = out["ended_at"] is not None
    if "hosts" in out:
        out["hosts"] = sorted(set(out["hosts"]))
    return out
