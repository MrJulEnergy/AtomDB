# atomdb — Architecture

## Context

Expensive atomistic data (r2SCAN DFT labels from HPC, MLIP-driven MD trajectories, training sets) currently lives scattered across project folders (e.g. `/work/jpeters/Master/{mptraj,KCl_vectra,...}/nodes/*/frames.h5`), with provenance only implicit in directory names and scripts. Goal: a local-first database + CLI + web UI where **provenance is first-class, graph-structured data**, so that any file (even an unfamiliar H5MD found months later) can be traced back to the MD run, model, DFT settings and HPC job that produced it — and forward to everything derived from it.

### Environment findings
- `/work/jpeters/Master/dataset_folder` is empty, not a git repo → becomes the **code repository**.
- Python 3.12 available via `uv` (system 3.14 too new for parts of the scientific stack); Node 24 + npm; git (user configured); sqlite3; zstd; 16 cores, 61 GB RAM.
- Existing data is **znh5md-written H5MD** (`h5md/creator = znh5md 0.4.8`): `particles/atoms/{position,species,force,box}`, `observables/atoms/{potential_energy,<info keys>}`, `ASE_ENTRY_ORIGIN` attrs (info/arrays/calc), NaN padding for variable atom counts. → Our H5MD layer must read/write exactly this dialect.
- HPC workflow (user decision): **import only happens here**. Many `sbatch` jobs (via laufband) write into the *same* output file; run directories are copied back and imported locally. No DB access from the cluster.
- Storage (user decision): **all files copied into one central store here**; elsewhere users get **read-only symlinks** pointing into the store. Files are moderate size (not 100s of GB), so always-copy is fine.
- ZnTrack/DVC import: explicitly **not** wanted.

---

## 1. Architecture overview

```
           ┌──────────── Browser (UI only, no authoritative state) ───────────┐
           │ React + TS: tables · periodic table · plots · 3D · prov graph    │
           └───────────────────────────────▲──────────────────────────────────┘
                                           │ JSON / REST (/api/v1)
  CLI (Typer, --json) ──┐          FastAPI app (serves built frontend too)
                        ▼                  ▼
                 ┌──────────── atomdb.services (single business-logic layer) ───────────┐
                 │ import · convert · provenance · search · stats · validate · backup   │
                 └──────┬──────────────┬────────────────┬──────────────────┬───────────┘
                        ▼              ▼                ▼                  ▼
                 SQLAlchemy ORM   FileStore (CAS)   Format adapters    Run-dir parsers
                 SQLite (WAL)     sha256/ab/…       ASE/znh5md/MDA     Slurm, CP2K, PLUMED
                 metadata+index   immutable 0444    lazy H5MD reader   (pluggable)
```

Rules: CLI and API are thin shells over the same `services` layer and the same Pydantic schemas → identical JSON from `atomdb … --json` and the HTTP API. SQLite holds metadata/indices only; atom data stays in H5MD and is read lazily.

## 2. Technology stack

| Concern | Choice | Reason |
|---|---|---|
| Language/env | Python 3.12, `uv`, `src/` layout, package `atomdb` | sci-stack compatibility |
| DB | SQLite (WAL, foreign keys ON) via **SQLAlchemy 2.0** + **Alembic** migrations | local-first; Postgres-portable (no SQLite-only types except isolated FTS) |
| Schemas | Pydantic v2 | shared by API + CLI JSON |
| CLI | Typer | typed, scriptable, auto help |
| API | FastAPI + uvicorn | OpenAPI for free (AI-readable) |
| Structure I/O | **ASE** (core), **znh5md** (H5MD dialect already in use), h5py (lazy/streaming reads) | established libs |
| Extra formats | **MDAnalysis** (optional extra `[md]`: DCD/XTC/TRR/GRO/NetCDF + topology) | MDTraj/pymatgen skipped (overlap, heavy) — adapter hooks allow adding later |
| Numerics | numpy; PCA via numpy SVD (no sklearn needed) | minimal deps |
| Descriptors | built-in lightweight descriptor (composition + pair-distance/RDF histograms); **dscribe SOAP** as optional extra `[descriptors]` | robust default, pluggable |
| Frontend | React + TypeScript + Vite, TanStack Query/Table/Router-lite, built bundle served by FastAPI | node only needed at build time |
| Plots | Plotly.js (histograms, overlays, PCA scatter with hover/click) | interactive, no custom work |
| 3D viewer | 3Dmol.js (cell box, element colors, large-ish systems) | simple, well-maintained |
| Provenance graph | Cytoscape.js + dagre layout | clickable DAG |
| Periodic table | custom CSS-grid component | no dependency needed |
| Tests | pytest (+ hypothesis for round-trip properties); vitest for a few UI units | |
| Backup | `tar` + `zstd` (system) via Python `tarfile` + `zstandard` | |

