# -*- coding: utf-8 -*-
"""
src/runtime/personality_binding/composed_personality_context.py

Phase 4.5: ComposedPersonalityContext —— Runtime 统一人格上下文

职责:
- 聚合 Phase 4.2 / 4.3 / 4.4 的运行时上下文数据
- 作为 PersonalityRuntimeBinding 的最终产物
- 由 ResponseAdapter 注入到 personality_context
- 既可作为 dict 注入,也可作为文本注入到 prompt

字段:
- identity_context:    Dict          # Phase 4.4 IdentityContext 序列化
- reflection_context:  Dict          # Phase 4.3 SelfReflection 上下文
- behavior_context:    Dict          # Phase 4.4 BehaviorSignature + 风格
- growth_context:      Dict          # Phase 4.2 SelfModel 上下文(基础人格)
- consistency_rules:   List[ConsistencyRule]  # 从 BehaviorSignature + Identity 抽取的规则
- meta:                Dict          # 元信息
- schema_version:      str = "1.0"
- has_binding:         bool          # 是否有任何 Phase 输入

约束:
- 不 import 业务实现,仅纯数据
- 不修改 RuntimeContext schema
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from src.runtime.personality_binding.consistency_rules_builder import (
    ConsistencyRule,
)


PERSONA_BINDING_CONTEXT_SCHEMA_VERSION = "1.0"


def _empty_dict() -> Dict[str, Any]:
    return {}


def _empty_list() -> List["ConsistencyRule"]:
    return []


@dataclass
class ComposedPersonalityContext:
    """Runtime 统一人格上下文(Phase 4.5 / v1.0)。

    作为 PersonalityRuntimeBinding 协调 Phase 4.2 / 4.3 / 4.4 后
    的最终输出,可直接被 ResponseAdapter 注入到 personality_context。
    """

    # Phase 4.4: Identity 上下文(来自 SelfIdentityRuntime)
    identity_context: Dict[str, Any] = field(default_factory=_empty_dict)
    # Phase 4.3: Reflection 上下文(来自 SelfReflectionContextProvider)
    reflection_context: Dict[str, Any] = field(default_factory=_empty_dict)
    # Phase 4.4: Behavior 上下文(来自 BehaviorSignatureProvider)
    behavior_context: Dict[str, Any] = field(default_factory=_empty_dict)
    # Phase 4.2: Growth/SelfModel 上下文(来自 SelfModelContextProvider)
    growth_context: Dict[str, Any] = field(default_factory=_empty_dict)
    # Phase 4.4 + 4.5: 一致性规则(ConsistencyRule 对象列表)
    consistency_rules: List[ConsistencyRule] = field(
        default_factory=_empty_list,
    )
    # 元信息
    meta: Dict[str, Any] = field(default_factory=_empty_dict)
    # 标识
    schema_version: str = PERSONA_BINDING_CONTEXT_SCHEMA_VERSION
    has_binding: bool = False
    identity_id: str = ""
    timestamp: str = ""
    version: int = 0
    source: str = "personality_runtime_binding"

    def __post_init__(self) -> None:
        """规范化 consistency_rules 字段: dict → ConsistencyRule 对象。

        允许上层调用方直接传入 dict 列表(测试/兼容性场景),
        在此处统一转换为 ConsistencyRule 对象,后续 .category/.text
        等属性访问与序列化都保持一致。
        """
        normalized: List[ConsistencyRule] = []
        for r in (self.consistency_rules or []):
            if isinstance(r, ConsistencyRule):
                normalized.append(r)
            elif isinstance(r, dict):
                try:
                    normalized.append(ConsistencyRule.from_dict(r))
                except Exception:  # noqa: BLE001
                    continue
        # 不可变 dataclass: 通过 object.__setattr__ 绕过 frozen 限制
        # 本类未声明 frozen, 直接赋值即可
        self.consistency_rules = normalized

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # 将 ConsistencyRule 转换为 dict(序列化友好)
        d["consistency_rules"] = [
            r.to_dict() if isinstance(r, ConsistencyRule) else r
            for r in self.consistency_rules
        ]
        return d

    @classmethod
    def from_dict(
        cls, data: Optional[Dict[str, Any]] = None,
    ) -> "ComposedPersonalityContext":
        payload = dict(data or {})
        # 解析 consistency_rules: dict → ConsistencyRule 对象
        rules_raw = payload.get("consistency_rules") or []
        rules: List[ConsistencyRule] = []
        for r in rules_raw:
            if isinstance(r, ConsistencyRule):
                rules.append(r)
            elif isinstance(r, dict):
                try:
                    rules.append(ConsistencyRule.from_dict(r))
                except Exception:  # noqa: BLE001
                    # 失败的规则直接跳过,不影响其它
                    continue
        return cls(
            identity_context=dict(payload.get("identity_context") or {}),
            reflection_context=dict(
                payload.get("reflection_context") or {},
            ),
            behavior_context=dict(payload.get("behavior_context") or {}),
            growth_context=dict(payload.get("growth_context") or {}),
            consistency_rules=rules,
            meta=dict(payload.get("meta") or {}),
            schema_version=str(
                payload.get("schema_version")
                or PERSONA_BINDING_CONTEXT_SCHEMA_VERSION,
            ),
            has_binding=bool(payload.get("has_binding", False)),
            identity_id=str(payload.get("identity_id", "") or ""),
            timestamp=str(payload.get("timestamp", "") or ""),
            version=int(payload.get("version", 0) or 0),
            source=str(
                payload.get("source", "")
                or "personality_runtime_binding",
            ),
        )

    def is_empty(self) -> bool:
        """是否没有任何实质内容。"""
        return (
            not self.identity_context
            and not self.reflection_context
            and not self.behavior_context
            and not self.growth_context
            and not self.consistency_rules
        )

    def has_identity(self) -> bool:
        return bool(self.identity_context)

    def has_reflection(self) -> bool:
        return bool(self.reflection_context)

    def has_behavior(self) -> bool:
        return bool(self.behavior_context)

    def has_growth(self) -> bool:
        return bool(self.growth_context)

    def has_consistency_rules(self) -> bool:
        return bool(self.consistency_rules)


__all__ = [
    "ComposedPersonalityContext",
    "PERSONA_BINDING_CONTEXT_SCHEMA_VERSION",
]
