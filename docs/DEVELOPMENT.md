# Development

## Setup

```bash
uv sync                      # core + dev deps
uv sync --extra md           # optional MDAnalysis formats
uv run pytest
```

## Conventions

- One commit per meaningful feature; docs updated in the same commit.
- All business logic lives in `atomdb.services`; CLI (`atomdb.cli`) and API (`atomdb.api`)
  are thin wrappers that share the Pydantic schemas in `atomdb.schemas`.
- Never write to, move or delete user source files. Imports copy into the store.
- Tests use a temporary `ATOMDB_HOME`; never point tests at a real store.
- Test fixtures live in `tests/data/` and are tiny; regenerate with
  `uv run python tests/make_fixtures.py`.
