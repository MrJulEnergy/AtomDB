"""Uniform, JSON-ready description of any node (dataset, calculation, model, job, file)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Calculation, Dataset, File, HPCJob, Model, Node
from .context import AtomDB
from .datasets import elements_of, file_out


def _row(obj, skip=("calc_id", "id")) -> dict[str, Any]:
    if obj is None:
        return {}
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns
            if c.key not in skip and getattr(obj, c.key) is not None}


def subtitle(s: Session, node: Node) -> str:
    if isinstance(node, Dataset):
        els = " ".join(elements_of(s, node))
        labels = "".join(x for x, f in (("E", node.has_energy), ("F", node.has_forces),
                                        ("S", node.has_stress)) if f)
        return f"{node.n_frames or 0} frames · {els}" + (f" · {labels}" if labels else "")
    if isinstance(node, Calculation):
        if node.md is not None:
            md = node.md
            bits = [md.engine, md.ensemble,
                    f"{md.temperature_k:g} K" if md.temperature_k is not None else None,
                    f"{md.pressure_bar:g} bar" if md.pressure_bar is not None else None,
                    f"dt {md.timestep_fs:g} fs" if md.timestep_fs is not None else None]
            es = ", ".join(e.method for e in node.enhanced_sampling)
            return " · ".join(b for b in bits if b) + (f" · {es}" if es else "")
        if node.dft is not None:
            d = node.dft
            fd = "/".join(x for x in (d.functional, d.dispersion if d.dispersion != "none" else None)
                          if x)
            return " · ".join(x for x in (d.code, fd, d.basis) if x)
        if node.sampling is not None:
            return f"{node.sampling.method} · {node.sampling.n_selected or '?'} selected"
        if node.calc_type == "conversion" and node.parameters:
            lost = node.parameters.get("lost_fields") or []
            return f"{node.parameters.get('from')} → {node.parameters.get('to')}" + (
                f" · LOST {len(lost)} fields" if lost else "")
        return node.calc_type
    if isinstance(node, Model):
        return " · ".join(x for x in (node.family, node.version,
                                      f"rc {node.cutoff:g} Å" if node.cutoff else None) if x)
    if isinstance(node, HPCJob):
        return " · ".join(x for x in (node.cluster, node.partition, f"job {node.job_id}"
                                      if node.job_id else None) if x)
    if isinstance(node, File):
        return f"{node.format or '?'} · {node.size_bytes} B"
    return node.kind


def describe(adb: AtomDB, s: Session, node: Node, full: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": node.id, "kind": node.kind, "name": node.name, "status": node.status,
        "subtitle": subtitle(s, node),
    }
    if isinstance(node, Calculation):
        d["calc_type"] = node.calc_type
    if not full:
        return d
    d.update({
        "description": node.description, "created_at": node.created_at,
        "updated_at": node.updated_at, "tags": sorted(t.name for t in node.tags),
        "attrs": {a.key: {"value": a.value, "unit": a.unit, "source": a.source}
                  for a in node.attrs},
        "notes": [n.text for n in node.notes],
    })
    if isinstance(node, Calculation):
        d.update({
            "started_at": node.started_at, "ended_at": node.ended_at, "command": node.command,
            "workdir": node.workdir, "exit_status": node.exit_status,
            "parameters": node.parameters, "software_versions": node.software_versions,
            "md": _row(node.md) or None, "dft": _row(node.dft) or None,
            "sampling": _row(node.sampling) or None, "training": _row(node.training) or None,
            "enhanced_sampling": [_row(e) for e in node.enhanced_sampling],
            "code_params": {p.key: p.value for p in node.params},
        })
    elif isinstance(node, Model):
        d.update(_row(node, skip=("node_id",)))
        if node.checkpoint_file_id:
            d["checkpoint"] = file_out(adb, s.get(File, node.checkpoint_file_id)).model_dump()
    elif isinstance(node, HPCJob):
        d.update(_row(node, skip=("node_id",)))
    elif isinstance(node, File):
        d.update(file_out(adb, node).model_dump())
    elif isinstance(node, Dataset):
        from .datasets import detail

        d.update(detail(adb, s, node).model_dump())
    return d
