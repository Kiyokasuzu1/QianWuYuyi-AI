"""
成长评估器 (GrowthEvaluator) v1.1

职责：
对标准化事件进行成长资格评估，计算可信度、稳定度、一致性、
影响等级和成长层级，最终决定该事件是否能进入成长系统。

v1.1 修正：
- 关系事件 applied_delta 强制为 0
- confidence 证据加成受类型权重限制
- stability 增长速率微调 (count*0.08, max 0.4)
- 添加语义一致性 TODO
- 默认 preference 信号改为 general_preference

设计原则：
- 行为证据 > 语言声明
- 长期一致 > 单次表达
- 关系事件不能污染人格核心
- 单次成长幅度严格受限
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime

from src.growth.growth_schema import (
    GrowthEvent,
    TYPE_WEIGHTS,
    SOURCE_RELIABILITY,
    DOMAIN_MAX_LEVEL,
    TYPE_IMPACT_BASE,
    GROWTH_SIGNAL_CANDIDATES,
    GROWTH_LEVEL_DELTA_RANGE,
    MAX_EVENT_IMPACT,
    MAX_SINGLE_EVENT_DELTA,
    EVALUATOR_VERSION,
)


class GrowthEvaluator:
    """
    成长评估器

    输入：标准化事件 + 历史同类事件（可选）
    输出：带有完整成长元数据的 GrowthEvent
    """

    def evaluate(self, event: Dict, history: List[Dict] = None) -> GrowthEvent:
        """
        评估一个事件是否值得进入成长系统。

        Args:
            event: 标准化事件字典（来自 EventNormalizer/EventValidator）
            history: 历史同类事件列表（来自 EventHistoryMatcher），可为空

        Returns:
            带有完整成长元数据的 GrowthEvent
        """
        history = history or []

        # 1. 基础评估
        source_reliability = self._calc_source_reliability(event)
        event_type = event.get("event_type", "")

        # 2. 可信度
        confidence = self._calc_confidence(event, source_reliability)

        # 3. 稳定度
        stability = self._calc_stability(event, history)

        # 4. 一致性
        consistency = self._calc_consistency(event, history)

        # 5. 影响等级
        impact = self._calc_impact(event)

        # 6. 证据质量
        evidence_quality = self._calc_evidence_quality(event)

        # 7. 成长层级
        growth_level = self._determine_growth_level(
            confidence, stability, consistency, impact
        )

        # 8. 领域限制
        growth_domain = self._determine_domain(event)
        max_allowed_level = DOMAIN_MAX_LEVEL.get(growth_domain, "context")

        # 9. 是否允许成长
        growth_allowed = self._check_growth_allowed(
            growth_level, max_allowed_level, event_type
        )

        # 10. 目标维度候选
        growth_signal = self._resolve_growth_signal(event)
        target_candidates = GROWTH_SIGNAL_CANDIDATES.get(growth_signal, [])

        # 11. 目标变化量（关系领域永远不产生人格变化）
        if growth_domain == "relationship_context":
            target_delta = 0.0
        else:
            target_delta = (
                self._calc_target_delta(growth_level, impact)
                if growth_allowed
                else 0.0
            )

        return {
            **event,
            "confidence": confidence,
            "source_reliability": source_reliability,
            "stability": stability,
            "consistency": consistency,
            "impact": impact,
            "evidence_quality": evidence_quality,
            "growth_level": growth_level,
            "growth_domain": growth_domain,
            "max_allowed_level": max_allowed_level,
            "growth_allowed": growth_allowed,
            "growth_signal": growth_signal,
            "target_candidates": target_candidates,
            "applied_delta": target_delta,
            "resolver_decision": None,
            "first_seen": event.get("first_seen") or datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat(),
            "occurrence_count": len(history) + 1,
            "schema_version": "0.21",
            "evaluator_version": EVALUATOR_VERSION,
        }

    # ============================================================
    # 可信度计算
    # ============================================================
    def _calc_confidence(self, event: Dict, source_reliability: float) -> float:
        """
        综合可信度 = 事件类型权重 × 来源可靠度 + 证据加成 × 类型权重

        修正：证据加成也受类型权重限制，防止低权重事件因证据多而越级。
        """
        event_type = event.get("event_type", "")
        type_weight = TYPE_WEIGHTS.get(event_type, 0.5)

        # 证据调整：多条证据增加可信度
        evidence_count = len(event.get("evidence", []))
        evidence_bonus = min(evidence_count * 0.05, 0.15)

        base = type_weight * source_reliability
        return round(min(base + evidence_bonus * type_weight, 1.0), 3)

    def _calc_source_reliability(self, event: Dict) -> float:
        """
        判断事件来源的可靠程度。

        - 来自用户行为记录（包含"了""过""完成了"等）：高可靠
        - 来自LLM推断（事件名含"可能""似乎"等）：低可靠
        - 默认为用户声明：中高可靠
        """
        evidence = event.get("evidence", [])
        if not evidence:
            return SOURCE_RELIABILITY["context_guess"]

        # 检查证据中是否有行为描述（过去时、完成时）
        behavior_markers = ["了", "过", "完成了", "做了", "创建了", "已经"]
        for ev in evidence:
            text = ev.get("text", "")
            if any(marker in text for marker in behavior_markers):
                return SOURCE_RELIABILITY["user_behavior"]

        # 检查是否为LLM推断（事件名称包含推测性词汇）
        event_name = event.get("event", "")
        inference_markers = ["可能", "似乎", "推测", "推断", "好像"]
        if any(marker in event_name for marker in inference_markers):
            return SOURCE_RELIABILITY["llm_inference"]

        # 默认为用户声明
        return SOURCE_RELIABILITY["user_statement"]

    # ============================================================
    # 稳定度计算
    # ============================================================
    def _calc_stability(self, event: Dict, history: List[Dict]) -> float:
        """
        稳定度：基于历史重复次数和时间跨度。

        - 首次出现：0.2
        - 10次以上：0.6（配合时间可达到 trait 门槛）
        - 3个月跨度：+0.3
        """
        if not history:
            return 0.2

        count = len(history) + 1
        count_score = min(count * 0.08, 0.4)  # 平缓增长

        # 时间跨度计算
        timestamps = []
        for h in history:
            ts = h.get("first_seen") or h.get("last_seen")
            if ts:
                timestamps.append(ts)
        timestamps.append(event.get("first_seen") or datetime.now().isoformat())

        time_score = 0.0
        if len(timestamps) >= 2:
            try:
                dates = []
                for ts in timestamps:
                    if ts:
                        dates.append(datetime.fromisoformat(ts[:10]))
                if len(dates) >= 2:
                    span_days = (max(dates) - min(dates)).days
                    time_score = min(span_days / 90 * 0.3, 0.3)
            except (ValueError, TypeError):
                pass

        return round(min(0.2 + count_score + time_score, 1.0), 3)

    # ============================================================
    # 一致性计算
    # ============================================================
    def _calc_consistency(self, event: Dict, history: List[Dict]) -> float:
        """
        语义一致性：新事件与历史事件的语义方向是否一致。

        - 一致方向：consistency 提升
        - 矛盾方向：consistency 降低
        - 首次出现：0.3

        TODO: 未来引入语义embedding检查，替代当前基于主题和类型的简单匹配。
        """
        if not history:
            return 0.3

        current_topic = event.get("canonical_topic", event.get("topic", ""))
        current_type = event.get("event_type", "")

        matches = 0
        conflicts = 0

        for h in history:
            h_topic = h.get("canonical_topic", h.get("topic", ""))
            h_type = h.get("event_type", "")

            # 同类型同主题 → 一致
            if current_type == h_type and current_topic == h_topic:
                matches += 1
            # 同类型不同主题 → 可能冲突
            elif current_type == h_type and current_topic != h_topic:
                conflicts += 1

        total = matches + conflicts + 1
        base = 0.3 + (matches / total) * 0.5

        # 冲突惩罚
        if conflicts > matches:
            base *= 0.5

        return round(min(base, 1.0), 3)

    # ============================================================
    # 影响等级计算
    # ============================================================
    def _calc_impact(self, event: Dict) -> float:
        """
        影响等级：事件对人格的潜在改变程度。
        结合事件类型基础影响和重要性，上限受 MAX_EVENT_IMPACT 约束。
        """
        event_type = event.get("event_type", "")
        importance = event.get("importance", 0.5)

        # 确保 importance 是数值
        if isinstance(importance, (list, tuple)):
            importance = float(importance[0]) if importance else 0.5
        elif not isinstance(importance, (int, float)):
            importance = 0.5

        type_base = TYPE_IMPACT_BASE.get(event_type, 0.1)

        impact = type_base * importance
        return round(min(impact, MAX_EVENT_IMPACT), 3)

    # ============================================================
    # 证据质量计算
    # ============================================================
    def _calc_evidence_quality(self, event: Dict) -> float:
        """
        证据质量：基于证据数量、角色和具体程度。
        用于辅助判断，当前不直接影响成长决策，但可供 Resolver 参考。
        """
        evidence = event.get("evidence", [])
        if not evidence:
            return 0.1

        score = 0.0

        # 多条证据加分
        score += min(len(evidence) * 0.15, 0.4)

        # 用户 + 助手双角色证据加分
        roles = set(e.get("role", "") for e in evidence)
        if "user" in roles and "assistant" in roles:
            score += 0.2

        # 证据文本长度（具体程度）
        total_length = sum(len(e.get("text", "")) for e in evidence)
        if total_length > 100:
            score += 0.2
        elif total_length > 50:
            score += 0.1

        return round(min(score, 1.0), 3)

    # ============================================================
    # 成长层级判定
    # ============================================================
    def _determine_growth_level(
        self,
        confidence: float,
        stability: float,
        consistency: float,
        impact: float,
    ) -> str:
        """
        成长层级判定：

        - trace：仅记录，不改变任何参数
        - context：短期适应，影响当前对话风格
        - preference：长期偏好，需多次验证
        - trait：人格倾向，需要最高标准
        """
        # trait：最高标准
        if (
            confidence >= 0.7
            and stability >= 0.6
            and consistency >= 0.6
            and impact >= 0.15
        ):
            return "trait"

        # preference：中等标准
        if (
            confidence >= 0.6
            and stability >= 0.4
            and consistency >= 0.4
            and impact >= 0.1
        ):
            return "preference"

        # context：最低成长标准
        if confidence >= 0.5 and impact >= 0.05:
            return "context"

        # trace：不满足任何成长条件
        return "trace"

    # ============================================================
    # 领域与限制
    # ============================================================
    def _determine_domain(self, event: Dict) -> str:
        """
        确定事件所属的成长领域。
        """
        event_type = event.get("event_type", "")

        domain_map = {
            "preference": "preference",
            "creation": "capability",
            "identity": "expression",
            "milestone": "capability",
            "relationship": "relationship_context",
            "growth_support": "knowledge",
        }
        return domain_map.get(event_type, "knowledge")

    def _check_growth_allowed(
        self,
        growth_level: str,
        max_allowed_level: str,
        event_type: str,
    ) -> bool:
        """
        判断是否允许成长。

        - trace 层不成长
        - 领域最大层级限制
        - 关系事件永远不能进入 preference 和 trait
        """
        if growth_level == "trace":
            return False

        # 层级比较：trace < context < preference < trait
        level_order = {"trace": 0, "context": 1, "preference": 2, "trait": 3}
        if level_order.get(growth_level, 0) > level_order.get(max_allowed_level, 0):
            return False

        # 关系事件：硬限制
        if event_type == "relationship" and growth_level in ("preference", "trait"):
            return False

        return True

    # ============================================================
    # 目标维度
    # ============================================================
    def _resolve_growth_signal(self, event: Dict) -> str:
        """
        将事件映射为成长信号。
        基于事件类型和主题的关键词匹配，无匹配时使用默认信号。
        """
        event_type = event.get("event_type", "")
        topic = event.get("canonical_topic", event.get("topic", ""))

        # 基于主题关键词的信号匹配
        signal_map = [
            (["创作", "设计", "制作", "画", "写", "构建", "创造"], "creative_activity_interest"),
            (["问题", "分析", "解决", "优化", "架构", "系统"], "complex_problem_solving"),
            (["聊天", "交流", "社交", "互动", "朋友"], "social_interaction_preference"),
            (["学习", "探索", "研究", "了解", "知识", "新"], "knowledge_exploration"),
            (["自己", "想法", "表达", "风格", "观点"], "self_expression_growth"),
            (["情绪", "感受", "心情", "理解", "共情"], "emotional_understanding"),
        ]

        combined = topic + event_type
        for keywords, signal in signal_map:
            if any(kw in combined for kw in keywords):
                return signal

        # 默认信号：preference 不再默认假设为知识探索
        default_signals = {
            "creation": "creative_activity_interest",
            "preference": "general_preference",
            "identity": "self_expression_growth",
            "milestone": "complex_problem_solving",
            "relationship": "",
        }
        return default_signals.get(event_type, "")

    # ============================================================
    # 变化量计算
    # ============================================================
    def _calc_target_delta(self, growth_level: str, impact: float) -> float:
        """
        计算目标变化量，基于成长层级和影响等级。
        上限受 MAX_SINGLE_EVENT_DELTA 约束。
        """
        min_delta, max_delta = GROWTH_LEVEL_DELTA_RANGE.get(growth_level, (0.0, 0.0))
        delta = min_delta + impact * (max_delta - min_delta)
        return round(min(delta, MAX_SINGLE_EVENT_DELTA), 4)