## 3. Database schema (normalized; PROV-inspired)

Core idea: every provenance-capable object is a **node** with a stable, human-readable ID; typed detail tables hang off it (joined-table inheritance); relationships live in an **edge** table. Typed tables make scientific fields queryable; generic key/value tables catch everything code-specific.

**Stable IDs**: `<PREFIX>-<YEAR>-<6-digit seq>`, e.g. `DS-2026-000142`, from a `id_counter(prefix, year, next)` table. Prefixes: `DS` dataset, `FL` file, `MD` MD/simulation run, `DFT` DFT calculation, `SMP` sampling, `CNV` conversion, `TRN` training run, `IMP` import, `CALC` other calculation, `MDL` model, `JOB` HPC job, `SW` software, `ENV` environment. Every node also has a UUID (for future multi-DB merges).

```
node(id PK, uuid, kind, name, description, status, created_at, updated_at, created_by_cmd)

── Entities ──
dataset(node_id PK→node, n_frames, n_atoms_min, n_atoms_max, n_atoms_total, formula_summary,
        has_energy, has_forces, has_stress, primary_file_id→file, content_fingerprint, source_text)
file(node_id PK→node, sha256 UNIQUE, size_bytes, format, store_relpath, mime, is_h5md, read_only)
file_origin(id, file_id→file, original_path, original_name, hostname, mtime, ctime,
            imported_at, import_id→node)            -- many origins per sha256 (dedup)
dataset_file(dataset_id, file_id, role)              -- role: canonical_h5md | original | auxiliary | input | log
model(node_id PK→node, family, name, version, architecture, checkpoint_file_id→file, cutoff,
      n_parameters, package, package_version, git_commit, trained_at)
structure: not a separate table — single structures are 1-frame datasets (keeps one code path)

── Frame index (one row per frame; values only for fast filter/plots, full data in H5MD) ──
frame(dataset_id, idx, n_atoms, formula, elem_mask_lo, elem_mask_hi,  -- 128-bit element bitmask
      energy, energy_per_atom, fmax, temperature, volume, time, step,
      fingerprint)                                   -- hash of (numbers, rounded positions, cell)
      PK(dataset_id, idx); indexes on fingerprint, energy, temperature
dataset_element(dataset_id, Z, symbol, n_frames_containing, n_atoms)
property_def(dataset_id, name, level{frame,atom,cell}, dtype, shape, unit, origin{info,arrays,calc},
             h5_path, label_calc_id→node NULL)       -- generic catalog of ALL fields in the file

── Activities ──
calculation(node_id PK→node, type{md,dft,sampling,conversion,training,import,geoopt,other},
            software_id→software, environment_id→environment, started_at, ended_at,
            command, workdir, exit_status)
calc_param(calc_id, section, key, value_json, unit)  -- arbitrary code settings (CP2K tree flattened)
dft_params(calc_id PK, code, code_version, functional, functional_canonical, dispersion{none,D3,D3BJ,D4,…},
           basis, pseudopotential, cutoff_Ry, rel_cutoff_Ry, eps_scf, smearing, charge, multiplicity,
           kpoints, has_energy, has_forces, has_stress)
md_params(calc_id PK, ensemble, temperature_K, pressure_bar, timestep_fs, n_steps, length_ps,
          thermostat, barostat, integrator, seed, density, pbc, initial_structure_id→node NULL,
          driver_model_id→model NULL)                -- which MLIP ran the MD
enhanced_sampling(calc_id, method{metad,opes,umbrella,abf,steered,remd,tamd,custom}, software,
                  version, cvs_json, bias_json, plumed_input_file_id→file)
sampling_op(calc_id PK, method{random,stride,uncertainty,fps,manual,filter}, criterion_json, seed)
training_run(calc_id PK, hyperparams_json, config_file_id→file, metrics_json)

── Agents / context ──
software(node_id PK, name, version, git_commit)
environment(id, python_version, platform, packages_json, hash UNIQUE)
hpc_job(node_id PK, cluster, partition, job_id, array_job_id, array_task_id, user, workdir,
        submit_script_file_id→file, nodes, n_cpus, n_gpus, gpu_type, mem, start, end, state)
calc_job(calc_id, job_id)                            -- one calc ← many jobs (laufband pattern)

── Graph ──
edge(id, src→node, dst→node, relation, role, created_at, import_id)
     relation ∈ {used, generated, derived_from, labeled_by, trained_on, driven_by, ran_on,
                 executed_with, converted_from, sampled_from, part_of}
     UNIQUE(src,dst,relation,role); index on src and dst
frame_link(child_dataset, child_idx, parent_dataset, parent_idx, via_calc)  -- frame-level lineage

── Misc ──
tag(id, name UNIQUE), node_tag(node_id, tag_id)
note(id, node_id, text, created_at)                  -- supplementary only, never provenance
audit_log(id, ts, command, actor, action, node_id, payload_json)   -- append-only
task(id, type, status, progress, params_json, result_json, created_at)  -- long jobs (stats, PCA)
search_fts  (FTS5 virtual table: node id, name, description, params text, tags) — isolated module
```

