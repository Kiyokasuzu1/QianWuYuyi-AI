"""
成长累积器 (GrowthAccumulator) v1.1

职责：
将所有 GrowthRecord 累积计算为当前人格成长偏移量。

v1.1 更新：
- 增加 calculate_activity 时间活跃度机制（旧记录影响自然衰减）
- 统一类型提示为 GrowthRecord
"""

from typing import Dict, List
from math import tanh
from datetime import datetime

from src.growth.growth_record import GrowthRecord
from src.personality.traits import PERSONALITY_DIMENSIONS


class GrowthAccumulator:

    EVENT_TYPE_WEIGHT = {
        "creation": 1.5,
        "milestone": 1.3,
        "identity": 1.2,
        "preference": 1.0,
        "relationship": 0.0,
        "growth_support": 0.8,
    }

    TANH_SCALE = 3.0
    GROWTH_SCALE = 0.2

    def calculate_activity(self, record: GrowthRecord) -> float:
        """
        计算记录的时间活跃度。
        刚创建时 activity ≈ 1，随时间自然衰减。
        使用 1/(1 + days/365) 公式，一年后约 0.5，五年后约 0.16。
        注意：这是记忆活跃度，不是人格影响衰减。
        人格一旦形成，不会因为时间而消失。
        """
        created_at = record.get("created_at", "")
        if not created_at:
            return 1.0

        try:
            created_date = datetime.fromisoformat(created_at[:10])
            days = (datetime.now() - created_date).days
            return round(1.0 / (1.0 + days / 365.0), 4)
        except (ValueError, TypeError):
            return 1.0

    def accumulate(self, records: List[GrowthRecord]) -> Dict[str, float]:
        raw_growth: Dict[str, float] = {}

        for record in records:
            affected = record.get("affected_dimensions", {})
            confidence = record.get("confidence", 0.5)
            source_type = record.get("source_type", "preference")
            event_weight = self.EVENT_TYPE_WEIGHT.get(source_type, 1.0)

            if event_weight == 0.0:
                continue

            activity = self.calculate_activity(record)

            for dim, delta in affected.items():
                contribution = delta * confidence * event_weight * activity
                raw_growth[dim] = raw_growth.get(dim, 0.0) + contribution

        return raw_growth

    def compress(self, raw_growth: Dict[str, float]) -> Dict[str, float]:
        compressed = {}
        for dim, value in raw_growth.items():
            compressed[dim] = round(
                tanh(value * self.TANH_SCALE) * self.GROWTH_SCALE, 4
            )
        return compressed

    def apply_limits(self, growth: Dict[str, float]) -> Dict[str, float]:
        clamped = {}
        for dim, value in growth.items():
            dim_config = PERSONALITY_DIMENSIONS.get(dim, {})
            low, high = dim_config.get("range", (0.0, 1.0))
            clamped[dim] = round(max(low, min(high, value)), 4)
        return clamped

    def compute(
        self,
        records: List[GrowthRecord],
        base_personality: Dict[str, float],
    ) -> Dict[str, float]:
        raw = self.accumulate(records)
        compressed = self.compress(raw)

        result = {}
        for dim, base_value in base_personality.items():
            growth_value = compressed.get(dim, 0.0)
            result[dim] = round(base_value + growth_value, 4)

        return self.apply_limits(result)