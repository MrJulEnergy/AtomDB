from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from atomdb.api.app import create_app
from atomdb.db.models import AuditLog
from atomdb.services.importer import import_file


def _two(adb, data_dir):
    a = import_file(adb, data_dir / "kcl_labeled.extxyz").dataset.id
    b = import_file(adb, data_dir / "kcl_variable.extxyz").dataset.id
    return a, b


def test_settings_create_then_edit_in_place(adb, data_dir):
    a, _ = _two(adb, data_dir)
    c = TestClient(create_app(adb))
    r = c.put(f"/api/v1/datasets/{a}/settings/dft",
              json={"values": {"code": "CP2K", "functional": "r2scan", "basis": "TZV2P",
                               "cutoff_ry": "2000", "labels": "energy,forces,stress"}})
    assert r.status_code == 200, r.text
    calc = r.json()["calc_id"]
    assert r.json()["created"]
    # second save edits the same calculation instead of adding another one
    r = c.put(f"/api/v1/datasets/{a}/settings/dft",
              json={"values": {"basis": "DZVP", "dispersion": "d3(bj)"}})
    assert r.json() == {"calc_id": calc, "created": False,
                        "changes": {"basis": ["TZV2P", "DZVP"], "dispersion": [None, "D3BJ"]},
                        "also_affects": []}
    st = c.get(f"/api/v1/datasets/{a}/settings").json()
    assert len(st["dft"]) == 1 and st["dft"][0]["values"]["cutoff_ry"] == 2000.0
    assert st["dft"][0]["name"] == "CP2K/r2SCAN/D3BJ"  # auto name follows the settings
    assert c.get("/api/v1/search", params={"q": "disp:D3BJ"}).json()["total"] == 1
    bad = c.put(f"/api/v1/datasets/{a}/settings/dft", json={"values": {"cutoff_ry": "lots"}})
    assert bad.status_code == 422
    with adb.session() as s:
        log = s.execute(select(AuditLog).where(AuditLog.action == "edit.dft")).scalar_one()
        assert log.payload["changes"]["basis"] == ["TZV2P", "DZVP"]


def test_patch_node_fields_and_md_model(adb, data_dir):
    a, _ = _two(adb, data_dir)
    c = TestClient(create_app(adb))
    r = c.patch(f"/api/v1/nodes/{a}", json={"name": "kcl", "tags": ["x", "y"], "note": "hi",
                                            "attrs": {"config": "r2scan.yaml"}})
    assert r.status_code == 200, r.text
    n = c.get(f"/api/v1/nodes/{a}").json()
    assert n["name"] == "kcl" and n["tags"] == ["x", "y"] and n["notes"] == ["hi"]
    c.patch(f"/api/v1/nodes/{a}", json={"tags": ["y"], "attrs": {"config": None}})
    n = c.get(f"/api/v1/nodes/{a}").json()
    assert n["tags"] == ["y"] and "config" not in n["attrs"]
    md = c.put(f"/api/v1/datasets/{a}/settings/md",
               json={"values": {"temperature_k": 400}, "model": "MACE-MH-1"}).json()["calc_id"]
    r = c.patch(f"/api/v1/nodes/{md}", json={"md": {"ensemble": "nvt"}, "model": "apax-1"})
    assert r.json()["changes"]["ensemble"] == [None, "NVT"]
    st = c.get(f"/api/v1/datasets/{a}/settings").json()["md"][0]
    assert st["model"] == "apax-1" and st["name"] == "MD NVT 400 K (apax-1)"
    assert c.patch(f"/api/v1/nodes/{md}", json={"dft": {"code": "x"}}).status_code == 422


