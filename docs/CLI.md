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

### `atomdb import FILE... [--name N] [--description D] [--tag T]... [--format F] [--type-map M] [--raw]`
Copy files into the content-addressed store and create datasets. The original is kept
byte-for-byte; non-H5MD inputs additionally get a verified canonical H5MD. Importing
identical bytes again is detected by SHA256: no new dataset is created, the new location
is recorded as an additional origin. `--type-map 1=K,2=Cl` is needed for LAMMPS files
without element info. `--raw` stores any file without parsing it.

### `atomdb convert INPUT OUTPUT [--to F] [--from F] [--allow-loss] [--overwrite] [--no-record]`
Convert a file (or a dataset ID → its canonical H5MD) to another format. Fields the
target cannot represent are listed; the conversion is refused (exit 6, `data_loss`)
unless `--allow-loss`. Output files are never overwritten without `--overwrite`. If a
store exists, the conversion is recorded (`CNV-…`) and the output stored.

### `atomdb whatis PATH [--max-frames N]`
Identify an unknown file. Evidence, strongest first:
1. **exact match** — sha256 equals a stored file (shows datasets and every original location)
2. **stamp** — `/atomdb` group in H5MD files written by atomdb (dataset, source file, software)
3. **content** — structure fingerprints of up to N frames match indexed frames; reports
   matching dataset, frame ranges and whether coordinates were confirmed. Works for
   re-saved, converted, subsampled or concatenated copies.

### `atomdb link ID [DEST] [--original]`
Symlink a dataset's canonical H5MD (or original file with `--original`) from the store
to `DEST` (directory or file path). Store files are read-only. Existing paths are never
replaced. Links are recorded so `validate` can find dangling ones.

### `atomdb scan ROOT [--plan plan.yaml] [--exclude DIR]... [--no-models] [--max-depth N]`
Find existing data under ROOT, strictly read-only. Skips `.venv`, `node_modules`, `.git`
and similar. Every candidate is hashed and classified:
`new`, `in_db` (identical bytes already stored), or `possible_duplicate` (first frame
matches an indexed frame). Also lists directories that look like calculation runs or
model checkpoints. `--plan` writes an editable YAML import plan.

### `atomdb import-plan PLAN [--dry-run]`
Import everything in a reviewed plan (names, descriptions, tags, formats and read options
per item). `in_db` items and items with `import: false` are skipped. Errors are reported
per item; other items still import.

### `atomdb formats`
Supported formats and their capabilities.

### `atomdb info ID`
Everything known about a dataset: counts, elements, files, checksums, origins.

### `atomdb ls [--limit N] [--offset N] [--tag T]`
List datasets, newest first.

### `atomdb tag ID TAG...`
Add tags.

### `atomdb serve [--host 127.0.0.1] [--port 8765]`
Start the web UI and HTTP API.
