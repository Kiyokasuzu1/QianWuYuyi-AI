"""
Phase 4.0 R2.5.1: ExperienceBridge

定位：Runtime 层的「经历分诊台」(Event Router)，不是任何一个子系统的业务器官。

MemoryCreatedEvent
        |
        v

ExperienceBridge.process(record)
        |
        +-- RouteDecision.classified_to
        +-- [audit_event]: ExperienceRouteDecisionCreated
        +-- 执行通道(仅当 classified_to != memory_only)
               |
               +-- relationship_memory: 只写 audit (R2.5.1 不立即影响 RelationshipState)
               +-- growth_candidate:    GrowthIntegrationService.accept_experience(record)

设计红线(Phase 4.0-R2.5.1 不得违反):
  1. ExperienceBridge 绝不直接实例化/调用 GrowthEvaluator / Normalizer / Validator /
     ProposalManager / Matcher。这些全部由 GrowthIntegrationService 自己封装。
     Bridge 只做 "这条经历该去哪里" 的分流判定。
  2. GrowthCandidate 入口必须保守:
       importance >= 0.65 (用户 Review 把原 0.45 提高)
       AND memory_type ∈ GC_ALLOWLIST
     缺结构化 memory_type 时，使用更严格的 importance >= 0.75 作为 fallback，
     绝不做关键词 "喜欢/爱/陪" 这种字符串猜测。
  3. RelationshipMemory 判定只认结构化 signal:
       metadata.relationship_signal = True
       或 memory_type ∈ RELATIONSHIP_TYPES
     绝对禁止 content 关键词匹配。
  4. 所有 RouteDecision 必须先发 AuditEvent (ExperienceRouteDecisionCreated)
     再执行下游。"为什么她变成这样?" 必须可追踪。
  5. Bridge 内部异常 -> 降级 memory_only，Runtime 绝不 crash。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Optional

from src.experience.route_decision import (
    ExperienceRouteDecision,
    RouteClassification,
    emit_route_audit,
)

logger = logging.getLogger(__name__)

# ============================================================
# R2.5.1 路由参数（均为 Review 最终确认值）
# ============================================================

# GrowthCandidate importance 阈值（Bridge 粗过滤，不是最后一道）
GC_IMPORTANCE_THRESHOLD: float = 0.65
# 没有 memory_type 结构化信号时用更严格的 fallback 阈值
GC_STRICT_FALLBACK_THRESHOLD: float = 0.75

# memory_type 白名单（允许进入 GrowthCandidate）
# 与 memory.pollution_guard.ALLOWED_TYPES + consolidation 名字对齐
GC_ALLOWLIST: frozenset = frozenset({
    # user_* 系列（Runtime 真实写入）
    "user_preference",
    "user_milestone",
    "user_fact",
    "user_event",
    "user_experience",
    "user_goal",
    # consolidation 系列
    "preference",
    "growth",
    "growth_memory",
    "identity",
    "milestone",
    "creation",
    "life_event",
    "semantic",
})

# memory_type 黑名单（绝对不进 GrowthCandidate，即便 importance 高）
GC_BLOCKLIST: frozenset = frozenset({
    "conversation",
    "daily_activity",
    "small_talk",
    "food",
    "weather",
    "user_emotion",   # 情绪快照不直接入成长（情绪单独系统后再接入）
    "emotional",      # consolidation: emotional
})

# RelationshipMemory 记忆类型（结构化信号）
RELATIONSHIP_TYPES: frozenset = frozenset({
    "relationship",
    "relationship_commitment",
    "relationship_memory",
})


# ============================================================
# 工具函数：从 record 中取结构化信号
# ============================================================

def _metadata_of(record: Dict[str, Any]) -> Dict[str, Any]:
    md = record.get("metadata")
    if isinstance(md, dict):
        return md
    return {}


def _read_memory_type(record: Dict[str, Any]) -> str:
    """读 memory_type：metadata.memory_type -> record.memory_type -> metadata.type -> record.type -> ''"""
    md = _metadata_of(record)
    for key in ("memory_type", "type"):
        for container in (md, record):
            v = container.get(key) if isinstance(container, dict) else None
            if v and isinstance(v, str):
                t = v.lower().strip()
                if t:
                    return t
    return ""


def _has_structured_relationship_signal(record: Dict[str, Any]) -> bool:
    """RelationshipMemory 判定：只吃结构化 signal，不做 content 关键词。

    允许的 signal:
      1. memory_type in RELATIONSHIP_TYPES
      2. metadata.relationship_signal 是 True / "true" / "1"
      3. metadata.signals.relationship is True
      4. metadata.meaning 以 "relationship_" 开头
    """
    if _read_memory_type(record) in RELATIONSHIP_TYPES:
        return True
    md = _metadata_of(record)

    # metadata.relationship_signal
    s = md.get("relationship_signal")
    if isinstance(s, bool):
        if s:
            return True
    elif isinstance(s, (int, str)):
        if str(s).strip().lower() in {"1", "true", "yes", "y", "t"}:
            return True

    # metadata.signals.relationship
    sigs = md.get("signals")
    if isinstance(sigs, dict):
        ss = sigs.get("relationship")
        if isinstance(ss, bool) and ss:
            return True
        if isinstance(ss, (int, str)) and str(ss).strip().lower() in {"1", "true", "yes", "y", "t"}:
            return True

    # metadata.meaning (来自 meaning_resolver / MemoryExtractor 未来产物)
    meaning = md.get("meaning") or md.get("category_id") or record.get("meaning") or ""
    meaning_str = str(meaning).strip().lower()
    if meaning_str.startswith("relationship"):
        return True

    return False


def _gc_qualified(record: Dict[str, Any]) -> tuple[bool, str]:
    """GrowthCandidate 资格判断。返回 (是否通过, 触发规则名)。

    R2.5.1 规则顺序（命中即返回，避免后续误判）:
      GC_BLOCKLIST:    直接拒绝
      GC_ALLOWLIST + importance >= 0.65: 通过
      无 memory_type 但 importance >= 0.75: 通过 (strict fallback)
      其他: 不通过
    """
    importance = float(record.get("importance") or 0.0)
    memory_type = _read_memory_type(record)

    # Rule 1: 类型黑名单
    if memory_type and memory_type in GC_BLOCKLIST:
        return False, "GC_MEMORY_TYPE_BLOCKLIST"

    # Rule 2: 类型白名单 + 标准阈值
    if memory_type and memory_type in GC_ALLOWLIST:
        if importance >= GC_IMPORTANCE_THRESHOLD:
            return True, "GC_ALLOWLIST_AND_IMPORTANCE"
        return False, "GC_IMPORTANCE_BELOW_THRESHOLD"

    # Rule 3: 缺 memory_type 的严格 fallback
    if not memory_type:
        if importance >= GC_STRICT_FALLBACK_THRESHOLD:
            return True, "GC_STRICT_FALLBACK_HIGH_IMPORTANCE"
        return False, "GC_NO_SIGNAL_AND_IMPORTANCE_LOW"

    return False, "GC_MEMORY_TYPE_NOT_IN_ALLOWLIST"


# ============================================================
# ExperienceBridge
# ============================================================

class ExperienceBridge:
    """经历分诊台。单例轻对象，Runtime 可以反复 new。"""

    # ---- 构造（允许依赖注入，方便测试；默认 Production 懒调用 GrowthIntegrationService.accept_experience_static）----
    def __init__(
        self,
        *,
        growth_handler: Optional[Any] = None,
        record_lookup: Optional[Any] = None,
    ) -> None:
        """
        Args:
            growth_handler: 可 callable(record)->dict，替代 GrowthIntegrationService.accept_experience_static。
                            仅用于单元测试，Production 传 None 走默认。
            record_lookup:  可 callable(memory_id)->Optional[dict]，当 Bridge 只收到 memory_id
                            时用它去 MemoryStore 取完整 record。R2.5.1 handlers.py 传入。
        """
        self._growth_handler = growth_handler
        self._record_lookup = record_lookup

    # ============================================================
    # 入口 1: process(record_or_id)
    # 由 MemoryCreatedEventHandler 调用
    # ============================================================
    def process(
        self,
        record_or_id: Any,
        *,
        user_id: Optional[str] = None,
    ) -> ExperienceRouteDecision:
        t_start = time.perf_counter()
        decision_id = f"rd_{uuid.uuid4().hex[:12]}"
        try:
            record: Dict[str, Any] = self._materialize_record(record_or_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ExperienceBridge] materialize_record 失败: %s", exc)
            decision: ExperienceRouteDecision = {
                "decision_id": decision_id,
                "memory_id": str(record_or_id),
                "user_id": user_id or "",
                "classified_to": "memory_only",
                "rule_triggered": "EB_MATERIALIZE_FAILED_FALLBACK_MEMORY_ONLY",
                "timestamp_iso": self._now_iso(),
                "duration_ms": self._elapsed_ms(t_start),
                "error": f"materialize failed: {exc!r}",
                "route_result": None,
            }
            emit_route_audit(decision)
            return decision

        memory_id = str(record.get("id") or "")
        final_user_id = user_id or str(record.get("user_id") or "")

        # ---- 防御性前置过滤：不是 user 角色的记忆，不走 Bridge 下游 ----
        role = str(record.get("role") or "").strip().lower()
        if role and role != "user":
            # assistant/tool/system 记忆永远只做 memory_only
            decision = self._finish(
                decision_id, memory_id, final_user_id,
                classified_to="memory_only",
                rule="EB_NON_USER_ROLE_MEMORY_ONLY",
                t_start=t_start,
                route_result=None,
                error=None,
            )
            emit_route_audit(decision)
            return decision

        # ---- 空内容防御：直接 memory_only ----
        content = str(record.get("content") or "").strip()
        if not content:
            decision = self._finish(
                decision_id, memory_id, final_user_id,
                classified_to="memory_only",
                rule="EB_EMPTY_CONTENT_MEMORY_ONLY",
                t_start=t_start,
                route_result=None,
                error=None,
            )
            emit_route_audit(decision)
            return decision

        # ============================================================
        # R2.5.1 路由顺序（互斥）：
        #   1. RelationshipMemory 结构化信号 -> relationship_memory
        #   2. GrowthCandidate 保守判定       -> growth_candidate
        #   3. 其他                           -> memory_only
        # ============================================================
        classified_to: RouteClassification
        rule: str
        route_result: Optional[Dict[str, Any]] = None

        if _has_structured_relationship_signal(record):
            classified_to = "relationship_memory"
            rule = "RELATIONSHIP_STRUCTURED_SIGNAL"
        else:
            gc_ok, gc_rule = _gc_qualified(record)
            if gc_ok:
                classified_to = "growth_candidate"
                rule = gc_rule
            else:
                classified_to = "memory_only"
                rule = gc_rule  # 直接把 GC 未通过的理由作为审计规则

        # ---- 先 Audit，再执行下游 ----
        pre_decision = self._finish(
            decision_id, memory_id, final_user_id,
            classified_to=classified_to,
            rule=rule,
            t_start=t_start,
            route_result=None,
            error=None,
        )
        emit_route_audit(pre_decision)

        # ---- 通道执行 ----
        if classified_to == "growth_candidate":
            route_result = self._route_growth(record)
        elif classified_to == "relationship_memory":
            # Phase 4.0-R2.5.2-B:
            #   audit + append to RelationshipEventStore (append-only observed events)
            # 红线（R2.5.2-B 冻结）：
            #   - 只 append 事件，不写 bond/trust/familiarity/promise/shared_history
            #   - 不做 content 关键词（已在 Bridge 判定结构化 signal 后才到这里）
            #   - 不输出到 prompt（future RelationshipContextProvider 再决定）
            rel_event: Dict[str, Any] | None = None
            rel_error: str | None = None
            try:
                from src.relationship.relationship_memory import RelationshipEventStore
                store = RelationshipEventStore.default()
                rel = store.append_record(record)
                rel_event = dict(rel)
            except Exception as exc:  # noqa: BLE001
                # 红线 5 级保护：失败只影响 audit 字段，不降 classified_to
                try:
                    rel_error = f"{type(exc).__name__}: {exc}"
                    logging.getLogger(__name__).warning(
                        "[ExperienceBridge] relationship_memory append 失败(已隔离): %s",
                        exc,
                    )
                except Exception:  # noqa: BLE001
                    rel_error = "append_error_isolated"
            route_result = {
                "channel": "relationship_memory",
                "action": (
                    "audit_and_appended_r252b" if rel_event is not None
                    else "audit_only_append_failed_r252b"
                ),
                "relationship_event_id": rel_event.get("id") if rel_event else None,
                "relationship_event_type": rel_event.get("type") if rel_event else None,
                "append_error": rel_error,
            }
        else:  # memory_only
            route_result = {"channel": "memory_only", "action": "noop"}

        final_decision = self._finish(
            decision_id, memory_id, final_user_id,
            classified_to=classified_to,
            rule=rule,
            t_start=t_start,
            route_result=route_result,
            error=None,
        )
        # 再发一次 final（包含 route_result），便于审计回溯完整结果
        emit_route_audit(final_decision)
        return final_decision

    # ============================================================
    # 入口 2: 便捷静态工厂（测试友好）
    # ============================================================
    @staticmethod
    def route(
        record_or_id: Any,
        *,
        user_id: Optional[str] = None,
        growth_handler: Optional[Any] = None,
        record_lookup: Optional[Any] = None,
    ) -> ExperienceRouteDecision:
        bridge = ExperienceBridge(growth_handler=growth_handler, record_lookup=record_lookup)
        return bridge.process(record_or_id, user_id=user_id)

    # ============================================================
    # 内部
    # ============================================================
    def _materialize_record(self, record_or_id: Any) -> Dict[str, Any]:
        """把 record (dict) 或 memory_id (str) 变成完整 record dict。"""
        if isinstance(record_or_id, dict):
            return record_or_id
        if isinstance(record_or_id, str):
            if not self._record_lookup:
                raise RuntimeError(
                    "ExperienceBridge: 收到 memory_id 字符串但没有注入 record_lookup。"
                    "请在 handlers.py 中传入 MemoryStore.get_by_id。"
                )
            rec = self._record_lookup(record_or_id)
            if not isinstance(rec, dict):
                raise RuntimeError(
                    f"ExperienceBridge: record_lookup({record_or_id!r}) 返回值不是 dict，是 {type(rec).__name__}"
                )
            return rec
        raise TypeError(
            f"ExperienceBridge.process 只接受 dict record 或 str memory_id，收到 {type(record_or_id).__name__}"
        )

    def _route_growth(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """growth_candidate 通道：调用 GrowthIntegrationService.accept_experience。

        隔离：任何异常都只记录，不抛到 Runtime。
        """
        try:
            handler = self._growth_handler
            if handler is None:
                from src.growth.growth_integration import GrowthIntegrationService

                # 双重保险：显式传 auto_accept_enabled=False
                result = GrowthIntegrationService.accept_experience_static(
                    record,
                    auto_accept_enabled=False,
                    confidence_threshold=0.8,
                )
            else:
                result = handler(record)
            return {
                "channel": "growth_candidate",
                "action": "dispatched_to_growth_integration_service",
                "growth_result": result,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ExperienceBridge] growth_handler 失败(已隔离): %s", exc)
            return {
                "channel": "growth_candidate",
                "action": "growth_handler_failed_isolated",
                "error": f"{exc!r}",
            }

    @staticmethod
    def _finish(
        decision_id: str,
        memory_id: str,
        user_id: str,
        *,
        classified_to: RouteClassification,
        rule: str,
        t_start: float,
        route_result: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> ExperienceRouteDecision:
        return {
            "decision_id": decision_id,
            "memory_id": memory_id,
            "user_id": user_id,
            "classified_to": classified_to,
            "rule_triggered": rule,
            "timestamp_iso": ExperienceBridge._now_iso(),
            "duration_ms": ExperienceBridge._elapsed_ms(t_start),
            "route_result": route_result,
            "error": error,
        }

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _elapsed_ms(t_start: float) -> int:
        return int((time.perf_counter() - t_start) * 1000)
