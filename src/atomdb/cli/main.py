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


def _register_subapps() -> None:
    from . import model, record

    app.add_typer(record.app, name="record")
    app.add_typer(model.app, name="model")


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
    temperature: Annotated[float | None, typer.Option(
        help="MD temperature in K (records the generating MD run).",
        rich_help_panel="Provenance shortcuts")] = None,
    pressure: Annotated[float | None, typer.Option(
        help="bar", rich_help_panel="Provenance shortcuts")] = None,
    ensemble: Annotated[str | None, typer.Option(rich_help_panel="Provenance shortcuts")] = None,
    timestep: Annotated[float | None, typer.Option(
        help="fs", rich_help_panel="Provenance shortcuts")] = None,
    model: Annotated[str | None, typer.Option(
        help="Model (ID or name) that generated this trajectory.",
        rich_help_panel="Provenance shortcuts")] = None,
    source: Annotated[str | None, typer.Option(
        help="Directory where the data was produced.", rich_help_panel="Provenance shortcuts")
    ] = None,
    sampled_from: Annotated[str | None, typer.Option(
        help="Dataset these frames were selected from.", rich_help_panel="Provenance shortcuts")
    ] = None,
    sampling_method: Annotated[str | None, typer.Option(
        rich_help_panel="Provenance shortcuts")] = None,
    labeled_from: Annotated[str | None, typer.Option(
        help="Dataset of the unlabeled structures (DFT input).",
        rich_help_panel="Provenance shortcuts")] = None,
    code: Annotated[str | None, typer.Option(
        help="DFT code (records a DFT labeling calculation).",
        rich_help_panel="Provenance shortcuts")] = None,
    functional: Annotated[str | None, typer.Option(rich_help_panel="Provenance shortcuts")] = None,
    dispersion: Annotated[str | None, typer.Option(rich_help_panel="Provenance shortcuts")] = None,
    basis: Annotated[str | None, typer.Option(rich_help_panel="Provenance shortcuts")] = None,
    pseudopotential: Annotated[str | None, typer.Option(
        rich_help_panel="Provenance shortcuts")] = None,
    set_: Annotated[list[str], typer.Option(
        "--set", help="Extra metadata key=value.", rich_help_panel="Provenance shortcuts")] = [],
    note: Annotated[str | None, typer.Option(rich_help_panel="Provenance shortcuts")] = None,
    json_: JsonOpt = False,
):
    """Import atomistic files into the store (original kept + canonical H5MD).

    Provenance shortcuts record how the data was made in the same transaction, e.g.
    `atomdb import out.extxyz --temperature 400 --model MACE-MH-1 --source /work/run_042`.
    """
    from ..services.annotate import add_note, apply_annotations
    from .parsing import key_values

    _json(json_)
    adb = open_db()
    read_options = _read_options(type_map, topology)
    meta = {k: v for k, v in {
        "temperature": temperature, "pressure": pressure, "ensemble": ensemble,
        "timestep": timestep, "model": model, "source": source, "sampled_from": sampled_from,
        "sampling_method": sampling_method, "labeled_from": labeled_from, "code": code,
        "functional": functional, "dispersion": dispersion, "basis": basis,
        "pseudopotential": pseudopotential}.items() if v is not None}
    meta.update(key_values(set_))
    results = []
    for p in paths:
        if raw:
            results.append(import_raw(adb, p, tags=tag))
            continue
        with adb.session() as s:
            r = import_file(
                adb, p, name=name if len(paths) == 1 else None, description=description,
                tags=tag, fmt=fmt, read_options=read_options, session=s,
            )
            if r.duplicate and (meta or note):
                r.warnings.append("duplicate: provenance flags not applied to the existing "
                                  "dataset (use `atomdb annotate`)")
            elif meta or note:
                created = apply_annotations(s, r.dataset.id, meta)
                if note:
                    add_note(s, r.dataset.id, note)
                if created:
                    r.message += f"; recorded {', '.join(created)}"
        results.append(r)

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
    """Everything known about a dataset, calculation, model, job or file."""
    from ..services.nodes import describe

    _json(json_)
    adb = open_db()
    with adb.session() as s:
        d = describe(adb, s, dsvc.get_node(s, node_id))
    emit(d, print_node)


