from __future__ import annotations

from atomdb.runparsers.plumed import parse_plumed

PLUMED = """
# KCl ion pair dissociation
UNITS LENGTH=A
d1: DISTANCE ATOMS=1,2
cn: COORDINATION GROUPA=1 GROUPB=3-50 R_0=3.5
METAD ...
  LABEL=metad
  ARG=d1,cn SIGMA=0.1,0.2 HEIGHT=1.2 PACE=500
  BIASFACTOR=10 TEMP=400 GRID_MIN=0,0 GRID_MAX=12,10
...
uwall: UPPER_WALLS ARG=d1 AT=10.0 KAPPA=150.0
PRINT ARG=d1,cn,metad.bias STRIDE=100 FILE=COLVAR
"""


def test_plumed_metad():
    r = parse_plumed(PLUMED)
    assert r["method"] == "metadynamics"
    assert [c["label"] for c in r["collective_variables"]] == ["d1", "cn"]
    assert r["collective_variables"][0]["ATOMS"] == "1,2"
    m = r["bias"]["metad"]
    assert m["PACE"] == 500 and m["BIASFACTOR"] == 10 and m["TEMP"] == 400
    assert m["SIGMA"] == [0.1, 0.2]
    assert r["bias"]["uwall"]["KAPPA"] == 150.0


def test_plumed_umbrella_and_opes():
    assert parse_plumed("d: DISTANCE ATOMS=1,2\nr: RESTRAINT ARG=d AT=3.0 KAPPA=500")[
        "method"] == "umbrella sampling"
    assert parse_plumed("d: DISTANCE ATOMS=1,2\nOPES_METAD ARG=d PACE=500 BARRIER=40")[
        "method"] == "OPES"
    assert parse_plumed("d: DISTANCE ATOMS=1,2\n")["method"] is None


def test_vocab_normalization():
    from atomdb import vocab

    for raw in ("r2scan", "R2SCAN", "r²SCAN", "MGGA_X_R2SCAN + MGGA_C_R2SCAN",
                "MGGA_C_R2SCAN + MGGA_X_R2SCAN"):
        assert vocab.functional(raw) == "r2SCAN", raw
    assert vocab.dispersion("DFTD3(BJ)") == "D3BJ"
    assert vocab.dispersion("dftd4") == "D4"
    assert vocab.dispersion("D3") == "D3"
    assert vocab.functional("my-custom-xc") == "my-custom-xc"


def test_cp2k_input(data_dir):
    from atomdb.runparsers.cp2k import parse_input, summarize_input

    d = data_dir / "run_cp2k_laufband"
    flat = parse_input((d / "cp2k.inp").read_text(), d)
    assert flat["FORCE_EVAL/DFT/MGRID/CUTOFF"] == "1000"  # @SET substituted
    s = summarize_input(flat)
    dft = s["dft"]
    assert dft["functional"] == "r2SCAN" and dft["dispersion"] == "D4"
    assert dft["cutoff_ry"] == 1000 and dft["rel_cutoff_ry"] == 60 and dft["eps_scf"] == 1e-6
    assert "GTH-SCAN-q9" in dft["pseudopotential"]
    assert "TZV2P-MOLOPT-SCAN-GTH-q7" in dft["basis"]
    assert s["labels"] == ["energy", "forces", "stress"]


def test_cp2k_input_aimd_pbe_d3():
    from atomdb.runparsers.cp2k import parse_input, summarize_input

    inp = """&GLOBAL
  RUN_TYPE MD
&END GLOBAL
&MOTION
  &MD
    ENSEMBLE NVT
    STEPS 2000
    TIMESTEP 0.5
    TEMPERATURE 330
    &THERMOSTAT
      TYPE CSVR
    &END THERMOSTAT
  &END MD
&END MOTION
&FORCE_EVAL
  &DFT
    &XC
      &XC_FUNCTIONAL PBE
      &END XC_FUNCTIONAL
      &VDW_POTENTIAL
        POTENTIAL_TYPE PAIR_POTENTIAL
        &PAIR_POTENTIAL
          TYPE DFTD3(BJ)
        &END PAIR_POTENTIAL
      &END VDW_POTENTIAL
    &END XC
  &END DFT
&END FORCE_EVAL
"""
    s = summarize_input(parse_input(inp))
    assert s["dft"]["functional"] == "PBE" and s["dft"]["dispersion"] == "D3BJ"
    assert s["md"] == {"engine": "CP2K", "ensemble": "NVT", "temperature_k": 330.0,
                       "timestep_fs": 0.5, "n_steps": 2000, "thermostat": "CSVR"}


def test_cp2k_output(data_dir):
    from atomdb.runparsers.cp2k import parse_output

    o = parse_output((data_dir / "run_cp2k_laufband/cp2k.out").read_text())
    assert o["code_version"] == "2025.1" and o["git_revision"] == "git:abc1234"
    assert o["n_force_evals"] == 3 and o["scf_not_converged"] == 1
    assert o["started_at"].hour == 9 and o["ended_at"].hour == 15
    assert o["hosts"] == ["node0421"]


def test_slurm_script(data_dir):
    from atomdb.runparsers.slurm import parse_job_output, parse_script

    s = parse_script((data_dir / "run_cp2k_laufband/submit.sh").read_text())
    assert s["partition"] == "cpu" and s["account"] == "my_account"
    assert s["n_cpus"] == 64 and s["time_limit"] == "24:00:00" and s["mem"] == "200G"
    assert s["modules"] == ["cp2k/2025.1"]
    j = parse_job_output("slurm-4819240.out", "CANCELLED AT ... DUE TO TIME LIMIT")
    assert j == {"job_id": "4819240", "array_task_id": None, "state": "TIMEOUT",
                 "file": "slurm-4819240.out"}
    assert parse_job_output("slurm-123_7.out", "")["array_task_id"] == "7"
    gpu = parse_script("#SBATCH --gres=gpu:a100:4\n#SBATCH -p gpu\n")
    assert gpu["n_gpus"] == 4 and gpu["gpu_type"] == "a100" and gpu["partition"] == "gpu"