Normalization of vocabulary: `vocab_alias(field, alias, canonical)` maps `R2SCAN`, `r2scan`, `r²SCAN` → `r2SCAN`; `DFTD4`/`D4` → `D4`; model names, etc. Searches hit canonical values.

## 4. Provenance data model

- **Entities**: Dataset, File, Model. **Activities**: Calculation subtypes (MD, DFT, Sampling, Conversion, Training, Import). **Agents/context**: Software, Environment, HPC job.
- Canonical patterns:
  - `MD-… --generated--> DS(traj)`; `MD-… --driven_by--> MDL-…`; `MD-… --used--> DS(initial structure)`
  - `SMP-… --used--> DS(traj)`; `SMP --generated--> DS(selected)`; `frame_link` maps each selected frame → parent frame
  - `DFT-… --used--> DS(selected)`; `DFT --generated--> DS(labeled)`; `property_def.label_calc_id` says *which properties* came from which DFT calc
  - `TRN-… --used--> DS(train)`; `TRN --generated--> MDL`
  - `CNV-… --used--> FL(source)`; `CNV --generated--> FL(output)`; `CNV` stores converter, version, params, lost-fields report
  - any calc `--ran_on--> JOB-…` (many), `--executed_with--> SW-…`
- **Traversal**: recursive CTE over `edge` in both directions (`ancestors(id, depth)`, `descendants(id, depth)`), returning a subgraph (nodes+edges) used by CLI tree print, API, and UI graph. Frame-level: `frame_lineage(ds, idx)` walks `frame_link` + dataset edges → produces the "Frame 18342 ← sampled from MD … ← MACE-MH-1 …" chain.
- **Immutability**: imported files and generated datasets are never mutated; corrections create new nodes or edits recorded in `audit_log`. Deleting a node with descendants is refused unless `--force`, and still keeps files in the store (garbage collection is a separate explicit command).
- **Self-describing files**: every canonical H5MD written by atomdb gets an `/atomdb` group (dataset ID, DB instance UUID, source sha256s, compact provenance JSON snapshot) **before** hashing. Plus per-frame `fingerprint` in DB → `atomdb whatis unknown.h5` identifies a file by (1) sha256 match, (2) embedded `/atomdb` stamp, (3) frame-fingerprint overlap ("87% of frames match DS-2026-000031 frames 0–4999"). This directly serves the "unfamiliar 20 GB H5MD in six months" criterion, even for re-saved or converted copies.

