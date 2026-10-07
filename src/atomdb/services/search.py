"""Search language (shared by CLI, API and UI).

    r2scan                         free text: names, descriptions, tags, any facet value
    r2scan+d4                      functional AND dispersion
    400K   T:400   T:300..400   T>350
    functional:r2scan  disp:D4  code:cp2k  basis:TZV2P  pseudo:GTH
    model:MACE-MH-1  family:MACE
    el:K,Cl   (contains all)   el:K|Na  (contains any)   only:K,Cl,O,H  (exactly these)
    es:any  es:metadynamics       enhanced sampling
    from:MD-2026-000017           anything derived from a node (any depth)
    lineage:r2scan                anywhere upstream, including earlier generations (e.g. the
                                  training data of the model that generated a trajectory)

Keyed terms and free text refer to a dataset's own generation: an MD trajectory generated
by a model trained on r2SCAN data does not match ``functional:r2scan`` (it matches
``lineage:r2scan``).
    frames>100  atoms<500  has:forces  has:stress  tag:production  name:kcl
    cluster:hawk  ensemble:NVT  sampling:uncertainty  status:placeholder
    <anything>:<value>            dataset metadata set with --set / annotate

Terms are combined with AND. Values may be quoted: model:"MACE MH 1".
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from .. import vocab
from ..db.models import Dataset, DatasetElement, DatasetFacet, Frame, NodeTag, Tag
from ..errors import InvalidInput
from ..frames import mask_for_symbols
from ..ids import normalize_id

TERM = re.compile(r"^(?P<key>[A-Za-z_][\w.:\-]*?)(?P<op>>=|<=|!=|:|=|>|<)(?P<val>.+)$")
TEMP_TEXT = re.compile(r"^(?P<num>\d+(?:\.\d+)?)\s*K$", re.IGNORECASE)
ALIASES = {
    "functional": "functional", "xc": "functional", "func": "functional",
    "dispersion": "dispersion", "disp": "dispersion", "vdw": "dispersion",
    "code": "code", "calculator": "code", "basis": "basis", "pseudo": "pseudopotential",
    "pseudopotential": "pseudopotential", "model": "model", "mlip": "model",
    "potential": "model", "family": "model_family", "t": "temperature", "temp": "temperature",
    "temperature": "temperature", "p": "pressure", "pressure": "pressure",
    "ensemble": "ensemble", "es": "enhanced_sampling", "enhanced": "enhanced_sampling",
    "enhanced_sampling": "enhanced_sampling", "sampling": "sampling_method",
    "cluster": "cluster", "partition": "partition", "job": "job_id", "from": "ancestor",
    "derived": "ancestor", "ancestor": "ancestor", "tag": "tag", "el": "elements",
    "element": "elements", "elements": "elements", "contains": "elements", "only": "only",
    "frames": "frames", "atoms": "atoms", "has": "has", "name": "name", "id": "id",
    "status": "status", "engine": "md_engine", "thermostat": "thermostat",
    "cutoff": "cutoff", "calc": "calc_type", "version": "code_version", "lineage": "lineage",
}
NUMERIC_FACETS = {"temperature", "pressure", "cutoff"}
CONTAINS_FACETS = {"basis", "pseudopotential", "code_version"}


@dataclass
class Query:
    text: list[str] = field(default_factory=list)
    terms: list[tuple[str, str, str]] = field(default_factory=list)  # (key, op, value)
    elements_all: list[str] = field(default_factory=list)
    elements_any: list[str] = field(default_factory=list)
    elements_only: list[str] = field(default_factory=list)

    def describe(self) -> list[str]:
        out = [f"text ~ {t!r}" for t in self.text]
        out += [f"{k} {op} {v}" for k, op, v in self.terms]
        if self.elements_all:
            out.append("contains all of " + ",".join(self.elements_all))
        if self.elements_any:
            out.append("contains any of " + "|".join(self.elements_any))
        if self.elements_only:
            out.append("exactly elements " + ",".join(self.elements_only))
        return out


def _symbols(text: str) -> list[str]:
    from ase.data import atomic_numbers

    syms = [x.strip() for x in re.split(r"[,+\s&]|and", text) if x.strip()]
    syms = [s[:1].upper() + s[1:].lower() for s in syms]
    bad = [s for s in syms if s not in atomic_numbers]
    if bad:
        raise InvalidInput(f"unknown element(s): {', '.join(bad)}")
    return syms


def parse(q: str, elements: list[str] | None = None, any_elements: bool = False) -> Query:
    out = Query()
    try:
        tokens = shlex.split(q or "")
    except ValueError as e:
        raise InvalidInput(f"cannot parse query: {e}") from e
    # join "400 K"
    merged: list[str] = []
    for tok in tokens:
        if tok.upper() == "K" and merged and re.fullmatch(r"\d+(\.\d+)?", merged[-1]):
            merged[-1] += "K"
        else:
            merged.append(tok)
    from ase.data import atomic_numbers

    collecting = False
    for tok in merged:
        low = tok.lower()
        if low in ("contains", "containing", "with") and tok == low:
            collecting = True
            continue
        if collecting:
            parts = [p for p in re.split(r"[,+]", tok) if p]
            if low in ("and", "+", "&", ","):
                continue
            if parts and all(p[:1].upper() + p[1:].lower() in atomic_numbers for p in parts):
                out.elements_all += _symbols(tok)
                continue
            collecting = False
        m = TERM.match(tok)
        if m and m.group("key").lower() in ALIASES or (m and m.group("key").startswith("attr.")):
            key = m.group("key").lower()
            key = ALIASES.get(key, key)
            op, val = m.group("op"), m.group("val")
            if op == "=":
                op = ":"
            if key == "elements":
                if "|" in val:
                    out.elements_any += _symbols(val.replace("|", ","))
                else:
                    out.elements_all += _symbols(val)
            elif key == "only":
                out.elements_only += _symbols(val)
            else:
                out.terms.append((key, op, val))
            continue
        tm = TEMP_TEXT.match(tok)
        if tm:
            out.terms.append(("temperature", ":", tm.group("num")))
            continue
        if "+" in tok and not tok.startswith("+"):
            a, b = tok.split("+", 1)
            if vocab.dispersion(b) in vocab.DISPERSION.values():
                out.terms.append(("functional", ":", a))
                out.terms.append(("dispersion", ":", b))
                continue
        if m and ":" in tok and m.group("op") == ":":
            # unknown key → dataset metadata (attr)
            out.terms.append((f"attr.{m.group('key')}", ":", m.group("val")))
            continue
        out.text.append(tok)
    if elements:
        (out.elements_any if any_elements else out.elements_all).extend(_symbols(",".join(elements)))
    return out


def _num_cond(col, op: str, val: str):
    val = val.strip().rstrip("Kk").strip()
    if ".." in val:
        lo, hi = val.split("..", 1)
        conds = []
        if lo:
            conds.append(col >= float(lo))
        if hi:
            conds.append(col <= float(hi))
        return and_(*conds)
    try:
        x = float(val)
    except ValueError:
        raise InvalidInput(f"expected a number, got {val!r}") from None
    return {":": and_(col >= x - 0.5, col <= x + 0.5), ">": col > x, "<": col < x,
            ">=": col >= x, "<=": col <= x, "!=": or_(col < x - 0.5, col > x + 0.5)}[op]


def _facet(key: str, cond) -> object:
    sub = select(DatasetFacet.dataset_id).where(DatasetFacet.key == key, cond)
    return Dataset.id.in_(sub)


def _term_condition(key: str, op: str, val: str):
    neg = op == "!="
    if key in NUMERIC_FACETS:
        return _facet(key, _num_cond(DatasetFacet.num, op, val))
    if key in ("frames", "atoms"):
        col = Dataset.n_frames if key == "frames" else Dataset.n_atoms_max
        if op == ":" and ".." not in val:
            return col == int(val)
        return _num_cond(col, op, val)
    if key == "has":
        cols = {"energy": Dataset.has_energy, "energies": Dataset.has_energy,
                "forces": Dataset.has_forces, "force": Dataset.has_forces,
                "stress": Dataset.has_stress}
        if val.lower() not in cols:
            raise InvalidInput("has: expects energy, forces or stress")
        return cols[val.lower()].is_(True)
    if key == "name":
        return func.lower(Dataset.name).contains(val.lower())
    if key == "id":
        return Dataset.id.startswith(normalize_id(val) if val.count("-") == 2 else val.upper())
    if key == "status":
        return Dataset.status == val
    if key == "tag":
        return Dataset.id.in_(select(NodeTag.node_id).join(Tag).where(Tag.name == val))
    if key == "ancestor":
        return _facet("ancestor", DatasetFacet.value == normalize_id(val))
    if key == "lineage":
        like = val.lower()
        sub = select(DatasetFacet.dataset_id).where(
            DatasetFacet.key.startswith("lineage:"),
            or_(func.lower(DatasetFacet.value).contains(like),
                and_(DatasetFacet.key == "lineage:functional_key",
                     DatasetFacet.value == vocab.match_key(vocab.functional(val)))))
        return Dataset.id.in_(sub)
    if key == "functional":
        target = vocab.match_key(vocab.functional(val))
        cond = _facet("functional_key", DatasetFacet.value == target)
    elif key == "dispersion":
        cond = _facet("dispersion", func.lower(DatasetFacet.value) ==
                      (vocab.dispersion(val) or val).lower())
    elif key == "model":
        target = vocab.match_key(val)
        cond = or_(_facet("model_key", DatasetFacet.value.contains(target)),
                   _facet("ancestor", DatasetFacet.value == normalize_id(val)))
    elif key == "enhanced_sampling":
        if val.lower() in ("any", "yes", "true", "*"):
            cond = _facet("enhanced_sampling", DatasetFacet.value != "")
        else:
            cond = _facet("enhanced_sampling", func.lower(DatasetFacet.value) ==
                          (vocab.es_method(val) or val).lower())
    elif key in CONTAINS_FACETS:
        cond = _facet(key, func.lower(DatasetFacet.value).contains(val.lower()))
    elif key.startswith("attr."):
        akey = "attr:" + key[5:]
        try:
            float(val.rstrip("Kk").split("..")[0] or "x")
            cond = or_(_facet(akey, _num_cond(DatasetFacet.num, op if op != "!=" else ":", val)),
                       _facet(akey, func.lower(DatasetFacet.value) == val.lower()))
        except (ValueError, InvalidInput):
            cond = _facet(akey, func.lower(DatasetFacet.value) == val.lower())
    else:
        cond = _facet(key, func.lower(DatasetFacet.value) == val.lower())
    return ~cond if neg else cond


def build(query: Query) -> Select:
    stmt = select(Dataset.id)
    conds = []
    for t in query.text:
        like = t.lower()
        conds.append(or_(
            func.lower(Dataset.name).contains(like),
            func.lower(func.coalesce(Dataset.description, "")).contains(like),
            Dataset.id.in_(select(DatasetFacet.dataset_id).where(
                ~DatasetFacet.key.startswith("lineage:"), DatasetFacet.key != "ancestor",
                func.lower(DatasetFacet.value).contains(like))),
            Dataset.id == normalize_id(t),
        ))
    for key, op, val in query.terms:
        conds.append(_term_condition(key, op, val))
    for el in query.elements_all:
        conds.append(Dataset.id.in_(select(DatasetElement.dataset_id)
                                    .where(DatasetElement.symbol == el)))
    if query.elements_any:
        conds.append(Dataset.id.in_(select(DatasetElement.dataset_id)
                                    .where(DatasetElement.symbol.in_(query.elements_any))))
    if query.elements_only:
        for el in query.elements_only:
            conds.append(Dataset.id.in_(select(DatasetElement.dataset_id)
                                        .where(DatasetElement.symbol == el)))
        conds.append(~exists(select(DatasetElement.dataset_id).where(
            DatasetElement.dataset_id == Dataset.id,
            DatasetElement.symbol.notin_(query.elements_only))))
    if conds:
        stmt = stmt.where(and_(*conds))
    return stmt


def facets_for(s: Session, ds_ids: list[str]) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {i: {} for i in ds_ids}
    keys = ("functional", "dispersion", "code", "model", "temperature", "pressure", "ensemble",
            "enhanced_sampling", "cluster", "source_dataset", "basis")
    for ds_id, key, value in s.execute(
        select(DatasetFacet.dataset_id, DatasetFacet.key, DatasetFacet.value)
        .where(DatasetFacet.dataset_id.in_(ds_ids), DatasetFacet.key.in_(keys))
    ):
        out[ds_id].setdefault(key, []).append(value)
    return out


def search(s: Session, q: str, elements: list[str] | None = None, any_elements: bool = False,
           offset: int = 0, limit: int = 50, sort: str = "-created") -> dict:
    from .datasets import summary

    query = parse(q, elements, any_elements)
    ids_stmt = build(query)
    total = s.execute(select(func.count()).select_from(ids_stmt.subquery())).scalar_one()
    order = {"-created": Dataset.created_at.desc(), "created": Dataset.created_at.asc(),
             "name": Dataset.name.asc(), "-frames": Dataset.n_frames.desc(),
             "frames": Dataset.n_frames.asc(), "id": Dataset.id.asc()}.get(sort)
    if order is None:
        raise InvalidInput(f"unknown sort {sort!r}")
    ids = list(s.execute(ids_stmt.order_by(order).offset(offset).limit(limit)).scalars())
    facets = facets_for(s, ids)
    items = [summary(s, s.get(Dataset, i), extra=facets[i]) for i in ids]
    return {"query": q, "interpreted": query.describe(), "total": total, "offset": offset,
            "limit": limit, "items": items}


def search_frames(
    s: Session, all_elements: list[str] | None = None, any_elements: list[str] | None = None,
    dataset_ids: list[str] | None = None, offset: int = 0, limit: int = 100,
    energy_per_atom: str | None = None, fmax: str | None = None,
) -> dict:
    """Frames containing element combinations (uses the per-frame element bitmask)."""
    conds = []
    if all_elements:
        lo, hi = mask_for_symbols(all_elements)
        conds.append(Frame.elem_mask_lo.op("&")(lo) == lo)
        conds.append(Frame.elem_mask_hi.op("&")(hi) == hi)
    if any_elements:
        lo, hi = mask_for_symbols(any_elements)
        conds.append(or_(Frame.elem_mask_lo.op("&")(lo) != 0, Frame.elem_mask_hi.op("&")(hi) != 0))
    if dataset_ids:
        conds.append(Frame.dataset_id.in_([normalize_id(i) for i in dataset_ids]))
    if energy_per_atom:
        conds.append(_num_cond(Frame.energy_per_atom, ":" if ".." in energy_per_atom else
                               energy_per_atom[:1], energy_per_atom.lstrip("<>=")))
    if fmax:
        conds.append(_num_cond(Frame.fmax, ":" if ".." in fmax else fmax[:1], fmax.lstrip("<>=")))
    stmt = select(Frame).where(and_(*conds)) if conds else select(Frame)
    total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    per_ds = dict(s.execute(
        select(Frame.dataset_id, func.count()).where(and_(*conds)).group_by(Frame.dataset_id)
        if conds else select(Frame.dataset_id, func.count()).group_by(Frame.dataset_id)
    ).all())
    rows = s.execute(stmt.order_by(Frame.dataset_id, Frame.idx).offset(offset).limit(limit)
                     ).scalars()
    items = [{"dataset": f.dataset_id, "frame": f.idx, "n_atoms": f.n_atoms,
              "formula": f.formula, "energy": f.energy, "energy_per_atom": f.energy_per_atom,
              "fmax": f.fmax, "temperature": f.temperature} for f in rows]
    return {"total": total, "per_dataset": per_ds, "offset": offset, "limit": limit,
            "items": items}
