from __future__ import annotations

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from atomdb.api.app import create_app
from atomdb.cli.main import app

runner = CliRunner()


def run(*args):
    res = runner.invoke(app, list(args))
    return res


def test_cli_json_roundtrip(home, tmp_path):
    assert run("init", "--json").exit_code == 0
    f = tmp_path / "a.xyz"
    f.write_text("1\n\nH 0 0 0\n")
    res = run("import", str(f), "--json", "--tag", "demo")
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["ok"] is True
    ds_id = out["data"][0]["dataset"]["id"]
    info = json.loads(run("info", ds_id, "--json").output)
    assert info["data"]["id"] == ds_id
    assert info["data"]["files"][0]["file"]["origins"][0]["original_name"] == "a.xyz"
    ls = json.loads(run("ls", "--json").output)
    assert ls["data"]["total"] == 1


def test_cli_errors_are_structured(home):
    run("init")
    res = run("info", "DS-2026-000099", "--json")
    assert res.exit_code == 4
    out = json.loads(res.output)
    assert out["ok"] is False and out["error"]["code"] == "not_found"


def test_api_lists_datasets(adb, tmp_path):
    from atomdb.services.importer import import_file

    f = tmp_path / "a.xyz"
    f.write_text("1\n\nH 0 0 0\n")
    r = import_file(adb, f)
    client = TestClient(create_app(adb))
    page = client.get("/api/v1/datasets").json()
    assert page["total"] == 1 and page["items"][0]["id"] == r.dataset.id
    assert client.get(f"/api/v1/datasets/{r.dataset.id}").status_code == 200
    err = client.get("/api/v1/datasets/DS-2026-000777")
    assert err.status_code == 404 and err.json()["error"]["code"] == "not_found"


def test_cli_convert_reports_loss(home, data_dir, tmp_path):
    run("init")
    res = run("convert", str(data_dir / "kcl_labeled.extxyz"), str(tmp_path / "o.xyz"),
              "--to", "xyz", "--json")
    assert res.exit_code == 6
    assert "calc.forces" in json.loads(res.output)["error"]["details"]["lost"]
    res = run("convert", str(data_dir / "kcl_labeled.extxyz"), str(tmp_path / "o.h5"), "--json")
    assert res.exit_code == 0 and json.loads(res.output)["data"]["lost_fields"] == []


def test_cli_import_type_map(home, data_dir):
    run("init")
    res = run("import", str(data_dir / "kcl.dump"), "--type-map", "1=K,2=Cl", "--json")
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["data"][0]["dataset"]["elements"] == ["Cl", "K"]


def test_cli_link_and_whatis(home, data_dir, tmp_path):
    run("init")
    ds = json.loads(run("import", str(data_dir / "kcl_labeled.extxyz"), "--json").output)
    ds_id = ds["data"][0]["dataset"]["id"]
    res = run("link", ds_id, str(tmp_path), "--json")
    assert res.exit_code == 0, res.output
    link = tmp_path / "kcl_labeled.h5"
    assert link.is_symlink() and link.resolve().is_relative_to(home)
    res = run("link", ds_id, str(tmp_path / "orig.extxyz"), "--original", "--json")
    assert (tmp_path / "orig.extxyz").read_bytes() == (data_dir / "kcl_labeled.extxyz").read_bytes()
    w = json.loads(run("whatis", str(link), "--json").output)["data"]
    assert w["exact_match"]["datasets"][0]["id"] == ds_id