## 5. File storage architecture

```
$ATOMDB_HOME  (default /work/jpeters/atomdb_store — outside the git repo; configurable)
├── atomdb.sqlite          (+ -wal/-shm)
├── config.toml            (store settings, vocab overrides, UI port)
├── files/sha256/ab/ab12…  (content-addressed, chmod 0444, original bytes untouched)
├── cache/                 (stats/histogram/descriptor caches; deletable)
├── thumbnails/
├── exports/  backups/  logs/
└── tmp/                   (atomic writes: write tmp → fsync → hash → rename)
```
- Import always **copies** (never moves/deletes source); dedup by sha256 (second import of same bytes adds only a `file_origin` row and reports "already known as FL-…, DS-…").
- **Symlinks for use elsewhere**: `atomdb link DS-2026-000142 [dest]` creates a read-only symlink (named by original filename) pointing into the store; `atomdb link --h5md|--original` chooses which representation. Symlinks are tracked (`link` table: path, target, created) so `validate` can report dangling ones.
- Data root separate from code repo so `git clean` can never touch data.

## 6. Format / conversion architecture

- `FormatAdapter` protocol: `name, extensions, sniff(path)->confidence, capabilities (energy, forces, stress, cell, pbc, info, arrays, variable_natoms, time, velocities), read_lazy(path)->FrameSource, write(frames, path, opts)`. Registry with entry points (`atomdb.formats`) for future plugins.
- `FrameSource`: `__len__`, `get(i)`, `iter_chunks(n)`, `property_catalog()` — never loads the whole trajectory.
- Adapters: `h5md` (znh5md + h5py lazy reader), `extxyz/xyz`, `ase-generic` (CIF, POSCAR/CONTCAR, LAMMPS data, PDB, Gaussian, …), `lammps-dump` (requires `--type-map 1=K,2=Cl`), `mdanalysis` (DCD/XTC/TRR/GRO/NetCDF, require topology for elements).
- **Import pipeline**: sniff → read lazily → build property catalog (every info/array/calc key, units) → write canonical H5MD (chunked, compressed, `/atomdb` stamp) → hash → store both original and H5MD → index frames in batches → record `IMP` + `CNV` activities and edges.
- **Loss accounting**: converter diffs source catalog vs target capabilities; any field that can't be represented is listed. `atomdb convert` refuses lossy conversion unless `--allow-loss`, and the `CNV` record stores the lost-field list. Round-trip tests enforce this.
- Units: canonical eV, Å, eV/Å, eV/Å³, fs, K; source units recorded in `property_def`.

### Run-directory import (HPC, local only)
`atomdb import-run /path/to/run_042/ [--sampled-from DS-…] [--model MDL-…] [--temperature 400 …]`
Pluggable `RunParser`s inspect the directory and propose records (shown as a dry-run plan, `--yes` to commit):
- **Slurm**: `#SBATCH` lines in `*.sh/*.slurm/*.sbatch`, `slurm-<job>_<task>.out` → one `JOB` per job/array task; **many jobs → one calculation** (laufband pattern: many sbatch jobs writing one shared output file). If a laufband state DB/file is present it is parsed opportunistically for worker ↔ job info (to verify against real files).
- **CP2K**: input `.inp` (nested `&SECTION` parser → `calc_param` tree; XC functional, VdW/D3/D4, BASIS_SET, POTENTIAL, CUTOFF, REL_CUTOFF, EPS_SCF, smearing), output header (version, git revision, host, timings), Hartree→eV conversion of energies/forces.
- **PLUMED**: `plumed.dat` → `enhanced_sampling` (method, CVs, bias params) + stored input file.
- **ASE/MLIP MD logs** (generic): temperature/timestep from log/config YAML when parseable; otherwise CLI flags fill gaps.
- Unknown files in the directory are still stored as `auxiliary` so nothing is lost.

