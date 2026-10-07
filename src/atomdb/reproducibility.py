"""Snapshot of the software environment, stored with every transformation."""

from __future__ import annotations

import platform
import subprocess
import sys
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from . import __version__

TRACKED = ("ase", "h5py", "numpy", "znh5md", "MDAnalysis", "dscribe")


@cache
def atomdb_git_commit() -> str | None:
    repo = Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return None


@cache
def software_versions() -> dict[str, str | None]:
    v: dict[str, str | None] = {
        "atomdb": __version__,
        "atomdb_git": atomdb_git_commit(),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    for pkg in TRACKED:
        try:
            v[pkg] = version(pkg)
        except PackageNotFoundError:
            pass
    return v


def command_line() -> str:
    return " ".join(sys.argv)
