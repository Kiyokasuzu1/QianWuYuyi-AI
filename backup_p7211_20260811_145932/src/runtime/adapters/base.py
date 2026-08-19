# src/runtime/adapters/base.py
"""
AdapterBase —— Runtime Adapter Layer 抽象基类（Phase 3.7.1）

设计原则：
- 不实现任何业务逻辑（仅接口契约）
- 所有 Adapter 必须继承 AdapterBase
- 提供统一的生命周期接口（attach / detach / health_check）
- Runtime 只依赖 AdapterBase / 各 Adapter 抽象类，**不** import 业务实现

依赖：
- 仅 stdlib + typing
- 禁止 import src.memory / src.emotion / src.personality / src.growth
  / src.contracts.proposal_normalizer
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Phase 3.7.1: Adapter Schema 版本
ADAPTER_SCHEMA_VERSION = "1.0"


class AdapterBase(ABC):
    """所有 Runtime Adapter 的抽象基类（v1.0）

    生命周期（与 docs/runtime_adapter.md §4 对齐）：

        attach()  ─►  active  ─►  health_check()  ─►  ...  ─►  detach()

    字段：
    - name:        str                    # Adapter 名
    - schema_version: str                  # 冻结于 v1.0
    - _attached:   bool                   # attach 状态

    子类必须实现：
    - attach()        - 接入 Runtime（建立依赖、加载初始状态）
    - detach()        - 解除接入（清理、关闭）
    - health_check()  - 健康检查
    """

    name: str = "adapter"
    schema_version: str = ADAPTER_SCHEMA_VERSION

    def __init__(self) -> None:
        self._attached: bool = False
        self._last_health: Optional[Dict[str, Any]] = None

    # --------------------------------------------------------
    # 生命周期接口
    # --------------------------------------------------------
    @abstractmethod
    def attach(self) -> None:
        """接入 Runtime。

        - 建立与业务模块的连接（注入 port / store / reference）
        - 加载必要的初始状态
        - 失败时抛 RuntimeError

        设计阶段只定义接口，不实现具体连接。
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.attach() 未实现"
        )

    @abstractmethod
    def detach(self) -> None:
        """解除接入。

        - 清理资源
        - 关闭连接
        - 幂等
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.detach() 未实现"
        )

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """健康检查。

        Returns:
            {
                "healthy": bool,
                "name": str,
                "schema_version": str,
                "details": Dict[str, Any],  # 可选
            }
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.health_check() 未实现"
        )

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def is_attached(self) -> bool:
        return self._attached

    def _mark_attached(self) -> None:
        self._attached = True

    def _mark_detached(self) -> None:
        self._attached = False

    def _cache_health(self, result: Dict[str, Any]) -> None:
        self._last_health = result
