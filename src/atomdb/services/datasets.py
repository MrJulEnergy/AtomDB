"""Dataset lookup, listing and serialization."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Dataset, DatasetElement, Node, NodeTag, Tag
from ..errors import NotFound
from ..ids import normalize_id
from ..schemas import DatasetDetail, DatasetFileOut, DatasetSummary, FileOut
from .context import AtomDB


def get_node(s: Session, node_id: str) -> Node:
    node = s.get(Node, normalize_id(node_id))
    if node is None:
        raise NotFound(f"no such node: {node_id}", id=node_id)
    return node


def get_dataset(s: Session, ds_id: str) -> Dataset:
    node = get_node(s, ds_id)
    if not isinstance(node, Dataset):
        raise NotFound(f"{ds_id} is a {node.kind}, not a dataset", id=ds_id)
    return node


def elements_of(s: Session, ds: Dataset) -> list[str]:
    rows = s.execute(
        select(DatasetElement.symbol)
        .where(DatasetElement.dataset_id == ds.id)
        .order_by(DatasetElement.z)
    ).scalars()
    return list(rows)


def file_out(adb: AtomDB, f) -> FileOut:
    return FileOut(
        id=f.id,
        name=f.name,
        sha256=f.sha256,
        size_bytes=f.size_bytes,
        format=f.format,
        store_path=str(adb.store.abspath(f.store_relpath)),
        created_at=f.created_at,
        origins=f.origins,
    )


def summary(s: Session, ds: Dataset, extra: dict | None = None) -> DatasetSummary:
    return DatasetSummary(
        id=ds.id,
        name=ds.name,
        status=ds.status,
        created_at=ds.created_at,
        n_frames=ds.n_frames,
        n_atoms_min=ds.n_atoms_min,
        n_atoms_max=ds.n_atoms_max,
        formula_summary=ds.formula_summary,
        elements=elements_of(s, ds),
        has_energy=ds.has_energy,
        has_forces=ds.has_forces,
        has_stress=ds.has_stress,
        energy_min=ds.energy_min,
        energy_max=ds.energy_max,
        fmax_max=ds.fmax_max,
        tags=sorted(t.name for t in ds.tags),
        extra=extra or {},
    )


def detail(adb: AtomDB, s: Session, ds: Dataset) -> DatasetDetail:
    base = summary(s, ds).model_dump()
    return DatasetDetail(
        **base,
        description=ds.description,
        updated_at=ds.updated_at,
        source_text=ds.source_text,
        files=[DatasetFileOut(role=df.role, file=file_out(adb, df.file)) for df in ds.files],
        notes=[n.text for n in ds.notes],
    )


def list_datasets(
    s: Session, offset: int = 0, limit: int = 50, ids: list[str] | None = None, tag: str | None = None
) -> tuple[int, list[Dataset]]:
    q = select(Dataset)
    if ids is not None:
        q = q.where(Dataset.id.in_(ids))
    if tag:
        q = q.join(NodeTag, NodeTag.node_id == Dataset.id).join(Tag).where(Tag.name == tag)
    total = s.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    rows = s.execute(q.order_by(Dataset.created_at.desc()).offset(offset).limit(limit)).scalars()
    return total, list(rows)


def add_tags(s: Session, node: Node, names: list[str]) -> None:
    for name in names:
        name = name.strip()
        if not name or any(t.name == name for t in node.tags):
            continue
        tag = s.execute(select(Tag).where(Tag.name == name)).scalar_one_or_none()
        if tag is None:
            tag = Tag(name=name)
            s.add(tag)
            s.flush()
        node.tags.append(tag)
