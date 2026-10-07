"""Stable human-readable IDs: ``<PREFIX>-<YEAR>-<NNNNNN>`` (e.g. ``DS-2026-000142``)."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import IdCounter
from .db.types import utcnow

PREFIXES = {
    "dataset": "DS",
    "file": "FL",
    "md": "MD",
    "dft": "DFT",
    "sampling": "SMP",
    "conversion": "CNV",
    "training": "TRN",
    "import": "IMP",
    "calculation": "CALC",
    "model": "MDL",
    "hpc_job": "JOB",
    "software": "SW",
}
KIND_BY_PREFIX = {v: k for k, v in PREFIXES.items()}
ID_RE = re.compile(r"^(?P<prefix>[A-Z]+)-(?P<year>\d{4})-(?P<seq>\d{6,})$")


def new_id(session: Session, kind: str) -> str:
    prefix = PREFIXES[kind]
    year = utcnow().year
    counter = session.execute(
        select(IdCounter).where(IdCounter.prefix == prefix, IdCounter.year == year)
    ).scalar_one_or_none()
    if counter is None:
        counter = IdCounter(prefix=prefix, year=year, next_value=1)
        session.add(counter)
    value = counter.next_value
    counter.next_value = value + 1
    session.flush()
    return f"{prefix}-{year}-{value:06d}"


def is_id(text: str) -> bool:
    return bool(ID_RE.match(text))


def normalize_id(text: str) -> str:
    """Accept lower case and short sequence numbers: ``ds-2026-142`` -> ``DS-2026-000142``."""
    t = text.strip().upper()
    m = re.match(r"^([A-Z]+)-(\d{4})-(\d+)$", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{int(m.group(3)):06d}"
    return t
