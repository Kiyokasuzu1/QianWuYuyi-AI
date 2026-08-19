"""
Reflection Engine —— 反思引擎

职责：
- 读取 Experience
- 生成 ReflectionInsight
- 第一阶段：规则模拟，不接入 LLM

设计原则：
- 不修改 Memory/Growth 核心逻辑
- 反思结果通过 GrowthAdapter 接口传递
- 第一阶段只记录，不真正写入
"""

import time
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from collections import Counter

from src.contracts.experience_schema import (
    RuntimeExperience,
    ReflectionInsight,
    GrowthAdapterBase,
)

logger = logging.getLogger(__name__)


@dataclass
class ReflectionEngineConfig:
    """反思引擎配置"""
    min_experiences: int = 3          # 最小经验数量
    max_experiences: int = 50         # 最大经验数量
    llm_enabled: bool = False         # 是否启用 LLM（第一阶段关闭）


class ReflectionEngine:
    """
    反思引擎

    从一批 Experience 中提炼模式、问题和改进建议。
    第一阶段使用规则模拟，不接入 LLM。
    """

    def __init__(
        self,
        config: Optional[ReflectionEngineConfig] = None,
        growth_adapter: Optional[GrowthAdapterBase] = None,
    ):
        self.config = config or ReflectionEngineConfig()
        self.growth_adapter = growth_adapter

        # 反思历史
        self._insights: List[ReflectionInsight] = []

    def reflect(
        self,
        experiences: List[RuntimeExperience],
    ) -> Optional[ReflectionInsight]:
        """
        从一批经验中生成反思洞察

        Args:
            experiences: 经验列表

        Returns:
            反思洞察，若经验不足则返回 None
        """
        # 过滤有效经验
        valid_experiences = [e for e in experiences if e.valid]

        if len(valid_experiences) < self.config.min_experiences:
            logger.debug(f"有效经验不足: {len(valid_experiences)} < {self.config.min_experiences}")
            return None

        # 限制经验数量
        if len(valid_experiences) > self.config.max_experiences:
            valid_experiences = valid_experiences[-self.config.max_experiences:]

        start_time = time.time()

        # 第一阶段：规则模拟
        insight = self._rule_based_reflect(valid_experiences)

        if insight:
            self._insights.append(insight)
            processing_time = (time.time() - start_time) * 1000
            logger.info(
                f"反思完成: {insight.insight_id}, "
                f"type={insight.insight_type}, "
                f"pattern={insight.pattern_detected}, "
                f"time={processing_time:.1f}ms"
            )

        return insight

    def _rule_based_reflect(
        self,
        experiences: List[RuntimeExperience],
    ) -> Optional[ReflectionInsight]:
        """
        基于规则的反思（第一阶段）

        检测模式：
        1. 高频 Action 类型
        2. 低成功率 Action
        3. 用户响应模式
        4. 触发类型分布
        """
        if not experiences:
            return None

        experience_ids = [e.experience_id for e in experiences]

        # 1. 检测高频 Action 类型
        action_types = [e.action_type for e in experiences]
        action_counter = Counter(action_types)
        most_common_action, most_common_count = action_counter.most_common(1)[0]

        # 2. 检测成功率
        successes = sum(1 for e in experiences if e.result and e.result.success)
        total = len(experiences)
        success_rate = successes / total if total > 0 else 0.0

        # 3. 检测用户响应率
        responses = sum(1 for e in experiences if e.result and e.result.response_received)
        response_rate = responses / total if total > 0 else 0.0

        # 4. 检测触发类型分布
        trigger_types = [e.trigger_type for e in experiences]
        trigger_counter = Counter(trigger_types)
        most_common_trigger, _ = trigger_counter.most_common(1)[0] if trigger_counter else ("unknown", 0)

        # 生成洞察
        insights = []

        # 模式 1: 高频主动行为
        if most_common_action == "send_message" and most_common_count >= 3:
            insights.append(ReflectionInsight(
                experience_ids=experience_ids,
                insight_type="pattern",
                summary=f"检测到高频主动消息行为: {most_common_count} 次",
                details=f"最近 {len(experiences)} 次经验中，主动消息占比最高，可能表明用户互动需求较高。",
                pattern_detected="high_frequency_proactive",
                pattern_frequency=most_common_count,
                suggested_adjustments=["考虑降低主动消息频率", "观察用户响应后再决定后续行为"],
                confidence=0.6,
                used_llm=False,
            ))

        # 模式 2: 低成功率
        if success_rate < 0.5 and total >= 3:
            insights.append(ReflectionInsight(
                experience_ids=experience_ids,
                insight_type="problem",
                summary=f"检测到低成功率: {success_rate:.1%}",
                details=f"最近 {len(experiences)} 次经验中，成功率仅 {success_rate:.1%}，需要分析失败原因。",
                pattern_detected="low_success_rate",
                pattern_frequency=int(total * (1 - success_rate)),
                suggested_adjustments=["检查 Action 执行条件", "降低决策置信度阈值", "增加失败重试逻辑"],
                confidence=0.7,
                used_llm=False,
            ))

        # 模式 3: 低用户响应率
        if response_rate < 0.3 and total >= 3:
            insights.append(ReflectionInsight(
                experience_ids=experience_ids,
                insight_type="problem",
                summary=f"检测到低用户响应率: {response_rate:.1%}",
                details=f"最近 {len(experiences)} 次经验中，用户响应率仅 {response_rate:.1%}，可能需要调整交互方式。",
                pattern_detected="low_user_response",
                pattern_frequency=int(total * (1 - response_rate)),
                suggested_adjustments=["调整消息内容和语气", "降低主动行为频率", "等待用户主动发起"],
                confidence=0.65,
                used_llm=False,
            ))

        # 模式 4: 高频用户消息触发
        if most_common_trigger == "user_message" and trigger_counter["user_message"] >= 5:
            insights.append(ReflectionInsight(
                experience_ids=experience_ids,
                insight_type="pattern",
                summary="检测到高频用户消息触发",
                details=f"最近 {len(experiences)} 次经验中，用户消息触发占主导，可能表明用户活跃度较高。",
                pattern_detected="high_user_activity",
                pattern_frequency=trigger_counter["user_message"],
                suggested_adjustments=["保持当前响应策略", "适当增加主动关心"],
                confidence=0.55,
                used_llm=False,
            ))

        # 返回最重要的洞察（按置信度排序）
        if insights:
            insights.sort(key=lambda i: i.confidence, reverse=True)
            return insights[0]

        # 无明显模式
        return None

    def get_insights(self) -> List[ReflectionInsight]:
        """获取反思历史"""
        return self._insights.copy()

    def clear_insights(self) -> None:
        """清空反思历史"""
        self._insights = []

    def store_insight_to_growth(self, insight: ReflectionInsight) -> bool:
        """
        存储洞察到 Growth

        Args:
            insight: 反思洞察

        Returns:
            是否成功
        """
        if not self.growth_adapter:
            logger.debug("GrowthAdapter 未配置，跳过存储")
            return False

        try:
            success = self.growth_adapter.store_insight(insight)
            if success:
                logger.info(f"洞察已存储到 Growth: {insight.insight_id}")
            return success
        except Exception as e:
            logger.warning(f"存储洞察失败: {e}")
            return False

    def get_recent_insights(self, limit: int = 10) -> List[ReflectionInsight]:
        """
        获取最近的洞察

        Args:
            limit: 最大数量

        Returns:
            洞察列表
        """
        return self._insights[-limit:] if self._insights else []

    def analyze_trends(self, experiences: List[RuntimeExperience]) -> Dict[str, Any]:
        """
        分析经验趋势（辅助方法）

        Args:
            experiences: 经验列表

        Returns:
            趋势分析结果
        """
        valid_experiences = [e for e in experiences if e.valid]

        if not valid_experiences:
            return {"error": "无有效经验"}

        action_types = [e.action_type for e in valid_experiences]
        trigger_types = [e.trigger_type for e in valid_experiences]

        successes = sum(1 for e in valid_experiences if e.result and e.result.success)
        responses = sum(1 for e in valid_experiences if e.result and e.result.response_received)

        return {
            "total_experiences": len(valid_experiences),
            "action_distribution": dict(Counter(action_types)),
            "trigger_distribution": dict(Counter(trigger_types)),
            "success_rate": successes / len(valid_experiences),
            "response_rate": responses / len(valid_experiences),
            "avg_duration_ms": sum(e.duration_ms for e in valid_experiences) / len(valid_experiences),
        }