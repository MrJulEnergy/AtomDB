# HTTP API

`atomdb serve` exposes a JSON API under `/api/v1`. Interactive OpenAPI docs:
`/api/v1/docs`; machine-readable spec: `/api/v1/openapi.json`.

Errors use `{"error": {"code": ..., "message": ..., "details": {...}}}` with the same
codes as the CLI (see CLI.md); `not_found` → 404, `invalid_input`/`format_error` → 422.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | version and store location |
| GET | `/api/v1/datasets?offset=&limit=&tag=` | paginated dataset summaries (`{total, offset, limit, items}`) |
| GET | `/api/v1/datasets/{id}` | dataset detail incl. files and origins |
