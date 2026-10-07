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


@router.get("/datasets/{ds_id}/frames")
def frames(ds_id: str, offset: int = 0, limit: int = Query(200, le=10000),
           adb: AtomDB = Depends(get_adb)):
    """Frame index (per-frame energy, fmax, temperature, ... — no coordinates)."""
    from ...services.framedata import list_frames

    with adb.session() as s:
        return list_frames(s, ds_id, offset, limit)


@router.get("/datasets/{ds_id}/frames/{idx}")
def frame(ds_id: str, idx: int, adb: AtomDB = Depends(get_adb)):
    """One frame with all data (positions, cell, forces, info, arrays). Loads only this frame."""
    from ...services.framedata import get_frame

    with adb.session() as s:
        return get_frame(adb, s, ds_id, idx)


@router.get("/datasets/{ds_id}/properties")
def properties(ds_id: str, adb: AtomDB = Depends(get_adb)):
    """Catalog of every field stored in the dataset (with units and label source)."""
    from sqlalchemy import select

    from ...db.models import PropertyDef

    with adb.session() as s:
        ds = dsvc.get_dataset(s, ds_id)
        rows = s.execute(select(PropertyDef).where(PropertyDef.dataset_id == ds.id)
                         .order_by(PropertyDef.origin, PropertyDef.name)).scalars()
        return [{c.key: getattr(r, c.key) for c in r.__table__.columns if c.key != "dataset_id"}
                for r in rows]


@router.get("/datasets/{ds_id}/stats")
def stats(ds_id: str, recompute: bool = False, adb: AtomDB = Depends(get_adb)):
    """Streaming statistics + histograms (energy, E/atom, forces incl. per element, cell, ...)."""
    from ...services.stats import dataset_stats

    with adb.session() as s:
        return dataset_stats(adb, s, ds_id, recompute=recompute)


@router.get("/compare")
def compare(ds: list[str] = Query(...), adb: AtomDB = Depends(get_adb)):
    """Several datasets on common histogram bins, plus shift/overlap summary."""
    from ...errors import InvalidInput
    from ...services.stats import compare as do_compare

    if len(ds) < 2:
        raise InvalidInput("compare needs at least two datasets (?ds=A&ds=B)")
    with adb.session() as s:
        return do_compare(adb, s, ds)


@router.get("/embedding", tags=["exploratory"])
def embedding(ds: list[str] = Query(...), descriptor: str = "composition-rdf",
              max_frames: int = Query(300, le=5000), seed: int = 0,
              adb: AtomDB = Depends(get_adb)):
    """EXPLORATORY descriptor + PCA projection (not stored)."""
    from ...services.embedding import embed

    with adb.session() as s:
        return embed(adb, s, ds, descriptor, max_frames, seed)


@router.get("/similarity", tags=["exploratory"])
def similarity(reference: str, other: str, descriptor: str = "composition-rdf",
               max_frames: int = Query(1000, le=10000), adb: AtomDB = Depends(get_adb)):
    """Descriptor-space novelty of OTHER relative to REFERENCE + distribution shifts."""
    from ...services.embedding import similarity as sim

    with adb.session() as s:
        return sim(adb, s, reference, other, descriptor, max_frames)


@router.get("/descriptors", tags=["exploratory"])
def descriptors():
    from ...descriptors import list_descriptors

    return list_descriptors()
