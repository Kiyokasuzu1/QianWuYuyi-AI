"""
关系事件提取器 (RelationshipEventExtractor)
从聊天内容中识别可能影响关系认知的事件。
只声明潜在影响维度和信号强度，不计算变化量、不修改状态。
"""
import hashlib
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
        从单条用户消息中提取关系事件。
        如果消息不包含关系信号或关系主体，返回 None。
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
                    event_id=self._generate_event_id(user_message),
                    event_type=pattern["event_type"],
                    evidence_ids=[evidence_id] if evidence_id else [],
                    signal_strength=self._calculate_signal_strength(matched, pattern["min_keywords"]),
                    potential_dimensions=pattern["potential_dimensions"],
                    description=user_message[:120],
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