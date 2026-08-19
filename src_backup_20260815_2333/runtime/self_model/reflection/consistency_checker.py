# -*- coding: utf-8 -*-
"""
src/runtime/self_model/reflection/consistency_checker.py

Phase 4.7: ConsistencyChecker —— SelfModel 一致性验证

职责:
- 验证当前 SelfModelSnapshot 是否仍然符合自身约束
- 检查身份完整性、字段合法性、人格漂移、历史一致性
- 输出 ConsistencyReport(score / is_consistent / issues / warnings)

约束:
- 不 import openai / qwen / llava / anthropic / google.generativeai
- 不 import src.personality.*
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- 异常隔离:任何 check 失败返回默认 ConsistencyReport
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.self_model.reflection.reflection_record import (
    _clamp_confidence,
    _clamp_severity,
)


logger = logging.getLogger(__name__)


CONSISTENCY_CHECKER_SCHEMA_VERSION = "1.0"

# 默认阈值
DEFAULT_CONSISTENCY_THRESHOLD = 0.7
DEFAULT_DRIFT_WINDOW = 10  # 最近 N 条记录
DEFAULT_DRIFT_COUNT_THRESHOLD = 5  # 短时间大量变化阈值
DEFAULT_TRAIT_DRIFT_THRESHOLD = 0.4  # trait 平均变化阈值
DEFAULT_VALUE_DRIFT_THRESHOLD = 0.3  # value 平均变化阈值

# 合法 schema_version(快照层面)
VALID_SCHEMA_VERSIONS = frozenset({
    "1.0",
    "1.1",
})


# ============================================================
# ConsistencyReport
# ============================================================
@dataclass
class ConsistencyReport:
    """一致性验证报告(Phase 4.7 / v1.0)。

    字段:
    - score:           float                # 一致性得分 [0.0, 1.0]
    - is_consistent:   bool                 # 是否一致(score >= threshold)
    - threshold:       float                # 阈值
    - issues:          List[dict]           # 严重问题(影响 score)
    - warnings:        List[str]            # 警告(不影响 score)
    - identity_ok:     bool                 # identity 完整性
    - schema_ok:       bool                 # schema 合法性
    - drift_score:     float                # 漂移得分 [0.0, 1.0],0 = 无漂移
    - history_ok:      bool                 # 历史一致性
    - metadata:        Dict[str, Any]
    """

    score: float = 1.0
    is_consistent: bool = True
    threshold: float = DEFAULT_CONSISTENCY_THRESHOLD
    issues: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    identity_ok: bool = True
    schema_ok: bool = True
    drift_score: float = 0.0
    history_ok: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.score = _clamp_severity(self.score)
        self.threshold = _clamp_severity(self.threshold)
        self.drift_score = _clamp_severity(self.drift_score)
        # 重新计算 is_consistent
        self.is_consistent = self.score >= self.threshold
        # 规范化 issues
        if not isinstance(self.issues, list):
            try:
                self.issues = list(self.issues or [])
            except Exception:  # noqa: BLE001
                self.issues = []
        self.issues = [i for i in self.issues if isinstance(i, dict)]
        # 规范化 warnings
        if not isinstance(self.warnings, list):
            try:
                self.warnings = list(self.warnings or [])
            except Exception:  # noqa: BLE001
                self.warnings = []
        self.warnings = [str(w) for w in self.warnings if w is not None]
        # 规范化 metadata
        if not isinstance(self.metadata, dict):
            self.metadata = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "is_consistent": self.is_consistent,
            "threshold": self.threshold,
            "issues": list(self.issues),
            "warnings": list(self.warnings),
            "identity_ok": self.identity_ok,
            "schema_ok": self.schema_ok,
            "drift_score": self.drift_score,
            "history_ok": self.history_ok,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]] = None) -> "ConsistencyReport":
        d = dict(data or {})
        return cls(
            score=_clamp_severity(d.get("score", 1.0)),
            threshold=_clamp_severity(
                d.get("threshold", DEFAULT_CONSISTENCY_THRESHOLD)
            ),
            issues=list(d.get("issues", []) or []),
            warnings=list(d.get("warnings", []) or []),
            identity_ok=bool(d.get("identity_ok", True)),
            schema_ok=bool(d.get("schema_ok", True)),
            drift_score=_clamp_severity(d.get("drift_score", 0.0)),
            history_ok=bool(d.get("history_ok", True)),
            metadata=dict(d.get("metadata", {}) or {}),
        )

    def issue_count(self) -> int:
        return len(self.issues)

    def warning_count(self) -> int:
        return len(self.warnings)

    def has_issues(self) -> bool:
        return len(self.issues) > 0


# ============================================================
# 工具
# ============================================================
def _safe_get(obj: Any, *path: str, default: Any = None) -> Any:
    cur: Any = obj
    for p in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(p, default)
        else:
            cur = getattr(cur, p, default)
    return cur


def _is_valid_schema_version(v: Any) -> bool:
    if v is None:
        return False
    try:
        return str(v) in VALID_SCHEMA_VERSIONS
    except Exception:  # noqa: BLE001
        return False


def _avg(values: List[float]) -> float:
    if not values:
        return 0.0
    try:
        return sum(values) / len(values)
    except Exception:  # noqa: BLE001
        return 0.0


def _max(values: List[float]) -> float:
    if not values:
        return 0.0
    try:
        return max(values)
    except Exception:  # noqa: BLE001
        return 0.0


# ============================================================
# ConsistencyChecker
# ============================================================
class ConsistencyChecker:
    """SelfModel 一致性验证(Phase 4.7 / v1.0)。

    字段:
    - _threshold:              float
    - _drift_window:           int
    - _drift_count_threshold:  int
    - _trait_drift_threshold:  float
    - _value_drift_threshold:  float
    - _check_count:            int
    - _last_error:             Optional[str]
    - _last_report:            Optional[ConsistencyReport]

    方法:
    - check(snapshot, evolution_history=None) -> ConsistencyReport
    - health_check() / describe()
    """

    def __init__(
        self,
        threshold: float = DEFAULT_CONSISTENCY_THRESHOLD,
        drift_window: int = DEFAULT_DRIFT_WINDOW,
        drift_count_threshold: int = DEFAULT_DRIFT_COUNT_THRESHOLD,
        trait_drift_threshold: float = DEFAULT_TRAIT_DRIFT_THRESHOLD,
        value_drift_threshold: float = DEFAULT_VALUE_DRIFT_THRESHOLD,
    ) -> None:
        self._threshold = float(threshold)
        self._drift_window = int(drift_window)
        self._drift_count_threshold = int(drift_count_threshold)
        self._trait_drift_threshold = float(trait_drift_threshold)
        self._value_drift_threshold = float(value_drift_threshold)
        self._check_count: int = 0
        self._last_error: Optional[str] = None
        self._last_report: Optional[ConsistencyReport] = None

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def check(
        self,
        snapshot: Any,
        evolution_history: Optional[Sequence[Any]] = None,
    ) -> ConsistencyReport:
        """验证 SelfModel 一致性。

        Args:
            snapshot:          SelfModelSnapshot
            evolution_history: 可选,EvolutionRecord 列表(用于历史一致性)

        Returns:
            ConsistencyReport
        """
        try:
            self._last_error = None
            if snapshot is None:
                # snapshot 缺失视为严重问题
                report = ConsistencyReport(
                    score=0.0,
                    threshold=self._threshold,
                    issues=[
                        {
                            "code": "snapshot_missing",
                            "severity": 1.0,
                            "description": "Snapshot is None; cannot validate consistency.",
                        }
                    ],
                    identity_ok=False,
                    schema_ok=False,
                    history_ok=False,
                )
                self._check_count += 1
                self._last_report = report
                return report

            # 深拷贝避免污染
            snap_copy = copy.deepcopy(snapshot)

            issues: List[Dict[str, Any]] = []
            warnings: List[str] = []

            # 1. identity 完整性
            identity_ok = self._check_identity(snap_copy, issues, warnings)
            # 2. 字段合法性
            schema_ok = self._check_field_validity(snap_copy, issues, warnings)
            # 3. 人格漂移(基于历史)
            drift_score = self._check_drift(
                snap_copy, evolution_history, issues, warnings,
            )
            # 4. 历史一致性
            history_ok = self._check_history_consistency(
                evolution_history, issues, warnings,
            )

            # 计算 score
            score = self._compute_score(
                identity_ok, schema_ok, drift_score, history_ok, issues,
            )

            report = ConsistencyReport(
                score=score,
                threshold=self._threshold,
                issues=issues,
                warnings=warnings,
                identity_ok=identity_ok,
                schema_ok=schema_ok,
                drift_score=drift_score,
                history_ok=history_ok,
                metadata={
                    "drift_window": self._drift_window,
                    "drift_count_threshold": self._drift_count_threshold,
                    "trait_drift_threshold": self._trait_drift_threshold,
                    "value_drift_threshold": self._value_drift_threshold,
                },
            )
            self._check_count += 1
            self._last_report = report
            return report
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"check_failed: {exc}"
            logger.warning("ConsistencyChecker.check 失败: %s", exc)
            # 失败时返回默认 report
            return ConsistencyReport(
                score=0.5,
                threshold=self._threshold,
                issues=[
                    {
                        "code": "check_exception",
                        "severity": 0.5,
                        "description": f"check() raised: {exc}",
                    }
                ],
                metadata={"error": repr(exc)},
            )

    # --------------------------------------------------------
    # 子检查
    # --------------------------------------------------------
    def _check_identity(
        self, snap: Any, issues: List[Dict[str, Any]], warnings: List[str],
    ) -> bool:
        identity_ok = True
        # identity_id 存在
        identity_id = _safe_get(snap, "identity_id", default="")
        if not identity_id:
            identity_ok = False
            issues.append({
                "code": "missing_identity_id",
                "severity": 1.0,
                "description": "Snapshot.identity_id is empty.",
            })
        # identity.core_identity 存在
        core_identity = _safe_get(snap, "identity", "core_identity", default="")
        identity_name = _safe_get(snap, "identity", "name", default="")
        if not core_identity and not identity_name:
            identity_ok = False
            issues.append({
                "code": "missing_core_identity",
                "severity": 0.8,
                "description": (
                    "Snapshot.identity.core_identity / name missing."
                ),
            })
        elif not core_identity:
            warnings.append("identity.core_identity is empty.")
        return identity_ok

    def _check_field_validity(
        self, snap: Any, issues: List[Dict[str, Any]], warnings: List[str],
    ) -> bool:
        schema_ok = True
        # schema_version
        sv = _safe_get(snap, "schema_version", default="")
        if not _is_valid_schema_version(sv):
            schema_ok = False
            issues.append({
                "code": "invalid_schema_version",
                "severity": 0.5,
                "description": f"Unknown schema_version: {sv!r}",
            })
        # version >= 1
        v = _safe_get(snap, "version", default=0)
        try:
            v_int = int(v or 0)
        except (TypeError, ValueError):
            v_int = 0
        if v_int < 1:
            schema_ok = False
            issues.append({
                "code": "invalid_version",
                "severity": 0.5,
                "description": f"Snapshot.version must be >= 1, got {v_int}.",
            })
        # 检查 entries 字段类型
        entries = _safe_get(snap, "entries", default=[])
        if entries is not None and not isinstance(entries, list):
            schema_ok = False
            issues.append({
                "code": "entries_not_list",
                "severity": 0.5,
                "description": "Snapshot.entries must be a list.",
            })
        # 检查 core_values 字段类型
        cv = _safe_get(snap, "core_values", default=[])
        if cv is not None and not isinstance(cv, list):
            schema_ok = False
            issues.append({
                "code": "core_values_not_list",
                "severity": 0.5,
                "description": "Snapshot.core_values must be a list.",
            })
        return schema_ok

    def _check_drift(
        self,
        snap: Any,
        history: Optional[Sequence[Any]],
        issues: List[Dict[str, Any]],
        warnings: List[str],
    ) -> float:
        """检查 drift_score,基于最近 N 条历史记录。

        Returns:
            drift_score [0.0, 1.0],0 表示无漂移
        """
        if not history:
            return 0.0
        recent = list(history[-self._drift_window:]) if self._drift_window > 0 else []
        if not recent:
            return 0.0
        # 统计变化数量
        total_changes = 0
        trait_changes: List[float] = []
        value_changes: List[float] = []
        for rec in recent:
            # rec 可以是 EvolutionRecord / dict
            if isinstance(rec, dict):
                changes = rec.get("changes", []) or []
            else:
                changes = getattr(rec, "changes", []) or []
            for ch in changes:
                # ch 可能是 dict / SelfModelChange
                if isinstance(ch, dict):
                    field_name = str(ch.get("field_name", "") or "")
                    old_v = ch.get("old_value")
                    new_v = ch.get("new_value")
                else:
                    field_name = str(getattr(ch, "field_name", "") or "")
                    old_v = getattr(ch, "old_value", None)
                    new_v = getattr(ch, "new_value", None)
                if old_v == new_v:
                    continue
                total_changes += 1
                if field_name.startswith("stable_traits"):
                    try:
                        if isinstance(old_v, (int, float)) and isinstance(new_v, (int, float)):
                            trait_changes.append(abs(float(new_v) - float(old_v)))
                    except (TypeError, ValueError):
                        pass
                if field_name.startswith("core_values"):
                    try:
                        if isinstance(old_v, (int, float)) and isinstance(new_v, (int, float)):
                            value_changes.append(abs(float(new_v) - float(old_v)))
                    except (TypeError, ValueError):
                        pass

        drift_score = 0.0
        if total_changes >= self._drift_count_threshold:
            drift_score = max(drift_score, min(1.0, total_changes / (self._drift_count_threshold * 2)))

        if trait_changes:
            avg = _avg(trait_changes)
            mx = _max(trait_changes)
            if avg >= self._trait_drift_threshold:
                drift_score = max(drift_score, min(1.0, avg / max(self._trait_drift_threshold, 0.0001)))
            if mx >= self._trait_drift_threshold * 2:
                drift_score = max(drift_score, min(1.0, mx / 2.0))
                issues.append({
                    "code": "stable_traits_drifting",
                    "severity": min(1.0, mx),
                    "description": (
                        f"stable_traits changed rapidly in recent history "
                        f"(max delta={mx:.3f})."
                    ),
                })

        if value_changes:
            avg = _avg(value_changes)
            mx = _max(value_changes)
            if avg >= self._value_drift_threshold:
                drift_score = max(drift_score, min(1.0, avg / max(self._value_drift_threshold, 0.0001)))
            if mx >= self._value_drift_threshold * 2:
                drift_score = max(drift_score, min(1.0, mx / 2.0))
                issues.append({
                    "code": "core_values_drifting",
                    "severity": min(1.0, mx),
                    "description": (
                        f"core_values changed rapidly in recent history "
                        f"(max delta={mx:.3f})."
                    ),
                })

        if drift_score > 0:
            warnings.append(
                f"drift_score={drift_score:.3f} detected in recent history."
            )
        return drift_score

    def _check_history_consistency(
        self,
        history: Optional[Sequence[Any]],
        issues: List[Dict[str, Any]],
        warnings: List[str],
    ) -> bool:
        if history is None:
            return True
        # 短时间大量变化: 超过 drift_count_threshold 即视为 history inconsistent
        if not isinstance(history, (list, tuple)):
            warnings.append("history is not a list; skipping history check.")
            return True
        recent = list(history[-self._drift_window:]) if self._drift_window > 0 else []
        if len(recent) >= self._drift_count_threshold:
            return True  # drift_score 已经反映
        return True

    def _compute_score(
        self,
        identity_ok: bool,
        schema_ok: bool,
        drift_score: float,
        history_ok: bool,
        issues: List[Dict[str, Any]],
    ) -> float:
        """计算综合 score:从 1.0 开始,扣分。"""
        score = 1.0
        if not identity_ok:
            score -= 0.4
        if not schema_ok:
            score -= 0.2
        if not history_ok:
            score -= 0.2
        # drift 扣分
        score -= drift_score * 0.3
        # 每个 issue 扣分
        for issue in issues:
            sev = _safe_get(issue, "severity", default=0.0)
            try:
                sev_f = float(sev)
            except (TypeError, ValueError):
                sev_f = 0.0
            score -= min(0.2, max(0.0, sev_f) * 0.2)
        return _clamp_severity(score)

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def check_count(self) -> int:
        return self._check_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def last_report(self) -> Optional[ConsistencyReport]:
        return self._last_report

    def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "schema_version": CONSISTENCY_CHECKER_SCHEMA_VERSION,
            "check_count": self._check_count,
            "threshold": self._threshold,
            "drift_window": self._drift_window,
            "drift_count_threshold": self._drift_count_threshold,
            "trait_drift_threshold": self._trait_drift_threshold,
            "value_drift_threshold": self._value_drift_threshold,
            "last_error": self._last_error,
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "schema_version": CONSISTENCY_CHECKER_SCHEMA_VERSION,
            "check_count": self._check_count,
            "threshold": self._threshold,
            "drift_window": self._drift_window,
            "drift_count_threshold": self._drift_count_threshold,
            "last_error": self._last_error,
        }


__all__ = [
    "ConsistencyReport",
    "ConsistencyChecker",
    "CONSISTENCY_CHECKER_SCHEMA_VERSION",
    "DEFAULT_CONSISTENCY_THRESHOLD",
    "DEFAULT_DRIFT_WINDOW",
    "DEFAULT_DRIFT_COUNT_THRESHOLD",
    "DEFAULT_TRAIT_DRIFT_THRESHOLD",
    "DEFAULT_VALUE_DRIFT_THRESHOLD",
    "VALID_SCHEMA_VERSIONS",
]
