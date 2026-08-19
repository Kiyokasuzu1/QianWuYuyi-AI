# -*- coding: utf-8 -*-
"""
src/runtime/recovery/runtime_diagnoser.py

Phase C.7.3 Runtime Recovery & Self-Diagnosis —— RuntimeDiagnoser

职责:
  - 读取 RuntimeObserver 输出的 snapshot
  - 分类异常 / 严重级别 / 类型
  - 生成恢复建议(recommendations)
  - 严格只读:不修改任何业务状态

=========================
诊断规则(冻结)
=========================
规则 1: Adapter unhealthy(adapter.healthy=False)
        → severity=medium, type="adapter_unhealthy"
规则 2: Runtime degraded(runtime.degraded=True)
        → severity=high, type="runtime_degraded"
规则 3: 连续失败(consecutive_failures >= threshold,默认 3)
        → severity=high, type="runtime_failure_streak"
规则 4: Audit unavailable(audit.available=False)
        → severity=medium, type="audit_unavailable"
规则 5: Persistence degraded(persistence.degraded=True)
        → severity=high, type="persistence_degraded"
规则 6: 所有 cycle 失败率 > 80%(failed / total > 0.8 且 total > 0)
        → severity=medium, type="cycle_failure_rate_high"
规则 7: Observer 自身异常
        → severity=low, type="observer_unavailable"

=========================
硬约束
=========================
禁止修改任何业务状态(只读)
禁止调用任何写入方法

异常处理:
  - diagnose() 永不抛异常
  - 任何内部异常 → 返回 degraded diagnosis + error 字段
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本 + 常量
# ============================================================

RUNTIME_DIAGNOSER_SCHEMA_VERSION = "1.0"
RUNTIME_DIAGNOSER_NAME = "runtime_diagnoser"
RUNTIME_DIAGNOSER_VERSION = "1.0.0"

# 严重级别(冻结)
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
ALL_SEVERITIES: List[str] = [SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH]
SEVERITY_RANK: Dict[str, int] = {
    SEVERITY_LOW: 0,
    SEVERITY_MEDIUM: 1,
    SEVERITY_HIGH: 2,
}

# 诊断类型常量
TYPE_ADAPTER_UNHEALTHY = "adapter_unhealthy"
TYPE_RUNTIME_DEGRADED = "runtime_degraded"
TYPE_RUNTIME_FAILURE_STREAK = "runtime_failure_streak"
TYPE_AUDIT_UNAVAILABLE = "audit_unavailable"
TYPE_PERSISTENCE_DEGRADED = "persistence_degraded"
TYPE_CYCLE_FAILURE_RATE_HIGH = "cycle_failure_rate_high"
TYPE_OBSERVER_UNAVAILABLE = "observer_unavailable"

# 默认阈值
DEFAULT_FAILURE_STREAK_THRESHOLD = 3
DEFAULT_CYCLE_FAILURE_RATE_THRESHOLD = 0.8

# 失败率最小样本(避免小样本误报)
DEFAULT_CYCLE_FAILURE_MIN_SAMPLES = 5


# ============================================================
# 时间工具
# ============================================================


def _now_iso() -> str:
    """ISO 8601 UTC timestamp(fail-soft)"""
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        try:
            return datetime.utcnow().isoformat() + "Z"
        except Exception:  # noqa: BLE001
            return "1970-01-01T00:00:00Z"


def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(d, dict):
            return d.get(key, default)
        return default
    except Exception:  # noqa: BLE001
        return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        return bool(v)
    except Exception:  # noqa: BLE001
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ============================================================
# RuntimeDiagnoser
# ============================================================


class RuntimeDiagnoser:
    """
    Runtime 自诊断器(Phase C.7.3 / v1.0)

    严格只读地分析 RuntimeObserver 的 snapshot,输出诊断结果。
    diagnose() 永不抛异常。

    Schema 契约(冻结于 v1.0):
      {
        "schema_version": "1.0",
        "healthy": bool,                  # 整体是否健康
        "severity": "low" | "medium" | "high",  # 最高严重级别
        "issue_count": int,                # issue 数量
        "issues": [
          {
            "component": str,             # 出问题的组件
            "severity": "low|medium|high",
            "type": str,                  # 诊断类型
            "message": str,               # 人类可读描述
            "details": dict,              # 附加上下文
          }
        ],
        "recommendations": [str],          # 恢复建议(可由 RuntimeRecovery 执行)
        "timestamp": str,                  # ISO 8601
        "diagnoser": {
          "name": "runtime_diagnoser",
          "version": "1.0.0",
          "schema_version": "1.0",
        },
      }
    """

    SCHEMA_VERSION = RUNTIME_DIAGNOSER_SCHEMA_VERSION
    NAME = RUNTIME_DIAGNOSER_NAME
    VERSION = RUNTIME_DIAGNOSER_VERSION

    def __init__(
        self,
        failure_streak_threshold: int = DEFAULT_FAILURE_STREAK_THRESHOLD,
        cycle_failure_rate_threshold: float = DEFAULT_CYCLE_FAILURE_RATE_THRESHOLD,
        cycle_failure_min_samples: int = DEFAULT_CYCLE_FAILURE_MIN_SAMPLES,
    ) -> None:
        self._failure_streak_threshold = max(1, int(failure_streak_threshold or DEFAULT_FAILURE_STREAK_THRESHOLD))
        self._cycle_failure_rate_threshold = _clamp_float(
            cycle_failure_rate_threshold, 0.0, 1.0, DEFAULT_CYCLE_FAILURE_RATE_THRESHOLD
        )
        self._cycle_failure_min_samples = max(1, int(cycle_failure_min_samples or DEFAULT_CYCLE_FAILURE_MIN_SAMPLES))
        self._lock = threading.RLock()
        # 内部统计(只读,无副作用)
        self._diagnose_count: int = 0
        self._last_diagnosis_ts: Optional[str] = None
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 诊断主入口
    # --------------------------------------------------------

    def diagnose(self, snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        对 RuntimeObserver 的 snapshot 进行诊断。

        Args:
            snapshot: RuntimeObserver.snapshot() 的返回(可为 None)

        Returns:
            诊断结果 dict(永不抛异常)
        """
        try:
            with self._lock:
                issues: List[Dict[str, Any]] = []
                recommendations: List[str] = []

                # 0) snapshot 为空 → 标记为 observer 不可用
                if not isinstance(snapshot, dict):
                    issues.append(self._make_issue(
                        component="observer",
                        severity=SEVERITY_LOW,
                        type_=TYPE_OBSERVER_UNAVAILABLE,
                        message="Observer snapshot is unavailable or invalid",
                        details={"snapshot_type": str(type(snapshot).__name__)},
                    ))
                    recommendations.append(
                        "Verify observer.snapshot() returns a valid dict."
                    )
                    diag = self._build_diagnosis(
                        healthy=False,
                        severity=SEVERITY_LOW,
                        issues=issues,
                        recommendations=recommendations,
                    )
                    self._record_success(diag)
                    return diag

                # 1) 规则 2: runtime degraded → high
                runtime = _safe_get(snapshot, "runtime", {}) or {}
                runtime_degraded = _safe_bool(_safe_get(runtime, "degraded", False))
                runtime_healthy = _safe_bool(_safe_get(runtime, "healthy", True))
                if runtime_degraded:
                    issues.append(self._make_issue(
                        component="runtime",
                        severity=SEVERITY_HIGH,
                        type_=TYPE_RUNTIME_DEGRADED,
                        message="Runtime is in degraded state",
                        details={
                            "degraded": True,
                            "healthy": runtime_healthy,
                            "enabled": _safe_bool(_safe_get(runtime, "enabled", False)),
                            "closed": _safe_bool(_safe_get(runtime, "closed", False)),
                        },
                    ))
                    recommendations.append(
                        "Trigger runtime recovery: re-attach adapters and re-check health."
                    )

                # 2) 规则 1: adapter unhealthy → medium
                adapters = _safe_get(snapshot, "adapters", {}) or {}
                unhealthy_names: List[str] = []
                if isinstance(adapters, dict):
                    for name, adp in adapters.items():
                        if not isinstance(adp, dict):
                            continue
                        if not _safe_bool(_safe_get(adp, "available", True)):
                            # adapter 不可用(health_check 异常)
                            issues.append(self._make_issue(
                                component=str(name),
                                severity=SEVERITY_MEDIUM,
                                type_=TYPE_ADAPTER_UNHEALTHY,
                                message=f"Adapter '{name}' is unavailable (health_check failed)",
                                details={"adapter": str(name)},
                            ))
                            unhealthy_names.append(str(name))
                            recommendations.append(
                                f"Re-attach adapter '{name}' to recover availability."
                            )
                            continue
                        if not _safe_bool(_safe_get(adp, "healthy", True)):
                            status = str(_safe_get(adp, "status", "degraded") or "degraded")
                            issues.append(self._make_issue(
                                component=str(name),
                                severity=SEVERITY_MEDIUM,
                                type_=TYPE_ADAPTER_UNHEALTHY,
                                message=f"Adapter '{name}' is unhealthy (status={status})",
                                details={
                                    "adapter": str(name),
                                    "status": status,
                                    "attached": _safe_bool(_safe_get(adp, "attached", False)),
                                },
                            ))
                            unhealthy_names.append(str(name))
                            recommendations.append(
                                f"Re-check health of adapter '{name}' and re-attach if needed."
                            )
                if not unhealthy_names and not runtime_degraded:
                    # 全部 adapter healthy 时给一个正向提示
                    recommendations.append(
                        "All adapters are healthy. No recovery action needed."
                    )

                # 3) 规则 3: 连续失败 ≥ 阈值 → high
                consecutive_failures = _safe_int(_safe_get(runtime, "consecutive_failures", 0))
                if consecutive_failures >= self._failure_streak_threshold:
                    issues.append(self._make_issue(
                        component="runtime",
                        severity=SEVERITY_HIGH,
                        type_=TYPE_RUNTIME_FAILURE_STREAK,
                        message=(
                            f"Runtime consecutive failures ({consecutive_failures}) "
                            f">= threshold ({self._failure_streak_threshold})"
                        ),
                        details={
                            "consecutive_failures": consecutive_failures,
                            "threshold": self._failure_streak_threshold,
                        },
                    ))
                    recommendations.append(
                        "Reset runtime temporary state and mark as degraded to break the failure streak."
                    )

                # 4) 规则 4: audit unavailable → medium
                audit = _safe_get(snapshot, "audit", {}) or {}
                if isinstance(audit, dict):
                    audit_available = _safe_bool(_safe_get(audit, "available", False))
                    if not audit_available:
                        issues.append(self._make_issue(
                            component="audit",
                            severity=SEVERITY_MEDIUM,
                            type_=TYPE_AUDIT_UNAVAILABLE,
                            message="Audit chain is unavailable",
                            details={
                                "audit_available": False,
                                "latest_cycle_id": _safe_get(audit, "latest_cycle_id", None),
                                "latest_timestamp": _safe_get(audit, "latest_timestamp", None),
                            },
                        ))
                        recommendations.append(
                            "Verify persistence is attached and audit chain sources are reachable."
                        )

                # 5) 规则 5: persistence degraded → high
                persistence = _safe_get(snapshot, "persistence", {}) or {}
                if isinstance(persistence, dict):
                    if _safe_bool(_safe_get(persistence, "degraded", False)):
                        issues.append(self._make_issue(
                            component="persistence",
                            severity=SEVERITY_HIGH,
                            type_=TYPE_PERSISTENCE_DEGRADED,
                            message="Persistence layer is degraded",
                            details={
                                "degraded": True,
                                "available": _safe_bool(_safe_get(persistence, "available", False)),
                                "write_count": _safe_int(_safe_get(persistence, "write_count", 0)),
                                "write_error_count": _safe_int(_safe_get(persistence, "write_error_count", 0)),
                            },
                        ))
                        recommendations.append(
                            "Inspect persistence write errors; recovery action will not retry writes."
                        )

                # 6) 规则 6: 失败率过高 → medium
                cycles = _safe_get(snapshot, "cycles", {}) or {}
                if isinstance(cycles, dict):
                    total = _safe_int(_safe_get(cycles, "total", 0))
                    failed = _safe_int(_safe_get(cycles, "failed", 0))
                    if total >= self._cycle_failure_min_samples and total > 0:
                        failure_rate = float(failed) / float(total)
                        if failure_rate > self._cycle_failure_rate_threshold:
                            issues.append(self._make_issue(
                                component="runtime",
                                severity=SEVERITY_MEDIUM,
                                type_=TYPE_CYCLE_FAILURE_RATE_HIGH,
                                message=(
                                    f"Cycle failure rate {failure_rate:.2%} "
                                    f"> {self._cycle_failure_rate_threshold:.2%}"
                                ),
                                details={
                                    "total": total,
                                    "failed": failed,
                                    "completed": _safe_int(_safe_get(cycles, "completed", 0)),
                                    "failure_rate": round(failure_rate, 4),
                                    "threshold": self._cycle_failure_rate_threshold,
                                },
                            ))
                            recommendations.append(
                                "Investigate recurring cycle failures; re-check health before resuming."
                            )

                # 7) 综合严重度 + 整体 healthy
                severity = self._max_severity(issues)
                healthy = (severity is None) and runtime_healthy and not runtime_degraded

                # 8) 去重 recommendations(保持顺序)
                deduped_recs: List[str] = []
                seen: set = set()
                for r in recommendations:
                    if r and r not in seen:
                        seen.add(r)
                        deduped_recs.append(r)

                diag = self._build_diagnosis(
                    healthy=bool(healthy),
                    severity=severity or SEVERITY_LOW,
                    issues=issues,
                    recommendations=deduped_recs,
                )
                self._record_success(diag)
                return diag
        except Exception as exc:  # noqa: BLE001
            # 兜底:任何未捕获异常
            with self._lock:
                self._last_error = repr(exc)
            logger.debug(f"[phase_c7_3] diagnoser.diagnose 异常(已隔离): {exc}")
            return {
                "schema_version": self.SCHEMA_VERSION,
                "healthy": False,
                "severity": SEVERITY_LOW,
                "issue_count": 1,
                "issues": [
                    {
                        "component": "diagnoser",
                        "severity": SEVERITY_LOW,
                        "type": "diagnoser_internal_error",
                        "message": "Diagnoser encountered an internal error",
                        "details": {"error": repr(exc)},
                    }
                ],
                "recommendations": [
                    "Diagnoser itself failed; verify input snapshot is a valid dict."
                ],
                "timestamp": _now_iso(),
                "diagnoser": {
                    "name": self.NAME,
                    "version": self.VERSION,
                    "schema_version": self.SCHEMA_VERSION,
                },
            }

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------

    def _make_issue(
        self,
        component: str,
        severity: str,
        type_: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "component": str(component or "unknown"),
            "severity": str(severity or SEVERITY_LOW),
            "type": str(type_ or "unknown"),
            "message": str(message or ""),
            "details": dict(details or {}),
        }

    def _max_severity(self, issues: List[Dict[str, Any]]) -> Optional[str]:
        """返回 issues 中最高严重级别;issues 为空返回 None"""
        if not issues:
            return None
        try:
            max_rank = -1
            max_sev: Optional[str] = None
            for iss in issues:
                sev = str(_safe_get(iss, "severity", SEVERITY_LOW))
                rank = SEVERITY_RANK.get(sev, -1)
                if rank > max_rank:
                    max_rank = rank
                    max_sev = sev
            return max_sev
        except Exception:  # noqa: BLE001
            return SEVERITY_LOW

    def _build_diagnosis(
        self,
        healthy: bool,
        severity: str,
        issues: List[Dict[str, Any]],
        recommendations: List[str],
    ) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "healthy": bool(healthy),
            "severity": str(severity or SEVERITY_LOW),
            "issue_count": int(len(issues)),
            "issues": list(issues),
            "recommendations": list(recommendations),
            "timestamp": _now_iso(),
            "diagnoser": {
                "name": self.NAME,
                "version": self.VERSION,
                "schema_version": self.SCHEMA_VERSION,
            },
        }

    def _record_success(self, diag: Dict[str, Any]) -> None:
        with self._lock:
            self._diagnose_count += 1
            self._last_diagnosis_ts = str(_safe_get(diag, "timestamp", _now_iso()))
            self._last_error = None

    # --------------------------------------------------------
    # 状态查询(只读)
    # --------------------------------------------------------

    @property
    def diagnose_count(self) -> int:
        with self._lock:
            return int(self._diagnose_count)

    @property
    def last_diagnosis_ts(self) -> Optional[str]:
        with self._lock:
            return self._last_diagnosis_ts

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "diagnose_count": int(self._diagnose_count),
                "last_diagnosis_ts": self._last_diagnosis_ts,
                "last_error": self._last_error,
                "failure_streak_threshold": int(self._failure_streak_threshold),
                "cycle_failure_rate_threshold": float(self._cycle_failure_rate_threshold),
            }


