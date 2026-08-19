# -*- coding: utf-8 -*-
"""
src/runtime/initiative/initiative_runtime_adapter.py

Phase C.9.1 Initiative Runtime Integration —— InitiativeRuntimeAdapter

职责:
  - 实现 Phase C.1 CycleAdapter 协议(duck-type)
  - 在 Runtime Cycle 流程里调度 Phase C.9.0 InitiativeDecisionEngine
  - 把 RuntimeCycleContext 上游产出(memory / emotion / relationship / personality / self_model / growth)转换为决策上下文
  - 把决策结果写入 ctx.initiative_output
  - **绝不**直接发送消息(QQ / Web / 聊天 API)
  - **绝不**修改 Personality / SelfModel / Relationship / Memory 任何状态

**核心原则 (硬约束)**:
  - 绝对不调 PersonalityResolver.resolve()
  - 绝对不调 PersonalityAdapter.apply_proposal()
  - 绝对不调 TraitStateUpdater.apply()
  - 绝对不调 SelfModelStore.update()
  - 绝对不调 RelationshipState.save()
  - 绝对不调 MessageChannel.send() / 任何 QQ / Web / 聊天发送接口
  - 绝对不调 MemoryStore.modify()
  - 绝对不修改 ctx.personality_output / ctx.relationship_output / ctx.self_model / ctx.memory_output

**允许**:
  - 读取 InitiativeMemoryStore(只读历史)
  - 读取 Personality snapshot(只读)
  - 读取 Relationship snapshot(只读)
  - 写入 InitiativeMemoryStore(用于记录 evaluated / suppressed / deferred)
  - 写入 ctx.initiative_output
  - 写入 ctx.adapter_results(由 RuntimeCycleContext 自行管理)
  - 记录 audit(runtime_initiative_runtime_evaluated)

不修改:
  - RuntimeCore / RuntimeBridge / RuntimeCycleOrchestrator
  - RuntimeCycleContext
  - cycle_adapter.py / cycle_event.py / cycle_context.py
  - 任何现有 Phase A/B/C 文件
  - Memory / Emotion / Personality / Growth / Relationship 任何核心文件
  - Personality / SelfModel / Relationship 任何写入接口

设计原则:
  - 与 Phase 5.0-D3-C 的 InitiativeAdapter 共存,只新增文件,不改旧文件
  - 外部注入优先(decision_engine / memory_store / audit),缺省时自建
  - 任何异常均 fail-soft
  - 读取时通过 dict() / list() 浅拷贝,杜绝副作用
"""
from __future__ import annotations

import copy
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.runtime.cycle_context import RuntimeCycleContext

logger = logging.getLogger(__name__)


# ============================================================
# Schema + 常量
# ============================================================

INITIATIVE_RUNTIME_ADAPTER_SCHEMA_VERSION = "1.0"
INITIATIVE_RUNTIME_ADAPTER_NAME = "initiative_runtime_adapter"
INITIATIVE_RUNTIME_ADAPTER_VERSION = "1.0.0"

# 标准 cycle step name
STANDARD_ADAPTER_INITIATIVE = "initiative"
SOURCE_NAME = "initiative_runtime_adapter"

# Decision labels
DECISION_INITIATE = "initiate"
DECISION_DEFER = "defer"
DECISION_SUPPRESS = "suppress"
ALL_DECISIONS: List[str] = [DECISION_INITIATE, DECISION_DEFER, DECISION_SUPPRESS]

# Audit
AUDIT_ACTION_RUNTIME_EVALUATED = "runtime_initiative_runtime_evaluated"
AUDIT_ACTION_RUNTIME_SUPPRESSED = "runtime_initiative_runtime_suppressed"
AUDIT_ACTION_RUNTIME_INITIATED = "runtime_initiative_runtime_initiated"
AUDIT_ACTION_RUNTIME_DEFERRED = "runtime_initiative_runtime_deferred"
AUDIT_ACTION_RUNTIME_FAILED = "runtime_initiative_runtime_failed"
AUDIT_COMPONENT = "runtime_initiative_adapter"

