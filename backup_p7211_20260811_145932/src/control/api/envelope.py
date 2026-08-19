# -*- coding: utf-8 -*-
"""
src/control/api/envelope.py

Phase C.10.3 — 统一响应信封

所有 Server API 响应均使用同一 envelope:

    {
        "success": bool,
        "data": <payload>,
        "error": "",
        "timestamp": "ISO8601",
        "schema_version": "1.0",
        "degraded": bool
    }
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


API_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_envelope(
    success: bool,
    data: Any = None,
    error: str = "",
    degraded: bool = False,
    schema_version: str = API_SCHEMA_VERSION,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """构造统一 envelope。"""
    return {
        "success": bool(success),
        "data": data if data is not None else {},
        "error": str(error or ""),
        "timestamp": timestamp or _now_iso(),
        "schema_version": str(schema_version or API_SCHEMA_VERSION),
        "degraded": bool(degraded),
    }


def make_success_envelope(
    data: Any = None,
    schema_version: str = API_SCHEMA_VERSION,
) -> Dict[str, Any]:
    """成功响应。"""
    return make_envelope(
        success=True,
        data=data if data is not None else {},
        error="",
        degraded=False,
        schema_version=schema_version,
    )


def make_error_envelope(
    error: str,
    data: Any = None,
    degraded: bool = True,
    schema_version: str = API_SCHEMA_VERSION,
) -> Dict[str, Any]:
    """失败/降级响应。"""
    return make_envelope(
        success=False,
        data=data if data is not None else {},
        error=str(error or ""),
        degraded=degraded,
        schema_version=schema_version,
    )


def is_envelope(obj: Any) -> bool:
    """判断一个 dict 是否为标准 envelope。"""
    if not isinstance(obj, dict):
        return False
    return all(k in obj for k in ("success", "data", "error", "schema_version"))


__all__ = [
    "API_SCHEMA_VERSION",
    "is_envelope",
    "make_envelope",
    "make_error_envelope",
    "make_success_envelope",
]