# ============================================================
# 内部辅助
# ============================================================


def _clamp_float(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(v)
        if f < lo:
            return lo
        if f > hi:
            return hi
        return f
    except (TypeError, ValueError):
        return default


# ============================================================
# 工厂 / 辅助 API
# ============================================================


def create_runtime_diagnoser(
    failure_streak_threshold: int = DEFAULT_FAILURE_STREAK_THRESHOLD,
    cycle_failure_rate_threshold: float = DEFAULT_CYCLE_FAILURE_RATE_THRESHOLD,
    cycle_failure_min_samples: int = DEFAULT_CYCLE_FAILURE_MIN_SAMPLES,
) -> RuntimeDiagnoser:
    """工厂函数:创建一个 RuntimeDiagnoser。"""
    return RuntimeDiagnoser(
        failure_streak_threshold=failure_streak_threshold,
        cycle_failure_rate_threshold=cycle_failure_rate_threshold,
        cycle_failure_min_samples=cycle_failure_min_samples,
    )


def safe_diagnose(
    diagnoser: Optional[RuntimeDiagnoser],
    snapshot: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """全局安全 diagnose(任何异常都吸收)"""
    if diagnoser is None:
        diagnoser = create_runtime_diagnoser()
    try:
        return diagnoser.diagnose(snapshot) or {}
    except Exception:  # noqa: BLE001
        return {
            "schema_version": RUNTIME_DIAGNOSER_SCHEMA_VERSION,
            "healthy": False,
            "severity": SEVERITY_LOW,
            "issue_count": 0,
            "issues": [],
            "recommendations": [],
            "timestamp": _now_iso(),
            "diagnoser": {
                "name": RUNTIME_DIAGNOSER_NAME,
                "version": RUNTIME_DIAGNOSER_VERSION,
                "schema_version": RUNTIME_DIAGNOSER_SCHEMA_VERSION,
            },
        }


# ============================================================
# 公共 API
# ============================================================


__all__ = [
    "RUNTIME_DIAGNOSER_SCHEMA_VERSION",
    "RUNTIME_DIAGNOSER_NAME",
    "RUNTIME_DIAGNOSER_VERSION",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "SEVERITY_HIGH",
    "ALL_SEVERITIES",
    "SEVERITY_RANK",
    "TYPE_ADAPTER_UNHEALTHY",
    "TYPE_RUNTIME_DEGRADED",
    "TYPE_RUNTIME_FAILURE_STREAK",
    "TYPE_AUDIT_UNAVAILABLE",
    "TYPE_PERSISTENCE_DEGRADED",
    "TYPE_CYCLE_FAILURE_RATE_HIGH",
    "TYPE_OBSERVER_UNAVAILABLE",
    "DEFAULT_FAILURE_STREAK_THRESHOLD",
    "DEFAULT_CYCLE_FAILURE_RATE_THRESHOLD",
    "DEFAULT_CYCLE_FAILURE_MIN_SAMPLES",
    "RuntimeDiagnoser",
    "create_runtime_diagnoser",
    "safe_diagnose",
    "_now_iso",
]
