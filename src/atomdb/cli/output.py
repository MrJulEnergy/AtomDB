"""Uniform CLI output: human-readable text or a stable JSON envelope.

JSON envelope: ``{"ok": true, "data": ...}`` or
``{"ok": false, "error": {"code": ..., "message": ..., "details": {...}}}``.
"""

from __future__ import annotations

import functools
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import typer
from pydantic import BaseModel

from ..errors import AtomDBError


@dataclass
class State:
    home: str | None = None
    json: bool = False


state = State()


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_jsonable(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


def emit(data: Any, human: Callable[[Any], None] | None = None) -> None:
    if state.json:
        print(json.dumps({"ok": True, "data": _jsonable(data)}, indent=2, default=str))
    elif human is not None:
        human(data)
    else:
        print(json.dumps(_jsonable(data), indent=2, default=str))


def fail(err: AtomDBError) -> None:
    if state.json:
        print(json.dumps({"ok": False, "error": err.to_dict()}, indent=2, default=str))
    else:
        print(f"error [{err.code}]: {err.message}", file=sys.stderr)
    raise typer.Exit(err.exit_code)


def handle_errors(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AtomDBError as err:
            fail(err)

    return wrapper


def kv(rows: list[tuple[str, Any]], indent: int = 0) -> None:
    width = max((len(k) for k, _ in rows), default=0)
    for k, v in rows:
        if v is None or v == "" or v == []:
            continue
        print(" " * indent + f"{k:<{width}}  {v}")


def table(headers: list[str], rows: list[list[Any]]) -> None:
    cols = [headers] + [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cols) for i in range(len(headers))]
    for j, r in enumerate(cols):
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
        if j == 0:
            print("  ".join("-" * w for w in widths))
