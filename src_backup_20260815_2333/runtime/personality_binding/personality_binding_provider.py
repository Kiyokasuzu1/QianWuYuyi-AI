# -*- coding: utf-8 -*-
"""
src/runtime/personality_binding/personality_binding_provider.py

Phase 4.5: PersonalityBindingProvider —— 暴露给 ResponseAdapter 的 Provider

职责:
- 对外提供统一接口: provide(ctx_payload) / format_context_for_prompt(...)
- 内部封装 PersonalityContextComposer / ConsistencyRulesBuilder /
  PersonalityPromptFormatter,使 ResponseAdapter 只需注入一个 provider。
- 支持"先 build 后缓存",减少每次 build_request 的重复计算。
- 失败隔离:任何异常被静默吞掉,返回空 dict / 空字符串。

输入 contract(provide 的 ctx_payload):
- Dict:  直接作为 ComposedPersonalityContext
- ComposedPersonalityContext: 直接使用
- None:  返回空 dict {}

输出:
- provide(ctx)              -> Dict[str, Any]   # 注入 personality_context
- format_for_prompt(ctx)    -> str              # 注入 system_prompt 文本
- format_context_for_prompt(dict) -> str        # 接受已 provide 的 dict

约束:
- 不 import 业务实现
- 不 import openai / qwen / llava / vision SDK
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- Runtime → Service → Snapshot 单向依赖
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.personality_binding.composed_personality_context import (
    ComposedPersonalityContext,
)
from src.runtime.personality_binding.consistency_rules_builder import (
    ConsistencyRulesBuilder,
)
from src.runtime.personality_binding.personality_context_composer import (
    PersonalityContextComposer,
)
from src.runtime.personality_binding.personality_prompt_formatter import (
    PersonalityPromptFormatter,
)


logger = logging.getLogger(__name__)


PERSONALITY_BINDING_PROVIDER_SCHEMA_VERSION = "1.0"


class PersonalityBindingProvider:
    """Phase 4.5 暴露给 ResponseAdapter 的统一 Provider。

    使用方式:
        provider = PersonalityBindingProvider()
        # 方式 A: 直接传入 ComposedPersonalityContext
        adapter.set_personality_binding_provider(provider)
        adapter.build_request(ctx, prc)
        # 方式 B: 配合 PersonalityRuntimeBinding 使用
        runtime_binding = PersonalityRuntimeBinding()
        # 在 Runtime 阶段:
        composed = runtime_binding.build_for_runtime(...)
        # 在 ResponseAdapter 阶段:
        provider.set_composed(composed)
    """

    name: str = "personality_binding_provider"
    schema_version: str = PERSONALITY_BINDING_PROVIDER_SCHEMA_VERSION

    def __init__(
        self,
        composer: Optional[PersonalityContextComposer] = None,
        rules_builder: Optional[ConsistencyRulesBuilder] = None,
        formatter: Optional[PersonalityPromptFormatter] = None,
    ) -> None:
        self._composer: PersonalityContextComposer = (
            composer or PersonalityContextComposer()
        )
        self._rules_builder: ConsistencyRulesBuilder = (
            rules_builder or ConsistencyRulesBuilder()
        )
        self._formatter: PersonalityPromptFormatter = (
            formatter or PersonalityPromptFormatter()
        )
        # 缓存最近一次 composed,供 ResponseAdapter 在没传入 ctx 时使用
        self._last_composed: Optional[ComposedPersonalityContext] = None
        # 缓存已 provide 出来的 dict(以 ctx_payload id 区分)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._provide_count: int = 0
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 组件访问
    # --------------------------------------------------------
    @property
    def composer(self) -> PersonalityContextComposer:
        return self._composer

    @property
    def rules_builder(self) -> ConsistencyRulesBuilder:
        return self._rules_builder

    @property
    def formatter(self) -> PersonalityPromptFormatter:
        return self._formatter

    @property
    def provider(self) -> "PersonalityBindingProvider":
        """Phase 4.5: 自引用属性(用于 RuntimeBinding.provider 联动)。"""
        return self

    @property
    def last_composed(self) -> Optional[ComposedPersonalityContext]:
        return self._last_composed

    # --------------------------------------------------------
    # 设置 / 注入
    # --------------------------------------------------------
    def set_composer(
        self, composer: PersonalityContextComposer,
    ) -> None:
        self._composer = composer

    def set_rules_builder(
        self, rules_builder: ConsistencyRulesBuilder,
    ) -> None:
        self._rules_builder = rules_builder

    def set_formatter(
        self, formatter: PersonalityPromptFormatter,
    ) -> None:
        self._formatter = formatter

    def set_composed(
        self,
        composed: Optional[ComposedPersonalityContext],
    ) -> None:
        """Runtime 在 SELF_MODEL_BUILD 阶段之后注入 ComposedPersonalityContext。"""
        self._last_composed = composed

    # --------------------------------------------------------
    # Provider 主接口 —— provide
    # --------------------------------------------------------
    def provide(
        self,
        ctx_payload: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Provider 接口 —— 把 ComposedPersonalityContext(或 dict)转为 dict。

        - ctx_payload: ComposedPersonalityContext / dict / None
        - 任何异常返回 {}
        """
        try:
            composed = self._coerce_to_composed(ctx_payload)
            self._last_composed = composed
            if composed is None:
                self._provide_count += 1
                self._last_error = None
                return self._empty_payload()
            data = composed.to_dict()
            self._provide_count += 1
            self._last_error = None
            return data
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"provide_failed: {exc}"
            self._provide_count += 1
            logger.warning(
                "PersonalityBindingProvider.provide 失败: %s", exc
            )
            return self._empty_payload()

    def provide_from_runtime(
        self,
        identity_context: Optional[Any] = None,
        reflection_context: Optional[Any] = None,
        behavior_context: Optional[Any] = None,
        growth_context: Optional[Any] = None,
        behavior_signature: Optional[Any] = None,
        extra_constraints: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """从 Runtime 各阶段直接 provide(不依赖已存在的 Composed)。

        - 先用 ConsistencyRulesBuilder 生成规则
        - 再用 Composer 合并
        - 注入 cache
        """
        try:
            rules = self._rules_builder.build(
                behavior_signature=behavior_signature,
                identity_context=identity_context,
                reflection_context=reflection_context,
            )
            composed = self._composer.compose(
                identity_context=identity_context,
                reflection_context=reflection_context,
                behavior_context=behavior_context,
                growth_context=growth_context,
                behavior_signature=behavior_signature,
                consistency_constraints=rules
                + (list(extra_constraints) if extra_constraints else []),
            )
            self._last_composed = composed
            self._provide_count += 1
            self._last_error = None
            return composed.to_dict()
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"provide_from_runtime_failed: {exc}"
            self._provide_count += 1
            logger.warning(
                "PersonalityBindingProvider.provide_from_runtime 失败: %s",
                exc,
            )
            return self._empty_payload()

    # --------------------------------------------------------
    # 文本格式化
    # --------------------------------------------------------
    def format_for_prompt(
        self,
        ctx_payload: Optional[Any] = None,
    ) -> str:
        """从 ComposedPersonalityContext / dict 拼出 system_prompt 文本。"""
        try:
            composed = self._coerce_to_composed(ctx_payload)
            if composed is None:
                # 回退到最近一次 composed
                composed = self._last_composed
            if composed is None:
                return ""
            return self._formatter.format(composed)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"format_for_prompt_failed: {exc}"
            logger.warning(
                "PersonalityBindingProvider.format_for_prompt 失败: %s",
                exc,
            )
            return ""

    def format_context_for_prompt(
        self,
        ctx_dict: Optional[Dict[str, Any]] = None,
    ) -> str:
        """从已 provide 出来的 dict 拼出 system_prompt 文本。"""
        try:
            if not ctx_dict or not ctx_dict.get("has_binding"):
                # 回退到最近一次 composed
                if self._last_composed is not None:
                    return self._formatter.format(self._last_composed)
                return ""
            composed = ComposedPersonalityContext.from_dict(ctx_dict)
            return self._formatter.format(composed)
        except Exception as exc:  # noqa: BLE001
            self._last_error = (
                f"format_context_for_prompt_failed: {exc}"
            )
            logger.warning(
                "PersonalityBindingProvider.format_context_for_prompt 失败: %s",
                exc,
            )
            return ""

    # --------------------------------------------------------
    # 内部: coerce
    # --------------------------------------------------------
    def _coerce_to_composed(
        self, ctx_payload: Optional[Any],
    ) -> Optional[ComposedPersonalityContext]:
        if ctx_payload is None:
            return self._last_composed
        if isinstance(ctx_payload, ComposedPersonalityContext):
            return ctx_payload
        if isinstance(ctx_payload, dict):
            try:
                return ComposedPersonalityContext.from_dict(ctx_payload)
            except Exception:  # noqa: BLE001
                return self._last_composed
        # 其它对象,尝试 to_dict
        try:
            d = ctx_payload.to_dict()
            if isinstance(d, dict):
                return ComposedPersonalityContext.from_dict(d)
        except Exception:  # noqa: BLE001
            pass
        return self._last_composed

    @staticmethod
    def _empty_payload() -> Dict[str, Any]:
        return ComposedPersonalityContext().to_dict()

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def provide_count(self) -> int:
        return self._provide_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "provide_count": self._provide_count,
            "last_error": self._last_error,
            "composer": self._composer.describe() if self._composer else {},
            "rules_builder": (
                self._rules_builder.describe()
                if self._rules_builder else {}
            ),
            "formatter": (
                self._formatter.describe() if self._formatter else {}
            ),
            "has_last_composed": self._last_composed is not None,
        }

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "provide_count": self._provide_count,
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        return result


__all__ = [
    "PersonalityBindingProvider",
    "PERSONALITY_BINDING_PROVIDER_SCHEMA_VERSION",
]
