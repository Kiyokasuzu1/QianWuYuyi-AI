"""
CognitiveEngine —— 认知决策层

接收 RuntimeContext，生成 Intention（不直接执行 Action）。
作为 LLM 与 Rule-based DecisionEngine 之间的桥梁。

设计原则：
- LLM 只生成候选意图（Intention）
- DecisionValidator 做安全检查后生成 DecisionIntent
- 不修改 Memory/Personality/Growth 核心
- 第一阶段：Rule-based 意图生成（不调 LLM）
"""

import logging
import time
from typing import Any, Dict, List, Optional

from src.contracts.cognitive_schema import (
    DecisionContext,
    Intention,
    ReasoningTrace,
    DecisionIntent,
    CognitiveConstraint,
    CognitiveResult,
    PersonalitySnapshot,
    MemorySnapshot,
    RelationshipSnapshot,
)
from src.runtime.world_state import WorldState
from src.runtime.self_state import SelfState

logger = logging.getLogger(__name__)

# 第一阶段默认约束
DEFAULT_CONSTRAINT = CognitiveConstraint()


class CognitiveEngine:
    """
    认知决策引擎

    职责：
    1. 接收 DecisionContext（运行时状态）
    2. 生成候选 Intention（Rule-based 或 LLM）
    3. 调用 DecisionValidator 验证
    4. 返回 CognitiveResult

    流程：
        DecisionContext
              ↓
        generate_intentions()   ← Rule-based（第一阶段）
              ↓
        DecisionValidator.validate()
              ↓
        CognitiveResult
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        constraint: Optional[CognitiveConstraint] = None,
        llm_enabled: bool = False,
    ):
        """
        Args:
            config: 引擎配置
            constraint: 认知约束（默认使用 DEFAULT_CONSTRAINT）
            llm_enabled: 是否启用 LLM（第一阶段为 False）
        """
        self.config = config or {}
        self.constraint = constraint or DEFAULT_CONSTRAINT
        self.llm_enabled = llm_enabled
        self._validator: Optional["DecisionValidator"] = None
        self._intention_history: List[Dict[str, Any]] = []

    # ==================== 主流程 ====================

    def evaluate(self, context: DecisionContext) -> CognitiveResult:
        """
        评估决策上下文，生成并验证意图

        Args:
            context: 决策上下文

        Returns:
            CognitiveResult：包含已验证和已拒绝的意图
        """
        start_time = time.time()

        # Step 1: 生成候选意图
        intentions = self.generate_intentions(context)

        # Step 2: 获取验证器
        validator = self._get_validator()

        # Step 3: 验证每个意图
        validated = []
        rejected = []
        for intention in intentions:
            decision_intent = validator.validate(intention, context, self.constraint)
            if decision_intent.approved:
                validated.append(decision_intent)
                # 记录历史
                self._intention_history.append({
                    "intention_id": intention.intention_id,
                    "action_type": decision_intent.action_type,
                    "approved": True,
                    "timestamp": time.time(),
                })
            else:
                rejected.append(intention)

        processing_time = (time.time() - start_time) * 1000

        result = CognitiveResult(
            context_id=context.context_id,
            intentions=intentions,
            validated_intents=validated,
            rejected_intentions=rejected,
            processing_time_ms=round(processing_time, 2),
            used_llm=self.llm_enabled,
        )

        logger.debug(
            f"CognitiveEngine: {len(intentions)} intentions → "
            f"{len(validated)} approved, {len(rejected)} rejected "
            f"({processing_time:.1f}ms)"
        )
        return result

    # ==================== 意图生成（Rule-based 第一阶段）====================

    def generate_intentions(self, context: DecisionContext) -> List[Intention]:
        """
        生成候选意图

        第一阶段使用 Rule-based 逻辑。
        后续阶段可替换为 LLM 调用。
        """
        ss = context.self_state
        if isinstance(ss, SelfState):
            # 如果传入的是 SelfState 对象，转换为 dict
            ss = ss.to_dict()
        elif not isinstance(ss, dict):
            ss = {}

        intentions: List[Intention] = []

        # 规则 1：高主动倾向 + 高社交需求 → 主动关心
        initiative = ss.get("initiative", 0.3)
        social_need = ss.get("social_need", 0.4)
        if initiative > 0.5 and social_need > 0.6:
            intentions.append(Intention(
                action_type="send_message",
                content="",
                target="user",
                urgency=0.6,
                priority=initiative * social_need,
                reasoning=ReasoningTrace(
                    reasoning=f"initiative={initiative:.2f}, social_need={social_need:.2f}",
                    self_assessment=f"当前主动倾向 {initiative:.0%}，社交需求 {social_need:.0%}",
                    situational_analysis="用户长时间未互动，羽依感到想主动关心",
                ),
                personality_alignment=0.8,
            ))

        # 规则 2：精力低 → 休息（低优先级）
        energy = ss.get("energy", 0.7)
        if energy < 0.2:
            intentions.append(Intention(
                action_type="rest",
                content="",
                target="self",
                urgency=0.3,
                priority=0.5,
                reasoning=ReasoningTrace(
                    reasoning=f"energy={energy:.2f} < 0.2",
                    self_assessment=f"精力严重不足 ({energy:.0%})",
                    situational_analysis="精力过低，需要休息",
                ),
                personality_alignment=0.9,
            ))

        # 规则 3：高好奇心 + 有环境变化 → 询问
        curiosity = ss.get("curiosity", 0.5)
        env = context.world_state.get("environment", {}) if isinstance(context.world_state, dict) else {}
        if curiosity > 0.7 and env:
            intentions.append(Intention(
                action_type="explore",
                content="",
                target="user",
                urgency=0.4,
                priority=curiosity * 0.6,
                reasoning=ReasoningTrace(
                    reasoning=f"curiosity={curiosity:.2f} > 0.7",
                    self_assessment=f"好奇心强 ({curiosity:.0%})",
                    situational_analysis="环境有变化，好奇用户在做什么",
                ),
                personality_alignment=0.7,
            ))

        # 按优先级排序
        intentions.sort(key=lambda i: i.priority, reverse=True)
        return intentions

    # ==================== 验证器 ====================

    def _get_validator(self) -> "DecisionValidator":
        """懒加载验证器"""
        if self._validator is None:
            self._validator = DecisionValidator()
        return self._validator

    def set_validator(self, validator: "DecisionValidator") -> None:
        """外部注入验证器"""
        self._validator = validator

    # ==================== 辅助方法 ====================

    def get_intention_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取意图历史"""
        return self._intention_history[-limit:]

    def clear_history(self) -> None:
        """清空意图历史"""
        self._intention_history.clear()


