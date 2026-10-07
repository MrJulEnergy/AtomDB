"""``atomdb`` command-line interface. Every command supports ``--json``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .. import __version__
from ..services import AtomDB
from ..services import datasets as dsvc
from ..services.importer import import_file
from .output import emit, handle_errors, kv, state, table

app = typer.Typer(
    help="atomdb — local atomistic data & provenance database.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    add_completion=False,
)

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")]


@app.callback()
def _main(
    home: Annotated[
        str | None,
        typer.Option("--home", envvar="ATOMDB_HOME", help="Store root (default: $ATOMDB_HOME)."),
    ] = None,
    json_: JsonOpt = False,
):
    state.home = home
    state.json = json_


def _json(flag: bool) -> None:
    if flag:
        state.json = True


def open_db() -> AtomDB:
    return AtomDB.open(state.home)


@app.command()
@handle_errors
def version(json_: JsonOpt = False):
    """Print the atomdb version."""
    _json(json_)
    emit({"version": __version__}, lambda d: print(d["version"]))


@app.command()
@handle_errors
def init(json_: JsonOpt = False):
    """Create (or upgrade) the store at $ATOMDB_HOME. Never deletes anything."""
    _json(json_)
    adb = AtomDB.init(state.home)
    data = {"root": str(adb.paths.root), "db": str(adb.paths.db), "instance": adb.db.instance_uuid()}
    emit(data, lambda d: print(f"atomdb store ready at {d['root']}"))


@app.command("import")
@handle_errors
def import_(
    paths: Annotated[list[Path], typer.Argument(help="Files to import (copied, never moved).")],
    name: Annotated[str | None, typer.Option(help="Dataset name (default: file stem).")] = None,
    description: Annotated[str | None, typer.Option("--description", "-d")] = None,
    tag: Annotated[list[str], typer.Option("--tag", "-t", help="Tag (repeatable).")] = [],
    fmt: Annotated[str | None, typer.Option("--format", help="Force a file format.")] = None,
    json_: JsonOpt = False,
):
    """Import atomistic files into the store."""
    _json(json_)
    adb = open_db()
    results = [
        import_file(adb, p, name=name if len(paths) == 1 else None, description=description,
                    tags=tag, fmt=fmt)
        for p in paths
    ]

    def human(rs):
        for r in rs:
            print(r.message)
            for w in r.warnings:
                print(f"  warning: {w}")

    emit(results, human)


@app.command()
@handle_errors
def info(node_id: str, json_: JsonOpt = False):
    """Show everything known about a dataset (or other node)."""
    _json(json_)
    adb = open_db()
    with adb.session() as s:
        ds = dsvc.get_dataset(s, node_id)
        d = dsvc.detail(adb, s, ds)

    def human(d):
        print(f"{d.id}  {d.name}")
        kv([
            ("status", d.status),
            ("description", d.description),
            ("frames", d.n_frames),
            ("atoms", f"{d.n_atoms_min}..{d.n_atoms_max}" if d.n_atoms_min is not None else None),
            ("formula", d.formula_summary),
            ("elements", " ".join(d.elements)),
            ("created", d.created_at.isoformat(timespec="seconds")),
            ("tags", ", ".join(d.tags)),
        ], indent=2)
        print("  files:")
        for df in d.files:
            print(f"    [{df.role}] {df.file.id} {df.file.name} ({df.file.size_bytes} B)")
            print(f"      sha256 {df.file.sha256}")
            for o in df.file.origins:
                print(f"      from {o.hostname}:{o.original_path}")

    emit(d, human)


@app.command("ls")
@handle_errors
def ls(
    limit: int = 50,
    offset: int = 0,
    tag: Annotated[str | None, typer.Option(help="Only datasets with this tag.")] = None,
    json_: JsonOpt = False,
):
    """List datasets (newest first)."""
    _json(json_)
    adb = open_db()
    with adb.session() as s:
        total, rows = dsvc.list_datasets(s, offset=offset, limit=limit, tag=tag)
        items = [dsvc.summary(s, d) for d in rows]
    data = {"total": total, "offset": offset, "limit": limit, "items": items}

    def human(d):
        table(
            ["ID", "NAME", "FRAMES", "ATOMS", "ELEMENTS", "CREATED"],
            [[i.id, i.name, i.n_frames, i.n_atoms_max, " ".join(i.elements),
              i.created_at.strftime("%Y-%m-%d")] for i in d["items"]],
        )
        print(f"({len(d['items'])} of {d['total']})")

    emit(data, human)


@app.command()
@handle_errors
def tag(node_id: str, tags: list[str], json_: JsonOpt = False):
    """Add tags to a node."""
    _json(json_)
    adb = open_db()
    with adb.session() as s:
        node = dsvc.get_node(s, node_id)
        dsvc.add_tags(s, node, tags)
        adb.audit(s, "node.tag", node.id, tags=tags)
        result = {"id": node.id, "tags": sorted(t.name for t in node.tags)}
    emit(result, lambda d: print(f"{d['id']}: {', '.join(d['tags'])}"))


@app.command()
@handle_errors
def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
):
    """Start the web UI / HTTP API."""
    import uvicorn

    from ..api.app import create_app

    adb = open_db()
    uvicorn.run(create_app(adb), host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    app()
