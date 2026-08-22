# -*- coding: utf-8 -*-
"""
情绪-记忆权重桥 (EmotionMemoryWeightBridge) — Emotion System 2.0（E-Emotion-4）

职责：
- 由当前情绪状态推导 emotion_weight（情绪越强权重越高）
- 将权重应用到记忆条目：只影响 importance / retrieval score / emotional_tags
- 普通事件 importance += 0；高情绪事件 importance += emotion_weight

红线（任务书 E-Emotion-4）：
- 禁止修改历史记忆内容（content/text/role 等字段一律不动）
- 只允许写入/更新：importance、emotional_tags、retrieval 相关计分字段
- 输出新 dict（或显式声明的浅拷贝），不原地改写调用方持有的记忆对象
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.emotion.emotion_state import EmotionState

# 情绪强度门槛：state.intensity >= 该值才产生权重
HIGH_EMOTION_THRESHOLD = 0.5
# 权重上限（importance 为 0~1，单次加权封顶）
MAX_EMOTION_WEIGHT = 0.3
# 允许修改的记忆字段白名单（E-Emotion-4 红线：内容字段不在名单内）
MUTABLE_FIELDS = frozenset({"importance", "emotional_tags", "retrieval_score"})


class EmotionMemoryWeightBridge:
    """情绪 → 记忆权重转换与安全应用。"""

    def __init__(
        self,
        threshold: float = HIGH_EMOTION_THRESHOLD,
        max_weight: float = MAX_EMOTION_WEIGHT,
    ) -> None:
        self.threshold = threshold
        self.max_weight = max_weight

    # --------------------------------------------------------
    # 权重计算
    # --------------------------------------------------------
    def compute_weight(self, state: EmotionState) -> float:
        """普通情绪 → 0；高情绪 → 按强度缩放（0 ~ max_weight）。"""
        if state is None:
            return 0.0
        intensity = float(getattr(state, "intensity", 0.0) or 0.0)
        if intensity < self.threshold:
            return 0.0
        ratio = (intensity - self.threshold) / (1.0 - self.threshold)
        return round(min(self.max_weight, self.max_weight * ratio), 4)

    def dominant_tag(self, state: EmotionState) -> Optional[str]:
        """主导情绪 → 记忆标签（情绪标签不影响内容语义）。"""
        if state is None:
            return None
        dominant = getattr(state, "dominant", "neutral")
        tag_map = {
            "joyful": "joy",
            "positive": "joy",
            "serene": "calm",
            "anxious": "anxiety",
            "tense": "anxiety",
            "uneasy": "uneasy",
            "flat": "flat",
        }
        return tag_map.get(dominant, dominant if dominant != "neutral" else None)

    # --------------------------------------------------------
    # 安全应用
    # --------------------------------------------------------
    def apply_to_memory(
        self,
        memory: Dict[str, Any],
        state: EmotionState,
        *,
        add_tag: bool = True,
    ) -> Dict[str, Any]:
        """把情绪权重应用到记忆条目，返回新 dict。

        - importance: 原值 + weight（clamp 0~1；无原值视为 0）
        - emotional_tags: 追加主导情绪标签（去重）
        - 其余字段（含 content/text）原样保留 —— 绝不修改历史内容
        """
        if not isinstance(memory, dict):
            return memory
        weight = self.compute_weight(state)
        updated = dict(memory)

        # importance 加权
        try:
            base = float(updated.get("importance", 0.0) or 0.0)
        except (TypeError, ValueError):
            base = 0.0
        updated["importance"] = round(max(0.0, min(1.0, base + weight)), 4)

        # 情绪标签
        if add_tag:
            tag = self.dominant_tag(state)
            if tag:
                tags = list(updated.get("emotional_tags") or [])
                if isinstance(tags, list) and tag not in tags:
                    tags.append(tag)
                updated["emotional_tags"] = tags

        return updated

    def apply_to_memories(
        self,
        memories: List[Dict[str, Any]],
        state: EmotionState,
        *,
        add_tag: bool = True,
    ) -> List[Dict[str, Any]]:
        """批量应用（每条独立返回新 dict，不改入参）。"""
        return [
            self.apply_to_memory(m, state, add_tag=add_tag)
            for m in memories
        ]

    # --------------------------------------------------------
    # 审计辅助
    # --------------------------------------------------------
    def diff_fields(self, before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
        """返回实际被修改的字段名（内容字段应永远不在其中）。"""
        keys = set(before.keys()) | set(after.keys())
        return sorted(
            k for k in keys
            if before.get(k) != after.get(k) and k not in ("emotional_tags",)
        )
