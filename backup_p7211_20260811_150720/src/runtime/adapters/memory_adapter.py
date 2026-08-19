"""
MemoryAdapter —— 经验到记忆的适配器

职责：
- 将 RuntimeExperience 转换为 Memory 格式
- 通过 MemoryStore 实现长期存储
- 提供搜索和查询接口

设计原则：
- 不修改 Memory 核心逻辑
- 只通过 MemoryStore 接口操作
- RuntimeExperience 必须经过 Adapter 才能进入长期系统
"""

import logging
import time
from typing import Any, Dict, List, Optional

from src.contracts.experience_schema import (
    RuntimeExperience,
    MemoryAdapterBase,
)
from src.memory.memory_store import MemoryStore

logger = logging.getLogger(__name__)


class MemoryAdapter(MemoryAdapterBase):
    """
    Memory 适配器

    将 RuntimeExperience 转换为 Memory 条目并存储。
    """

    def __init__(self, memory_store: Optional[MemoryStore] = None, user_id: str = "yuyi"):
        """
        初始化 MemoryAdapter

        Args:
            memory_store: MemoryStore 实例，若为 None 则使用默认路径
            user_id: 用户 ID
        """
        self._store = memory_store or MemoryStore()
        self._user_id = user_id

        # 经验到记忆的映射：experience_id -> memory_id
        self._id_map: Dict[str, str] = {}

    def store_experience(self, experience: RuntimeExperience) -> bool:
        """
        存储经验到 Memory

        Args:
            experience: 运行时经验

        Returns:
            是否存储成功
        """
        if not experience or not experience.valid:
            logger.debug(f"跳过无效经验: {experience.invalid_reason if experience else 'None'}")
            return False

        try:
            # 转换为 Memory 格式
            memory_entry = self._convert_to_memory(experience)

            # 存储到 MemoryStore
            result = self._store.add(memory_entry)

            if result and result.get("id"):
                # 记录映射
                self._id_map[experience.experience_id] = result["id"]
                logger.debug(
                    f"经验已存储到 Memory: {experience.experience_id} -> {result['id']}"
                )
                return True
            else:
                logger.warning(f"MemoryStore.add 返回 None，可能重复或失败")
                return False

        except Exception as e:
            logger.error(f"存储经验到 Memory 失败: {e}")
            return False

    def get_recent_experiences(self, limit: int = 10) -> List[RuntimeExperience]:
        """
        从 Memory 获取最近的经验

        Args:
            limit: 最大数量

        Returns:
            经验列表（从 Memory 反序列化）
        """
        try:
            memories = self._store.load()
            # 过滤经验类型的记忆
            experience_memories = [
                m for m in memories
                if m.get("metadata", {}).get("type") == "runtime_experience"
            ]
            # 按时间倒序
            experience_memories.sort(
                key=lambda m: m.get("timestamp", ""),
                reverse=True,
            )
            # 取最近的
            recent = experience_memories[:limit]

            # 转换回 RuntimeExperience
            return [self._convert_from_memory(m) for m in recent]

        except Exception as e:
            logger.error(f"获取最近经验失败: {e}")
            return []

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
        try:
            memories = self._store.load()
            # 过滤经验类型
            experience_memories = [
                m for m in memories
                if m.get("metadata", {}).get("type") == "runtime_experience"
            ]

            # 按条件过滤
            if action_type:
                experience_memories = [
                    m for m in experience_memories
                    if m.get("metadata", {}).get("action_type") == action_type
                ]

            if trigger_type:
                experience_memories = [
                    m for m in experience_memories
                    if m.get("metadata", {}).get("trigger_type") == trigger_type
                ]

            # 按时间倒序
            experience_memories.sort(
                key=lambda m: m.get("timestamp", ""),
                reverse=True,
            )

            return [self._convert_from_memory(m) for m in experience_memories[:limit]]

        except Exception as e:
            logger.error(f"搜索经验失败: {e}")
            return []

    def get_memory_store(self) -> MemoryStore:
        """获取底层 MemoryStore"""
        return self._store

    def get_experience_memory_id(self, experience_id: str) -> Optional[str]:
        """
        获取经验对应的 Memory ID

        Args:
            experience_id: 经验 ID

        Returns:
            Memory ID，若不存在返回 None
        """
        return self._id_map.get(experience_id)

    def clear_mapping(self) -> None:
        """清空映射"""
        self._id_map.clear()

    # ==================== 内部方法 ====================

    def _convert_to_memory(self, experience: RuntimeExperience) -> Dict[str, Any]:
        """
        将 RuntimeExperience 转换为 Memory 条目

        Args:
            experience: 运行时经验

        Returns:
            Memory 条目字典
        """
        # 构建内容
        content_parts = []
        content_parts.append(f"[RuntimeExperience] {experience.action_type}")

        if experience.trigger_type:
            content_parts.append(f"trigger={experience.trigger_type}")

        if experience.result:
            if experience.result.success:
                content_parts.append("success")
            else:
                content_parts.append(f"failed: {experience.result.error_message}")

            if experience.result.user_response:
                content_parts.append(f"response: {experience.result.user_response[:100]}")

        content_parts.append(f"duration={experience.duration_ms:.0f}ms")

        content = " | ".join(content_parts)

        # 构建元数据
        metadata = {
            "type": "runtime_experience",
            "experience_id": experience.experience_id,
            "action_type": experience.action_type,
            "trigger_type": experience.trigger_type,
            "decision_source": experience.decision_source,
            "success": experience.result.success if experience.result else False,
            "response_received": experience.result.response_received if experience.result else False,
            "user_response": experience.result.user_response if experience.result else None,
            "timestamp": experience.timestamp,
            "duration_ms": experience.duration_ms,
        }

        return {
            "user_id": self._user_id,
            "content": content,
            "role": "system",
            "metadata": metadata,
        }

    def _convert_from_memory(self, memory: Dict[str, Any]) -> RuntimeExperience:
        """
        将 Memory 条目转换回 RuntimeExperience

        Args:
            memory: Memory 条目

        Returns:
            运行时经验
        """
        metadata = memory.get("metadata", {})

        # 构建经验
        experience = RuntimeExperience(
            experience_id=metadata.get("experience_id", ""),
            trigger_type=metadata.get("trigger_type", "unknown"),
            decision_source=metadata.get("decision_source", "unknown"),
            action_type=metadata.get("action_type", ""),
            trigger_event={},
            self_state_before={},
            self_state_after={},
            duration_ms=metadata.get("duration_ms", 0),
            timestamp=memory.get("timestamp", ""),
        )

        # 恢复结果
        from src.contracts.experience_schema import ActionResult
        success = metadata.get("success", False)
        response_received = metadata.get("response_received", False)
        user_response = metadata.get("user_response")
        experience.result = ActionResult(
            action_id=experience.experience_id,
            success=success,
            response_received=response_received,
            user_response=user_response,
        )

        return experience


