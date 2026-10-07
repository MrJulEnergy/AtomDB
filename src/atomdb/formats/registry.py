"""Format registry and detection. Third-party adapters register via the
``atomdb.formats`` entry point group (a callable returning a list of adapters)."""

from __future__ import annotations

from functools import cache
from importlib.metadata import entry_points
from pathlib import Path

from ..errors import FormatError
from .ase_formats import ase_adapters
from .base import FormatAdapter
from .h5md import H5MDAdapter


@cache
def adapters() -> dict[str, FormatAdapter]:
    found: list[FormatAdapter] = [H5MDAdapter(), *ase_adapters()]
    try:
        from .mdanalysis import mdanalysis_adapters

        found += mdanalysis_adapters()
    except ImportError:
        pass
    for ep in entry_points(group="atomdb.formats"):
        found += list(ep.load()())
    return {a.name: a for a in found}


def get_adapter(name: str) -> FormatAdapter:
    try:
        return adapters()[name]
    except KeyError:
        raise FormatError(
            f"unknown format {name!r}; known: {', '.join(sorted(adapters()))}"
        ) from None


def detect(path: Path) -> FormatAdapter:
    scored = sorted(
        ((a.sniff(path), a.name) for a in adapters().values() if a.readable), reverse=True
    )
    if not scored or scored[0][0] <= 0:
        raise FormatError(
            f"cannot determine the format of {path.name}; pass --format "
            f"(known: {', '.join(sorted(adapters()))})",
            path=str(path),
        )
    return adapters()[scored[0][1]]


def format_for_output(path: Path, explicit: str | None = None) -> FormatAdapter:
    if explicit:
        return get_adapter(explicit)
    name = path.name.lower()
    if name.endswith((".h5", ".h5md", ".hdf5")):
        return get_adapter("h5md")
    for a in adapters().values():
        if a.writable and (
            any(name.endswith(e) for e in a.extensions)
            or any(name.startswith(p) for p in getattr(a, "filename_patterns", ()))
        ):
            return a
    raise FormatError(f"cannot infer output format from {path.name}; pass --to")
