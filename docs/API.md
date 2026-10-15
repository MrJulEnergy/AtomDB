# HTTP API

`atomdb serve` exposes a JSON API under `/api/v1`. Interactive OpenAPI docs:
`/api/v1/docs`; machine-readable spec: `/api/v1/openapi.json`.

Errors use `{"error": {"code": ..., "message": ..., "details": {...}}}` with the same
codes as the CLI (see CLI.md); `not_found` → 404, `invalid_input`/`format_error` → 422.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | version and store location |
| GET | `/api/v1/datasets?q=&offset=&limit=&sort=` | dataset summaries + provenance facets (`extra`); `q` = search language |
| GET | `/api/v1/datasets/{id}` | dataset detail incl. files and origins |
| GET | `/api/v1/datasets/{id}/frames?offset=&limit=` | frame index (energy, fmax, T, volume, ... — no coordinates) |
| GET | `/api/v1/datasets/{id}/frames/{idx}` | one frame, lazily read from H5MD: symbols, positions, cell, pbc, calc results, info, arrays |
| GET | `/api/v1/datasets/{id}/properties` | catalog of every stored field (origin, level, dtype, shape, unit, label calculation) |
| GET | `/api/v1/datasets/{id}/completeness` | provenance checklist |
| GET | `/api/v1/datasets/{id}/stats?recompute=` | streaming statistics + histograms (`quantities`, `force_per_element`, `composition`) |
| GET | `/api/v1/compare?ds=A&ds=B` | datasets on common bins + `summary` (mean shift in σ, histogram overlap) |
| GET | `/api/v1/datasets/{id}/frames/{idx}/lineage` | frame-level lineage chain |
| GET | `/api/v1/search?q=&element=&any=` | same as `/datasets` with element list, returns `interpreted` |
| GET | `/api/v1/categories?q=` | dataset counts per browse category (`geometry`, `data_type`, `dft_code`, `mlip_family`) among datasets matching `q` |
| GET | `/api/v1/frames?all=K&all=Cl&any=&dataset=` | frames by element combination |
| GET | `/api/v1/elements` | elements present (datasets / frames / atoms per element) |
| GET | `/api/v1/embedding?ds=A&ds=B&descriptor=&max_frames=&seed=` | EXPLORATORY PCA projection: `points[{dataset, frame, pc1, pc2, energy_per_atom, fmax, ...}]`, `explained_variance` |
| GET | `/api/v1/embedding/atoms?ds=A&ds=B&descriptor=mace-mp0-small&max_frames=&max_atoms=&element=O&allow_large=` | EXPLORATORY per-atom PCA: `points[{dataset, frame, atom, element, pc1, pc2}]`, per-dataset element counts, `skipped` (large datasets) |
| GET | `/api/v1/similarity?reference=&other=&descriptor=` | EXPLORATORY novelty fraction, most novel frames, element and distribution differences |
| GET | `/api/v1/descriptors` | available descriptors |
| GET | `/api/v1/nodes/{id}` | any node, fully described (MD/DFT/sampling/training blocks, code params, attrs, software versions) |
| GET | `/api/v1/nodes?kind=calculations\|models\|jobs\|files` | list nodes |
| GET | `/api/v1/nodes/{id}/provenance?direction=up\|down\|both&depth=` | provenance subgraph `{root, direction, nodes[{id, kind, name, status, subtitle, level}], edges[{src, dst, relation, role, source, attrs}]}` |
| GET | `/api/v1/relations` | relation vocabulary |
| GET | `/api/v1/suggestions?dataset=` | relations suggested by shared frames |

## Actions (POST; paths refer to the machine running the server)

| Method | Path | Body / params | Description |
|---|---|---|---|
| POST | `/api/v1/import` | `{path, name?, description?, tags?, format?, read_options?, metadata?}` | import a file; `metadata` = provenance shortcuts (temperature, model, source, sampled_from, functional, dispersion, code, …) |
| POST | `/api/v1/import-run` | `{path, execute=false, cluster?, calc_type?, inputs?, sampled_from?, model?}` | plan (default) or execute a run-directory import; an IPSuite project root returns `{runs: [...]}` |
| POST | `/api/v1/convert` | `{input, output, to?, allow_loss=false}` | conversion; 409 `data_loss` lists lost fields |
| POST | `/api/v1/whatis` | `{path}` | identify a file |
| POST | `/api/v1/validate?dataset=&quick=true` | | integrity report |
| POST | `/api/v1/backup` | | create a backup archive |
| GET | `/api/v1/store` | | store location, sizes, backups |
| GET | `/api/v1/schema` | | same as `atomdb schema --json` |

## Editing (web UI "✎ edit metadata")

Changes are applied in place and written to `audit_log` with old and new values.

| Method | Path | Body | Purpose |
|---|---|---|---|
| PATCH | `/api/v1/nodes/{id}` | `{name?, label?, description?, tags?, note?, attrs?, dft?, md?, model?}` | edit any node; `tags` replaces the list, `attrs` value `null` removes a key; `dft`/`md`/`model` only for calculations of that type |
| GET | `/api/v1/datasets/{id}/settings` | | the DFT calculation(s) that labeled the dataset and MD run(s) that generated it, with editable fields and `shared_with` (other outputs of the same calculation) |
| PUT | `/api/v1/datasets/{id}/settings/{dft\|md}` | `{values, inputs?, model?, calc_id?}` | edit that calculation in place, or record one if none exists (same as `record dft/md`); `also_affects` lists other datasets sharing it |
| POST | `/api/v1/copy-metadata` | `{source, targets: [...], parts?: [dft, md, attrs, tags]}` | apply one dataset's settings, code input, config files, metadata and tags to many datasets; input structures are not copied |
| POST | `/api/v1/edges` | `{src, relation, dst, role?}` | add a relation (as `atomdb relate`) |
