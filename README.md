# atomdb

Local-first database for atomistic simulation data — MD trajectories, DFT reference
data, MLIP datasets and models — with **provenance as first-class data**.

The question atomdb is built to answer: *"Where did this file come from, how was it
made, and what was it used for?"*

## Status

Under active development. See `docs/ARCHITECTURE.md` for the design and the phase plan.

## Quick start

```bash
uv sync
export ATOMDB_HOME=/work/jpeters/atomdb_store   # data store (outside the repo)
uv run atomdb init
uv run atomdb import trajectory.extxyz --json
uv run atomdb ls
uv run atomdb serve          # web UI on http://127.0.0.1:8765
```

## Documentation

| File | Content |
|---|---|
| `docs/ARCHITECTURE.md` | overall design, stack, phases |
| `docs/DATABASE_SCHEMA.md` | tables and columns |
| `docs/PROVENANCE.md` | provenance graph model |
| `docs/FILE_FORMATS.md` | supported formats, metadata preservation |
| `docs/CLI.md` | command reference |
| `docs/API.md` | HTTP API reference |
| `docs/DEVELOPMENT.md` | dev setup, tests, conventions |
