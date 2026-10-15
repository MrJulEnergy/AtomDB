"""``atomdb tags ...`` — the tag registry (kinds, aliases) and redundant-tag cleanup."""

from __future__ import annotations

from typing import Annotated

import typer

from ..services import tags as tsvc
from .output import emit, handle_errors, table

app = typer.Typer(help="Tag kinds (system/project/quality/free), aliases, cleanup.",
                  invoke_without_command=True)
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")]


def _db():
    from .main import _json, open_db

    return _json, open_db()


@app.callback()
@handle_errors
def list_(ctx: typer.Context, json_: JsonOpt = False):
    """List tags with kind, usage and aliases."""
    if ctx.invoked_subcommand is not None:
        return
    set_json, adb = _db()
    set_json(json_)
    with adb.session() as s:
        rows = tsvc.list_tags(s)
    emit(rows, lambda rows: table(
        ["TAG", "KIND", "USED", "ALIASES", "DESCRIPTION"],
        [[r["name"], r["kind"], r["count"], ", ".join(r["aliases"]), r["description"] or ""]
         for r in sorted(rows, key=lambda r: (tsvc.KINDS.index(r["kind"]), -r["count"]))]))


@app.command("set")
@handle_errors
def set_(
    name: str,
    kind: Annotated[str | None, typer.Option(help="system | project | quality | free")] = None,
    description: str | None = None,
    rename: Annotated[str | None, typer.Option(help="New name.")] = None,
    json_: JsonOpt = False,
):
    """Change a tag's kind (chip colour / sidebar group), description or name."""
    set_json, adb = _db()
    set_json(json_)
    with adb.session() as s:
        r = tsvc.set_tag(s, name, kind=kind, description=description, rename=rename)
    emit(r, lambda r: print(f"{r['name']}: {r['kind']}"
                            + (f" — {r['description']}" if r["description"] else "")))


@app.command()
@handle_errors
def merge(alias: str, into: str, json_: JsonOpt = False):
    """Move every use of ALIAS to INTO; ALIAS stays as an alias (typing it tags INTO)."""
    set_json, adb = _db()
    set_json(json_)
    with adb.session() as s:
        r = tsvc.merge(s, alias, into)
    emit(r, lambda r: print(f"{r['alias']} → {r['into']} ({r['moved']} nodes moved)"))


@app.command()
@handle_errors
def delete(name: str, json_: JsonOpt = False):
    """Delete an unused tag."""
    set_json, adb = _db()
    set_json(json_)
    with adb.session() as s:
        r = tsvc.delete_tag(s, name)
    emit(r, lambda r: print(f"deleted {r['deleted']}"))


@app.command()
@handle_errors
def cleanup(
    apply: Annotated[bool, typer.Option("--apply", help="Remove the redundant tags.")] = False,
    json_: JsonOpt = False,
):
    """Find tags that only repeat facts from the frames/provenance (dry run by default).

    redundant   — confirmed by recorded data; removed with --apply
    only record — the tag is the only place this is stated; kept (record it properly)
    contradicts — the data says otherwise; kept and flagged
    """
    set_json, adb = _db()
    set_json(json_)
    with adb.session() as s:
        r = tsvc.cleanup(s, apply=apply)
        if not apply:
            s.rollback()

    def human(r):
        for key, title in (("redundant", "REDUNDANT (removed with --apply)" if not apply
                            else "REMOVED"),
                           ("only_record", "ONLY RECORD (kept: record this properly)"),
                           ("contradicts", "CONTRADICTS THE DATA (kept)")):
            print(f"{title}: {len(r[key])}")
            by_tag: dict[str, list[str]] = {}
            for x in r[key]:
                by_tag.setdefault(f"{x['tag']:16} {x['reason']}", []).append(x["dataset"][-3:])
            for k, ids in sorted(by_tag.items()):
                print(f"  {k}  [{', '.join(ids)}]")
        if r.get("deleted_tags"):
            print("deleted unused tags: " + ", ".join(r["deleted_tags"]))

    emit(r, human)
