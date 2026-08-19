"""
关系事件提取器 (RelationshipEventExtractor)
从聊天内容中识别可能影响关系认知的事件。
只声明潜在影响维度和信号强度，不计算变化量、不修改状态。
"""
import hashlib
from datetime import datetime, timezone
from typing import List, Optional
from src.relationship.relationship_event import RelationshipEvent


class RelationshipEventExtractor:
    # 关系相关关键词与对应的事件类型和潜在维度
    RELATION_PATTERNS = [
        {
            "keywords": ["一起", "共同", "合作", "开发", "设计", "项目", "半年", "长期", "一直"],
            "event_type": "collaboration",
            "potential_dimensions": {"collaboration", "trust", "familiarity"},
            "min_keywords": 2,
        },
        {
            "keywords": ["相信", "信任", "放心", "可靠", "稳定"],
            "event_type": "trust_building",
            "potential_dimensions": {"trust"},
            "min_keywords": 1,
        },
        {
            "keywords": ["尊重", "理解", "接受", "边界", "不勉强"],
            "event_type": "boundary_respect",
            "potential_dimensions": {"trust"},
            "min_keywords": 1,
        },
        {
            "keywords": ["习惯", "偏好", "了解", "知道你喜欢", "你通常"],
            "event_type": "preference_learning",
            "potential_dimensions": {"familiarity"},
            "min_keywords": 1,
        },
    ]

    # 关系主体词：消息中必须包含至少一个，才可能触发关系事件
    RELATION_TARGET_WORDS = ["你", "羽依", "我们", "一起", "你帮我"]

    def extract(self, user_message: str, evidence_id: str = "") -> Optional[RelationshipEvent]:
        """
        从单条用户消息中提取关系事件（统一 schema 的 dict）。
        如果消息不包含关系信号或关系主体，返回 None。

        Phase 4.2-A：产出与新 R2.5.2-B 契约统一的 RelationshipEvent
        （11 个冻结键 + 2 个提取扩展键 evidence_ids/potential_dimensions），
        不再是旧 dataclass——TypedDict 是唯一事实来源。
        """
        if not user_message or len(user_message.strip()) < 3:
            return None

        # 必须包含关系主体词
        if not any(w in user_message for w in self.RELATION_TARGET_WORDS):
            return None

        # 检查是否匹配关系模式
        for pattern in self.RELATION_PATTERNS:
            matched = [kw for kw in pattern["keywords"] if kw in user_message]
            if len(matched) >= pattern["min_keywords"]:
                return RelationshipEvent(
                    id=self._generate_event_id(user_message),
                    type=pattern["event_type"],
                    content=user_message[:120],
                    source_memory_id=evidence_id or "",
                    confidence=self._calculate_signal_strength(matched, pattern["min_keywords"]),
                    created_at=datetime.now(timezone.utc).isoformat(),
                    status="observed",
                    meaning=None,
                    memory_type=None,
                    user_id="",
                    participants=["user", "yuyi"],
                    evidence_ids=[evidence_id] if evidence_id else [],
                    potential_dimensions=sorted(pattern["potential_dimensions"]),
                )

        return None

    def _generate_event_id(self, text: str) -> str:
        """使用 MD5 生成稳定的事件 ID"""
        digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
        return f"rel_evt_{digest}"

    def _calculate_signal_strength(self, matched: List[str], min_required: int) -> float:
        """根据命中关键词数量计算信号强度（非最终置信度）"""
        ratio = len(matched) / max(min_required, 1)
        return round(min(0.9, 0.4 + ratio * 0.3), 2)