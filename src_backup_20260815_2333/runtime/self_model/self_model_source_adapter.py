# -*- coding: utf-8 -*-
# src/runtime/self_model/self_model_source_adapter.py
"""
Phase 4.2.2: SelfModelSourceAdapter —— 数据源 Adapter 抽象基类

职责:
- 定义 SelfModel 数据源 Adapter 的统一接口
- 桥接 Runtime → Adapter → 现有模块(Growth / Memory / Personality / Emotion)
- 隔离业务实现:本模块不 import 任何业务模块
- 通过 attach / detach / health_check 生命周期管理

设计原则:
- 继承 AdapterBase(统一生命周期)
- 每个数据源 Adapter 实现 extract_inputs() 方法
- extract_inputs() 返回统一的 Dict,SelfModelFoundation 可直接消费
- 不调用 LLM,纯结构化数据提取
- 任何异常被静默吞掉,返回 {} 表示无数据

数据契约(extract_inputs 返回):
- trait_states:    Dict[str, Dict]   # 稳定特质(name -> {current_value, ...})
- growth_records:  List[Dict]        # 成长记录
- core_values:     List[Dict]        # 核心价值观
- preferences:     List[Dict]        # 偏好
- current_state:   Dict[str, Any]    # 当前运行时人格快照
- extra_entries:   List[SelfModelEntry]
- relationship_state: Dict            # 关系状态摘要
- identity_overrides: Dict            # 身份覆盖

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality / src.memory / src.emotion / src.growth
  (业务模块由具体 Adapter 内部延迟 import,基类保持纯抽象)
- 不修改 RuntimeContext schema
- 不修改 Personality 核心模块
"""
from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any, Dict, List, Optional

from src.runtime.adapters.base import AdapterBase


logger = logging.getLogger(__name__)


SELF_MODEL_SOURCE_ADAPTER_SCHEMA_VERSION = "1.0"


# 数据源类型常量(用于 Registry 索引)
SOURCE_TYPE_GROWTH = "growth"
SOURCE_TYPE_MEMORY = "memory"
SOURCE_TYPE_PERSONALITY = "personality"
SOURCE_TYPE_EMOTION = "emotion"

VALID_SOURCE_TYPES = frozenset({
    SOURCE_TYPE_GROWTH,
    SOURCE_TYPE_MEMORY,
    SOURCE_TYPE_PERSONALITY,
    SOURCE_TYPE_EMOTION,
})


class SelfModelSourceAdapter(AdapterBase):
    """SelfModel 数据源 Adapter 抽象基类(Phase 4.2.2 / v1.0)。

    所有 SelfModel 数据源 Adapter(Growth / Memory / Personality / Emotion)
    都继承此类,实现 extract_inputs() 以提供统一的 SelfModel 数据输入。

    继承:
    - AdapterBase(提供 attach / detach / health_check 生命周期)

    子类必须实现:
    - source_type 属性:    标识数据源类型,必须属于 VALID_SOURCE_TYPES
    - extract_inputs()方法: 返回结构化 Dict,供 SelfModelFoundation.build() 消费

    字段:
    - name:           str                # Adapter 名
    - schema_version: str = "1.0"        # 冻结版本
    - _last_inputs:   Optional[Dict]     # 最近一次 extract_inputs 结果
    - _last_error:    Optional[str]      # 最近一次 extract 错误
    - _extract_count: int                # extract 调用次数
    """

    schema_version: str = SELF_MODEL_SOURCE_ADAPTER_SCHEMA_VERSION

    def __init__(self) -> None:
        super().__init__()
        self._last_inputs: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None
        self._extract_count: int = 0

    # --------------------------------------------------------
    # 子类必须实现
    # --------------------------------------------------------
    @property
    @abstractmethod
    def source_type(self) -> str:
        """数据源类型(必须属于 VALID_SOURCE_TYPES)。"""
        raise NotImplementedError(
            f"{self.__class__.__name__}.source_type 未实现"
        )

    @abstractmethod
    def extract_inputs(self, ctx: Optional[Any] = None) -> Dict[str, Any]:
        """从数据源提取结构化输入,供 SelfModelFoundation.build() 消费。

        设计原则:
        - 返回 Dict 字段必须是 SelfModelFoundation._build_* 接受的字段名
          (trait_states / growth_records / core_values / preferences /
           current_state / extra_entries / relationship_state / identity_overrides)
        - 任何异常必须被内部捕获,返回 {}
        - 不得修改入参 ctx
        - 不得调用 LLM

        Args:
            ctx: Runtime 共享上下文(可选,某些 Adapter 可能需要 ctx 信息)

        Returns:
            结构化输入 Dict(可能为空 {})
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.extract_inputs() 未实现"
        )

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入数据源(设计阶段仅标记状态,具体连接由子类实现)。"""
        self._mark_attached()
        self._cache_health({
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "source_type": self.source_type,
        })

    def detach(self) -> None:
        """解除接入。"""
        self._mark_detached()
        self._last_inputs = None
        self._last_error = None

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": self.is_attached,
            "name": self.name,
            "schema_version": self.schema_version,
            "source_type": getattr(self, "source_type", "unknown"),
            "extract_count": self._extract_count,
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # 安全 extract 包装(异常隔离)
    # --------------------------------------------------------
    def safe_extract(self, ctx: Optional[Any] = None) -> Dict[str, Any]:
        """安全调用 extract_inputs,任何异常被静默吞掉,返回 {}。

        同时:
        - 更新 _last_inputs / _last_error
        - 增加 _extract_count
        """
        try:
            result = self.extract_inputs(ctx)
            # 防御:确保返回 Dict
            if not isinstance(result, dict):
                self._last_error = (
                    f"extract_inputs returned non-dict: {type(result).__name__}"
                )
                result = {}
            else:
                self._last_error = None
            self._last_inputs = dict(result)
            self._extract_count += 1
            return result
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"extract_failed: {exc}"
            self._extract_count += 1
            logger.warning(
                "%s.safe_extract() 失败: %s",
                self.__class__.__name__, exc,
            )
            return {}

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def last_inputs(self) -> Optional[Dict[str, Any]]:
        return self._last_inputs

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def extract_count(self) -> int:
        return self._extract_count

    def describe(self) -> Dict[str, Any]:
        """返回 Adapter 描述(供 Registry / 测试用)。"""
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "source_type": self.source_type,
            "is_attached": self.is_attached,
            "extract_count": self._extract_count,
            "last_error": self._last_error,
        }


__all__ = [
    "SELF_MODEL_SOURCE_ADAPTER_SCHEMA_VERSION",
    "SelfModelSourceAdapter",
    "SOURCE_TYPE_GROWTH",
    "SOURCE_TYPE_MEMORY",
    "SOURCE_TYPE_PERSONALITY",
    "SOURCE_TYPE_EMOTION",
    "VALID_SOURCE_TYPES",
]