def test_copy_metadata_to_many(adb, data_dir):
    a, b = _two(adb, data_dir)
    c = TestClient(create_app(adb))
    src = c.put(f"/api/v1/datasets/{a}/settings/dft",
                json={"values": {"code": "CP2K", "functional": "r2scan", "eps_scf": 1e-8}}
                ).json()["calc_id"]
    c.patch(f"/api/v1/nodes/{a}", json={"tags": ["kcl"],
                                        "attrs": {"config": "cp2k.yaml", "source_directory": "/x"}})
    r = c.post("/api/v1/copy-metadata", json={"source": a, "targets": [b, a]})
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert [x["dataset"] for x in res] == [b] and res[0]["dft"] != src
    st = c.get(f"/api/v1/datasets/{b}/settings").json()["dft"][0]["values"]
    assert st["functional"] == "r2SCAN" and st["eps_scf"] == 1e-8
    n = c.get(f"/api/v1/nodes/{b}").json()
    assert n["tags"] == ["kcl"] and set(n["attrs"]) == {"config"}
    # copying again updates the target's calculation, it does not add a second one
    c.put(f"/api/v1/datasets/{a}/settings/dft", json={"values": {"basis": "DZVP"}})
    c.post("/api/v1/copy-metadata", json={"source": a, "targets": [b], "parts": ["dft"]})
    st = c.get(f"/api/v1/datasets/{b}/settings").json()["dft"]
    assert len(st) == 1 and st[0]["values"]["basis"] == "DZVP"
    assert c.post("/api/v1/copy-metadata", json={"source": a, "targets": [b],
                                                 "parts": ["bogus"]}).status_code == 422


def test_post_edge(adb, data_dir):
    a, b = _two(adb, data_dir)
    c = TestClient(create_app(adb))
    r = c.post("/api/v1/edges", json={"src": b, "relation": "derived_from", "dst": a})
    assert r.status_code == 200, r.text
    assert c.post("/api/v1/edges", json={"src": b, "relation": "nope", "dst": a}).status_code == 422


def test_shared_calculation_is_reported(adb, data_dir):
    from atomdb.services.records import record_dft

    a, b = _two(adb, data_dir)
    with adb.session() as s:
        record_dft(s, [a, b], dft={"code": "CP2K"})
    c = TestClient(create_app(adb))
    assert c.get(f"/api/v1/datasets/{a}/settings").json()["dft"][0]["shared_with"] == [b]
    r = c.put(f"/api/v1/datasets/{a}/settings/dft", json={"values": {"basis": "DZVP"}}).json()
    assert r["also_affects"] == [b]


def test_label_set_search_clear_and_chips(adb, data_dir):
    a, _ = _two(adb, data_dir)
    c = TestClient(create_app(adb))
    r = c.patch(f"/api/v1/nodes/{a}", json={"label": "KCl short", "tags": ["water", "KCl"]})
    assert r.json()["changes"]["label"] == [None, "KCl short"]
    d = c.get(f"/api/v1/datasets/{a}").json()
    assert d["label"] == "KCl short"
    kinds = [ch["kind"] for ch in d["chips"]]
    assert kinds[:2] == ["system", "system"] and "size" in kinds
    assert c.get("/api/v1/search", params={"q": "label:short"}).json()["total"] == 1
    assert c.get("/api/v1/search", params={"q": "short"}).json()["total"] == 1
    assert c.patch(f"/api/v1/nodes/{a}", json={"label": "x" * 80}).status_code == 422
    c.patch(f"/api/v1/nodes/{a}", json={"label": ""})
    assert c.get(f"/api/v1/datasets/{a}").json()["label"] is None


def test_suggest_label_strips_method():
    from types import SimpleNamespace

    from atomdb.services.labels import suggest_label

    ds = SimpleNamespace(name="KCl/water clusters (8 A) from 18 PET-MAD-S MDs (0/2/5 mol/kg) "
                              "/ r2SCAN vacuum (ORCA)")
    facets = {"model": ["PET-MAD-S"], "functional": ["r2SCAN"], "code": ["ORCA"]}
    assert suggest_label(ds, facets) == "KCl/water clusters (8 A) (0/2/5 mol/kg)"
    ds = SimpleNamespace(name="MPtrj subset: pure Si (559)")
    assert suggest_label(ds, {}) == "MPtrj subset: pure Si"
