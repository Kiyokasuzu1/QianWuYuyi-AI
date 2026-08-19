"""
Phase 4.0 — R2.7.2.1 CognitiveSession Contract

CognitiveSession 是"连续多轮认知循环"的顶层容器：
  session_id: str
  started_at / ended_at: int (ms)
  turns: List[RuntimeTrace]             — 每轮一个 RuntimeTrace（按时间序，ts 单调）
  memory_ids_seen: List[str]            — 累积记忆 ID（去重）
  personality_versions: List[int]       — 每轮结束后的 PersonalityState 版本（单调 +1 或 0）
  creativity_trace: List[float]         — 每轮结束后的 creativity trait 值（用于趋势计算）
  continuity_reports: List[dict]        — 每轮的 identity_continuity_report
  continuity_report: dict               — Session 级汇总 continuity（aggregating）
  summary: dict
      num_turns: int
      growth_happened_count: int
      creativity_trend: "steady_up" | "fluctuating" | "steady_down" | "flat"
      creativity_delta_abs: float
      overall_status: "ok" | "warnings" | "errors"
      strongest_interest: Optional[str]  — 累积记忆中出现频率最高的 topic
  version: int                           — 每次 run_scenario 调用时自动 +1
  generated_at: str                      — ISO 时间戳

Validator 强制约束：
  - turns 非空；每个 turn 通过 validate_runtime_trace_shape
  - turns[i].run_ts_ms < turns[i+1].run_ts_ms（严格时间序）
  - personality_versions 单调不减（≥ 0）；len(personality_versions) == len(turns)
  - creativity_trace 长度 == len(turns)；值 ∈ [0, 1]
  - summary.num_turns == len(turns)；summary.creativity_delta_abs == last - first
  - summary.creativity_trend 计算正确（见 _compute_creativity_trend）
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.runtime.runtime_trace_schema import (
    validate_runtime_trace_shape,
)

SESSION_FIELDS = (
    "session_id",
    "started_at",
    "ended_at",
    "turns",
    "memory_ids_seen",
    "personality_versions",
    "creativity_trace",
    "continuity_reports",
    "continuity_report",
    "summary",
    "version",
    "generated_at",
)


def _compute_creativity_trend(trace: List[float]) -> str:
    """趋势计算：
    - ≤2 个元素：flat
    - 否则：
        每个点 i ∈ [1..len(trace)-1]: sign_i = sign(trace[i] - trace[i-1])
        若所有 sign_i ≥ 0 且至少一个 > 0 → steady_up
        若所有 sign_i ≤ 0 且至少一个 < 0 → steady_down
        若全部 0 → flat
        否则 → fluctuating
    """
    if len(trace) <= 2:
        return "flat"
    first = trace[0]
    last = trace[-1]
    diffs = [trace[i] - trace[i - 1] for i in range(1, len(trace))]
    pos = sum(1 for d in diffs if d > 1e-9)
    neg = sum(1 for d in diffs if d < -1e-9)
    if pos > 0 and neg == 0:
        return "steady_up"
    if neg > 0 and pos == 0:
        return "steady_down"
    if pos == 0 and neg == 0:
        return "flat"
    return "fluctuating"


def _aggregate_continuity(reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总每轮 continuity：
    - overall_status: all continuous_safe → continuous_safe；否则 → 最差的那个
    - num_safe: 连续通过的轮数
    - total: len(reports)
    """
    total = len(reports)
    num_safe = sum(1 for r in reports if r.get("overall_status") == "continuous_safe")
    status_order = ["continuous_safe", "minor_drift", "significant_drift", "breaks_continuity", "unknown"]
    worst = "continuous_safe" if reports else "unknown"
    worst_rank = 0
    for r in reports:
        s = r.get("overall_status", "unknown")
        try:
            rank = status_order.index(s)
        except ValueError:
            rank = len(status_order) - 1
        if rank > worst_rank:
            worst_rank = rank
            worst = s
    return {
        "overall_status": worst,
        "num_safe": num_safe,
        "total": total,
        "per_turn": reports,
    }


def create_empty_cognitive_session(
    *,
    session_id: str = "s_empty",
    version: int = 0,
) -> Dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "session_id": session_id,
        "started_at": 0,
        "ended_at": 0,
        "turns": [],
        "memory_ids_seen": [],
        "personality_versions": [],
        "creativity_trace": [],
        "continuity_reports": [],
        "continuity_report": _aggregate_continuity([]),
        "summary": {
            "num_turns": 0,
            "growth_happened_count": 0,
            "creativity_trend": "flat",
            "creativity_delta_abs": 0.0,
            "overall_status": "ok",
            "strongest_interest": None,
        },
        "version": version,
        "generated_at": now_iso,
    }