## 6b. Adding data that already exists (retroactive / legacy import) — first-class

Most valuable data already exists (e.g. `/work/jpeters/Master/*/nodes/*/frames.h5`, old run dirs, checkpoints). The design must handle data whose provenance is partly known, unknown, or only discoverable later:

- **Discovery**: `atomdb scan DIR [--recursive] [--exclude .venv,node_modules]` walks a tree read-only, sniffs atomistic files, model checkpoints and run dirs, computes sha256, and reports: new / already in DB (by checksum) / probably duplicate (by frame fingerprints). Output is an editable **import plan** (YAML/CSV: path, proposed name, format, tags, metadata, parent links).
- **Batch import**: `atomdb import-plan plan.yaml [--dry-run]` imports everything in the plan in one go; per-file metadata (temperature, model, functional, sampled-from, notes) can be filled in the plan before committing. Same for many run dirs: `atomdb import-run DIR1 DIR2 …`.
- **Existing H5MD imported as-is**: if a file is already valid H5MD (znh5md dialect), its original bytes are stored unchanged and used directly as the canonical representation — no re-conversion; the `/atomdb` stamp then lives only in the DB (original never modified).
- **Existing models**: `atomdb model add CHECKPOINT …` for checkpoints trained in the past, with optional `--trained-on DS-…`.
- **Unknown ancestors as placeholders**: when something is known to come from "some MD run at 400 K with MACE-MH-1" but that run isn't in the DB, `annotate --from-placeholder md --temperature 400 --model …` creates a placeholder node (`status=placeholder`). It can later be **merged** with the real record once imported (`atomdb merge PLACEHOLDER-ID REAL-ID`), rewriting edges with an audit entry.
- **Provenance added after the fact**: `atomdb annotate` / `atomdb link-prov SRC REL DST` and UI edit forms add metadata and edges at any time; each field records `source ∈ {parsed, user, inferred}` and when it was added.
- **Relationship suggestions**: frame fingerprints let the DB detect that frames of dataset A appear in dataset B (e.g. a selection taken from an old trajectory) → offered as *suggested* `sampled_from`/`derived_from` edges (+ frame_links) that the user accepts or rejects; never auto-committed.
- **Completeness indicator**: each dataset shows which provenance fields are missing (no DFT settings, no source trajectory, no HPC job…), and `atomdb ls --incomplete` lists datasets to backfill.
- Originals are never moved or modified; scanning and importing are read-only on source trees (verified by sha256 before/after in tests).

These land in P2 (scan, import-plan, as-is H5MD), P3 (placeholders, merge, annotate, suggestions) and P4 (UI edit forms, completeness view).

## 7. CLI design (`atomdb`)

All commands: `--json` (stable schema, `{"ok":true,"data":…}` / `{"ok":false,"error":{"code":"…","message":"…"}}`), non-zero exit codes per error class, `--db/ATOMDB_HOME`, no interactive prompts when `--yes`/non-TTY.
```
atomdb init                                   atomdb serve [--port 8765]
atomdb import FILE… [--name --format --type-map --temperature --model --source --tag --parent DS-…]
atomdb import-run DIR [--dry-run] [--yes] [--sampled-from …]
atomdb info ID          atomdb ls [datasets|models|calcs|jobs|files] [--filter …]
atomdb search "functional:r2scan el:K,Cl T:400"   atomdb search --element K --element Cl [--any]
atomdb provenance ID [--up|--down] [--depth N] [--frame IDX] [--format tree|json|dot]
atomdb whatis PATH                            # identify unknown file
atomdb convert IN OUT [--to h5md] [--allow-loss]   atomdb link ID [DEST]
atomdb stats ID [--recompute]                 atomdb compare ID ID… [--json]
atomdb embed ID… [--descriptor simple|soap] [--max-frames N] --out pca.json
atomdb annotate ID --set temperature=400 | --link driven_by MDL-…   # add provenance after the fact
atomdb model add CHECKPOINT --family MACE --name MACE-MH-1 …   atomdb tag ID TAG
atomdb validate [ID|--all]                    atomdb backup [--out …tar.zst]  atomdb restore ARCHIVE
atomdb schema [--json]                        # entity/relationship/vocabulary docs for agents
```
Search mini-language (shared by CLI/API/UI): free text + `field:value`, `field>value`, ranges `T:300..400`, element sets `el:K,Cl` (AND) / `el:K|Na` (OR), `model:"MACE-MH-1"`, `disp:D4`, `es:any` (enhanced sampling), `derived:MD-2026-000017`.

