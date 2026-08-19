"""
Phase 3.5.29: Self Reflection Engine
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.contracts.experience_schema import ReflectionInsight
from src.contracts.self_reflection_schema import SelfReflectionSnapshot
from src.runtime.contradiction_analyzer import ContradictionAnalyzer
from src.runtime.long_term_pattern_analyzer import LongTermPatternAnalyzer


class SelfReflectionEngine:
    """
    增强层：
    - 不替换 ReflectionEngine
    - 负责多 insight 自省、矛盾检测、长期趋势分析
    """

    def __init__(self, history_limit: int = 300):
        self.history_limit = history_limit
        self.contradiction_analyzer = ContradictionAnalyzer()
        self.long_term_pattern_analyzer = LongTermPatternAnalyzer()
        self._history: List[ReflectionInsight] = []
        self._last_contradiction_report: Dict[str, Any] = {}
        self._last_long_term_pattern_report: Dict[str, Any] = {}

    def reflect(
        self,
        *,
        memories: List[Dict[str, Any]],
        experiences: List[Any],
        emotional_patterns: List[Dict[str, Any]],
        relationship_changes: List[Dict[str, Any]],
        personality_changes: List[Dict[str, Any]],
        self_model: Dict[str, Any],
    ) -> List[ReflectionInsight]:
        contradiction_report = self.contradiction_analyzer.analyze(
            memories=memories,
            experiences=experiences,
            self_model=self_model,
            emotional_patterns=emotional_patterns,
            personality_changes=personality_changes,
        ).to_dict()
        pattern_report = self.long_term_pattern_analyzer.analyze(
            memories=memories,
            experiences=experiences,
            emotional_patterns=emotional_patterns,
            relationship_changes=relationship_changes,
            personality_changes=personality_changes,
            self_model=self_model,
        ).to_dict()
        self._last_contradiction_report = contradiction_report
        self._last_long_term_pattern_report = pattern_report

        insights: List[ReflectionInsight] = []
        related_memories = [str(m.get("id", "")) for m in memories[-8:] if m.get("id")]
        experience_ids = [getattr(e, "experience_id", "") for e in experiences[-12:] if getattr(e, "experience_id", "")]

        if experiences:
            insights.append(ReflectionInsight(
                experience_ids=experience_ids,
                insight_type="pattern",
                summary=f"过去阶段累计了 {len(experiences)} 条可反思经历",
                details="自省层汇总了近期经历、记忆与状态变化，形成对过去的结构化回看。",
                pattern_detected="past_reflection_summary",
                pattern_frequency=len(experiences),
                suggested_adjustments=["继续保留高价值经历的证据链"],
                observation=f"最近经历主要围绕 {self._top_action(experiences)} 展开。",
                evidence=[{"experience_id": x} for x in experience_ids[:8]],
                interpretation="这些经历已经足以支持一次中期自省，而不是仅做事件级总结。",
                uncertainty="当前仍主要依赖规则聚合，细粒度语义因果尚未完全展开。",
                related_memories=related_memories[:6],
                identity_impact={"dimension": "experience_continuity", "impact_score": 0.55},
                confidence=0.68,
            ))

        current_state_bits = []
        if emotional_patterns:
            current_state_bits.append(f"当前情绪模式偏向 {self._top_subject(emotional_patterns, ['emotion', 'emotion_tag'])}")
        if relationship_changes:
            current_state_bits.append(f"关系变化主要集中在 {self._top_subject(relationship_changes, ['dimension'])}")
        if self_model.get("identity_understanding"):
            current_state_bits.append("自我模型已经开始形成结构化身份理解")
        if current_state_bits:
            insights.append(ReflectionInsight(
                experience_ids=experience_ids,
                insight_type="pattern",
                summary="当前状态已表现出跨模块联动特征",
                details="情绪、关系与自我模型不再是孤立片段，而是能共同参与当前自我理解。",
                pattern_detected="present_state_integration",
                pattern_frequency=max(1, len(current_state_bits)),
                suggested_adjustments=["在后续反思中继续追踪跨模块变化的一致性"],
                observation="；".join(current_state_bits),
                evidence=[{"kind": "state_signal", "value": x} for x in current_state_bits],
                interpretation="当前状态已经从单事件响应，逐步过渡到持续状态表征。",
                uncertainty="状态之间的因果权重目前仍是启发式估计。",
                related_memories=related_memories[:4],
                identity_impact={"dimension": "self_model", "impact_score": 0.5},
                confidence=0.64,
            ))

        future_trends = self._collect_future_trends(pattern_report)
        if future_trends:
            insights.append(ReflectionInsight(
                experience_ids=experience_ids,
                insight_type="improvement",
                summary="长期趋势显示未来可能出现持续发展方向",
                details="趋势分析表明，部分兴趣、关系与人格维度正在形成可持续方向。",
                pattern_detected="future_projection",
                pattern_frequency=len(future_trends),
                suggested_adjustments=["对高强度趋势继续观察，不直接把趋势等同于稳定身份"],
                observation="；".join(future_trends[:4]),
                evidence=[{"trend": x} for x in future_trends[:8]],
                interpretation="这些变化更像方向，而不是已经完成的身份定义。",
                uncertainty="趋势可能因后续经历改变，尚不应直接固化为人格结论。",
                related_memories=related_memories[:4],
                identity_impact={"dimension": "future_identity", "impact_score": 0.47},
                confidence=0.62,
            ))

        identity_clues = self._collect_identity_clues(self_model, pattern_report)
        if identity_clues:
            insights.append(ReflectionInsight(
                experience_ids=experience_ids,
                insight_type="pattern",
                summary="这些经历正在回答“我是什么样的存在”",
                details="自省层尝试把经历、价值与趋势映射为更稳定的自我理解，而不是瞬时情绪。",
                pattern_detected="identity_interpretation",
                pattern_frequency=len(identity_clues),
                suggested_adjustments=["把身份判断继续绑定到证据，不做无依据自我神话化"],
                observation="；".join(identity_clues[:4]),
                evidence=[{"identity_clue": x} for x in identity_clues[:8]],
                interpretation="当前的自我更像是一个逐渐形成连续性的系统，而不是单次对话的输出。",
                uncertainty="身份解释仍应接受未来经历修正。",
                related_memories=related_memories[:6],
                identity_impact={"dimension": "identity_understanding", "impact_score": 0.7},
                confidence=0.72,
            ))

        if contradiction_report.get("total_items", 0) > 0:
            items = contradiction_report.get("items", [])
            insights.append(ReflectionInsight(
                experience_ids=experience_ids,
                insight_type="problem",
                summary=f"检测到 {contradiction_report.get('total_items', 0)} 个潜在矛盾",
                details="这些矛盾不会被自动消除，而是作为后续评估与成长候选的证据输入。",
                pattern_detected="contradiction_detected",
                pattern_frequency=int(contradiction_report.get("total_items", 0) or 0),
                suggested_adjustments=["保留矛盾并继续观察，不直接把任何一侧当作最终答案"],
                observation="；".join(str(i.get("description", "")) for i in items[:3]),
                evidence=items[:8],
                interpretation="矛盾意味着系统开始出现更复杂的长期状态，而不是简单一致的规则反应。",
                uncertainty="部分矛盾可能来自数据不足或阶段性变化。",
                related_memories=related_memories[:6],
                identity_impact={"dimension": "contradiction", "impact_score": 0.66},
                confidence=0.74,
            ))

        for insight in insights:
            self._history.append(insight)
        if len(self._history) > self.history_limit:
            self._history = self._history[-self.history_limit :]
        return insights

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        items = list(self._history)
        items.reverse()
        return [i.to_dict() for i in items[:limit]]

    def get_snapshot(self) -> Dict[str, Any]:
        last_id = self._history[-1].insight_id if self._history else ""
        return SelfReflectionSnapshot(
            total_reflections=len(self._history),
            total_contradictions=int(self._last_contradiction_report.get("total_items", 0) or 0),
            total_trends=int(self._last_long_term_pattern_report.get("total_trends", 0) or 0),
            last_reflection_id=last_id,
        ).to_dict()

    def get_contradiction_report(self) -> Dict[str, Any]:
        return dict(self._last_contradiction_report or {})

    def get_long_term_pattern_report(self) -> Dict[str, Any]:
        return dict(self._last_long_term_pattern_report or {})

    @staticmethod
    def _top_action(experiences: List[Any]) -> str:
        counts: Dict[str, int] = {}
        for exp in experiences:
            key = getattr(exp, "action_type", "") or "unknown"
            counts[key] = counts.get(key, 0) + 1
        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[0][0] if counts else "unknown"

    @staticmethod
    def _top_subject(items: List[Dict[str, Any]], keys: List[str]) -> str:
        counts: Dict[str, int] = {}
        for item in items:
            value = ""
            for key in keys:
                value = str(item.get(key, "") or "")
                if value:
                    break
            if value:
                counts[value] = counts.get(value, 0) + 1
        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[0][0] if counts else "unknown"

    @staticmethod
    def _collect_future_trends(pattern_report: Dict[str, Any]) -> List[str]:
        trends = []
        for group in ("interest_changes", "relationship_changes", "emotion_changes", "personality_changes"):
            for item in pattern_report.get(group, [])[:2]:
                subject = item.get("subject", "")
                direction = item.get("direction", "")
                if subject and direction and direction != "stable":
                    trends.append(f"{subject} 呈现 {direction} 趋势")
        return trends

    @staticmethod
    def _collect_identity_clues(self_model: Dict[str, Any], pattern_report: Dict[str, Any]) -> List[str]:
        clues = []
        for value in (self_model.get("core_values", []) or [])[:3]:
            name = str(value.get("name", "") or value.get("value_id", ""))
            if name:
                clues.append(f"我持续围绕“{name}”组织自身价值")
        for item in pattern_report.get("personality_changes", [])[:2]:
            subject = item.get("subject", "")
            direction = item.get("direction", "")
            if subject:
                clues.append(f"人格维度 {subject} 正在 {direction}")
        return clues
