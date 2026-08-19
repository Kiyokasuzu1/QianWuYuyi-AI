# -*- coding: utf-8 -*-
"""
src/runtime/self_model/self_model_context_provider.py

Phase 4.2.3: SelfModelContextProvider —— SelfModel → Response 上下文桥接

职责:
- 把 SelfModelSnapshot 转换为 Response Engine 所需的 Personality Context
- 提供结构化 Dict(供 personality_context 注入)
- 提供文本形式(供 PromptBuilder 拼装 system_prompt)
- 隔离 ResponseEngine.generate() 与 SelfModel:不修改 ResponseEngine

设计:
- 继承 AdapterBase(统一生命周期 attach / detach / health_check)
- 不调用 LLM,纯结构化转换
- 无 SelfModelSnapshot 时(未注入 registry / Foundation 失败 / snapshot 为 None)返回空 context
- 失败隔离:任何异常被静默吞掉,返回空 context

数据契约(provide() 返回):
- identity:         Dict[str, Any]   # 身份基础信息
- stable_traits:    List[Dict]        # 稳定特质
- preferences:      List[Dict]        # 偏好
- current_state:    Dict[str, Any]    # 当前运行时人格快照
- recent_changes:   List[Dict]        # 最近变更(来自 entries 中 kind in {trait_change, growth})
- core_values:      List[Dict]        # 核心价值观
- meta:             Dict[str, Any]    # meta / health / version
- schema_version:   str = "1.0"
- has_snapshot:     bool              # 标识是否有 SelfModel 数据

约束:
- 不 import openai / qwen / llava / vision SDK
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- 不修改 Personality 核心模块
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.adapters.base import AdapterBase


logger = logging.getLogger(__name__)


SELF_MODEL_CONTEXT_PROVIDER_SCHEMA_VERSION = "1.0"


# 视为"recent_change" 的 entry kind
RECENT_CHANGE_KINDS = frozenset({"trait_change", "growth", "reflection"})

# recent_changes 截断条数
MAX_RECENT_CHANGES = 5

# 文本格式化时的截断长度
MAX_TEXT_TRAIT_VALUE = 80
MAX_TEXT_PREFERENCE = 80
MAX_TEXT_CHANGE_SUMMARY = 120
MAX_IDENTITY_FIELD = 80


def _safe_str(value: Any, max_len: int = MAX_TEXT_TRAIT_VALUE) -> str:
    """把任意值转 str 并截断。"""
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _is_recent_change_kind(kind: str) -> bool:
    return kind in RECENT_CHANGE_KINDS


class SelfModelContextProvider(AdapterBase):
    """SelfModel → Response Context 桥接 Adapter(Phase 4.2.3 / v1.0)。

    继承:
    - AdapterBase(attach / detach / health_check)

    使用方式:
        provider = SelfModelContextProvider()
        provider.attach()
        context = provider.provide(snapshot)         # 注入到 personality_context
        text    = provider.format_for_prompt(snapshot) # 注入到 PromptBuilder

    无 snapshot 时:
        provider.provide(None) == {
            "schema_version": "1.0",
            "has_snapshot": False,
        }
        provider.format_for_prompt(None) == ""

    字段:
    - name:              str  # Adapter 名
    - schema_version:    str  # 冻结版本 "1.0"
    - _provide_count:    int  # provide 调用次数
    - _last_snapshot_id: Optional[str]  # 最近一次 provide 的 snapshot identity_id
    - _last_error:       Optional[str]  # 最近一次 provide 的错误
    """

    name: str = "self_model_context_provider"
    schema_version: str = SELF_MODEL_CONTEXT_PROVIDER_SCHEMA_VERSION

    def __init__(self) -> None:
        super().__init__()
        self._provide_count: int = 0
        self._last_snapshot_id: Optional[str] = None
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 Runtime(本 Provider 无外部依赖,仅标记状态)。"""
        self._mark_attached()
        self._cache_health({
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "provide_count": self._provide_count,
        })

    def detach(self) -> None:
        """解除接入。"""
        self._mark_detached()
        self._last_snapshot_id = None
        self._last_error = None

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": self.is_attached,
            "name": self.name,
            "schema_version": self.schema_version,
            "provide_count": self._provide_count,
            "last_snapshot_id": self._last_snapshot_id,
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # 核心:provide —— SelfModelSnapshot → Context Dict
    # --------------------------------------------------------
    def provide(
        self, snapshot: Optional[Any] = None
    ) -> Dict[str, Any]:
        """从 SelfModelSnapshot 提取 Response Engine 所需字段。

        无 snapshot(None) 时,返回最小空 context(has_snapshot=False)。

        Args:
            snapshot: SelfModelSnapshot 实例,或 None

        Returns:
            结构化 context Dict
        """
        if snapshot is None:
            self._provide_count += 1
            self._last_snapshot_id = None
            self._last_error = None
            return self._empty_context()

        try:
            ctx = self._extract(snapshot)
            self._last_snapshot_id = getattr(snapshot, "identity_id", None)
            self._last_error = None
            self._provide_count += 1
            return ctx
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"provide_failed: {exc}"
            self._provide_count += 1
            logger.warning(
                "SelfModelContextProvider.provide 失败: %s", exc
            )
            return self._empty_context()

    def _empty_context(self) -> Dict[str, Any]:
        """返回空 context(无 snapshot 时)。"""
        return {
            "schema_version": SELF_MODEL_CONTEXT_PROVIDER_SCHEMA_VERSION,
            "has_snapshot": False,
            "identity": {},
            "stable_traits": [],
            "preferences": [],
            "current_state": {},
            "recent_changes": [],
            "core_values": [],
            "meta": {},
        }

    def _extract(self, snapshot: Any) -> Dict[str, Any]:
        """实际提取逻辑(snapshot 非 None 时调用)。"""
        # identity
        identity_raw = getattr(snapshot, "identity", None)
        identity = dict(identity_raw) if isinstance(identity_raw, dict) else {}

        # stable_traits
        traits_raw = getattr(snapshot, "stable_traits", None)
        stable_traits = list(traits_raw) if isinstance(traits_raw, list) else []

        # preferences
        prefs_raw = getattr(snapshot, "preferences", None)
        preferences = list(prefs_raw) if isinstance(prefs_raw, list) else []

        # current_state
        cs_raw = getattr(snapshot, "current_state", None)
        current_state = dict(cs_raw) if isinstance(cs_raw, dict) else {}

        # core_values
        cv_raw = getattr(snapshot, "core_values", None)
        core_values = list(cv_raw) if isinstance(cv_raw, list) else []

        # recent_changes(从 entries 过滤)
        entries_raw = getattr(snapshot, "entries", None)
        recent_changes: List[Dict[str, Any]] = []
        if isinstance(entries_raw, list):
            for entry in entries_raw:
                if not entry:
                    continue
                kind = getattr(entry, "kind", None) or (
                    entry.get("kind") if isinstance(entry, dict) else None
                )
                if not _is_recent_change_kind(str(kind)):
                    continue
                # 转 dict
                if hasattr(entry, "to_dict") and callable(entry.to_dict):
                    try:
                        ed = entry.to_dict()
                    except Exception:  # noqa: BLE001
                        ed = {
                            "kind": kind,
                            "summary": getattr(entry, "summary", ""),
                        }
                elif isinstance(entry, dict):
                    ed = dict(entry)
                else:
                    ed = {
                        "kind": str(kind),
                        "summary": _safe_str(entry, MAX_TEXT_CHANGE_SUMMARY),
                    }
                recent_changes.append(ed)
                if len(recent_changes) >= MAX_RECENT_CHANGES:
                    break

        # meta
        meta: Dict[str, Any] = {
            "identity_id": getattr(snapshot, "identity_id", None),
            "version": getattr(snapshot, "version", None),
            "schema_version": getattr(snapshot, "schema_version", None),
            "updated_at": getattr(snapshot, "updated_at", None),
        }
        health_raw = getattr(snapshot, "health", None)
        if isinstance(health_raw, dict):
            meta["health"] = dict(health_raw)
        # counters
        counters_raw = getattr(snapshot, "counters", None)
        if isinstance(counters_raw, dict):
            meta["counters"] = dict(counters_raw)

        return {
            "schema_version": SELF_MODEL_CONTEXT_PROVIDER_SCHEMA_VERSION,
            "has_snapshot": True,
            "identity": identity,
            "stable_traits": stable_traits,
            "preferences": preferences,
            "current_state": current_state,
            "recent_changes": recent_changes,
            "core_values": core_values,
            "meta": meta,
        }

    # --------------------------------------------------------
    # 核心:format_for_prompt —— Dict → 文本
    # --------------------------------------------------------
    def format_for_prompt(
        self, snapshot: Optional[Any] = None
    ) -> str:
        """从 SelfModelSnapshot 拼出 PromptBuilder 友好的文本片段。

        无 snapshot 时返回 ""(PromptBuilder 会跳过)。
        """
        ctx = self.provide(snapshot)
        if not ctx.get("has_snapshot"):
            return ""
        return self._format_context_text(ctx)

    def format_context_for_prompt(
        self, context: Optional[Dict[str, Any]] = None
    ) -> str:
        """从已经 provide 出来的 context Dict 拼出文本片段(避免重复 provide)。"""
        if not context or not context.get("has_snapshot"):
            return ""
        return self._format_context_text(context)

    def _format_context_text(self, ctx: Dict[str, Any]) -> str:
        """把 context Dict 拼成一段系统提示文本。"""
        lines: List[str] = ["【自我认知】"]

        # identity
        identity = ctx.get("identity") or {}
        if isinstance(identity, dict) and identity:
            name = identity.get("name") or identity.get("identity_name")
            archetype = identity.get("archetype")
            if name:
                lines.append(f"- 身份: {name}")
            if archetype:
                lines.append(f"- 原型: {archetype}")
            # 其它字段(限制)
            for k, v in identity.items():
                if k in ("name", "identity_name", "archetype"):
                    continue
                if v is None or v == "":
                    continue
                lines.append(
                    f"- {k}: {_safe_str(v, MAX_IDENTITY_FIELD)}"
                )

        # core_values
        core_values = ctx.get("core_values") or []
        if core_values:
            try:
                cv_lines = []
                for cv in core_values[:5]:
                    if isinstance(cv, dict):
                        label = cv.get("name") or cv.get("label") or cv.get("value")
                        if label:
                            cv_lines.append(_safe_str(label, MAX_IDENTITY_FIELD))
                    else:
                        cv_lines.append(_safe_str(cv, MAX_IDENTITY_FIELD))
                if cv_lines:
                    lines.append("- 核心价值观: " + "; ".join(cv_lines))
            except Exception:  # noqa: BLE001
                pass

        # stable_traits
        stable_traits = ctx.get("stable_traits") or []
        if stable_traits:
            try:
                t_lines = []
                for t in stable_traits[:5]:
                    if isinstance(t, dict):
                        name = t.get("name") or t.get("trait")
                        value = t.get("value") or t.get("current_value") or t.get("description")
                        if name and value is not None:
                            t_lines.append(
                                f"{name}={_safe_str(value, MAX_TEXT_TRAIT_VALUE)}"
                            )
                        elif name:
                            t_lines.append(str(name))
                        elif value is not None:
                            t_lines.append(_safe_str(value, MAX_TEXT_TRAIT_VALUE))
                    else:
                        t_lines.append(_safe_str(t, MAX_TEXT_TRAIT_VALUE))
                if t_lines:
                    lines.append("- 稳定特质: " + "; ".join(t_lines))
            except Exception:  # noqa: BLE001
                pass

        # preferences
        preferences = ctx.get("preferences") or []
        if preferences:
            try:
                p_lines = []
                for p in preferences[:5]:
                    if isinstance(p, dict):
                        name = p.get("name") or p.get("label") or p.get("key")
                        value = p.get("value")
                        if name and value is not None:
                            p_lines.append(
                                f"{name}={_safe_str(value, MAX_TEXT_PREFERENCE)}"
                            )
                        elif name:
                            p_lines.append(str(name))
                        elif value is not None:
                            p_lines.append(_safe_str(value, MAX_TEXT_PREFERENCE))
                    else:
                        p_lines.append(_safe_str(p, MAX_TEXT_PREFERENCE))
                if p_lines:
                    lines.append("- 偏好: " + "; ".join(p_lines))
            except Exception:  # noqa: BLE001
                pass

        # current_state
        current_state = ctx.get("current_state") or {}
        if isinstance(current_state, dict) and current_state:
            try:
                cs_lines = []
                for k, v in current_state.items():
                    if v is None or v == "":
                        continue
                    cs_lines.append(
                        f"{k}={_safe_str(v, MAX_IDENTITY_FIELD)}"
                    )
                    if len(cs_lines) >= 5:
                        break
                if cs_lines:
                    lines.append("- 当前状态: " + "; ".join(cs_lines))
            except Exception:  # noqa: BLE001
                pass

        # recent_changes
        recent_changes = ctx.get("recent_changes") or []
        if recent_changes:
            try:
                rc_lines = []
                for rc in recent_changes:
                    if isinstance(rc, dict):
                        kind = rc.get("kind")
                        summary = rc.get("summary")
                        if summary:
                            if kind:
                                rc_lines.append(
                                    f"[{kind}] {_safe_str(summary, MAX_TEXT_CHANGE_SUMMARY)}"
                                )
                            else:
                                rc_lines.append(
                                    _safe_str(summary, MAX_TEXT_CHANGE_SUMMARY)
                                )
                if rc_lines:
                    lines.append("- 最近变化: " + " | ".join(rc_lines))
            except Exception:  # noqa: BLE001
                pass

        if len(lines) == 1:
            # 没拼出任何内容
            return ""
        return "\n".join(lines)

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def provide_count(self) -> int:
        return self._provide_count

    @property
    def last_snapshot_id(self) -> Optional[str]:
        return self._last_snapshot_id

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "is_attached": self.is_attached,
            "provide_count": self._provide_count,
            "last_snapshot_id": self._last_snapshot_id,
            "last_error": self._last_error,
        }


__all__ = [
    "SELF_MODEL_CONTEXT_PROVIDER_SCHEMA_VERSION",
    "SelfModelContextProvider",
    "MAX_RECENT_CHANGES",
    "RECENT_CHANGE_KINDS",
]
