"""``atomdb`` command-line interface. Every command supports ``--json``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .. import __version__
from ..db.models import Dataset, File
from ..errors import InvalidInput
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


def _read_options(type_map: str | None, topology: Path | None) -> dict:
    opts: dict = {}
    if type_map:
        opts["type_map"] = type_map
    if topology:
        opts["topology"] = str(topology.expanduser().resolve())
    return opts


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
    topology: Annotated[
        Path | None, typer.Option(help="Topology for DCD/XTC/TRR/NetCDF (stored as input).")
    ] = None,
    raw: Annotated[
        bool, typer.Option(help="Store bytes without parsing (inputs, logs, checkpoints).")
    ] = False,
    json_: JsonOpt = False,
):
    """Import atomistic files into the store (original kept + canonical H5MD)."""
    _json(json_)
    adb = open_db()
    read_options = _read_options(type_map, topology)
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
    topology: Annotated[Path | None, typer.Option(help="Topology for binary MD formats.")] = None,
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
        read_options=_read_options(type_map, topology),
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
def whatis(
    path: Path,
    max_frames: Annotated[int, typer.Option(help="Frames to fingerprint at most.")] = 5000,
    json_: JsonOpt = False,
):
    """Identify an unknown file: exact checksum, embedded stamp, or matching frames."""
    from ..services.whatis import whatis as do_whatis

    _json(json_)
    r = do_whatis(open_db(), path, max_frames=max_frames)

    def human(r):
        print(f"{r['path']}")
        print(f"  sha256 {r['sha256']}  ({r['size_bytes']} B, format {r['format'] or '?'}, "
              f"{r['n_frames'] if r['n_frames'] is not None else '?'} frames)")
        print(f"  VERDICT: {r['verdict']}")
        if r["exact_match"]:
            em = r["exact_match"]
            print(f"  exact match: {em['file']['id']} stored at {em['file']['store_path']}")
            for d in em["datasets"]:
                print(f"    dataset {d['id']}  {d['name']}")
            for o in em["file"]["origins"]:
                print(f"    imported from {o['hostname']}:{o['original_path']} at {o['imported_at']}")
        if r["stamp"]:
            st = r["stamp"]
            print("  embedded atomdb stamp:")
            for k in ("dataset_id", "exported_from_dataset", "source_name", "source_path",
                      "source_sha256", "created_at", "same_instance"):
                if st.get(k) not in (None, ""):
                    print(f"    {k}: {st[k]}")
        for m in r["content_matches"]:
            d = m["dataset"]
            print(f"  content: {m['matched_frames']}/{r['frames_checked']} checked frames match "
                  f"{d['id']} ({d['name']}) frames {m['dataset_frames']}"
                  f"{'  [coordinates confirmed]' if m['coordinates_confirmed'] else ''}")
        if r["exact_match"] or r["content_matches"]:
            print("  next: atomdb provenance <ID> --up   (full history)")

    emit(r, human)


@app.command()
@handle_errors
def link(
    node_id: str,
    dest: Annotated[Path, typer.Argument(help="Target path or directory.")] = Path("."),
    original: Annotated[bool, typer.Option(help="Link the original file instead of H5MD.")] = False,
    json_: JsonOpt = False,
):
    """Create a read-only symlink to a dataset's file in the store."""
    from ..store.links import make_link

    _json(json_)
    adb = open_db()
    with adb.session() as s:
        node = dsvc.get_node(s, node_id)
        if isinstance(node, File):
            f = node
        elif isinstance(node, Dataset):
            role = "original" if original else "canonical_h5md"
            f = next(df.file for df in node.files if df.role == role)
        else:
            raise InvalidInput(f"{node.id} is a {node.kind}; link a dataset or file")
        p = make_link(s, adb.store.abspath(f.store_relpath), f, dest, node.id)
        adb.audit(s, "link.create", node.id, path=str(p))
        data = {"link": str(p.absolute()), "target": str(adb.store.abspath(f.store_relpath)),
                "file_id": f.id}
    emit(data, lambda d: print(f"{d['link']} -> {d['target']}"))


@app.command()
@handle_errors
def scan(
    root: Path,
    plan: Annotated[Path | None, typer.Option(help="Write an editable YAML import plan.")] = None,
    exclude: Annotated[list[str], typer.Option(help="Directory names to skip (repeatable).")] = [],
    no_models: Annotated[bool, typer.Option(help="Ignore model checkpoints.")] = False,
    max_depth: int | None = None,
    json_: JsonOpt = False,
):
    """Find existing atomistic data under ROOT (read-only) and report what is new."""
    from ..services.scan import DEFAULT_EXCLUDE, write_plan
    from ..services.scan import scan as do_scan

    _json(json_)
    r = do_scan(open_db(), root, exclude=DEFAULT_EXCLUDE + tuple(exclude),
                include_models=not no_models, max_depth=max_depth)
    if plan:
        r["plan_file"] = str(write_plan(r, plan).resolve())

    def human(r):
        table(["STATUS", "KIND", "FORMAT", "SIZE", "PATH", "KNOWN AS"],
              [[i["status"], i["kind"], i["format"] or "", i["size_bytes"],
                i["path"], " ".join(i["known_as"])] for i in r["items"]])
        sm = r["summary"]
        print(f"{sm['new']} new, {sm['in_db']} already stored, "
              f"{sm['possible_duplicate']} possible duplicates")
        if r["run_dirs"]:
            print("directories that look like calculation runs (use import-run):")
            for d in r["run_dirs"]:
                print(f"  {d}")
        if r["model_dirs"]:
            print("directories that look like model checkpoints (use model add):")
            for d in r["model_dirs"]:
                print(f"  {d}")
        if r.get("plan_file"):
            print(f"plan written to {r['plan_file']} — edit, then: atomdb import-plan {r['plan_file']}")

    emit(r, human)


@app.command("import-plan")
@handle_errors
def import_plan(
    plan: Path,
    dry_run: bool = False,
    json_: JsonOpt = False,
):
    """Import everything listed in a (reviewed) plan written by `atomdb scan --plan`."""
    from ..services.scan import execute_plan, load_plan

    _json(json_)
    results = execute_plan(open_db(), load_plan(plan), dry_run=dry_run)

    def human(rs):
        for r in rs:
            extra = r.get("id") or (r.get("error") or {}).get("message", "")
            print(f"{r['action']:<10} {r['path']}  {extra}")

    emit(results, human)


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
