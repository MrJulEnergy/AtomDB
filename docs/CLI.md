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

### Provenance shortcuts on `import`
`--temperature K --pressure BAR --ensemble E --timestep FS --model NAME|ID` record the MD
run that generated the file; `--source DIR` its working directory; `--sampled-from DS
[--sampling-method M]` a sampling step (frames linked by structure); `--code --functional
--dispersion --basis --pseudopotential [--labeled-from DS]` a DFT labeling calculation;
`--set key=value` other metadata; `--note TEXT`. Applied in the same transaction.

```bash
atomdb import /work/jpeters/project/run_042/output.extxyz \
    --temperature 400 --model mace-mh-1 --source /work/jpeters/project/run_042/
```

### `atomdb import-run DIR... [--yes] [--cluster NAME] [--type dft|md|other] [--input DS] [--sampled-from DS] [--model M] [--exclude GLOB]`
Import calculation directories copied back from an HPC system. Without `--yes` it only
shows what would be recorded. Recognises:

- **Slurm**: `#SBATCH` options of the submit script (partition, nodes, tasks, CPUs, GPUs,
  memory, time limit, account, `module load` lines) and one `JOB` per `slurm-<id>[_<task>].out`
  with failure state from the log (TIMEOUT, CANCELLED, OUT_OF_MEMORY, FAILED).
- **CP2K**: input (`@SET`/`@INCLUDE` aware; functional incl. libxc combinations such as
  r2SCAN, dispersion D3/D3BJ/D4, basis and pseudopotential per kind, cutoffs, EPS_SCF,
  smearing, charge, multiplicity, k-points, run type, MD settings for AIMD) — the full input
  tree is stored as `calc_param`s; output (version, git revision, hosts, start/end, number
  of force evaluations, unconverged SCF runs → warning).
- **PLUMED** input → enhanced-sampling record.
- **Structures**: unlabeled files are inputs, labeled files (or MD trajectories) outputs;
  both imported as datasets. CP2K `*-frc-*`/`*-vel-*` files are stored raw (they are not
  structures).
- **laufband / many jobs, one output**: one calculation linked to all jobs; frame↔job
  attribution is not invented.
- Every other file is stored (`part_of` the calculation). `*.wfn`, `*.bak-*`, core dumps are
  skipped by default.

Re-importing the same directory is detected.

### `atomdb info ID`
Everything known about any node (dataset, calculation, model, job, file).

### `atomdb provenance ID [--up] [--down] [--depth N] [--frame IDX] [--format tree|json|dot]`
**Where did this come from?** (`--up`, default) and **what was this used for?** (`--down`),
or both. `--frame` additionally prints a single frame's lineage across datasets.

### `atomdb ls [datasets|calculations|models|jobs|files] [--limit N] [--offset N] [--tag T]`
List nodes, newest first.

### `atomdb record md --output DS [--input DS] [--model M] [--temperature K] [--ensemble E] ...`
Full MD record: engine, ensemble, temperature, pressure, timestep, steps, length, thermostat,
barostat, integrator, seed, save interval; enhanced sampling via `--enhanced-sampling
METHOD --cv ... --bias key=value` or `--plumed plumed.dat` (parsed and stored);
`--param key=value`, `--input-file F` (stored), `--job JOB-ID`, `--placeholder`.

### `atomdb record dft --output DS [--input DS] --code --functional --dispersion ...`
DFT labeling: code/version, functional, dispersion, basis, pseudopotential, `--cutoff`,
`--rel-cutoff` (Ry), `--eps-scf`, smearing, charge, multiplicity, k-points, `--labels
energy,forces,stress`. Marks those properties of the output as computed by this
calculation and links output frames to input frames.

### `atomdb record sampling --parent DS --child DS --method M [--criterion k=v] [--frame-map F]`
Frame selection. Frames are linked by structure unless an explicit map is given.

### `atomdb record training --model M --train DS [--validation DS] [--hyperparam k=v] [--config F]`

