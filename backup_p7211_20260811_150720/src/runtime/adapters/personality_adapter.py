# src/runtime/adapters/personality_adapter.py
"""
PersonalityAdapter —— Personality 模块 Adapter 接口骨架（Phase 3.7.1）

仅定义接口，不实现业务逻辑。
Runtime 通过 PersonalityAdapter 访问 Personality 模块，避免直接 import PersonalityResolver。

依赖：仅 stdlib + 同包 base + RuntimeContext
禁止：import src.personality.*（业务实现）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.runtime.adapters.base import AdapterBase
from src.runtime.context import RuntimeContext


class PersonalityAdapter(AdapterBase):
    """Personality 模块 Adapter（v1.0）

    职责：
    - 封装 Personality 系统的访问
    - 提供统一的人格快照 / 应用更新入口
    - Runtime 不直接依赖 PersonalityResolver

    接口：
    - snapshot()             - 获取当前人格快照
    - apply_update(proposal) - 应用一个成长提案
    """

    name: str = "personality_adapter"
    schema_version: str = "1.0"

    def __init__(self) -> None:
        super().__init__()

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 Personality 模块（设计阶段仅标记状态）。"""
        self._mark_attached()
        self._cache_health(
            {"healthy": True, "name": self.name, "schema_version": self.schema_version}
        )

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
    # Personality 业务接口（接口骨架，不实现）
    # --------------------------------------------------------
    def snapshot(self) -> Any:
        """获取当前人格快照（设计阶段仅返回 None）。

        Returns:
            人格快照（业务实现时返回 PersonalitySnapshot）
        """
        raise NotImplementedError(
            "PersonalityAdapter.snapshot() 接口未实现（设计阶段）"
        )

    def apply_update(self, proposal: Any) -> Any:
        """应用一个成长提案（设计阶段仅返回 None）。

        Args:
            proposal: GrowthProposal（canonical schema 来自 src.contracts.growth_schema）

        Returns:
            应用结果（业务实现时返回 UpdateResult）
        """
        raise NotImplementedError(
            "PersonalityAdapter.apply_update() 接口未实现（设计阶段）"
        )
