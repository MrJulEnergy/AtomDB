"""Tag registry: kinds (chip colour / sidebar group), descriptions, aliases, cleanup."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...services import AtomDB
from ...services import tags as tsvc
from ..deps import get_adb

router = APIRouter(tags=["tags"])


class TagEdit(BaseModel):
    kind: str | None = None
    description: str | None = None
    name: str | None = None  # rename


class TagMerge(BaseModel):
    into: str


@router.get("/tags")
def list_tags(adb: AtomDB = Depends(get_adb)):
    """All canonical tags with kind, description, aliases and usage count."""
    with adb.session() as s:
        return {"kinds": list(tsvc.KINDS), "tags": tsvc.list_tags(s)}


@router.patch("/tags/{name}")
def edit_tag(name: str, req: TagEdit, adb: AtomDB = Depends(get_adb)):
    with adb.session() as s:
        return tsvc.set_tag(s, name, kind=req.kind, description=req.description, rename=req.name)


@router.post("/tags/{name}/merge")
def merge_tag(name: str, req: TagMerge, adb: AtomDB = Depends(get_adb)):
    """Move every use of NAME to INTO; NAME stays as an alias (typing it tags INTO)."""
    with adb.session() as s:
        return tsvc.merge(s, name, req.into)


@router.delete("/tags/{name}")
def delete_tag(name: str, adb: AtomDB = Depends(get_adb)):
    with adb.session() as s:
        return tsvc.delete_tag(s, name)


@router.get("/tags/cleanup")
def cleanup_preview(adb: AtomDB = Depends(get_adb)):
    """Dry run: tags that only repeat facts derived from frames/provenance."""
    with adb.session() as s:
        r = tsvc.cleanup(s, apply=False)
        s.rollback()
        return r


@router.post("/tags/cleanup")
def cleanup_apply(adb: AtomDB = Depends(get_adb)):
    with adb.session() as s:
        return tsvc.cleanup(s, apply=True)
