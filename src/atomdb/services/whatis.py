"""Identify an unknown file: "where did this come from?".

Three independent lines of evidence, strongest first:
1. exact bytes: sha256 matches a stored file
2. embedded stamp: the ``/atomdb`` group atomdb writes into its own H5MD files
3. content: structure fingerprints of the file's frames match indexed frames
   (works for re-saved, converted, subsampled or concatenated copies)
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
from sqlalchemy import select

from ..db.models import Dataset, DatasetFile, File, Frame
from ..errors import AtomDBError, InvalidInput
from ..formats.h5md import H5MDFrameSource, read_stamp
from ..formats.registry import detect
from ..frames import fingerprint
from ..store.hashing import sha256_file
from .context import AtomDB
from .datasets import file_out, summary

BATCH = 500


def _ranges(idxs: list[int], max_segments: int = 20) -> str:
    idxs = sorted(idxs)
    out, start, prev = [], idxs[0], idxs[0]
    for i in idxs[1:] + [None]:
        if i is not None and i == prev + 1:
            prev = i
            continue
        out.append(f"{start}" if start == prev else f"{start}-{prev}")
        if i is not None:
            start = prev = i
    if len(out) > max_segments:
        return ",".join(out[:max_segments]) + f",… ({len(out) - max_segments} more ranges)"
    return ",".join(out)


def whatis(adb: AtomDB, path: str | Path, max_frames: int = 5000, confirm: int = 3) -> dict:
    p = Path(path).expanduser()
    if not p.is_file():
        raise InvalidInput(f"not a file: {p}")
    sha = sha256_file(p)
    result: dict = {
        "path": str(p.resolve()), "sha256": sha, "size_bytes": p.stat().st_size,
        "exact_match": None, "stamp": None, "content_matches": [], "format": None,
        "n_frames": None, "frames_checked": 0, "verdict": "unknown",
    }
    with adb.session() as s:
        f = s.execute(select(File).where(File.sha256 == sha)).scalar_one_or_none()
        if f is not None:
            dss = s.execute(
                select(Dataset).join(DatasetFile, DatasetFile.dataset_id == Dataset.id)
                .where(DatasetFile.file_id == f.id).distinct()
            ).scalars().all()
            result["exact_match"] = {
                "file": file_out(adb, f).model_dump(mode="json"),
                "datasets": [summary(s, d).model_dump(mode="json") for d in dss],
            }

    stamp = read_stamp(p)
    if stamp:
        result["stamp"] = stamp
        result["stamp"]["same_instance"] = stamp.get("instance_uuid") == adb.db.instance_uuid()

    # content fingerprints
    try:
        adapter = detect(p)
        src = adapter.open(p)
        result["format"] = adapter.name
    except AtomDBError as e:
        result["content_error"] = e.message
        src = None
    if src is not None:
        fps: list[tuple[int, str]] = []
        frames_by_idx = {}
        try:
            if src.random_access:
                n = len(src)
                result["n_frames"] = n
                sel = np.unique(np.linspace(0, n - 1, min(n, max_frames)).astype(int)) if n else []
                for i in sel:
                    a = src.get(int(i))
                    fps.append((int(i), fingerprint(a)))
                    if len(frames_by_idx) < confirm:
                        frames_by_idx[int(i)] = a
            else:
                n = 0
                for chunk in src.iter_chunks():
                    for a in chunk:
                        if n < max_frames:
                            fps.append((n, fingerprint(a)))
                            if len(frames_by_idx) < confirm:
                                frames_by_idx[n] = a
                        n += 1
                result["n_frames"] = n
        except AtomDBError as e:
            result["content_error"] = e.message
        result["frames_checked"] = len(fps)
        result["content_matches"] = _match(adb, fps, frames_by_idx)

    if result["exact_match"]:
        result["verdict"] = "identical file is in the database"
    elif result["stamp"] and result["stamp"].get("same_instance"):
        result["verdict"] = f"written by this atomdb for {result['stamp'].get('dataset_id')}"
    elif result["content_matches"]:
        best = result["content_matches"][0]
        result["verdict"] = (
            f"{best['fraction_of_file']:.0%} of checked frames found in {best['dataset']['id']}"
        )
    return result


def _match(adb: AtomDB, fps: list[tuple[int, str]], frames_by_idx: dict) -> list[dict]:
    if not fps:
        return []
    by_fp: dict[str, list[int]] = defaultdict(list)
    for i, fp in fps:
        by_fp[fp].append(i)
    hits: dict[str, dict] = {}
    with adb.session() as s:
        keys = list(by_fp)
        for k in range(0, len(keys), BATCH):
            rows = s.execute(
                select(Frame.dataset_id, Frame.idx, Frame.fingerprint)
                .where(Frame.fingerprint.in_(keys[k : k + BATCH]))
            ).all()
            for ds_id, idx, fp in rows:
                h = hits.setdefault(ds_id, {"file_idx": set(), "ds_idx": [], "pairs": []})
                for fi in by_fp[fp]:
                    h["file_idx"].add(fi)
                    h["pairs"].append((fi, idx))
                h["ds_idx"].append(idx)
        out = []
        for ds_id, h in hits.items():
            ds = s.get(Dataset, ds_id)
            confirmed = _confirm(adb, ds, h["pairs"], frames_by_idx)
            out.append({
                "dataset": summary(s, ds).model_dump(mode="json"),
                "matched_frames": len(h["file_idx"]),
                "fraction_of_file": len(h["file_idx"]) / len(fps),
                "fraction_of_dataset": len(set(h["ds_idx"])) / (ds.n_frames or 1),
                "dataset_frames": _ranges(list(set(h["ds_idx"]))),
                "coordinates_confirmed": confirmed,
            })
    out.sort(key=lambda m: (-m["matched_frames"], m["dataset"]["id"]))
    return out


def _confirm(adb: AtomDB, ds: Dataset, pairs, frames_by_idx) -> bool | None:
    """Compare actual coordinates for a few matched frames (fingerprints are coarse)."""
    checks = [(fi, di) for fi, di in pairs if fi in frames_by_idx][:3]
    if not checks or ds.primary_file is None:
        return None
    src = H5MDFrameSource(adb.store.abspath(ds.primary_file.store_relpath))
    for fi, di in checks:
        a, b = frames_by_idx[fi], src.get(di)
        if len(a) != len(b) or not np.allclose(a.positions, b.positions, atol=1e-3):
            return False
    return True
