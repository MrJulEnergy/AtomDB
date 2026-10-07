"""``atomdb model ...`` — register MLIP models / checkpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select

from ..db.models import Model
from ..services.nodes import describe
from ..services.records import ModelSpec, add_model
from .output import emit, handle_errors, state, table
from .parsing import key_values

app = typer.Typer(help="Register and list interatomic potentials.", no_args_is_help=True)
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")]


@app.command("add")
@handle_errors
def add(
    checkpoint: Annotated[Path | None, typer.Argument(
        help="Checkpoint file or directory (stored). Omit for external/foundation models.")]
    = None,
    name: Annotated[str, typer.Option(help="Model name, e.g. MACE-MH-1.")] = ...,
    family: Annotated[str | None, typer.Option(help="MACE, NequIP, Allegro, GAP, apax, ...")]
    = None,
    version: str | None = None,
    architecture: str | None = None,
    cutoff: Annotated[float | None, typer.Option(help="Å")] = None,
    n_parameters: int | None = None,
    package: str | None = None,
    package_version: str | None = None,
    git_commit: str | None = None,
    trained_at: Annotated[str | None, typer.Option(help="ISO date.")] = None,
    url: str | None = None,
    description: str | None = None,
    trained_on: Annotated[list[str], typer.Option(help="Training dataset ID (repeatable).")] = [],
    hyperparam: Annotated[list[str], typer.Option(help="key=value")] = [],
    json_: JsonOpt = False,
):
    """Register a model. Re-adding a name that exists as placeholder fills it in."""
    from .main import open_db

    if json_:
        state.json = True
    adb = open_db()
    spec = ModelSpec(
        name=name, family=family, version=version, architecture=architecture, cutoff=cutoff,
        n_parameters=n_parameters, package=package, package_version=package_version,
        git_commit=git_commit, url=url, description=description,
        trained_at=datetime.fromisoformat(trained_at).replace(tzinfo=UTC) if trained_at else None,
        hyperparameters=key_values(hyperparam) or None, trained_on=trained_on,
    )
    with adb.session() as s:
        m = add_model(adb, s, spec, checkpoint)
        d = describe(adb, s, m)
    emit(d, lambda d: print(f"model {d['id']}  {d['name']}  {d['subtitle']}"))


@app.command("ls")
@handle_errors
def ls(json_: JsonOpt = False):
    """List models."""
    from .main import open_db

    if json_:
        state.json = True
    adb = open_db()
    with adb.session() as s:
        rows = [describe(adb, s, m, full=False)
                for m in s.execute(select(Model).order_by(Model.created_at)).scalars()]
    emit(rows, lambda rs: table(["ID", "NAME", "STATUS", "DETAILS"],
                                [[r["id"], r["name"], r["status"], r["subtitle"]] for r in rs]))
