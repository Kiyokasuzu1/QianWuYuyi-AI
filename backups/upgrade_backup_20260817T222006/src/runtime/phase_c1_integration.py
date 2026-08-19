# -*- coding: utf-8 -*-
"""
src/runtime/phase_c1_integration.py

Phase C.1 Runtime Core Integration Layer —— RuntimeCycleOrchestrator

职责:
  - 串联已有 Adapter(Memory / Emotion / Personality / Relationship / Growth)
  - 通过 RuntimeB4Bridge 读取 B.4-B.13 摘要(只读)
  - 维护 RuntimeCycleContext 一次 cycle 的共享上下文
  - 留痕到 B.5 ActionPersistenceManager(append-only)
  - 全部 fail-soft,主流程不中断
  - 支持空状态运行(0 adapter 仍可 process_event)

不修改:
  - RuntimeCore / RuntimeBridge
  - RuntimeB4Bridge 已有逻辑(仅新增 cycle_orchestrator 注入点)
  - ProactiveEngine / ActionDispatcher / ActionConfidenceGate
  - 任何 Adapter(MemoryAdapter / EmotionAdapter / PersonalityAdapter / GrowthAdapter / ResponseAdapter)
  - B.4-B.13 任何行为

C.1 硬约束:
  - 不发起 action
  - 不修改 personality / growth / memory / emotion / relationship 任何核心
  - 不绕 B.4-B.13
  - 不在未 attach 时强制调用
  - 不阻塞主流程
  - 不在 process_cycle 内做网络 / LLM 调用
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.phase_b4_integration import RuntimeB4Bridge

from src.runtime.cycle_context import (
    RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION,
    CycleHistoryIndex,
    RuntimeCycleContext,
    _now_iso,
)
from src.runtime.cycle_adapter import (
    CycleAdapterRegistry,
    STANDARD_ADAPTERS_IN_ORDER,
    is_cycle_adapter,
    safe_call_health_check,
    safe_call_process_cycle,
    safe_call_snapshot,
)
from src.runtime.cycle_event import (
    CYCLE_EVENT_COMPLETED,
    CYCLE_EVENT_DECISION_COMPLETED,
    CYCLE_EVENT_FAILED,
    CYCLE_EVENT_STARTED,
    PHASE_C1_NAME,
    PHASE_C1_STAGE_ADAPTER_FAILED,
    PHASE_C1_STAGE_CYCLE_COMPLETED,
    PHASE_C1_STAGE_CYCLE_FAILED,
    PHASE_C1_STAGE_CYCLE_STARTED,
    PHASE_C1_STAGE_STEP_COMPLETED,
    PHASE_C1_VERSION,
    STEP_TO_EVENT,
    CYCLE_EVENT_SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)


# ============================================================
# Default config
# ============================================================

PHASE_C1_DEFAULT_CONFIG: Dict[str, Any] = {
    "phase_c1": {
        "enabled": True,
        "max_cycles": 500,                # 内存保留最大 cycle 数
        "auto_attach": False,             # 不自动 attach 注册的 adapter
        "fail_on_b4_error": False,        # B.4 摘要失败不中断 cycle
        "max_consecutive_failures": 5,    # 连续失败容忍上限(只统计,不抛)
    },
}


def apply_phase_c1_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """合并用户配置(用户 key 优先,默认兜底)"""
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}
    for k, v in PHASE_C1_DEFAULT_CONFIG.items():
        if isinstance(v, dict):
            existing = merged.get(k)
            if isinstance(existing, dict):
                sub: Dict[str, Any] = {}
                sub.update(v)
                sub.update(existing)
                merged[k] = sub
            else:
                merged[k] = {k2: v2 for k2, v2 in v.items()}
        else:
            merged.setdefault(k, v)
    return merged


def is_phase_c1_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(cfg, dict):
        return False
    if "phase_c1" not in cfg:
        return False
    p = cfg.get("phase_c1", {})
    if not isinstance(p, dict):
        return False
    return bool(p.get("enabled", True))


# ============================================================
# 内部辅助
# ============================================================

def _extract_user_input(event: Any) -> str:
    """从 event 中提取 user_input 字符串(失败返回空串)"""
    try:
        if event is None:
            return ""
        if isinstance(event, str):
            return event
        if isinstance(event, dict):
            return str(
                event.get("user_input", "")
                or event.get("content", "")
                or event.get("text", "")
                or event.get("input", "")
                or ""
            )
        # 兼容 Event dataclass
        if hasattr(event, "user_input"):
            v = getattr(event, "user_input", None)
            if v:
                return str(v)
        if hasattr(event, "content"):
            v = getattr(event, "content", None)
            if v:
                return str(v)
        # 兼容 payload
        payload = getattr(event, "payload", None)
        if isinstance(payload, dict):
            return str(
                payload.get("text", "")
                or payload.get("content", "")
                or payload.get("user_input", "")
                or ""
            )
        return ""
    except Exception:  # noqa: BLE001
        return ""


# ============================================================
# RuntimeCycleOrchestrator
# ============================================================

class RuntimeCycleOrchestrator:
    """
    Runtime Cycle 编排器(Phase C.1)

    串联 5 步标准 cycle:
      memory → emotion → personality → relationship → growth → decision(B.4) → finalize

    所有 adapter 失败都被 try/except 隔离。
    """

    PHASE_C1_NAME = PHASE_C1_NAME
    PHASE_C1_VERSION = PHASE_C1_VERSION
    SCHEMA_VERSION = CYCLE_EVENT_SCHEMA_VERSION

    def __init__(
        self,
        bridge: Any = None,                 # RuntimeB4Bridge(B.4-B.13)
        persistence: Any = None,            # ActionPersistenceManager(B.5)
        runtime_bridge: Any = None,         # 已有 RuntimeBridge singleton(可选,仅用于扩展)
        cfg: Optional[Dict[str, Any]] = None,
        enabled: Optional[bool] = None,
        max_cycles: int = 500,
    ) -> None:
        # 配置
        r_cfg: Dict[str, Any] = {}
        if isinstance(cfg, dict):
            raw = cfg.get("phase_c1", {}) or {}
            if isinstance(raw, dict):
                r_cfg = raw
        is_enabled = (
            bool(enabled) if enabled is not None else bool(r_cfg.get("enabled", True))
        )
        max_c = int(r_cfg.get("max_cycles", max_cycles) or 500)
        self._fail_on_b4_error = bool(r_cfg.get("fail_on_b4_error", False))
        self._max_consecutive_failures = int(r_cfg.get("max_consecutive_failures", 5) or 5)

        # 依赖(可空,空时 fail-soft)
        self._bridge = bridge
        self._persistence = persistence
        self._runtime_bridge = runtime_bridge

        # 状态
        self._enabled = bool(is_enabled)
        self._closed = False
        self._lock = threading.RLock()

        # Adapter registry
        self._adapters = CycleAdapterRegistry()

        # History
        self._history = CycleHistoryIndex(max_cycles=max(1, max_c))

        # 统计
        self._cycle_count = 0
        self._completed_count = 0
        self._failed_count = 0
        self._consecutive_failures = 0
        self._last_error: Optional[str] = None
        self._last_event_at: Optional[str] = None
        self._created_at: str = _now_iso()

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def cycle_count(self) -> int:
        with self._lock:
            return self._cycle_count

    @property
    def completed_count(self) -> int:
        with self._lock:
            return self._completed_count

    @property
    def failed_count(self) -> int:
        with self._lock:
            return self._failed_count

    @property
    def adapter_count(self) -> int:
        return self._adapters.count()

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    @property
    def persistence(self) -> Any:
        return self._persistence

    @property
    def bridge(self) -> Any:
        return self._bridge

    @property
    def runtime_bridge(self) -> Any:
        return self._runtime_bridge

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def set_bridge(self, bridge: Any) -> None:
        with self._lock:
            self._bridge = bridge

    def set_persistence(self, persistence: Any) -> None:
        with self._lock:
            self._persistence = persistence

    def set_runtime_bridge(self, runtime_bridge: Any) -> None:
        with self._lock:
            self._runtime_bridge = runtime_bridge

    # --------------------------------------------------------
    # Adapter 管理
    # --------------------------------------------------------

    def register_adapter(self, adapter: Any, name: Optional[str] = None) -> bool:
        """注册一个 adapter(任意符合 CycleAdapter 协议的对象)"""
        if not is_cycle_adapter(adapter):
            with self._lock:
                self._last_error = f"register_adapter failed: not a CycleAdapter ({type(adapter).__name__})"
            return False
        # 若 adapter 未 attach,可选自动 attach
        try:
            if hasattr(adapter, "is_attached") and callable(adapter.is_attached):
                try:
                    if not adapter.is_attached() and hasattr(adapter, "attach"):
                        try:
                            adapter.attach()
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        return self._adapters.register(adapter, name=name)

    def unregister_adapter(self, name: str) -> bool:
        return self._adapters.unregister(str(name or ""))

    def get_adapter(self, name: str) -> Optional[Any]:
        return self._adapters.get(str(name or ""))

    def list_adapters(self) -> List[Dict[str, Any]]:
        return self._adapters.list()

    def has_adapter(self, name: str) -> bool:
        return self._adapters.has(str(name or ""))

    # --------------------------------------------------------
    # Core: process_event
    # --------------------------------------------------------

    def process_event(
        self,
        event: Any,
        user_id: str = "yuyi",
        session_id: str = "",
    ) -> RuntimeCycleContext:
        """
        处理一个事件(主入口)

        流程:
          1) 创建 RuntimeCycleContext
          2) step 1-5: 跑 5 个标准 adapter
          3) step 6: 读 B.4-B.13 摘要
          4) step 7: finalize(留痕 + emit)
          5) 返回 ctx
        """
        ctx: Optional[RuntimeCycleContext] = None
        try:
            if not self._enabled or self._closed:
                ctx = self._build_context(event, user_id=user_id, session_id=session_id)
                ctx.error_count += 1
                ctx.degraded_adapters.append("orchestrator")
                ctx.adapter_results["orchestrator"] = {
                    "ok": False,
                    "error": "orchestrator_disabled",
                    "timestamp": _now_iso(),
                }
                ctx.log_stage("disabled", decision="rejected", success=False)
                self._persist_event(PHASE_C1_STAGE_CYCLE_FAILED, ctx, decision="disabled")
                return ctx

            # 1) build context
            ctx = self._build_context(event, user_id=user_id, session_id=session_id)
            ctx.log_stage(CYCLE_EVENT_STARTED, decision="started", success=True)
            self._persist_event(PHASE_C1_STAGE_CYCLE_STARTED, ctx, decision="started")

            # 2) 5 标准步
            self._step_1_memory(ctx)
            self._step_2_emotion(ctx)
            self._step_3_personality(ctx)
            self._step_4_relationship(ctx)
            self._step_5_growth(ctx)

            # 3) B.4-B.13
            self._step_6_decision_b_stages(ctx)

            # 4) finalize
            self._step_7_finalize(ctx)

            return ctx
        except Exception as exc:  # noqa: BLE001
            # 兜底:任何未捕获异常
            with self._lock:
                self._failed_count += 1
                self._consecutive_failures += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_c1] process_event 异常(已隔离): {exc}")
            if ctx is None:
                ctx = self._build_context(event, user_id=user_id, session_id=session_id)
            ctx.error_count += 1
            ctx.log_stage(CYCLE_EVENT_FAILED, decision="exception", success=False, details={"error": repr(exc)})
            ctx.adapter_results["orchestrator"] = {
                "ok": False,
                "error": repr(exc),
                "timestamp": _now_iso(),
            }
            try:
                self._persist_event(PHASE_C1_STAGE_CYCLE_FAILED, ctx, decision="exception")
            except Exception:  # noqa: BLE001
                pass
            return ctx

    def run_cycle(
        self,
        ctx: RuntimeCycleContext,
    ) -> RuntimeCycleContext:
        """对已有 ctx 跑剩余步骤(便于测试和手动编排)"""
        if not isinstance(ctx, RuntimeCycleContext):
            ctx = self._build_context(ctx)
        try:
            if not self._enabled or self._closed:
                ctx.log_stage("disabled", decision="rejected", success=False)
                return ctx
            self._step_1_memory(ctx)
            self._step_2_emotion(ctx)
            self._step_3_personality(ctx)
            self._step_4_relationship(ctx)
            self._step_5_growth(ctx)
            self._step_6_decision_b_stages(ctx)
            self._step_7_finalize(ctx)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._failed_count += 1
                self._last_error = repr(exc)
            ctx.log_stage(CYCLE_EVENT_FAILED, decision="exception", success=False, details={"error": repr(exc)})
        return ctx

    # --------------------------------------------------------
    # Step 0: build context
    # --------------------------------------------------------

    def _build_context(
        self,
        event: Any,
        user_id: str = "yuyi",
        session_id: str = "",
    ) -> RuntimeCycleContext:
        try:
            uid = str(user_id or "yuyi")
            sid = str(session_id or "")
            if not sid:
                sid = f"session_{uuid.uuid4().hex[:12]}"
            ctx = RuntimeCycleContext(
                session_id=sid,
                user_id=uid,
                timestamp=_now_iso(),
                input_event=event,
            )
            ctx.metadata["user_input"] = _extract_user_input(event)
            return ctx
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c1] _build_context 异常(已隔离): {exc}")
            return RuntimeCycleContext(input_event=event)

    # --------------------------------------------------------
    # Step 1-5: adapter 跑 5 步
    # --------------------------------------------------------

    def _run_step(self, ctx: RuntimeCycleContext, step_name: str, adapter_name: str) -> RuntimeCycleContext:
        """统一 step 包装:try/except + log + persist"""
        try:
            adapter = self._adapters.get(adapter_name)
            if adapter is None:
                ctx.log_stage(
                    STEP_TO_EVENT.get(step_name, f"cycle_{step_name}_skipped"),
                    decision="skipped",
                    success=True,
                    details={"reason": "adapter_not_registered", "adapter": adapter_name},
                )
                ctx.adapter_results[adapter_name] = {
                    "ok": True,
                    "skipped": True,
                    "reason": "adapter_not_registered",
                    "timestamp": _now_iso(),
                }
                return ctx
            # 调 adapter
            result_ctx = safe_call_process_cycle(adapter, ctx)
            if result_ctx is None:
                # adapter 抛异常
                ctx.record_adapter(adapter_name, ok=False, error="process_cycle raised")
                ctx.log_stage(
                    PHASE_C1_STAGE_ADAPTER_FAILED,
                    decision="exception",
                    success=False,
                    details={"adapter": adapter_name, "step": step_name},
                )
                self._persist_event(
                    PHASE_C1_STAGE_ADAPTER_FAILED,
                    ctx,
                    decision=f"{adapter_name}_exception",
                )
                return ctx
            ctx.record_adapter(adapter_name, ok=True)
            ctx.log_stage(
                STEP_TO_EVENT.get(step_name, f"cycle_{step_name}_completed"),
                decision="ok",
                success=True,
                details={"adapter": adapter_name, "step": step_name},
            )
            self._persist_event(
                PHASE_C1_STAGE_STEP_COMPLETED,
                ctx,
                decision=f"{step_name}_ok",
            )
            return result_ctx
        except Exception as exc:  # noqa: BLE001
            ctx.record_adapter(adapter_name, ok=False, error=repr(exc))
            ctx.log_stage(
                PHASE_C1_STAGE_ADAPTER_FAILED,
                decision="exception",
                success=False,
                details={"adapter": adapter_name, "error": repr(exc)},
            )
            try:
                self._persist_event(
                    PHASE_C1_STAGE_ADAPTER_FAILED,
                    ctx,
                    decision=f"{adapter_name}_exception",
                )
            except Exception:  # noqa: BLE001
                pass
            return ctx

    def _step_1_memory(self, ctx: RuntimeCycleContext) -> RuntimeCycleContext:
        return self._run_step(ctx, "memory", "memory")

    def _step_2_emotion(self, ctx: RuntimeCycleContext) -> RuntimeCycleContext:
        return self._run_step(ctx, "emotion", "emotion")

    def _step_3_personality(self, ctx: RuntimeCycleContext) -> RuntimeCycleContext:
        return self._run_step(ctx, "personality", "personality")

    def _step_4_relationship(self, ctx: RuntimeCycleContext) -> RuntimeCycleContext:
        return self._run_step(ctx, "relationship", "relationship")

    def _step_5_growth(self, ctx: RuntimeCycleContext) -> RuntimeCycleContext:
        return self._run_step(ctx, "growth", "growth")

    # --------------------------------------------------------
    # Step 6: 读 B.4-B.13 摘要(只读)
    # --------------------------------------------------------

    def _step_6_decision_b_stages(self, ctx: RuntimeCycleContext) -> RuntimeCycleContext:
        try:
            if self._bridge is None:
                ctx.log_stage(
                    CYCLE_EVENT_DECISION_COMPLETED,
                    decision="skipped",
                    success=True,
                    details={"reason": "bridge_not_attached"},
                )
                ctx.adapter_results["runtime_b4_bridge"] = {
                    "ok": True,
                    "skipped": True,
                    "reason": "bridge_not_attached",
                    "timestamp": _now_iso(),
                }
                return ctx

            bridge = self._bridge
            if not hasattr(bridge, "summarize_b4"):
                ctx.log_stage(
                    CYCLE_EVENT_DECISION_COMPLETED,
                    decision="skipped",
                    success=True,
                    details={"reason": "bridge_no_summarize_b4"},
                )
                ctx.adapter_results["runtime_b4_bridge"] = {
                    "ok": True,
                    "skipped": True,
                    "reason": "bridge_no_summarize_b4",
                    "timestamp": _now_iso(),
                }
                return ctx

            b4_summary = bridge.summarize_b4()
            if not isinstance(b4_summary, dict):
                b4_summary = {}

            ctx.b4_summary = b4_summary
            ctx.decision_context = {
                "evolution_enabled": bool(b4_summary.get("decision_evolution", {}).get("enabled", False)),
                "execution_enabled": bool(b4_summary.get("decision_execution", {}).get("enabled", False)),
                "intelligence_enabled": bool(b4_summary.get("decision_intelligence", {}).get("enabled", False)),
                "observer_enabled": bool(b4_summary.get("decision_observability", {}).get("enabled", False)),
                "total_executions": int(b4_summary.get("total_executions", 0) or 0),
                "total_evolution_proposals": int(b4_summary.get("total_evolution_proposals", 0) or 0),
            }
            ctx.log_stage(
                CYCLE_EVENT_DECISION_COMPLETED,
                decision="ok",
                success=True,
                details={"b4_keys": list(b4_summary.keys())},
            )
            ctx.adapter_results["runtime_b4_bridge"] = {
                "ok": True,
                "timestamp": _now_iso(),
            }
            self._persist_event(
                PHASE_C1_STAGE_STEP_COMPLETED,
                ctx,
                decision="decision_ok",
            )
            return ctx
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = repr(exc)
            ctx.record_adapter("runtime_b4_bridge", ok=False, error=repr(exc))
            ctx.log_stage(
                CYCLE_EVENT_DECISION_COMPLETED,
                decision="exception",
                success=False,
                details={"error": repr(exc)},
            )
            if self._fail_on_b4_error:
                raise
            return ctx

    # --------------------------------------------------------
    # Step 7: finalize
    # --------------------------------------------------------

    def _step_7_finalize(self, ctx: RuntimeCycleContext) -> RuntimeCycleContext:
        try:
            # 入 history
            self._history.add(ctx)
            with self._lock:
                self._cycle_count += 1
                self._completed_count += 1
                self._consecutive_failures = 0
                self._last_event_at = ctx.timestamp

            ctx.log_stage(
                CYCLE_EVENT_COMPLETED,
                decision="ok" if ctx.error_count == 0 else "ok_with_errors",
                success=True,
                details={
                    "error_count": int(ctx.error_count),
                    "degraded_adapters": list(ctx.degraded_adapters),
                },
            )
            self._persist_event(PHASE_C1_STAGE_CYCLE_COMPLETED, ctx, decision="ok")
            return ctx
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._failed_count += 1
                self._consecutive_failures += 1
                self._last_error = repr(exc)
            ctx.log_stage(
                CYCLE_EVENT_FAILED,
                decision="finalize_exception",
                success=False,
                details={"error": repr(exc)},
            )
            try:
                self._persist_event(PHASE_C1_STAGE_CYCLE_FAILED, ctx, decision="finalize_exception")
            except Exception:  # noqa: BLE001
                pass
            return ctx

    # --------------------------------------------------------
    # Persistence helper
    # --------------------------------------------------------

    def _persist_event(
        self,
        stage: str,
        ctx: RuntimeCycleContext,
        decision: str = "",
    ) -> None:
        """写一条 cycle 状态变化到 persistence(完全 fail-soft)"""
        if self._persistence is None:
            return
        try:
            if not hasattr(self._persistence, "persist_event"):
                return
            ts = time.time()
            aid = f"phase_c1_{ctx.cycle_id}"
            lc_id = f"phase_c1_{ctx.cycle_id}_{int(ts * 1000)}_{uuid.uuid4().hex[:6]}"
            ok = self._persistence.persist_event(
                action_id=aid,
                lifecycle_id=lc_id,
                stage=stage,
                decision=str(decision or ""),
                ts=ts,
                result=ctx.snapshot(),
                source="phase_c1_orchestrator",
            )
            if not ok:
                logger.debug("[phase_c1] persist_event 返回 False(已隔离)")
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c1] persist_event 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # Query: get_context / list_cycles
    # --------------------------------------------------------

    def get_context(self, cycle_id: str) -> Optional[RuntimeCycleContext]:
        return self._history.get(str(cycle_id or ""))

    def list_cycles(self, limit: int = 50) -> List[RuntimeCycleContext]:
        return self._history.list(limit=limit)

    # --------------------------------------------------------
    # Health Normalization Helper (Phase C.7.1)
    # --------------------------------------------------------

    def _normalize_adapter_health(
        self,
        health: Any,
        adapter_name: str,
    ) -> Dict[str, Any]:
        """将不同 Adapter 返回的 health dict 归一化为统一内部结构。

        各 Adapter 的 health_check() 返回格式不统一:
          - MemoryRuntimeAdapter 使用 {"healthy": bool, ...}
          - EmotionRuntimeAdapter / PersonalityRuntimeAdapter /
            RelationshipRuntimeAdapter / GrowthRuntimeAdapter 使用
            {"status": "healthy" | "degraded", ...}

        本方法在不修改 Adapter 本身的前提下,在 Orchestrator 内部做格式归一化,
        统一暴露给 health_check() / is_degraded / snapshot 使用。

        Args:
            health:        Adapter.health_check() 的原始返回
            adapter_name:  adapter 名称(用于回填)

        Returns:
            归一化结构:
              {
                "name":     str,   # adapter 名称
                "healthy":  bool,  # 是否健康
                "degraded": bool,  # 是否降级(healthy 取反)
                "raw":      dict,  # 原始 health 拷贝(便于排查)
              }
        """
        try:
            n = str(adapter_name or "")
            # 基础默认:不健康
            normalized: Dict[str, Any] = {
                "name": n,
                "healthy": False,
                "degraded": True,
                "raw": {},
            }
            if not isinstance(health, dict):
                return normalized

            # 保留原始拷贝(便于排查)
            try:
                normalized["raw"] = dict(health)
            except Exception:  # noqa: BLE001
                normalized["raw"] = {}

            # 优先读取 "healthy" 字段(Memory 等用)
            if "healthy" in health:
                try:
                    normalized["healthy"] = bool(health["healthy"])
                except Exception:  # noqa: BLE001
                    normalized["healthy"] = False
                normalized["degraded"] = not normalized["healthy"]
                return normalized

            # 退化:读 "status" 字段(Emotion / Personality / Relationship / Growth 等)
            try:
                status_raw = health.get("status", "")
                status = str(status_raw or "").strip().lower()
            except Exception:  # noqa: BLE001
                status = ""
            if status == "healthy":
                normalized["healthy"] = True
                normalized["degraded"] = False
            else:
                # 包含 "degraded" / "error" / "unhealthy" / 未知 / 空 → 视为不健康
                normalized["healthy"] = False
                normalized["degraded"] = True
            return normalized
        except Exception:  # noqa: BLE001
            return {
                "name": str(adapter_name or ""),
                "healthy": False,
                "degraded": True,
                "raw": {},
            }

    # --------------------------------------------------------
    # Health check / snapshot
    # --------------------------------------------------------

    def health_check(self) -> Dict[str, Any]:
        try:
            with self._lock:
                health: Dict[str, Any] = {
                    "name": self.PHASE_C1_NAME,
                    "version": self.PHASE_C1_VERSION,
                    "schema_version": self.SCHEMA_VERSION,
                    "enabled": self._enabled,
                    "closed": self._closed,
                    "cycle_count": int(self._cycle_count),
                    "completed_count": int(self._completed_count),
                    "failed_count": int(self._failed_count),
                    "consecutive_failures": int(self._consecutive_failures),
                    "adapter_count": self._adapters.count(),
                    "history_in_memory": int(self._history.count()),
                    "last_event_at": self._last_event_at,
                    "last_error": self._last_error,
                    "created_at": self._created_at,
                    "degraded": self.is_degraded,
                }
                # 收集 adapter 健康(通过归一化层统一格式)
                adapter_health: List[Dict[str, Any]] = []
                healthy_adapters = 0
                for item in self._adapters.list():
                    name = item.get("name", "")
                    raw_h = item.get("health", {})
                    normalized = self._normalize_adapter_health(raw_h, name)
                    adapter_health.append(normalized)
                    if normalized.get("healthy"):
                        healthy_adapters += 1
                health["adapters"] = adapter_health
                health["healthy_adapters"] = int(healthy_adapters)
                # 整体 healthy 判定
                if self._closed:
                    health["healthy"] = False
                elif not self._enabled:
                    health["healthy"] = True  # disabled 视作 healthy
                elif self._adapters.count() == 0:
                    # 0 adapter(空状态)按 idle 处理:无连续失败即 healthy
                    health["healthy"] = (
                        self._consecutive_failures < self._max_consecutive_failures
                    )
                else:
                    # 启用态 + 有 adapter:连续失败未超阈值 且 至少 1 个 adapter 健康
                    health["healthy"] = (
                        self._consecutive_failures < self._max_consecutive_failures
                        and healthy_adapters > 0
                    )
                return health
        except Exception as exc:  # noqa: BLE001
            return {
                "name": self.PHASE_C1_NAME,
                "healthy": False,
                "error": repr(exc),
            }

    def snapshot(self) -> Dict[str, Any]:
        try:
            with self._lock:
                snap = {
                    "name": self.PHASE_C1_NAME,
                    "version": self.PHASE_C1_VERSION,
                    "schema_version": self.SCHEMA_VERSION,
                    "enabled": self._enabled,
                    "closed": self._closed,
                    "cycle_count": int(self._cycle_count),
                    "completed_count": int(self._completed_count),
                    "failed_count": int(self._failed_count),
                    "consecutive_failures": int(self._consecutive_failures),
                    "adapter_count": self._adapters.count(),
                    "adapters": self._adapters.list(),
                    "history_stats": self._history.get_stats(),
                    "last_error": self._last_error,
                    "last_event_at": self._last_event_at,
                    "created_at": self._created_at,
                    "degraded": self.is_degraded,
                    "ts": time.time(),
                    # Phase C.7.1: 补齐 healthy 字段(与 health_check() 一致)
                    "healthy": self.health_check().get("healthy", False),
                }
            return snap
        except Exception as exc:  # noqa: BLE001
            return {
                "name": self.PHASE_C1_NAME,
                "version": self.PHASE_C1_VERSION,
                "error": repr(exc),
            }

    @property
    def is_degraded(self) -> bool:
        try:
            # 条件 1:persistence 处于 degraded
            if self._persistence is not None:
                if bool(getattr(self._persistence, "is_degraded", False)):
                    return True
            # 条件 2:任一 adapter 处于 degraded(通过归一化层判断)
            for item in self._adapters.list():
                name = item.get("name", "")
                raw_h = item.get("health", {})
                normalized = self._normalize_adapter_health(raw_h, name)
                if not normalized.get("healthy", False):
                    return True
            return False
        except Exception:  # noqa: BLE001
            return True

    # --------------------------------------------------------
    # 辅助: clear / reset
    # --------------------------------------------------------

    def clear(self) -> None:
        """清空 history(测试用)"""
        with self._lock:
            self._history.clear()
            self._cycle_count = 0
            self._completed_count = 0
            self._failed_count = 0
            self._consecutive_failures = 0
            self._last_error = None
            self._last_event_at = None

    def list_recent_cycles(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [c.snapshot() for c in self._history.list(limit=limit)]


# ============================================================
# Factory / helper
# ============================================================

def create_runtime_cycle_orchestrator(
    bridge: Any = None,
    persistence: Any = None,
    runtime_bridge: Any = None,
    cfg: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
    max_cycles: int = 500,
) -> RuntimeCycleOrchestrator:
    """工厂:根据 cfg 构造 RuntimeCycleOrchestrator。"""
    r_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        raw = cfg.get("phase_c1", {}) or {}
        if isinstance(raw, dict):
            r_cfg = raw
    is_enabled = bool(enabled) if enabled is not None else bool(r_cfg.get("enabled", True))
    max_c = int(r_cfg.get("max_cycles", max_cycles) or 500)

    return RuntimeCycleOrchestrator(
        bridge=bridge,
        persistence=persistence,
        runtime_bridge=runtime_bridge,
        cfg=cfg,
        enabled=is_enabled,
        max_cycles=max_c,
    )


def safe_get_orchestrator_summary(
    orch: Optional["RuntimeCycleOrchestrator"],
) -> Dict[str, Any]:
    """全局安全 summary(任何异常都吸收)"""
    empty = {
        "name": PHASE_C1_NAME,
        "version": PHASE_C1_VERSION,
        "enabled": False,
        "cycle_count": 0,
        "completed_count": 0,
        "failed_count": 0,
    }
    if orch is None:
        return empty
    try:
        return orch.snapshot() or empty
    except Exception:  # noqa: BLE001
        return empty


# ============================================================
# 公共 API
# ============================================================

__all__ = [
    "PHASE_C1_DEFAULT_CONFIG",
    "PHASE_C1_NAME",
    "PHASE_C1_VERSION",
    "CYCLE_EVENT_SCHEMA_VERSION",
    "RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION",
    "apply_phase_c1_config",
    "is_phase_c1_enabled",
    "RuntimeCycleOrchestrator",
    "create_runtime_cycle_orchestrator",
    "safe_get_orchestrator_summary",
    "_extract_user_input",
]
