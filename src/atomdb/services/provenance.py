"""Creating and traversing provenance relations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Edge

# relation vocabulary; direction is always src --relation--> dst
RELATIONS = {
    "used": "activity used an entity as input",
    "generated": "activity produced an entity",
    "derived_from": "entity was derived from another entity",
    "sampled_from": "dataset frames were selected from another dataset",
    "labeled_by": "dataset properties were computed by a calculation",
    "trained_on": "model was trained on a dataset",
    "driven_by": "simulation was driven by a model/potential",
    "ran_on": "calculation ran as an HPC job",
    "executed_with": "calculation used a software",
    "converted_from": "file was converted from another file",
    "part_of": "node belongs to a larger node",
    "same_as": "placeholder merged into a real node",
}


def add_edge(
    s: Session,
    src: str,
    dst: str,
    relation: str,
    role: str = "",
    source: str = "auto",
    attrs: dict | None = None,
) -> Edge:
    if relation not in RELATIONS:
        raise ValueError(f"unknown relation {relation!r}")
    existing = s.execute(
        select(Edge).where(
            Edge.src == src, Edge.dst == dst, Edge.relation == relation, Edge.role == role
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    e = Edge(src=src, dst=dst, relation=relation, role=role, source=source, attrs=attrs)
    s.add(e)
    return e
