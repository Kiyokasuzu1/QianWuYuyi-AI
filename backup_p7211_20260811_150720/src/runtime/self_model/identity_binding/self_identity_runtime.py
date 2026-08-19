# -*- coding: utf-8 -*-
"""
src/runtime/self_model/identity_binding/self_identity_runtime.py

Phase 4.4: SelfIdentityRuntime —— 运行时人格一致性协调器

职责:
- 集成 IdentityContextBuilder / BehaviorSignatureProvider /
  PersonalityConsistencyChecker
- 提供统一入口: build_context / get_behavior_signature / check_response
- 可选提供给 RuntimeCore / ResponseAdapter
- 不影响没有注入时的旧行为(向后兼容)

设计:
- 不调用 LLM,纯本地规则 / 启发式
- 不 import src.personality.*
- 不修改 ResponseEngine.generate() 签名
- 不修改 RuntimeContext schema
- Runtime → Service → Snapshot 单向依赖

约束:
- 不 import openai / qwen / llava / vision SDK
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


SELF_IDENTITY_RUNTIME_SCHEMA_VERSION = "1.0"

# 行为签名默认场景
DEFAULT_SCENARIO = "unknown"


def _safe_str(value: Any, max_len: int = 80) -> str:
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


# ============================================================
# SelfIdentityRuntime
# ============================================================
class SelfIdentityRuntime:
    """运行时人格一致性协调器(Phase 4.4 / v1.0)。

    使用方式:
        rt = SelfIdentityRuntime(
            identity_context_builder=IdentityContextBuilder(),
            behavior_signature_provider=BehaviorSignatureProvider(),
            consistency_checker=PersonalityConsistencyChecker(),
        )
        ctx = rt.build_context(snapshot, reflection=...)
        sig = rt.get_behavior_signature(snapshot)
        result = rt.check_response(
            candidate_text="...",
            identity_context=ctx,
            scenario="emotional_topic",
        )

    默认(全部 None):仍可调用,内部自动创建 default 实现。
    """

    name: str = "self_identity_runtime"
    schema_version: str = SELF_IDENTITY_RUNTIME_SCHEMA_VERSION

    def __init__(
        self,
        identity_context_builder: Optional[Any] = None,
        behavior_signature_provider: Optional[Any] = None,
        consistency_checker: Optional[Any] = None,
        default_scenario: str = DEFAULT_SCENARIO,
    ) -> None:
        # 懒加载:未注入时内部用默认实现
        self._builder: Any = identity_context_builder
        self._signature_provider: Any = behavior_signature_provider
        self._checker: Any = consistency_checker
        self._default_scenario: str = (
            default_scenario or DEFAULT_SCENARIO
        )

        # 状态
        self._build_count: int = 0
        self._check_count: int = 0
        self._last_identity_id: Optional[str] = None
        self._last_consistency: Optional[float] = None
        self._last_error: Optional[str] = None

        # 缓存(可选;由外部显式 invalidate 清空)
        self._cached_signature: Dict[str, Any] = {}  # identity_id -> signature dict

    # --------------------------------------------------------
    # 懒加载辅助
    # --------------------------------------------------------
    def _get_builder(self) -> Any:
        if self._builder is None:
            from src.runtime.self_model.identity_binding.identity_context_builder import (  # noqa: E501
                IdentityContextBuilder,
            )
            self._builder = IdentityContextBuilder()
        return self._builder

    def _get_signature_provider(self) -> Any:
        if self._signature_provider is None:
            from src.runtime.self_model.identity_binding.behavior_signature import (  # noqa: E501
                BehaviorSignatureProvider,
            )
            self._signature_provider = BehaviorSignatureProvider()
        return self._signature_provider

    def _get_checker(self) -> Any:
        if self._checker is None:
            from src.runtime.self_model.identity_binding.personality_consistency_checker import (  # noqa: E501
                PersonalityConsistencyChecker,
            )
            self._checker = PersonalityConsistencyChecker()
        return self._checker

    # --------------------------------------------------------
    # 主入口 1: build_context
    # --------------------------------------------------------
    def build_context(
        self,
        snapshot: Optional[Any] = None,
        reflection: Optional[Any] = None,
        behavior_signature: Optional[Any] = None,
        use_cached_signature: bool = True,
    ) -> Any:
        """构建 IdentityContext。

        Args:
            snapshot:              SelfModelSnapshot
            reflection:            ReflectionRecord(可选)
            behavior_signature:    BehaviorSignature(可选;None 时自动生成)
            use_cached_signature:  若 behavior_signature 为 None,是否用缓存

        Returns:
            IdentityContext(可能为空)
        """
        try:
            # 缺省 behavior_signature 时自动生成
            if behavior_signature is None and snapshot is not None:
                behavior_signature = self._resolve_signature(
                    snapshot, use_cache=use_cached_signature,
                )
            ctx = self._get_builder().build(
                snapshot=snapshot,
                reflection=reflection,
                behavior_signature=behavior_signature,
            )
            self._build_count += 1
            try:
                self._last_identity_id = getattr(ctx, "identity_id", None)
            except Exception:  # noqa: BLE001
                self._last_identity_id = None
            self._last_error = None
            return ctx
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"build_context_failed: {exc}"
            self._build_count += 1
            logger.warning("SelfIdentityRuntime.build_context 失败: %s", exc)
            # 返回空 IdentityContext,避免再次调用可能已异常的 builder
            return self._empty_context()

    @staticmethod
    def _empty_context() -> Any:
        """构造一个空的 IdentityContext(用于异常隔离)。"""
        from src.runtime.self_model.identity_binding.identity_context_builder import (
            IdentityContext,
        )
        return IdentityContext(
            identity={},
            core_values=[],
            stable_traits=[],
            preferences=[],
            current_state={},
            recent_changes=[],
            reflection=None,
            behavior_signature=None,
            meta={},
            identity_id="",
            schema_version="1.0",
            version=0,
            timestamp="",
            has_snapshot=False,
        )

    def _resolve_signature(
        self,
        snapshot: Any,
        use_cache: bool = True,
    ) -> Any:
        """从 snapshot 生成(或取缓存)BehaviorSignature。"""
        identity_id = str(getattr(snapshot, "identity_id", "") or "")
        if use_cache and identity_id and identity_id in self._cached_signature:
            cached = self._cached_signature[identity_id]
            # 反序列化(可能存的是 dict)
            try:
                from src.runtime.self_model.identity_binding.behavior_signature import (  # noqa: E501
                    BehaviorSignature,
                )
                if isinstance(cached, dict):
                    return BehaviorSignature.from_dict(cached)
                if isinstance(cached, BehaviorSignature):
                    return cached
            except Exception:  # noqa: BLE001
                pass
        sig = self._get_signature_provider().build(snapshot)
        # 写入缓存
        try:
            self._cached_signature[identity_id] = sig.to_dict()
        except Exception:  # noqa: BLE001
            pass
        return sig

    # --------------------------------------------------------
    # 主入口 2: get_behavior_signature
    # --------------------------------------------------------
    def get_behavior_signature(
        self,
        snapshot: Optional[Any] = None,
        use_cache: bool = True,
    ) -> Any:
        """获取(或生成)BehaviorSignature。"""
        if snapshot is None:
            return self._get_signature_provider().build(None)
        return self._resolve_signature(snapshot, use_cache=use_cache)

    # --------------------------------------------------------
    # 主入口 3: check_response
    # --------------------------------------------------------
    def check_response(
        self,
        candidate_text: Optional[str] = None,
        identity_context: Optional[Any] = None,
        behavior_signature: Optional[Any] = None,
        scenario: Optional[str] = None,
    ) -> Any:
        """执行一致性检查。

        Args:
            candidate_text:    候选回复
            identity_context:  IdentityContext 或 dict 或 None
            behavior_signature: BehaviorSignature 或 None(缺省时从 ctx 取)
            scenario:          行为场景
        """
        try:
            if behavior_signature is None and identity_context is not None:
                # 尝试从 identity_context.behavior_signature 取出
                bs = None
                if isinstance(identity_context, dict):
                    bs = identity_context.get("behavior_signature")
                else:
                    try:
                        bs = identity_context.behavior_signature
                    except Exception:  # noqa: BLE001
                        bs = None
                behavior_signature = bs
            result = self._get_checker().check(
                candidate_text=candidate_text,
                identity_context=identity_context,
                behavior_signature=behavior_signature,
                scenario=scenario or self._default_scenario,
            )
            self._check_count += 1
            try:
                self._last_consistency = float(
                    getattr(result, "consistency", 1.0),
                )
            except Exception:  # noqa: BLE001
                self._last_consistency = None
            self._last_error = None
            return result
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"check_response_failed: {exc}"
            self._check_count += 1
            logger.warning("SelfIdentityRuntime.check_response 失败: %s", exc)
            # 返回"一致"占位
            from src.runtime.self_model.identity_binding.personality_consistency_checker import (  # noqa: E501
                ConsistencyResult,
            )
            return ConsistencyResult(
                consistency=1.0,
                conflict_score=0.0,
                is_consistent=True,
                conflicts=[],
                scenario=str(scenario or self._default_scenario),
            )

    # --------------------------------------------------------
    # 主入口 4: process_for_runtime —— 一站式给 Runtime 用
    # --------------------------------------------------------
    def process_for_runtime(
        self,
        snapshot: Optional[Any] = None,
        reflection: Optional[Any] = None,
    ) -> Any:
        """Runtime 调用的一站式入口。

        返回 IdentityContext(包含 behavior_signature),可直接挂到 ctx。
        """
        return self.build_context(
            snapshot=snapshot,
            reflection=reflection,
            behavior_signature=None,
            use_cached_signature=True,
        )

    # --------------------------------------------------------
    # 缓存管理
    # --------------------------------------------------------
    def invalidate_signature_cache(
        self,
        identity_id: Optional[str] = None,
    ) -> int:
        """清空 behavior signature 缓存。

        Args:
            identity_id: 仅清空该 id;None 清空所有

        Returns:
            清空的条目数
        """
        if identity_id is None:
            n = len(self._cached_signature)
            self._cached_signature.clear()
            return n
        if identity_id in self._cached_signature:
            del self._cached_signature[identity_id]
            return 1
        return 0

    # --------------------------------------------------------
    # 子组件 getter
    # --------------------------------------------------------
    @property
    def identity_context_builder(self) -> Any:
        return self._builder or self._get_builder()

    @property
    def behavior_signature_provider(self) -> Any:
        return self._signature_provider or self._get_signature_provider()

    @property
    def consistency_checker(self) -> Any:
        return self._checker or self._get_checker()

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def build_count(self) -> int:
        return self._build_count

    @property
    def check_count(self) -> int:
        return self._check_count

    @property
    def last_identity_id(self) -> Optional[str]:
        return self._last_identity_id

    @property
    def last_consistency(self) -> Optional[float]:
        return self._last_consistency

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def default_scenario(self) -> str:
        return self._default_scenario

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "build_count": self._build_count,
            "check_count": self._check_count,
            "last_identity_id": self._last_identity_id,
            "last_consistency": self._last_consistency,
            "last_error": self._last_error,
            "default_scenario": self._default_scenario,
            "cached_signature_count": len(self._cached_signature),
        }

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "build_count": self._build_count,
            "check_count": self._check_count,
            "last_identity_id": self._last_identity_id,
            "last_consistency": self._last_consistency,
            "cached_signature_count": len(self._cached_signature),
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        return result


__all__ = [
    "SELF_IDENTITY_RUNTIME_SCHEMA_VERSION",
    "SelfIdentityRuntime",
    "DEFAULT_SCENARIO",
]
