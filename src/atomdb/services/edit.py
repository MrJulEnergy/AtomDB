"""Editing metadata in place (web UI): node fields, DFT/MD settings, copying settings.

Unlike ``record_*``/``annotate`` (which always add new records), these functions change
existing rows. Every change is written to the audit log with old and new values, so
nothing is lost silently.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import vocab
from ..db.models import Calculation, Dataset, DFTParams, Edge, MDParams, Node, NodeAttr
from ..errors import InvalidInput
from .context import AtomDB
from .datasets import add_tags, get_dataset, get_node
from .labels import LABEL_MAX
from .provenance import add_edge
from .records import _mark_labels, add_params, record_dft, record_md, resolve_model, touch

SECTIONS: dict[str, type] = {"dft": DFTParams, "md": MDParams}
#: node attributes that describe one particular file and are never copied
FILE_SPECIFIC_ATTRS = {"source_directory"}
COPY_PARTS = ("dft", "md", "attrs", "tags")


def _columns(table: type) -> dict[str, Any]:
    return {c.key: c for c in table.__table__.columns if c.key != "calc_id"}


def _coerce(col, value: Any) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if col.key == "labels":
        if isinstance(value, str):
            value = [x.strip() for x in value.split(",") if x.strip()]
        return list(value) or None
    try:
        py = col.type.python_type
    except NotImplementedError:
        return value
    try:
        if py is bool:
            return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
        if py is int:
            return int(value)
        if py is float:
            return float(value)
        if py is str:
            return str(value).strip()
    except (TypeError, ValueError):
        raise InvalidInput(f"{col.key}: cannot interpret {value!r} as {py.__name__}") from None
    return value


def _normalize(section: str, values: dict[str, Any]) -> dict[str, Any]:
    cols = _columns(SECTIONS[section])
    unknown = sorted(set(values) - set(cols))
    if unknown:
        raise InvalidInput(f"unknown {section} field(s): {', '.join(unknown)}; "
                           f"known: {', '.join(cols)}")
    out = {k: _coerce(cols[k], v) for k, v in values.items()}
    if section == "dft":
        if "functional" in out and out["functional"] is not None:
            out.setdefault("functional_raw", out["functional"])
            out["functional"] = vocab.functional(out["functional"])
        if "dispersion" in out and out["dispersion"] is not None:
            out["dispersion"] = vocab.dispersion(out["dispersion"])
    if section == "md" and out.get("ensemble"):
        out["ensemble"] = vocab.ensemble(out["ensemble"])
    return out


def _auto_name(s: Session, calc: Calculation) -> str | None:
    """The name ``record_*`` would give this calculation (used to keep auto names current)."""
    if calc.dft is not None:
        d = calc.dft
        disp = d.dispersion if d.dispersion and d.dispersion != "none" else None
        return "/".join(x for x in (d.code, d.functional, disp) if x) or "DFT calculation"
    if calc.md is not None:
        m = calc.md
        model = next((e.dst for e in _edges(s, src=calc.id, relation="driven_by")), None)
        mname = s.get(Node, model).name if model else None
        t = m.temperature_k
        return " ".join(x for x in ("MD", m.ensemble, f"{t:g} K" if t is not None else None,
                                    f"({mname})" if mname else None) if x)
    return None


def _edges(s: Session, **where) -> list[Edge]:
    q = select(Edge)
    for k, v in where.items():
        q = q.where(getattr(Edge, k) == v)
    return list(s.execute(q).scalars())


# --------------------------------------------------------------------------- calculations


def edit_calc(s: Session, calc_id: str, section: str, values: dict[str, Any],
              model: str | None = None) -> dict[str, list]:
    """Change DFT or MD settings of an existing calculation. Returns {field: [old, new]}."""
    calc = get_node(s, calc_id)
    if not isinstance(calc, Calculation):
        raise InvalidInput(f"{calc.id} is a {calc.kind}, not a calculation")
    if section not in SECTIONS:
        raise InvalidInput(f"unknown section {section!r}; editable: {', '.join(SECTIONS)}")
    if calc.calc_type != section:
        raise InvalidInput(f"{calc.id} is a {calc.calc_type} calculation, cannot set {section} "
                           "settings")
    values = _normalize(section, values)
    old_auto = _auto_name(s, calc)
    row = getattr(calc, section)
    if row is None:
        row = SECTIONS[section]()
        setattr(calc, section, row)
    changes: dict[str, list] = {}
    for k, v in values.items():
        old = getattr(row, k)
        if old != v:
            changes[k] = [old, v]
            setattr(row, k, v)
    if section == "md" and model is not None:
        changes.update(_set_model(s, calc, model))
    if section == "dft" and "labels" in changes and row.labels:
        for e in _edges(s, src=calc.id, relation="generated"):
            if isinstance(s.get(Node, e.dst), Dataset):
                _mark_labels(s, e.dst, calc.id, row.labels)
    if changes:
        s.flush()
        touch(calc)
        if calc.name == old_auto:  # keep auto-generated names in sync with the settings
            calc.name = _auto_name(s, calc) or calc.name
        AtomDB.audit(s, f"edit.{section}", calc.id, changes=changes)
    return changes


def _set_model(s: Session, calc: Calculation, model: str) -> dict[str, list]:
    old = _edges(s, src=calc.id, relation="driven_by")
    old_ids = [e.dst for e in old]
    new_id = resolve_model(s, model).id if model.strip() else None
    if old_ids == ([new_id] if new_id else []):
        return {}
    for e in old:
        s.delete(e)
    s.flush()
    if new_id:
        add_edge(s, calc.id, new_id, "driven_by", source="user")
    return {"model": [old_ids or None, new_id]}


# --------------------------------------------------------------------------- datasets


def dataset_calcs(s: Session, ds_id: str) -> dict[str, list[Calculation]]:
    """The DFT calculation(s) that labeled a dataset and the MD run(s) that generated it."""
    out: dict[str, list[Calculation]] = {"dft": [], "md": []}
    for e in _edges(s, src=ds_id, relation="labeled_by"):
        c = s.get(Node, e.dst)
        if isinstance(c, Calculation) and c.calc_type == "dft":
            out["dft"].append(c)
    for e in _edges(s, dst=ds_id, relation="generated"):
        c = s.get(Node, e.src)
        if isinstance(c, Calculation) and c.calc_type == "md" and c not in out["md"]:
            out["md"].append(c)
    return out


def _settings_of(s: Session, calc: Calculation, section: str, ds_id: str) -> dict[str, Any]:
    row = getattr(calc, section)
    values = {k: getattr(row, k) if row is not None else None for k in _columns(SECTIONS[section])}
    d: dict[str, Any] = {"calc_id": calc.id, "name": calc.name, "values": values,
                         "inputs": [e.dst for e in _edges(s, src=calc.id, relation="used")],
                         "shared_with": _other_outputs(s, calc.id, ds_id)}
    if section == "md":
        d["model"] = next((s.get(Node, e.dst).name for e in
                           _edges(s, src=calc.id, relation="driven_by")), None)
    return d


def _other_outputs(s: Session, calc_id: str, ds_id: str) -> list[str]:
    """Other datasets produced by the same calculation (they share its settings)."""
    return sorted({e.dst for e in _edges(s, src=calc_id, relation="generated")
                   if e.dst != ds_id and isinstance(s.get(Node, e.dst), Dataset)})


def dataset_settings(s: Session, ds_id: str) -> dict[str, Any]:
    """Editable settings of a dataset: its own labeling/generating calculations."""
    ds = get_dataset(s, ds_id)
    calcs = dataset_calcs(s, ds.id)
    return {
        "dataset": ds.id,
        "fields": {sec: list(_columns(t)) for sec, t in SECTIONS.items()},
        **{sec: [_settings_of(s, c, sec, ds.id) for c in cs] for sec, cs in calcs.items()},
    }


def set_dataset_settings(s: Session, ds_id: str, section: str, values: dict[str, Any],
                         inputs: list[str] | None = None, model: str | None = None,
                         calc_id: str | None = None) -> dict[str, Any]:
    """Edit the dataset's DFT/MD calculation in place, or record one if there is none.

    With several candidate calculations, ``calc_id`` selects which one to edit.
    """
    ds = get_dataset(s, ds_id)
    calcs = dataset_calcs(s, ds.id)[section]
    if calc_id is not None:
        calcs = [c for c in calcs if c.id == get_node(s, calc_id).id]
        if not calcs:
            raise InvalidInput(f"{calc_id} is not a {section} calculation of {ds.id}")
    if len(calcs) > 1:
        raise InvalidInput(f"{ds.id} has {len(calcs)} {section} calculations "
                           f"({', '.join(c.id for c in calcs)}); choose one with calc_id")
    if calcs:
        calc = calcs[0]
        changes = edit_calc(s, calc.id, section, values, model=model)
        for i in inputs or []:
            add_edge(s, calc.id, get_node(s, i).id, "used", source="user")
        return {"calc_id": calc.id, "created": False, "changes": changes,
                "also_affects": _other_outputs(s, calc.id, ds.id)}
    values = {k: v for k, v in _normalize(section, values).items() if v is not None}
    if section == "dft":
        labels = values.pop("labels", None)
        calc = record_dft(s, [ds.id], inputs=inputs or [], dft=values, labels=labels)
    else:
        calc = record_md(s, [ds.id], inputs=inputs or [], model=model or None, md=values)
    return {"calc_id": calc.id, "created": True, "changes": values}


# --------------------------------------------------------------------------- nodes


def edit_node(s: Session, node_id: str, name: str | None = None,
              description: str | None = None, tags: list[str] | None = None,
              note: str | None = None, attrs: dict[str, Any] | None = None,
              label: str | None = None) -> dict[str, list]:
    """Change name/label/description, replace the tag list, add a note, set (value) or
    remove (None) metadata attributes. An empty label clears it. Returns {field: [old, new]}."""
    node = get_node(s, node_id)
    changes: dict[str, list] = {}
    if name is not None and name.strip() and name.strip() != node.name:
        changes["name"] = [node.name, name.strip()]
        node.name = name.strip()
    if label is not None and (label.strip() or None) != node.label:
        new = label.strip() or None
        if new and len(new) > LABEL_MAX:
            raise InvalidInput(f"label is {len(new)} characters; keep it under {LABEL_MAX}")
        changes["label"] = [node.label, new]
        node.label = new
    if description is not None and (description.strip() or None) != node.description:
        changes["description"] = [node.description, description.strip() or None]
        node.description = description.strip() or None
    if tags is not None:
        from .tags import resolve

        old = sorted(t.name for t in node.tags)
        want = sorted({(r.name if (r := resolve(s, t)) else t.strip()) for t in tags if t.strip()})
        if old != want:
            node.tags[:] = [t for t in node.tags if t.name in want]
            add_tags(s, node, want)
            changes["tags"] = [old, want]
    for k, v in (attrs or {}).items():
        k = k.strip()
        if not k:
            continue
        existing = next((a for a in node.attrs if a.key == k), None)
        if v is None or (isinstance(v, str) and not v.strip()):
            if existing is not None:
                changes[f"attr:{k}"] = [existing.value, None]
                node.attrs.remove(existing)
        elif existing is None:
            node.attrs.append(NodeAttr(key=k, value=v, source="user"))
            changes[f"attr:{k}"] = [None, v]
        elif existing.value != v:
            changes[f"attr:{k}"] = [existing.value, v]
            existing.value, existing.source = v, "user"
    if note and note.strip():
        from .annotate import add_note

        add_note(s, node.id, note.strip())
        changes["note"] = [None, note.strip()]
    if changes:
        AtomDB.audit(s, "edit.node", node.id, changes=changes)
    return changes


# --------------------------------------------------------------------------- copying


def copy_metadata(s: Session, source: str, targets: list[str],
                  parts: list[str] | tuple[str, ...] = COPY_PARTS) -> dict[str, Any]:
    """Apply the settings of one dataset to many others (same DFT/MD config).

    - ``dft``/``md``: the typed settings, the full code input (``calc_param``) and input
      *files* (e.g. a stored cp2k config) of the source's calculation; each target gets
      its own calculation (or its existing one is edited). Input *datasets* are not
      copied — they differ per file.
    - ``attrs``: metadata attributes (except file-specific ones such as source_directory)
    - ``tags``: added, existing tags are kept
    """
    src = get_dataset(s, source)
    bad = sorted(set(parts) - set(COPY_PARTS))
    if bad:
        raise InvalidInput(f"unknown part(s) {', '.join(bad)}; known: {', '.join(COPY_PARTS)}")
    src_calcs = dataset_calcs(s, src.id)
    for sec in ("dft", "md"):
        if sec in parts and len(src_calcs[sec]) > 1:
            raise InvalidInput(f"{src.id} has several {sec} calculations; cannot pick one to copy")
    results = []
    for t in targets:
        tgt = get_dataset(s, t)
        if tgt.id == src.id:
            continue
        r: dict[str, Any] = {"dataset": tgt.id}
        for sec in ("dft", "md"):
            if sec not in parts or not src_calcs[sec]:
                continue
            sc = src_calcs[sec][0]
            row = getattr(sc, sec)
            values = {k: getattr(row, k) for k in _columns(SECTIONS[sec])} if row else {}
            model = None
            if sec == "md":
                model = next((e.dst for e in _edges(s, src=sc.id, relation="driven_by")), "")
            res = set_dataset_settings(s, tgt.id, sec, values, model=model)
            calc = s.get(Calculation, res["calc_id"])
            add_params(calc, {p.key: p.value for p in sc.params}, source="copied")
            for e in _edges(s, src=sc.id, relation="used"):
                if s.get(Node, e.dst).kind == "file":
                    add_edge(s, calc.id, e.dst, "used", role=e.role, source="user")
            r[sec] = res["calc_id"]
            if res.get("also_affects"):
                r.setdefault("also_affects", []).extend(res["also_affects"])
        if "attrs" in parts:
            attrs = {a.key: a.value for a in src.attrs if a.key not in FILE_SPECIFIC_ATTRS}
            if attrs:
                edit_node(s, tgt.id, attrs=attrs)
                r["attrs"] = sorted(attrs)
        if "tags" in parts and src.tags:
            add_tags(s, tgt, [tg.name for tg in src.tags])
            r["tags"] = sorted(tg.name for tg in src.tags)
        results.append(r)
    AtomDB.audit(s, "edit.copy", src.id, targets=[r["dataset"] for r in results],
                 parts=list(parts))
    return {"source": src.id, "parts": list(parts), "results": results}

