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
