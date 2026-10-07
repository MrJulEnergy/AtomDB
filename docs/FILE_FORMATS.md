# File formats

## Principles

1. **H5MD is the canonical format.** Every imported trajectory/structure gets an H5MD
   representation (znh5md dialect, the format IPSuite/ZnTrack already write).
2. **Originals are kept byte-for-byte.** The original file is copied into the store and
   linked to the dataset with role `original`; the H5MD has role `canonical_h5md`.
3. **Existing H5MD files are stored as-is.** If the input already is H5MD, its original
   bytes *are* the canonical file — no re-encoding, no modification.
4. **Conversions are verified.** After writing H5MD, atomdb reads it back and compares
   frame count, structure fingerprints, energies and the full set of fields. Any
   difference aborts the import (nothing is half-imported).
5. **Generic metadata.** Every field is catalogued in `property_def` (origin `info`,
   `arrays`, `calc`, `atoms`; level `frame`, `atom`, `cell`; dtype, shape, unit, count).
   Nothing is hard-coded to energy/forces.
6. **Self-describing outputs.** H5MD files that atomdb writes carry an `/atomdb` group
   (attributes: `dataset_id`, `instance_uuid`, `source_sha256`, `source_name`,
   `source_path`, `source_format`, `created_at`, `software`).

## Supported formats

| Name | Extensions | Read | Write | Represents | Notes |
|---|---|---|---|---|---|
| `h5md` | .h5 .h5md .hdf5 | ✓ | ✓ | everything (info, arrays, calc, variable N) | detected by content |
| `extxyz` | .extxyz .xyz | ✓ | ✓ | everything except nested dicts | detected by `key=value` comment line |
| `xyz` | .xyz | ✓ | ✓ | elements + positions only | |
| `cif` | .cif | ✓ | ✓ | structure, cell | |
| `vasp` | POSCAR CONTCAR .vasp | ✓ | ✓ | single structure | |
| `vasp-xdatcar` | XDATCAR | ✓ | | trajectory | |
| `vasp-out` | OUTCAR | ✓ | | energies, forces, stress | |
| `lammps-data` | .lmp .data | ✓ | ✓ | single structure | types → elements via `--type-map` or inferred from `Masses` (warning) |
| `lammps-dump` | .dump .lammpstrj | ✓ | | trajectory, forces, velocities | requires `--type-map 1=K,2=Cl` unless the dump has an `element` column; original types kept as array `lammps_type` |
| `proteindatabank` | .pdb | ✓ | ✓ | structure | |
| `gromacs` | .gro | ✓ | ✓ | structure, velocities | |
| `traj` | .traj | ✓ | ✓ | ASE trajectory incl. calc results | |
| `dcd` | .dcd | ✓ | | positions, cell (float32) | optional extra `md`; needs `--topology` |
| `xtc` | .xtc | ✓ | | positions, cell (lossy, 0.001 nm) | optional extra `md`; needs `--topology` |
| `trr` | .trr | ✓ | | positions, velocities, forces (float32) | optional extra `md`; needs `--topology`; forces kJ/(mol·Å) → eV/Å |
| `netcdf` | .nc .ncdf | ✓ | | AMBER NetCDF | optional extra `md`; needs `--topology` |

Binary MD formats (`uv sync --extra md`, via MDAnalysis) carry no element information.
The topology file is stored alongside the dataset (role `topology`) and linked as an
input of the import activity. Time is converted ps → fs.

Formats are pluggable: implement `atomdb.formats.base.FormatAdapter` and register a
callable returning adapters under the `atomdb.formats` entry-point group.

## Canonical units

energy eV · forces eV/Å · stress eV/Å³ · positions/cell Å · time fs · temperature K.

## Known pitfalls handled

- LAMMPS types carry no element information → explicit type map (recorded in provenance).
- `energy` in extxyz becomes a calculator result in recent ASE; atomdb reads energies from
  calculator results first, then `info` keys (`energy`, `total_energy`, `potential_energy`,
  `free_energy`, `REF_energy`).
- Variable atom counts (NaN padding in H5MD) are handled by znh5md.
- Partial periodicity is preserved per frame.
