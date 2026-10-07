"""Recording scientific provenance: MD runs, DFT labeling, sampling, training, models.

Each ``record_*`` function creates a Calculation node with its typed parameter row and
the provenance edges to its inputs/outputs. Fields record where they came from
(``source``: user | parsed | inferred) via the generic ``calc_param``/``node_attr`` rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import vocab
from ..db.models import (
    CalcParam,
    Calculation,
    Dataset,
    DFTParams,
    EnhancedSampling,
    MDParams,
    Model,
    Node,
    NodeAttr,
    PropertyDef,
    SamplingOp,
    TrainingRun,
)
from ..db.types import utcnow
from ..errors import InvalidInput, NotFound
from ..ids import is_id, new_id, normalize_id
from ..reproducibility import command_line, software_versions
from .context import AtomDB
from .datasets import get_node
from .files import store_file
from .provenance import add_edge

LABEL_PROPS = {"energy": ("energy", "free_energy"), "forces": ("forces",), "stress": ("stress",)}


def _require(s: Session, node_id: str, kind: type | None = None) -> Node:
    node = get_node(s, node_id)
    if kind is not None and not isinstance(node, kind):
        raise InvalidInput(f"{node.id} is a {node.kind}, expected {kind.__name__.lower()}")
    return node


def set_attrs(s: Session, node: Node, attrs: dict[str, Any], source: str = "user") -> None:
    for k, v in attrs.items():
        existing = next((a for a in node.attrs if a.key == k), None)
        if existing is None:
            node.attrs.append(NodeAttr(key=k, value=v, source=source))
        else:
            existing.value = v
            existing.source = source


def add_params(calc: Calculation, params: dict[str, Any], source: str = "user") -> None:
    have = {p.key: p for p in calc.params}
    for k, v in params.items():
        if k in have:
            have[k].value, have[k].source = v, source
        else:
            calc.params.append(CalcParam(key=k, value=v, source=source))


# --------------------------------------------------------------------------- models


def find_model(s: Session, ref: str) -> Model | None:
    if is_id(normalize_id(ref)):
        node = s.get(Node, normalize_id(ref))
        return node if isinstance(node, Model) else None
    return s.execute(
        select(Model).where(func.lower(Model.name) == ref.strip().lower())
        .order_by(Model.created_at)
    ).scalars().first()


def resolve_model(s: Session, ref: str, create_placeholder: bool = True) -> Model:
    """Find a model by ID or name; unknown names become placeholder models."""
    m = find_model(s, ref)
    if m is not None:
        return m
    if not create_placeholder or is_id(normalize_id(ref)):
        raise NotFound(f"no such model: {ref}")
    m = Model(id=new_id(s, "model"), name=ref.strip(), status="placeholder",
              description="created by reference; register details with `atomdb model add`")
    s.add(m)
    s.flush()
    return m


@dataclass
class ModelSpec:
    name: str
    family: str | None = None
    version: str | None = None
    architecture: str | None = None
    cutoff: float | None = None
    n_parameters: int | None = None
    package: str | None = None
    package_version: str | None = None
    git_commit: str | None = None
    trained_at: datetime | None = None
    url: str | None = None
    description: str | None = None
    hyperparameters: dict | None = None
    trained_on: list[str] = field(default_factory=list)


def add_model(adb: AtomDB, s: Session, spec: ModelSpec, checkpoint: Path | None = None) -> Model:
    m = find_model(s, spec.name)
    if m is None or m.status != "placeholder":
        m = Model(id=new_id(s, "model"), name=spec.name)
        s.add(m)
    else:
        m.status = "active"  # upgrade a placeholder created by reference
    for k in ("family", "version", "architecture", "cutoff", "n_parameters", "package",
              "package_version", "git_commit", "trained_at", "url", "description",
              "hyperparameters"):
        v = getattr(spec, k)
        if v is not None:
            setattr(m, k, v)
    s.flush()
    if checkpoint is not None:
        files = _store_checkpoint(adb, s, checkpoint, m.id)
        m.checkpoint_file_id = files[0].id
        for f in files:
            add_edge(s, f.id, m.id, "part_of", role="checkpoint")
    if spec.trained_on:
        trn = Calculation(id=new_id(s, "training"), name=f"training of {m.name}",
                          calc_type="training", command=command_line(),
                          software_versions=software_versions(), status="placeholder")
        trn.training = TrainingRun(hyperparameters=spec.hyperparameters)
        s.add(trn)
        s.flush()
        for ds in spec.trained_on:
            d = _require(s, ds, Dataset)
            add_edge(s, trn.id, d.id, "used", role="training_data")
            add_edge(s, m.id, d.id, "trained_on")
        add_edge(s, trn.id, m.id, "generated")
    AtomDB.audit(s, "model.add", m.id, name=m.name)
    return m


def _store_checkpoint(adb: AtomDB, s: Session, path: Path, model_id: str):
    path = Path(path).expanduser()
    if path.is_file():
        f, _ = store_file(adb, s, path, fmt="checkpoint", import_id=model_id)
        return [f]
    if path.is_dir():
        files = []
        for p in sorted(x for x in path.rglob("*") if x.is_file()):
            f, _ = store_file(adb, s, p, fmt="checkpoint", import_id=model_id)
            set_attrs(s, f, {"relative_path": str(p.relative_to(path)),
                             "checkpoint_dir": str(path.resolve())}, source="parsed")
            files.append(f)
        if not files:
            raise InvalidInput(f"checkpoint directory is empty: {path}")
        return files
    raise InvalidInput(f"checkpoint not found: {path}")


# --------------------------------------------------------------------------- calculations


def _calc(s: Session, kind: str, name: str, workdir: str | None, status: str,
          description: str | None, started_at=None, ended_at=None) -> Calculation:
    c = Calculation(
        id=new_id(s, kind), name=name, calc_type=kind, workdir=workdir, status=status,
        description=description, command=command_line(), software_versions=software_versions(),
        started_at=started_at, ended_at=ended_at,
    )
    s.add(c)
    s.flush()
    return c


def _link_io(s: Session, calc: Calculation, inputs: list[str], outputs: list[str],
             source: str) -> tuple[list[Dataset], list[Node]]:
    ins, outs = [], []
    for i in inputs:
        node = _require(s, i)
        add_edge(s, calc.id, node.id, "used", source=source)
        ins.append(node)
    for o in outputs:
        node = _require(s, o)
        add_edge(s, calc.id, node.id, "generated", source=source)
        outs.append(node)
    return ins, outs


def record_md(
    s: Session,
    outputs: list[str],
    inputs: list[str] | None = None,
    model: str | None = None,
    name: str | None = None,
    description: str | None = None,
    workdir: str | None = None,
    params: dict[str, Any] | None = None,
    md: dict[str, Any] | None = None,
    enhanced: dict[str, Any] | None = None,
    jobs: list[str] | None = None,
    source: str = "user",
    status: str = "active",
) -> Calculation:
    md = {k: v for k, v in (md or {}).items() if v is not None}
    if "ensemble" in md:
        md["ensemble"] = vocab.ensemble(md["ensemble"])
    t = md.get("temperature_k")
    m = resolve_model(s, model) if model else None
    label = name or " ".join(x for x in (
        "MD", md.get("ensemble"), f"{t:g} K" if t is not None else None,
        f"({m.name})" if m else None) if x)
    c = _calc(s, "md", label, workdir, status, description)
    c.md = MDParams(**md)
    if params:
        add_params(c, params, source)
    _link_io(s, c, inputs or [], outputs, source)
    if m is not None:
        add_edge(s, c.id, m.id, "driven_by", source=source)
    if enhanced:
        add_enhanced_sampling(s, c, **enhanced)
    for j in jobs or []:
        add_edge(s, c.id, normalize_id(j), "ran_on", source=source)
    AtomDB.audit(s, "record.md", c.id, outputs=outputs, inputs=inputs or [], md=md)
    return c


def add_enhanced_sampling(
    s: Session, calc: Calculation, method: str, software: str | None = None,
    version: str | None = None, collective_variables: list | None = None,
    bias: dict | None = None, input_file_id: str | None = None,
) -> EnhancedSampling:
    es = EnhancedSampling(method=vocab.es_method(method), software=software, version=version,
                          collective_variables=collective_variables, bias=bias,
                          input_file_id=input_file_id)
    calc.enhanced_sampling.append(es)
    if input_file_id:
        add_edge(s, calc.id, input_file_id, "used", role="bias_input")
    return es


def record_dft(
    s: Session,
    outputs: list[str],
    inputs: list[str] | None = None,
    name: str | None = None,
    description: str | None = None,
    workdir: str | None = None,
    dft: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    labels: list[str] | None = None,
    jobs: list[str] | None = None,
    source: str = "user",
    status: str = "active",
) -> Calculation:
    dft = {k: v for k, v in (dft or {}).items() if v is not None}
    if "functional" in dft:
        dft.setdefault("functional_raw", dft["functional"])
        dft["functional"] = vocab.functional(dft["functional"])
    if "dispersion" in dft:
        dft["dispersion"] = vocab.dispersion(dft["dispersion"])
    labels = labels or ["energy", "forces"]
    disp = dft.get("dispersion")
    label = name or "/".join(x for x in (
        dft.get("code"), dft.get("functional"), disp if disp and disp != "none" else None) if x)
    c = _calc(s, "dft", label or "DFT calculation", workdir, status, description)
    c.dft = DFTParams(labels=labels, **dft)
    if params:
        add_params(c, params, source)
    ins, outs = _link_io(s, c, inputs or [], outputs, source)
    from .lineage import link_frames

    for o in outs:
        if isinstance(o, Dataset):
            add_edge(s, o.id, c.id, "labeled_by", source=source,
                     attrs={"properties": labels})
            _mark_labels(s, o.id, c.id, labels)
            for i in ins:  # labeled frames are the input structures, re-computed
                if isinstance(i, Dataset):
                    link_frames(s, o.id, i.id, c.id)
    for j in jobs or []:
        add_edge(s, c.id, normalize_id(j), "ran_on", source=source)
    AtomDB.audit(s, "record.dft", c.id, outputs=outputs, dft=dft)
    return c


def _mark_labels(s: Session, ds_id: str, calc_id: str, labels: list[str]) -> None:
    names = [n for lab in labels for n in LABEL_PROPS.get(lab, (lab,))]
    for p in s.execute(select(PropertyDef).where(
            PropertyDef.dataset_id == ds_id, PropertyDef.origin == "calc",
            PropertyDef.name.in_(names))).scalars():
        p.label_calc_id = calc_id


def record_sampling(
    s: Session,
    parent: str,
    child: str,
    method: str,
    criterion: dict[str, Any] | None = None,
    seed: int | None = None,
    name: str | None = None,
    description: str | None = None,
    source: str = "user",
    frame_map: list[tuple[int, int]] | None = None,
) -> Calculation:
    from .lineage import link_frames

    p = _require(s, parent, Dataset)
    ch = _require(s, child, Dataset)
    c = _calc(s, "sampling", name or f"{method} selection from {p.id}", None, "active",
              description)
    c.sampling = SamplingOp(method=method, criterion=criterion, seed=seed,
                            n_selected=ch.n_frames)
    add_edge(s, c.id, p.id, "used", source=source)
    add_edge(s, c.id, ch.id, "generated", source=source)
    add_edge(s, ch.id, p.id, "sampled_from", source=source)
    stats = link_frames(s, ch.id, p.id, c.id, frame_map)
    c.sampling.criterion = {**(criterion or {}), "frame_mapping": stats}
    AtomDB.audit(s, "record.sampling", c.id, parent=p.id, child=ch.id, method=method)
    return c


def record_training(
    s: Session,
    model: str,
    train: list[str],
    validation: list[str] | None = None,
    hyperparameters: dict | None = None,
    metrics: dict | None = None,
    name: str | None = None,
    workdir: str | None = None,
    jobs: list[str] | None = None,
    source: str = "user",
) -> Calculation:
    m = resolve_model(s, model)
    c = _calc(s, "training", name or f"training of {m.name}", workdir, "active", None)
    c.training = TrainingRun(hyperparameters=hyperparameters, metrics=metrics)
    for d in train:
        ds = _require(s, d, Dataset)
        add_edge(s, c.id, ds.id, "used", role="training_data", source=source)
        add_edge(s, m.id, ds.id, "trained_on", source=source)
    for d in validation or []:
        ds = _require(s, d, Dataset)
        add_edge(s, c.id, ds.id, "used", role="validation_data", source=source)
    add_edge(s, c.id, m.id, "generated", source=source)
    for j in jobs or []:
        add_edge(s, c.id, normalize_id(j), "ran_on", source=source)
    AtomDB.audit(s, "record.training", c.id, model=m.id, train=train)
    return c


def touch(node: Node) -> None:
    node.updated_at = utcnow()