# Stage names
STAGE_INITIATIVE_EVALUATE = "phase_c91_initiative_evaluate"
STAGE_INITIATIVE_DEGRADED = "phase_c91_initiative_degraded"
STAGE_INITIATIVE_INITIATED = "phase_c91_initiative_initiated"
STAGE_INITIATIVE_DEFERRED = "phase_c91_initiative_deferred"
STAGE_INITIATIVE_SUPPRESSED = "phase_c91_initiative_suppressed"

ALL_PHASE_C91_STAGES = (
    STAGE_INITIATIVE_EVALUATE,
    STAGE_INITIATIVE_DEGRADED,
    STAGE_INITIATIVE_INITIATED,
    STAGE_INITIATIVE_DEFERRED,
    STAGE_INITIATIVE_SUPPRESSED,
)

DEFAULT_ACTOR = "runtime"


# ============================================================
# 工具
# ============================================================


def _now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        try:
            return datetime.utcnow().isoformat() + "Z"
        except Exception:
            return "1970-01-01T00:00:00Z"


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:
            return default
        return f
    except (TypeError, ValueError):
        return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        if v is None:
            return default
        return bool(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        if isinstance(v, tuple):
            return list(v)
        return []
    except Exception:
        return []


def _safe_dict(v: Any) -> Dict[str, Any]:
    try:
        if isinstance(v, dict):
            return dict(v)
        return {}
    except Exception:
        return {}


def _safe_copy_dict(v: Any, max_items: int = 20) -> Dict[str, Any]:
    if not isinstance(v, dict):
        return {}
    try:
        out: Dict[str, Any] = {}
        for i, (k, val) in enumerate(v.items()):
            if i >= max_items:
                break
            if isinstance(val, dict):
                out[str(k)] = dict(val)
            elif isinstance(val, (list, tuple)):
                out[str(k)] = _safe_copy_list(val)
            elif isinstance(val, (str, int, float, bool)) or val is None:
                out[str(k)] = val
            else:
                out[str(k)] = _safe_str(repr(val), "")
        return out
    except Exception:
        return {}


def _safe_copy_list(v: Any, max_items: int = 20) -> List[Any]:
    if v is None:
        return []
    if not isinstance(v, (list, tuple)):
        return []
    try:
        out: List[Any] = []
        for i, item in enumerate(v):
            if i >= max_items:
                break
            if isinstance(item, dict):
                out.append(dict(item))
            elif isinstance(item, (list, tuple)):
                out.append(_safe_copy_list(item))
            elif isinstance(item, (str, int, float, bool)) or item is None:
                out.append(item)
            else:
                out.append(_safe_str(repr(item), ""))
        return out
    except Exception:
        return []


def _normalize_decision_label(decision: str) -> str:
    """统一 decision 标签:allow -> initiate, deny -> suppress, defer -> defer."""
    d = _safe_str(decision, "").strip().lower()
    if d in ("allow", "initiate", "yes", "true"):
        return DECISION_INITIATE
    if d in ("deny", "suppress", "no", "false", "rejected"):
        return DECISION_SUPPRESS
    if d == "defer":
        return DECISION_DEFER
    return DECISION_SUPPRESS  # 未知值降级为 suppress


def _build_degraded_output(error: str = "") -> Dict[str, Any]:
    """构造降级 output(fail-soft)。"""
    return {
        "decision": DECISION_SUPPRESS,
        "confidence": 0.0,
        "priority": 0.0,
        "trigger": "none",
        "reasons": ["degraded"],
        "message_request": None,
        "channel_ready": False,
        "timestamp": _now_iso(),
        "degraded": True,
        "error": _safe_str(error, "unknown"),
        "source": SOURCE_NAME,
        "schema_version": INITIATIVE_RUNTIME_ADAPTER_SCHEMA_VERSION,
    }


def _record_audit_safely(
    audit: Any,
    action: str,
    detail: Dict[str, Any],
    result: str = "success",
) -> bool:
    """安全记录 audit(失败时静默返回 False)。"""
    try:
        if audit is None:
            return False
        d = dict(detail) if isinstance(detail, dict) else {}
        if hasattr(audit, "record") and callable(getattr(audit, "record")):
            try:
                audit.record(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=d,
                    result=result,
                )
                return True
            except Exception:
                pass
        if hasattr(audit, "save_audit_record") and callable(getattr(audit, "save_audit_record")):
            try:
                from src.audit.record import AuditRecord
                rec = AuditRecord(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=d,
                    result=result,
                )
                audit.save_audit_record(rec)
                return True
            except Exception:
                pass
        if hasattr(audit, "record_audit_log") and callable(getattr(audit, "record_audit_log")):
            try:
                audit.record_audit_log(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=d,
                    result=result,
                )
                return True
            except Exception:
                pass
        return False
    except Exception:
        return False


# ============================================================
# 决策上下文构建
# ============================================================


def _extract_decision_context(ctx: RuntimeCycleContext) -> Dict[str, Any]:
    """从 RuntimeCycleContext 提取 InitiativeDecisionEngine 需要的 context。

    **只读操作** —— 浅拷贝所有字段,绝不修改 ctx 任何上游字段。
    """
    try:
        user_id = _safe_str(getattr(ctx, "user_id", "yuyi"), "yuyi") or "yuyi"
        cycle_id = _safe_str(getattr(ctx, "cycle_id", ""), "")
        # upstream outputs(只读浅拷贝)
        memory = _safe_copy_dict(getattr(ctx, "memory_output", None))
        emotion = _safe_copy_dict(getattr(ctx, "emotion_output", None))
        personality = _safe_copy_dict(getattr(ctx, "personality_output", None))
        relationship = _safe_copy_dict(getattr(ctx, "relationship_output", None))
        growth_src = getattr(ctx, "growth_output", None)
        if isinstance(growth_src, list):
            growth = _safe_copy_list(growth_src)
        elif isinstance(growth_src, dict):
            growth = _safe_copy_dict(growth_src)
        else:
            growth = {}

        # metadata 提取
        meta = _safe_copy_dict(getattr(ctx, "metadata", None))
        relationship_level = _safe_float(
            meta.get("relationship_level", 0.5), 0.5,
        )
        # 优先从 relationship 读 level
        if isinstance(relationship, dict):
            rel_metrics = relationship.get("current_metrics")
            if isinstance(rel_metrics, dict):
                lvl = _safe_float(rel_metrics.get("familiarity", 0.0), 0.0)
                if lvl > 0.0:
                    relationship_level = lvl

        user_preference = _safe_str(meta.get("user_preference", "open"), "open") or "open"
        if user_preference not in ("open", "reserved", "silent"):
            user_preference = "open"

        # 组装 context(只读快照,绝不持有原 ctx 引用)
        decision_ctx: Dict[str, Any] = {
            "user_id": user_id,
            "cycle_id": cycle_id,
            "relationship_level": relationship_level,
            "user_preference": user_preference,
            "memory": memory,
            "emotion": emotion,
            "personality": personality,
            "relationship": relationship,
            "growth": growth,
        }
        return decision_ctx
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[phase_c91] extract_decision_context 异常(已隔离): {exc}")
        return {
            "user_id": "yuyi",
            "cycle_id": "",
            "relationship_level": 0.5,
            "user_preference": "open",
            "memory": {},
            "emotion": {},
            "personality": {},
            "relationship": {},
            "growth": {},
            "_extract_error": repr(exc),
        }


# ============================================================
# InitiativeRuntimeAdapter(主类)
# ============================================================


class InitiativeRuntimeAdapter:
    """
    Initiative 系统 Runtime Cycle Adapter (Phase C.9.1 / v1.0)

    **Read-Mostly Adapter** —— 只读取 RuntimeCycleContext 上游产出,
    调用 Phase C.9.0 InitiativeDecisionEngine 做主动决策,
    把决策结果写入 ctx.initiative_output。

    实现 CycleAdapter 协议(duck-type):
      name             = "initiative"
      schema_version   = "1.0"
      attach()         - 接入(可选自建 decision engine)
      detach()         - 解除
      health_check()   - 健康检查
      process_cycle()  - 一次 cycle 处理
      snapshot()       - 快照

    业务行为:
      - 读 ctx.memory_output / emotion_output / relationship_output /
              personality_output / growth_output / metadata(全部只读)
      - 调用 InitiativeDecisionEngine.evaluate(decision_ctx)
      - 写 ctx.initiative_output(统一格式)
      - 记录 audit(runtime_initiative_runtime_evaluated / suppressed / initiated / deferred)
      - 写 ctx.stage_log / ctx.adapter_results

    全部 fail-soft: 任何异常都返回 degraded output,Runtime 不会中断。
    """

    name: str = STANDARD_ADAPTER_INITIATIVE
    schema_version: str = INITIATIVE_RUNTIME_ADAPTER_SCHEMA_VERSION

    def __init__(
        self,
        decision_engine: Any = None,
        memory_store: Any = None,
        audit: Any = None,
        user_id: str = "yuyi",
    ) -> None:
        self._user_id = _safe_str(user_id, "yuyi") or "yuyi"

        # 外部注入优先
        self._engine = decision_engine
        self._memory_store = memory_store
        self._audit = audit

        # 内部状态
        self._attached: bool = False
        self._lock = threading.RLock()
        self._process_count: int = 0
        self._initiate_count: int = 0
        self._defer_count: int = 0
        self._suppress_count: int = 0
        self._degraded_count: int = 0
        self._last_output: Optional[Dict[str, Any]] = None
        self._last_snapshot: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None
        self._last_health: Optional[Dict[str, Any]] = None

    # --------------------------------------------------------
    # CycleAdapter 协议
    # --------------------------------------------------------

    def attach(self) -> bool:
        """接入 Initiative(可选自建 decision engine)。"""
        with self._lock:
            try:
                if self._engine is None:
                    try:
                        from src.runtime.initiative import (
                            create_initiative_decision_engine,
                        )
                        self._engine = create_initiative_decision_engine(
                            memory_store=self._memory_store,
                            audit=self._audit,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            f"[phase_c91] 自建 decision engine 失败: {exc}"
                        )
                        self._engine = None
                self._attached = True
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_c91] attach 异常(已隔离): {exc}")
                self._attached = True  # 仍标记 attached,继续以 degraded 模式工作
        return True

    def detach(self) -> bool:
        with self._lock:
            self._attached = False
        return True

    def is_attached(self) -> bool:
        with self._lock:
            return self._attached

    def health_check(self) -> Dict[str, Any]:
        try:
            with self._lock:
                engine_ok = self._engine is not None and hasattr(
                    self._engine, "evaluate",
                ) and callable(getattr(self._engine, "evaluate", None))
                status = "healthy" if engine_ok else "degraded"
                result: Dict[str, Any] = {
                    "adapter": STANDARD_ADAPTER_INITIATIVE,
                    "status": status,
                    "engine_available": engine_ok,
                    "attached": bool(self._attached),
                    "process_count": int(self._process_count),
                    "initiate_count": int(self._initiate_count),
                    "defer_count": int(self._defer_count),
                    "suppress_count": int(self._suppress_count),
                    "degraded_count": int(self._degraded_count),
                    "user_id": str(self._user_id),
                    "schema_version": self.schema_version,
                }
            self._last_health = result
            return result
        except Exception as exc:  # noqa: BLE001
            return {
                "adapter": STANDARD_ADAPTER_INITIATIVE,
                "status": "degraded",
                "error": repr(exc),
            }

    def process_cycle(self, ctx: Any) -> Any:
        """一次 Runtime Cycle 处理(被 orchestrator 调用)。

        流程:
          1) 校验 ctx(非 RuntimeCycleContext 直接返回)
          2) 提取只读 decision context
          3) 调用 InitiativeDecisionEngine.evaluate()(降级安全)
          4) 映射到统一 output(decision / confidence / priority / trigger / reasons / message_request)
          5) 写入 ctx.initiative_output
          6) 记录 ctx.stage_log / ctx.adapter_results
          7) 记录 audit
          8) 缓存 last_output / last_snapshot
          9) 返回 ctx

        **任何异常都 fail-soft,绝不抛。**
        **绝不修改 personality / self_model / relationship / memory 上游字段。**
        **绝不调用 send / apply / update / save 等写入接口。**
        """
        with self._lock:
            self._process_count += 1
        try:
            if not isinstance(ctx, RuntimeCycleContext):
                # 非标准 ctx:不处理,原样返回
                return ctx

            # 1) 提取只读 decision context
            decision_ctx = _extract_decision_context(ctx)

            # 2) 调用 engine(降级安全)
            raw_result = self._safe_evaluate(decision_ctx)

            # 3) 映射到统一 output
            output = self._build_output(decision_ctx, raw_result)

            # 4) 写入 ctx.initiative_output(只新增字段,不动其它)
            try:
                ctx.initiative_output = output
            except Exception:  # noqa: BLE001
                # 极端:ctx 不可写。降级为只更新内部缓存
                logger.debug("[phase_c91] ctx.initiative_output 写入失败")

            # 5) stage_log
            try:
                decision = _safe_str(output.get("decision", "suppress"), "suppress")
                if decision == DECISION_INITIATE:
                    stage_name = STAGE_INITIATIVE_INITIATED
                elif decision == DECISION_DEFER:
                    stage_name = STAGE_INITIATIVE_DEFERRED
                elif output.get("degraded"):
                    stage_name = STAGE_INITIATIVE_DEGRADED
                else:
                    stage_name = STAGE_INITIATIVE_SUPPRESSED
                ctx.log_stage(
                    stage=stage_name,
                    decision=decision,
                    success=not bool(output.get("degraded")),
                    details={
                        "trigger": _safe_str(output.get("trigger", ""), ""),
                        "priority": _safe_float(output.get("priority", 0.0), 0.0),
                        "confidence": _safe_float(output.get("confidence", 0.0), 0.0),
                        "channel_ready": _safe_bool(output.get("channel_ready", False), False),
                    },
                )
            except Exception:  # noqa: BLE001
                pass

            # 6) adapter_results
            try:
                ctx.record_adapter(
                    name=STANDARD_ADAPTER_INITIATIVE,
                    ok=not bool(output.get("degraded")),
                    error=_safe_str(output.get("error", ""), ""),
                    details={
                        "decision": _safe_str(output.get("decision", ""), ""),
                        "trigger": _safe_str(output.get("trigger", ""), ""),
                        "priority": _safe_float(output.get("priority", 0.0), 0.0),
                        "channel_ready": _safe_bool(output.get("channel_ready", False), False),
                    },
                )
            except Exception:  # noqa: BLE001
                pass

            # 7) 记录 audit
            try:
                audit_detail = {
                    "cycle_id": _safe_str(decision_ctx.get("cycle_id", ""), ""),
                    "decision": _safe_str(output.get("decision", ""), ""),
                    "confidence": _safe_float(output.get("confidence", 0.0), 0.0),
                    "priority": _safe_float(output.get("priority", 0.0), 0.0),
                    "trigger": _safe_str(output.get("trigger", ""), ""),
                    "timestamp": _safe_str(output.get("timestamp", _now_iso()), _now_iso()),
                    "user_id": _safe_str(decision_ctx.get("user_id", ""), ""),
                    "channel_ready": _safe_bool(output.get("channel_ready", False), False),
                }
                if output.get("degraded"):
                    audit_action = AUDIT_ACTION_RUNTIME_FAILED
                    audit_result = "degraded"
                else:
                    decision_label = _safe_str(output.get("decision", ""), "")
                    if decision_label == DECISION_INITIATE:
                        audit_action = AUDIT_ACTION_RUNTIME_INITIATED
                    elif decision_label == DECISION_DEFER:
                        audit_action = AUDIT_ACTION_RUNTIME_DEFERRED
                    else:
                        audit_action = AUDIT_ACTION_RUNTIME_SUPPRESSED
                    audit_result = "success"
                _record_audit_safely(
                    audit=self._audit,
                    action=audit_action,
                    detail=audit_detail,
                    result=audit_result,
                )
                # 同时记录 evaluated(无论结果如何)
                _record_audit_safely(
                    audit=self._audit,
                    action=AUDIT_ACTION_RUNTIME_EVALUATED,
                    detail=audit_detail,
                    result=audit_result,
                )
            except Exception:  # noqa: BLE001
                pass

            # 8) 缓存
            with self._lock:
                self._last_output = output
                if output.get("degraded"):
                    self._degraded_count += 1
                    self._last_error = _safe_str(output.get("error", ""), "")
                decision = _safe_str(output.get("decision", ""), "")
                if decision == DECISION_INITIATE:
                    self._initiate_count += 1
                elif decision == DECISION_DEFER:
                    self._defer_count += 1
                else:
                    self._suppress_count += 1
            return ctx
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c91] process_cycle 异常(已隔离): {exc}")
            with self._lock:
                self._degraded_count += 1
                self._last_error = repr(exc)
            # 写入 degraded output
            try:
                if isinstance(ctx, RuntimeCycleContext):
                    ctx.initiative_output = _build_degraded_output(repr(exc))
            except Exception:  # noqa: BLE001
                pass
            return ctx

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "available": self._engine is not None,
                "attached": bool(self._attached),
                "process_count": int(self._process_count),
                "initiate_count": int(self._initiate_count),
                "defer_count": int(self._defer_count),
                "suppress_count": int(self._suppress_count),
                "degraded_count": int(self._degraded_count),
                "user_id": str(self._user_id),
                "last_output": dict(self._last_output) if self._last_output else None,
                "last_error": self._last_error,
            }

    # --------------------------------------------------------
    # 内部:安全调用 engine
    # --------------------------------------------------------

    def _safe_evaluate(self, decision_ctx: Dict[str, Any]) -> Dict[str, Any]:
        """调用 decision engine,异常时返回 degraded dict。"""
        try:
            if self._engine is None:
                return {
                    "should_initiate": False,
                    "degraded": True,
                    "error": "engine_not_attached",
                    "decision": DECISION_SUPPRESS,
                    "priority": 0.0,
                    "confidence": 0.0,
                    "trigger": "none",
                    "reason": ["engine_not_attached"],
                    "timestamp": _now_iso(),
                }
            evaluate_fn = getattr(self._engine, "evaluate", None)
            if not callable(evaluate_fn):
                return {
                    "should_initiate": False,
                    "degraded": True,
                    "error": "engine_evaluate_not_callable",
                    "decision": DECISION_SUPPRESS,
                    "priority": 0.0,
                    "confidence": 0.0,
                    "trigger": "none",
                    "reason": ["engine_invalid"],
                    "timestamp": _now_iso(),
                }
            result = evaluate_fn(decision_ctx, actor=DEFAULT_ACTOR)
            if not isinstance(result, dict):
                return {
                    "should_initiate": False,
                    "degraded": True,
                    "error": "engine_returned_non_dict",
                    "decision": DECISION_SUPPRESS,
                    "priority": 0.0,
                    "confidence": 0.0,
                    "trigger": "none",
                    "reason": ["engine_invalid_output"],
                    "timestamp": _now_iso(),
                }
            return result
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c91] _safe_evaluate 异常(已隔离): {exc}")
            return {
                "should_initiate": False,
                "degraded": True,
                "error": repr(exc),
                "decision": DECISION_SUPPRESS,
                "priority": 0.0,
                "confidence": 0.0,
                "trigger": "none",
                "reason": ["engine_exception"],
                "timestamp": _now_iso(),
            }

    def _build_output(
        self,
        decision_ctx: Dict[str, Any],
        raw_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """映射 engine 输出到标准 output 格式。"""
        try:
            degraded = _safe_bool(raw_result.get("degraded", False), False)
            decision_raw = _safe_str(raw_result.get("decision", ""), "")
            if degraded:
                decision = DECISION_SUPPRESS
            else:
                decision = _normalize_decision_label(decision_raw)
                # 即使 decision=allow,只要 should_initiate=False,也降级为 suppress
                if not _safe_bool(raw_result.get("should_initiate", False), False):
                    if decision == DECISION_INITIATE:
                        decision = DECISION_SUPPRESS
            confidence = _safe_float(raw_result.get("confidence", 0.0), 0.0)
            priority = _safe_float(raw_result.get("priority", 0.0), 0.0)
            trigger = _safe_str(raw_result.get("trigger", "none"), "none") or "none"
            reasons_src = raw_result.get("reason", raw_result.get("reasons", []))
            reasons = _safe_list(reasons_src)
            if isinstance(reasons_src, str):
                reasons = [reasons_src]
            # 收集 policy_result reasons(若存在)
            policy_result = raw_result.get("policy_result")
            if isinstance(policy_result, dict):
                policy_reasons = _safe_list(policy_result.get("reasons", []))
                for pr in policy_reasons:
                    if isinstance(pr, str) and pr not in reasons:
                        reasons.append(pr)
            message_request = raw_result.get("message_request")
            if not isinstance(message_request, dict):
                message_request = None
            # 安全:确保 channel_ready=False(message_request 不允许直接 channel ready)
            # —— 主动发送由 C.9.2+ 决定,这里永远 False
            channel_ready = False

            output: Dict[str, Any] = {
                "decision": decision,
                "confidence": float(confidence),
                "priority": float(priority),
                "trigger": str(trigger),
                "reasons": [str(r) for r in reasons],
                "message_request": message_request,
                "channel_ready": channel_ready,
                "timestamp": _safe_str(
                    raw_result.get("timestamp", _now_iso()),
                    _now_iso(),
                ),
                "degraded": bool(degraded),
                "error": _safe_str(raw_result.get("error", ""), ""),
                "source": SOURCE_NAME,
                "schema_version": INITIATIVE_RUNTIME_ADAPTER_SCHEMA_VERSION,
                "user_id": _safe_str(decision_ctx.get("user_id", ""), ""),
                "cycle_id": _safe_str(decision_ctx.get("cycle_id", ""), ""),
                "initiative_id": _safe_str(raw_result.get("initiative_id", ""), ""),
            }
            return output
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c91] _build_output 异常(已隔离): {exc}")
            return _build_degraded_output(repr(exc))

    # --------------------------------------------------------
    # 公开只读访问器
    # --------------------------------------------------------

    @property
    def last_output(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._last_output) if self._last_output else None

    @property
    def last_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._last_snapshot) if self._last_snapshot else None

    @property
    def process_count(self) -> int:
        with self._lock:
            return self._process_count

    @property
    def initiate_count(self) -> int:
        with self._lock:
            return self._initiate_count

    @property
    def defer_count(self) -> int:
        with self._lock:
            return self._defer_count

    @property
    def suppress_count(self) -> int:
        with self._lock:
            return self._suppress_count

    @property
    def degraded_count(self) -> int:
        with self._lock:
            return self._degraded_count

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def get_decision_engine(self) -> Any:
        """获取底层 decision engine(只读引用,调用方不应触发写入方法)。"""
        return self._engine


