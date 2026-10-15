"""ORM models.

Every provenance-capable object is a :class:`Node` with a stable human-readable ID
(``DS-2026-000142``). Typed detail tables (dataset, file, ...) use joined-table
inheritance; relationships between nodes live in the ``edge`` table (see PROVENANCE.md).
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .types import UTCDateTime, utcnow


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON, datetime: UTCDateTime}


# --------------------------------------------------------------------------- bookkeeping


class Meta(Base):
    """Key/value store for instance-level facts (instance UUID, creation date, ...)."""

    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class IdCounter(Base):
    __tablename__ = "id_counter"
    prefix: Mapped[str] = mapped_column(String(8), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, default=1)


class AuditLog(Base):
    """Append-only record of every mutation."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    action: Mapped[str] = mapped_column(String(64))
    node_id: Mapped[str | None] = mapped_column(String(32), index=True)
    command: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)


# --------------------------------------------------------------------------- nodes


class Node(Base):
    __tablename__ = "node"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, default=lambda: str(uuid_mod.uuid4())
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(512), index=True)
    #: short handle shown instead of the (often long) name in lists and plots
    label: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    created_by_cmd: Mapped[str | None] = mapped_column(Text)

    tags: Mapped[list[Tag]] = relationship(secondary="node_tag", lazy="selectin")
    notes: Mapped[list[Note]] = relationship(back_populates="node", lazy="selectin")
    attrs: Mapped[list[NodeAttr]] = relationship(lazy="selectin", cascade="all, delete-orphan")

    __mapper_args__ = {"polymorphic_on": "kind", "polymorphic_identity": "node"}


class Dataset(Node):
    __tablename__ = "dataset"
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"), primary_key=True)
    n_frames: Mapped[int | None] = mapped_column(Integer)
    n_atoms_min: Mapped[int | None] = mapped_column(Integer)
    n_atoms_max: Mapped[int | None] = mapped_column(Integer)
    n_atoms_total: Mapped[int | None] = mapped_column(BigInteger)
    formula_summary: Mapped[str | None] = mapped_column(String(512))
    has_energy: Mapped[bool] = mapped_column(Boolean, default=False)
    has_forces: Mapped[bool] = mapped_column(Boolean, default=False)
    has_stress: Mapped[bool] = mapped_column(Boolean, default=False)
    energy_min: Mapped[float | None] = mapped_column(Float)
    energy_max: Mapped[float | None] = mapped_column(Float)
    fmax_max: Mapped[float | None] = mapped_column(Float)
    primary_file_id: Mapped[str | None] = mapped_column(ForeignKey("file.node_id"))
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    source_text: Mapped[str | None] = mapped_column(Text)
    #: summary of the frames' pbc / cells / per-atom arrays (services/structure.py)
    structure: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    primary_file: Mapped[File | None] = relationship(foreign_keys=[primary_file_id])
    files: Mapped[list[DatasetFile]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", lazy="selectin"
    )

    __mapper_args__ = {"polymorphic_identity": "dataset", "inherit_condition": node_id == Node.id}


class File(Node):
    """A byte-identical file in the content-addressed store."""

    __tablename__ = "file"
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    format: Mapped[str | None] = mapped_column(String(32))
    store_relpath: Mapped[str] = mapped_column(String(256))
    origins: Mapped[list[FileOrigin]] = relationship(
        back_populates="file", cascade="all, delete-orphan", lazy="selectin"
    )

    __mapper_args__ = {"polymorphic_identity": "file"}


class FileOrigin(Base):
    """Where a stored file was found. Many origins can map to one sha256 (dedup)."""

    __tablename__ = "file_origin"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("file.node_id"), index=True)
    original_path: Mapped[str] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(String(512))
    hostname: Mapped[str | None] = mapped_column(String(256))
    mtime: Mapped[datetime | None]
    ctime: Mapped[datetime | None]
    imported_at: Mapped[datetime] = mapped_column(default=utcnow)
    import_id: Mapped[str | None] = mapped_column(String(32))
    file: Mapped[File] = relationship(back_populates="origins")

    __table_args__ = (UniqueConstraint("file_id", "original_path", "hostname"),)


