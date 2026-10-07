"""``atomdb`` command-line interface. Every command supports ``--json``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .. import __version__
from ..services import AtomDB
from ..services import datasets as dsvc
from ..services.importer import import_file, import_raw
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
    fmt: Annotated[str | None, typer.Option("--format", "-f", help="Force a file format.")] = None,
    type_map: Annotated[
        str | None, typer.Option(help="LAMMPS type→element map, e.g. 1=K,2=Cl,3=O,4=H")
    ] = None,
    raw: Annotated[
        bool, typer.Option(help="Store bytes without parsing (inputs, logs, checkpoints).")
    ] = False,
    json_: JsonOpt = False,
):
    """Import atomistic files into the store (original kept + canonical H5MD)."""
    _json(json_)
    adb = open_db()
    read_options = {"type_map": type_map} if type_map else {}
    results = []
    for p in paths:
        if raw:
            results.append(import_raw(adb, p, tags=tag))
        else:
            results.append(import_file(
                adb, p, name=name if len(paths) == 1 else None, description=description,
                tags=tag, fmt=fmt, read_options=read_options,
            ))

    def human(rs):
        for r in rs:
            print(r.message)
            for w in r.warnings:
                print(f"  warning: {w}")

    emit(results, human)


@app.command()
@handle_errors
def convert(
    src: Annotated[str, typer.Argument(help="Input file or dataset ID.")],
    out: Annotated[Path, typer.Argument(help="Output file (never overwritten silently).")],
    to: Annotated[str | None, typer.Option(help="Output format (default: from extension).")] = None,
    fmt: Annotated[str | None, typer.Option("--from", help="Input format (default: detect).")] = None,
    allow_loss: Annotated[bool, typer.Option(help="Accept dropping unrepresentable fields.")] = False,
    overwrite: bool = False,
    type_map: Annotated[str | None, typer.Option(help="LAMMPS type→element map.")] = None,
    no_record: Annotated[bool, typer.Option(help="Do not record in the database.")] = False,
    json_: JsonOpt = False,
):
    """Convert between formats. Lost fields are reported; lossy conversion needs --allow-loss."""
    from ..config import get_paths
    from ..services.converter import convert as do_convert

    _json(json_)
    adb = None if no_record or not get_paths(state.home).initialized else open_db()
    r = do_convert(
        adb, src, out, to=to, frm=fmt, allow_loss=allow_loss, overwrite=overwrite,
        read_options={"type_map": type_map} if type_map else None,
    )

    def human(r):
        print(f"{r['from']} → {r['to']}: {r['n_frames']} frames written to {r['output']}")
        if r["lost_fields"]:
            print(f"  LOST: {', '.join(r['lost_fields'])}")
        if r["conversion_id"]:
            print(f"  recorded as {r['conversion_id']}")

    emit(r, human)


@app.command()
@handle_errors
def formats(json_: JsonOpt = False):
    """List supported file formats and what they can represent."""
    from ..formats.registry import adapters

    _json(json_)
    data = [
        {"name": a.name, "extensions": list(a.extensions), "read": a.readable,
         "write": a.writable, "description": a.description,
         "read_options": list(a.read_options), "capabilities": a.capabilities.to_dict()}
        for a in adapters().values()
    ]
    emit(data, lambda d: table(
        ["FORMAT", "EXT", "R", "W", "DESCRIPTION"],
        [[x["name"], " ".join(x["extensions"]), "✓" if x["read"] else "", "✓" if x["write"] else "",
          x["description"]] for x in d],
    ))


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