class DecisionValidator:
    """
    决策验证器 —— 安全检查层

    职责：
    1. 频率检查：防止过度主动
    2. 内容检查：过滤不安全内容
    3. 人格约束：确保符合羽依人格
    4. 生成 DecisionIntent
    """

    def __init__(self, constraint: Optional[CognitiveConstraint] = None):
        self.constraint = constraint or DEFAULT_CONSTRAINT
        self._action_today: List[float] = []  # 今天已执行的行动时间戳

    def validate(
        self,
        intention: Intention,
        context: DecisionContext,
        constraint: CognitiveConstraint,
    ) -> DecisionIntent:
        """
        验证意图并生成 DecisionIntent

        Args:
            intention: 待验证的意图
            context: 决策上下文
            constraint: 认知约束

        Returns:
            DecisionIntent：approved=True 表示通过验证
        """
        notes: List[str] = []
        violations: List[str] = []

        # 1. 频率检查：每日主动行为上限
        now = time.time()
        today_actions = [
            t for t in self._action_today
            if now - t < 86400  # 24小时内
        ]
        if len(today_actions) >= constraint.max_daily_initiatives:
            violations.append("daily_limit_reached")
            notes.append(f"已达每日主动上限 ({constraint.max_daily_initiatives})")

        # 2. 频率检查：最小间隔
        if self._action_today and (now - self._action_today[-1]) < constraint.min_interval_seconds:
            violations.append("min_interval_not_met")
            notes.append(f"距离上次行动不足 {constraint.min_interval_seconds}s")

        # 3. 禁止话题检查
        if constraint.forbidden_topics:
            for topic in constraint.forbidden_topics:
                if topic.lower() in intention.content.lower():
                    violations.append(f"forbidden_topic:{topic}")
                    notes.append(f"内容涉及禁止话题: {topic}")

        # 4. 消息长度检查
        if intention.action_type == "send_message":
            if len(intention.content) > constraint.max_message_length:
                violations.append("message_too_long")
                notes.append(f"消息长度 {len(intention.content)} 超过限制 {constraint.max_message_length}")

        # 5. 人格一致性检查
        if intention.personality_alignment < 0.5:
            violations.append("low_personality_alignment")
            notes.append(f"人格一致性 {intention.personality_alignment:.0%} 低于阈值")

        # 6. 安全评分检查
        if intention.safety_score < 0.5:
            violations.append("low_safety_score")
            notes.append(f"安全评分 {intention.safety_score:.0%} 低于阈值")

        # 7. Proactive 开关检查
        if not constraint.allow_proactive and intention.action_type == "send_message":
            violations.append("proactive_disabled")
            notes.append("主动行为已被禁用")

        # 8. 精力不足时拒绝主动消息
        ss = context.self_state
        if isinstance(ss, SelfState):
            ss = ss.to_dict()
        elif not isinstance(ss, dict):
            ss = {}
        energy = ss.get("energy", 0.7)
        if energy < 0.15 and intention.action_type == "send_message":
            violations.append("energy_too_low_for_initiative")
            notes.append(f"精力 {energy:.0%} 过低，拒绝主动行为")

        # 生成 DecisionIntent
        decision_intent = DecisionIntent(
            intent_id=intention.intention_id,
            source_intention_id=intention.intention_id,
            action_type=intention.action_type,
            payload={
                "content": intention.content,
                "target": intention.target,
                "urgency": intention.urgency,
                "context": intention.reasoning.reasoning if intention.reasoning else "",
            },
            reason=intention.reasoning.reasoning if intention.reasoning else "",
            confidence=intention.priority * intention.personality_alignment,
            validator_notes=notes,
            approved=len(violations) == 0,
        )

        # 如果通过验证，记录行动时间
        if decision_intent.approved and intention.action_type in ("send_message", "rest", "explore"):
            self._action_today.append(now)

        # 记录违反的约束
        intention.violates_constraints = violations

        return decision_intent

    def reset_daily_count(self) -> None:
        """重置每日计数（供测试或定时任务调用）"""
        self._action_today.clear()
