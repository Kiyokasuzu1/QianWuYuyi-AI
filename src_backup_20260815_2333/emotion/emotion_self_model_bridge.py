"""
情绪-自我模型桥接层 (EmotionSelfModelBridge)
负责将 EmotionBelief 列表合并到 SelfModelV3 中。
直接修改传入的模型，不返回副本。
"""
from typing import List
from src.emotion.emotion_belief import EmotionBelief
from src.personality.self_model_v3 import SelfModelV3


class EmotionSelfModelBridge:
    def __init__(self, confidence_history_weight: float = 0.7):
        # 旧置信度权重（0~1），新置信度权重为 1 - weight
        self.confidence_history_weight = confidence_history_weight

    def merge(
        self,
        model: SelfModelV3,
        beliefs: List[EmotionBelief],
    ) -> None:
        """
        将情绪信念合并到自我模型中。直接修改 model，不返回新对象。
        """
        existing_beliefs = model.emotional_self_understanding

        for new_belief in beliefs:
            # 防止外部引用污染：通过序列化/反序列化拷贝一份
            copied_belief = EmotionBelief.from_dict(new_belief.to_dict())

            # 查找已存在的同 key 信念
            existing = self._find_existing(existing_beliefs, copied_belief)

            if existing:
                # 更新现有信念
                self._update_existing(existing, copied_belief)
            else:
                # 追加新信念
                existing_beliefs.append(copied_belief)

    def _find_existing(
        self,
        existing_beliefs: List[EmotionBelief],
        new_belief: EmotionBelief,
    ):
        """查找同 key 的已有信念"""
        key = new_belief.get_merge_key()
        for b in existing_beliefs:
            if b.get_merge_key() == key:
                return b
        return None

    def _update_existing(
        self,
        existing: EmotionBelief,
        new_belief: EmotionBelief,
    ) -> None:
        """更新已有信念的置信度、稳定性和证据链"""
        # 加权平均置信度
        w = self.confidence_history_weight
        existing.confidence = round(
            existing.confidence * w + new_belief.confidence * (1 - w), 2
        )

        # 稳定性取最近值
        existing.stability = new_belief.stability

        # 合并证据链（去重、保留顺序）
        seen = set(existing.evidence_trace_ids)
        for tid in new_belief.evidence_trace_ids:
            if tid not in seen:
                existing.evidence_trace_ids.append(tid)
                seen.add(tid)

        # 更新次数和来源
        existing.occurrence_count = max(existing.occurrence_count, new_belief.occurrence_count)
        existing.source_pattern_id = new_belief.source_pattern_id

        # 更新时间
        from datetime import datetime
        existing.updated_at = datetime.now().isoformat()