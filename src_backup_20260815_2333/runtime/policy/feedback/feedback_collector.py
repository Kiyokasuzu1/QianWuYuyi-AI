# -*- coding: utf-8 -*-
"""
src/runtime/policy/feedback/feedback_collector.py

Phase C.10.9 — Policy Metrics Collector

本文件实现 PolicyMetrics 收集器:

- PolicyMetrics
    单模块某窗口的指标快照(append-only dataclass):
    - module:               模块名
    - execute_count:        执行次数
    - success_count:        成功次数
    - failure_count:        失败次数
    - throttle_hit_count:   被 throttle 拒绝次数
    - budget_reject_count:  被 budget 拒绝次数
    - total_latency_ms:     累计延迟(毫秒)
    - avg_latency_ms:       平均延迟
    - total_tokens:         累计 token 消耗
    - total_cost:           累计成本
    - avg_cost:             平均成本
    - llm_call_count:       LLM 调用次数
    - window_started_at:    窗口开始时间
    - last_updated_at:      最近更新时间
    - cycle_id:             最近一次 cycle

- MetricsCollector
    集中管理所有模块的 metrics:
    - record_execution(...): 记录一次执行
    - record_throttle_hit(...): 记录一次 throttle 拒绝
    - record_budget_reject(...): 记录一次 budget 拒绝
    - collect(module) → PolicyMetrics
    - collect_all() → Dict[module, PolicyMetrics]
    - reset(module) / reset_all(): 测试用

设计原则:
- 完全 append-only(写入不修改已有数据,只累加)
- 线程安全(RLock)
- 任何异常 → fail-soft,不影响 Runtime
- 不与 ThrottleRegistry / RuntimeBudget 状态耦合
  (collector 独立记录;二者数据可对照但独立)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# PolicyMetrics
# ============================================================
@dataclass
class PolicyMetrics:
    """单模块某窗口的指标快照。

    字段:
    - module:               模块名
    - execute_count:        执行次数(allow + readonly)
    - success_count:        成功次数(执行无异常)
    - failure_count:        失败次数(执行抛错)
    - throttle_hit_count:   被 ThrottleRule 拒绝次数
    - budget_reject_count:  被 BudgetRule 拒绝次数
    - other_deny_count:     被其他规则拒绝次数
    - total_latency_ms:     累计延迟(毫秒)
    - avg_latency_ms:       平均延迟(派生)
    - total_tokens:         累计 token 消耗
    - total_cost:           累计成本
    - avg_cost:             平均成本(派生)
    - llm_call_count:       LLM 调用次数
    - window_started_at:    窗口开始时间
    - last_updated_at:      最近更新时间
    - cycle_id:             最近一次 cycle
    """

    module: str = ""
    execute_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    throttle_hit_count: int = 0
    budget_reject_count: int = 0
    other_deny_count: int = 0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    total_tokens: int = 0
    total_cost: float = 0.0
    avg_cost: float = 0.0
    llm_call_count: int = 0
    window_started_at: float = 0.0
    last_updated_at: float = 0.0
    cycle_id: str = ""

    def recompute(self) -> None:
        """重新计算 avg_* 派生字段。"""
        try:
            n = max(1, int(self.execute_count))
            self.avg_latency_ms = float(self.total_latency_ms or 0.0) / n
            self.avg_cost = float(self.total_cost or 0.0) / n
        except Exception:  # noqa: BLE001
            pass

    @property
    def success_rate(self) -> float:
        try:
            if self.execute_count <= 0:
                return 0.0
            return float(self.success_count) / float(self.execute_count)
        except Exception:  # noqa: BLE001
            return 0.0

    @property
    def failure_rate(self) -> float:
        try:
            if self.execute_count <= 0:
                return 0.0
            return float(self.failure_count) / float(self.execute_count)
        except Exception:  # noqa: BLE001
            return 0.0

    @property
    def deny_count(self) -> int:
        try:
            return (
                int(self.throttle_hit_count)
                + int(self.budget_reject_count)
                + int(self.other_deny_count)
            )
        except Exception:  # noqa: BLE001
            return 0

    @property
    def deny_rate(self) -> float:
        try:
            if self.execute_count <= 0:
                return 0.0
            # 评估总数 = 成功 + 失败 + 拒绝(被 throttle/budget 拒绝不会增加 execute_count,
            # 所以此 rate 仅以 execute_count 为分母)
            return float(self.deny_count) / float(self.execute_count)
        except Exception:  # noqa: BLE001
            return 0.0

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "module": str(self.module or ""),
                "execute_count": int(self.execute_count),
                "success_count": int(self.success_count),
                "failure_count": int(self.failure_count),
                "throttle_hit_count": int(self.throttle_hit_count),
                "budget_reject_count": int(self.budget_reject_count),
                "other_deny_count": int(self.other_deny_count),
                "deny_count": int(self.deny_count),
                "total_latency_ms": float(self.total_latency_ms or 0.0),
                "avg_latency_ms": float(self.avg_latency_ms or 0.0),
                "total_tokens": int(self.total_tokens or 0),
                "total_cost": float(self.total_cost or 0.0),
                "avg_cost": float(self.avg_cost or 0.0),
                "llm_call_count": int(self.llm_call_count or 0),
                "success_rate": float(self.success_rate or 0.0),
                "failure_rate": float(self.failure_rate or 0.0),
                "deny_rate": float(self.deny_rate or 0.0),
                "window_started_at": float(self.window_started_at or 0.0),
                "last_updated_at": float(self.last_updated_at or 0.0),
                "cycle_id": str(self.cycle_id or ""),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyMetrics.to_dict 异常(已隔离): %s", exc)
            return {
                "module": str(self.module or ""),
                "execute_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "throttle_hit_count": 0,
                "budget_reject_count": 0,
                "other_deny_count": 0,
                "deny_count": 0,
                "total_latency_ms": 0.0,
                "avg_latency_ms": 0.0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "avg_cost": 0.0,
                "llm_call_count": 0,
                "success_rate": 0.0,
                "failure_rate": 0.0,
                "deny_rate": 0.0,
                "window_started_at": 0.0,
                "last_updated_at": 0.0,
                "cycle_id": "",
            }


# ============================================================
# MetricsCollector
# ============================================================
class MetricsCollector:
    """集中管理所有模块的 PolicyMetrics。

    特性:
    - 线程安全(RLock)
    - append-only:任何 record_* 都不修改已有数据,只累加计数
    - 窗口:每个模块独立窗口,首次 record 时启动
    - 异常隔离
    """

    def __init__(self, auto_register: bool = True) -> None:
        self._lock = threading.RLock()
        self._metrics: Dict[str, PolicyMetrics] = {}
        self._auto_register = bool(auto_register)

    # --------------------------------------------------------
    # 模块注册
    # --------------------------------------------------------
    def _ensure(self, module: str) -> Optional[PolicyMetrics]:
        try:
            module = str(module or "").strip().lower()
            if not module:
                return None
            with self._lock:
                m = self._metrics.get(module)
                if m is None:
                    if not self._auto_register:
                        return None
                    m = PolicyMetrics(
                        module=module,
                        window_started_at=time.time(),
                    )
                    self._metrics[module] = m
                return m
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector._ensure 异常(已隔离): %s", exc)
            return None

    def has_module(self, module: str) -> bool:
        try:
            with self._lock:
                return str(module or "").strip().lower() in self._metrics
        except Exception:  # noqa: BLE001
            return False

    def all_modules(self) -> List[str]:
        try:
            with self._lock:
                return [str(m) for m in self._metrics.keys()]
        except Exception:  # noqa: BLE001
            return []

    # --------------------------------------------------------
    # 记录接口
    # --------------------------------------------------------
    def record_execution(
        self,
        module: str,
        success: bool = True,
        latency_ms: float = 0.0,
        tokens: int = 0,
        cost: float = 0.0,
        llm_calls: int = 0,
        cycle_id: str = "",
        now: Optional[float] = None,
    ) -> None:
        """记录一次模块执行(成功或失败)。"""
        try:
            m = self._ensure(module)
            if m is None:
                return
            with self._lock:
                m.execute_count += 1
                if success:
                    m.success_count += 1
                else:
                    m.failure_count += 1
                m.total_latency_ms = float(m.total_latency_ms or 0.0) + max(0.0, float(latency_ms or 0.0))
                m.total_tokens = int(m.total_tokens or 0) + max(0, int(tokens or 0))
                m.total_cost = float(m.total_cost or 0.0) + max(0.0, float(cost or 0.0))
                m.llm_call_count = int(m.llm_call_count or 0) + max(0, int(llm_calls or 0))
                m.last_updated_at = float(now if now is not None else time.time())
                if cycle_id:
                    m.cycle_id = str(cycle_id)
                m.recompute()
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.record_execution 异常(已隔离): %s", exc)

    def record_throttle_hit(
        self,
        module: str,
        cycle_id: str = "",
        now: Optional[float] = None,
    ) -> None:
        """记录一次 throttle 拒绝。"""
        try:
            m = self._ensure(module)
            if m is None:
                return
            with self._lock:
                m.throttle_hit_count += 1
                m.last_updated_at = float(now if now is not None else time.time())
                if cycle_id:
                    m.cycle_id = str(cycle_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.record_throttle_hit 异常(已隔离): %s", exc)

    def record_budget_reject(
        self,
        module: str,
        cycle_id: str = "",
        now: Optional[float] = None,
    ) -> None:
        """记录一次 budget 拒绝。"""
        try:
            m = self._ensure(module)
            if m is None:
                return
            with self._lock:
                m.budget_reject_count += 1
                m.last_updated_at = float(now if now is not None else time.time())
                if cycle_id:
                    m.cycle_id = str(cycle_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.record_budget_reject 异常(已隔离): %s", exc)

    def record_other_deny(
        self,
        module: str,
        cycle_id: str = "",
        now: Optional[float] = None,
    ) -> None:
        """记录一次其他原因(非 throttle / 非 budget)的拒绝。"""
        try:
            m = self._ensure(module)
            if m is None:
                return
            with self._lock:
                m.other_deny_count += 1
                m.last_updated_at = float(now if now is not None else time.time())
                if cycle_id:
                    m.cycle_id = str(cycle_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.record_other_deny 异常(已隔离): %s", exc)

    # --------------------------------------------------------
    # 收集接口
    # --------------------------------------------------------
    def collect(self, module: str) -> Optional[PolicyMetrics]:
        """读取某模块的 metrics 副本。"""
        try:
            with self._lock:
                m = self._metrics.get(str(module or "").strip().lower())
                if m is None:
                    return None
                # 返回一个浅拷贝,避免外部修改
                return PolicyMetrics(
                    module=m.module,
                    execute_count=m.execute_count,
                    success_count=m.success_count,
                    failure_count=m.failure_count,
                    throttle_hit_count=m.throttle_hit_count,
                    budget_reject_count=m.budget_reject_count,
                    other_deny_count=m.other_deny_count,
                    total_latency_ms=m.total_latency_ms,
                    avg_latency_ms=m.avg_latency_ms,
                    total_tokens=m.total_tokens,
                    total_cost=m.total_cost,
                    avg_cost=m.avg_cost,
                    llm_call_count=m.llm_call_count,
                    window_started_at=m.window_started_at,
                    last_updated_at=m.last_updated_at,
                    cycle_id=m.cycle_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.collect 异常(已隔离): %s", exc)
            return None

    def collect_all(self) -> Dict[str, PolicyMetrics]:
        """读取所有模块的 metrics 副本。"""
        try:
            with self._lock:
                return {
                    m: self.collect(m)  # type: ignore
                    for m in self._metrics.keys()
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.collect_all 异常(已隔离): %s", exc)
            return {}

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """读取所有模块的 metrics to_dict 形式。"""
        try:
            all_m = self.collect_all()
            return {m: metrics.to_dict() for m, metrics in all_m.items() if metrics is not None}
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.snapshot 异常(已隔离): %s", exc)
            return {}

    def stats(self) -> Dict[str, int]:
        try:
            with self._lock:
                total_exec = 0
                total_throttle = 0
                total_budget = 0
                total_other = 0
                for m in self._metrics.values():
                    total_exec += int(m.execute_count)
                    total_throttle += int(m.throttle_hit_count)
                    total_budget += int(m.budget_reject_count)
                    total_other += int(m.other_deny_count)
                return {
                    "modules": len(self._metrics),
                    "total_executions": total_exec,
                    "total_throttle_hits": total_throttle,
                    "total_budget_rejects": total_budget,
                    "total_other_denies": total_other,
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.stats 异常(已隔离): %s", exc)
            return {}

    # --------------------------------------------------------
    # 测试用
    # --------------------------------------------------------
    def reset(self, module: Optional[str] = None) -> None:
        """重置某个模块(或全部)的 metrics 窗口。"""
        try:
            with self._lock:
                if module is None:
                    self._metrics.clear()
                else:
                    self._metrics.pop(str(module or "").strip().lower(), None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MetricsCollector.reset 异常(已隔离): %s", exc)


# ============================================================
# 工厂
# ============================================================
def build_metrics_collector(auto_register: bool = True) -> MetricsCollector:
    """构造一个 MetricsCollector(默认自动注册新模块)。"""
    return MetricsCollector(auto_register=auto_register)


def build_empty_metrics(module: str) -> PolicyMetrics:
    """构造一个空的 PolicyMetrics(用于默认值/测试)。"""
    return PolicyMetrics(
        module=str(module or "").strip().lower(),
        window_started_at=time.time(),
        last_updated_at=time.time(),
    )