## 8. API design (`/api/v1`, OpenAPI at `/api/v1/docs`)

```
GET  /datasets?q=&el=&sort=&page=         GET /datasets/{id}       PATCH /datasets/{id} (annotations)
GET  /datasets/{id}/frames?filter=&page=   GET /datasets/{id}/frames/{idx}  (single-frame lazy read)
GET  /datasets/{id}/stats                  GET /datasets/{id}/properties
GET  /nodes/{id}   GET /nodes/{id}/provenance?direction=up|down|both&depth=
GET  /frames/{ds}/{idx}/lineage
GET  /search?q=    GET /elements (present-element summary)   GET /elements/query?all=K,Cl&any=
GET  /models  /calculations  /jobs  /files/{id}  /files/{id}/download
POST /import (server-local path)  POST /import-run   POST /convert   POST /whatis
POST /compare   POST /embed → task   GET /tasks/{id}
POST /validate  POST /backup         GET /schema
```
`docs/API.md` + `atomdb schema --json` describe entities, relations, vocabulary, search syntax, and example agent workflows.

## 9. Frontend architecture

Vite + React + TS in `frontend/`, built into `src/atomdb/web/static/` (served by FastAPI; app runnable without node). Server-side pagination/filtering everywhere (fast with thousands of datasets). Pages:
1. **Datasets** — dense table (name, ID, frames, atoms, elements, E/F ranges, T, p, calculator, functional, dispersion, MLIP, source traj, created, location) + search bar + facet filters.
2. **Dataset detail** — metadata panels (MD / DFT / MLIP / HPC / enhanced sampling), property catalog, label sources, files & origins, stats plots, prominent **WHERE DID THIS COME FROM?** / **WHAT WAS THIS USED FOR?** buttons.
3. **Frame viewer** — 3Dmol structure, cell, per-frame values, force table/arrow toggle, frame slider, lineage chain.
4. **Provenance graph** — Cytoscape DAG, click node → side panel metadata, expand up/down.
5. **Periodic table** — present elements highlighted (counts), absent dimmed; AND/OR toggle → linked dataset/frame results.
6. **Compare** — overlaid E, E/atom, |F| (per element), atom counts, composition, T distributions.
7. **Embedding explorer** — banner "EXPLORATORY / DIAGNOSTIC"; pick datasets, descriptor, frame cap → PCA scatter colored by dataset; click point → frame.
8. **Integrity** — validate results, store usage, backups.
Style: plain, dense, monospace IDs, neutral palette; light/dark.

## 10. Statistics, descriptors, similarity

- Stats computed at import in chunks (streaming min/max/mean/std via Welford; fixed-bin histograms with a first pass for range from frame index): energy, E/atom, |F|, Fx/Fy/Fz, |F| per element, n_atoms, volume, density, lattice params, temperature. Cached in `cache/` + summary in DB.
- Descriptor plugin interface `Descriptor.compute(frames_chunk) -> (n_frames, d)`; default `simple` (composition vector + per-element-pair distance histograms, numpy+ASE neighbor list); `soap` optional (dscribe, averaged). PCA via numpy SVD; frame subsampling cap.
- Similarity (later phase): composition distance, Wasserstein distance of E/F distributions, descriptor nearest-neighbour coverage ("x% of new frames farther than d from train set") — `Similarity` plugin interface designed in phase 6.