# ============================================================
# Phase 3.7.1: 新的抽象接口设计
# ============================================================
# 说明：
# - 上方 `MemoryAdapter` 是 Phase 3.5.x 经验→记忆的具体实现（保留供现有代码使用）
# - 下方 `MemoryAdapterSpec` 是 Phase 3.7.1 Runtime Adapter Layer 设计层接口
# - Runtime 接入 Memory 时推荐使用 `MemoryAdapterSpec`（抽象接口）
# - 由于 Python 不允许同名类，新设计采用 `MemoryAdapterSpec` 命名以避免与遗留实现冲突

from src.runtime.adapters.base import AdapterBase as _AdapterBase
from src.runtime.context import RuntimeContext as _RuntimeContext
from src.runtime.events import Event as _Event


class MemoryAdapterSpec(_AdapterBase):
    """Memory 模块 Adapter 抽象接口（Phase 3.7.1 / v1.0）

    Runtime 接入 Memory 系统的统一入口。

    职责：
    - 封装 Memory 系统访问
    - Runtime 不直接依赖 MemoryService / MemoryStore

    接口（v1.0 冻结）：
    - retrieve(context) - 检索相关记忆并填充 context.memory_context
    - store(event)      - 存储事件到长期 Memory

    继承：
    - AdapterBase（提供 attach / detach / health_check 生命周期）
    """
    name: str = "memory_adapter_spec"
    schema_version: str = "1.0"

    def __init__(self) -> None:
        super().__init__()

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 Memory 系统（设计阶段仅标记状态）。"""
        self._mark_attached()
        self._cache_health({
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
        })

    def detach(self) -> None:
        """解除接入。"""
        self._mark_detached()

    def health_check(self) -> Dict[str, Any]:
        result = {
            "healthy": self.is_attached,
            "name": self.name,
            "schema_version": self.schema_version,
        }
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # Memory 业务接口（接口骨架，不实现）
    # --------------------------------------------------------
    def retrieve(self, context: _RuntimeContext) -> Any:
        """检索相关记忆（设计阶段仅返回 None，不实现具体检索）。

        Args:
            context: Runtime 共享上下文（运行时填入 user_input / session_id）

        Returns:
            检索结果（业务实现时返回 MemoryContext）
        """
        raise NotImplementedError(
            "MemoryAdapterSpec.retrieve() 接口未实现（设计阶段）"
        )

    def store(self, event: _Event) -> Any:
        """存储事件到长期 Memory（设计阶段仅返回 None）。

        Args:
            event: Runtime Event

        Returns:
            存储结果（业务实现时返回 MemoryEntry）
        """
        raise NotImplementedError(
            "MemoryAdapterSpec.store() 接口未实现（设计阶段）"
        )
