from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...schemas import DatasetDetail
from ...services import AtomDB
from ...services import datasets as dsvc
from ..deps import get_adb

router = APIRouter(tags=["datasets"])


@router.get("/datasets")
def list_datasets(
    q: str = "",
    offset: int = 0,
    limit: int = Query(50, le=1000),
    sort: str = "-created",
    adb: AtomDB = Depends(get_adb),
):
    """Dataset summaries with key provenance facets; `q` uses the search language."""
    from ...services.search import search

    with adb.session() as s:
        return search(s, q, offset=offset, limit=limit, sort=sort)


@router.get("/datasets/{ds_id}", response_model=DatasetDetail)
def get_dataset(ds_id: str, adb: AtomDB = Depends(get_adb)):
    with adb.session() as s:
        return dsvc.detail(adb, s, dsvc.get_dataset(s, ds_id))
