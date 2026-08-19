# -*- coding: utf-8 -*-
"""
src/runtime/policy/feedback/feedback_engine.py

Phase C.10.9 — Policy Feedback Engine (统一入口)

本文件实现 PolicyFeedbackEngine,作为整个 Feedback 子系统的统一入口:

- PolicyFeedbackEngine
    - collect_metrics()         收集当前 metrics 快照
    - generate_proposals()      基于 metrics + policy snapshot 生成 proposals
    - evaluate()                完整闭环:collect → evaluate → store.append
    - snapshot()                返回 collector / evaluator / store 的整体状态
    - record_execution()        直接记录一次执行(供 Runtime 调用)
    - record_throttle_hit()     记录 throttle 拒绝
    - record_budget_reject()    记录 budget 拒绝
    - record_other_deny()       记录其他原因拒绝

设计原则:
- 只生成 proposal,不修改任何 Policy 配置
- 异常隔离:任何内部异常 → fail-soft,默认返回空值/True
- 不与 ThrottleRegistry / RuntimeBudget 状态耦合(只读)
- 事件发布:可选 publish_event,失败时静默
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from .adaptive_evaluator import (
    AdaptiveEvaluator,
    EvaluatorConfig,
    PolicySnapshot,
    build_evaluator,
    capture_policy_snapshot,
)
from .feedback_collector import (
    MetricsCollector,
    PolicyMetrics,
    build_metrics_collector,
)
from .proposal import (
    PolicyAdjustmentProposal,
    ProposalStore,
    build_proposal,
)

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================
DEFAULT_MAX_PROPOSALS = 10000
DEFAULT_PUBLISH_EVENTS = True


# ============================================================
# PolicyFeedbackEngine
# ============================================================
class PolicyFeedbackEngine:
    """Feedback 子系统统一入口。

    用法:
        engine = PolicyFeedbackEngine.build_default(
            throttle_registry=layer.throttle_registry,
            runtime_budget=layer.runtime_budget,
        )
        engine.record_execution("growth", success=True, latency_ms=120, cost=2.0)
        # ... 经过若干 cycle ...
        proposals = engine.evaluate(cycle_id="c-123")
        for p in proposals:
            send_to_audit(p)
    """

    def __init__(
        self,
        collector: Optional[MetricsCollector] = None,
        evaluator: Optional[AdaptiveEvaluator] = None,
        store: Optional[ProposalStore] = None,
        throttle_registry: Any = None,
        runtime_budget: Any = None,
        max_proposals: int = DEFAULT_MAX_PROPOSALS,
        publish_events: bool = DEFAULT_PUBLISH_EVENTS,
        event_publisher: Optional[Any] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._collector = collector if collector is not None else build_metrics_collector()
        self._evaluator = evaluator if evaluator is not None else build_evaluator()
        self._store = store if store is not None else ProposalStore(
            max_proposals=max_proposals,
        )
        self._throttle_registry = throttle_registry
        self._runtime_budget = runtime_budget
        self._publish_events = bool(publish_events)
        self._event_publisher = event_publisher  # 可选,函数签名 (event_type, event_data)
        # 引擎统计
        self._stats: Dict[str, int] = {
            "evaluate_total": 0,
            "evaluate_with_proposals": 0,
            "evaluate_empty": 0,
            "errors": 0,
        }

    # --------------------------------------------------------
    # 构造器
    # --------------------------------------------------------
    @classmethod
    def build_default(
        cls,
        throttle_registry: Any = None,
        runtime_budget: Any = None,
        config: Optional[EvaluatorConfig] = None,
        max_proposals: int = DEFAULT_MAX_PROPOSALS,
        publish_events: bool = DEFAULT_PUBLISH_EVENTS,
        event_publisher: Optional[Any] = None,
    ) -> "PolicyFeedbackEngine":
        """构造一个默认 Feedback Engine(自带 collector / evaluator / store)。"""
        collector = build_metrics_collector()
        evaluator = build_evaluator(config=config or EvaluatorConfig.default())
        store = ProposalStore(max_proposals=max_proposals)
        return cls(
            collector=collector,
            evaluator=evaluator,
            store=store,
            throttle_registry=throttle_registry,
            runtime_budget=runtime_budget,
            max_proposals=max_proposals,
            publish_events=publish_events,
            event_publisher=event_publisher,
        )

    # --------------------------------------------------------
    # 访问器
    # --------------------------------------------------------
    @property
    def collector(self) -> MetricsCollector:
        return self._collector

    @property
    def evaluator(self) -> AdaptiveEvaluator:
        return self._evaluator

    @property
    def store(self) -> ProposalStore:
        return self._store

    @property
    def throttle_registry(self) -> Any:
        return self._throttle_registry

    @property
    def runtime_budget(self) -> Any:
        return self._runtime_budget

    def get_stats(self) -> Dict[str, int]:
        try:
            with self._lock:
                base = dict(self._stats)
            base.update(self._evaluator.get_stats())
            return base
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyFeedbackEngine.get_stats 异常(已隔离): %s", exc)
            return {}

    # --------------------------------------------------------
    # 记录接口(供 Runtime 调用)
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
            self._collector.record_execution(
                module=str(module or ""),
                success=bool(success),
                latency_ms=float(latency_ms or 0.0),
                tokens=int(tokens or 0),
                cost=float(cost or 0.0),
                llm_calls=int(llm_calls or 0),
                cycle_id=str(cycle_id or ""),
                now=now,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("record_execution 异常(已隔离): %s", exc)

    def record_throttle_hit(
        self,
        module: str,
        cycle_id: str = "",
        now: Optional[float] = None,
    ) -> None:
        try:
            self._collector.record_throttle_hit(
                module=str(module or ""),
                cycle_id=str(cycle_id or ""),
                now=now,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("record_throttle_hit 异常(已隔离): %s", exc)

    def record_budget_reject(
        self,
        module: str,
        cycle_id: str = "",
        now: Optional[float] = None,
    ) -> None:
        try:
            self._collector.record_budget_reject(
                module=str(module or ""),
                cycle_id=str(cycle_id or ""),
                now=now,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("record_budget_reject 异常(已隔离): %s", exc)

    def record_other_deny(
        self,
        module: str,
        cycle_id: str = "",
        now: Optional[float] = None,
    ) -> None:
        try:
            self._collector.record_other_deny(
                module=str(module or ""),
                cycle_id=str(cycle_id or ""),
                now=now,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("record_other_deny 异常(已隔离): %s", exc)

    # --------------------------------------------------------
    # 核心:collect / generate / evaluate
    # --------------------------------------------------------
    def collect_metrics(self) -> Dict[str, PolicyMetrics]:
        """收集所有模块的 metrics 快照。"""
        try:
            return self._collector.collect_all()
        except Exception as exc:  # noqa: BLE001
            logger.debug("collect_metrics 异常(已隔离): %s", exc)
            return {}

    def _capture_policy_snapshot(self) -> PolicySnapshot:
        """捕获当前 Policy 状态(只读)。"""
        try:
            return capture_policy_snapshot(
                throttle_registry=self._throttle_registry,
                runtime_budget=self._runtime_budget,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("_capture_policy_snapshot 异常(已隔离): %s", exc)
            return PolicySnapshot()

    def generate_proposals(
        self,
        metrics: Optional[Dict[str, PolicyMetrics]] = None,
        policy_snapshot: Optional[PolicySnapshot] = None,
        cycle_id: str = "",
    ) -> List[PolicyAdjustmentProposal]:
        """生成 proposal 列表(不写入 store,只返回)。"""
        try:
            m = metrics if metrics is not None else self.collect_metrics()
            snap = policy_snapshot if policy_snapshot is not None else self._capture_policy_snapshot()
            return self._evaluator.evaluate(
                metrics=m,
                policy_snapshot=snap,
                cycle_id=str(cycle_id or ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("generate_proposals 异常(已隔离): %s", exc)
            return []

    def evaluate(
        self,
        cycle_id: str = "",
    ) -> List[PolicyAdjustmentProposal]:
        """完整闭环:collect → generate → store.append → publish。

        返回最终 append 到 store 的 proposal 列表。
        任何异常 → fail-soft,返回空列表。
        """
        try:
            with self._lock:
                self._stats["evaluate_total"] += 1
            metrics = self.collect_metrics()
            snap = self._capture_policy_snapshot()
            proposals = self._evaluator.evaluate(
                metrics=metrics,
                policy_snapshot=snap,
                cycle_id=str(cycle_id or ""),
            )
            # 写入 store
            if proposals:
                self._store.append_many(proposals)
                with self._lock:
                    self._stats["evaluate_with_proposals"] += 1
                # 发布事件(可选)
                if self._publish_events:
                    self._publish_feedback_events(
                        proposals=proposals,
                        cycle_id=str(cycle_id or ""),
                        metrics_snapshot=metrics,
                    )
            else:
                with self._lock:
                    self._stats["evaluate_empty"] += 1
            return proposals
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["errors"] += 1
            logger.debug("PolicyFeedbackEngine.evaluate 异常(已隔离): %s", exc)
            return []

    # --------------------------------------------------------
    # 事件发布
    # --------------------------------------------------------
    def _publish_feedback_events(
        self,
        proposals: List[PolicyAdjustmentProposal],
        cycle_id: str = "",
        metrics_snapshot: Optional[Dict[str, PolicyMetrics]] = None,
    ) -> None:
        """发布 RuntimePolicyFeedbackEvent(失败隔离)。"""
        try:
            # 优先使用 event_publisher
            publisher = self._event_publisher
            if publisher is None:
                try:
                    from src.events.bus import publish_event
                    publisher = publish_event  # type: ignore
                except Exception:  # noqa: BLE001
                    publisher = None
            if publisher is None:
                return
            try:
                from src.events.events import (
                    RuntimePolicyFeedbackEvent,
                    YuyiEvent,
                )
            except Exception:  # noqa: BLE001
                return
            for p in (proposals or []):
                try:
                    ev: Any = RuntimePolicyFeedbackEvent(
                        module=p.module,
                        metric=p.parameter,
                        value=str(p.suggested_value),
                        proposal=p,
                        confidence=float(p.confidence or 0.0),
                        cycle_id=str(cycle_id or ""),
                        old_value=p.old_value,
                        reason=str(p.reason or ""),
                        direction=str(p.direction or ""),
                        evidence=dict(p.evidence or {}),
                    )
                except Exception:
                    ev = YuyiEvent(
                        event_type="runtime.policy_feedback",
                        source="runtime",
                        data={
                            "module": p.module,
                            "metric": p.parameter,
                            "value": str(p.suggested_value),
                            "confidence": float(p.confidence or 0.0),
                            "cycle_id": str(cycle_id or ""),
                            "proposal": p.to_dict(),
                        },
                    )
                try:
                    publisher(ev)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("_publish_feedback_events 异常(已隔离): %s", exc)

    # --------------------------------------------------------
    # 状态/快照
    # --------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """返回 collector / evaluator / store 的整体快照。"""
        try:
            return {
                "metrics": self._collector.snapshot(),
                "metrics_stats": self._collector.stats(),
                "evaluator_stats": self._evaluator.get_stats(),
                "evaluator_config": self._evaluator.config.to_dict(),
                "proposal_store": self._store.snapshot(),
                "proposal_stats": self._store.stats(),
                "engine_stats": self.get_stats(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyFeedbackEngine.snapshot 异常(已隔离): %s", exc)
            return {
                "metrics": {},
                "metrics_stats": {},
                "evaluator_stats": {},
                "evaluator_config": {},
                "proposal_store": {"total": 0, "proposals": []},
                "proposal_stats": {},
                "engine_stats": {},
            }

    def health_check(self) -> Dict[str, Any]:
        try:
            return {
                "ok": True,
                "collector_ok": self._collector is not None,
                "evaluator_ok": self._evaluator is not None,
                "store_ok": self._store is not None,
                "throttle_linked": self._throttle_registry is not None,
                "budget_linked": self._runtime_budget is not None,
                "publish_events": self._publish_events,
                "stats": self.get_stats(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("health_check 异常(已隔离): %s", exc)
            return {"ok": False, "error": str(exc)}

    def reset_metrics(self) -> None:
        """测试用:重置 collector 的所有 metrics。"""
        try:
            self._collector.reset()
        except Exception as exc:  # noqa: BLE001
            logger.debug("reset_metrics 异常(已隔离): %s", exc)


# ============================================================
# 工厂
# ============================================================
def build_feedback_engine(
    throttle_registry: Any = None,
    runtime_budget: Any = None,
    config: Optional[EvaluatorConfig] = None,
    max_proposals: int = DEFAULT_MAX_PROPOSALS,
    publish_events: bool = DEFAULT_PUBLISH_EVENTS,
    event_publisher: Optional[Any] = None,
) -> PolicyFeedbackEngine:
    """构造一个 PolicyFeedbackEngine(默认配置)。"""
    return PolicyFeedbackEngine.build_default(
        throttle_registry=throttle_registry,
        runtime_budget=runtime_budget,
        config=config,
        max_proposals=max_proposals,
        publish_events=publish_events,
        event_publisher=event_publisher,
    )