def validate_cognitive_session_shape(s: Any) -> None:
    if not isinstance(s, dict):
        raise ValueError("cognitive_session must be a dict")
    for req in SESSION_FIELDS:
        if req not in s:
            raise ValueError(f"cognitive_session missing top-level key: {req}")

    # session_id non-empty
    if not isinstance(s["session_id"], str) or not s["session_id"].strip():
        raise ValueError("cognitive_session.session_id must be non-empty str")

    # version int ≥ 0
    if not isinstance(s["version"], int) or s["version"] < 0:
        raise ValueError("cognitive_session.version must be int ≥ 0")

    # generated_at non-empty str
    if not isinstance(s["generated_at"], str) or not s["generated_at"].strip():
        raise ValueError("cognitive_session.generated_at must be non-empty str")

    # started_at / ended_at int ≥ 0
    for k in ("started_at", "ended_at"):
        if not isinstance(s[k], int) or s[k] < 0:
            raise ValueError(f"cognitive_session.{k} must be int ≥ 0")

    # turns: list, each validate_runtime_trace_shape, strict ts monotonic
    turns = s["turns"]
    if not isinstance(turns, list):
        raise ValueError("cognitive_session.turns must be a list")
    prev_ts = -1
    for idx, t in enumerate(turns):
        validate_runtime_trace_shape(t)
        ts = t.get("run_ts_ms")
        if not isinstance(ts, int):
            raise ValueError(f"turn[{idx}].run_ts_ms must be int")
        if ts <= prev_ts:
            raise ValueError(
                f"turns[{idx}].run_ts_ms={ts} must be strictly greater than previous={prev_ts}"
            )
        prev_ts = ts
    n = len(turns)

    # memory_ids_seen
    if not isinstance(s["memory_ids_seen"], list):
        raise ValueError("cognitive_session.memory_ids_seen must be a list")
    # 去重检查
    m_ids = s["memory_ids_seen"]
    if len(m_ids) != len(set(m_ids)):
        raise ValueError("cognitive_session.memory_ids_seen must have unique items")

    # personality_versions: list of int ≥ 0, 单调不减, len == n
    pv = s["personality_versions"]
    if not isinstance(pv, list) or len(pv) != n:
        raise ValueError("cognitive_session.personality_versions must be list length == len(turns)")
    prev_v = -1
    for i, v in enumerate(pv):
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"personality_versions[{i}] must be int ≥ 0")
        if v < prev_v:
            raise ValueError(f"personality_versions[{i}]={v} < previous={prev_v} (monotonicity violation)")
        prev_v = v

    # creativity_trace: list of float [0,1], len == n
    ct = s["creativity_trace"]
    if not isinstance(ct, list) or len(ct) != n:
        raise ValueError("cognitive_session.creativity_trace must be list length == len(turns)")
    for i, v in enumerate(ct):
        if not isinstance(v, (int, float)):
            raise ValueError(f"creativity_trace[{i}] must be float")
        if v < -1e-9 or v > 1 + 1e-9:
            raise ValueError(f"creativity_trace[{i}]={v} out of [0,1] range")

    # continuity_reports: list, len == n
    crs = s["continuity_reports"]
    if not isinstance(crs, list) or len(crs) != n:
        raise ValueError("cognitive_session.continuity_reports must be list length == len(turns)")

    # continuity_report: aggregate
    expected_agg = _aggregate_continuity(crs)
    actual = s["continuity_report"]
    # 只检查关键字段
    for k in ("overall_status", "num_safe", "total"):
        if actual.get(k) != expected_agg.get(k):
            raise ValueError(
                f"cognitive_session.continuity_report.{k} mismatch: expected {expected_agg.get(k)!r} got {actual.get(k)!r}"
            )

    # summary dict
    su = s["summary"]
    if not isinstance(su, dict):
        raise ValueError("cognitive_session.summary must be a dict")
    if su.get("num_turns") != n:
        raise ValueError(
            f"summary.num_turns mismatch: expected {n} got {su.get('num_turns')!r}"
        )
    if not isinstance(su.get("growth_happened_count"), int) or su["growth_happened_count"] < 0:
        raise ValueError("summary.growth_happened_count must be int ≥ 0")
    expected_trend = _compute_creativity_trend(ct) if ct else "flat"
    if su.get("creativity_trend") != expected_trend:
        raise ValueError(
            f"summary.creativity_trend mismatch: expected {expected_trend!r} got {su.get('creativity_trend')!r}"
        )
    expected_delta = 0.0 if not ct else (ct[-1] - ct[0])
    if not (
        isinstance(su.get("creativity_delta_abs"), (int, float))
        and abs(float(su["creativity_delta_abs"]) - expected_delta) < 1e-9
    ):
        raise ValueError(
            f"summary.creativity_delta_abs mismatch: expected {expected_delta!r} got {su.get('creativity_delta_abs')!r}"
        )
    if su.get("overall_status") not in ("ok", "warnings", "errors"):
        raise ValueError(f"summary.overall_status={su.get('overall_status')!r} not in (ok,warnings,errors)")
