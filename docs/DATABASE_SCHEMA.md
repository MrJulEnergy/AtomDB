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

### File bookkeeping
| Table | Purpose |
|---|---|
| `file_origin` | every place a stored file was imported from: `original_path`, `original_name`, `hostname`, `mtime`, `ctime`, `imported_at`, `import_id`. Many origins per file → deduplication. |
| `dataset_file` | dataset ↔ file with a `role` (`original`, `canonical_h5md`, `auxiliary`, `input`, `log`) |
| `symlink` | symlinks handed out by `atomdb link` |

### Tags and notes
`tag`, `node_tag`, `note` (free text; supplementary only, never provenance).
