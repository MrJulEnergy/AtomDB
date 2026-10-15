"""Editing metadata from the web UI. Every change is audited with old and new values."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...services import AtomDB
from ...services import edit as esvc
from ...services.annotate import add_link
from ..deps import get_adb

router = APIRouter(tags=["edit"])


class NodeEdit(BaseModel):
    name: str | None = None
    label: str | None = Field(None, description="short display name; '' clears it")
    description: str | None = None
    tags: list[str] | None = Field(None, description="full tag list (replaces the current one)")
    note: str | None = Field(None, description="note to add")
    attrs: dict[str, Any] = Field(default_factory=dict,
                                  description="metadata to set; null removes a key")
    dft: dict[str, Any] | None = Field(None, description="DFT settings (calculations only)")
    md: dict[str, Any] | None = Field(None, description="MD settings (calculations only)")
    model: str | None = Field(None, description="MD driving model (name or ID; '' removes)")


@router.patch("/nodes/{node_id}")
def patch_node(node_id: str, req: NodeEdit, adb: AtomDB = Depends(get_adb)):
    """Edit name, description, tags, notes, metadata and (for calculations) DFT/MD settings."""
    with adb.session() as s:
        changes = esvc.edit_node(s, node_id, name=req.name, description=req.description,
                                 tags=req.tags, note=req.note, attrs=req.attrs,
                                 label=req.label)
        for sec in ("dft", "md"):
            values = getattr(req, sec)
            if values is not None or (sec == "md" and req.model is not None):
                changes.update(esvc.edit_calc(s, node_id, sec, values or {},
                                              model=req.model if sec == "md" else None))
    return {"id": node_id, "changes": changes}


@router.get("/datasets/{ds_id}/settings")
def get_settings(ds_id: str, adb: AtomDB = Depends(get_adb)):
    """The DFT calculation(s) that labeled the dataset and the MD run(s) that generated it."""
    with adb.session() as s:
        return esvc.dataset_settings(s, ds_id)


class SettingsEdit(BaseModel):
    values: dict[str, Any] = {}
    inputs: list[str] = Field(default_factory=list,
                              description="input datasets/files (e.g. the unlabeled structures)")
    model: str | None = None
    calc_id: str | None = None


@router.put("/datasets/{ds_id}/settings/{section}")
def put_settings(ds_id: str, section: str, req: SettingsEdit, adb: AtomDB = Depends(get_adb)):
    """Edit the dataset's DFT/MD calculation in place, or record one if there is none."""
    with adb.session() as s:
        return esvc.set_dataset_settings(s, ds_id, section, req.values, inputs=req.inputs,
                                         model=req.model, calc_id=req.calc_id)


class CopyRequest(BaseModel):
    source: str
    targets: list[str]
    parts: list[str] = list(esvc.COPY_PARTS)


@router.post("/copy-metadata")
def copy_metadata(req: CopyRequest, adb: AtomDB = Depends(get_adb)):
    """Apply the DFT/MD settings, metadata and tags of one dataset to many others."""
    with adb.session() as s:
        return esvc.copy_metadata(s, req.source, req.targets, req.parts)


class EdgeRequest(BaseModel):
    src: str
    relation: str
    dst: str
    role: str = ""


@router.post("/edges")
def post_edge(req: EdgeRequest, adb: AtomDB = Depends(get_adb)):
    """Add a provenance relation (as `atomdb relate`)."""
    with adb.session() as s:
        e = add_link(s, req.src, req.relation, req.dst, role=req.role)
        return {"src": e.src, "relation": e.relation, "dst": e.dst, "role": e.role}