class DatasetFile(Base):
    __tablename__ = "dataset_file"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("dataset.node_id"), primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("file.node_id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    dataset: Mapped[Dataset] = relationship(back_populates="files")
    file: Mapped[File] = relationship(lazy="selectin")


class SymLink(Base):
    """Symlinks handed out by ``atomdb link`` so validate can find dangling ones."""

    __tablename__ = "symlink"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("file.node_id"))
    node_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Calculation(Node):
    """An activity: MD run, DFT labeling, sampling, conversion, training, import, ..."""

    __tablename__ = "calculation"
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"), primary_key=True)
    calc_type: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    command: Mapped[str | None] = mapped_column(Text)
    workdir: Mapped[str | None] = mapped_column(Text)
    exit_status: Mapped[str | None] = mapped_column(String(32))
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: reproducibility snapshot, e.g. {"python": "3.12.10", "ase": "3.29.0", "atomdb": ...}
    software_versions: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    md: Mapped[MDParams | None] = relationship(lazy="selectin", cascade="all, delete-orphan")
    dft: Mapped[DFTParams | None] = relationship(lazy="selectin", cascade="all, delete-orphan")
    sampling: Mapped[SamplingOp | None] = relationship(
        lazy="selectin", cascade="all, delete-orphan"
    )
    training: Mapped[TrainingRun | None] = relationship(
        lazy="selectin", cascade="all, delete-orphan"
    )
    enhanced_sampling: Mapped[list[EnhancedSampling]] = relationship(
        lazy="selectin", cascade="all, delete-orphan"
    )
    params: Mapped[list[CalcParam]] = relationship(lazy="selectin", cascade="all, delete-orphan")

    __mapper_args__ = {"polymorphic_identity": "calculation"}


class MDParams(Base):
    """Molecular dynamics settings of an MD calculation."""

    __tablename__ = "md_params"
    calc_id: Mapped[str] = mapped_column(ForeignKey("calculation.node_id"), primary_key=True)
    engine: Mapped[str | None] = mapped_column(String(64))  # ASE, LAMMPS, CP2K, GROMACS, apax...
    engine_version: Mapped[str | None] = mapped_column(String(64))
    ensemble: Mapped[str | None] = mapped_column(String(16), index=True)  # NVE/NVT/NPT/...
    temperature_k: Mapped[float | None] = mapped_column(Float, index=True)
    pressure_bar: Mapped[float | None] = mapped_column(Float)
    timestep_fs: Mapped[float | None] = mapped_column(Float)
    n_steps: Mapped[int | None] = mapped_column(BigInteger)
    length_ps: Mapped[float | None] = mapped_column(Float)
    thermostat: Mapped[str | None] = mapped_column(String(64))
    barostat: Mapped[str | None] = mapped_column(String(64))
    integrator: Mapped[str | None] = mapped_column(String(64))
    seed: Mapped[int | None] = mapped_column(BigInteger)
    density_g_cm3: Mapped[float | None] = mapped_column(Float)
    pbc: Mapped[str | None] = mapped_column(String(16))
    initial_velocities: Mapped[str | None] = mapped_column(String(128))
    save_interval: Mapped[int | None] = mapped_column(Integer)


class DFTParams(Base):
    """Electronic-structure settings of a DFT calculation (labels)."""

    __tablename__ = "dft_params"
    calc_id: Mapped[str] = mapped_column(ForeignKey("calculation.node_id"), primary_key=True)
    code: Mapped[str | None] = mapped_column(String(64), index=True)
    code_version: Mapped[str | None] = mapped_column(String(64))
    functional: Mapped[str | None] = mapped_column(String(64), index=True)  # canonical
    functional_raw: Mapped[str | None] = mapped_column(String(256))
    dispersion: Mapped[str | None] = mapped_column(String(32), index=True)  # none/D3/D3BJ/D4
    basis: Mapped[str | None] = mapped_column(String(256))
    pseudopotential: Mapped[str | None] = mapped_column(String(256))
    cutoff_ry: Mapped[float | None] = mapped_column(Float)
    rel_cutoff_ry: Mapped[float | None] = mapped_column(Float)
    eps_scf: Mapped[float | None] = mapped_column(Float)
    smearing: Mapped[str | None] = mapped_column(String(64))
    charge: Mapped[int | None] = mapped_column(Integer)
    multiplicity: Mapped[int | None] = mapped_column(Integer)
    spin_polarized: Mapped[bool | None] = mapped_column(Boolean)
    kpoints: Mapped[str | None] = mapped_column(String(64))
    labels: Mapped[list[Any] | None] = mapped_column(JSON)  # ["energy","forces","stress"]