def print_node(d: dict) -> None:
    print(f"{d['id']}  {d['name']}" + ("" if d["status"] == "active" else f"  [{d['status']}]"))
    if d.get("subtitle"):
        print(f"  {d['subtitle']}")
    rows = [("kind", d["kind"]), ("description", d.get("description")),
            ("created", str(d.get("created_at", ""))[:19]), ("tags", ", ".join(d.get("tags", [])))]
    if d["kind"] == "dataset":
        rows += [("frames", d.get("n_frames")),
                 ("atoms", f"{d['n_atoms_min']}..{d['n_atoms_max']}"
                  if d.get("n_atoms_min") is not None else None),
                 ("formula", d.get("formula_summary")), ("elements", " ".join(d["elements"])),
                 ("labels", " ".join(k for k in ("energy", "forces", "stress")
                                     if d.get(f"has_{k}"))),
                 ("energy range", f"{d['energy_min']:.6g} .. {d['energy_max']:.6g} eV"
                  if d.get("energy_min") is not None else None),
                 ("max |F|", f"{d['fmax_max']:.4g} eV/Å" if d.get("fmax_max") else None)]
    for key in ("calc_type", "workdir", "command", "family", "version", "cutoff", "url",
                "cluster", "partition", "job_id", "array_job_id", "array_task_id", "user",
                "n_cpus", "n_gpus", "start", "end", "state", "sha256", "size_bytes", "format",
                "store_path"):
        if d.get(key) not in (None, ""):
            rows.append((key, d[key]))
    kv(rows, indent=2)
    for block in ("md", "dft", "sampling", "training"):
        if d.get(block):
            print(f"  {block}:")
            kv(list(d[block].items()), indent=4)
    for es in d.get("enhanced_sampling") or []:
        print("  enhanced sampling:")
        kv(list(es.items()), indent=4)
    if d.get("code_params"):
        print("  code parameters:")
        kv(sorted(d["code_params"].items()), indent=4)
    if d.get("attrs"):
        print("  metadata:")
        kv([(k, f"{v['value']}{' ' + v['unit'] if v['unit'] else ''}  ({v['source']})")
            for k, v in d["attrs"].items()], indent=4)
    if d.get("software_versions"):
        print("  software: " + ", ".join(f"{k} {v}" for k, v in d["software_versions"].items()
                                         if v and k != "platform"))
    for f in d.get("files", []):
        print(f"  [{f['role']}] {f['file']['id']} {f['file']['name']} "
              f"({f['file']['size_bytes']} B)  sha256 {f['file']['sha256'][:16]}…")
        for o in f["file"]["origins"]:
            print(f"      from {o['hostname']}:{o['original_path']}")
    for n in d.get("notes", []):
        print(f"  note: {n}")
    print(f"  → atomdb provenance {d['id']} --up   |   --down")


@app.command()
@handle_errors
def provenance(
    node_id: str,
    up: Annotated[bool, typer.Option("--up", help="Where did this come from? (default)")] = False,
    down: Annotated[bool, typer.Option("--down", help="What was this used for?")] = False,
    depth: int = 50,
    frame: Annotated[int | None, typer.Option(help="Trace a single frame of a dataset.")] = None,
    fmt: Annotated[str, typer.Option("--format", help="tree | json | dot")] = "tree",
    json_: JsonOpt = False,
):
    """Provenance graph of a node: --up (origin, default), --down (uses), or both."""
    from ..services.lineage import frame_lineage
    from ..services.provenance import provenance_graph, render_dot, render_tree

    _json(json_)
    direction = "both" if up and down else ("down" if down else "up")
    adb = open_db()
    with adb.session() as s:
        node = dsvc.get_node(s, node_id)
        g = provenance_graph(adb, s, node.id, direction, depth)
        if frame is not None:
            g["frame_lineage"] = frame_lineage(s, node.id, frame)
    if fmt == "dot" and not state.json:
        print(render_dot(g))
        return
    if fmt == "json":
        state.json = True

    def human(g):
        if g.get("frame_lineage"):
            print("frame lineage:")
            for step in g["frame_lineage"]:
                via = f"   (via {step['via']})" if step["via"] else ""
                print(f"  {step['dataset']} frame {step['frame']}{via}")
            print()
        if direction == "both":
            for d in ("up", "down"):
                sub = {**g, "direction": d}
                print(f"== {'WHERE DID THIS COME FROM' if d == 'up' else 'WHAT WAS THIS USED FOR'}")
                print(render_tree(sub))
        else:
            print(render_tree(g))

    emit(g, human)


