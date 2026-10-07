"""H5MD — the preferred internal format.

Uses the znh5md dialect (the one written by IPSuite/ZnTrack workflows): per-frame
``info`` → ``observables/<group>/<key>``, per-atom arrays → ``particles/<group>/<key>``,
calculator results tagged via the ``ASE_ENTRY_ORIGIN`` attribute, NaN padding for
variable atom counts. atomdb adds an ``/atomdb`` group with provenance stamps to files it
writes itself (never to files it imports).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import ase
import h5py
import numpy as np
import znh5md

from ..errors import FormatError
from .base import Capabilities, FormatAdapter, FrameSource

STAMP_GROUP = "atomdb"


def is_hdf5(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(8) == b"\x89HDF\r\n\x1a\n"
    except OSError:
        return False


def is_h5md(path: Path) -> bool:
    if not is_hdf5(path):
        return False
    try:
        with h5py.File(path, "r") as f:
            return "h5md" in f and "particles" in f
    except OSError:
        return False


def particles_group(f: h5py.File) -> str:
    groups = list(f["particles"].keys())
    if not groups:
        raise FormatError("H5MD file has an empty /particles group")
    return "atoms" if "atoms" in groups else groups[0]


class H5MDFrameSource(FrameSource):
    random_access = True

    def __init__(self, path: Path):
        self.path = path
        try:
            self.io = znh5md.IO(str(path))
            self._len = len(self.io)
        except Exception as e:
            raise FormatError(f"cannot read H5MD file {path.name}: {e}", path=str(path)) from e

    def __len__(self) -> int:
        return self._len

    def get(self, index: int) -> ase.Atoms:
        if index < 0:
            index += self._len
        if not 0 <= index < self._len:
            raise IndexError(index)
        return self.io[index]

    def iter_chunks(self, chunk_size: int = 256) -> Iterator[list[ase.Atoms]]:
        for start in range(0, self._len, chunk_size):
            try:
                yield list(self.io[start : min(start + chunk_size, self._len)])
            except Exception as e:
                raise FormatError(
                    f"corrupt H5MD data in {self.path.name} near frame {start}: {e}"
                ) from e


class H5MDAdapter(FormatAdapter):
    name = "h5md"
    extensions = (".h5md", ".h5", ".hdf5")
    description = "H5MD (znh5md dialect) — preferred internal format"
    capabilities = Capabilities(
        energy=True, forces=True, stress=True, calc_other=True, info=True, arrays=True,
        velocities=True, nested_info=True,
    )
    writable = True

    def sniff(self, path: Path) -> float:
        return 0.95 if is_h5md(path) else 0.0

    def open(self, path: Path, **options: Any) -> FrameSource:
        return H5MDFrameSource(path)

    def write(self, frames: Iterable[ase.Atoms], path: Path, **options: Any) -> None:
        chunk = options.pop("chunk_size", 256)
        io = znh5md.IO(
            str(path),
            author=options.pop("author", "N/A"),
            author_email=options.pop("author_email", "N/A"),
            timestep=options.pop("timestep", 1.0),
            tqdm_limit=10**12,
        )
        buf: list[ase.Atoms] = []
        for atoms in frames:
            buf.append(atoms)
            if len(buf) >= chunk:
                io.extend(buf)
                buf = []
        if buf:
            io.extend(buf)


def write_stamp(path: Path, stamp: dict) -> None:
    """Embed atomdb provenance in a file atomdb itself wrote (before it is hashed)."""
    with h5py.File(path, "a") as f:
        if STAMP_GROUP in f:
            del f[STAMP_GROUP]
        g = f.create_group(STAMP_GROUP)
        for k, v in stamp.items():
            g.attrs[k] = v if isinstance(v, (str, int, float)) else json.dumps(v)


def read_stamp(path: Path) -> dict | None:
    if not is_hdf5(path):
        return None
    try:
        with h5py.File(path, "r") as f:
            if STAMP_GROUP not in f:
                return None
            out = {}
            for k, v in f[STAMP_GROUP].attrs.items():
                if isinstance(v, bytes):
                    v = v.decode()
                if isinstance(v, str) and v[:1] in "[{":
                    try:
                        v = json.loads(v)
                    except json.JSONDecodeError:
                        pass
                out[k] = v.item() if isinstance(v, np.generic) else v
            return out
    except OSError:
        return None


def h5md_units(path: Path) -> dict[str, str]:
    """Units recorded in the file (``unit`` attributes), keyed by dataset name."""
    units: dict[str, str] = {}
    with h5py.File(path, "r") as f:

        def visit(name, obj):
            if isinstance(obj, h5py.Dataset) and name.endswith("/value") and "unit" in obj.attrs:
                u = obj.attrs["unit"]
                units[name.split("/")[-2]] = u.decode() if isinstance(u, bytes) else str(u)

        f.visititems(visit)
    return units
