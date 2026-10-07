from __future__ import annotations

import shutil

from atomdb.services import datasets as dsvc
from atomdb.services.nodes import describe
from atomdb.services.provenance import provenance_graph
from atomdb.services.runimport import execute_run, plan_run
from atomdb.store.hashing import sha256_file


def copy_run(data_dir, tmp_path):
    d = tmp_path / "run_042"
    shutil.copytree(data_dir / "run_cp2k_laufband", d)
    return d


def test_plan_is_read_only_and_complete(data_dir, tmp_path):
    d = copy_run(data_dir, tmp_path)
    before = {p.name: sha256_file(p) for p in d.iterdir()}
    plan = plan_run(d, cluster="hawk")
    assert {p.name: sha256_file(p) for p in d.iterdir()} == before
    assert plan["calc"]["type"] == "dft"
    assert plan["calc"]["dft"]["functional"] == "r2SCAN"
    assert plan["calc"]["dft"]["code_version"] == "2025.1"
    assert sorted(j["job_id"] for j in plan["jobs"]) == ["4819231", "4819232", "4819240"]
    assert plan["laufband"]
    roles = {i["role"] for i in plan["inputs"]}
    assert {"cp2k_input", "structures"} <= roles
    assert [o["path"].split("/")[-1] for o in plan["outputs"]] == ["labeled.h5"]
    assert any("TIMEOUT" in w for w in plan["warnings"])
    assert any("did NOT converge" in w for w in plan["warnings"])


def test_execute_run_creates_full_provenance(adb, data_dir, tmp_path):
    d = copy_run(data_dir, tmp_path)
    res = execute_run(adb, plan_run(d, cluster="hawk"))
    assert len(res["jobs"]) == 3
    with adb.session() as s:
        calc = describe(adb, s, dsvc.get_node(s, res["calculation"]))
        assert calc["dft"]["dispersion"] == "D4"
        assert calc["dft"]["cutoff_ry"] == 1000
        assert calc["code_params"]["FORCE_EVAL/DFT/SCF/EPS_SCF"] == "1.0E-6"
        assert calc["parameters"]["laufband"] is True
        assert calc["started_at"].year == 2026
        out = res["datasets"]["outputs"][0]
        g = provenance_graph(adb, s, out, "up")
        kinds = {n["kind"] for n in g["nodes"]}
        assert {"calculation", "hpc_job", "dataset", "file"} <= kinds
        jobs = [n for n in g["nodes"] if n["kind"] == "hpc_job"]
        assert {j["subtitle"] for j in jobs} >= {"hawk · cpu · job 4819240"}
        job = describe(adb, s, dsvc.get_node(s, res["jobs"][0]))
        assert job["n_cpus"] == 64 and job["mem"] == "200G"
        # every file of the run dir is preserved in the store
        stored = {f.sha256 for f in [dsvc.get_node(s, fid) for fid in res["files"]]}
    for p in d.iterdir():
        if p.name not in ("labeled.h5", "structures.extxyz"):
            assert sha256_file(p) in stored, p.name


def test_reimport_is_detected(adb, data_dir, tmp_path):
    d = copy_run(data_dir, tmp_path)
    first = execute_run(adb, plan_run(d))
    second = execute_run(adb, plan_run(d))
    assert second["calculation"] == first["calculation"]
    assert second["jobs"] == first["jobs"]
    assert any("already imported" in w for w in second["warnings"])
