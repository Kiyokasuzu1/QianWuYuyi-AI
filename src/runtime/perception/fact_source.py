# -*- coding: utf-8 -*-
"""
src/runtime/perception/fact_source.py

Phase 3.8.x: Fact 来源枚举

定义一条信息（Fact）从何处获得。
所有 Runtime / Adapter 构造 Fact 时必须显式标记 source，
以便 PerceptionGuard 在生成回复前判断该信息是否为「推测」。

枚举值：
- USER_INPUT: 用户直接输入（最高可信度）
- MEMORY:     从记忆系统检索得到（有历史依据）
- VISION:     来自视觉 / 外部感知输入
- SYSTEM:     系统级 / 配置级输入
- INFERENCE:  由模型推理 / 推测生成（最低可信度，必须降级表达）
"""
from __future__ import annotations

from enum import Enum


class FactSource(str, Enum):
    """信息可信度来源标记（v1.0）。"""

    USER_INPUT = "user_input"
    MEMORY = "memory"
    VISION = "vision"
    SYSTEM = "system"
    INFERENCE = "inference"

    @property
    def is_grounded(self) -> bool:
        """是否属于有外部依据（非推测）的来源。"""
        return self != FactSource.INFERENCE

    @property
    def trust_score(self) -> float:
        """信任度分数（0.0 ~ 1.0），供下游决策参考。"""
        mapping = {
            FactSource.USER_INPUT: 1.0,
            FactSource.SYSTEM: 0.95,
            FactSource.VISION: 0.9,
            FactSource.MEMORY: 0.8,
            FactSource.INFERENCE: 0.3,
        }
        return mapping[self]


# 公开的别名，方便直接使用
USER_INPUT = FactSource.USER_INPUT
MEMORY = FactSource.MEMORY
VISION = FactSource.VISION
SYSTEM = FactSource.SYSTEM
INFERENCE = FactSource.INFERENCE


__all__ = ["FactSource", "USER_INPUT", "MEMORY", "VISION", "SYSTEM", "INFERENCE"]
