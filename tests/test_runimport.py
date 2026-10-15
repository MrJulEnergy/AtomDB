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


DVC_YAML = """\
stages:
  Pep-1_AddDataH5MD:
    cmd: zntrack run ipsuite.data_loading.add_data_h5md.AddDataH5MD --name Pep-1_AddDataH5MD
    deps:
    - data/structures.extxyz
    metrics:
    - nodes/Pep-1/AddDataH5MD/node-meta.json:
        cache: true
  Pep-1_ApplyCalculator:
    cmd: zntrack run ipsuite.calc.apply.ApplyCalculator --name Pep-1_ApplyCalculator
    deps:
    - nodes/Pep-1/AddDataH5MD/node-meta.json
    - r2scan/POTENTIAL
    metrics:
    - nodes/Pep-1/ApplyCalculator/node-meta.json:
        cache: true
    outs:
    - nodes/Pep-1/ApplyCalculator/frames.h5
    - nodes/Pep-1/ApplyCalculator/model
    params:
    - Pep-1_ApplyCalculator
    - r2scan/cp2k.yaml: null
"""
MAIN_SH = """\
#!/bin/bash
#SBATCH --job-name=cp2k
#SBATCH --partition=ant
#SBATCH --ntasks=24
#SBATCH --output=logs/%j.stdout
#SBATCH --error=logs/%j.stderr
module load cp2k/2025.1
export LAUFBAND_IDENTIFIER=${SLURM_JOB_ID}
for NODE in Pep-1_ApplyCalculator; do
  uv run zntrack run ipsuite.calc.apply.ApplyCalculator --name ${NODE}
done
"""


def make_ipsuite_project(data_dir, tmp_path):
    src = data_dir / "run_cp2k_laufband"
    root = tmp_path / "pep_project"
    node = root / "nodes" / "Pep-1" / "ApplyCalculator"
    for d in (root / "data", root / "r2scan", root / "logs", root / "nodes/Pep-1/AddDataH5MD"):
        d.mkdir(parents=True)
    (root / "dvc.yaml").write_text(DVC_YAML)
    (root / "zntrack.json").write_text("{}")
    (root / "params.yaml").write_text("Pep-1_ApplyCalculator:\n  dump_rate: 1\n")
    (root / "main.py").write_text("import ipsuite as ips\n")
    (root / "main.sh").write_text(MAIN_SH)
    (root / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "ipsuite"\nversion = "0.2.9"\n'
        'source = { git = "https://github.com/zincware/IPSuite.git#236f4acb1f405c09e45e" }\n')
    (root / "r2scan/cp2k.yaml").write_text("force_eval: {}\n")
    (root / "r2scan/POTENTIAL").write_text("H GTH-SCAN-q1\n")
    shutil.copy(src / "structures.extxyz", root / "data/structures.extxyz")
    (root / "nodes/Pep-1/AddDataH5MD/node-meta.json").write_text("{}")
    for job in ("2287859", "2287861"):
        (node / "model" / job).mkdir(parents=True)
        shutil.copy(src / "cp2k.inp", node / "model" / job / "cp2k.inp")
        shutil.copy(src / "cp2k.out", node / "model" / job / "cp2k_1.out")
        (node / "model" / job / "cp2k-RESTART.wfn").write_bytes(b"\0" * 64)
        (root / "logs" / f"{job}.stdout").write_text("Running Pep-1_ApplyCalculator\n")
    (root / "logs/2287861.stderr").write_text("*** JOB 2287861 CANCELLED DUE TO TIME LIMIT ***\n")
    shutil.copy(src / "labeled.h5", node / "frames.h5")
    (node / "node-meta.json").write_text("{}")
    return root


def test_ipsuite_project_import(adb, data_dir, tmp_path):
    from atomdb.services.importer import import_file
    from atomdb.services.runimport import is_ipsuite_project, plan_project

    root = make_ipsuite_project(data_dir, tmp_path)
    parent = import_file(adb, data_dir / "run_cp2k_laufband/structures.extxyz",
                         name="peptides", tags=["peptide"]).dataset.id
    assert is_ipsuite_project(root)
    (plan,) = plan_project(root, cluster="ant")
    assert plan["project"]["stage"] == "Pep-1_ApplyCalculator"
    assert plan["calc"]["type"] == "dft" and plan["calc"]["name"] == "Pep-1 CP2K r2SCAN D4"
    assert sorted(j["job_id"] for j in plan["jobs"]) == ["2287859", "2287861"]
    assert any("2287861: TIMEOUT" in w for w in plan["warnings"])
    assert plan["submit_script"]["partition"] == "ant"
    roles = {i["role"] for i in plan["inputs"]}
    assert {"structures", "cp2k_config", "cp2k_data", "cp2k_input"} <= roles
    assert [o["path"].split("/")[-1] for o in plan["outputs"]] == ["frames.h5"]
    assert plan["calc"]["software"]["ipsuite"] == "0.2.9+git236f4acb1f"
    assert not any(p.endswith(".wfn") for p in
                   [i["path"] for i in plan["aux"] + plan["inputs"]])

    res = execute_run(adb, plan)
    # the data file is the stored dataset → resolved, not imported twice
    assert res["datasets"]["inputs"] == [parent]
    (out,) = res["datasets"]["outputs"]
    with adb.session() as s:
        ds = describe(adb, s, dsvc.get_node(s, out))
        assert ds["name"] == "peptides | Pep-1 CP2K r2SCAN D4"
        assert "peptide" in ds["tags"]
        calc = describe(adb, s, dsvc.get_node(s, res["calculation"]))
        assert calc["parameters"]["ipsuite"]["stage"] == "Pep-1_ApplyCalculator"
        from atomdb.services.lineage import frame_parents
        assert frame_parents(s, out, 0)[0].parent_dataset == parent
    again = execute_run(adb, plan_project(root, cluster="ant")[0])
    assert again["calculation"] == res["calculation"]
