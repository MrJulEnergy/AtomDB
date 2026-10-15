"""Write/maintenance actions. Paths are paths on the machine running the server."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...services import AtomDB
from ..deps import get_adb

router = APIRouter(tags=["actions"])


class ImportRequest(BaseModel):
    path: str
    name: str | None = None
    description: str | None = None
    tags: list[str] = []
    format: str | None = None
    read_options: dict = {}
    metadata: dict = Field(default_factory=dict, description=(
        "provenance shortcuts as for `atomdb import`: temperature, model, source, "
        "sampled_from, functional, dispersion, code, ..."))


@router.post("/import")
def do_import(req: ImportRequest, adb: AtomDB = Depends(get_adb)):
    from ...services.annotate import apply_annotations
    from ...services.importer import import_file

    with adb.session() as s:
        r = import_file(adb, req.path, name=req.name, description=req.description, tags=req.tags,
                        fmt=req.format, read_options=req.read_options, session=s)
        created = []
        if req.metadata and not r.duplicate:
            created = apply_annotations(s, r.dataset.id, dict(req.metadata))
    return {"result": r, "recorded": created}


class RunRequest(BaseModel):
    path: str
    execute: bool = False
    cluster: str | None = None
    calc_type: str | None = None
    inputs: list[str] = []
    sampled_from: str | None = None
    model: str | None = None


@router.post("/import-run")
def do_import_run(req: RunRequest, adb: AtomDB = Depends(get_adb)):
    """Plan (default) or execute (`execute: true`) the import of a calculation directory.

    An IPSuite project root returns `{runs: [{plan, result?}, ...]}` (one per node).
    """
    from ...services.runimport import execute_run, is_ipsuite_project, plan_project, plan_run

    def run(plan: dict) -> dict:
        out: dict = {"plan": plan}
        if req.execute:
            out["result"] = execute_run(adb, plan, input_datasets=req.inputs,
                                        sampled_from=req.sampled_from, model=req.model,
                                        calc_type=req.calc_type)
        return out

    if is_ipsuite_project(req.path):
        return {"runs": [run(p) for p in plan_project(req.path, cluster=req.cluster)]}
    return run(plan_run(req.path, cluster=req.cluster))


class ConvertRequest(BaseModel):
    input: str
    output: str
    to: str | None = None
    allow_loss: bool = False


@router.post("/convert")
def do_convert(req: ConvertRequest, adb: AtomDB = Depends(get_adb)):
    from ...services.converter import convert

    return convert(adb, req.input, req.output, to=req.to, allow_loss=req.allow_loss)


class PathRequest(BaseModel):
    path: str


@router.post("/whatis")
def do_whatis(req: PathRequest, adb: AtomDB = Depends(get_adb)):
    from ...services.whatis import whatis

    return whatis(adb, req.path)


@router.post("/validate")
def do_validate(dataset: str | None = None, quick: bool = True, adb: AtomDB = Depends(get_adb)):
    from ...services.validate import validate

    return validate(adb, dataset, quick=quick)


@router.post("/backup")
def do_backup(adb: AtomDB = Depends(get_adb)):
    from ...services.backup import backup

    return backup(adb)


@router.get("/store")
def store_info(adb: AtomDB = Depends(get_adb)):
    """Store location, size and existing backups."""
    files = adb.paths.files
    size = sum(p.stat().st_size for p in files.rglob("*") if p.is_file()) if files.exists() else 0
    backups = sorted(
        ({"name": p.name, "size_bytes": p.stat().st_size} for p in adb.paths.backups.glob("*.tar.zst")),
        key=lambda b: b["name"], reverse=True)
    return {"root": str(adb.paths.root), "db": str(adb.paths.db),
            "db_size_bytes": adb.paths.db.stat().st_size, "files_size_bytes": size,
            "instance_uuid": adb.db.instance_uuid(), "backups": backups}


@router.get("/schema")
def get_schema():
    from ...services.schema_doc import schema

    return schema()
