"""How complete is a dataset's provenance, and which relations does the data suggest?"""

from __future__ import annotations

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from ..db.models import Calculation, Dataset, Edge, Frame, Model, Node
from .provenance import traverse


def _upstream_nodes(s: Session, ds_id: str, depth: int = 12) -> list[Node]:
    g = traverse(s, ds_id, "up", depth)
    return [n for nid in g["levels"] if nid != ds_id and (n := s.get(Node, nid)) is not None]


def completeness(s: Session, ds: Dataset) -> dict:
    """Checklist of provenance a dataset should have. Missing items are actionable."""
    ups = _upstream_nodes(s, ds.id)
    calcs = [n for n in ups if isinstance(n, Calculation)]
    science = [c for c in calcs if c.calc_type not in ("import", "conversion")]
    direct = s.execute(select(Edge).where(
        ((Edge.dst == ds.id) & (Edge.relation == "generated"))
        | ((Edge.src == ds.id) & Edge.relation.in_(("sampled_from", "derived_from", "labeled_by")))
    )).scalars().all()
    checks: list[dict] = []

    def check(key: str, ok: bool, hint: str) -> None:
        checks.append({"item": key, "ok": ok, "hint": None if ok else hint})

    origin_ok = any(e.relation in ("sampled_from", "derived_from", "labeled_by") for e in direct) \
        or any(c.calc_type not in ("import", "conversion") for c in science)
    check("origin", origin_ok,
          "how was this made? `atomdb annotate ID --set temperature=… --set model=…`, "
          "`record md/dft`, or `relate ID sampled_from DS`")
    if ds.has_energy or ds.has_forces:
        dfts = [c for c in science if c.dft is not None]
        check("label method", bool(dfts),
              "which calculation produced energies/forces? `atomdb record dft --output ID ...`")
        for c in dfts[:1]:
            d = c.dft
            check("functional", bool(d.functional), f"{c.id}: functional unknown")
            check("dispersion", d.dispersion is not None,
                  f"{c.id}: dispersion unknown (use 'none' if none)")
            check("basis / pseudopotential", bool(d.basis or d.pseudopotential or d.kpoints),
                  f"{c.id}: basis/pseudopotential unknown")
            check("code version", bool(d.code_version), f"{c.id}: code version unknown")
    mds = [c for c in science if c.md is not None]
    for c in mds[:1]:
        check("md temperature", c.md.temperature_k is not None or c.md.ensemble == "NVE",
              f"{c.id}: temperature unknown")
        drivers = s.execute(select(Edge).where(Edge.src == c.id, Edge.relation == "driven_by")
                            ).scalars().all()
        is_aimd = c.dft is not None
        check("md potential", bool(drivers) or is_aimd,
              f"{c.id}: which model drove the MD? `atomdb relate {c.id} driven_by MDL-…`")
        for e in drivers:
            m = s.get(Model, e.dst)
            if m is not None and m.status == "placeholder":
                check("model details", False,
                      f"{m.id} ({m.name}) is a placeholder: `atomdb model add --name '{m.name}' …`")
    hpc = any(n.kind == "hpc_job" for n in ups)
    if science:
        check("hpc job", hpc or any(c.workdir for c in science),
              "where did it run? `atomdb import-run DIR` or record with --workdir/--job")
    missing = [c for c in checks if not c["ok"]]
    return {"dataset": ds.id, "complete": not missing, "score": (
        (len(checks) - len(missing)) / len(checks) if checks else 1.0),
        "checks": checks, "missing": [m["item"] for m in missing]}


def _first_frame_match(s: Session, single: str, traj: str) -> bool:
    fp = s.execute(select(Frame.fingerprint).where(Frame.dataset_id == single, Frame.idx == 0)
                   ).scalar_one_or_none()
    fp0 = s.execute(select(Frame.fingerprint).where(Frame.dataset_id == traj, Frame.idx == 0)
                    ).scalar_one_or_none()
    return fp is not None and fp == fp0


def suggest_relations(s: Session, ds_id: str | None = None, min_shared: int = 1) -> list[dict]:
    """Datasets sharing structures that are not yet connected in the provenance graph."""
    a, b = aliased(Frame), aliased(Frame)
    q = (select(a.dataset_id, b.dataset_id, func.count(func.distinct(a.idx)))
         .join(b, and_(a.fingerprint == b.fingerprint, a.dataset_id != b.dataset_id))
         .group_by(a.dataset_id, b.dataset_id))
    if ds_id:
        q = q.where(a.dataset_id == ds_id)
    out = []
    for child_id, parent_id, shared in s.execute(q):
        if shared < min_shared:
            continue
        child, parent = s.get(Dataset, child_id), s.get(Dataset, parent_id)
        if child is None or parent is None:
            continue
        frac_child = shared / (child.n_frames or 1)
        # a subset of the other dataset → "child sampled_from parent"
        if frac_child < 0.999 or (child.n_frames or 0) > (parent.n_frames or 0):
            continue
        if child.n_frames == parent.n_frames and child_id > parent_id:
            continue  # identical content: report once, older dataset as parent
        connected = parent_id in traverse(s, child_id, "up", 12)["levels"] or \
            child_id in traverse(s, parent_id, "up", 12)["levels"]
        if connected:
            continue
        rel = "derived_from" if child.n_frames == parent.n_frames else "sampled_from"
        if child.n_frames == 1 and (parent.n_frames or 0) > 1 and _first_frame_match(
                s, child_id, parent_id):
            # a single structure equal to frame 0 of a trajectory is its starting point:
            # the trajectory was derived from it, not the other way round
            out.append({
                "child": parent_id, "parent": child_id, "relation": "derived_from",
                "shared_frames": shared, "child_frames": parent.n_frames,
                "parent_frames": child.n_frames,
                "reason": f"frame 0 of {parent_id} equals {child_id}: probably its starting "
                          "structure",
                "command": f"atomdb relate {parent_id} derived_from {child_id} "
                           "--role initial_structure",
            })
            continue
        out.append({
            "child": child_id, "parent": parent_id, "relation": rel, "shared_frames": shared,
            "child_frames": child.n_frames, "parent_frames": parent.n_frames,
            "reason": f"all {child.n_frames} frames of {child_id} occur in {parent_id}"
                      + (" (identical content)" if rel == "derived_from" else ""),
            "command": f"atomdb relate {child_id} {rel} {parent_id}",
        })
    out.sort(key=lambda x: (x["child"], x["parent"]))
    return out
