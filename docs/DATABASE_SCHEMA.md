# Database schema

SQLite (WAL mode, foreign keys on) accessed through SQLAlchemy 2.0. The schema is
managed by Alembic migrations in `src/atomdb/db/migrations/versions/`; `atomdb init`
creates or upgrades a store and is safe to re-run. Only portable column types are used
(PostgreSQL migration path); datetimes are always timezone-aware UTC.

Add a migration after changing `src/atomdb/db/models.py`:

```bash
uv run python scripts/new_migration.py "describe the change"
```

## Identifiers

Every node has a stable ID `<PREFIX>-<YEAR>-<NNNNNN>` plus a UUID.

| Prefix | Kind |
|---|---|
| DS | dataset |
| FL | file |
| MD / DFT / SMP / CNV / TRN / IMP / CALC | calculations (MD run, DFT labeling, sampling, conversion, training, import, other) |
| MDL | model |
| JOB | HPC job |
| SW | software |

IDs are allocated from `id_counter(prefix, year, next_value)` and never reused.
Lookups accept lower case and short numbers (`ds-2026-142`).

## Tables

### Bookkeeping
| Table | Purpose |
|---|---|
| `meta` | instance facts (`instance_uuid`, `created_at`) |
| `id_counter` | ID allocation |
| `audit_log` | append-only log of every mutation (action, node, command line, user, payload) |

### Nodes (joined-table inheritance on `node.kind`)
| Table | Columns |
|---|---|
| `node` | `id`, `uuid`, `kind`, `name`, `description`, `status`, `created_at`, `updated_at`, `created_by_cmd` |
| `dataset` | `n_frames`, `n_atoms_min/max/total`, `formula_summary`, `has_energy/forces/stress`, `energy_min/max`, `fmax_max`, `primary_file_id`, `content_fingerprint`, `source_text` |
| `file` | `sha256` (unique), `size_bytes`, `format`, `store_relpath` |

| `calculation` | `calc_type` (md/dft/sampling/training/conversion/import/...), `started_at`, `ended_at`, `command`, `workdir`, `exit_status`, `parameters` (JSON), `software_versions` (JSON snapshot: atomdb + git commit, python, ase, h5py, numpy, znh5md, ...) |
| `model` | `family`, `version`, `architecture`, `checkpoint_file_id`, `cutoff`, `n_parameters`, `package`, `package_version`, `git_commit`, `trained_at`, `url`, `hyperparameters` |
| `hpc_job` | `cluster`, `partition`, `job_id`, `array_job_id`, `array_task_id`, `user`, `workdir`, `submit_script_file_id`, `nodes`, `n_nodes`, `n_tasks`, `n_cpus`, `n_gpus`, `gpu_type`, `mem`, `time_limit`, `start`, `end`, `state`, `sbatch_options` |

### Typed calculation details
| Table | Columns |
|---|---|
| `md_params` | engine, engine_version, ensemble, temperature_k, pressure_bar, timestep_fs, n_steps, length_ps, thermostat, barostat, integrator, seed, density_g_cm3, pbc, initial_velocities, save_interval |
| `dft_params` | code, code_version, functional (canonical), functional_raw, dispersion, basis, pseudopotential, cutoff_ry, rel_cutoff_ry, eps_scf, smearing, charge, multiplicity, spin_polarized, kpoints, labels |
| `enhanced_sampling` | method, software, version, collective_variables, bias, input_file_id |
| `sampling_op` | method, criterion, seed, n_selected |
| `training_run` | hyperparameters, metrics, config_file_id |
| `calc_param` | (calc_id, key) → value (JSON), unit, source — arbitrary code settings |
| `node_attr` | (node_id, key) → value (JSON), unit, source — metadata without a dedicated column |

### Provenance graph
| Table | Purpose |
|---|---|
| `edge` | `src`, `dst`, `relation`, `role`, `source` (auto/parsed/user/inferred), `attrs`, unique per (src, dst, relation, role) — see PROVENANCE.md |
| `frame_link` | frame-level lineage (child dataset/frame → parent dataset/frame, via calculation) |

### Frame index and metadata catalog
| Table | Purpose |
|---|---|
| `frame` | one row per frame: `n_atoms`, `formula`, `elem_mask_lo/hi` (element bitmask: Z 1–62 / 63–118), `energy`, `energy_per_atom`, `fmax`, `temperature`, `volume`, `time`, `step`, `fingerprint` |
| `dataset_element` | per dataset and element: frames containing it, atom count |
| `dataset_facet` | search index: (dataset, key, value, num) aggregated from the upstream graph — functional, functional_key, dispersion, code, basis, pseudopotential, temperature, pressure, ensemble, model, model_key, model_family, enhanced_sampling, sampling_method, cluster, job_id, ancestor, source_dataset, tag, `attr:<key>`. Derived; refreshed automatically by a `before_commit` hook for every dataset downstream of changed provenance |
| `property_def` | every field in the file: origin (atoms/info/arrays/calc), name, level (frame/atom/cell), dtype, shape (−1 = n_atoms), unit, count, `label_calc_id` (calculation that computed it) |

### File bookkeeping
| Table | Purpose |
|---|---|
| `file_origin` | every place a stored file was imported from: `original_path`, `original_name`, `hostname`, `mtime`, `ctime`, `imported_at`, `import_id`. Many origins per file → deduplication. |
| `dataset_file` | dataset ↔ file with a `role` (`original`, `canonical_h5md`, `auxiliary`, `input`, `log`) |
| `symlink` | symlinks handed out by `atomdb link` |

### Tags and notes
`tag`, `node_tag`, `note` (free text; supplementary only, never provenance).
