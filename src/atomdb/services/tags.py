"""Tag registry: kinds, aliases, merging, and the redundant-tag cleanup.

Tags are labels, not facts. Their ``kind`` decides how they are shown:

* ``system``  — what is simulated (water, KCl, peptide): system chip + sidebar group
* ``project`` — campaign / purpose (kcl_explore, training-set): project chip + group
* ``quality`` — warnings (unreliable, force-outliers): red chip + group
* ``free``    — anything else: searchable with ``tag:``, no chip

Facts (geometry, code, functional, model, …) come from the frames and provenance;
``cleanup`` finds tags that only repeat them.
"""

from __future__ import annotations

import re

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import vocab
from ..db.models import Dataset, DatasetFacet, NodeTag, Tag
from ..errors import InvalidInput, NotFound
from .context import AtomDB

KINDS = ("system", "project", "quality", "free")
#: kind of a tag when it is first created (same list as the tag_kinds migration seed);
#: change it afterwards on the Tags page or with ``atomdb tags set``
DEFAULT_KINDS = {
    **dict.fromkeys(["water", "kcl", "nacl", "peptide", "protein", "materials", "pore",
                     "monomer", "dimer", "solvated", "ice", "salt", "electrolyte",
                     "interface"], "system"),
    **dict.fromkeys(["active-learning", "training-set", "validation-set", "test-set", "fps",
                     "distillation", "benchmark"], "project"),
    **dict.fromkeys(["unreliable", "stale-stress", "force-outliers", "mislabelled-name",
                     "duplicate-labels", "partial", "broken"], "quality"),
}


def _get(s: Session, name: str) -> Tag:
    t = s.execute(select(Tag).where(func.lower(Tag.name) == name.strip().lower())
                  .order_by(Tag.alias_of.is_not(None))).scalars().first()
    if t is None:
        raise NotFound(f"no tag {name!r}")
    return t


def resolve(s: Session, name: str) -> Tag | None:
    """The canonical tag for ``name`` (case-insensitive, following aliases), or None."""
    name = name.strip()
    t = s.execute(select(Tag).where(Tag.name == name)).scalar_one_or_none()
    if t is None:
        t = s.execute(select(Tag).where(func.lower(Tag.name) == name.lower())
                      .order_by(Tag.alias_of.is_not(None))).scalars().first()
    seen = set()
    while t is not None and t.alias_of is not None and t.id not in seen:
        seen.add(t.id)
        t = s.get(Tag, t.alias_of)
    return t


def get_or_create(s: Session, name: str) -> Tag:
    t = resolve(s, name)
    if t is None:
        t = Tag(name=name.strip(), kind=DEFAULT_KINDS.get(name.strip().lower(), "free"))
        s.add(t)
        s.flush()
    return t


def kinds_by_name(s: Session) -> dict[str, str]:
    return dict(s.execute(select(Tag.name, Tag.kind).where(Tag.alias_of.is_(None))).all())


def list_tags(s: Session) -> list[dict]:
    counts = dict(s.execute(select(NodeTag.tag_id, func.count()).group_by(NodeTag.tag_id)).all())
    tags = s.execute(select(Tag).order_by(Tag.name)).scalars().all()
    aliases: dict[int, list[str]] = {}
    for t in tags:
        if t.alias_of is not None:
            aliases.setdefault(t.alias_of, []).append(t.name)
    return [{"name": t.name, "kind": t.kind, "description": t.description,
             "aliases": sorted(aliases.get(t.id, [])), "count": counts.get(t.id, 0)}
            for t in tags if t.alias_of is None]


def _refresh_tagged(s: Session, tag_ids: list[int]) -> None:
    from .facets import refresh

    s.flush()
    ids = set(s.execute(select(NodeTag.node_id).where(NodeTag.tag_id.in_(tag_ids))).scalars())
    refresh(s, {i for i in ids if s.get(Dataset, i) is not None})


