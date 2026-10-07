from __future__ import annotations

from fastapi import Request

from ..services import AtomDB


def get_adb(request: Request) -> AtomDB:
    return request.app.state.adb
