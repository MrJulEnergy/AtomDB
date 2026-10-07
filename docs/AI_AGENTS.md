# Using atomdb from an AI agent (Claude Code etc.)

Start with `atomdb schema --json`: it lists entities, relations, ID formats, search syntax,
exit codes and the typical workflows below.

## Conventions

- Always pass `--json`. Output is `{"ok": true, "data": …}` or
  `{"ok": false, "error": {"code", "message", "details"}}`; the exit code matches the error
  class (2 invalid input, 3 no store, 4 not found, 5 bad file, 6 data loss, 7 integrity).
- IDs are stable: `DS-2026-000142` (datasets), `MD-/DFT-/SMP-/TRN-/CNV-/IMP-/CALC-`
  (calculations), `MDL-` (models), `JOB-` (HPC jobs), `FL-` (files). Input is
  case-insensitive and accepts short numbers (`ds-2026-142`).
- Nothing is ever deleted or overwritten by atomdb. Imports copy; conversions refuse to
  drop data unless `--allow-loss`; restores need an empty target.
- Import commands without `--yes` (`import-run`) are dry runs: inspect, then repeat with `--yes`.
- Units: eV, Å, eV/Å, eV/Å³, fs, K, bar, Ry (cutoffs).

## Recipes

| Task | Commands |
|---|---|
| Identify an unknown file | `atomdb whatis PATH --json` → `atomdb provenance ID --up --json` |
| What was something used for | `atomdb provenance ID --down --json` |
| Find datasets | `atomdb search "r2scan+d4 el:K,Cl T:400" --json` |
| Find frames by elements | `atomdb search --element K --element Cl --frames --json` |
| Import with provenance | `atomdb import F --temperature 400 --model MACE-MH-1 --source DIR --json` |
| Import an HPC run | `atomdb import-run DIR --cluster X --json`, then add `--yes` |
| Import existing data | `atomdb scan ROOT --plan plan.yaml --json`, edit, `atomdb import-plan plan.yaml --json` |
| Fill provenance gaps | `atomdb check --incomplete --json`, `atomdb suggest --json`, `atomdb annotate/relate/record` |
| Statistics | `atomdb stats DS --json`, `atomdb compare DS1 DS2 --json` |
| New vs old data | `atomdb similarity TRAIN NEW --json` (exploratory) |
| Use data elsewhere | `atomdb link DS DEST` (read-only symlink into the store) |
| Integrity / backup | `atomdb validate --json`, `atomdb backup --json` |

The HTTP API (`atomdb serve`, `/api/v1`, OpenAPI at `/api/v1/openapi.json`) exposes the
same data; see API.md.
