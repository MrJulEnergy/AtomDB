from __future__ import annotations

import ase
import ase.io
import numpy as np
import pytest
from fastapi.testclient import TestClient

from atomdb.api.app import create_app
from atomdb.errors import InvalidInput
from atomdb.services import tags as tsvc
from atomdb.services.datasets import add_tags, get_dataset
from atomdb.services.importer import import_file
from atomdb.services.records import record_dft
from atomdb.services.search import category_counts, facets_for, search


def _box(path, seed, pbc=True):
    rng = np.random.default_rng(seed)
    ase.io.write(path, [ase.Atoms("OH2", positions=rng.random((3, 3)) * 3, cell=[6, 6, 6],
                                  pbc=pbc) for _ in range(3)])
    return path


@pytest.fixture
def two(adb, tmp_path):
    a = import_file(adb, _box(tmp_path / "a.extxyz", 1)).dataset.id
    b = import_file(adb, _box(tmp_path / "b.extxyz", 2, pbc=False)).dataset.id
    return a, b


def test_kinds_drive_chips_and_categories(adb, two):
    a, _ = two
    with adb.session() as s:
        add_tags(s, get_dataset(s, a), ["water", "kcl_explore", "unreliable", "misc"])
    with adb.session() as s:
        kinds = {t["name"]: t["kind"] for t in tsvc.list_tags(s)}
        assert kinds == {"water": "system", "kcl_explore": "free", "unreliable": "quality",
                         "misc": "free"}
        tsvc.set_tag(s, "kcl_explore", kind="project", description="KCl exploration")
    with adb.session() as s:
        f = facets_for(s, [a])[a]
        assert f["system"] == ["water"] and f["project"] == ["kcl_explore"]
        assert f["quality"] == ["unreliable"] and "misc" not in str(f.get("project"))
        item = search(s, f"id:{a}")["items"][0]
        chips = {(c["kind"], c["text"]) for c in item.chips}
        assert {("system", "water"), ("project", "kcl_explore"), ("warn", "unreliable")} <= chips
        assert not any(c["text"] == "misc" for c in item.chips)
        assert {x["value"] for x in category_counts(s)["project"]} == {"kcl_explore"}
        assert {i.id for i in search(s, "project:kcl_explore")["items"]} == {a}
        with pytest.raises(InvalidInput):
            tsvc.set_tag(s, "misc", kind="colourful")


def test_aliases_and_merge(adb, two):
    a, b = two
    with adb.session() as s:
        add_tags(s, get_dataset(s, a), ["peptides"])
        add_tags(s, get_dataset(s, b), ["Peptide"])
        add_tags(s, get_dataset(s, b), ["PEPTIDE"])  # same tag, any case
        assert [t.name for t in get_dataset(s, b).tags] == ["Peptide"]
    with adb.session() as s:
        r = tsvc.merge(s, "peptides", "peptide")
        assert r["moved"] == 1
    with adb.session() as s:
        listed = {t["name"]: t for t in tsvc.list_tags(s)}
        assert "peptides" not in listed and listed["Peptide"]["aliases"] == ["peptides"]
        assert listed["Peptide"]["count"] == 2
        # typing the alias (any case) tags the canonical tag
        add_tags(s, get_dataset(s, a), ["PEPTIDES"])
        assert [t.name for t in get_dataset(s, a).tags] == ["Peptide"]
        with pytest.raises(InvalidInput, match="used by"):
            tsvc.delete_tag(s, "peptide")


def test_cleanup_classifies_and_applies(adb, two):
    a, b = two
    with adb.session() as s:
        record_dft(s, [a], dft={"code": "CP2K", "functional": "r2SCAN", "dispersion": "D4"})
        add_tags(s, get_dataset(s, a), ["pbc", "cp2k", "r2scan", "dft", "d4-added", "water",
                                        "cluster", "kcl_explore"])
        add_tags(s, get_dataset(s, b), ["dft", "revpbe0", "cluster"])
    with adb.session() as s:
        r = tsvc.cleanup(s)
    red = {(x["dataset"], x["tag"]) for x in r["redundant"]}
    assert red == {(a, "pbc"), (a, "cp2k"), (a, "r2scan"), (a, "dft"), (a, "d4-added"),
                   (b, "cluster")}
    assert {(x["dataset"], x["tag"]) for x in r["only_record"]} == {(b, "dft"), (b, "revpbe0")}
    assert {(x["dataset"], x["tag"]) for x in r["contradicts"]} == {(a, "cluster")}
    assert not r["applied"]
    with adb.session() as s:
        assert len(get_dataset(s, a).tags) == 8  # dry run changed nothing
        r = tsvc.cleanup(s, apply=True)
    with adb.session() as s:
        assert sorted(t.name for t in get_dataset(s, a).tags) == ["cluster", "kcl_explore", "water"]
        assert sorted(t.name for t in get_dataset(s, b).tags) == ["dft", "revpbe0"]
        assert {"pbc", "cp2k", "r2scan", "d4-added"} <= set(r["deleted_tags"])


def test_tags_api(adb, two):
    a, _ = two
    with adb.session() as s:
        add_tags(s, get_dataset(s, a), ["fps", "Fps-old"])
    c = TestClient(create_app(adb))
    body = c.get("/api/v1/tags").json()
    assert body["kinds"] == ["system", "project", "quality", "free"]
    assert {t["name"] for t in body["tags"]} == {"fps", "Fps-old"}
    assert c.patch("/api/v1/tags/Fps-old", json={"kind": "quality"}).json()["kind"] == "quality"
    assert c.post("/api/v1/tags/Fps-old/merge", json={"into": "fps"}).json()["moved"] == 0
    assert c.patch("/api/v1/tags/fps", json={"kind": "nope"}).status_code == 422
    assert "redundant" in c.get("/api/v1/tags/cleanup").json()


def test_tag_migration_on_populated_store(tmp_path):
    """The registry migration must work on a store that already has tagged nodes (a
    table rebuild is refused by SQLite while node_tag references tag)."""
    from alembic import command
    from sqlalchemy import text

    from atomdb.db.engine import alembic_config, make_engine, upgrade

    engine = make_engine(tmp_path / "atomdb.sqlite")
    command.upgrade(alembic_config(engine), "b7e2f4a91c3d")
    with engine.begin() as c:
        c.execute(text("INSERT INTO node (id, uuid, kind, name, status, created_at, updated_at) "
                       "VALUES ('DS-2026-000001', 'u1', 'dataset', 'x', 'active', "
                       "'2026-01-01', '2026-01-01')"))
        for i, name in enumerate(["water", "Water", "kcl_explore", "misc"], 1):
            c.execute(text("INSERT INTO tag (id, name) VALUES (:i, :n)"), {"i": i, "n": name})
            c.execute(text("INSERT INTO node_tag (node_id, tag_id) VALUES ('DS-2026-000001', :i)"),
                      {"i": i})
    upgrade(engine)
    with engine.connect() as c:
        rows = dict(c.execute(text("SELECT name, kind FROM tag WHERE alias_of IS NULL")).all())
        assert rows == {"water": "system", "kcl_explore": "project", "misc": "free"}
        assert c.execute(text("SELECT alias_of FROM tag WHERE name = 'Water'")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM node_tag")).scalar() == 3
        assert c.execute(text("PRAGMA foreign_key_check")).all() == []
