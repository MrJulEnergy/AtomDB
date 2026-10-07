"""Backups: one ``.tar.zst`` with a consistent database snapshot, all store files,
configuration and a checksummed manifest. Restore verifies every file."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import zstandard
from sqlalchemy import func, select

from ..config import DB_FILENAME, get_paths
from ..db.engine import current_revision
from ..db.models import Dataset, Edge, File, Node
from ..errors import IntegrityProblem, InvalidInput
from ..reproducibility import software_versions
from ..store.hashing import sha256_file
from .context import AtomDB

MANIFEST = "MANIFEST.json"


def _snapshot_db(src: Path, dst: Path) -> None:
    """Consistent copy of a live SQLite database (online backup API)."""
    with sqlite3.connect(src) as a, sqlite3.connect(dst) as b:
        a.backup(b)


def backup(adb: AtomDB, out: Path | None = None, level: int = 10) -> dict:
    ts = datetime.now(UTC)
    if out is None:
        out = adb.paths.backups / f"atomdb-backup-{ts:%Y-%m-%d-%H%M%S}.tar.zst"
    out = Path(out).expanduser()
    if out.exists():
        raise InvalidInput(f"backup file exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with adb.session() as s:
        files = [(f.store_relpath, f.sha256, f.size_bytes) for f in s.execute(select(File)).scalars()]
        counts = {
            "nodes": s.execute(select(func.count()).select_from(Node)).scalar_one(),
            "datasets": s.execute(select(func.count()).select_from(Dataset)).scalar_one(),
            "files": len(files),
            "edges": s.execute(select(func.count()).select_from(Edge)).scalar_one(),
        }
    with tempfile.TemporaryDirectory(dir=adb.paths.tmp) as tmp:
        snap = Path(tmp) / DB_FILENAME
        _snapshot_db(adb.paths.db, snap)
        manifest = {
            "format": "atomdb-backup/1", "created_at": ts.isoformat(),
            "instance_uuid": adb.db.instance_uuid(), "source_root": str(adb.paths.root),
            "schema_revision": current_revision(adb.db.engine), "software": software_versions(),
            "counts": counts,
            "database": {"path": DB_FILENAME, "sha256": sha256_file(snap),
                         "size": snap.stat().st_size},
            "files": [{"path": f"files/{rel}", "sha256": sha, "size": size}
                      for rel, sha, size in files],
            "config": [p.name for p in adb.paths.root.glob("*.toml")],
        }
        missing = [f["path"] for f in manifest["files"]
                   if not (adb.paths.root / f["path"]).exists()]
        if missing:
            raise IntegrityProblem(f"{len(missing)} store files are missing; run `atomdb validate` "
                                   f"first (e.g. {missing[0]})")
        part = out.with_name(out.name + ".part")
        h = hashlib.sha256()
        try:
            with open(part, "wb") as raw:
                writer = _HashingWriter(raw, h)
                cctx = zstandard.ZstdCompressor(level=level, threads=-1)
                with cctx.stream_writer(writer, closefd=False) as zw, \
                        tarfile.open(fileobj=zw, mode="w|") as tar:
                    data = json.dumps(manifest, indent=1).encode()
                    info = tarfile.TarInfo(MANIFEST)
                    info.size = len(data)
                    info.mtime = int(ts.timestamp())
                    tar.addfile(info, io.BytesIO(data))
                    tar.add(snap, arcname=DB_FILENAME)
                    for name in manifest["config"]:
                        tar.add(adb.paths.root / name, arcname=name)
                    for f in manifest["files"]:
                        tar.add(adb.paths.root / f["path"], arcname=f["path"])
            part.replace(out)
        finally:
            if part.exists():
                part.unlink()
    digest = h.hexdigest()
    out.with_name(out.name + ".sha256").write_text(f"{digest}  {out.name}\n")
    return {"archive": str(out), "sha256": digest, "size_bytes": out.stat().st_size,
            "counts": counts}


class _HashingWriter(io.RawIOBase):
    def __init__(self, raw, h):
        self.raw, self.h = raw, h

    def writable(self):
        return True

    def write(self, b):
        self.h.update(b)
        return self.raw.write(b)


@contextmanager
def _open(archive: Path):
    """Stream-read a .tar.zst archive."""
    with open(archive, "rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as zs, \
            tarfile.open(fileobj=zs, mode="r|") as tar:
        yield tar


def verify_archive(archive: Path) -> dict:
    archive = Path(archive)
    side = archive.with_name(archive.name + ".sha256")
    problems = []
    if side.exists():
        expected = side.read_text().split()[0]
        if sha256_file(archive) != expected:
            problems.append("archive checksum does not match .sha256 sidecar")
    manifest, seen = None, {}
    try:
        _read_all(archive, seen)
    except (zstandard.ZstdError, tarfile.TarError, OSError, EOFError) as e:
        return {"ok": False, "problems": [*problems, f"archive unreadable: {e}"]}
    with _open(archive) as tar:
        for m in tar:
            if m.name == MANIFEST:
                manifest = json.loads(tar.extractfile(m).read())
                break
    if manifest is None:
        return {"ok": False, "problems": ["no manifest in archive"]}
    want = {manifest["database"]["path"]: manifest["database"]["sha256"]}
    want.update({f["path"]: f["sha256"] for f in manifest["files"]})
    for path, sha in want.items():
        if path not in seen:
            problems.append(f"missing in archive: {path}")
        elif seen[path] != sha:
            problems.append(f"checksum mismatch: {path}")
    return {"ok": not problems, "problems": problems, "manifest": {
        k: manifest[k] for k in ("created_at", "instance_uuid", "source_root", "schema_revision",
                                 "counts")}}


def _read_all(archive: Path, seen: dict) -> None:
    with _open(archive) as tar:
        for m in tar:
            if not m.isfile():
                continue
            f = tar.extractfile(m)
            h = hashlib.sha256()
            while chunk := f.read(1 << 20):
                h.update(chunk)
            seen[m.name] = h.hexdigest()


def restore(archive: Path, home: Path) -> dict:
    """Restore into an empty/new store directory (never over existing data)."""
    archive = Path(archive).expanduser()
    home = Path(home).expanduser().resolve()
    if home.exists() and any(home.iterdir()):
        raise InvalidInput(f"restore target is not empty: {home}")
    check = verify_archive(archive)
    if not check["ok"]:
        raise IntegrityProblem("archive verification failed: " + "; ".join(check["problems"][:5]))
    home.mkdir(parents=True, exist_ok=True)
    with _open(archive) as tar:
        for m in tar:
            name = Path(m.name)
            if name.is_absolute() or ".." in name.parts or not (m.isfile() or m.isdir()):
                raise IntegrityProblem(f"unsafe archive member: {m.name}")
            if m.name == MANIFEST:
                (home / "restored-from-MANIFEST.json").write_bytes(tar.extractfile(m).read())
                continue
            dest = home / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as out:
                shutil.copyfileobj(tar.extractfile(m), out)
            if m.name.startswith("files/"):
                dest.chmod(0o444)
    paths = get_paths(home)
    paths.ensure()
    adb = AtomDB.open(home)
    from .validate import validate

    rep = validate(adb)
    adb.close()
    return {"home": str(home), "verified": True, "validation": {k: rep[k] for k in
                                                                ("ok", "errors", "warnings")},
            "manifest": check["manifest"]}
