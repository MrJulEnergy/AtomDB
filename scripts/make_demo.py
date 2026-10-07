"""Build a small demo store showing the full provenance cycle.

    uv run python scripts/make_demo.py /tmp/atomdb_demo
    ATOMDB_HOME=/tmp/atomdb_demo uv run atomdb serve

MD (MACE-MH-1, 400 K) → trajectory → uncertainty sampling → CP2K r2SCAN/D4 labeling on
an HPC cluster (3 Slurm jobs, laufband) → training set → KCl-MACE model → new MD.
Uses only the tiny test fixtures.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import ase.io

from atomdb.services import AtomDB
from atomdb.services.importer import import_file
from atomdb.services.records import ModelSpec, add_model, record_md, record_sampling, record_training
from atomdb.services.runimport import execute_run, plan_run

DATA = Path(__file__).resolve().parents[1] / "tests" / "data"


def main(home: Path) -> None:
    if home.exists() and any(home.iterdir()):
        sys.exit(f"{home} is not empty")
    adb = AtomDB.init(home)
    work = Path(tempfile.mkdtemp())
    traj = ase.io.read(DATA / "run_cp2k_laufband/structures.extxyz", ":") + ase.io.read(
        DATA / "kcl_plain.xyz", ":")
    for i, a in enumerate(traj):
        a.info["temperature"] = 400.0 + i
        a.info["time"] = i * 50.0
    ase.io.write(work / "md_mace_400K.extxyz", traj)
    run = work / "run_042"
    shutil.copytree(DATA / "run_cp2k_laufband", run)

    with adb.session() as s:
        md_ds = import_file(adb, work / "md_mace_400K.extxyz", name="KCl(aq) MD 400 K MACE-MH-1",
                            session=s).dataset.id
        record_md(s, [md_ds], model="MACE-MH-1",
                  md={"engine": "ASE", "ensemble": "NVT", "temperature_k": 400, "timestep_fs": 0.5,
                      "n_steps": 200000, "thermostat": "Langevin"},
                  enhanced={"method": "metad", "software": "PLUMED",
                            "collective_variables": [{"label": "d1", "action": "DISTANCE",
                                                      "ATOMS": "1,2"}],
                            "bias": {"PACE": 500, "HEIGHT": 1.2, "BIASFACTOR": 10}},
                  workdir="/work/jpeters/kcl/md_400K")
    plan = plan_run(run, cluster="hawk")
    res = execute_run(adb, plan)
    sel_ds = res["datasets"]["inputs"][0]
    lab_ds = res["datasets"]["outputs"][0]
    with adb.session() as s:
        record_sampling(s, md_ds, sel_ds, "uncertainty", criterion={"committee_std": 0.05})
        m = add_model(adb, s, ModelSpec(name="KCl-MACE-r2SCAN-v1", family="MACE", version="0.3.13",
                                        cutoff=5.0, package="mace-torch"))
        record_training(s, m.id, [lab_ds], hyperparameters={"r_max": 5.0, "max_L": 1,
                                                            "num_channels": 128})
    ase.io.write(work / "md_new.extxyz", ase.io.read(DATA / "kcl_variable.extxyz", ":"))
    with adb.session() as s:
        new = import_file(adb, work / "md_new.extxyz", name="KCl(aq) MD 330 K own model",
                          session=s).dataset.id
        record_md(s, [new], model=m.id, md={"ensemble": "NPT", "temperature_k": 330,
                                             "pressure_bar": 1.0})
    import_file(adb, DATA / "kcl.cif", name="KCl rock salt")
    adb.close()
    print(f"demo store at {home}\n  ATOMDB_HOME={home} uv run atomdb serve")


if __name__ == "__main__":
    main(Path(sys.argv[1]).expanduser().resolve())