class CalcParam(Base):
    """Arbitrary code settings (e.g. the flattened CP2K input tree)."""

    __tablename__ = "calc_param"
    calc_id: Mapped[str] = mapped_column(ForeignKey("calculation.node_id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(256), primary_key=True)  # e.g. FORCE_EVAL/DFT/SCF/EPS_SCF
    value: Mapped[Any] = mapped_column(JSON)
    unit: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16), default="user")  # parsed|user|inferred


class EnhancedSampling(Base):
    __tablename__ = "enhanced_sampling"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    calc_id: Mapped[str] = mapped_column(ForeignKey("calculation.node_id"), index=True)
    method: Mapped[str] = mapped_column(String(32), index=True)  # metad/opes/umbrella/abf/...
    software: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(64))
    collective_variables: Mapped[list[Any] | None] = mapped_column(JSON)
    bias: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_file_id: Mapped[str | None] = mapped_column(ForeignKey("file.node_id"))


class SamplingOp(Base):
    __tablename__ = "sampling_op"
    calc_id: Mapped[str] = mapped_column(ForeignKey("calculation.node_id"), primary_key=True)
    method: Mapped[str] = mapped_column(String(32))  # random/stride/uncertainty/fps/manual/filter
    criterion: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    seed: Mapped[int | None] = mapped_column(BigInteger)
    n_selected: Mapped[int | None] = mapped_column(Integer)


class TrainingRun(Base):
    __tablename__ = "training_run"
    calc_id: Mapped[str] = mapped_column(ForeignKey("calculation.node_id"), primary_key=True)
    hyperparameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    config_file_id: Mapped[str | None] = mapped_column(ForeignKey("file.node_id"))


class Model(Node):
    """An interatomic potential (MLIP checkpoint or foundation model)."""

    __tablename__ = "model"
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"), primary_key=True)
    family: Mapped[str | None] = mapped_column(String(64), index=True)  # MACE, NequIP, apax...
    version: Mapped[str | None] = mapped_column(String(64))
    architecture: Mapped[str | None] = mapped_column(String(128))
    checkpoint_file_id: Mapped[str | None] = mapped_column(ForeignKey("file.node_id"))
    cutoff: Mapped[float | None] = mapped_column(Float)
    n_parameters: Mapped[int | None] = mapped_column(BigInteger)
    package: Mapped[str | None] = mapped_column(String(64))
    package_version: Mapped[str | None] = mapped_column(String(64))
    git_commit: Mapped[str | None] = mapped_column(String(64))
    trained_at: Mapped[datetime | None]
    url: Mapped[str | None] = mapped_column(Text)
    hyperparameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __mapper_args__ = {"polymorphic_identity": "model", "inherit_condition": node_id == Node.id}


class HPCJob(Node):
    __tablename__ = "hpc_job"
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"), primary_key=True)
    cluster: Mapped[str | None] = mapped_column(String(128), index=True)
    partition: Mapped[str | None] = mapped_column(String(128))
    job_id: Mapped[str | None] = mapped_column(String(64), index=True)
    array_job_id: Mapped[str | None] = mapped_column(String(64))
    array_task_id: Mapped[str | None] = mapped_column(String(32))
    user: Mapped[str | None] = mapped_column(String(64))
    workdir: Mapped[str | None] = mapped_column(Text)
    submit_script_file_id: Mapped[str | None] = mapped_column(ForeignKey("file.node_id"))
    nodes: Mapped[str | None] = mapped_column(String(256))
    n_nodes: Mapped[int | None] = mapped_column(Integer)
    n_tasks: Mapped[int | None] = mapped_column(Integer)
    n_cpus: Mapped[int | None] = mapped_column(Integer)
    n_gpus: Mapped[int | None] = mapped_column(Integer)
    gpu_type: Mapped[str | None] = mapped_column(String(64))
    mem: Mapped[str | None] = mapped_column(String(32))
    time_limit: Mapped[str | None] = mapped_column(String(32))
    start: Mapped[datetime | None]
    end: Mapped[datetime | None]
    state: Mapped[str | None] = mapped_column(String(32))
    sbatch_options: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __mapper_args__ = {"polymorphic_identity": "hpc_job", "inherit_condition": node_id == Node.id}


