from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...schemas import DatasetDetail, Page
from ...services import AtomDB
from ...services import datasets as dsvc
from ..deps import get_adb

router = APIRouter(tags=["datasets"])


@router.get("/datasets", response_model=Page)
def list_datasets(
    offset: int = 0,
    limit: int = Query(50, le=1000),
    tag: str | None = None,
    adb: AtomDB = Depends(get_adb),
):
    with adb.session() as s:
        total, rows = dsvc.list_datasets(s, offset=offset, limit=limit, tag=tag)
        items = [dsvc.summary(s, d) for d in rows]
    return Page(total=total, offset=offset, limit=limit, items=items)


@router.get("/datasets/{ds_id}", response_model=DatasetDetail)
def get_dataset(ds_id: str, adb: AtomDB = Depends(get_adb)):
    with adb.session() as s:
        return dsvc.detail(adb, s, dsvc.get_dataset(s, ds_id))
