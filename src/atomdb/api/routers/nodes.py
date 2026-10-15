from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from ...db.models import Dataset, DatasetElement, Node
from ...services import AtomDB
from ...services import datasets as dsvc
from ...services.completeness import completeness, suggest_relations
from ...services.lineage import frame_lineage
from ...services.nodes import describe
from ...services.provenance import RELATIONS, provenance_graph
from ...services.search import category_counts, search, search_frames
from ..deps import get_adb

router = APIRouter()
KINDS = {"datasets": "dataset", "calculations": "calculation", "models": "model",
         "jobs": "hpc_job", "files": "file"}


@router.get("/nodes/{node_id}", tags=["nodes"])
def get_node(node_id: str, adb: AtomDB = Depends(get_adb)):
    """Full description of any node (dataset, calculation, model, HPC job, file)."""
    with adb.session() as s:
        return describe(adb, s, dsvc.get_node(s, node_id))


@router.get("/nodes", tags=["nodes"])
def list_nodes(kind: str = "calculations", offset: int = 0, limit: int = Query(50, le=1000),
               adb: AtomDB = Depends(get_adb)):
    with adb.session() as s:
        q = select(Node).where(Node.kind == KINDS.get(kind, kind))
        total = s.execute(select(func.count()).select_from(q.subquery())).scalar_one()
        rows = s.execute(q.order_by(Node.created_at.desc()).offset(offset).limit(limit)).scalars()
        return {"total": total, "offset": offset, "limit": limit,
                "items": [describe(adb, s, n, full=False) for n in rows]}


@router.get("/nodes/{node_id}/provenance", tags=["provenance"])
def get_provenance(node_id: str, direction: str = Query("up", pattern="^(up|down|both)$"),
                   depth: int = Query(50, ge=1, le=200), adb: AtomDB = Depends(get_adb)):
    """WHERE DID THIS COME FROM (up) / WHAT WAS THIS USED FOR (down)."""
    with adb.session() as s:
        node = dsvc.get_node(s, node_id)
        return provenance_graph(adb, s, node.id, direction, depth)


@router.get("/relations", tags=["provenance"])
def relations():
    return RELATIONS


@router.get("/datasets/{ds_id}/frames/{idx}/lineage", tags=["provenance"])
def get_frame_lineage(ds_id: str, idx: int, adb: AtomDB = Depends(get_adb)):
    with adb.session() as s:
        ds = dsvc.get_dataset(s, ds_id)
        chain = frame_lineage(s, ds.id, idx)
        for step in chain:
            n = s.get(Node, step["dataset"])
            step["dataset_name"] = n.name if n else None
            if step["via"]:
                v = s.get(Node, step["via"])
                step["via_name"] = v.name if v else None
        return chain


@router.get("/datasets/{ds_id}/completeness", tags=["provenance"])
def get_completeness(ds_id: str, adb: AtomDB = Depends(get_adb)):
    with adb.session() as s:
        return completeness(s, dsvc.get_dataset(s, ds_id))


@router.get("/suggestions", tags=["provenance"])
def get_suggestions(dataset: str | None = None, adb: AtomDB = Depends(get_adb)):
    with adb.session() as s:
        return suggest_relations(s, dataset)


@router.get("/search", tags=["search"])
def do_search(q: str = "", element: list[str] = Query(default=[]), any: bool = False,
              offset: int = 0, limit: int = Query(50, le=1000), sort: str = "-created",
              adb: AtomDB = Depends(get_adb)):
    """Dataset search. See the search language in docs/CLI.md."""
    with adb.session() as s:
        return search(s, q, elements=element, any_elements=any, offset=offset, limit=limit,
                      sort=sort)


@router.get("/categories", tags=["search"])
def categories(q: str = "", adb: AtomDB = Depends(get_adb)):
    """Dataset counts per browse category (geometry, data type, DFT code, MLIP family) for `q`."""
    with adb.session() as s:
        return category_counts(s, q)


@router.get("/frames", tags=["search"])
def do_search_frames(all: list[str] = Query(default=[]), any: list[str] = Query(default=[]),
                     dataset: list[str] = Query(default=[]), offset: int = 0,
                     limit: int = Query(100, le=5000), adb: AtomDB = Depends(get_adb)):
    """Frames containing all of / any of the given elements."""
    with adb.session() as s:
        return search_frames(s, all_elements=all or None, any_elements=any or None,
                             dataset_ids=dataset or None, offset=offset, limit=limit)


@router.get("/elements", tags=["search"])
def elements(adb: AtomDB = Depends(get_adb)):
    """Elements present in the database with dataset/frame/atom counts."""
    with adb.session() as s:
        rows = s.execute(
            select(DatasetElement.symbol, DatasetElement.z,
                   func.count(DatasetElement.dataset_id), func.sum(DatasetElement.n_frames),
                   func.sum(DatasetElement.n_atoms))
            .group_by(DatasetElement.symbol, DatasetElement.z).order_by(DatasetElement.z)
        ).all()
        n_ds = s.execute(select(func.count(Dataset.id))).scalar_one()
    return {"n_datasets": n_ds, "elements": [
        {"symbol": sym, "z": z, "datasets": nd, "frames": nf, "atoms": na}
        for sym, z, nd, nf, na in rows]}
