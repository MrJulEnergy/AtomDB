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
| GET | `/api/v1/frames?all=K&all=Cl&any=&dataset=` | frames by element combination |
| GET | `/api/v1/elements` | elements present (datasets / frames / atoms per element) |
| GET | `/api/v1/embedding?ds=A&ds=B&descriptor=&max_frames=&seed=` | EXPLORATORY PCA projection: `points[{dataset, frame, pc1, pc2, energy_per_atom, fmax, ...}]`, `explained_variance` |
| GET | `/api/v1/similarity?reference=&other=&descriptor=` | EXPLORATORY novelty fraction, most novel frames, element and distribution differences |
| GET | `/api/v1/descriptors` | available descriptors |
| GET | `/api/v1/nodes/{id}` | any node, fully described (MD/DFT/sampling/training blocks, code params, attrs, software versions) |
| GET | `/api/v1/nodes?kind=calculations\|models\|jobs\|files` | list nodes |
| GET | `/api/v1/nodes/{id}/provenance?direction=up\|down\|both&depth=` | provenance subgraph `{root, direction, nodes[{id, kind, name, status, subtitle, level}], edges[{src, dst, relation, role, source, attrs}]}` |
| GET | `/api/v1/relations` | relation vocabulary |
| GET | `/api/v1/suggestions?dataset=` | relations suggested by shared frames |
