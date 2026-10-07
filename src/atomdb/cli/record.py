"""``atomdb record ...`` — add scientific provenance (MD, DFT, sampling, training)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..services.records import record_dft, record_md, record_sampling, record_training
from .output import emit, handle_errors, state
from .parsing import csv, key_values

app = typer.Typer(help="Record how data was produced (MD, DFT, sampling, training).",
                  no_args_is_help=True)

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")]
IdList = Annotated[list[str], typer.Option(help="Node ID (repeatable).")]


def _db():
    from .main import open_db

    return open_db()


def _done(calc_id: str, json_: bool):
    if json_:
        state.json = True
    emit({"id": calc_id}, lambda d: print(f"recorded {d['id']}"))


def _store_input(adb, s, path: Path | None, calc_id: str, role: str):
    if path is None:
        return None
    from ..services.files import store_file
    from ..services.provenance import add_edge

    f, _ = store_file(adb, s, path, import_id=calc_id)
    add_edge(s, calc_id, f.id, "used", role=role)
    return f


@app.command("md")
@handle_errors
def md(
    output: IdList,
    input: IdList = [],
    model: Annotated[str | None, typer.Option(help="Model ID or name driving the MD.")] = None,
    engine: str | None = None,
    engine_version: str | None = None,
    ensemble: str | None = None,
    temperature: Annotated[float | None, typer.Option(help="K")] = None,
    pressure: Annotated[float | None, typer.Option(help="bar")] = None,
    timestep: Annotated[float | None, typer.Option(help="fs")] = None,
    steps: int | None = None,
    length: Annotated[float | None, typer.Option(help="ps")] = None,
    thermostat: str | None = None,
    barostat: str | None = None,
    integrator: str | None = None,
    seed: int | None = None,
    save_interval: int | None = None,
    enhanced_sampling: Annotated[str | None, typer.Option(
        help="Method: metad, opes, umbrella, abf, steered, remd, tamd, custom")] = None,
    plumed: Annotated[Path | None, typer.Option(help="PLUMED input (parsed + stored).")] = None,
    cv: Annotated[list[str], typer.Option(help="Collective variable (repeatable).")] = [],
    bias: Annotated[list[str], typer.Option(help="Bias parameter key=value.")] = [],
    param: Annotated[list[str], typer.Option(help="Other setting key=value.")] = [],
    input_file: Annotated[list[Path], typer.Option(help="Input file to store.")] = [],
    job: IdList = [],
    workdir: str | None = None,
    name: str | None = None,
    description: str | None = None,
    placeholder: Annotated[bool, typer.Option(
        help="Reconstructed from memory, not from run files.")] = False,
    json_: JsonOpt = False,
):
    """Record an MD simulation that generated OUTPUT dataset(s)."""
    adb = _db()
    with adb.session() as s:
        enhanced = None
        if enhanced_sampling or plumed:
            enhanced = {"method": enhanced_sampling or "custom",
                        "collective_variables": cv or None, "bias": key_values(bias) or None}
            if plumed:
                from ..runparsers.plumed import parse_plumed

                parsed = parse_plumed(plumed.read_text())
                enhanced["method"] = enhanced_sampling or parsed["method"] or "custom"
                enhanced["collective_variables"] = cv or parsed["collective_variables"]
                enhanced["bias"] = {**parsed["bias"], **key_values(bias)}
                enhanced["software"] = "PLUMED"
        c = record_md(
            s, output, inputs=input, model=model, name=name, description=description,
            workdir=workdir, params=key_values(param), jobs=job,
            md={"engine": engine, "engine_version": engine_version, "ensemble": ensemble,
                "temperature_k": temperature, "pressure_bar": pressure, "timestep_fs": timestep,
                "n_steps": steps, "length_ps": length, "thermostat": thermostat,
                "barostat": barostat, "integrator": integrator, "seed": seed,
                "save_interval": save_interval},
            status="placeholder" if placeholder else "active",
        )
        if plumed:
            f = _store_input(adb, s, plumed, c.id, "plumed_input")
            c.enhanced_sampling[0].input_file_id = f.id
        for p in input_file:
            _store_input(adb, s, p, c.id, "input_file")
        cid = c.id
    _done(cid, json_)


@app.command("dft")
@handle_errors
def dft(
    output: IdList,
    input: IdList = [],
    code: str | None = None,
    code_version: str | None = None,
    functional: str | None = None,
    dispersion: str | None = None,
    basis: str | None = None,
    pseudopotential: str | None = None,
    cutoff: Annotated[float | None, typer.Option(help="Ry")] = None,
    rel_cutoff: Annotated[float | None, typer.Option(help="Ry")] = None,
    eps_scf: float | None = None,
    smearing: str | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    kpoints: str | None = None,
    labels: Annotated[str, typer.Option(help="Computed properties.")] = "energy,forces",
    param: Annotated[list[str], typer.Option(help="Other setting key=value.")] = [],
    input_file: Annotated[list[Path], typer.Option(help="Input file to store.")] = [],
    job: IdList = [],
    workdir: str | None = None,
    name: str | None = None,
    description: str | None = None,
    placeholder: bool = False,
    json_: JsonOpt = False,
):
    """Record DFT labeling: INPUT structures → OUTPUT labeled dataset(s)."""
    adb = _db()
    with adb.session() as s:
        c = record_dft(
            s, output, inputs=input, name=name, description=description, workdir=workdir,
            dft={"code": code, "code_version": code_version, "functional": functional,
                 "dispersion": dispersion, "basis": basis, "pseudopotential": pseudopotential,
                 "cutoff_ry": cutoff, "rel_cutoff_ry": rel_cutoff, "eps_scf": eps_scf,
                 "smearing": smearing, "charge": charge, "multiplicity": multiplicity,
                 "kpoints": kpoints},
            params=key_values(param), labels=csv(labels), jobs=job,
            status="placeholder" if placeholder else "active",
        )
        for p in input_file:
            _store_input(adb, s, p, c.id, "input_file")
        cid = c.id
    _done(cid, json_)


@app.command("sampling")
@handle_errors
def sampling(
    parent: Annotated[str, typer.Option(help="Dataset frames were selected from.")],
    child: Annotated[str, typer.Option(help="Dataset of selected frames.")],
    method: Annotated[str, typer.Option(
        help="random, stride, uncertainty, fps, manual, filter, ...")],
    criterion: Annotated[list[str], typer.Option(help="key=value (threshold, stride, ...)")] = [],
    seed: int | None = None,
    frame_map: Annotated[Path | None, typer.Option(
        help="Text file with 'child_idx parent_idx' per line (default: match by structure).")
    ] = None,
    name: str | None = None,
    description: str | None = None,
    json_: JsonOpt = False,
):
    """Record that CHILD's frames were selected from PARENT (frames linked individually)."""
    fmap = None
    if frame_map:
        fmap = [tuple(int(x) for x in line.split()[:2])
                for line in frame_map.read_text().splitlines() if line.strip()
                and not line.startswith("#")]
    adb = _db()
    with adb.session() as s:
        c = record_sampling(s, parent, child, method, criterion=key_values(criterion), seed=seed,
                            name=name, description=description, frame_map=fmap)
        cid = c.id
        stats = c.sampling.criterion.get("frame_mapping")
    if json_:
        state.json = True
    emit({"id": cid, "frame_mapping": stats},
         lambda d: print(f"recorded {d['id']}  (frames linked: {d['frame_mapping']})"))


@app.command("training")
@handle_errors
def training(
    model: Annotated[str, typer.Option(help="Model ID or name that was trained.")],
    train: IdList,
    validation: IdList = [],
    hyperparam: Annotated[list[str], typer.Option(help="key=value")] = [],
    config: Annotated[Path | None, typer.Option(help="Training config file (stored).")] = None,
    metric: Annotated[list[str], typer.Option(help="key=value")] = [],
    job: IdList = [],
    workdir: str | None = None,
    name: str | None = None,
    json_: JsonOpt = False,
):
    """Record that MODEL was trained on TRAIN dataset(s)."""
    adb = _db()
    with adb.session() as s:
        hp = key_values(hyperparam)
        if config is not None:
            import yaml

            try:
                hp = {**(yaml.safe_load(config.read_text()) or {}), **hp}
            except yaml.YAMLError:
                pass
        c = record_training(s, model, train, validation=validation, hyperparameters=hp or None,
                            metrics=key_values(metric) or None, name=name, workdir=workdir,
                            jobs=job)
        if config is not None:
            f = _store_input(adb, s, config, c.id, "config")
            c.training.config_file_id = f.id
        cid = c.id
    _done(cid, json_)
