# -*- coding: utf-8 -*-
"""
src/runtime/personality_binding/personality_runtime_binding.py

Phase 4.5: PersonalityRuntimeBinding —— 运行时人格绑定协调器

职责:
- 作为 Phase 4.5 的"主服务"被 RuntimeCore 注入。
- 集成 PersonalityContextComposer / ConsistencyRulesBuilder /
  PersonalityPromptFormatter / PersonalityBindingProvider。
- 接收 RuntimeCore 在 SELF_MODEL_BUILD 之后产生的
  SelfModelSnapshot / ReflectionRecord / IdentityContext / BehaviorSignature,
  生成统一 ComposedPersonalityContext。
- 把 ComposedPersonalityContext 写入 ctx._personality_runtime_context,
  供 ResponseAdapter.build_request() 在生成 LLM 请求前注入。
- 同时把 PersonalityBindingProvider 注入到 ResponseAdapter,
  让 Adapter 走 personality_runtime_context / personality_runtime_text 字段。
- 不修改 ResponseEngine.generate() 签名
- 不 import src.personality.*
- 不修改 RuntimeContext schema

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- Runtime → Service → Snapshot 单向依赖
- 失败隔离:任何异常被静默吞掉,返回空 ComposedPersonalityContext
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.runtime.personality_binding.composed_personality_context import (
    ComposedPersonalityContext,
    PERSONA_BINDING_CONTEXT_SCHEMA_VERSION,
)
from src.runtime.personality_binding.consistency_rules_builder import (
    ConsistencyRulesBuilder,
)
from src.runtime.personality_binding.personality_binding_provider import (
    PersonalityBindingProvider,
)
from src.runtime.personality_binding.personality_context_composer import (
    PersonalityContextComposer,
)
from src.runtime.personality_binding.personality_prompt_formatter import (
    PersonalityPromptFormatter,
)


logger = logging.getLogger(__name__)


PERSONA_RUNTIME_BINDING_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.utcnow().isoformat() + "Z"


# PersonalityRuntimeBinding 在 ctx 上的字段名(供 ResponseAdapter 读取)
PERSONALITY_RUNTIME_CONTEXT_ATTR = "_personality_runtime_context"


class PersonalityRuntimeBinding:
    """运行时人格绑定协调器(Phase 4.5 / v1.0)。

    使用方式:
        binding = PersonalityRuntimeBinding()
        core.configure_personality_runtime_binding(binding)

        # Runtime 在 SELF_MODEL_BUILD 之后:
        composed = binding.build_for_runtime(
            snapshot=...,         # Phase 4.2
            reflection=...,       # Phase 4.3
            identity_context=..., # Phase 4.4
            behavior_signature=..., # Phase 4.4
            growth_context=...,   # Phase 4.2 SelfModelContextProvider 输出
        )
        binding.apply_to_context(ctx, composed)

        # ResponseAdapter 在 build_request 时:
        #   读取 ctx._personality_runtime_context
        #   注入 personality_context["personality_runtime_data"]
        #   注入 personality_context["personality_runtime_text"]
    """

    name: str = "personality_runtime_binding"
    schema_version: str = PERSONA_RUNTIME_BINDING_SCHEMA_VERSION

    def __init__(
        self,
        composer: Optional[PersonalityContextComposer] = None,
        rules_builder: Optional[ConsistencyRulesBuilder] = None,
        formatter: Optional[PersonalityPromptFormatter] = None,
        provider: Optional[PersonalityBindingProvider] = None,
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
        # provider 默认指向 self(供外部 set_composed / provide 调用)
        self._provider: PersonalityBindingProvider = (
            provider or PersonalityBindingProvider(
                composer=self._composer,
                rules_builder=self._rules_builder,
                formatter=self._formatter,
            )
        )
        # 缓存最近一次构建的 ComposedPersonalityContext
        self._last_composed: Optional[ComposedPersonalityContext] = None
        # 状态
        self._build_count: int = 0
        self._apply_count: int = 0
        self._last_identity_id: str = ""
        self._last_error: Optional[str] = None
        # 缓存(identity_id -> ComposedPersonalityContext)
        self._cache: Dict[str, ComposedPersonalityContext] = {}
        # 配置
        self._cache_enabled: bool = True

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
    def provider(self) -> PersonalityBindingProvider:
        return self._provider

    @property
    def last_composed(self) -> Optional[ComposedPersonalityContext]:
        return self._last_composed

    @property
    def cache_enabled(self) -> bool:
        return self._cache_enabled

    def set_cache_enabled(self, enabled: bool) -> None:
        self._cache_enabled = bool(enabled)

    def set_provider(
        self, provider: PersonalityBindingProvider,
    ) -> None:
        self._provider = provider

    def invalidate_cache(
        self, identity_id: Optional[str] = None,
    ) -> int:
        if identity_id is None:
            n = len(self._cache)
            self._cache.clear()
            return n
        if identity_id in self._cache:
            del self._cache[identity_id]
            return 1
        return 0

    # --------------------------------------------------------
    # 主入口 1: build_for_runtime —— 给 Runtime 阶段调用
    # --------------------------------------------------------
    def build_for_runtime(
        self,
        snapshot: Optional[Any] = None,
        reflection: Optional[Any] = None,
        identity_context: Optional[Any] = None,
        behavior_signature: Optional[Any] = None,
        growth_context: Optional[Any] = None,
        extra_constraints: Optional[Any] = None,
    ) -> ComposedPersonalityContext:
        """主入口:从各阶段输入构造 ComposedPersonalityContext。

        - snapshot:            SelfModelSnapshot(Phase 4.2)
        - reflection:          ReflectionRecord(Phase 4.3)
        - identity_context:    IdentityContext / dict(Phase 4.4)
        - behavior_signature:  BehaviorSignature / dict(Phase 4.4)
        - growth_context:      SelfModelContextProvider 输出 dict(Phase 4.2)
        - extra_constraints:   额外一致性规则(list / dict)

        返回:
        - ComposedPersonalityContext(总以非 None 形式返回;空时 has_binding=False)
        """
        try:
            # 1) 先用 rules_builder 生成一致性规则
            rules = self._rules_builder.build(
                behavior_signature=behavior_signature,
                identity_context=identity_context,
                reflection_context=(
                    self._coerce_reflection_to_ctx(reflection)
                    if reflection is not None
                    else None
                ),
                extra_rules=extra_constraints,
            )
            # 2) 把 reflection 转成 reflection_context dict
            reflection_context = self._coerce_reflection_to_ctx(reflection)
            # 3) 从 snapshot 提取 identity_id(Phase 4.2 来源)
            snapshot_identity_id = self._extract_identity_id(snapshot)
            # 4) 合并各来源
            composed = self._composer.compose(
                identity_context=identity_context,
                reflection_context=reflection_context,
                behavior_context=None,
                growth_context=growth_context,
                behavior_signature=behavior_signature,
                consistency_constraints=rules,
                identity_id=snapshot_identity_id or None,
            )
            composed.meta.setdefault("binding_name", self.name)
            composed.meta.setdefault(
                "binding_schema_version", self.schema_version,
            )
            composed.meta["built_at"] = _now_iso()

            # 4) 缓存
            self._last_composed = composed
            self._last_identity_id = composed.identity_id
            self._build_count += 1
            self._last_error = None
            if self._cache_enabled and composed.identity_id:
                self._cache[composed.identity_id] = composed
            # 5) 同步给 provider
            self._provider.set_composed(composed)
            return composed
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"build_for_runtime_failed: {exc}"
            self._build_count += 1
            logger.warning(
                "PersonalityRuntimeBinding.build_for_runtime 失败: %s",
                exc,
            )
            return self._empty_context()

    # --------------------------------------------------------
    # 主入口 2: apply_to_context —— 写入 ctx
    # --------------------------------------------------------
    def apply_to_context(
        self,
        ctx: Optional[Any] = None,
        composed: Optional[ComposedPersonalityContext] = None,
    ) -> None:
        """把 ComposedPersonalityContext 挂到 ctx._personality_runtime_context。

        - ctx 不存在时 no-op(失败隔离)
        - composed 为 None 时使用最近一次 build 的结果
        """
        try:
            if ctx is None:
                return
            if composed is None:
                composed = self._last_composed
            if composed is None:
                composed = self._empty_context()
            try:
                setattr(ctx, PERSONALITY_RUNTIME_CONTEXT_ATTR, composed)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PersonalityRuntimeBinding.apply_to_context "
                    "setattr 失败: %s", exc,
                )
                return
            self._apply_count += 1
            self._last_error = None
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"apply_to_context_failed: {exc}"
            logger.warning(
                "PersonalityRuntimeBinding.apply_to_context 失败: %s",
                exc,
            )

    # --------------------------------------------------------
    # 工具:从 ctx 读取(供 ResponseAdapter / 其它 Adapter)
    # --------------------------------------------------------
    @staticmethod
    def get_from_context(
        ctx: Optional[Any] = None,
    ) -> Optional[ComposedPersonalityContext]:
        """从 ctx._personality_runtime_context 读取 ComposedPersonalityContext。

        - 不存在 / 类型不对 → 返回 None
        """
        if ctx is None:
            return None
        try:
            obj = getattr(ctx, PERSONALITY_RUNTIME_CONTEXT_ATTR, None)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(obj, ComposedPersonalityContext):
            return obj
        return None

    @staticmethod
    def get_data_from_context(
        ctx: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """从 ctx._personality_runtime_context 读取并序列化为 dict。"""
        composed = PersonalityRuntimeBinding.get_from_context(ctx)
        if composed is None:
            return {}
        try:
            return composed.to_dict()
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def get_text_from_context(
        ctx: Optional[Any] = None,
        formatter: Optional[PersonalityPromptFormatter] = None,
    ) -> str:
        """从 ctx._personality_runtime_context 读取并格式化为 prompt 文本。"""
        composed = PersonalityRuntimeBinding.get_from_context(ctx)
        if composed is None:
            return ""
        f = formatter or PersonalityPromptFormatter()
        try:
            return f.format(composed)
        except Exception:  # noqa: BLE001
            return ""

    # --------------------------------------------------------
    # 内部:ReflectionRecord -> reflection_context dict
    # --------------------------------------------------------
    @staticmethod
    def _coerce_reflection_to_ctx(
        reflection: Optional[Any],
    ) -> Optional[Dict[str, Any]]:
        if reflection is None:
            return None
        if isinstance(reflection, dict):
            return dict(reflection)
        if isinstance(reflection, list):
            # 已经是 list(可能来自 store)
            return {
                "schema_version": "1.0",
                "has_reflection": bool(reflection),
                "recent": [
                    r.to_dict() if hasattr(r, "to_dict") else dict(r)
                    if isinstance(r, dict) else {"value": str(r)}
                    for r in reflection[:3]
                ],
            }
        # dataclass / object
        out: Dict[str, Any] = {}
        for key in (
            "reflection_id", "identity_id", "timestamp",
            "source_audit_id", "trigger_category", "reflection_kind",
            "priority", "observation", "interpretation",
            "relation_to_values", "confidence",
            "from_version", "to_version",
        ):
            try:
                v = getattr(reflection, key, None)
            except Exception:  # noqa: BLE001
                v = None
            if v is not None:
                out[key] = v
        if out:
            out["schema_version"] = "1.0"
            out["has_reflection"] = True
        return out or None

    # --------------------------------------------------------
    # 内部:从 snapshot 中提取 identity_id
    # --------------------------------------------------------
    @staticmethod
    def _extract_identity_id(snapshot: Optional[Any]) -> str:
        """从 SelfModelSnapshot / dict 中提取 identity_id 字符串。"""
        if snapshot is None:
            return ""
        if isinstance(snapshot, dict):
            return str(snapshot.get("identity_id", "") or "")
        try:
            return str(getattr(snapshot, "identity_id", "") or "")
        except Exception:  # noqa: BLE001
            return ""

    # --------------------------------------------------------
    # 内部:空 context
    # --------------------------------------------------------
    @staticmethod
    def _empty_context() -> ComposedPersonalityContext:
        return ComposedPersonalityContext(
            identity_context={},
            reflection_context={},
            behavior_context={},
            growth_context={},
            consistency_rules=[],
            meta={"binding_name": "personality_runtime_binding"},
            schema_version=PERSONA_BINDING_CONTEXT_SCHEMA_VERSION,
            has_binding=False,
            identity_id="",
            timestamp=_now_iso(),
            version=0,
            source="personality_runtime_binding",
        )

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def build_count(self) -> int:
        return self._build_count

    @property
    def apply_count(self) -> int:
        return self._apply_count

    @property
    def last_identity_id(self) -> str:
        return self._last_identity_id

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "build_count": self._build_count,
            "apply_count": self._apply_count,
            "last_identity_id": self._last_identity_id,
            "last_error": self._last_error,
            "cache_size": len(self._cache),
            "cache_enabled": self._cache_enabled,
            "composer": self._composer.describe() if self._composer else {},
            "rules_builder": (
                self._rules_builder.describe()
                if self._rules_builder else {}
            ),
            "formatter": (
                self._formatter.describe() if self._formatter else {}
            ),
        }

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "build_count": self._build_count,
            "apply_count": self._apply_count,
            "cache_size": len(self._cache),
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        return result


__all__ = [
    "PersonalityRuntimeBinding",
    "PERSONA_RUNTIME_BINDING_SCHEMA_VERSION",
    "PERSONALITY_RUNTIME_CONTEXT_ATTR",
]
