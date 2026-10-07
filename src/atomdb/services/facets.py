"""Search facets: per-dataset facts aggregated over the upstream provenance graph.

Kept consistent automatically: an ``after_flush`` hook records which provenance-relevant
objects changed, and ``before_commit`` re-computes the facets of every dataset downstream
of them. ``rebuild_all`` recomputes everything (``atomdb reindex``).
"""

from __future__ import annotations

from sqlalchemy import delete, event, insert, select
from sqlalchemy.orm import Session

from .. import vocab
from ..db.models import (
    Calculation,
    Dataset,
    DatasetFacet,
    DFTParams,
    Edge,
    EnhancedSampling,
    HPCJob,
    MDParams,
    Model,
    Node,
    NodeAttr,
    NodeTag,
    SamplingOp,
)
from .provenance import traverse

TRACKED = (Edge, Calculation, MDParams, DFTParams, EnhancedSampling, SamplingOp, Model,
           NodeAttr, NodeTag, Dataset, HPCJob)


def compute_facets(s: Session, ds: Dataset) -> list[dict]:
    rows: list[tuple[str, str, float | None]] = []

    def add(key: str, value, num: float | None = None) -> None:
        if value is None or value == "":
            return
        rows.append((key, str(value)[:512], num))

    for t in ds.tags:
        add("tag", t.name)
    for a in ds.attrs:
        v = a.value
        add(f"attr:{a.key}", v, float(v) if isinstance(v, (int, float)) and not isinstance(
            v, bool) else None)
    full = traverse(s, ds.id, "up", 50)["levels"]
    own = traverse(s, ds.id, "up", 50, stop_at_models=True)["levels"]
    for nid in full:
        if nid == ds.id:
            continue
        add("ancestor", nid)
        node = s.get(Node, nid)
        if node is None:
            continue
        # plain keys: facts of this dataset's own generation (not walking past a model);
        # "lineage:<key>": facts anywhere upstream, incl. the model's training data
        for key, value, num in _node_facts(node):
            add("lineage:" + key, value, num)
            if nid in own:
                add(key, value, num)
    seen = set()
    out = []
    for k, v, n in rows:
        if (k, v) in seen:
            continue
        seen.add((k, v))
        out.append({"dataset_id": ds.id, "key": k, "value": v, "num": n})
    return out


def _node_facts(node: Node) -> list[tuple[str, object, float | None]]:
    out: list[tuple[str, object, float | None]] = []

    def add(key: str, value, num: float | None = None) -> None:
        if value is not None and value != "":
            out.append((key, value, num))

    if isinstance(node, Calculation):
        add("calc_type", node.calc_type)
        if node.dft is not None:
            d = node.dft
            add("functional", d.functional)
            add("functional_key", vocab.match_key(d.functional) if d.functional else None)
            add("dispersion", d.dispersion)
            add("code", d.code)
            add("code_version", d.code_version)
            add("basis", d.basis)
            add("pseudopotential", d.pseudopotential)
            if d.cutoff_ry is not None:
                add("cutoff", d.cutoff_ry, d.cutoff_ry)
        if node.md is not None:
            m = node.md
            if m.temperature_k is not None:
                add("temperature", f"{m.temperature_k:g}", m.temperature_k)
            if m.pressure_bar is not None:
                add("pressure", f"{m.pressure_bar:g}", m.pressure_bar)
            add("ensemble", m.ensemble)
            add("md_engine", m.engine)
            add("thermostat", m.thermostat)
        for es in node.enhanced_sampling:
            add("enhanced_sampling", es.method)
        if node.sampling is not None:
            add("sampling_method", node.sampling.method)
    elif isinstance(node, Model):
        add("model", node.name)
        add("model_key", vocab.match_key(node.name))
        add("model_family", node.family)
    elif isinstance(node, HPCJob):
        add("cluster", node.cluster)
        add("partition", node.partition)
        add("job_id", node.job_id)
    elif isinstance(node, Dataset):
        add("source_dataset", node.id)
    return out


def refresh(s: Session, ds_ids: set[str]) -> None:
    for ds_id in ds_ids:
        ds = s.get(Dataset, ds_id)
        s.execute(delete(DatasetFacet).where(DatasetFacet.dataset_id == ds_id))
        if ds is None:
            continue
        rows = compute_facets(s, ds)
        if rows:
            s.execute(insert(DatasetFacet), rows)


def rebuild_all(s: Session) -> int:
    ids = set(s.execute(select(Dataset.id)).scalars())
    refresh(s, ids)
    return len(ids)


def _touched_ids(obj) -> set[str]:
    if isinstance(obj, Edge):
        return {obj.src, obj.dst}
    if isinstance(obj, Node):
        return {obj.id}
    for attr in ("calc_id", "node_id"):
        v = getattr(obj, attr, None)
        if v:
            return {v}
    return set()


def install(session_cls) -> None:
    @event.listens_for(session_cls, "after_flush")
    def _collect(session, _ctx):
        touched = session.info.setdefault("facet_touched", set())
        for obj in (*session.new, *session.dirty, *session.deleted):
            if isinstance(obj, TRACKED):
                touched |= _touched_ids(obj)

    @event.listens_for(session_cls, "before_commit")
    def _refresh(session):
        touched = session.info.pop("facet_touched", set())
        if not touched:
            return
        session.flush()
        touched |= session.info.pop("facet_touched", set())
        affected: set[str] = set()
        for nid in touched:
            if nid is None:
                continue
            g = traverse(session, nid, "down", 50)
            for x in g["levels"]:
                if x.startswith("DS-"):
                    affected.add(x)
        if affected:
            refresh(session, affected)
            session.flush()
        session.info.pop("facet_touched", None)
