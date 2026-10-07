"""Adding provenance after the fact: import shortcuts, metadata, links, merges."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db.models import Edge, FrameLink, Node, Note, PropertyDef
from ..errors import InvalidInput
from ..ids import normalize_id
from .context import AtomDB
from .datasets import get_node
from .provenance import RELATIONS, add_edge
from .records import record_dft, record_md, record_sampling, set_attrs

MD_KEYS = {"temperature": "temperature_k", "pressure": "pressure_bar", "ensemble": "ensemble",
           "timestep": "timestep_fs", "steps": "n_steps", "length": "length_ps",
           "thermostat": "thermostat", "barostat": "barostat", "engine": "engine",
           "integrator": "integrator", "seed": "seed"}
DFT_KEYS = {"code": "code", "code_version": "code_version", "functional": "functional",
            "dispersion": "dispersion", "basis": "basis", "pseudopotential": "pseudopotential",
            "cutoff": "cutoff_ry", "rel_cutoff": "rel_cutoff_ry", "eps_scf": "eps_scf"}


def apply_annotations(
    s: Session, ds_id: str, metadata: dict[str, Any], links: list[dict] | None = None,
    source: str = "user",
) -> list[str]:
    """Turn flat metadata (import flags, plan entries) into structured provenance.

    Recognised keys create records: MD keys (temperature, ensemble, model, ...) → an MD
    calculation that generated the dataset; DFT keys (functional, dispersion, code, ...) →
    a DFT calculation that labeled it; ``sampled_from`` → a sampling record; ``source`` →
    working directory. Remaining keys become node attributes. Returns created IDs.
    """
    md_meta = dict(metadata)
    created: list[str] = []
    workdir = md_meta.pop("source", None)
    model = md_meta.pop("model", None)
    sampled_from = md_meta.pop("sampled_from", None)
    sampling_method = md_meta.pop("sampling_method", None) or "unspecified"
    labeled_from = md_meta.pop("labeled_from", None)
    labels = md_meta.pop("labels", None)
    md = {MD_KEYS[k]: md_meta.pop(k) for k in list(md_meta) if k in MD_KEYS}
    dft = {DFT_KEYS[k]: md_meta.pop(k) for k in list(md_meta) if k in DFT_KEYS}
    if md or model:
        c = record_md(s, [ds_id], model=model, md=md, workdir=workdir, source=source)
        created.append(c.id)
    if dft:
        c = record_dft(s, [ds_id], inputs=[labeled_from] if labeled_from else [], dft=dft,
                       workdir=workdir if not (md or model) else None,
                       labels=labels.split(",") if isinstance(labels, str) else labels,
                       source=source)
        created.append(c.id)
    if sampled_from:
        c = record_sampling(s, sampled_from, ds_id, sampling_method, source=source)
        created.append(c.id)
    if workdir and not (md or model or dft):
        md_meta["source_directory"] = workdir
    if md_meta:
        set_attrs(s, get_node(s, ds_id), md_meta, source=source)
    for link in links or []:
        add_link(s, ds_id, link["relation"], link["target"], source=source)
    return created


def add_link(s: Session, src: str, relation: str, dst: str, role: str = "",
             source: str = "user") -> Edge:
    if relation not in RELATIONS:
        raise InvalidInput(f"unknown relation {relation!r}; known: {', '.join(RELATIONS)}")
    a, b = get_node(s, src), get_node(s, dst)
    if a.id == b.id:
        raise InvalidInput("a node cannot be linked to itself")
    e = add_edge(s, a.id, b.id, relation, role=role, source=source)
    if relation in ("sampled_from", "derived_from") and a.kind == b.kind == "dataset":
        from .lineage import link_frames

        e.attrs = {"frame_mapping": link_frames(s, a.id, b.id, None)}
    AtomDB.audit(s, "edge.add", a.id, relation=relation, target=b.id)
    return e


def add_note(s: Session, node_id: str, text: str) -> None:
    node = get_node(s, node_id)
    node.notes.append(Note(text=text))


def merge(s: Session, placeholder_id: str, real_id: str) -> dict:
    """Replace a placeholder by the real node: re-point all edges, keep an audit trail."""
    ph, real = get_node(s, placeholder_id), get_node(s, real_id)
    if ph.status != "placeholder":
        raise InvalidInput(f"{ph.id} is not a placeholder (status {ph.status})")
    if ph.kind != real.kind:
        raise InvalidInput(f"cannot merge {ph.kind} into {real.kind}")
    moved = 0
    for e in s.execute(select(Edge).where((Edge.src == ph.id) | (Edge.dst == ph.id))).scalars():
        new_src = real.id if e.src == ph.id else e.src
        new_dst = real.id if e.dst == ph.id else e.dst
        dup = s.execute(select(Edge).where(Edge.src == new_src, Edge.dst == new_dst,
                                           Edge.relation == e.relation,
                                           Edge.role == e.role)).scalar_one_or_none()
        if dup is not None or new_src == new_dst:
            s.delete(e)
        else:
            e.src, e.dst = new_src, new_dst
            moved += 1
    s.execute(update(PropertyDef).where(PropertyDef.label_calc_id == ph.id)
              .values(label_calc_id=real.id))
    s.execute(update(FrameLink).where(FrameLink.via_calc == ph.id).values(via_calc=real.id))
    ph.status = "merged"
    s.flush()
    add_edge(s, ph.id, real.id, "same_as", source="user")
    AtomDB.audit(s, "node.merge", ph.id, into=real.id, edges_moved=moved)
    return {"placeholder": ph.id, "merged_into": real.id, "edges_moved": moved}


def ensure_exists(s: Session, node_id: str) -> Node:
    node = s.get(Node, normalize_id(node_id))
    if node is None:
        raise InvalidInput(f"unknown node {node_id}")
    return node
