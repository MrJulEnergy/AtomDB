# atomdb

Local-first database for atomistic simulation data — MD trajectories, DFT reference data,
MLIP datasets and models — with **provenance as first-class, graph-structured data**.

> Find an unfamiliar H5MD file months later → `atomdb whatis file.h5` → `atomdb provenance
> DS-… --up` shows the DFT settings, the sampling step, the MD run, the model that drove it,
> the HPC jobs and the original files.

## Install & start

```bash
uv sync                                   # + `--extra md` (DCD/XTC/TRR), `--extra descriptors` (SOAP)
(cd frontend && npm install && npm run build)   # web UI (once)
export ATOMDB_HOME=/work/user/atomdb_store   # data store, outside the repo (default: /work/$USER/atomdb_store)
uv run atomdb init
uv run atomdb serve                        # http://127.0.0.1:8765
```

Try it on a demo store: `uv run python scripts/make_demo.py /tmp/atomdb_demo && ATOMDB_HOME=/tmp/atomdb_demo uv run atomdb serve`.

## Everyday use

```bash
# existing data: scan (read-only) → review plan → import
atomdb scan /work/user/Master/mptraj --plan plan.yaml
atomdb import-plan plan.yaml
atomdb suggest                              # relations implied by shared structures

# new data with provenance
atomdb import run_042/output.extxyz --temperature 400 --model MACE-MH-1 --source run_042/
atomdb import-run /work/user/runs/run_042 --cluster hawk          # dry run
atomdb import-run /work/user/runs/run_042 --cluster hawk --yes    # Slurm + CP2K + PLUMED → records

# questions
atomdb whatis mystery.h5
atomdb provenance DS-2026-000042 --up      # WHERE DID THIS COME FROM?
atomdb provenance DS-2026-000042 --down    # WHAT WAS THIS USED FOR?
atomdb search "r2scan+d4 el:K,Cl T:400"
atomdb stats DS-2026-000042 ; atomdb compare TRAIN NEW ; atomdb similarity TRAIN NEW
atomdb check --incomplete                   # what is still unknown?

# use data elsewhere / safety
atomdb link DS-2026-000042 ~/project/       # read-only symlink into the store
atomdb validate ; atomdb backup
```

Every command takes `--json` (stable envelope, error codes) — see `docs/AI_AGENTS.md`.

## Guarantees

- Source files are only read; imports copy into a content-addressed, read-only store
  (`files/sha256/ab/…`). Duplicates are detected by checksum; extra locations are recorded.
- Non-H5MD inputs get a canonical H5MD that is verified by read-back; existing H5MD files
  are stored unchanged. Conversions refuse to drop fields unless `--allow-loss`.
- All state lives on disk (SQLite + store); the browser holds nothing.
- Nothing is deleted or overwritten: restores need an empty target, validation only reports.

## Documentation

| File | Content |
|---|---|
| `docs/ARCHITECTURE.md` | design, stack, phases, implementation notes |
| `docs/DATABASE_SCHEMA.md` | tables and columns |
| `docs/PROVENANCE.md` | provenance graph model, traversal, frame lineage |
| `docs/FILE_FORMATS.md` | formats, H5MD, metadata preservation |
| `docs/CLI.md` | command reference incl. search language |
| `docs/API.md` | HTTP API |
| `docs/AI_AGENTS.md` | how AI agents should drive atomdb |
| `docs/DEVELOPMENT.md` | dev setup, tests, conventions |
