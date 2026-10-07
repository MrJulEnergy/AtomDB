# CLI reference

`atomdb` is the scripting interface (HPC-friendly, AI-friendly). All commands:

- accept `--json` (either as global option `atomdb --json ls` or per command `atomdb ls --json`)
- use the store at `--home` / `$ATOMDB_HOME`
- return a stable envelope in JSON mode:
  - success: `{"ok": true, "data": ...}`
  - failure: `{"ok": false, "error": {"code": "...", "message": "...", "details": {...}}}`

## Exit codes

| Code | Error code | Meaning |
|---|---|---|
| 0 | – | success |
| 1 | `error` | generic error |
| 2 | `invalid_input` | bad arguments / missing file |
| 3 | `not_initialized` | no store at `$ATOMDB_HOME` (run `atomdb init`) |
| 4 | `not_found` | unknown ID |
| 5 | `format_error` | unreadable or corrupt input file |
| 6 | `data_loss` | conversion would drop data (use `--allow-loss`) |
| 7 | `integrity` | store/database inconsistency |

## Commands

### `atomdb init`
Create or upgrade the store. Idempotent; never deletes anything.

### `atomdb import FILE... [--name N] [--description D] [--tag T]... [--format F]`
Copy files into the content-addressed store and create datasets. Importing identical
bytes again is detected by SHA256: no new dataset is created, the new location is
recorded as an additional origin.

### `atomdb info ID`
Everything known about a dataset: counts, elements, files, checksums, origins.

### `atomdb ls [--limit N] [--offset N] [--tag T]`
List datasets, newest first.

### `atomdb tag ID TAG...`
Add tags.

### `atomdb serve [--host 127.0.0.1] [--port 8765]`
Start the web UI and HTTP API.
