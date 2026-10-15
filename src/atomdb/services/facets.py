"""Search facets: per-dataset facts aggregated over the upstream provenance graph.

Kept consistent automatically: an ``after_flush`` hook records which provenance-relevant
objects changed, and ``before_commit`` re-computes the facets of every dataset downstream
of them. ``rebuild_all`` recomputes everything (``atomdb reindex``).
"""

from __future__ import annotations

from sqlalchemy import delete, event, func, insert, select
from sqlalchemy.orm import Session

from .. import vocab
from ..db.models import (
    Calculation,
    Dataset,
    DatasetElement,
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
    PropertyDef,
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
        if t.kind in TAG_KINDS:  # system / project / quality tags are browse categories
            add(t.kind, t.name)
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
    props = s.execute(select(PropertyDef.origin, PropertyDef.name)
                      .where(PropertyDef.dataset_id == ds.id)).all()
    n_el = s.execute(select(func.count()).select_from(DatasetElement)
                     .where(DatasetElement.dataset_id == ds.id)).scalar_one()
    for key, value in _categories(rows):
        add(key, value)
    for key, value in _structure_categories(ds, rows, props, n_el):
        add(key, value)
    seen = set()
    out = []
    for k, v, n in rows:
        if (k, v) in seen:
            continue
        seen.add((k, v))
        out.append({"dataset_id": ds.id, "key": k, "value": v, "num": n})
    return out


#: browse categories (sidebar, ``key:a,b`` search); the last ones come from the frames
CATEGORY_KEYS = ("system", "project", "quality", "geometry", "cell_shape", "cell", "solvation",
                 "data_type", "dft_code", "mlip_family", "labels", "atom_data", "composition",
                 "n_elements", "size")
#: tag kinds that become browse categories (services/tags.py); "free" tags do not
TAG_KINDS = ("system", "project", "quality")
#: search synonyms of the geometry values
GEOMETRY_SYNONYMS = {"periodic": "pbc", "bulk": "pbc", "cluster": "no-pbc", "vacuum": "no-pbc",
                     "molecule": "no-pbc", "non-periodic": "no-pbc", "nopbc": "no-pbc",
                     "slab": "partial-pbc", "wire": "partial-pbc"}
CLASSICAL_FAMILIES = {"classical force field"}
CLASSICAL_ENGINES = {"gromacs", "lammps-ff", "openmm", "amber"}
CLUSTER_CODES = {"ORCA", "Psi4", "Gaussian", "TURBOMOLE"}  # molecular codes: never periodic


def _categories(rows: list[tuple[str, str, float | None]]) -> list[tuple[str, str]]:
    """Coarse browse categories derived from the dataset's own-generation facts
    (provenance only; tags are labels and never decide a fact)."""
    own: dict[str, set[str]] = {}
    for k, v, _ in rows:
        own.setdefault(k, set()).add(v)
    codes = {c for c in (vocab.code(x) for x in own.get("code", ())) if c}
    families = own.get("model_family", set())
    mlips = {f for f in families if f.lower() not in CLASSICAL_FAMILIES}
    out = [("dft_code", c) for c in sorted(codes)] + [("mlip_family", f) for f in sorted(mlips)]

    if codes and codes <= CLUSTER_CODES:
        geometry = "no-pbc"
    else:
        geometry = "unknown"
    out.append(("geometry_hint", geometry))

    if "dft" in own.get("calc_type", set()):
        data_type = "dft"
    # an MLIP step wins over classical MD further upstream (e.g. MACE-relaxed GROMACS frames);
    # a model without a family counts as an MLIP unless a classical force field is present
    elif mlips or (own.get("model") and not families & CLASSICAL_FAMILIES):
        data_type = "mlip-md"
    elif (families & CLASSICAL_FAMILIES
          or {e.lower() for e in own.get("md_engine", ())} & CLASSICAL_ENGINES):
        data_type = "classical-md"
    else:
        data_type = "other"
    out.append(("data_type", data_type))

    # implicit solvation of the labelling calculation (box/cluster geometry is separate)
    solv = own.get("solvation", set())
    if solv:
        out += [("solvation", v) for v in sorted(solv)]
    elif data_type == "dft":
        out.append(("solvation", "none"))
    return out


GEOMETRY_BY_DIMS = {3: "pbc", 2: "partial-pbc", 1: "partial-pbc", 0: "no-pbc"}
CELL_SHAPES = ("triclinic", "orthorhombic", "cubic")  # most general first
#: calculator results → ``labels`` values (others are not offered as a category)
LABEL_NAMES = {"energy": "energy", "free_energy": "energy", "forces": "forces",
               "stress": "stress", "virial": "virial", "dipole": "dipole",
               "charges": "charges", "magmom": "magmoms", "magmoms": "magmoms",
               "energies": "atomic-energies"}
#: per-atom arrays → ``atom_data`` values (unlisted arrays keep their own name)
ARRAY_NAMES = {"momenta": "velocities", "velocities": "velocities",
               "initial_charges": "charges", "initial_magmoms": "magmoms", "tags": "tags",
               "masses": "masses", "residuenames": "topology", "residuenumbers": "topology",
               "atomtypes": "topology", "chain": "topology", "bfactor": "topology",
               "occupancy": "topology"}
#: atoms per frame; every bin the dataset's [min, max] range touches
SIZE_BINS = ((0, 99, "small"), (100, 1000, "medium"), (1001, None, "large"))
N_ELEMENTS = {1: "unary", 2: "binary", 3: "ternary", 4: "quaternary"}


def _structure_categories(ds: Dataset, rows, props, n_el: int) -> list[tuple[str, str]]:
    """Categories from the frames themselves (``Dataset.structure``, property catalog,
    sizes). Geometry: ``atoms.pbc``; the molecular-code guess only when there is no summary."""
    hint = next((v for k, v, _ in rows if k == "geometry_hint"), "unknown")
    st = ds.structure or {}
    out: list[tuple[str, str]] = []
    pbc = st.get("pbc") or {}
    if pbc:
        kinds = {GEOMETRY_BY_DIMS[k.count("T")] for k in pbc}
        geometry = kinds.pop() if len(kinds) == 1 else "mixed"
    else:
        geometry = hint
    out.append(("geometry", geometry))

    shapes = st.get("cell_shape") or {}
    shape = next((c for c in CELL_SHAPES if shapes.get(c)), None)
    if shape:  # one value: the most general shape of any frame
        out.append(("cell_shape", shape))
    vol = st.get("volume")
    if vol:
        out.append(("cell", "variable" if vol[1] > 0 and (vol[1] - vol[0]) / vol[1] > 1e-4
                    else "fixed"))

    labels = {LABEL_NAMES[n] for o, n in props if o == "calc" and n in LABEL_NAMES}
    labels |= {n for flag, n in ((ds.has_energy, "energy"), (ds.has_forces, "forces"),
                                 (ds.has_stress, "stress")) if flag}
    if any(o == "calc" and n.endswith("_uncertainty") for o, n in props):
        labels.add("uncertainty")
    out += [("labels", v) for v in sorted(labels)] or [("labels", "none")]

    nonzero = st.get("arrays")
    extras = set()
    for o, n in props:
        if o != "arrays" or n in ("forces",) or (nonzero is not None and not nonzero.get(n)):
            continue
        extras.add(ARRAY_NAMES.get(n, n))
    out += [("atom_data", v) for v in sorted(extras)]

    if st.get("n_formulas"):
        out.append(("composition", "fixed" if st["n_formulas"] == 1 else "variable"))
    if n_el:
        out.append(("n_elements", N_ELEMENTS.get(n_el, "5+")))
    lo, hi = ds.n_atoms_min, ds.n_atoms_max
    if lo is not None and hi is not None:
        out += [("size", name) for a, b, name in SIZE_BINS if hi >= a and (b is None or lo <= b)]
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
        for prm in node.params:
            if prm.key.lower() == "solvation" and prm.value and str(prm.value).lower() != "none":
                add("solvation", str(prm.value).split("(")[0].strip().upper())
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
        # commit() flushes only after this hook: flush first so pending changes are seen
        session.flush()
        touched = session.info.pop("facet_touched", set())
        if not touched:
            return
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
