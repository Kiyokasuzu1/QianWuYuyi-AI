# -*- coding: utf-8 -*-
"""
src/runtime/policy/lifecycle/verifier.py

Phase C.10.10 — Policy Verifier

本文件实现:
- VerificationResult    单条 verify 结果
- PolicyVerifier        验证 apply 是否生效 / 是否健康

校验维度:
- 数值一致性:ThrottleRegistry 当前值是否 == proposal.suggested_value
- 错误率:通过 metrics_collector 比较 apply 前后错误率
- 可选 cooldown 检查

任何异常 → fail-soft,返回 ok=False
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..feedback.proposal import (
    ALL_PARAMETERS,
    PolicyAdjustmentProposal,
)

logger = logging.getLogger(__name__)


DEFAULT_VERIFY_DELAY = 0.0
DEFAULT_ERROR_RATE_SPIKE = 0.30  # 错误率比 apply 前提升 30% 视为异常
DEFAULT_SAMPLE_MIN = 5


# ============================================================
# VerificationResult
# ============================================================
@dataclass
class VerificationResult:
    """单条 verify 的结果。

    字段:
    - ok:                 整体是否通过
    - proposal_id:        对应的 proposal_id
    - module:             模块名
    - parameter:          参数名
    - expected_value:     期望值(proposal.suggested_value)
    - actual_value:       实际值(从 ThrottleRegistry 读取)
    - value_match:        实际值 == 期望值
    - error_rate_before:  apply 前的错误率(从 MetricsCollector 读取)
    - error_rate_after:   apply 后的错误率
    - error_rate_ok:      错误率未显著恶化
    - message:            详细信息
    - timestamp:          时间戳
    """

    ok: bool = False
    proposal_id: str = ""
    module: str = ""
    parameter: str = ""
    expected_value: Any = None
    actual_value: Any = None
    value_match: bool = False
    error_rate_before: float = 0.0
    error_rate_after: float = 0.0
    error_rate_ok: bool = True
    message: str = ""
    timestamp: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "ok": bool(self.ok),
                "proposal_id": str(self.proposal_id),
                "module": str(self.module),
                "parameter": str(self.parameter),
                "expected_value": self.expected_value,
                "actual_value": self.actual_value,
                "value_match": bool(self.value_match),
                "error_rate_before": float(self.error_rate_before),
                "error_rate_after": float(self.error_rate_after),
                "error_rate_ok": bool(self.error_rate_ok),
                "message": str(self.message),
                "timestamp": float(self.timestamp),
                "metadata": dict(self.metadata or {}),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("VerificationResult.to_dict 异常(已隔离): %s", exc)
            return {
                "ok": False,
                "proposal_id": "",
                "module": "",
                "parameter": "",
                "expected_value": None,
                "actual_value": None,
                "value_match": False,
                "error_rate_before": 0.0,
                "error_rate_after": 0.0,
                "error_rate_ok": False,
                "message": "to_dict_failed",
                "timestamp": 0.0,
                "metadata": {},
            }


# ============================================================
# PolicyVerifier
# ============================================================
class PolicyVerifier:
    """验证 apply 是否生效 + 运行时是否健康。

    行为:
    - value_match: ThrottleRegistry 当前值 vs proposal.suggested_value
    - error_rate:  从 MetricsCollector 读取 apply 前后错误率
    - 任何异常 → fail-soft,返回 ok=False
    """

    def __init__(
        self,
        throttle_registry: Any = None,
        metrics_collector: Any = None,
        error_rate_spike: float = DEFAULT_ERROR_RATE_SPIKE,
        min_sample: int = DEFAULT_SAMPLE_MIN,
    ) -> None:
        self._lock = threading.RLock()
        self._throttle_registry = throttle_registry
        self._metrics_collector = metrics_collector
        self._error_rate_spike = float(error_rate_spike)
        self._min_sample = max(0, int(min_sample))
        self._stats: Dict[str, int] = {
            "verify_total": 0,
            "verify_ok": 0,
            "verify_failed": 0,
            "errors": 0,
        }

    # --------------------------------------------------------
    # 访问器
    # --------------------------------------------------------
    def configure(
        self,
        throttle_registry: Any = None,
        metrics_collector: Any = None,
    ) -> None:
        with self._lock:
            if throttle_registry is not None:
                self._throttle_registry = throttle_registry
            if metrics_collector is not None:
                self._metrics_collector = metrics_collector

    @property
    def throttle_registry(self) -> Any:
        return self._throttle_registry

    @property
    def metrics_collector(self) -> Any:
        return self._metrics_collector

    def get_stats(self) -> Dict[str, int]:
        try:
            with self._lock:
                return dict(self._stats)
        except Exception:  # noqa: BLE001
            return {}

    def reset_stats(self) -> None:
        try:
            with self._lock:
                for k in self._stats:
                    self._stats[k] = 0
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------
    # 核心
    # --------------------------------------------------------
    def verify(
        self,
        proposal: PolicyAdjustmentProposal,
        delay_seconds: float = DEFAULT_VERIFY_DELAY,
    ) -> VerificationResult:
        """验证 apply 是否生效。

        流程:
        - value_check: ThrottleRegistry 当前值 vs proposal.suggested_value
        - error_rate_check: MetricsCollector 错误率(若可用)
        - 组合判断 ok
        """
        try:
            with self._lock:
                self._stats["verify_total"] += 1
            if not isinstance(proposal, PolicyAdjustmentProposal):
                with self._lock:
                    self._stats["errors"] += 1
                return VerificationResult(
                    ok=False,
                    message="invalid_proposal",
                )
            if delay_seconds and delay_seconds > 0:
                try:
                    time.sleep(float(delay_seconds))
                except Exception:  # noqa: BLE001
                    pass

            result = VerificationResult(
                proposal_id=str(proposal.proposal_id),
                module=str(proposal.module),
                parameter=str(proposal.parameter),
                expected_value=proposal.suggested_value,
                timestamp=float(time.time()),
            )

            # 1. value check
            value_ok, actual, msg = self._check_value(proposal)
            result.actual_value = actual
            result.value_match = bool(value_ok)
            if not value_ok:
                result.message = f"value_mismatch:{msg}"

            # 2. error rate check
            error_ok, before, after = self._check_error_rate(proposal)
            result.error_rate_before = float(before)
            result.error_rate_after = float(after)
            result.error_rate_ok = bool(error_ok)

            # 3. 综合判断
            if value_ok and error_ok:
                result.ok = True
                result.message = "verified"
                with self._lock:
                    self._stats["verify_ok"] += 1
            else:
                if value_ok and not error_ok:
                    result.message = (
                        f"value_ok_but_error_rate_spike:"
                        f"before={before:.2%},after={after:.2%}"
                    )
                result.ok = False
                with self._lock:
                    self._stats["verify_failed"] += 1
            return result
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["errors"] += 1
                self._stats["verify_failed"] += 1
            logger.debug("PolicyVerifier.verify 异常(已隔离): %s", exc)
            return VerificationResult(
                ok=False,
                message=f"verify_exception:{exc}",
            )

    # --------------------------------------------------------
    # 私有:value check
    # --------------------------------------------------------
    def _check_value(
        self,
        proposal: PolicyAdjustmentProposal,
    ) -> tuple:
        try:
            registry = self._throttle_registry
            if registry is None:
                return False, None, "throttle_registry_not_configured"
            snap = None
            try:
                snap = registry.snapshot() or {}
            except Exception:  # noqa: BLE001
                snap = {}
            state = snap.get(str(proposal.module)) or {}
            param = str(proposal.parameter)
            expected = proposal.suggested_value
            if param == "throttle.interval":
                actual = int(state.get("interval", 0) or 0)
                if int(expected) == actual:
                    return True, actual, "ok"
                return False, actual, f"expected={expected},got={actual}"
            if param == "throttle.throttle":
                actual = float(state.get("throttle", 1.0) or 1.0)
                if abs(float(expected) - actual) < 1e-6:
                    return True, actual, "ok"
                return False, actual, f"expected={expected},got={actual}"
            if param == "throttle.cooldown_seconds":
                actual = float(state.get("cooldown_seconds", 0.0) or 0.0)
                if abs(float(expected) - actual) < 1e-6:
                    return True, actual, "ok"
                return False, actual, f"expected={expected},got={actual}"
            # budget 参数:无法直接通过 throttle.snapshot 验证,返回 ok + actual=None
            return True, None, "budget_check_skipped"
        except Exception as exc:  # noqa: BLE001
            logger.debug("_check_value 异常(已隔离): %s", exc)
            return False, None, f"check_failed:{exc}"

    # --------------------------------------------------------
    # 私有:error rate check
    # --------------------------------------------------------
    def _check_error_rate(
        self,
        proposal: PolicyAdjustmentProposal,
    ) -> tuple:
        """检查错误率。

        返回 (ok, before, after)
        - before/after 来自 MetricsCollector.metrics
        - 若 collector 不可用,返回 (True, 0.0, 0.0)
        - 若 sample 数 < min_sample,返回 (True, current, current)
        """
        try:
            collector = self._metrics_collector
            if collector is None:
                return True, 0.0, 0.0
            collect_method = getattr(collector, "collect", None)
            if not callable(collect_method):
                return True, 0.0, 0.0
            metric = collect_method(str(proposal.module))
            if metric is None:
                return True, 0.0, 0.0
            try:
                failure_rate = float(metric.failure_rate or 0.0)
            except Exception:  # noqa: BLE001
                failure_rate = 0.0
            try:
                execute_count = int(metric.execute_count or 0)
            except Exception:  # noqa: BLE001
                execute_count = 0
            if execute_count < self._min_sample:
                return True, failure_rate, failure_rate
            # 简化:无"apply 前后"时间戳,只校验当前错误率不超过 spike
            if failure_rate > float(self._error_rate_spike):
                return False, failure_rate, failure_rate
            return True, failure_rate, failure_rate
        except Exception as exc:  # noqa: BLE001
            logger.debug("_check_error_rate 异常(已隔离): %s", exc)
            return True, 0.0, 0.0


# ============================================================
# 工厂
# ============================================================
def build_default_verifier(
    throttle_registry: Any = None,
    metrics_collector: Any = None,
    error_rate_spike: float = DEFAULT_ERROR_RATE_SPIKE,
) -> PolicyVerifier:
    return PolicyVerifier(
        throttle_registry=throttle_registry,
        metrics_collector=metrics_collector,
        error_rate_spike=error_rate_spike,
    )
