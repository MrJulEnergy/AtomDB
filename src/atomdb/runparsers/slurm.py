"""Slurm submission scripts and ``slurm-*.out`` files.

Only what is visible in the run directory is used (no ``sacct``: imports happen locally,
after the run directory was copied back from the cluster).
"""

from __future__ import annotations

import re
from pathlib import Path

SHORT = {"-p": "partition", "-N": "nodes", "-n": "ntasks", "-c": "cpus-per-task",
         "-t": "time", "-J": "job-name", "-A": "account", "-a": "array", "-o": "output",
         "-e": "error", "-G": "gpus", "-w": "nodelist", "-q": "qos", "-M": "clusters"}
OUT_RE = re.compile(r"^slurm-(\d+)(?:_(\d+))?\.out$")
FAILURE_MARKERS = {
    "DUE TO TIME LIMIT": "TIMEOUT", "CANCELLED": "CANCELLED", "oom-kill": "OUT_OF_MEMORY",
    "Exceeded job memory limit": "OUT_OF_MEMORY", "NODE_FAIL": "NODE_FAIL",
    "Segmentation fault": "FAILED", "Traceback (most recent call last)": "FAILED",
}


def is_submit_script(path: Path, head: str) -> bool:
    return "#SBATCH" in head


def parse_script(text: str) -> dict:
    opts: dict[str, str] = {}
    modules: list[str] = []
    commands: list[str] = []
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#SBATCH"):
            body = line[len("#SBATCH"):].split("#", 1)[0].strip()
            if not body:
                continue
            if body.startswith("--"):
                k, _, v = body[2:].partition("=")
                if not v and " " in k:
                    k, v = k.split(None, 1)
                opts[k.strip()] = v.strip() or "true"
            elif body[:2] in SHORT:
                opts[SHORT[body[:2]]] = body[2:].strip()
            continue
        if not line or line.startswith("#"):
            continue
        m = re.match(r"(?:module|ml)\s+(?:load|add)\s+(.+)", line)
        if m:
            modules += m.group(1).split()
            continue
        m = re.match(r"export\s+([A-Z_][A-Z0-9_]*)=(.*)", line)
        if m:
            env[m.group(1)] = m.group(2).strip("'\"")
            continue
        commands.append(line)
    gres = opts.get("gres", "")
    n_gpus = None
    gpu_type = None
    m = re.search(r"gpu(?::([A-Za-z0-9_\-]+))?:(\d+)", gres)
    if m:
        gpu_type, n_gpus = m.group(1), int(m.group(2))
    elif opts.get("gpus"):
        g = opts["gpus"].split(":")
        n_gpus = int(g[-1]) if g[-1].isdigit() else None
        gpu_type = g[0] if len(g) > 1 else None
    def _int(k):
        v = opts.get(k)
        return int(v) if v and v.isdigit() else None
    cpus = _int("cpus-per-task")
    ntasks = _int("ntasks") or (_int("ntasks-per-node") or 0) * (_int("nodes") or 1) or None
    return {
        "options": opts,
        "job_name": opts.get("job-name"),
        "partition": opts.get("partition"),
        "account": opts.get("account"),
        "cluster": opts.get("clusters") or opts.get("cluster"),
        "n_nodes": _int("nodes"),
        "n_tasks": ntasks,
        "n_cpus": (cpus or 1) * (ntasks or 1) if (cpus or ntasks) else None,
        "n_gpus": n_gpus,
        "gpu_type": gpu_type,
        "mem": opts.get("mem") or opts.get("mem-per-cpu"),
        "time_limit": opts.get("time"),
        "array": opts.get("array"),
        "modules": modules,
        "commands": commands,
        "env": env,
        "uses_laufband": any("laufband" in c.lower() for c in commands) or any(
            "laufband" in v.lower() for v in env.values()),
    }


def parse_job_output(name: str, text: str) -> dict | None:
    m = OUT_RE.match(name)
    if not m:
        return None
    state = None  # unknown: Slurm writes no success marker
    for marker, st in FAILURE_MARKERS.items():
        if marker in text:
            state = st
            break
    return {"job_id": m.group(1), "array_task_id": m.group(2), "state": state,
            "file": name}