def set_tag(s: Session, name: str, kind: str | None = None, description: str | None = None,
            rename: str | None = None) -> dict:
    t = _get(s, name)
    if t.alias_of is not None:
        raise InvalidInput(f"{t.name!r} is an alias of {s.get(Tag, t.alias_of).name!r}; edit that")
    changes = {}
    if kind is not None and kind != t.kind:
        if kind not in KINDS:
            raise InvalidInput(f"kind must be one of {', '.join(KINDS)}")
        changes["kind"] = [t.kind, kind]
        t.kind = kind
    if description is not None and (description.strip() or None) != t.description:
        changes["description"] = [t.description, description.strip() or None]
        t.description = description.strip() or None
    if rename is not None and rename.strip() and rename.strip() != t.name:
        other = s.execute(select(Tag).where(Tag.name == rename.strip())).scalar_one_or_none()
        if other is not None:
            raise InvalidInput(f"tag {rename!r} exists; merge instead")
        changes["name"] = [t.name, rename.strip()]
        t.name = rename.strip()
    if changes:
        AtomDB.audit(s, "tag.edit", None, tag=t.name, changes=changes)
        _refresh_tagged(s, [t.id])
    return {"name": t.name, "kind": t.kind, "description": t.description, "changes": changes}


def merge(s: Session, alias: str, into: str) -> dict:
    """Move every use of ``alias`` to ``into``; ``alias`` stays as a redirect."""
    a, c = _get(s, alias), resolve(s, into)
    if c is None:
        raise NotFound(f"no tag {into!r}")
    if a.id == c.id:
        raise InvalidInput("a tag cannot be merged into itself")
    nodes = set(s.execute(select(NodeTag.node_id).where(NodeTag.tag_id == a.id)).scalars())
    have = set(s.execute(select(NodeTag.node_id).where(NodeTag.tag_id == c.id)).scalars())
    s.execute(delete(NodeTag).where(NodeTag.tag_id == a.id))
    for n in nodes - have:
        s.add(NodeTag(node_id=n, tag_id=c.id))
    # earlier aliases of ``a`` now point at ``c`` too
    for t in s.execute(select(Tag).where(Tag.alias_of == a.id)).scalars():
        t.alias_of = c.id
    a.alias_of, a.kind = c.id, c.kind
    AtomDB.audit(s, "tag.merge", None, alias=a.name, into=c.name, nodes=sorted(nodes))
    s.flush()
    s.expire_all()  # node.tags collections were changed behind the ORM's back
    _refresh_tagged(s, [c.id])
    return {"alias": a.name, "into": c.name, "moved": len(nodes - have)}


def delete_tag(s: Session, name: str) -> dict:
    t = _get(s, name)
    n = s.execute(select(func.count()).select_from(NodeTag).where(NodeTag.tag_id == t.id)
                  ).scalar_one()
    if n:
        raise InvalidInput(f"{t.name!r} is used by {n} nodes; remove it there or merge it")
    for a in s.execute(select(Tag).where(Tag.alias_of == t.id)).scalars():
        s.delete(a)
    s.delete(t)
    AtomDB.audit(s, "tag.delete", None, tag=name)
    return {"deleted": name}


# --------------------------------------------------------------------------- cleanup

GEOMETRY_TAGS = {"pbc": "pbc", "periodic": "pbc", "bulk": "pbc", "cluster": "no-pbc",
                 "vacuum": "no-pbc", "gas-phase": "no-pbc"}
#: facets (own generation and upstream) that a fact-like tag can repeat
FACT_KEYS = ("code", "functional", "dispersion", "model", "model_family", "md_engine",
             "solvation", "dft_code", "mlip_family")


#: tag words that name a code, method, model or engine (reported if nothing matches)
FACT_WORDS = ({k.replace("-", "").replace(".", "") for k in vocab.CODES} | set(vocab.FUNCTIONALS)
              | {"revpbe0", "mp2", "rimp2", "ccsdt", "mace", "petmad", "nequip", "allegro", "apax",
                 "so3krates", "gromacs", "lammps", "openmm", "amber", "cpcm", "smd"})


def _norm(x: str) -> str:
    return re.sub(r"[^a-z0-9]", "", vocab.match_key(x) or x.lower())