## 11. Testing strategy

- Tiny fixtures committed in `tests/data/` (generated by a script also committed): 3-frame xyz, extxyz with energy/forces/stress/virial/config_type/charges/dipole + custom per-atom arrays, variable-atom-count extxyz, znh5md H5MD (same dialect as user files), POSCAR, CIF, LAMMPS data+dump, PDB, GRO; fake CP2K run dir (inp/out/slurm script/slurm-*.out, multiple jobs one output) and plumed.dat; corrupted/truncated files.
- Unit: checksum, dedup, ID generation, adapters' round-trips (energy/forces/stress/cell/pbc/info/arrays exact or tolerance), loss reports, CP2K/Slurm/PLUMED parsers, vocab normalization, search parser, element bitmask queries, streaming stats == numpy reference.
- Integration: import → restart (new engine/process) → everything persists; provenance traversal both directions + frame lineage; `whatis` on renamed/re-saved file; validate detects missing/corrupted store files and broken edges; backup → restore → identical DB checksums.
- API tests via FastAPI TestClient; CLI tests via Typer CliRunner incl. JSON schema checks. Frontend: vitest for search-query builder/periodic table logic; manual smoke via `atomdb serve`.

## 12. Development phases (each bullet ≈ one commit; app runnable after each)

- **P0** `chore: init repo, uv project, docs skeleton (README, ARCHITECTURE, DATABASE_SCHEMA, PROVENANCE, FILE_FORMATS, CLI, API, DEVELOPMENT)`
- **P1** db engine+Alembic · node/dataset/file entities + ID generator · content-addressed FileStore + sha256 dedup · CLI skeleton (`init/import raw/info/ls --json`) · FastAPI + minimal React shell (dataset list) · persistence test
- **P2** adapter registry · extxyz/xyz · H5MD (znh5md) lazy reader/writer + `/atomdb` stamp · ASE-generic formats · frame index + fingerprints · property catalog (generic metadata) · convert + loss report · MDAnalysis/LAMMPS dump adapters · `whatis`
- **P3** edge table + traversal + `provenance` CLI/API · calculation/MD/DFT/sampling/training tables · model entity · software/environment/HPC job · run-dir import: Slurm parser · CP2K parser · PLUMED/enhanced sampling · `annotate`/`link`
- **P4** search language + FTS · datasets explorer UI · dataset detail + where-from/used-for · provenance graph UI · frame viewer (3Dmol)
- **P5** element index + periodic table UI · streaming stats · energy/force histograms (+ per element) · structure stats
- **P6** descriptor plugins + PCA explorer · dataset comparison · similarity interface
- **P7** backup/restore (tar.zst + manifest) · validate/integrity · performance (indices, batch inserts, pagination) · API/CLI polish and schema docs

Docs are updated in the same commit as the feature they describe.

## 13. Scientific-data pitfalls to handle explicitly

- **Units**: CP2K Hartree, Hartree/Bohr; stress in GPa vs eV/Å³; pressure bar/atm; always store source unit + canonical value.
- **Stress conventions**: sign, Voigt vs 3×3, virial vs stress (virial = −stress·V); never silently conflate.
- **ASE extxyz quirks**: which keys become `calc` results vs `info` (version-dependent), `energy` vs `free_energy`, per-atom vs total.
- **Variable atom counts** (NaN padding in znh5md) — stats must mask padding.
- Precision: float32 storage or XTC compression loses precision → recorded as loss.
- Wrapped vs unwrapped positions; partial PBC; non-orthogonal cells; missing cells in xyz.
- LAMMPS dump/XTC/DCD lack element info → require explicit type map/topology, recorded in provenance.
- Functional/dispersion naming ambiguity (r2SCAN vs rSCAN vs SCAN; D3 vs D3(BJ) vs D4) → canonical vocab + raw value kept.
- Duplicate frames across datasets (fingerprints detect), checksum of HDF5 changes on re-save (fingerprints solve).
- Laufband-style concurrent writers: frame order ≠ job order; frame↔job attribution may be unknown → modelled as calc ↔ many jobs, not invented.
- Timestamps: store UTC with timezone.

