"""Content-addressed, append-only file store: ``files/sha256/ab/<sha256>``.

Files are copied (never moved), hashed while copying, verified, and made read-only.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..errors import IntegrityProblem
from .hashing import CHUNK, sha256_file


@dataclass
class StoredFile:
    sha256: str
    relpath: str
    size: int
    is_new: bool


class FileStore:
    def __init__(self, root: Path, tmp: Path):
        self.root = root
        self.tmp = tmp

    @staticmethod
    def relpath_for(sha256: str) -> str:
        return f"sha256/{sha256[:2]}/{sha256}"

    def path_for(self, sha256: str) -> Path:
        return self.root / self.relpath_for(sha256)

    def abspath(self, relpath: str) -> Path:
        return self.root / relpath

    def contains(self, sha256: str) -> bool:
        return self.path_for(sha256).exists()

    def ingest(self, src: str | Path) -> StoredFile:
        """Copy ``src`` into the store. The source is only read, never modified."""
        src = Path(src)
        self.tmp.mkdir(parents=True, exist_ok=True)
        tmp_path = self.tmp / f"ingest-{uuid.uuid4().hex}"
        h = hashlib.sha256()
        size = 0
        try:
            with open(src, "rb") as fin, open(tmp_path, "wb") as fout:
                while chunk := fin.read(CHUNK):
                    h.update(chunk)
                    fout.write(chunk)
                    size += len(chunk)
                fout.flush()
                os.fsync(fout.fileno())
            digest = h.hexdigest()
            dest = self.path_for(digest)
            if dest.exists():
                return StoredFile(digest, self.relpath_for(digest), size, is_new=False)
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.replace(tmp_path, dest)
            return StoredFile(digest, self.relpath_for(digest), size, is_new=True)
        finally:
            if tmp_path.exists():
                os.chmod(tmp_path, stat.S_IWUSR | stat.S_IRUSR)
                tmp_path.unlink()

    def verify(self, sha256: str) -> bool:
        p = self.path_for(sha256)
        return p.exists() and sha256_file(p) == sha256

    def export_copy(self, sha256: str, dest: Path) -> Path:
        if not self.contains(sha256):
            raise IntegrityProblem(f"store file missing: {sha256}")
        shutil.copyfile(self.path_for(sha256), dest)
        return dest
