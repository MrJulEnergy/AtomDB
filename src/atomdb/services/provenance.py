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


# --------------------------------------------------------------------------- traversal
#
# "Upstream" answers WHERE DID THIS COME FROM?, "downstream" WHAT WAS THIS USED FOR?
# For every relation except `generated`, src depends on dst (src was made from / ran on
# / used dst), so upstream follows src → dst. `generated` points activity → output, so
# upstream of an output is its activity (dst → src).

UPSTREAM_REVERSED = {"generated"}
#: phrasing of relations when read downstream ("what was this used for?")
INVERSE = {
    "used": "input to", "derived_from": "derived into", "sampled_from": "sampled into",
    "labeled_by": "labeled", "trained_on": "trained", "driven_by": "drove", "ran_on": "ran",
    "executed_with": "executed", "converted_from": "converted to", "same_as": "same as",
    "generated": "generated",
}
NOT_TRAVERSED = {"part_of"}


def _neighbors(s: Session, ids: set[str], direction: str) -> list[Edge]:
    if not ids:
        return []
    ids_l = list(ids)
    out: list[Edge] = []
    for k in range(0, len(ids_l), 500):
        batch = ids_l[k : k + 500]
        if direction == "up":
            q = select(Edge).where(
                ((Edge.src.in_(batch)) & (Edge.relation.notin_(UPSTREAM_REVERSED | NOT_TRAVERSED)))
                | ((Edge.dst.in_(batch)) & (Edge.relation.in_(UPSTREAM_REVERSED)))
            )
        else:
            q = select(Edge).where(
                ((Edge.dst.in_(batch)) & (Edge.relation.notin_(UPSTREAM_REVERSED | NOT_TRAVERSED)))
                | ((Edge.src.in_(batch)) & (Edge.relation.in_(UPSTREAM_REVERSED)))
            )
        out.extend(s.execute(q).scalars())
    return out


def traverse(s: Session, root: str, direction: str = "up", depth: int = 50) -> dict:
    """Breadth-first walk of the provenance graph. Returns nodes, edges and levels."""
    if direction not in ("up", "down", "both"):
        raise ValueError("direction must be up, down or both")
    if direction == "both":
        up = traverse(s, root, "up", depth)
        down = traverse(s, root, "down", depth)
        nodes = {**up["levels"], **{k: -v for k, v in down["levels"].items()}}
        nodes[root] = 0
        edges = {(e["src"], e["dst"], e["relation"], e["role"]): e
                 for e in up["edges"] + down["edges"]}
        return {"root": root, "direction": "both", "levels": nodes, "edges": list(edges.values())}

    levels = {root: 0}
    frontier = {root}
    edges: dict[int, Edge] = {}
    for level in range(1, depth + 1):
        found = _neighbors(s, frontier, direction)
        nxt: set[str] = set()
        for e in found:
            if direction == "up":
                other = e.src if e.relation in UPSTREAM_REVERSED else e.dst
                here = e.dst if e.relation in UPSTREAM_REVERSED else e.src
            else:
                other = e.dst if e.relation in UPSTREAM_REVERSED else e.src
                here = e.src if e.relation in UPSTREAM_REVERSED else e.dst
            if here not in frontier:
                continue
            edges[e.id] = e
            if other not in levels:
                levels[other] = level
                nxt.add(other)
        if not nxt:
            break
        frontier = nxt
    return {
        "root": root,
        "direction": direction,
        "levels": levels,
        "edges": [
            {"src": e.src, "dst": e.dst, "relation": e.relation, "role": e.role,
             "source": e.source, "attrs": e.attrs}
            for e in edges.values()
        ],
    }


def provenance_graph(adb, s: Session, root: str, direction: str = "up", depth: int = 50) -> dict:
    """Traversal result enriched with node descriptions (for CLI/API/UI)."""
    from ..db.models import Node
    from .nodes import describe

    g = traverse(s, root, direction, depth)
    nodes = []
    for nid, level in sorted(g["levels"].items(), key=lambda kv: (abs(kv[1]), kv[0])):
        node = s.get(Node, nid)
        if node is None:
            nodes.append({"id": nid, "kind": "missing", "name": "(broken reference)",
                          "status": "missing", "subtitle": "", "level": level})
            continue
        nodes.append({**describe(adb, s, node, full=False), "level": level})
    return {"root": root, "direction": direction, "nodes": nodes, "edges": g["edges"]}


def render_tree(graph: dict) -> str:
    """Indented text tree rooted at ``graph['root']`` (upstream or downstream)."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    up = graph["direction"] == "up"
    children: dict[str, list[tuple[str, str]]] = {}
    for e in graph["edges"]:
        if up:
            parent, child = (e["dst"], e["src"]) if e["relation"] in UPSTREAM_REVERSED else (
                e["src"], e["dst"])
        else:
            parent, child = (e["src"], e["dst"]) if e["relation"] in UPSTREAM_REVERSED else (
                e["dst"], e["src"])
        rel = e["relation"] if up else INVERSE.get(e["relation"], e["relation"])
        label = rel + (f"[{e['role']}]" if e["role"] else "")
        children.setdefault(parent, []).append((child, label))
    lines: list[str] = []
    seen: set[str] = set()

    def fmt(nid: str) -> str:
        n = nodes.get(nid, {"kind": "?", "name": "?", "subtitle": ""})
        st = "" if n.get("status") in (None, "active") else f" [{n['status']}]"
        sub = f"  — {n['subtitle']}" if n.get("subtitle") else ""
        return f"{nid}  {n['name']}{st}{sub}"

    def walk(nid: str, prefix: str, label: str | None, last: bool) -> None:
        if label is None:
            lines.append(fmt(nid))
        else:
            branch = "└─" if last else "├─"
            again = "  (see above)" if nid in seen else ""
            lines.append(f"{prefix}{branch} {label} → {fmt(nid)}{again}")
        if nid in seen:
            return
        seen.add(nid)
        kids = sorted(children.get(nid, []), key=lambda c: c[0])
        ext = "" if label is None else ("   " if last else "│  ")
        for i, (k, lab) in enumerate(kids):
            walk(k, prefix + ext, lab, i == len(kids) - 1)

    walk(graph["root"], "", None, True)
    return "\n".join(lines)


def render_dot(graph: dict) -> str:
    out = ["digraph provenance {", "  rankdir=BT; node [shape=box, fontname=monospace];"]
    for n in graph["nodes"]:
        label = f"{n['id']}\\n{n['name']}\\n{n.get('subtitle', '')}".replace('"', "'")
        out.append(f'  "{n["id"]}" [label="{label}"];')
    for e in graph["edges"]:
        out.append(f'  "{e["src"]}" -> "{e["dst"]}" [label="{e["relation"]}"];')
    out.append("}")
    return "\n".join(out)
