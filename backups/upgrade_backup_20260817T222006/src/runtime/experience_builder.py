"""
Experience Builder —— 经验构建器

职责：
- 接收 Event + Action + Result
- 构建 RuntimeExperience
- 管理经验缓冲区
- 与 MemoryAdapter 交互（第一阶段为接口）

设计原则：
- 不修改 Memory 核心逻辑
- 经验构建完成后通过 MemoryAdapter 接口传递
- 第一阶段只记录，不真正写入
"""

import time
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from src.contracts.experience_schema import (
    RuntimeExperience,
    ActionResult,
    ExperienceValidator,
    MemoryAdapterBase,
)

logger = logging.getLogger(__name__)


@dataclass
class ExperienceBuilderConfig:
    """经验构建器配置"""
    max_buffer_size: int = 100           # 最大缓冲区大小
    auto_validate: bool = True           # 自动验证
    auto_flush: bool = False             # 自动刷新到 Memory
    flush_interval: int = 10             # 刷新间隔（经验数量）
    max_building_size: int = 100         # Phase 4.4-A1（P2）：构建中悬挂上限


class ExperienceBuilder:
    """
    经验构建器

    将 Event → Decision → Action → Result 的完整链路打包为经验。
    """

    def __init__(
        self,
        config: Optional[ExperienceBuilderConfig] = None,
        memory_adapter: Optional[MemoryAdapterBase] = None,
    ):
        self.config = config or ExperienceBuilderConfig()
        self.memory_adapter = memory_adapter
        self.validator = ExperienceValidator()

        # 经验缓冲区
        self._buffer: List[RuntimeExperience] = []

        # 构建中状态（用于追踪单次构建过程）
        self._building: Dict[str, Any] = {}  # experience_id -> {start_time, state_before, ...}

    def start_building(
        self,
        trigger_event: Dict[str, Any],
        trigger_type: str,
        self_state_before: Dict[str, Any],
    ) -> str:
        """
        开始构建经验

        Args:
            trigger_event: 触发事件
            trigger_type: 触发类型
            self_state_before: 执行前的 SelfState

        Returns:
            构建中的经验 ID
        """
        from src.contracts.experience_schema import RuntimeExperience
        import uuid

        experience_id = f"exp_{uuid.uuid4().hex[:8]}"

        # Phase 4.4-A1（P2）：悬挂上限 —— 超限丢弃最旧构建中经历，
        # 防止永不 finish 的场景（如历史 chat 轮）导致无界累积
        cap = int(getattr(self.config, "max_building_size", 100) or 100)
        if len(self._building) >= cap:
            try:
                oldest_id = min(
                    self._building,
                    key=lambda k: self._building[k].get("start_time", 0.0),
                )
                dropped = self._building.pop(oldest_id, None)
                logger.warning(
                    "[ExperienceBuilder] 构建中悬挂超限（cap=%d），丢弃最旧: %s",
                    cap, oldest_id,
                )
            except Exception:  # noqa: BLE001
                pass

        self._building[experience_id] = {
            "start_time": time.time(),
            "trigger_event": trigger_event,
            "trigger_type": trigger_type,
            "self_state_before": self_state_before,
            "decision_source": "unknown",
            "intention_id": "",
            "action_type": "",
        }

        return experience_id

    def record_decision(
        self,
        experience_id: str,
        decision_source: str,
        intention_id: str,
        action_type: str,
    ) -> None:
        """
        记录决策信息

        Args:
            experience_id: 经验 ID
            decision_source: 决策来源（rule/cognitive）
            intention_id: 意图 ID
            action_type: Action 类型
        """
        if experience_id not in self._building:
            logger.warning(f"未找到构建中的经验: {experience_id}")
            return

        self._building[experience_id]["decision_source"] = decision_source
        self._building[experience_id]["intention_id"] = intention_id
        self._building[experience_id]["action_type"] = action_type

    def finish_building(
        self,
        experience_id: str,
        result: ActionResult,
        self_state_after: Dict[str, Any],
    ) -> Optional[RuntimeExperience]:
        """
        完成构建经验

        Args:
            experience_id: 经验 ID
            result: Action 执行结果
            self_state_after: 执行后的 SelfState

        Returns:
            构建完成的经验，若无效则返回 None
        """
        if experience_id not in self._building:
            logger.warning(f"未找到构建中的经验: {experience_id}")
            return None

        building = self._building.pop(experience_id)

        # 计算耗时
        duration_ms = (time.time() - building["start_time"]) * 1000

        # 构建经验
        experience = RuntimeExperience(
            experience_id=experience_id,
            trigger_event=building["trigger_event"],
            trigger_type=building["trigger_type"],
            decision_source=building["decision_source"],
            intention_id=building["intention_id"],
            action_type=building["action_type"],
            result=result,
            self_state_before=building["self_state_before"],
            self_state_after=self_state_after,
            duration_ms=duration_ms,
        )

        # 验证
        if self.config.auto_validate:
            is_valid = self.validator.validate(experience)
            if not is_valid:
                logger.debug(f"经验无效: {experience.invalid_reason}")
                return None

        # 加入缓冲区
        self._buffer.append(experience)

        # 缓冲区溢出处理
        if len(self._buffer) > self.config.max_buffer_size:
            self._buffer = self._buffer[-self.config.max_buffer_size:]

        # 自动刷新
        if self.config.auto_flush and len(self._buffer) >= self.config.flush_interval:
            self.flush_to_memory()

        logger.debug(f"经验构建完成: {experience_id}, action={experience.action_type}, duration={duration_ms:.1f}ms")

        return experience

    def cancel_building(self, experience_id: str) -> None:
        """
        取消构建

        Args:
            experience_id: 经验 ID
        """
        if experience_id in self._building:
            del self._building[experience_id]
            logger.debug(f"经验构建取消: {experience_id}")

    def flush_to_memory(self) -> int:
        """
        刷新缓冲区到 Memory

        Returns:
            刷新的经验数量
        """
        if not self.memory_adapter:
            logger.debug("MemoryAdapter 未配置，跳过刷新")
            return 0

        if not self._buffer:
            return 0

        count = 0
        for experience in self._buffer:
            try:
                success = self.memory_adapter.store_experience(experience)
                if success:
                    count += 1
            except Exception as e:
                logger.warning(f"存储经验失败: {e}")

        self._buffer = []
        logger.info(f"刷新 {count} 条经验到 Memory")
        return count

    def get_buffer(self) -> List[RuntimeExperience]:
        """获取缓冲区内容"""
        return self._buffer.copy()

    def clear_buffer(self) -> None:
        """清空缓冲区"""
        self._buffer = []

    def get_building_count(self) -> int:
        """获取构建中的经验数量"""
        return len(self._building)

    def build_from_action_result(
        self,
        action_id: str,
        action_type: str,
        trigger_event: Dict[str, Any],
        trigger_type: str,
        decision_source: str,
        self_state_before: Dict[str, Any],
        self_state_after: Dict[str, Any],
        success: bool = True,
        error_message: str = "",
        user_response: str = "",
    ) -> Optional[RuntimeExperience]:
        """
        从 Action 结果构建经验（快捷方法）

        Args:
            action_id: Action ID
            action_type: Action 类型
            trigger_event: 触发事件
            trigger_type: 触发类型
            decision_source: 决策来源
            self_state_before: 执行前 SelfState
            self_state_after: 执行后 SelfState
            success: 是否成功
            error_message: 错误信息
            user_response: 用户响应

        Returns:
            构建完成的经验
        """
        start_time = time.time()

        result = ActionResult(
            action_id=action_id,
            success=success,
            error_message=error_message,
            response_received=bool(user_response),
            user_response=user_response,
            state_changes={
                k: (self_state_before.get(k), self_state_after.get(k))
                for k in self_state_after.keys()
                if k in self_state_before and self_state_before.get(k) != self_state_after.get(k)
            },
        )

        duration_ms = (time.time() - start_time) * 1000

        experience = RuntimeExperience(
            trigger_event=trigger_event,
            trigger_type=trigger_type,
            decision_source=decision_source,
            action_type=action_type,
            result=result,
            self_state_before=self_state_before,
            self_state_after=self_state_after,
            duration_ms=duration_ms,
        )

        # 验证
        if self.config.auto_validate:
            is_valid = self.validator.validate(experience)
            if not is_valid:
                logger.debug(f"经验无效: {experience.invalid_reason}")
                return None

        self._buffer.append(experience)

        if len(self._buffer) > self.config.max_buffer_size:
            self._buffer = self._buffer[-self.config.max_buffer_size:]

        return experience