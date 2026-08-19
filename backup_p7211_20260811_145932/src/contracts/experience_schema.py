"""
Experience Schema —— 经验与反思层契约

定义 Runtime Experience & Reflection Layer 的数据交换格式。
与 src/contracts/cognitive_schema.py 风格一致。

设计原则：
- Event + Action + Result → Experience
- Experience → ReflectionInsight（第一阶段规则生成，不接入 LLM）
- MemoryAdapter/GrowthAdapter 只定义接口，不真正写入
- 所有字段可序列化，便于日志和审计
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ==================== 经验结果 ====================

@dataclass
class ActionResult:
    """
    Action 执行结果

    记录 Action 执行后的状态变化和副作用。
    """
    action_id: str = ""
    success: bool = False
    error_message: str = ""
    response_received: bool = False          # 是否收到用户响应
    user_response: str = ""                  # 用户响应内容
    state_changes: Dict[str, Any] = field(default_factory=dict)  # SelfState 变化
    side_effects: List[str] = field(default_factory=list)        # 副作用记录
    timestamp: str = field(default_factory=_now_iso)


@dataclass
class RuntimeExperience:
    """
    运行时经验

    将 Event → Decision → Action → Result 的完整链路打包为可反思的经验单元。
    """
    experience_id: str = field(default_factory=lambda: f"exp_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=_now_iso)

    # 来源
    trigger_event: Dict[str, Any] = field(default_factory=dict)  # 触发事件
    trigger_type: str = "unknown"                                # event/tick/user_message

    # 决策过程
    decision_source: str = "unknown"      # rule/cognitive
    intention_id: str = ""                # 关联的意图 ID
    action_type: str = ""                 # 执行的 Action 类型

    # 执行结果
    result: Optional[ActionResult] = None

    # 上下文快照
    self_state_before: Dict[str, Any] = field(default_factory=dict)
    self_state_after: Dict[str, Any] = field(default_factory=dict)

    # 元信息
    duration_ms: float = 0.0              # 从事件到结果的总耗时
    valid: bool = True                    # 是否为有效经验（用于过滤）
    invalid_reason: str = ""              # 无效原因

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "experience_id": self.experience_id,
            "timestamp": self.timestamp,
            "trigger_event": self.trigger_event,
            "trigger_type": self.trigger_type,
            "decision_source": self.decision_source,
            "intention_id": self.intention_id,
            "action_type": self.action_type,
            "result": {
                "action_id": self.result.action_id,
                "success": self.result.success,
                "error_message": self.result.error_message,
                "response_received": self.result.response_received,
                "user_response": self.result.user_response,
                "state_changes": self.result.state_changes,
                "side_effects": self.result.side_effects,
                "timestamp": self.result.timestamp,
            } if self.result else None,
            "self_state_before": self.self_state_before,
            "self_state_after": self.self_state_after,
            "duration_ms": self.duration_ms,
            "valid": self.valid,
            "invalid_reason": self.invalid_reason,
        }


# ==================== 反思洞察 ====================

@dataclass
class ReflectionInsight:
    """
    反思洞察

    从一批 Experience 中提炼出的模式、问题和改进建议。
    """
    insight_id: str = field(default_factory=lambda: f"ins_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=_now_iso)

    # 来源
    experience_ids: List[str] = field(default_factory=list)  # 关联的经验 ID

    # 洞察内容
    insight_type: str = "pattern"       # pattern/problem/improvement
    summary: str = ""                   # 一句话总结
    details: str = ""                   # 详细描述

    # 模式识别（规则生成）
    pattern_detected: str = ""          # 检测到的模式名称
    pattern_frequency: int = 0          # 该模式在经验中出现的频率

    # 改进建议
    suggested_adjustments: List[str] = field(default_factory=list)

    # Phase 3.5.29: 深层自省字段（向后兼容，默认空）
    observation: str = ""                # 观察到的事实
    evidence: List[Dict[str, Any]] = field(default_factory=list)   # 证据链
    interpretation: str = ""             # 对事实的解释
    uncertainty: str = ""                # 不确定性说明
    related_memories: List[str] = field(default_factory=list)      # 关联记忆 ID
    identity_impact: Dict[str, Any] = field(default_factory=dict)  # 对身份/自我模型的影响

    # 元信息
    confidence: float = 0.5             # 置信度（规则生成时固定）
    used_llm: bool = False              # 是否使用了 LLM

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "insight_id": self.insight_id,
            "timestamp": self.timestamp,
            "experience_ids": self.experience_ids,
            "insight_type": self.insight_type,
            "summary": self.summary,
            "details": self.details,
            "pattern_detected": self.pattern_detected,
            "pattern_frequency": self.pattern_frequency,
            "suggested_adjustments": self.suggested_adjustments,
            "observation": self.observation,
            "evidence": self.evidence,
            "interpretation": self.interpretation,
            "uncertainty": self.uncertainty,
            "related_memories": self.related_memories,
            "identity_impact": self.identity_impact,
            "confidence": self.confidence,
            "used_llm": self.used_llm,
        }


# ==================== 适配器接口 ====================

class MemoryAdapterBase:
    """
    Memory 适配器基类

    定义 Experience 写入 Memory 的接口。
    第一阶段只定义接口，不真正实现。
    """

    def store_experience(self, experience: RuntimeExperience) -> bool:
        """
        存储经验到 Memory

        Args:
            experience: 运行时经验

        Returns:
            是否存储成功
        """
        raise NotImplementedError("MemoryAdapter.store_experience 未实现")

    def get_recent_experiences(self, limit: int = 10) -> List[RuntimeExperience]:
        """
        获取最近的经验

        Args:
            limit: 最大数量

        Returns:
            经验列表
        """
        raise NotImplementedError("MemoryAdapter.get_recent_experiences 未实现")

    def search_experiences(
        self,
        action_type: Optional[str] = None,
        trigger_type: Optional[str] = None,
        limit: int = 10,
    ) -> List[RuntimeExperience]:
        """
        搜索经验

        Args:
            action_type: Action 类型过滤
            trigger_type: 触发类型过滤
            limit: 最大数量

        Returns:
            经验列表
        """
        raise NotImplementedError("MemoryAdapter.search_experiences 未实现")


class GrowthAdapterBase:
    """
    Growth 适配器基类

    定义 Reflection 写入 Growth 的接口。
    第一阶段只定义接口，不真正实现。
    """

    def store_insight(self, insight: ReflectionInsight) -> bool:
        """
        存储洞察到 Growth

        Args:
            insight: 反思洞察

        Returns:
            是否存储成功
        """
        raise NotImplementedError("GrowthAdapter.store_insight 未实现")

    def get_recent_insights(self, limit: int = 10) -> List[ReflectionInsight]:
        """
        获取最近的洞察

        Args:
            limit: 最大数量

        Returns:
            洞察列表
        """
        raise NotImplementedError("GrowthAdapter.get_recent_insights 未实现")


# ==================== 经验验证 ====================

@dataclass
class ExperienceValidator:
    """
    经验验证器

    过滤无效经验，避免污染反思过程。
    """
    min_duration_ms: float = 10.0       # 最小耗时（低于此值可能为无效操作）
    max_duration_ms: float = 60000.0    # 最大耗时（超过此值可能为异常）
    require_result: bool = True         # 是否要求有结果

    def validate(self, experience: RuntimeExperience) -> bool:
        """
        验证经验是否有效

        Args:
            experience: 待验证的经验

        Returns:
            是否有效
        """
        # 检查耗时范围
        if experience.duration_ms < self.min_duration_ms:
            experience.valid = False
            experience.invalid_reason = f"duration_too_short: {experience.duration_ms}ms"
            return False

        if experience.duration_ms > self.max_duration_ms:
            experience.valid = False
            experience.invalid_reason = f"duration_too_long: {experience.duration_ms}ms"
            return False

        # 检查是否有结果
        if self.require_result and experience.result is None:
            experience.valid = False
            experience.invalid_reason = "missing_result"
            return False

        # 检查必要的字段
        if not experience.action_type:
            experience.valid = False
            experience.invalid_reason = "missing_action_type"
            return False

        experience.valid = True
        experience.invalid_reason = ""
        return True
