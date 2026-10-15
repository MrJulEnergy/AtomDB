"""Generate the tiny committed test fixtures in tests/data/.

    uv run python tests/make_fixtures.py

Deterministic (fixed seed). Real scientific data must never be committed.
"""

from __future__ import annotations

from pathlib import Path

import ase
import ase.io
import numpy as np
import znh5md
from ase.calculators.singlepoint import SinglePointCalculator

OUT = Path(__file__).parent / "data"
rng = np.random.default_rng(42)


def kcl_water(n_water: int, seed_shift: float = 0.0) -> ase.Atoms:
    symbols = ["K", "Cl"] + ["O", "H", "H"] * n_water
    n = len(symbols)
    pos = rng.random((n, 3)) * 8.0 + seed_shift
    return ase.Atoms(symbols, positions=pos, cell=np.diag([8.0, 8.0, 8.0]), pbc=True)


def labeled(atoms: ase.Atoms, i: int) -> ase.Atoms:
    n = len(atoms)
    atoms.info.update({
        "config_type": "md" if i % 2 == 0 else "sampled",
        "temperature": 400.0,
        "dipole_custom": rng.random(3),
        "step": i * 10,
    })
    atoms.arrays["charges_custom"] = rng.normal(size=n)
    atoms.calc = SinglePointCalculator(
        atoms,
        energy=-100.0 - i * 0.5 - rng.random(),
        forces=rng.normal(scale=0.5, size=(n, 3)),
        stress=rng.normal(scale=0.01, size=6),
    )
    return atoms


CP2K_INP = """\
@SET CUTOFF 1000
&GLOBAL
  PROJECT kcl_r2scan
  RUN_TYPE ENERGY_FORCE
  PRINT_LEVEL LOW
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  STRESS_TENSOR ANALYTICAL
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT_UZH
    POTENTIAL_FILE_NAME POTENTIAL_UZH
    CHARGE 0
    &MGRID
      CUTOFF ${CUTOFF}
      REL_CUTOFF 60
    &END MGRID
    &SCF
      EPS_SCF 1.0E-6
      MAX_SCF 50
      &OT
        MINIMIZER DIIS
      &END OT
    &END SCF
    &XC
      &XC_FUNCTIONAL
        &MGGA_X_R2SCAN
        &END MGGA_X_R2SCAN
        &MGGA_C_R2SCAN
        &END MGGA_C_R2SCAN
      &END XC_FUNCTIONAL
      &VDW_POTENTIAL
        POTENTIAL_TYPE PAIR_POTENTIAL
        &PAIR_POTENTIAL
          TYPE DFTD4
          REFERENCE_FUNCTIONAL r2SCAN
        &END PAIR_POTENTIAL
      &END VDW_POTENTIAL
    &END XC
  &END DFT
  &SUBSYS
    &KIND K
      BASIS_SET TZV2P-MOLOPT-SCAN-GTH-q9
      POTENTIAL GTH-SCAN-q9
    &END KIND
    &KIND Cl
      BASIS_SET TZV2P-MOLOPT-SCAN-GTH-q7
      POTENTIAL GTH-SCAN-q7
    &END KIND
    &KIND O
      BASIS_SET TZV2P-MOLOPT-SCAN-GTH-q6
      POTENTIAL GTH-SCAN-q6
    &END KIND
    &KIND H
      BASIS_SET TZV2P-MOLOPT-SCAN-GTH-q1
      POTENTIAL GTH-SCAN-q1
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
"""

SUBMIT = """\
#!/bin/bash
#SBATCH --job-name=kcl_r2scan_label
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=64
#SBATCH --cpus-per-task=1
#SBATCH --time=24:00:00
#SBATCH --mem=200G
#SBATCH -A my_account
module load cp2k/2025.1
export OMP_NUM_THREADS=1
# many jobs of this script run concurrently; laufband hands out frames
srun python label.py --input structures.extxyz --output labeled.h5
"""

CP2K_OUT = """\
 **** **** ******  **  PROGRAM STARTED AT               2026-08-17 09:12:01.123
 ***** ** ***  *** **   PROGRAM STARTED ON                          node0421
 **    ****   ******    PROGRAM STARTED BY                           user
 ***** **    ** ** **   PROGRAM PROCESS ID                            123456
  **** **  *******  **  PROGRAM STARTED IN /scratch/user/kcl/run_042

 CP2K| version string:                                      CP2K version 2025.1
 CP2K| source code revision number:                                  git:abc1234
 GLOBAL| Run type                                                  ENERGY_FORCE
  *** SCF run converged in    14 steps ***
 ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]              -540.123456789012
  *** SCF run converged in    13 steps ***
 ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]              -540.223456789012
  *** SCF run NOT converged ***
 ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]              -540.023456789012
 **** **** ******  **  PROGRAM ENDED AT                 2026-08-17 15:40:12.001
"""


