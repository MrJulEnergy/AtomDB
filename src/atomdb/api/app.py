"""FastAPI application: JSON API under /api/v1 plus the built web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..errors import AtomDBError
from ..services import AtomDB
from .routers import actions, datasets, edit, nodes, tags

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"
API_PREFIX = "/api/v1"


def create_app(adb: AtomDB) -> FastAPI:
    app = FastAPI(
        title="atomdb",
        version=__version__,
        description="Local atomistic data & provenance database. See docs/API.md.",
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.state.adb = adb

    @app.exception_handler(AtomDBError)
    async def _atomdb_error(_req: Request, err: AtomDBError):
        return JSONResponse(status_code=err.http_status, content={"error": err.to_dict()})

    @app.exception_handler(Exception)
    async def _unexpected(_req: Request, err: Exception):
        # keep the error envelope for bugs too (the traceback is still logged)
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal", "message": f"{type(err).__name__}: {err}", "details": {}}})

    @app.get(f"{API_PREFIX}/health")
    def health():
        return {"ok": True, "version": __version__, "store": str(adb.paths.root)}

    app.include_router(datasets.router, prefix=API_PREFIX)
    app.include_router(nodes.router, prefix=API_PREFIX)
    app.include_router(actions.router, prefix=API_PREFIX)
    app.include_router(edit.router, prefix=API_PREFIX)
    app.include_router(tags.router, prefix=API_PREFIX)

    index = STATIC_DIR / "index.html"
    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"error": {"code": "not_found"}})
        if index.exists():
            return FileResponse(index)
        return JSONResponse(
            {"message": "frontend not built; run `npm run build` in frontend/", "api": API_PREFIX}
        )

    return app
