"""Importing files into the store as datasets."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from ..db.models import Dataset, DatasetFile
from ..errors import InvalidInput
from ..ids import new_id
from ..schemas import ImportResult
from .context import AtomDB
from .datasets import add_tags, summary
from .files import store_file


def import_file(
    adb: AtomDB,
    path: str | Path,
    name: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    fmt: str | None = None,
) -> ImportResult:
    p = Path(path).expanduser()
    if not p.is_file():
        raise InvalidInput(f"file not found: {p}", path=str(p))
    with adb.session() as s:
        f, duplicate = store_file(adb, s, p, fmt=fmt)
        if duplicate:
            existing = s.execute(
                select(Dataset)
                .join(DatasetFile, DatasetFile.dataset_id == Dataset.id)
                .where(DatasetFile.file_id == f.id)
            ).scalars().first()
            if existing is not None:
                return ImportResult(
                    dataset=summary(s, existing),
                    file_id=f.id,
                    sha256=f.sha256,
                    duplicate=True,
                    message=f"already known: identical bytes stored as {f.id}, dataset {existing.id}",
                )
        ds = Dataset(
            id=new_id(s, "dataset"),
            name=name or p.stem,
            description=description,
            primary_file_id=f.id,
            created_by_cmd=None,
        )
        s.add(ds)
        s.flush()
        ds.files.append(DatasetFile(file_id=f.id, role="original"))
        add_tags(s, ds, tags or [])
        adb.audit(s, "dataset.import", ds.id, file_id=f.id, path=str(p.resolve()))
        return ImportResult(
            dataset=summary(s, ds),
            file_id=f.id,
            sha256=f.sha256,
            duplicate=duplicate,
            message=f"imported {p.name} as {ds.id}",
        )