def make_run_dir(d: Path) -> None:
    d.mkdir(exist_ok=True)
    (d / "cp2k.inp").write_text(CP2K_INP)
    (d / "submit.sh").write_text(SUBMIT)
    (d / "label.py").write_text("# driver: laufband loop, ASE CP2K calculator\n")
    (d / "cp2k.out").write_text(CP2K_OUT)
    (d / "slurm-4819231.out").write_text("worker started\nlabeled 2 frames\n")
    (d / "slurm-4819232.out").write_text("worker started\nlabeled 1 frame\n")
    (d / "slurm-4819240.out").write_text(
        "worker started\nslurmstepd: error: *** JOB 4819240 ON node0433 CANCELLED AT "
        "2026-08-18T09:12:01 DUE TO TIME LIMIT ***\n")
    (d / "laufband.sqlite").write_bytes(b"SQLite format 3\x00" + bytes(64))
    unlabeled = [kcl_water(2, 0.3) for _ in range(3)]
    ase.io.write(d / "structures.extxyz", unlabeled)
    out = d / "labeled.h5"
    if out.exists():
        out.unlink()
    labeled = []
    for i, a in enumerate(unlabeled):
        b = a.copy()
        b.calc = SinglePointCalculator(b, energy=-540.1 * 27.211 - i,
                                       forces=rng.normal(scale=0.3, size=(len(b), 3)),
                                       stress=rng.normal(scale=0.01, size=6))
        labeled.append(b)
    znh5md.IO(str(out)).extend(labeled)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    # plain xyz: 3 frames, positions only
    plain = [kcl_water(2) for _ in range(3)]
    for a in plain:
        a.cell = None
        a.pbc = False
    ase.io.write(OUT / "kcl_plain.xyz", plain, format="xyz")

    # extxyz with labels and arbitrary metadata
    lab = [labeled(kcl_water(2), i) for i in range(4)]
    ase.io.write(OUT / "kcl_labeled.extxyz", lab, format="extxyz")

    # variable atom count, partial pbc
    var = [labeled(kcl_water(k), k) for k in (1, 3, 2)]
    var[1].pbc = [True, True, False]
    ase.io.write(OUT / "kcl_variable.extxyz", var, format="extxyz")

    # H5MD written by znh5md (same dialect as IPSuite/ZnTrack outputs)
    h5 = OUT / "kcl_znh5md.h5"
    if h5.exists():
        h5.unlink()
    znh5md.IO(str(h5)).extend([labeled(kcl_water(2, 0.1), i) for i in range(5)])

    # single-structure formats
    crystal = ase.Atoms("KCl", scaled_positions=[[0, 0, 0], [0.5, 0.5, 0.5]],
                        cell=np.diag([3.2, 3.2, 3.2]), pbc=True)
    ase.io.write(OUT / "POSCAR", crystal, format="vasp")
    ase.io.write(OUT / "kcl.cif", crystal, format="cif")
    ase.io.write(OUT / "kcl.pdb", crystal, format="proteindatabank")
    ase.io.write(OUT / "kcl.gro", crystal, format="gromacs")
    ase.io.write(OUT / "kcl.lmp", crystal, format="lammps-data", masses=True)
    ase.io.write(OUT / "kcl_nomass.lmp", crystal, format="lammps-data")

    # LAMMPS dump without element column (needs a type map)
    (OUT / "kcl.dump").write_text(
        "ITEM: TIMESTEP\n0\nITEM: NUMBER OF ATOMS\n2\nITEM: BOX BOUNDS pp pp pp\n"
        "0 3.2\n0 3.2\n0 3.2\nITEM: ATOMS id type x y z fx fy fz\n"
        "1 1 0.0 0.0 0.0 0.1 0.0 0.0\n2 2 1.6 1.6 1.6 -0.1 0.0 0.0\n"
        "ITEM: TIMESTEP\n10\nITEM: NUMBER OF ATOMS\n2\nITEM: BOX BOUNDS pp pp pp\n"
        "0 3.2\n0 3.2\n0 3.2\nITEM: ATOMS id type x y z fx fy fz\n"
        "1 1 0.1 0.0 0.0 0.2 0.0 0.0\n2 2 1.5 1.6 1.6 -0.2 0.0 0.0\n"
    )

    # binary MD trajectories (needs MDAnalysis)
    try:
        import MDAnalysis as mda

        traj = [kcl_water(2) for _ in range(3)]
        ase.io.write(OUT / "kcl_md.gro", traj[0], format="gromacs")
        u = mda.Universe(str(OUT / "kcl_md.gro"))
        for ext in ("dcd", "xtc"):
            with mda.Writer(str(OUT / f"kcl_md.{ext}"), n_atoms=len(traj[0])) as w:
                for a in traj:
                    u.atoms.positions = a.positions
                    u.dimensions = [8.0, 8.0, 8.0, 90, 90, 90]
                    w.write(u.atoms)
    except ImportError:
        print("MDAnalysis not installed: skipping DCD/XTC fixtures")

    make_run_dir(OUT / "run_cp2k_laufband")

    # corrupted inputs
    text = (OUT / "kcl_labeled.extxyz").read_text().splitlines()
    (OUT / "corrupt_truncated.extxyz").write_text("\n".join(text[: len(text) // 2 - 3]) + "\n")
    (OUT / "corrupt_garbage.xyz").write_text("this is not\na structure file\n@@@\n")
    raw = h5.read_bytes()
    (OUT / "corrupt_truncated.h5").write_bytes(raw[: len(raw) // 3])
    print(f"fixtures written to {OUT}")


if __name__ == "__main__":
    main()