@app.command("ls")
@handle_errors
def ls(
    kind: Annotated[str, typer.Argument(
        help="datasets | calculations | models | jobs | files")] = "datasets",
    limit: int = 50,
    offset: int = 0,
    tag: Annotated[str | None, typer.Option(help="Only nodes with this tag.")] = None,
    json_: JsonOpt = False,
):
    """List nodes of a kind (newest first)."""
    _json(json_)
    adb = open_db()
    if kind == "datasets":
        with adb.session() as s:
            total, rows = dsvc.list_datasets(s, offset=offset, limit=limit, tag=tag)
            items = [dsvc.summary(s, d) for d in rows]
        data = {"total": total, "offset": offset, "limit": limit, "items": items}

        def human(d):
            table(
                ["ID", "NAME", "FRAMES", "ATOMS", "ELEMENTS", "LABELS", "CREATED"],
                [[i.id, i.name, i.n_frames, i.n_atoms_max, " ".join(i.elements),
                  "".join(x for x, f in (("E", i.has_energy), ("F", i.has_forces),
                                         ("S", i.has_stress)) if f),
                  i.created_at.strftime("%Y-%m-%d")] for i in d["items"]],
            )
            print(f"({len(d['items'])} of {d['total']})")

        emit(data, human)
        return
    from ..services.nodes import describe

    kinds = {"calculations": "calculation", "models": "model", "jobs": "hpc_job",
             "files": "file"}
    if kind not in kinds:
        raise InvalidInput(f"unknown kind {kind!r}; use datasets, {', '.join(kinds)}")
    from sqlalchemy import func, select

    from ..db.models import Node

    with adb.session() as s:
        q = select(Node).where(Node.kind == kinds[kind])
        total = s.execute(select(func.count()).select_from(q.subquery())).scalar_one()
        rows = s.execute(q.order_by(Node.created_at.desc()).offset(offset).limit(limit)).scalars()
        items = [describe(adb, s, n, full=False) for n in rows]
    emit({"total": total, "offset": offset, "limit": limit, "items": items},
         lambda d: (table(["ID", "NAME", "STATUS", "DETAILS"],
                          [[i["id"], i["name"], i["status"], i["subtitle"]] for i in d["items"]]),
                    print(f"({len(d['items'])} of {d['total']})")))


@app.command()
@handle_errors
def annotate(
    node_id: str,
    set_: Annotated[list[str], typer.Option(
        "--set", help="key=value. MD keys (temperature, ensemble, model, timestep, ...), DFT "
        "keys (functional, dispersion, code, basis, ...), sampled_from create structured "
        "records; others become metadata.")] = [],
    note: str | None = None,
    name: str | None = None,
    description: str | None = None,
    json_: JsonOpt = False,
):
    """Add provenance/metadata to an existing node after the fact."""
    from ..services.annotate import add_note, apply_annotations
    from ..services.records import set_attrs
    from .parsing import key_values

    _json(json_)
    adb = open_db()
    with adb.session() as s:
        node = dsvc.get_node(s, node_id)
        meta = key_values(set_)
        created = []
        if node.kind == "dataset":
            created = apply_annotations(s, node.id, meta)
        elif meta:
            set_attrs(s, node, meta)
        if note:
            add_note(s, node.id, note)
        if name:
            node.name = name
        if description:
            node.description = description
        adb.audit(s, "node.annotate", node.id, set=meta, note=note, name=name)
        data = {"id": node.id, "created": created}
    emit(data, lambda d: print(f"annotated {d['id']}"
                               + (f"; recorded {', '.join(d['created'])}" if d["created"] else "")))


@app.command()
@handle_errors
def relate(
    src: str,
    relation: Annotated[str, typer.Argument(
        help="used | generated | derived_from | sampled_from | labeled_by | trained_on | "
        "driven_by | ran_on | converted_from | part_of")],
    dst: str,
    role: str = "",
    json_: JsonOpt = False,
):
    """Add a provenance relation SRC --RELATION--> DST (e.g. DS-1 sampled_from DS-2)."""
    from ..services.annotate import add_link

    _json(json_)
    adb = open_db()
    with adb.session() as s:
        e = add_link(s, src, relation, dst, role=role)
        data = {"src": e.src, "relation": e.relation, "dst": e.dst, "attrs": e.attrs}
    emit(data, lambda d: print(f"{d['src']} --{d['relation']}--> {d['dst']}"
                               + (f"  {d['attrs']}" if d["attrs"] else "")))


@app.command()
@handle_errors
def merge(placeholder: str, real: str, json_: JsonOpt = False):
    """Merge a placeholder node (e.g. a model or MD run known only by name) into the real one."""
    from ..services.annotate import merge as do_merge

    _json(json_)
    adb = open_db()
    with adb.session() as s:
        data = do_merge(s, placeholder, real)
    emit(data, lambda d: print(f"merged {d['placeholder']} into {d['merged_into']} "
                               f"({d['edges_moved']} relations moved)"))


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


_register_subapps()

if __name__ == "__main__":  # pragma: no cover
    app()