def _facts(s: Session, ds: Dataset) -> dict[str, set[str]]:
    f: dict[str, set[str]] = {}
    for k, v in s.execute(select(DatasetFacet.key, DatasetFacet.value)
                          .where(DatasetFacet.dataset_id == ds.id)).all():
        f.setdefault(k.removeprefix("lineage:"), set()).add(v)
    return f


def _classify(tag: str, ds: Dataset, f: dict[str, set[str]]) -> tuple[str, str] | None:
    """('redundant'|'only_record'|'contradicts', reason) for fact-like tags, else None."""
    low = tag.lower()
    if low in GEOMETRY_TAGS:
        geo = f.get("geometry", set())
        want = GEOMETRY_TAGS[low]
        if want in geo:
            return "redundant", f"geometry is {want} (from atoms.pbc)"
        if geo - {"unknown"}:
            return "contradicts", f"frames say {', '.join(sorted(geo))}"
        return "only_record", "no structure summary"
    if low == "dft":
        if "dft" in f.get("calc_type", set()):
            return "redundant", "a DFT calculation is recorded"
        return "only_record", "no DFT calculation recorded"
    if low == "md":
        if f.get("md_engine") or f.get("ensemble") or f.get("temperature") or f.get("model"):
            return "redundant", "MD settings / driving model recorded"
        return "only_record", "no MD recorded"
    if low == "unlabelled":
        if not (ds.has_energy or ds.has_forces):
            return "redundant", "no energies/forces in the frames"
        return "contradicts", "frames have energies/forces"
    if low in ("d4-added", "d3-added"):
        if low[:2].upper() in {d.upper() for d in f.get("dispersion", set())}:
            return "redundant", f"dispersion {low[:2].upper()} recorded"
        return "only_record", "dispersion not recorded"
    key = _norm(tag)
    for k in FACT_KEYS:
        for v in f.get(k, set()):
            nv = _norm(v)
            if key and (key == nv or (len(key) >= 4 and key in nv)):
                return "redundant", f"{k} = {v}"
    if key in FACT_WORDS:
        return "only_record", "names a code/method/model, but nothing matching is recorded"
    return None


def cleanup(s: Session, apply: bool = False, words: set[str] | None = None) -> dict:
    """Find tags that repeat derived facts. ``words``: extra tag names to treat as
    fact-like (default: every tag of kind ``free``)."""
    free = {t.name for t in s.execute(select(Tag).where(Tag.kind == "free")).scalars()}
    words = free if words is None else words
    out = {"redundant": [], "only_record": [], "contradicts": []}
    drop: list[tuple[str, int]] = []
    for ds in s.execute(select(Dataset).where(Dataset.status != "removed")
                        .order_by(Dataset.node_id)).scalars():
        f = _facts(s, ds)
        for t in list(ds.tags):
            if t.name not in words and t.name.lower() not in GEOMETRY_TAGS:
                continue
            c = _classify(t.name, ds, f)
            if c is None:
                continue
            out[c[0]].append({"dataset": ds.id, "tag": t.name, "reason": c[1]})
            if c[0] == "redundant":
                drop.append((ds.id, t.id))
    if apply and drop:
        for node_id, tag_id in drop:
            s.execute(delete(NodeTag).where(NodeTag.node_id == node_id, NodeTag.tag_id == tag_id))
        AtomDB.audit(s, "tag.cleanup", None, removed=[{"dataset": d, "tag_id": t} for d, t in drop])
        unused = s.execute(select(Tag).where(
            Tag.alias_of.is_(None), Tag.kind == "free",
            ~Tag.id.in_(select(NodeTag.tag_id).distinct()))).scalars().all()
        out["deleted_tags"] = sorted(t.name for t in unused)
        for t in unused:
            for a in s.execute(select(Tag).where(Tag.alias_of == t.id)).scalars():
                s.delete(a)
            s.delete(t)
        s.flush()
        s.expire_all()
        from .facets import refresh

        refresh(s, {d for d, _ in drop})
    out["applied"] = apply
    return out


def nodes_with(s: Session, name: str) -> list[str]:
    t = resolve(s, name)
    if t is None:
        return []
    return list(s.execute(select(NodeTag.node_id).where(NodeTag.tag_id == t.id)).scalars())

