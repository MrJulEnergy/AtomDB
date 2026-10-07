"""Frame-level lineage: which parent frame did a child frame come from?"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from ..db.models import Frame, FrameLink


def link_frames(
    s: Session,
    child: str,
    parent: str,
    via_calc: str | None,
    frame_map: list[tuple[int, int]] | None = None,
) -> dict:
    """Store child→parent frame links, given explicitly or matched by fingerprint.

    Fingerprint matching only links unambiguous matches (one parent frame per key).
    """
    s.execute(delete(FrameLink).where(FrameLink.child_dataset == child,
                                      FrameLink.parent_dataset == parent))
    if frame_map is not None:
        rows = [{"child_dataset": child, "child_idx": c, "parent_dataset": parent,
                 "parent_idx": p, "via_calc": via_calc} for c, p in frame_map]
        if rows:
            s.execute(insert(FrameLink), rows)
        return {"method": "explicit", "linked": len(rows)}

    parent_fp: dict[str, list[int]] = defaultdict(list)
    for fp, idx in s.execute(
        select(Frame.fingerprint, Frame.idx).where(Frame.dataset_id == parent)
    ):
        parent_fp[fp].append(idx)
    rows, unmatched, ambiguous = [], 0, 0
    for fp, idx in s.execute(
        select(Frame.fingerprint, Frame.idx).where(Frame.dataset_id == child)
    ):
        cands = parent_fp.get(fp, [])
        if len(cands) == 1:
            rows.append({"child_dataset": child, "child_idx": idx, "parent_dataset": parent,
                         "parent_idx": cands[0], "via_calc": via_calc})
        elif cands:
            ambiguous += 1
        else:
            unmatched += 1
    for k in range(0, len(rows), 5000):
        s.execute(insert(FrameLink), rows[k : k + 5000])
    return {"method": "fingerprint", "linked": len(rows), "unmatched": unmatched,
            "ambiguous": ambiguous}


def frame_parents(s: Session, ds_id: str, idx: int) -> list[FrameLink]:
    return list(s.execute(
        select(FrameLink).where(FrameLink.child_dataset == ds_id, FrameLink.child_idx == idx)
    ).scalars())


def frame_children(s: Session, ds_id: str, idx: int) -> list[FrameLink]:
    return list(s.execute(
        select(FrameLink).where(FrameLink.parent_dataset == ds_id, FrameLink.parent_idx == idx)
    ).scalars())


def frame_lineage(s: Session, ds_id: str, idx: int, max_depth: int = 50) -> list[dict]:
    """Chain of (dataset, frame) going upstream through frame links."""
    chain = [{"dataset": ds_id, "frame": idx, "via": None}]
    seen = {(ds_id, idx)}
    cur = (ds_id, idx)
    for _ in range(max_depth):
        parents = frame_parents(s, *cur)
        if not parents:
            break
        p = parents[0]
        nxt = (p.parent_dataset, p.parent_idx)
        if nxt in seen:
            break
        seen.add(nxt)
        chain.append({"dataset": nxt[0], "frame": nxt[1], "via": p.via_calc,
                      "alternatives": [(q.parent_dataset, q.parent_idx) for q in parents[1:]]})
        cur = nxt
    return chain