# ============================================================
# 工厂
# ============================================================


def create_initiative_runtime_adapter(
    decision_engine: Any = None,
    memory_store: Any = None,
    audit: Any = None,
    user_id: str = "yuyi",
) -> InitiativeRuntimeAdapter:
    """工厂函数:创建一个 InitiativeRuntimeAdapter。"""
    return InitiativeRuntimeAdapter(
        decision_engine=decision_engine,
        memory_store=memory_store,
        audit=audit,
        user_id=user_id,
    )


def safe_get_initiative_output(ctx: Any) -> Optional[Dict[str, Any]]:
    """从 RuntimeCycleContext 安全读取 initiative_output。"""
    try:
        if ctx is None:
            return None
        out = getattr(ctx, "initiative_output", None)
        if not isinstance(out, dict):
            return None
        return dict(out)
    except Exception:  # noqa: BLE001
        return None


# ============================================================
# 公共 API
# ============================================================


__all__ = [
    "INITIATIVE_RUNTIME_ADAPTER_SCHEMA_VERSION",
    "INITIATIVE_RUNTIME_ADAPTER_NAME",
    "INITIATIVE_RUNTIME_ADAPTER_VERSION",
    "STANDARD_ADAPTER_INITIATIVE",
    "SOURCE_NAME",
    "DECISION_INITIATE",
    "DECISION_DEFER",
    "DECISION_SUPPRESS",
    "ALL_DECISIONS",
    "AUDIT_ACTION_RUNTIME_EVALUATED",
    "AUDIT_ACTION_RUNTIME_SUPPRESSED",
    "AUDIT_ACTION_RUNTIME_INITIATED",
    "AUDIT_ACTION_RUNTIME_DEFERRED",
    "AUDIT_ACTION_RUNTIME_FAILED",
    "AUDIT_COMPONENT",
    "STAGE_INITIATIVE_EVALUATE",
    "STAGE_INITIATIVE_DEGRADED",
    "STAGE_INITIATIVE_INITIATED",
    "STAGE_INITIATIVE_DEFERRED",
    "STAGE_INITIATIVE_SUPPRESSED",
    "ALL_PHASE_C91_STAGES",
    "DEFAULT_ACTOR",
    "InitiativeRuntimeAdapter",
    "create_initiative_runtime_adapter",
    "safe_get_initiative_output",
]