### `atomdb model add [CHECKPOINT] --name N [--family F --version V --cutoff RC ...] [--trained-on DS]`
Register a model; checkpoint may be a file or directory (all files stored). Without a
checkpoint (foundation models) use `--url`. Fills in a placeholder of the same name.

### `atomdb model ls`

### `atomdb annotate ID [--set key=value]... [--note T] [--name N] [--description D]`
Add provenance later; for datasets, MD/DFT/sampling keys create structured records.

### `atomdb relate SRC RELATION DST [--role R]`
Add any relation (see PROVENANCE.md).

### `atomdb search [QUERY] [-e EL]... [--any] [--frames] [--sort S] [--limit N]`
Search datasets by everything in their upstream provenance. Terms are ANDed.

| Example | Meaning |
|---|---|
| `r2scan` | free text in names, descriptions, tags and all provenance facets |
| `r2scan+d4` | functional r2SCAN **and** dispersion D4 |
| `functional:r2scan`, `xc:PBE`, `disp:D3BJ`, `code:cp2k`, `basis:TZV2P`, `pseudo:GTH` | DFT settings (canonical vocabulary) |
| `T:400`, `400 K`, `T:300..400`, `T>350`, `p:1` | MD temperature / pressure (±0.5 for equality) |
| `model:MACE-MH-1`, `family:MACE` | generated by (or downstream of) a model |
| `el:K,Cl`, `contains K and Cl`, `el:K\|Na`, `only:K,Cl,O,H` | elements: all / any / exactly |
| `es:any`, `es:metadynamics`, `sampling:uncertainty`, `ensemble:NVT` | methods |
| `from:MD-2026-000017` | everything derived from a node, any depth |
| `lineage:r2scan` | anywhere upstream incl. earlier generations (e.g. the training data of the model that generated a trajectory) |
| `frames>100`, `atoms<500`, `has:forces`, `has:stress`, `tag:prod`, `name:kcl`, `cluster:hawk` | other |
| `config_type:md` (any other `key:value`) | dataset metadata set via `--set`/`annotate` |

Keyed terms and free text describe a dataset's **own generation**: the provenance walk
stops at models, so a trajectory generated by a model trained on r2SCAN/400 K data matches
`model:<that model>` but not `functional:r2scan` or `T:400` — use `lineage:` for that.

`--frames` returns frames instead (element filters use per-frame element bitmasks).
The output reports how the query was interpreted.

### `atomdb stats DS [--recompute]`
Statistics streamed chunk-wise from the canonical H5MD (never loads the whole file):
energy, energy/atom, max |F|, atoms per frame, volume, temperature (frame index); force
components and |F| over all atoms and **per element**; cell lengths/angles; density;
composition counts. Min/max/mean/std + 60-bin histograms. Cached in
`$ATOMDB_HOME/cache/stats` (keyed by file checksum).

### `atomdb compare DS_REF DS [DS...]`
The same statistics on common histogram bins plus, per quantity, the mean shift in units
of the pooled standard deviation and the histogram overlap (1 = identical, 0 = disjoint),
and which elements differ.

### `atomdb reindex`
Rebuild the search index. Normally unnecessary: facets are refreshed automatically on
every commit that changes provenance.

### `atomdb check [DS] [--incomplete]`
Provenance completeness checklist per dataset: origin known? label method (DFT) known?
functional / dispersion / basis / code version? MD temperature and driving potential?
placeholder models? HPC job / working directory? Each missing item comes with the
command that fills it in.

### `atomdb suggest [DS]`
Datasets whose frames all occur in another dataset but which are not connected in the
graph → suggested `sampled_from` / `derived_from` relations with the `atomdb relate`
command to accept them. Nothing is applied automatically.

### `atomdb merge PLACEHOLDER REAL`
Replace a placeholder node by the real one everywhere.

### `atomdb tag ID TAG...`
Add tags.

### `atomdb serve [--host 127.0.0.1] [--port 8765]`
Start the web UI and HTTP API.
