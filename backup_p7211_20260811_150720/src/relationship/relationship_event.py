"""
Phase 4.0 — R2.5.2-B: RelationshipEvent（关系事件定义）

只定义「发生了一件关系相关的事件」的形状。
绝对不：
  - 把数值写入 bond / trust / familiarity / promise / shared_history
  - 做 content 关键词猜测（只吃 metadata.relationship_signal + meaning 结构化信号）
  - 输出到 prompt 或影响回复（由未来 RelationshipIntelligenceEngine 消费本模块后再决定）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional, TypedDict
import uuid


# R2.5.2-B 唯一允许的事件状态（与 3 条红线对齐：只记录 observed，从不 applied）
RelationshipEventStatus = Literal["observed"]

# R2.5.2-B 允许的事件类型枚举（冻结；想加类型必须先过 Gate）
RelationshipEventType = Literal[
    "promise",          # 用户向羽依做出的承诺 / 或反过来羽依记住用户承诺
    "declaration",      # 关系宣言（如 "羽依你对我很重要"），非关键词
    "milestone",        # 关系里程碑（共同经历 N 次互动等结构化记录，不是关键词）
    "support",          # 用户提供支持 / 羽依对用户的支持记录（结构化 signal）
    "boundary",         # 用户设定的关系边界（例如 "不要聊这件事"，结构化 signal）
    "shared_activity",  # 结构化意义为共同活动（future）
    "other",            # 兜底：meaning category 匹配但不属于上列
]


# ============================================================
# 冻结字段：RelationshipEvent
# 与用户给的 JSON 示例对齐（字段顺序/名称固定，Gate 里做 exact check）
# ============================================================

class RelationshipEvent(TypedDict, total=False):
    """关系事件（观察级，append-only）。

    字段来源：
      id:               rel_evt_{hex12}（内部生成）
      type:             枚举 RelationshipEventType（from meaning / memory_type 结构化推断）
      content:          原 memory.content 的原始文本（只读存档，Gate 测试里不作为判断依据）
      source_memory_id: 原 memory.id（强溯源）
      confidence:       0~1；Bridge 侧不做，由 meaning_resolver 或 memory.metadata 继承
      created_at:       ISO 字符串，UTC
      status:           "observed"（R2.5.2-B 唯一合法值）
      meaning:          memory.metadata.meaning（如果有）— 供未来 IE 消费
      memory_type:      memory.metadata.memory_type（如果有）
      user_id:          原 memory.user_id（事件归属）
      participants:     list[str]，参与方（R2.5.2-B 默认 ["user", "yuyi"]；结构化 signal 才可改写）
    """
    id: str
    type: str
    content: str
    source_memory_id: str
    confidence: float
    created_at: str
    status: RelationshipEventStatus
    meaning: Optional[str]
    memory_type: Optional[str]
    user_id: str
    participants: list


# 冻结键名（Gate B-1 用来强制 exact check）
RELATIONSHIP_EVENT_FROZEN_KEYS: tuple = (
    "id",
    "type",
    "content",
    "source_memory_id",
    "confidence",
    "created_at",
    "status",
    "meaning",
    "memory_type",
    "user_id",
    "participants",
)

RELATIONSHIP_EVENT_ALLOWED_TYPES: tuple = (
    "promise", "declaration", "milestone", "support",
    "boundary", "shared_activity", "other",
)

RELATIONSHIP_EVENT_ALLOWED_STATUS: tuple = ("observed",)


# ============================================================
# 构造：从结构化 relationship record → RelationshipEvent
# 红线：绝不看 content 关键词。只依赖 metadata.meaning / metadata.memory_type /
#       metadata.relationship_signal（都是上游 Bridge 判定过的）
# ============================================================

def build_relationship_event_from_record(record: Dict[str, Any]) -> RelationshipEvent:
    """**只由 ExperienceBridge 调用（因为 Bridge 已确认结构化 relationship 信号）。**

    绝对禁止在此函数里：
      - re.search(content, "喜欢|爱|永远|陪")
      - 任何关键词匹配
    """
    md = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    memory_type = str(md.get("memory_type") or record.get("memory_type") or "")
    meaning = str(md.get("meaning") or "")

    # type 推断（纯结构化 meaning / memory_type 字面量匹配；不扫描 content）
    ev_type = _derive_event_type(memory_type=memory_type, meaning=meaning)

    ev_id = f"rel_evt_{uuid.uuid4().hex[:12]}"
    confidence_raw = (
        record.get("importance")
        if isinstance(record.get("importance"), (int, float))
        else md.get("confidence", 0.6)
    )
    try:
        confidence_f = float(confidence_raw or 0.6)
    except Exception:  # noqa: BLE001
        confidence_f = 0.6
    confidence_f = min(max(confidence_f, 0.0), 1.0)

    participants = ["user", "yuyi"]
    if isinstance(md.get("participants"), list) and md["participants"]:
        participants = [str(x) for x in md["participants"] if x] or participants

    ev: RelationshipEvent = {
        "id": ev_id,
        "type": ev_type,
        "content": str(record.get("content") or ""),
        "source_memory_id": str(record.get("id") or ""),
        "confidence": confidence_f,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "observed",
        "meaning": meaning or None,
        "memory_type": memory_type or None,
        "user_id": str(record.get("user_id") or ""),
        "participants": participants,
    }
    return ev


def _derive_event_type(*, memory_type: str, meaning: str) -> str:
    """只靠 memory_type / meaning 字面量前缀匹配出 event type。绝不看 content."""
    m_lower = (meaning or "").lower()
    mt_lower = (memory_type or "").lower()
    # 1. 靠 meaning 后缀更精确（例如 relationship_promise → promise）
    for key in ("promise", "declaration", "milestone", "support", "boundary", "shared_activity"):
        if key in m_lower or key in mt_lower:
            return key
    # 2. relationship 大类但没明确子类型 → other
    if "relationship" in mt_lower or "relationship" in m_lower:
        return "other"
    # 3. 兜底（理论上 Bridge 已经保证 relationship 结构化信号，不会走到这里）
    return "other"
