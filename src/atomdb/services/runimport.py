"""Import a calculation directory (copied back from an HPC cluster) with full provenance.

``plan_run`` inspects a directory read-only and proposes records; ``execute_run`` creates
them in one transaction:

    JOB-… (one per slurm-*.out)  ←ran_on─  DFT/MD calculation  ─used→  inputs (cp2k.inp, ...)
                                               │        └generated→ logs
                                               ├generated→ DS(labeled / trajectory)
                                               └used→ DS(input structures)

Many sbatch jobs writing one shared output file (laufband) are modelled as one
calculation that ran on many jobs; which job produced which frame is not invented.

An IPSuite project root (``zntrack.json`` + ``dvc.yaml`` + ``nodes/``) is split by
``plan_project``: one calculation per ``ApplyCalculator`` node, with the data file
loaded by its upstream ``AddData*`` stage as input, the project's submit script and the
per-job ``model/<jobid>/`` directories as jobs.
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from pathlib import Path

import yaml
from sqlalchemy import select

from ..db.models import Calculation, Dataset, DFTParams, HPCJob
from ..errors import AtomDBError, InvalidInput
from ..formats.registry import detect
from ..frames import get_energy, get_forces
from ..ids import new_id
from ..runparsers import cp2k, slurm
from ..runparsers.plumed import parse_plumed
from .context import AtomDB
from .files import store_file
from .importer import import_file
from .provenance import add_edge
from .records import add_enhanced_sampling, add_params, record_dft, record_md, record_sampling

DEFAULT_EXCLUDE = ("*.wfn", "*.wfn.bak-*", "*.bak-*", "core", "core.*", "*.tmp")
SKIP_TRAJ = ("*-frc-*.xyz", "*-vel-*.xyz")  # CP2K force/velocity files are not structures
HEAD = 8192


def _head(p: Path) -> str:
    try:
        with open(p, "rb") as fh:
            return fh.read(HEAD).decode(errors="replace")
    except OSError:
        return ""


def _labeled(p: Path) -> bool:
    try:
        src = detect(p).open(p)
        a = src.get(0) if src.random_access else next(iter(src))
        return get_energy(a) is not None or get_forces(a) is not None
    except (AtomDBError, StopIteration, IndexError, OSError):
        return False


def plan_run(
    run_dir: str | Path,
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE,
    max_depth: int = 2,
    cluster: str | None = None,
) -> dict:
    root = Path(run_dir).expanduser().resolve()
    if not root.is_dir():
        raise InvalidInput(f"not a directory: {root}")
    files = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_symlink() or not p.is_file() or len(rel.parts) > max_depth + 1:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        files.append(p)

    plan: dict = {
        "run_dir": str(root), "cluster": cluster, "calc": {"type": "other"}, "jobs": [],
        "submit_script": None, "inputs": [], "outputs": [], "logs": [], "aux": [],
        "excluded": [], "warnings": [], "laufband": False,
    }
    inp_summary = None
    outputs_parsed = []
    script = None
    atomistic: list[Path] = []
    plumed_files: list[Path] = []
    for p in files:
        name = p.name
        if any(fnmatch.fnmatch(name, pat) for pat in exclude):
            plan["excluded"].append(str(p))
            continue
        head = _head(p)
        if "laufband" in name.lower():
            plan["laufband"] = True
            plan["aux"].append({"path": str(p), "role": "laufband_state"})
            continue
        job = slurm.parse_job_output(name, head)
        if job:
            plan["jobs"].append(job)
            plan["logs"].append({"path": str(p), "role": "slurm_output"})
            continue
        if name.endswith(".inp") and ("&FORCE_EVAL" in head.upper() or "&GLOBAL" in head.upper()):
            flat = cp2k.parse_input(p.read_text(errors="replace"), p.parent)
            if inp_summary is None:
                inp_summary = cp2k.summarize_input(flat)
                inp_summary["flat"] = flat
                inp_summary["file"] = str(p)
            plan["inputs"].append({"path": str(p), "role": "cp2k_input", "as": "file"})
            continue
        if cp2k.is_output(head):
            outputs_parsed.append(cp2k.parse_output(p.read_text(errors="replace")))
            plan["logs"].append({"path": str(p), "role": "cp2k_output"})
            continue
        if slurm.is_submit_script(p, head) and script is None:
            script = p
            continue
        if "plumed" in name.lower() and p.suffix in (".dat", ".inp", ".plumed", ""):
            plumed_files.append(p)
            plan["inputs"].append({"path": str(p), "role": "plumed_input", "as": "file"})
            continue
        if any(fnmatch.fnmatch(name, pat) for pat in SKIP_TRAJ):
            plan["aux"].append({"path": str(p), "role": "auxiliary"})
            plan["warnings"].append(f"{name}: CP2K force/velocity file stored as raw file")
            continue
        try:
            fmt = detect(p)
            if fmt.sniff(p) >= 0.5:
                atomistic.append(p)
                continue
        except AtomDBError:
            pass
        plan["aux"].append({"path": str(p), "role": "auxiliary"})

    if script is not None:
        s = slurm.parse_script(script.read_text(errors="replace"))
        plan["submit_script"] = {"path": str(script), **s}
        plan["laufband"] |= s["uses_laufband"]
        if not plan["cluster"]:
            plan["cluster"] = s.get("cluster")

    # calculation type and settings
    calc: dict = {"type": "other", "params": {}}
    if inp_summary:
        calc["type"] = "md" if inp_summary["md"] else "dft"
        calc["dft"] = inp_summary["dft"]
        calc["md"] = inp_summary["md"]
        calc["labels"] = inp_summary["labels"]
        calc["params"] = inp_summary["flat"]
        calc["name"] = " ".join(x for x in (
            inp_summary.get("project"), "CP2K", inp_summary["dft"].get("functional"),
            inp_summary["dft"].get("dispersion") if inp_summary["dft"].get("dispersion") != "none"
            else None) if x)
    if outputs_parsed:
        starts = [o["started_at"] for o in outputs_parsed if o["started_at"]]
        ends = [o["ended_at"] for o in outputs_parsed if o["ended_at"]]
        calc["started_at"] = min(starts) if starts else None
        calc["ended_at"] = max(ends) if ends else None
        first = outputs_parsed[0]
        if calc.get("dft") is not None and first.get("code_version"):
            calc["dft"]["code_version"] = first["code_version"]
        calc["qc"] = {
            "n_force_evals": sum(o["n_force_evals"] for o in outputs_parsed),
            "scf_converged": sum(o["scf_converged"] for o in outputs_parsed),
            "scf_not_converged": sum(o["scf_not_converged"] for o in outputs_parsed),
            "outputs_completed": sum(1 for o in outputs_parsed if o["completed"]),
            "outputs": len(outputs_parsed),
            "hosts": sorted({h for o in outputs_parsed for h in o.get("hosts", [])}),
            "cp2k_git_revision": first.get("git_revision"),
            "original_workdir": first.get("workdir"),
        }
        if calc["qc"]["scf_not_converged"]:
            plan["warnings"].append(
                f"{calc['qc']['scf_not_converged']} SCF runs did NOT converge — check labels")
    if plumed_files:
        parsed = parse_plumed(plumed_files[0].read_text(errors="replace"))
        calc["enhanced_sampling"] = {**parsed, "file": str(plumed_files[0])}
    calc["software"] = {
        "modules": (plan["submit_script"] or {}).get("modules", []),
        **({"cp2k": outputs_parsed[0].get("code_version")} if outputs_parsed else {}),
    }
    plan["calc"] = calc

    # structures: inputs vs outputs
    coord = (inp_summary or {}).get("coord_file")
    for p in atomistic:
        if coord and p.name == Path(coord).name:
            plan["inputs"].append({"path": str(p), "role": "initial_structure", "as": "dataset"})
        elif calc["type"] == "dft" and not _labeled(p):
            plan["inputs"].append({"path": str(p), "role": "structures", "as": "dataset"})
        else:
            plan["outputs"].append({"path": str(p), "role": "result", "as": "dataset"})
    if plan["laufband"] and len(plan["jobs"]) > 1:
        plan["warnings"].append(
            f"{len(plan['jobs'])} jobs wrote shared outputs (laufband): recorded as one "
            "calculation that ran on all jobs; frame↔job attribution is unknown")
    failed = [j for j in plan["jobs"] if j["state"]]
    for j in failed:
        plan["warnings"].append(f"job {j['job_id']}: {j['state']}")
    if calc["type"] == "other":
        plan["warnings"].append("no recognised calculation input (CP2K); calculation type "
                                "'other' — pass --type and settings, or use `record`")
    return plan


# ----------------------------------------------------------------- IPSuite projects

LOCK_PACKAGES = ("ipsuite", "zntrack", "laufband", "znh5md", "ase", "cp2k-input-tools")


def is_ipsuite_project(run_dir: str | Path) -> bool:
    root = Path(run_dir).expanduser()
    return all((root / n).exists() for n in ("zntrack.json", "dvc.yaml", "nodes"))


def plan_project(
    run_dir: str | Path,
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE,
    cluster: str | None = None,
) -> list[dict]:
    """One run plan per IPSuite ``ApplyCalculator`` node of the project at ``run_dir``."""
    root = Path(run_dir).expanduser().resolve()
    stages = (yaml.safe_load((root / "dvc.yaml").read_text()) or {}).get("stages", {})
    params = {}
    if (root / "params.yaml").is_file():
        params = yaml.safe_load((root / "params.yaml").read_text()) or {}
    produced = {}  # path → stage that writes it
    for name, st in stages.items():
        for key in ("outs", "metrics", "plots"):
            for o in st.get(key, []) or []:
                produced[o if isinstance(o, str) else next(iter(o))] = name

    scripts = []
    for p in sorted(root.glob("*.sh")):
        head = _head(p)
        if slurm.is_submit_script(p, head):
            scripts.append((p, slurm.parse_script(p.read_text(errors="replace"))))
    software = _lock_versions(root / "uv.lock")
    project_files = [p for p in (root / n for n in (
        "main.py", "dvc.yaml", "params.yaml", "zntrack.json", "pyproject.toml", "uv.lock",
    )) if p.is_file()]
    laufband_files = [p for p in root.iterdir() if p.is_file()
                      and ("laufband" in p.name.lower() or "graphband" in p.name.lower())]

    plans = []
    for stage, st in stages.items():
        outs = [o if isinstance(o, str) else next(iter(o)) for o in st.get("outs", []) or []]
        model_out = next((o for o in outs if o.endswith("/model")), None)
        frames_out = next((o for o in outs if o.endswith("/frames.h5")), None)
        if "ApplyCalculator" not in st.get("cmd", "") or not model_out or not frames_out:
            continue
        node_dir = root / Path(model_out).parent
        if not node_dir.is_dir():
            continue
        plan = plan_run(node_dir, exclude=exclude, max_depth=2, cluster=cluster)
        plan["project"] = {"root": str(root), "stage": stage, "cmd": st.get("cmd"),
                           "params": params.get(stage)}

        # submit script: the one that runs this stage (else the only one)
        sc = next(((p, s) for p, s in scripts if any(stage in c for c in s["commands"])), None)
        if sc is None and len(scripts) == 1:
            sc = scripts[0]
        if sc is not None:
            plan["submit_script"] = {"path": str(sc[0]), **sc[1]}
            plan["laufband"] |= sc[1]["uses_laufband"]
            plan["cluster"] = plan["cluster"] or sc[1].get("cluster")

        # jobs: one model/<jobid>/ directory per Slurm job (LAUFBAND_IDENTIFIER)
        out_pattern = (sc[1]["options"].get("output") if sc else None) or "slurm-%j.out"
        err_pattern = sc[1]["options"].get("error") if sc else None
        jobs = []
        for jd in sorted((node_dir / "model").iterdir() if (node_dir / "model").is_dir()
                         else []):
            if not (jd.is_dir() and jd.name.isdigit()):
                continue
            job = {"job_id": jd.name, "array_task_id": None, "state": None, "file": None}
            for role, pat in (("slurm_output", out_pattern), ("slurm_error", err_pattern)):
                log = root / pat.replace("%j", jd.name) if pat else None
                if log is None or not log.is_file():
                    continue
                state = slurm.job_state(_tail(log))
                job["state"] = job["state"] or state
                job["file"] = job["file"] or log.name
                plan["logs"].append({"path": str(log), "role": role})
            jobs.append(job)
        plan["jobs"] = jobs
        plan["warnings"] = [w for w in plan["warnings"] if not w.startswith("job ")]
        by_state: dict[str, list[str]] = {}
        for j in jobs:
            if j["state"]:
                by_state.setdefault(j["state"], []).append(j["job_id"])
        for state, ids in by_state.items():
            if len(ids) == 1:
                plan["warnings"].append(f"job {ids[0]}: {state}")
            else:  # laufband workers usually run into the time limit: one line, not one per job
                plan["warnings"].append(f"{len(ids)} of {len(jobs)} jobs ended with {state}"
                                        + (" (expected for laufband workers)"
                                           if state == "TIMEOUT" and plan["laufband"] else ""))
        if plan["laufband"] and len(jobs) > 1 and not any("attribution" in w for w in
                                                          plan["warnings"]):
            plan["warnings"].append(
                f"{len(jobs)} jobs wrote shared outputs (laufband): recorded as one "
                "calculation that ran on all jobs; frame↔job attribution is unknown")

        # input structures: data file(s) loaded by the upstream AddData* stage
        inputs_found = False
        for dep in st.get("deps", []) or []:
            up = produced.get(dep)
            if up is None:
                if (root / dep).is_file():
                    plan["inputs"].append({"path": str(root / dep), "role": "cp2k_data",
                                           "as": "file"})
                continue
            if "AddData" not in stages[up].get("cmd", ""):
                plan["warnings"].append(
                    f"input structures come from stage {up} (not a data file): pass --input")
                continue
            for d in stages[up].get("deps", []) or []:
                if (root / d).is_file() and d not in produced:
                    plan["inputs"].append({"path": str(root / d), "role": "structures",
                                           "as": "dataset"})
                    inputs_found = True
        if not inputs_found:
            plan["warnings"].append("no input structure file found for this node")
        for prm in st.get("params", []) or []:
            key = prm if isinstance(prm, str) else next(iter(prm))
            if (root / key).is_file():
                plan["inputs"].append({"path": str(root / key), "role": "cp2k_config",
                                       "as": "file"})
        plan["aux"] += [{"path": str(p), "role": "project"} for p in project_files]
        plan["aux"] += [{"path": str(p), "role": "laufband_state"} for p in laufband_files]

        calc = plan["calc"]
        if calc.get("dft"):
            calc["name"] = " ".join(x for x in (
                stage.removesuffix("_ApplyCalculator"), "CP2K", calc["dft"].get("functional"),
                calc["dft"].get("dispersion") if calc["dft"].get("dispersion") != "none"
                else None) if x)
        calc["software"] = {**calc["software"], **software}
        for o in plan["outputs"]:
            o["name_from_input"] = calc.get("name")
        plans.append(plan)
    if not plans:
        raise InvalidInput(f"no ApplyCalculator node with outputs in {root}")
    return plans


def _tail(p: Path, n: int = 16384) -> str:
    with open(p, "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - n))
        return fh.read().decode(errors="replace")


def _lock_versions(lock: Path) -> dict:
    if not lock.is_file():
        return {}
    try:
        pkgs = tomllib.loads(lock.read_text()).get("package", [])
    except tomllib.TOMLDecodeError:
        return {}
    out = {}
    for pkg in pkgs:
        if pkg.get("name") in LOCK_PACKAGES:
            src = pkg.get("source", {})
            rev = re.search(r"#([0-9a-f]{7,40})$", src.get("git", ""))
            out[pkg["name"]] = pkg.get("version", "") + (f"+git{rev.group(1)[:10]}" if rev else "")
    return out


def execute_run(
    adb: AtomDB,
    plan: dict,
    input_datasets: list[str] | None = None,
    sampled_from: str | None = None,
    model: str | None = None,
    name: str | None = None,
    calc_type: str | None = None,
    extra: dict | None = None,
) -> dict:
    calc_spec = plan["calc"]
    ctype = calc_type or calc_spec["type"]
    created: dict = {"jobs": [], "datasets": {"inputs": [], "outputs": []}, "files": [],
                     "calculation": None, "warnings": list(plan["warnings"])}
    with adb.session() as s:
        script_file = None
        if plan["submit_script"]:
            script_file, _ = store_file(adb, s, plan["submit_script"]["path"])
            created["files"].append(script_file.id)
        job_ids = []
        sc = plan["submit_script"] or {}
        for j in plan["jobs"]:
            job = _get_or_create_job(s, j, sc, plan, script_file)
            job_ids.append(job.id)
        created["jobs"] = job_ids

        in_ds = list(input_datasets or [])
        for item in plan["inputs"]:
            if item["as"] == "dataset":
                r = import_file(adb, item["path"], session=s,
                                description=f"{item['role']} of run {plan['run_dir']}")
                in_ds.append(r.dataset.id)
                created["datasets"]["inputs"].append(r.dataset.id)
        out_ds = []
        parents = [s.get(Dataset, i) for i in in_ds]
        parents = [p for p in parents if p is not None]
        for item in plan["outputs"]:
            ds_name = description = None
            if item.get("name_from_input") and len(parents) == 1:
                # generic IPSuite output name (frames.h5): name it after what was labelled
                ds_name = f"{parents[0].name} | {item['name_from_input']}"
                description = (f"{item['name_from_input']} relabelling of {parents[0].id} "
                               f"({parents[0].name}); output of run {plan['run_dir']}")
            r = import_file(adb, item["path"], session=s, name=ds_name,
                            description=description or f"output of run {plan['run_dir']}",
                            tags=sorted({t.name for p in parents for t in p.tags}) or None)
            if r.duplicate:
                created["warnings"].append(f"{Path(item['path']).name} already imported as "
                                           f"{r.dataset.id}")
            for p in parents:
                if ctype == "dft" and r.dataset.n_frames < (p.n_frames or 0):
                    created["warnings"].append(
                        f"{r.dataset.id}: only {r.dataset.n_frames} of {p.n_frames} frames "
                        f"of {p.id} labelled (run incomplete?)")
            out_ds.append(r.dataset.id)
            created["datasets"]["outputs"].append(r.dataset.id)

        existing = _already_imported(s, plan, out_ds)
        if existing:
            created["calculation"] = existing
            created["warnings"].append(f"this run was already imported as {existing}")
            return created

        workdir = plan["run_dir"]
        if ctype == "dft":
            c = record_dft(s, out_ds, inputs=in_ds, name=name or calc_spec.get("name"),
                           workdir=workdir, dft=dict(calc_spec.get("dft") or {}),
                           labels=calc_spec.get("labels"), jobs=job_ids, source="parsed")
        elif ctype == "md":
            c = record_md(s, out_ds, inputs=in_ds, model=model, name=name, workdir=workdir,
                          md=dict(calc_spec.get("md") or {}), jobs=job_ids, source="parsed")
            if calc_spec.get("dft"):  # ab initio MD: also keep the electronic structure
                c.dft = DFTParams(labels=calc_spec.get("labels"), **calc_spec["dft"])
                for o in out_ds:
                    add_edge(s, o, c.id, "labeled_by", source="parsed")
        else:
            c = Calculation(id=new_id(s, "calculation"), name=name or Path(workdir).name,
                            calc_type="other", workdir=workdir)
            s.add(c)
            s.flush()
            for i in in_ds:
                add_edge(s, c.id, i, "used", source="parsed")
            for o in out_ds:
                add_edge(s, c.id, o, "generated", source="parsed")
            for jid in job_ids:
                add_edge(s, c.id, jid, "ran_on", source="parsed")
        c.started_at = calc_spec.get("started_at")
        c.ended_at = calc_spec.get("ended_at")
        c.parameters = {
            "run_dir": workdir, "qc": calc_spec.get("qc"), "run_software": calc_spec.get("software"),
            "laufband": plan["laufband"], "slurm": {k: v for k, v in sc.items()
                                                   if k not in ("path",)} or None,
            **({"ipsuite": plan["project"]} if plan.get("project") else {}),
            **(extra or {}),
        }
        if calc_spec.get("params"):
            add_params(c, calc_spec["params"], source="parsed")
        if calc_spec.get("enhanced_sampling"):
            es = calc_spec["enhanced_sampling"]
            f, _ = store_file(adb, s, es["file"], import_id=c.id)
            add_enhanced_sampling(s, c, method=es["method"] or "custom", software="PLUMED",
                                  collective_variables=es["collective_variables"],
                                  bias=es["bias"], input_file_id=f.id)
        for item in plan["inputs"]:
            if item["as"] == "file":
                f, _ = store_file(adb, s, item["path"], import_id=c.id)
                add_edge(s, c.id, f.id, "used", role=item["role"], source="parsed")
                created["files"].append(f.id)
        for item in plan["logs"]:
            f, _ = store_file(adb, s, item["path"], import_id=c.id)
            add_edge(s, c.id, f.id, "generated", role=item["role"], source="parsed")
            created["files"].append(f.id)
        for item in plan["aux"]:
            f, _ = store_file(adb, s, item["path"], import_id=c.id)
            add_edge(s, f.id, c.id, "part_of", role=item["role"], source="parsed")
            created["files"].append(f.id)
        if sampled_from:
            target = in_ds[0] if in_ds else (out_ds[0] if out_ds else None)
            if target:
                record_sampling(s, sampled_from, target, "unspecified", source="user")
        adb.audit(s, "run.import", c.id, run_dir=workdir, jobs=job_ids)
        created["calculation"] = c.id
    return created


def _get_or_create_job(s, j: dict, sc: dict, plan: dict, script_file) -> HPCJob:
    if j["array_task_id"]:
        q = select(HPCJob).where(HPCJob.array_job_id == j["job_id"],
                                 HPCJob.array_task_id == j["array_task_id"])
    else:
        q = select(HPCJob).where(HPCJob.job_id == j["job_id"])
    if plan["cluster"]:
        q = q.where(HPCJob.cluster == plan["cluster"])
    existing = s.execute(q).scalars().first()
    if existing is not None:
        return existing
    job = HPCJob(
        id=new_id(s, "hpc_job"),
        name=f"{sc.get('job_name') or 'job'} {j['job_id']}"
             + (f"_{j['array_task_id']}" if j["array_task_id"] else ""),
        cluster=plan["cluster"], partition=sc.get("partition"),
        job_id=j["job_id"] if not j["array_task_id"] else f"{j['job_id']}_{j['array_task_id']}",
        array_job_id=j["job_id"] if j["array_task_id"] else None,
        array_task_id=j["array_task_id"], workdir=plan["run_dir"],
        submit_script_file_id=script_file.id if script_file else None,
        n_nodes=sc.get("n_nodes"), n_tasks=sc.get("n_tasks"), n_cpus=sc.get("n_cpus"),
        n_gpus=sc.get("n_gpus"), gpu_type=sc.get("gpu_type"), mem=sc.get("mem"),
        time_limit=sc.get("time_limit"), state=j["state"],
        sbatch_options=sc.get("options"),
    )
    s.add(job)
    s.flush()
    if script_file is not None:
        add_edge(s, job.id, script_file.id, "used", role="submit_script", source="parsed")
    return job


def _already_imported(s, plan: dict, out_ds: list[str]) -> str | None:
    q = select(Calculation).where(Calculation.workdir == plan["run_dir"])
    for c in s.execute(q).scalars():
        if c.calc_type in ("dft", "md", "other") and c.status != "merged":
            return c.id
    return None
