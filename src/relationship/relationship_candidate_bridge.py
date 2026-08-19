# -*- coding: utf-8 -*-
"""Phase 2.5-D Commit 2: RelationshipCandidateBridge —— MemoryCreatedEvent → 关系候选提案。

事件消费者式接线(与 GrowthPipeline 主动调用解耦,不重复造 EventExtractor):

    MemoryCreatedEvent(记忆已落库)
        ↓  EventBus(MEMORY_CREATED)
    relationship_candidate_handler(事件消费者,fail-soft)
        ↓  构建评估记录(user_id/memory_id/content/timestamp —— 证据链完整)
        ↓  RelationshipCoreEvaluator(2.5-C 纯规则引擎,无 LLM)
        ↓  候选且分数达标 + 去重(source_memory_ids 不重叠)
        ↓  生成 RelationshipProposal(candidate → evaluating → pending_review)
        ↓  RelationshipProposalStore(append-only)

系统侧永远只能到达 pending_review;accepted / activated 只能由人工治理
端点达成(见 relationship_activation.py)。未来 QQ 机器人 / 网页 / 语音 /
游戏等任何入口,只要发布 MemoryCreatedEvent,关系候选提取自动生效。

治理边界:
- 绝不写 RelationshipCore、绝不触碰 personality / emotion_state /
  relationship_state / memory_schema;
- 任何异常完全隔离:handler 抛错不影响事件发布方与其他订阅者;
- 不新建 EventExtractor:事件本身即携带提取所需的证据链。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from src.events.events import EventType
from src.relationship.relationship_core_evaluator import (
    CATEGORY_CANDIDATE,
    RelationshipCoreEvaluator,
)
from src.relationship.relationship_proposal import PROPOSAL_STATUS, RelationshipProposal
from src.relationship.relationship_proposal_store import RelationshipProposalStore

logger = logging.getLogger(__name__)


class RelationshipCandidateBridge:
    """把 MemoryCreatedEvent 转换为「待人工审核」的关系核心候选提案。"""

    def __init__(
        self,
        proposal_store: Optional[RelationshipProposalStore] = None,
        evaluator: Optional[RelationshipCoreEvaluator] = None,
        min_score: float = 0.5,
    ):
        self.proposal_store = proposal_store or RelationshipProposalStore()
        self.evaluator = evaluator or RelationshipCoreEvaluator()
        try:
            self.min_score = float(min_score)
        except (TypeError, ValueError):
            self.min_score = 0.5

    # ------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------

    def on_memory_created(self, event: Any) -> Optional[Dict[str, Any]]:
        """消费一条 MemoryCreatedEvent;返回提案结果或 None(跳过/异常)。

        任何异常都在此内部消化,绝不向 EventBus 抛出。
        """
        try:
            if getattr(event, "event_type", None) != EventType.MEMORY_CREATED:
                return None
            memory_id = str(getattr(event, "memory_id", "") or "")
            user_id = str(getattr(event, "user_id", "") or "")
            content = str(getattr(event, "content", "") or "")
            if not memory_id or not content:
                return None

            record = {
                "user_id": user_id,
                "memory_id": memory_id,
                "content": content,
                "timestamp": str(getattr(event, "timestamp", "") or ""),
            }
            evaluated = self.evaluator.evaluate(record)
            if evaluated.get("category") != CATEGORY_CANDIDATE:
                return None
            try:
                score = float(evaluated.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            if score < self.min_score:
                return None
            if self._already_proposed(memory_id):
                return None

            proposal = RelationshipProposal(
                source_memory_ids=[memory_id],
                source_user_id=user_id,
                category=CATEGORY_CANDIDATE,
                score=score,
                reason=str(evaluated.get("reason") or ""),
            )
            if not proposal.transition(
                PROPOSAL_STATUS["EVALUATING"], actor="evaluator", reason="事件驱动候选评估",
            ):
                return None
            if not proposal.transition(
                PROPOSAL_STATUS["PENDING_REVIEW"],
                actor="evaluator",
                reason="提交人工审核(系统侧永不超过此状态)",
            ):
                return None
            if not self.proposal_store.save(proposal):
                return {
                    "ok": False,
                    "proposal_id": proposal.proposal_id,
                    "memory_id": memory_id,
                    "error": "提案保存失败",
                }
            return {
                "ok": True,
                "proposal_id": proposal.proposal_id,
                "status": proposal.status,
                "memory_id": memory_id,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("关系候选桥接处理失败(已隔离): %s", exc)
            return None

    def _already_proposed(self, memory_id: str) -> bool:
        """同一记忆已存在提案(任意状态)时不再重复提议。

        去重覆盖终态:被人工驳回的记忆不会被自动重新提交——
        驳回是终局治理决定,重新提议需要新的人工/评估输入。
        """
        try:
            for data in self.proposal_store.list(limit=2000):
                ids = data.get("source_memory_ids") or []
                if memory_id in [str(item) for item in ids]:
                    return True
        except Exception:  # noqa: BLE001
            return False
        return False

    # ------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------

    def subscribe(self, bus: Any = None) -> "RelationshipCandidateBridge":
        try:
            from src.events.bus import get_event_bus
            bus = bus or get_event_bus()
            bus.subscribe(EventType.MEMORY_CREATED, self.on_memory_created)
        except Exception as exc:  # noqa: BLE001
            logger.debug("桥接订阅失败(已隔离): %s", exc)
        return self

    def unsubscribe(self, bus: Any = None) -> "RelationshipCandidateBridge":
        try:
            from src.events.bus import get_event_bus
            bus = bus or get_event_bus()
            bus.unsubscribe(EventType.MEMORY_CREATED, self.on_memory_created)
        except Exception:  # noqa: BLE001
            pass
        return self


# ===================================================
# 模块级单例与总线 handler(生产接线)
# ===================================================

_default_bridge: Optional[RelationshipCandidateBridge] = None
_default_bridge_lock = threading.Lock()


def get_relationship_candidate_bridge() -> RelationshipCandidateBridge:
    """惰性单例(默认存储路径);构造只读索引,不产生任何写入。"""
    global _default_bridge
    if _default_bridge is None:
        with _default_bridge_lock:
            if _default_bridge is None:
                _default_bridge = RelationshipCandidateBridge()
    return _default_bridge


def relationship_candidate_handler(event: Any) -> Optional[Dict[str, Any]]:
    """EventBus 面向的模块级 handler(函数对象唯一 → 订阅天然去重)。

    异常完全隔离:桥接失败绝不影响发布方与其他订阅者。
    """
    try:
        return get_relationship_candidate_bridge().on_memory_created(event)
    except Exception as exc:  # noqa: BLE001
        logger.debug("关系候选 handler 失败(已隔离): %s", exc)
        return None


def ensure_relationship_candidate_bridge(
    bus: Any = None,
) -> RelationshipCandidateBridge:
    """幂等订阅到全局 EventBus;返回单例桥接。

    订阅模块级 handler(函数对象唯一 → 重复 ensure 不会重复注册),
    与桥接实例的绑定方法订阅(测试用)严格区分。
    """
    try:
        from src.events.bus import get_event_bus
        bus = bus or get_event_bus()
        bus.subscribe(EventType.MEMORY_CREATED, relationship_candidate_handler)
    except Exception as exc:  # noqa: BLE001
        logger.debug("桥接订阅失败(已隔离): %s", exc)
    return get_relationship_candidate_bridge()


__all__ = [
    "RelationshipCandidateBridge",
    "get_relationship_candidate_bridge",
    "relationship_candidate_handler",
    "ensure_relationship_candidate_bridge",
]
