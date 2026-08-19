"""
演化评估器 (EvolutionEvaluator) v1.2

职责：
审核 ReflectionEngine 产生的 trait_candidates，判断是否
应该成为长期人格的一部分。防止单次对话污染人格。

v1.2 修正：
- 修复 overall_confidence 计算 bug
- 修复时间解析时区兼容
- 增强 TraitState 取值健壮性
- 加强反向冲突判断（负面总量 >= 正面总量时拒绝）
- 增加 rejected_dimensions 输出
- 移除 _check_dimension 未使用的 trait_states 参数
- 调整正负方向判断阈值，避免极小值被忽略
"""

from typing import Dict, List, Optional
from datetime import datetime, timezone
import uuid

from src.personality.evolution_record import EvolutionRecord
from src.personality.personality_growth_record import PersonalityGrowthHistory
from src.personality.trait_state import TraitState


class EvolutionEvaluator:

    MIN_OCCURRENCES = 5
    MIN_AVG_CONFIDENCE = 0.85
    MAX_DAYS_SINCE_LAST = 90
    MIN_NET_DELTA = 0.05
    EPSILON = 0.001  # 用于判断正负变化的最小阈值

    def evaluate(
        self,
        candidates: List[str],
        history: PersonalityGrowthHistory,
        trait_states: Dict[str, TraitState],
        source_reflection_id: Optional[str] = None,
    ) -> EvolutionRecord:
        approved_dimensions = []
        rejected_dimensions = []
        trait_changes = {}
        rejection_reasons = {}
        approved_records = []
        all_source_records = []

        for dim in candidates:
            dim_records = history.get_by_dimension(dim)
            all_source_records.extend(r.get("record_id", "") for r in dim_records)

            check_result = self._check_dimension(dim, dim_records)
            if check_result["approved"]:
                approved_dimensions.append(dim)
                approved_records.extend(dim_records)
                old_value = self._get_trait_value(dim, trait_states)
                new_value = min(1.0, old_value + check_result["delta"])
                trait_changes[dim] = {
                    "before": old_value,
                    "after": new_value,
                    "delta": check_result["delta"],
                }
            else:
                rejected_dimensions.append(dim)
                rejection_reasons[dim] = check_result.get("reason", "unknown")

        overall_confidence = self._calc_confidence(approved_records)
        reason = self._generate_reason(approved_dimensions, rejected_dimensions)

        record: EvolutionRecord = {
            "record_id": f"evo_{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trigger_candidates": candidates,
            "source_reflection_id": source_reflection_id,
            "source_growth_records": list(set(all_source_records)),
            "trait_changes": trait_changes,
            "approved": len(approved_dimensions) > 0,
            "confidence": overall_confidence,
            "decision_reason": reason,
            "rejection_reasons": rejection_reasons,
            "rejected_dimensions": rejected_dimensions,
            "evolution_level": self._determine_level(approved_dimensions),
            "requires_validation": len(approved_dimensions) > 0 and overall_confidence < 0.9,
        }
        return record

    def _get_trait_value(self, dim: str, trait_states: Dict[str, TraitState]) -> float:
        state = trait_states.get(dim)
        if state is None:
            return 0.5
        if isinstance(state, dict):
            return state.get("current_value", 0.5)
        return getattr(state, "current_value", 0.5)

    def _check_dimension(self, dim: str, records: List) -> Dict:
        if len(records) < self.MIN_OCCURRENCES:
            return {"approved": False, "delta": 0.0, "reason": "insufficient_records"}

        confidences = [r.get("confidence", 0.0) for r in records]
        avg_conf = sum(confidences) / len(confidences)
        if avg_conf < self.MIN_AVG_CONFIDENCE:
            return {"approved": False, "delta": 0.0, "reason": "low_confidence"}

        timestamps = [r.get("timestamp", "") for r in records if r.get("timestamp")]
        if timestamps:
            latest = max(timestamps)
            try:
                latest_date = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                if latest_date.tzinfo is None:
                    latest_date = latest_date.replace(tzinfo=timezone.utc)
                days_ago = (datetime.now(timezone.utc) - latest_date).days
                if days_ago > self.MAX_DAYS_SINCE_LAST:
                    return {"approved": False, "delta": 0.0, "reason": "expired"}
            except (ValueError, TypeError):
                pass

        deltas = []
        for r in records:
            changes = r.get("changes", {})
            dim_change = changes.get(dim, {})
            delta = dim_change.get("delta", 0.0)
            deltas.append(delta)

        positive_sum = sum(d for d in deltas if d > self.EPSILON)
        negative_sum = sum(abs(d) for d in deltas if d < -self.EPSILON)
        if negative_sum >= positive_sum:
            return {"approved": False, "delta": 0.0, "reason": "conflicting_direction"}

        net_delta = sum(deltas)
        if net_delta < self.MIN_NET_DELTA:
            return {"approved": False, "delta": 0.0, "reason": "weak_growth"}

        positive_count = sum(1 for d in deltas if d > self.EPSILON)
        avg_delta = sum(d for d in deltas if d > self.EPSILON) / max(positive_count, 1)
        capped_delta = min(avg_delta, 0.15)

        return {"approved": True, "delta": round(capped_delta, 4)}

    def _calc_confidence(self, records: List) -> float:
        if not records:
            return 0.0
        return round(sum(r.get("confidence", 0.0) for r in records) / len(records), 2)

    def _determine_level(self, approved: List[str]) -> str:
        if not approved:
            return "rejected"
        if len(approved) > 1:
            return "trait_upgrade"
        return "trait_adjust"

    def _generate_reason(self, approved: List[str], rejected: List[str]) -> str:
        parts = []
        if approved:
            parts.append(f"批准升级: {', '.join(approved)}")
        if rejected:
            parts.append(f"拒绝升级: {', '.join(rejected)}（未满足审批标准）")
        if not parts:
            return "无候选特质需审批"
        return "；".join(parts)