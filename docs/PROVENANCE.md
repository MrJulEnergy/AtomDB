# Provenance model

Provenance is stored as a directed graph, not as free text. It is inspired by W3C PROV:

- **Entities**: datasets (`DS`), files (`FL`), models (`MDL`)
- **Activities** (calculations): MD (`MD`), DFT (`DFT`), sampling (`SMP`), training (`TRN`),
  conversion (`CNV`), import (`IMP`), other (`CALC`)
- **Context**: HPC jobs (`JOB`)

Every node has a stable ID and a `status`: `active`, `placeholder` (known to exist but
reconstructed from memory / referenced by name only), or `merged`.

## Relations (`edge` table)

Edges always read `src --relation--> dst`:

| Relation | Meaning | Example |
|---|---|---|
| `used` | activity used an input | `DFT-… used DS(selected)` |
| `generated` | activity produced an output | `MD-… generated DS(traj)` |
| `sampled_from` | dataset frames were selected from another dataset | `DS(selected) sampled_from DS(traj)` |
| `labeled_by` | dataset properties computed by a calculation | `DS(labeled) labeled_by DFT-…` |
| `derived_from` | generic derivation | |
| `trained_on` | model trained on dataset | `MDL-… trained_on DS(train)` |
| `driven_by` | simulation driven by a potential | `MD-… driven_by MDL-…` |
| `ran_on` | calculation executed as HPC job | `DFT-… ran_on JOB-…` |
| `converted_from` | file converted from another file | `FL(h5) converted_from FL(extxyz)` |
| `part_of` | node belongs to a larger node (not traversed) | `FL(checkpoint) part_of MDL-…` |
| `same_as` | placeholder merged into the real node | |

Each edge records its `source` (`auto` = created by atomdb, `parsed` = from run files,
`user` = entered by hand, `inferred` = suggested from data) and optional `role`
(`training_data`, `topology`, `plumed_input`, ...).

## Traversal

- **Upstream — "where did this come from?"** follows `generated` backwards (output →
  activity) and every other relation forwards (activity → input, dataset → parent,
  MD → model, calculation → job).
- **Downstream — "what was this used for?"** is the reverse.

`atomdb provenance ID --up | --down` (API: `GET /api/v1/nodes/{id}/provenance?direction=`)
returns all reachable nodes with their level and all edges, as a tree, JSON or Graphviz dot.

Example (upstream of a DFT-labeled dataset):

```
DS-2026-000003  labeled  — 2 frames · H O Cl K · EF
├─ labeled_by → DFT-2026-000001  CP2K/r2SCAN/D4  — CP2K · r2SCAN/D4 · TZV2P-MOLOPT
│  └─ used → DS-2026-000002  selected  — 2 frames · H O Cl K
│     ├─ sampled_from → DS-2026-000001  md  — 4 frames · H O Cl K
│     │  └─ generated → MD-2026-000001  MD NVT 400 K (MACE-MH-1)  — NVT · 400 K · dt 0.5 fs
│     │     └─ driven_by → MDL-2026-000001  MACE-MH-1 [placeholder]
...
```

## Frame-level lineage

`frame_link(child_dataset, child_idx, parent_dataset, parent_idx, via_calc)` maps
individual frames. Links are created when recording sampling (`record sampling`, import
`--sampled-from`) and DFT labeling (labeled frames → input structures), either from an
explicit map or by matching structure fingerprints (only unambiguous matches are linked;
counts of unmatched/ambiguous frames are stored with the sampling record).
`atomdb provenance DS --frame N` prints the frame's chain, e.g.
`DS-3 frame 1 ← (DFT-1) DS-2 frame 1 ← (SMP-1) DS-1 frame 3`.

## Typed scientific records

| Activity | Typed table | Key fields |
|---|---|---|
| MD | `md_params` | engine, ensemble, temperature_k, pressure_bar, timestep_fs, n_steps, length_ps, thermostat, barostat, integrator, seed, ... |
| MD (enhanced sampling) | `enhanced_sampling` | method, software, collective variables, bias parameters, stored PLUMED input |
| DFT | `dft_params` | code, version, functional (canonical + raw), dispersion, basis, pseudopotential, cutoffs, eps_scf, smearing, charge, multiplicity, k-points, labels |
| sampling | `sampling_op` | method, criterion, seed, n_selected, frame-mapping stats |
| training | `training_run` | hyperparameters, metrics, stored config |
| any | `calc_param` | arbitrary code settings (e.g. flattened CP2K input) with source |

Models (`model`): family, version, architecture, cutoff, parameters, package/version,
git commit, training date, URL, hyperparameters, checkpoint files (file or directory).

Canonical vocabulary (`atomdb/vocab.py`) makes `r2scan`, `R2SCAN`, `r²SCAN` and CP2K's
`MGGA_X_R2SCAN + MGGA_C_R2SCAN` all `r2SCAN`; `DFTD4` → `D4`, `D3(BJ)` → `D3BJ`.
The raw value is kept (`functional_raw`).

## Adding provenance after the fact

Existing data rarely arrives with complete provenance. atomdb supports filling it in later:

- `atomdb import FILE --temperature 400 --model MACE-MH-1 --source DIR` (and
  `--sampled-from`, `--code/--functional/--dispersion`, `--set key=value`)
- `atomdb annotate ID --set temperature=400 --set model=MACE-MH-1 --note "..."`
- `atomdb record md|dft|sampling|training ...` for full control
- `atomdb relate SRC RELATION DST` for any relation
- Names of unknown models create **placeholder** models; `atomdb model add --name X`
  later fills in the same node. `atomdb merge PLACEHOLDER REAL` re-points all relations
  from a placeholder to a real node (audited).
- Import plans (`atomdb scan --plan`) accept the same `metadata` keys and `links` per item.

- `atomdb check --incomplete` lists datasets with gaps and how to fill them;
  `atomdb suggest` proposes relations implied by shared structures.

All changes are recorded in the append-only `audit_log`.
