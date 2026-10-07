"""PLUMED input parser → enhanced-sampling method, collective variables, bias parameters."""

from __future__ import annotations

import re

BIAS_METHODS = {
    "METAD": "metadynamics", "PBMETAD": "metadynamics",
    "OPES_METAD": "OPES", "OPES_METAD_EXPLORE": "OPES", "OPES_EXPANDED": "OPES",
    "RESTRAINT": "umbrella sampling", "MOVINGRESTRAINT": "steered MD",
    "ABF": "ABF", "DRR": "ABF", "EXTENDED_LAGRANGIAN": "TAMD", "BIASVALUE": "custom bias",
    "EXTERNAL": "custom bias",
}
WALLS = {"UPPER_WALLS", "LOWER_WALLS"}
NON_CV = {"PRINT", "FLUSH", "ENDPLUMED", "UNITS", "INCLUDE", "RESTART", "DUMPATOMS",
          "WHOLEMOLECULES", "FIT_TO_TEMPLATE", "GROUP", "MOLINFO", "WRAPAROUND", "COMMITTOR",
          *BIAS_METHODS, *WALLS}


def _logical_lines(text: str) -> list[str]:
    out, buf = [], None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if buf is not None:
            if line == "...":
                out.append(buf)
                buf = None
            else:
                buf += " " + line.replace("...", "")
            continue
        if line.endswith("..."):
            buf = line[:-3]
            continue
        out.append(line)
    if buf:
        out.append(buf)
    return out


def _parse_line(line: str) -> tuple[str | None, str, dict[str, str], list[str]]:
    tokens = line.split()
    label = None
    if tokens and tokens[0].endswith(":"):
        label = tokens[0][:-1]
        tokens = tokens[1:]
    if not tokens:
        return label, "", {}, []
    action = tokens[0].upper()
    kv: dict[str, str] = {}
    flags: list[str] = []
    for tok in tokens[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            kv[k.upper()] = v.strip("{}")
        else:
            flags.append(tok)
    if "LABEL" in kv:
        label = kv.pop("LABEL")
    return label, action, kv, flags


def _num(v: str):
    parts = v.split(",")
    vals = []
    for p in parts:
        try:
            vals.append(float(p) if re.search(r"[.eE]", p) else int(p))
        except ValueError:
            return v
    return vals[0] if len(vals) == 1 else vals


def parse_plumed(text: str) -> dict:
    cvs, bias, methods = [], {}, []
    for line in _logical_lines(text):
        label, action, kv, flags = _parse_line(line)
        if not action:
            continue
        if action in BIAS_METHODS:
            methods.append(BIAS_METHODS[action])
            bias[label or action.lower()] = {"action": action,
                                             **{k: _num(v) for k, v in kv.items()},
                                             **({"flags": flags} if flags else {})}
        elif action in WALLS:
            bias[label or action.lower()] = {"action": action,
                                             **{k: _num(v) for k, v in kv.items()}}
        elif action not in NON_CV:
            cvs.append({"label": label, "action": action, **kv,
                        **({"flags": flags} if flags else {})})
    method = methods[0] if len(set(methods)) == 1 else (
        "multiple: " + ", ".join(sorted(set(methods))) if methods else None)
    return {"method": method, "collective_variables": cvs, "bias": bias}
