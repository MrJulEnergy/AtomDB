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
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    created_by_cmd: Mapped[str | None] = mapped_column(Text)

    tags: Mapped[list[Tag]] = relationship(secondary="node_tag", lazy="selectin")
    notes: Mapped[list[Note]] = relationship(back_populates="node", lazy="selectin")

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

    __mapper_args__ = {"polymorphic_identity": "calculation"}


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
    __tablename__ = "tag"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)


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