class NodeAttr(Base):
    """User/parsed metadata on any node that has no dedicated column."""

    __tablename__ = "node_attr"
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    unit: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16), default="user")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class FrameLink(Base):
    """Frame-level lineage: child frame was taken from parent frame."""

    __tablename__ = "frame_link"
    child_dataset: Mapped[str] = mapped_column(ForeignKey("dataset.node_id"), primary_key=True)
    child_idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_dataset: Mapped[str] = mapped_column(ForeignKey("dataset.node_id"), primary_key=True)
    parent_idx: Mapped[int] = mapped_column(Integer)
    via_calc: Mapped[str | None] = mapped_column(ForeignKey("node.id"))

    __table_args__ = (Index("ix_frame_link_parent", "parent_dataset", "parent_idx"),)


class Edge(Base):
    """A provenance relation ``src --relation--> dst`` (see PROVENANCE.md)."""

    __tablename__ = "edge"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    src: Mapped[str] = mapped_column(ForeignKey("node.id"), index=True)
    dst: Mapped[str] = mapped_column(ForeignKey("node.id"), index=True)
    relation: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(16), default="auto")  # auto|parsed|user|inferred
    attrs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    __table_args__ = (UniqueConstraint("src", "dst", "relation", "role"),)


# --------------------------------------------------------------------------- frame index


class Frame(Base):
    """One row per frame. Values are an index for search/plots; data stays in H5MD."""

    __tablename__ = "frame"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("dataset.node_id"), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    n_atoms: Mapped[int] = mapped_column(Integer)
    formula: Mapped[str] = mapped_column(String(256))
    elem_mask_lo: Mapped[int] = mapped_column(BigInteger, default=0)
    elem_mask_hi: Mapped[int] = mapped_column(BigInteger, default=0)
    energy: Mapped[float | None] = mapped_column(Float)
    energy_per_atom: Mapped[float | None] = mapped_column(Float)
    fmax: Mapped[float | None] = mapped_column(Float)
    temperature: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    time: Mapped[float | None] = mapped_column(Float)
    step: Mapped[int | None] = mapped_column(BigInteger)
    fingerprint: Mapped[str] = mapped_column(String(20), index=True)

    __table_args__ = (
        Index("ix_frame_masks", "elem_mask_lo", "elem_mask_hi"),
        Index("ix_frame_energy", "dataset_id", "energy_per_atom"),
    )


class DatasetElement(Base):
    __tablename__ = "dataset_element"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("dataset.node_id"), primary_key=True)
    z: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(3), index=True)
    n_frames: Mapped[int] = mapped_column(Integer)
    n_atoms: Mapped[int] = mapped_column(BigInteger)


class DatasetFacet(Base):
    """Search index: facts about a dataset aggregated from its whole upstream graph
    (functional, dispersion, model, temperature, ancestors, ...). Derived data —
    rebuilt automatically when provenance changes (see services/facets.py)."""

    __tablename__ = "dataset_facet"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("dataset.node_id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(512))
    num: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        Index("ix_facet_key_value", "key", "value"),
        Index("ix_facet_key_num", "key", "num"),
    )


class PropertyDef(Base):
    """Catalog of every field found in a dataset's file (generic metadata preservation)."""

    __tablename__ = "property_def"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("dataset.node_id"), primary_key=True)
    origin: Mapped[str] = mapped_column(String(16), primary_key=True)  # atoms|info|arrays|calc
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    level: Mapped[str] = mapped_column(String(8))  # frame|atom|cell
    dtype: Mapped[str] = mapped_column(String(32))
    shape: Mapped[list[Any] | None] = mapped_column(JSON)
    unit: Mapped[str | None] = mapped_column(String(32))
    count: Mapped[int] = mapped_column(Integer, default=0)
    #: calculation that produced this property (e.g. DFT labels), if known
    label_calc_id: Mapped[str | None] = mapped_column(ForeignKey("node.id"))


# --------------------------------------------------------------------------- tags/notes


class Tag(Base):
    """A label. ``kind`` (system/project/quality/free) sets its chip colour and sidebar
    group; an alias row (``alias_of``) redirects to the canonical tag (services/tags.py)."""

    __tablename__ = "tag"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(16), default="free", server_default="free")
    description: Mapped[str | None] = mapped_column(Text)
    alias_of: Mapped[int | None] = mapped_column(ForeignKey("tag.id"))


class NodeTag(Base):
    __tablename__ = "node_tag"
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id"), primary_key=True)


class Note(Base):
    """Free-text supplement. Never a substitute for structured provenance."""

    __tablename__ = "note"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("node.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    node: Mapped[Node] = relationship(back_populates="notes")


Index("ix_node_created", Node.created_at)
