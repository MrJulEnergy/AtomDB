"""Pydantic schemas shared by CLI JSON output and the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class NodeRef(_Out):
    id: str
    kind: str
    name: str


class FileOriginOut(_Out):
    original_path: str
    original_name: str
    hostname: str | None
    mtime: datetime | None
    ctime: datetime | None
    imported_at: datetime
    import_id: str | None


class FileOut(_Out):
    id: str
    name: str
    sha256: str
    size_bytes: int
    format: str | None
    store_path: str
    created_at: datetime
    origins: list[FileOriginOut] = []


class DatasetFileOut(_Out):
    role: str
    file: FileOut


class DatasetSummary(_Out):
    id: str
    name: str
    status: str
    created_at: datetime
    n_frames: int | None
    n_atoms_min: int | None
    n_atoms_max: int | None
    formula_summary: str | None
    elements: list[str] = []
    has_energy: bool
    has_forces: bool
    has_stress: bool
    energy_min: float | None = None
    energy_max: float | None = None
    fmax_max: float | None = None
    tags: list[str] = []
    extra: dict[str, Any] = {}


class DatasetDetail(DatasetSummary):
    description: str | None
    updated_at: datetime
    source_text: str | None
    files: list[DatasetFileOut] = []
    notes: list[str] = []


class ImportResult(_Out):
    dataset: DatasetSummary | None
    file_id: str
    sha256: str
    duplicate: bool
    message: str
    warnings: list[str] = []


class Page(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[Any]
