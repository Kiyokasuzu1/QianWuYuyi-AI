# -*- coding: utf-8 -*-
"""
src/runtime/self_model/reflection/reflection_context_provider.py

Phase 4.3: SelfReflectionContextProvider —— 自我反思 → Response 上下文桥接

职责:
- 把 ReflectionRecord 列表转换为 Response Engine 所需的 Personality Context
- 提供结构化 Dict(供 personality_context 注入)
- 提供文本形式(供 PromptBuilder 拼装 system_prompt)
- 只取最近 N 条高优先级 reflection,不污染 prompt
- 隔离 ResponseEngine.generate() 与 Self Reflection
- 不修改 ResponseEngine

设计:
- 继承 AdapterBase(统一生命周期 attach / detach / health_check)
- 不调用 LLM,纯结构化转换
- 无 reflection 时返回空 context
- 失败隔离:任何异常被静默吞掉,返回空 context
- 只取最近 max_items 条,默认 3
- 支持按 priority 过滤(默认全部),可选 minimum_priority

数据契约(provide() 返回):
- schema_version:   str = "1.0"
- has_reflection:   bool
- identity_id:      Optional[str]
- recent:           List[Dict]     # 最近 N 条结构化
- meta:             Dict[str, Any]

文本格式:
【自我反思】
最近变化:
- <kind>: <observation>
原因:
- <interpretation>
关联价值:
- v1, v2
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.adapters.base import AdapterBase


logger = logging.getLogger(__name__)


SELF_REFLECTION_CONTEXT_PROVIDER_SCHEMA_VERSION = "1.0"

# 最近 N 条 reflection 默认值
DEFAULT_MAX_RECENT_REFLECTIONS = 3

# 文本截断
MAX_TEXT_OBSERVATION = 160
MAX_TEXT_INTERPRETATION = 200
MAX_TEXT_VALUE = 60


def _safe_str(value: Any, max_len: int = 120) -> str:
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _priority_rank(p: Optional[str]) -> int:
    """priority 排序权重,越大越重要。"""
    if not p:
        return 0
    s = str(p).lower()
    if s == "high":
        return 3
    if s == "medium":
        return 2
    if s == "low":
        return 1
    return 0


def _record_to_dict(record: Any) -> Dict[str, Any]:
    """把 ReflectionRecord 序列化为 dict。"""
    if record is None:
        return {}
    if isinstance(record, dict):
        return dict(record)
    # dataclass-like
    out: Dict[str, Any] = {}
    for key in (
        "reflection_id", "identity_id", "timestamp",
        "source_audit_id", "trigger_category", "reflection_kind",
        "priority", "observation", "interpretation",
        "relation_to_values", "confidence",
        "from_version", "to_version",
    ):
        try:
            v = getattr(record, key, None)
        except Exception:  # noqa: BLE001
            v = None
        out[key] = v
    return out


class SelfReflectionContextProvider(AdapterBase):
    """SelfReflection → Response Context 桥接 Adapter(Phase 4.3 / v1.0)。

    使用方式:
        provider = SelfReflectionContextProvider()
        provider.attach()
        ctx = provider.provide(reflection_records, identity_id="smf_a")
        text = provider.format_for_prompt(reflection_records, identity_id="smf_a")

    无 reflection 时:
        provider.provide([], identity_id=...) == {
            "schema_version": "1.0",
            "has_reflection": False,
        }
    """

    name: str = "self_reflection_context_provider"
    schema_version: str = SELF_REFLECTION_CONTEXT_PROVIDER_SCHEMA_VERSION

    def __init__(
        self,
        max_recent: int = DEFAULT_MAX_RECENT_REFLECTIONS,
    ) -> None:
        super().__init__()
        if max_recent <= 0:
            max_recent = DEFAULT_MAX_RECENT_REFLECTIONS
        self._max_recent: int = max_recent
        self._provide_count: int = 0
        self._last_identity_id: Optional[str] = None
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        self._mark_attached()
        self._cache_health({
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "max_recent": self._max_recent,
        })

    def detach(self) -> None:
        self._last_identity_id = None
        self._last_error = None
        self._mark_detached()

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": self.is_attached,
            "name": self.name,
            "schema_version": self.schema_version,
            "max_recent": self._max_recent,
            "provide_count": self._provide_count,
            "last_identity_id": self._last_identity_id,
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # 核心:provide
    # --------------------------------------------------------
    def provide(
        self,
        reflection_records: Optional[Sequence[Any]] = None,
        identity_id: Optional[str] = None,
        minimum_priority: Optional[str] = None,
    ) -> Dict[str, Any]:
        """从 reflection records 列表提取 Response Engine 所需字段。

        Args:
            reflection_records: ReflectionRecord 列表(可空)
            identity_id: 可选,只取该 identity 的 records
            minimum_priority: 最低 priority 阈值(high/medium/low)

        Returns:
            结构化 context Dict
        """
        if not reflection_records:
            self._provide_count += 1
            self._last_identity_id = identity_id
            self._last_error = None
            return self._empty_context(identity_id)

        try:
            # 过滤 identity
            if identity_id:
                pool = [r for r in reflection_records
                        if _record_identity(r) == identity_id]
            else:
                pool = list(reflection_records)
            # 过滤 priority
            if minimum_priority:
                threshold = _priority_rank(minimum_priority)
                pool = [r for r in pool
                        if _priority_rank(_record_priority(r)) >= threshold]
            # 排序: priority desc, timestamp desc
            pool.sort(
                key=lambda r: (
                    _priority_rank(_record_priority(r)),
                    _record_timestamp(r) or "",
                ),
                reverse=True,
            )
            recent = pool[: self._max_recent]
            recent_dicts = [_record_to_dict(r) for r in recent]
            self._last_identity_id = identity_id
            self._last_error = None
            self._provide_count += 1
            return {
                "schema_version": SELF_REFLECTION_CONTEXT_PROVIDER_SCHEMA_VERSION,
                "has_reflection": bool(recent_dicts),
                "identity_id": identity_id,
                "recent": recent_dicts,
                "meta": {
                    "total_pool": len(pool),
                    "max_recent": self._max_recent,
                    "minimum_priority": minimum_priority,
                },
            }
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"provide_failed: {exc}"
            self._provide_count += 1
            logger.warning(
                "SelfReflectionContextProvider.provide 失败: %s", exc
            )
            return self._empty_context(identity_id)

    def _empty_context(self, identity_id: Optional[str]) -> Dict[str, Any]:
        return {
            "schema_version": SELF_REFLECTION_CONTEXT_PROVIDER_SCHEMA_VERSION,
            "has_reflection": False,
            "identity_id": identity_id,
            "recent": [],
            "meta": {},
        }

    # --------------------------------------------------------
    # 文本格式化
    # --------------------------------------------------------
    def format_for_prompt(
        self,
        reflection_records: Optional[Sequence[Any]] = None,
        identity_id: Optional[str] = None,
        minimum_priority: Optional[str] = None,
    ) -> str:
        """从 reflection 列表拼出 PromptBuilder 友好的文本片段。

        无 reflection 时返回 ""(PromptBuilder 会跳过)。
        """
        ctx = self.provide(
            reflection_records,
            identity_id=identity_id,
            minimum_priority=minimum_priority,
        )
        if not ctx.get("has_reflection"):
            return ""
        return self._format_context_text(ctx)

    def format_context_for_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """从已 provide 的 context Dict 拼出文本片段。"""
        if not context or not context.get("has_reflection"):
            return ""
        return self._format_context_text(context)

    def _format_context_text(self, ctx: Dict[str, Any]) -> str:
        lines: List[str] = ["【自我反思】"]
        recent = ctx.get("recent") or []
        if not recent:
            return ""
        # 按时间升序展示(老的在前,新的在后)
        ordered = list(recent)
        try:
            ordered.sort(key=lambda r: r.get("timestamp") or "")
        except Exception:  # noqa: BLE001
            pass
        # 最近变化
        lines.append("最近变化:")
        for r in ordered:
            kind = r.get("reflection_kind") or "silent"
            obs = _safe_str(r.get("observation", ""), MAX_TEXT_OBSERVATION)
            if not obs:
                continue
            lines.append(f"- [{kind}] {obs}")
        # 原因(interpretation)
        lines.append("原因:")
        for r in ordered:
            interp = _safe_str(r.get("interpretation", ""), MAX_TEXT_INTERPRETATION)
            if not interp:
                continue
            lines.append(f"- {interp}")
        # 关联价值
        all_values: List[str] = []
        for r in ordered:
            for v in (r.get("relation_to_values") or []):
                vs = _safe_str(v, MAX_TEXT_VALUE)
                if vs and vs not in all_values:
                    all_values.append(vs)
        if all_values:
            lines.append("关联价值:")
            lines.append("- " + ", ".join(all_values))
        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def provide_count(self) -> int:
        return self._provide_count

    @property
    def last_identity_id(self) -> Optional[str]:
        return self._last_identity_id

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def max_recent(self) -> int:
        return self._max_recent

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "max_recent": self._max_recent,
            "is_attached": self.is_attached,
            "provide_count": self._provide_count,
            "last_identity_id": self._last_identity_id,
            "last_error": self._last_error,
        }


def _record_identity(record: Any) -> str:
    if record is None:
        return ""
    if isinstance(record, dict):
        return str(record.get("identity_id", "") or "")
    return str(getattr(record, "identity_id", "") or "")


def _record_priority(record: Any) -> str:
    if record is None:
        return ""
    if isinstance(record, dict):
        return str(record.get("priority", "") or "")
    return str(getattr(record, "priority", "") or "")


def _record_timestamp(record: Any) -> str:
    if record is None:
        return ""
    if isinstance(record, dict):
        return str(record.get("timestamp", "") or "")
    return str(getattr(record, "timestamp", "") or "")


__all__ = [
    "SelfReflectionContextProvider",
    "SELF_REFLECTION_CONTEXT_PROVIDER_SCHEMA_VERSION",
    "DEFAULT_MAX_RECENT_REFLECTIONS",
]