## 14. Risks & trade-offs

- Schema breadth vs simplicity: typed tables only for MD/DFT/sampling/model/HPC; everything else in `calc_param` → avoids scope creep but keeps queries possible.
- Frame table size (millions of rows) is fine for SQLite with proper indices; per-frame element bitmask avoids a huge frame×element table.
- znh5md coupling: pinned version + own h5py reader for lazy access so we can read files even if znh5md changes.
- FTS5 is SQLite-specific → isolated in `search/fts.py` with a LIKE fallback for Postgres migration.
- Heuristic run-dir parsing can be wrong → always dry-run preview, raw files stored, fields marked `source: parsed|user|inferred`.
- MDAnalysis/dscribe are heavy → optional extras, not core.
- Single-user local server: no auth; bind to 127.0.0.1 by default.

## 15. Proposed directory structure

```
dataset_folder/                      (git repo = code)
├── pyproject.toml  uv.lock  README.md  .gitignore
├── docs/ ARCHITECTURE.md DATABASE_SCHEMA.md PROVENANCE.md FILE_FORMATS.md CLI.md API.md DEVELOPMENT.md
├── src/atomdb/
│   ├── config.py  ids.py  units.py  vocab.py
│   ├── db/ (engine.py, models/*.py, migrations/ (alembic))
│   ├── store/ (filestore.py, links.py, hashing.py)
│   ├── formats/ (base.py, registry.py, h5md.py, extxyz.py, ase_generic.py, lammps.py, mdanalysis.py)
│   ├── runparsers/ (base.py, slurm.py, cp2k.py, plumed.py, laufband.py)
│   ├── services/ (importer.py, converter.py, provenance.py, search.py, stats.py, compare.py,
│   │              whatis.py, validate.py, backup.py, tasks.py)
│   ├── descriptors/ (base.py, simple.py, soap.py, pca.py)
│   ├── schemas/ (pydantic models)
│   ├── cli/ (main.py + command modules)
│   ├── api/ (app.py, routers/*.py)
│   └── web/static/ (built frontend)
├── frontend/ (Vite React TS: src/pages, src/components/{PeriodicTable,ProvGraph,StructureViewer,Histogram})
├── tests/ (data/, make_fixtures.py, unit/, integration/, api/, cli/)
└── scripts/
```
Data root (`$ATOMDB_HOME`, default `/work/jpeters/atomdb_store`) lives outside the repo.

## Verification (per phase and end-to-end)

- `uv run pytest` green after every commit; `uv run atomdb serve` starts and UI loads.
- End-to-end acceptance script (`tests/integration/test_acceptance.py` + manual): import an MD trajectory (extxyz) with `--model MACE-MH-1 --temperature 400`; import a fake CP2K run dir with `--sampled-from` that trajectory; create a training run → model; restart the server/process; then
  `atomdb provenance <DFT dataset> --up` shows DFT → sampling → MD → MACE-MH-1, `--down` from the MD shows the derived datasets/model; `atomdb search "functional:r2scan el:K,Cl"` finds it; `atomdb whatis` on a copied/renamed H5MD returns the dataset; `atomdb validate --all` clean; `atomdb backup` + `restore` into fresh home reproduces everything.
- Legacy-data acceptance: `atomdb scan /work/jpeters/Master/mptraj --exclude .venv` → import plan → `import-plan` → re-scan reports everything as "already in DB"; add a placeholder parent and later merge it; fingerprint suggestion links `train_atoms.h5` frames to the dataset they were selected from.
- Try it on a real file: `/work/jpeters/Master/mptraj/nodes/Train/Apax/train_atoms.h5` (read-only import, original untouched — verify sha256 before/after).


