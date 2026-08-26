# -*- coding: utf-8 -*-
"""
src/temporal/__init__.py

Phase B1a — Temporal Core 包入口（纯函数，零外部依赖）。
"""
from __future__ import annotations

from src.temporal.temporal_core import (
    DEFAULT_NAIVE_ASSUME_TZ,
    DEFAULT_TIMELINE_MAX_ITEMS,
    DEFAULT_USER_TZ,
    FUTURE_TOLERANCE_SECONDS,
    Duration,
    TimelineEntry,
    build_timeline,
    duration_between,
    normalize_time,
    relative_time,
    render_user_tz,
    to_utc_iso,
)

__all__ = [
    "DEFAULT_NAIVE_ASSUME_TZ",
    "DEFAULT_TIMELINE_MAX_ITEMS",
    "DEFAULT_USER_TZ",
    "FUTURE_TOLERANCE_SECONDS",
    "Duration",
    "TimelineEntry",
    "build_timeline",
    "duration_between",
    "normalize_time",
    "relative_time",
    "render_user_tz",
    "to_utc_iso",
]
