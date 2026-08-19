"""
羽依成长引擎（GrowthEngine）v1.8

职责:
人生事件 → 成长意义识别 → GrowthState 统计更新 + GrowthRecord 生成

v1.8 (Phase 3.8.2-B):
- 新增 apply_proposal() 纯执行方法：接收 GrowthProposal，只执行状态变化
- 不承担认知职责（不判断意义、不决定方向、不映射规则）
- apply(event) 保留为兼容层，内部转为 GrowthProposal 后调用 apply_proposal()
- GROWTH_MAP 保留作为 fallback（当无 ExperienceMeaning 时）

v1.7 更新:
- 新增 apply_evaluated 方法，基于 GrowthEvaluator 的评估结果生成 GrowthRecord
- 原有 apply 方法保留，用于 GrowthState 的历史统计和关系状态更新
- GrowthRecord 由 PersonalityResolver 在 Phase 3.2 消费，Engine 不直接修改人格维度
- Phase 7.1：apply 方法末尾调用 self.state.save() 确保成长状态持久化
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
import uuid

from src.growth.growth_state import GrowthState
from src.growth.meaning_resolver import resolve_meaning
from src.growth.event_identity_resolver import resolve_event_identity
from src.growth.growth_record import GrowthRecord, create_growth_record
from src.growth.growth_schema import MAX_SINGLE_EVENT_DELTA


class GrowthEngine:

    def __init__(self, state: Optional[GrowthState] = None):
        self.state = state or GrowthState()

    GROWTH_MAP = {
        "birth": {
            "metrics": {"self_awareness": 0.18, "identity_strength": 0.15, "curiosity": 0.10},
            "milestone": True
        },
        "identity_creation": {
            "metrics": {"identity_strength": 0.20, "self_awareness": 0.15, "self_confidence": 0.10},
            "milestone": True
        },
        "relationship_start": {
            "metrics": {"trust": 0.08, "warmth": 0.08, "closeness": 0.12, "emotional_memory": 0.10}
        },
        "emotional_expression": {
            "metrics": {"trust": 0.10, "attachment": 0.12, "security": 0.08, "emotional_memory": 0.15}
        },
        "promise": {
            "metrics": {"attachment": 0.15, "trust": 0.10, "security": 0.12}
        },
        "creation": {
            "metrics": {"self_confidence": 0.08, "self_expression": 0.10, "initiative": 0.06, "creativity": 0.08}
        },
        "growth_support": {
            "metrics": {"trust": 0.08, "self_confidence": 0.10, "closeness": 0.08}
        },
        "companionship": {
            "metrics": {"closeness": 0.02, "warmth": 0.01}
        }
    }

    REPEAT_BONUS = {
        "promise": {"trust": 0.02, "security": 0.02},
        "relationship_start": {"closeness": 0.02, "warmth": 0.01},
        "emotional_expression": {"emotional_memory": 0.02},
        "creation": {"self_expression": 0.02, "creativity": 0.02},
        "companionship": {"warmth": 0.01, "closeness": 0.01}
    }

    def _history_key(self, event):
        return resolve_event_identity(event)

    def _already_grown(self, event):
        if event.get("is_first_occurrence") is False:
            return True
        if event.get("is_first_occurrence") is True:
            return False

        history = self.state.get().setdefault("growth_history", [])
        key = self._history_key(event)

        for item in history:
            if item.get("history_key") == key:
                return True
        return False

    def _calc_growth(self, value, importance, current):
        return round(value * importance * (1 - current), 4)

    def _apply_metrics(self, metrics, importance):
        before = {}
        delta = {}
        for key, value in metrics.items():
            current = self.state.get_metric(key)
            before[key] = current
            amount = self._calc_growth(value, importance, current)
            if amount > 0.0001:
                delta[key] = amount
        if delta:
            self.state.update_metrics(delta)
        return before, delta

    def _record_history(self, event, mode, before, delta):
        history = self.state.get().setdefault("growth_history", [])

        if mode == "repeat":
            key = self._history_key(event)
            for item in history:
                if item.get("history_key") == key:
                    item["reinforcement_count"] = item.get("reinforcement_count", 0) + 1
                    item["last_reinforced_at"] = datetime.now().isoformat()
                    return

        history.append({
            "meaning": event.get("meaning", ""),
            "topic": event.get("canonical_topic", event.get("topic", "")),
            "event_identity": (
                event.get("event_identity")
                or resolve_event_identity(event)
            ),
            "history_key": self._history_key(event),
            "mode": mode,
            "reinforcement_count": 0,
            "last_reinforced_at": None,
            "before": before,
            "delta": delta,
            "time": datetime.now().isoformat()
        })

    def apply(self, event: Dict):
        """
        原有方法：更新 GrowthState 统计指标（保留用于历史统计和关系状态）
        Phase 7.1：末尾调用 self.state.save() 确保成长状态持久化
        """
        if event.get("event_scope") == "system":
            return {"status": "ignored", "reason": "system_event"}

        # 1. 确保事件身份存在（兜底）
        if not event.get("event_identity"):
            resolve_event_identity(event)

        # 2. 使用身份解析含义
        meaning = resolve_meaning(event)
        event["meaning"] = meaning
        
        if not meaning:
            meaning = event.get("meaning") or event.get("event_type", "unknown")
            event["meaning"] = meaning
            if not meaning or meaning == "unknown":
                return {"status": "skipped", "reason": "unknown_meaning"}

        rule = self.GROWTH_MAP.get(meaning)
        if not rule:
            return {"status": "skipped", "reason": "no_rule"}

        importance = event.get("importance", 0.5)
        existed = self._already_grown(event)

        if existed:
            metrics = self.REPEAT_BONUS.get(meaning, {})
            mode = "repeat"
        else:
            metrics = rule.get("metrics", {})
            mode = "first"

        before, delta = self._apply_metrics(metrics, importance)

        if not existed and rule.get("milestone", False):
            self.state.add_milestone(event.get("event_id"), event.get("topic", ""))

        self._record_history(event, mode, before, delta)
        self.state.save()  # Phase 7.1: 确保成长状态持久化

        return {
            "status": "applied",
            "mode": mode,
            "meaning": meaning,
            "topic": event.get("topic"),
            "delta": delta,
            "before": before,
        }

    def apply_evaluated(self, evaluated_event: Dict) -> Optional[GrowthRecord]:
        """
        Phase 3.1 新增：基于 GrowthEvaluator 评估结果生成 GrowthRecord。
        仅生成记录，不修改人格数值。人格变更由 PersonalityResolver 在 Phase 3.2 统一处理。
        """
        if not evaluated_event.get("growth_allowed", False):
            return None

        growth_signal = evaluated_event.get("growth_signal", "")
        source_type = evaluated_event.get("event_type", "")
        growth_level = evaluated_event.get("growth_level", "context")
        confidence = evaluated_event.get("confidence", 0.5)
        target_candidates = evaluated_event.get("target_candidates", [])
        applied_delta = evaluated_event.get("applied_delta", 0.0)
        event_id = evaluated_event.get("event_id", "")
        canonical_topic = evaluated_event.get("canonical_topic", "")

        if not target_candidates or applied_delta <= 0.0:
            return None

        affected_dimensions = {}
        primary_dimension = target_candidates[0]
        affected_dimensions[primary_dimension] = applied_delta

        if len(target_candidates) > 1:
            secondary_dimension = target_candidates[1]
            affected_dimensions[secondary_dimension] = round(applied_delta * 0.5, 4)

        record = create_growth_record(
            record_id=str(uuid.uuid4())[:8],
            source_event_id=event_id,
            growth_signal=growth_signal,
            source_type=source_type,
            growth_level=growth_level,
            affected_dimensions=affected_dimensions,
            confidence=confidence,
            reason=f"[{growth_level}] {growth_signal} - {canonical_topic} (confidence={confidence:.2f})",
            created_at=datetime.now().isoformat(),
        )
        return record

    # ============================================================
    # Phase 3.8.2-B：apply_proposal() — 纯执行方法
    # ============================================================
    def apply_proposal(self, proposal: Any) -> Dict:
        """
        接收合法的 GrowthProposal，执行状态变化。

        职责：纯执行。不判断、不映射、不理解。
        - 输入：GrowthProposal（来自 src.contracts.growth_schema）
        - 输出：执行结果（before/delta/status）

        禁止：
        - 判断事件意义（那是 deep_resolve_meaning 的职责）
        - 决定成长方向（那是 GrowthEvaluator 的职责）
        - 映射 GROWTH_MAP（那是 fallback 路径的职责）
        """
        try:
            # 兼容两种输入：contracts.GrowthProposal 或 dict
            if hasattr(proposal, "proposed_changes"):
                changes = proposal.proposed_changes
                proposal_id = getattr(proposal, "id", "unknown")
                confidence = getattr(proposal, "confidence", 0.5)
                source_event_id = getattr(proposal, "source_event_id", "")
            elif isinstance(proposal, dict):
                changes = proposal.get("proposed_changes", [])
                proposal_id = proposal.get("id", "unknown")
                confidence = proposal.get("confidence", 0.5)
                source_event_id = proposal.get("source_event_id", "")
            else:
                return {"status": "rejected", "reason": "invalid_proposal_type"}

            if not changes:
                return {"status": "rejected", "reason": "no_changes"}

            # 无证据拒绝
            evidence_ids = (
                getattr(proposal, "evidence_ids", [])
                if hasattr(proposal, "evidence_ids")
                else proposal.get("evidence_ids", [])
                if isinstance(proposal, dict)
                else []
            )
            if not evidence_ids and confidence < 0.8:
                return {"status": "rejected", "reason": "no_evidence_and_low_confidence"}

            # 提取指标变化
            metrics = {}
            for change in changes:
                if hasattr(change, "path") and hasattr(change, "after"):
                    dim = change.path
                    val = change.after
                    if isinstance(val, (int, float)):
                        metrics[dim] = float(val)
                elif isinstance(change, dict):
                    dim = change.get("path") or change.get("dimension", "")
                    val = change.get("after") or change.get("delta", 0)
                    if isinstance(val, (int, float)):
                        metrics[dim] = float(val)

            if not metrics:
                return {"status": "rejected", "reason": "no_metric_changes"}

            # 置信度加权 + 单次上限截断
            weighted_metrics = {}
            for dim, val in metrics.items():
                weighted = val * confidence
                capped = min(weighted, MAX_SINGLE_EVENT_DELTA)
                if capped > 0.0001:
                    weighted_metrics[dim] = round(capped, 4)

            if not weighted_metrics:
                return {"status": "rejected", "reason": "delta_too_small"}

            # 执行状态更新
            before = {}
            for dim in weighted_metrics:
                before[dim] = self.state.get_metric(dim)

            self.state.update_metrics(weighted_metrics)
            self.state.save()

            # 记录历史
            history = self.state.get().setdefault("growth_history", [])
            history.append({
                "proposal_id": proposal_id,
                "source_event_id": source_event_id,
                "mode": "proposal",
                "before": before,
                "delta": weighted_metrics,
                "confidence": confidence,
                "time": datetime.now().isoformat(),
            })

            return {
                "status": "applied",
                "proposal_id": proposal_id,
                "mode": "proposal",
                "before": before,
                "delta": weighted_metrics,
            }

        except Exception as e:
            return {"status": "error", "reason": str(e)}

    # ============================================================
    # Phase 3.8.5 Step 5：apply_relationship() — 关系状态更新
    # ============================================================
    def apply_relationship(self, event: Dict, relationship_state) -> Dict:
        """
        更新关系状态（RelationshipState），不修改 GrowthState。

        职责：纯关系更新。不判断、不成长、不影响人格。
        - 输入：event + RelationshipState 实例
        - 输出：{"status": "applied"}

        从 Pipeline._update_relationship() 迁移至此，使 GrowthEngine
        成为所有成长相关状态变更的统一入口。
        """
        event_type = event.get("event_type", "")

        raw_importance = event.get("importance", 0.5)
        if isinstance(raw_importance, (list, tuple)):
            importance = float(raw_importance[0]) if len(raw_importance) > 0 else 0.5
        elif isinstance(raw_importance, (int, float)):
            importance = float(raw_importance)
        else:
            importance = 0.5

        category = event.get("category", "")
        topic = event.get("canonical_topic", event.get("topic", ""))
        event_id = event.get("event_id", "")

        if event_type == "relationship":
            relationship_state.update_trust(0.05 * importance)
            relationship_state.update_bond(0.06 * importance)
            relationship_state.update_familiarity(0.04 * importance)
            relationship_state.update_history(0.03 * importance)

        elif event_type == "commitment":
            relationship_state.update_trust(0.06 * importance)
            relationship_state.update_bond(0.08 * importance)
            relationship_state.update_promise(0.10 * importance)
            relationship_state.update_history(0.05 * importance)

        elif event_type == "milestone":
            if category == "羽依诞生阶段":
                relationship_state.update_bond(0.04 * importance)
                relationship_state.update_history(0.05 * importance)
                relationship_state.add_important_event({
                    "event_id": event_id,
                    "topic": topic,
                    "type": "birth"
                })

        elif event_type == "identity":
            relationship_state.update_history(0.02 * importance)

        if importance >= 0.85:
            relationship_state.add_milestone(event_id, topic)

        return {"status": "applied"}

    # ============================================================
    # Phase 3.8.5 Step 5：generate_personality_influence() — 人格影响生成
    # ============================================================
    def generate_personality_influence(self, event: Dict, result: Dict) -> List:
        """
        从 apply 结果生成 PersonalityInfluence 记录列表。

        职责：纯影响记录生成。不修改任何状态。
        - 输入：event + apply/apply_proposal 结果
        - 输出：List[PersonalityInfluence]

        从 Pipeline._record_influence() + _calculate_influence_confidence()
        迁移至此，使 GrowthEngine 成为所有成长相关状态变更的统一入口。
        """
        from src.personality.personality_influence import PersonalityInfluence, InfluenceType

        influences = []
        if not result.get("delta"):
            return influences

        for dim, delta in result["delta"].items():
            if abs(delta) <= 0.001:
                continue

            # 计算影响可信度
            confidence = 0.5
            if event.get("validation_status") == "confirmed":
                confidence += 0.3
            if len(event.get("source_ids", [])) > 1:
                confidence += 0.1
            if result.get("status") == "applied":
                confidence += 0.1
            deltas = result.get("delta", {}).values()
            if deltas:
                max_delta = max(abs(v) for v in deltas)
                if max_delta > 0.05:
                    confidence += 0.05
            confidence = min(confidence, 1.0)

            influence_type = result.get("change_type", InfluenceType.POSITIVE_GROWTH)
            if isinstance(influence_type, str):
                try:
                    influence_type = InfluenceType(influence_type)
                except ValueError:
                    influence_type = InfluenceType.POSITIVE_GROWTH

            import uuid
            from datetime import datetime

            influence = PersonalityInfluence(
                influence_id=f"inf_{uuid.uuid4().hex[:8]}",
                timestamp=datetime.now().isoformat(),
                source_event_id=event.get("event_id", ""),
                source_event_description=event.get("canonical_topic", ""),
                affected_dimension=dim,
                before_value=result.get("before", {}).get(dim, 0.5),
                after_value=result.get("before", {}).get(dim, 0.5) + delta,
                delta=delta,
                influence_type=influence_type,
                impact_weight=min(abs(delta), 1.0),
                confidence=confidence,
                evidence=event.get("source_ids", []),
            )
            influences.append(influence)

        return influences

    def apply_batch(self, events: list):
        return [self.apply(e) for e in events]

    def get_state(self):
        return self.state.get()

    def reset(self):
        self.state.reset()
        print("🔄 GrowthState 已重置")