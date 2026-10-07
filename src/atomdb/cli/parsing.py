"""Helpers for parsing CLI values."""

from __future__ import annotations

import json
from typing import Any

from ..errors import InvalidInput


def auto_value(text: str) -> Any:
    t = text.strip()
    if t.lower() in ("true", "yes"):
        return True
    if t.lower() in ("false", "no"):
        return False
    if t.lower() in ("none", "null"):
        return None
    for cast in (int, float):
        try:
            return cast(t)
        except ValueError:
            pass
    if t[:1] in "[{":
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            pass
    return t


def key_values(items: list[str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise InvalidInput(f"expected key=value, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = auto_value(v)
    return out


def csv(text: str | None) -> list[str]:
    return [x.strip() for x in (text or "").split(",") if x.strip()]
