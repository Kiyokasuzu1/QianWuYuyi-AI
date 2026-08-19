# -*- coding: utf-8 -*-
"""
src/runtime/self_model/identity_binding/identity_context_builder.py

Phase 4.4: IdentityContextBuilder —— 运行时人格上下文聚合器

职责:
- 从 SelfModelSnapshot 聚合出"运行时身份上下文"(IdentityContext)
- 可选整合 ReflectionRecord / BehaviorSignature
- 输出结构化 Dict / 文本,供 ResponseEngine 注入 personality_context

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- Runtime → Service → Snapshot 单向依赖
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


IDENTITY_CONTEXT_SCHEMA_VERSION = "1.0"


# 文本截断长度
MAX_TEXT_VALUE = 60
MAX_TEXT_TRAIT = 80
MAX_TEXT_PREFERENCE = 80
MAX_TEXT_REFLECTION_OBS = 200
MAX_TEXT_REFLECTION_INTERP = 240
MAX_TEXT_IDENTITY_FIELD = 80
MAX_TEXT_SCENARIO_KEY = 32


# 视为"近期变化"的 entry kind(与 Phase 4.2.3 对齐)
RECENT_CHANGE_KINDS = frozenset({"trait_change", "growth", "reflection"})

# 文本中显示的 recent changes 条数上限
MAX_RECENT_CHANGES = 5

# 文本中显示的 reflection 数量上限
MAX_RECENT_REFLECTIONS = 3


def _safe_str(value: Any, max_len: int = MAX_TEXT_VALUE) -> str:
    """把任意值转 str 并截断(失败返回空串)。"""
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _coerce_dict(value: Any) -> Dict[str, Any]:
    """尽量把值当 dict 取出。"""
    if isinstance(value, dict):
        return dict(value)
    return {}


def _coerce_list(value: Any) -> List[Any]:
    """尽量把值当 list 取出。"""
    if isinstance(value, list):
        return list(value)
    return []


# ============================================================
# IdentityContext 数据类
# ============================================================
@dataclass
class IdentityContext:
    """运行时人格上下文 —— Phase 4.4 v1.0。

    字段:
    - identity_id:        str                  # 来自 SelfModelSnapshot.identity_id
    - version:            int                  # 来自 SelfModelSnapshot.version
    - schema_version:     str = "1.0"
    - timestamp:          str                  # 构建时间
    - identity:           Dict[str, Any]       # 身份基础信息
    - core_values:        List[Dict[str, Any]] # 核心价值观
    - stable_traits:      List[Dict[str, Any]] # 稳定特质
    - preferences:        List[Dict[str, Any]] # 偏好
    - current_state:      Dict[str, Any]       # 当前运行时人格快照
    - recent_changes:     List[Dict[str, Any]] # 最近变更(来自 entries)
    - reflection:         Optional[Dict]       # 来自 ReflectionRecord(若注入)
    - behavior_signature: Optional[Dict]       # 来自 BehaviorSignature(若注入)
    - has_snapshot:       bool                 # 是否有 SelfModelSnapshot 数据
    - meta:               Dict[str, Any]       # 元信息
    """

    identity: Dict[str, Any] = field(default_factory=dict)
    core_values: List[Dict[str, Any]] = field(default_factory=list)
    stable_traits: List[Dict[str, Any]] = field(default_factory=list)
    preferences: List[Dict[str, Any]] = field(default_factory=list)
    current_state: Dict[str, Any] = field(default_factory=dict)
    recent_changes: List[Dict[str, Any]] = field(default_factory=list)
    reflection: Optional[Dict[str, Any]] = None
    behavior_signature: Optional[Dict[str, Any]] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    identity_id: str = ""
    schema_version: str = IDENTITY_CONTEXT_SCHEMA_VERSION
    version: int = 0
    timestamp: str = ""
    has_snapshot: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IdentityContext":
        payload = dict(data or {})
        return cls(
            identity=_coerce_dict(payload.get("identity")),
            core_values=_coerce_list(payload.get("core_values")),
            stable_traits=_coerce_list(payload.get("stable_traits")),
            preferences=_coerce_list(payload.get("preferences")),
            current_state=_coerce_dict(payload.get("current_state")),
            recent_changes=_coerce_list(payload.get("recent_changes")),
            reflection=payload.get("reflection"),
            behavior_signature=payload.get("behavior_signature"),
            meta=_coerce_dict(payload.get("meta")),
            identity_id=str(payload.get("identity_id", "") or ""),
            schema_version=str(
                payload.get("schema_version")
                or IDENTITY_CONTEXT_SCHEMA_VERSION
            ),
            version=int(payload.get("version", 0) or 0),
            timestamp=str(payload.get("timestamp", "") or ""),
            has_snapshot=bool(payload.get("has_snapshot", False)),
        )

    def is_empty(self) -> bool:
        """是否没有任何实质内容。"""
        return (
            not self.identity
            and not self.core_values
            and not self.stable_traits
            and not self.preferences
            and not self.current_state
            and not self.recent_changes
            and not self.reflection
            and not self.behavior_signature
        )


# ============================================================
# IdentityContextBuilder
# ============================================================
class IdentityContextBuilder:
    """运行时人格上下文聚合器(Phase 4.4 / v1.0)。

    使用方式:
        builder = IdentityContextBuilder()
        ctx = builder.build(snapshot)               # 基础
        ctx = builder.build(                        # 完整
            snapshot,
            reflection=reflection_record,            # Phase 4.3 输出
            behavior_signature=behavior_signature,  # Phase 4.4 输出
        )
        text = builder.format_for_prompt(ctx)       # 文本形式
    """

    name: str = "identity_context_builder"
    schema_version: str = IDENTITY_CONTEXT_SCHEMA_VERSION

    def __init__(self) -> None:
        self._build_count: int = 0
        self._last_identity_id: Optional[str] = None
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def build(
        self,
        snapshot: Optional[Any] = None,
        reflection: Optional[Any] = None,
        behavior_signature: Optional[Any] = None,
    ) -> IdentityContext:
        """构建 IdentityContext。

        Args:
            snapshot:          SelfModelSnapshot 或 None
            reflection:        ReflectionRecord 或 None(Phase 4.3)
            behavior_signature: BehaviorSignature 或 None(本 phase)

        Returns:
            IdentityContext(空时 has_snapshot=False,其它字段为空)
        """
        if snapshot is None:
            self._build_count += 1
            self._last_identity_id = None
            self._last_error = None
            return self._empty_context()

        try:
            ctx = self._extract(
                snapshot, reflection, behavior_signature,
            )
            self._last_identity_id = getattr(snapshot, "identity_id", None)
            self._last_error = None
            self._build_count += 1
            return ctx
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"build_failed: {exc}"
            self._build_count += 1
            logger.warning("IdentityContextBuilder.build 失败: %s", exc)
            return self._empty_context()

    # --------------------------------------------------------
    # 内部:空 context
    # --------------------------------------------------------
    def _empty_context(self) -> IdentityContext:
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
            schema_version=IDENTITY_CONTEXT_SCHEMA_VERSION,
            version=0,
            timestamp="",
            has_snapshot=False,
        )

    # --------------------------------------------------------
    # 内部:抽取
    # --------------------------------------------------------
    def _extract(
        self,
        snapshot: Any,
        reflection: Optional[Any],
        behavior_signature: Optional[Any],
    ) -> IdentityContext:
        # identity
        identity = _coerce_dict(getattr(snapshot, "identity", None))
        # core_values / stable_traits / preferences
        core_values = _coerce_list(getattr(snapshot, "core_values", None))
        stable_traits = _coerce_list(getattr(snapshot, "stable_traits", None))
        preferences = _coerce_list(getattr(snapshot, "preferences", None))
        # current_state
        current_state = _coerce_dict(getattr(snapshot, "current_state", None))
        # recent_changes
        recent_changes = self._extract_recent_changes(snapshot)

        # reflection(可选) — 容忍 None / dict / 任意对象
        ref_dict = self._coerce_reflection(reflection)
        # behavior_signature(可选)
        bs_dict = self._coerce_behavior_signature(behavior_signature)

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
        counters_raw = getattr(snapshot, "counters", None)
        if isinstance(counters_raw, dict):
            meta["counters"] = dict(counters_raw)

        return IdentityContext(
            identity=identity,
            core_values=core_values,
            stable_traits=stable_traits,
            preferences=preferences,
            current_state=current_state,
            recent_changes=recent_changes,
            reflection=ref_dict,
            behavior_signature=bs_dict,
            meta=meta,
            identity_id=str(getattr(snapshot, "identity_id", "") or ""),
            schema_version=IDENTITY_CONTEXT_SCHEMA_VERSION,
            version=int(getattr(snapshot, "version", 0) or 0),
            timestamp=str(getattr(snapshot, "updated_at", "") or ""),
            has_snapshot=True,
        )

    @staticmethod
    def _extract_recent_changes(snapshot: Any) -> List[Dict[str, Any]]:
        """从 entries 中抽取 recent_changes。"""
        out: List[Dict[str, Any]] = []
        entries = getattr(snapshot, "entries", None)
        if not isinstance(entries, list):
            return out
        for entry in entries:
            if not entry:
                continue
            kind = getattr(entry, "kind", None) or (
                entry.get("kind") if isinstance(entry, dict) else None
            )
            if not kind or str(kind) not in RECENT_CHANGE_KINDS:
                continue
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
                    "summary": _safe_str(entry, MAX_TEXT_REFLECTION_OBS),
                }
            out.append(ed)
            if len(out) >= MAX_RECENT_CHANGES:
                break
        return out

    @staticmethod
    def _coerce_reflection(reflection: Optional[Any]) -> Optional[Dict[str, Any]]:
        """把 ReflectionRecord(或 dict)转为可序列化的 dict。"""
        if reflection is None:
            return None
        if isinstance(reflection, dict):
            return dict(reflection)
        # 尝试 to_dict()
        if hasattr(reflection, "to_dict") and callable(reflection.to_dict):
            try:
                d = reflection.to_dict()
                if isinstance(d, dict):
                    return d
            except Exception:  # noqa: BLE001
                pass
        # 退化:用属性拼装一个最小 dict
        try:
            return {
                "reflection_id": getattr(reflection, "reflection_id", ""),
                "identity_id": getattr(reflection, "identity_id", ""),
                "timestamp": getattr(reflection, "timestamp", ""),
                "trigger_category": getattr(
                    reflection, "trigger_category", "",
                ),
                "reflection_kind": getattr(
                    reflection, "reflection_kind", "",
                ),
                "priority": getattr(reflection, "priority", ""),
                "observation": getattr(reflection, "observation", ""),
                "interpretation": getattr(reflection, "interpretation", ""),
                "relation_to_values": list(
                    getattr(reflection, "relation_to_values", []) or []
                ),
                "confidence": getattr(reflection, "confidence", 0.0),
                "from_version": getattr(reflection, "from_version", 0),
                "to_version": getattr(reflection, "to_version", 0),
            }
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _coerce_behavior_signature(
        behavior_signature: Optional[Any],
    ) -> Optional[Dict[str, Any]]:
        """把 BehaviorSignature(或 dict)转为可序列化的 dict。"""
        if behavior_signature is None:
            return None
        if isinstance(behavior_signature, dict):
            return dict(behavior_signature)
        if hasattr(behavior_signature, "to_dict") and callable(
            behavior_signature.to_dict,
        ):
            try:
                d = behavior_signature.to_dict()
                if isinstance(d, dict):
                    return d
            except Exception:  # noqa: BLE001
                pass
        # 退化:用属性拼装
        try:
            scenarios = getattr(behavior_signature, "scenarios", {}) or {}
            if hasattr(scenarios, "items"):
                sc_dict = {}
                for k, v in scenarios.items():
                    if hasattr(v, "to_dict") and callable(v.to_dict):
                        try:
                            sc_dict[str(k)] = v.to_dict()
                            continue
                        except Exception:  # noqa: BLE001
                            pass
                    sc_dict[str(k)] = v
            else:
                sc_dict = dict(scenarios) if isinstance(scenarios, dict) else {}
            return {
                "signature_id": getattr(behavior_signature, "signature_id", ""),
                "identity_id": getattr(behavior_signature, "identity_id", ""),
                "scenarios": sc_dict,
                "default_scenario": getattr(
                    behavior_signature, "default_scenario", "",
                ),
                "schema_version": getattr(
                    behavior_signature, "schema_version", "",
                ),
            }
        except Exception:  # noqa: BLE001
            return None

    # --------------------------------------------------------
    # Provider 接口(与 ResponseAdapter 对齐)
    # --------------------------------------------------------
    def provide(self, identity_context: Optional[Any] = None) -> Dict[str, Any]:
        """Provider 接口 —— 把 IdentityContext(任意形式)转换为可序列化的 dict。

        - 如果传入的是 IdentityContext 实例,直接 to_dict()
        - 如果是 dict,返回其浅拷贝
        - 如果是 None,返回 {}
        - 其它类型:尽量用 to_dict() / __dict__ 兜底
        - 任何异常:返回 {}
        """
        if identity_context is None:
            return {}
        if isinstance(identity_context, IdentityContext):
            try:
                return identity_context.to_dict()
            except Exception:  # noqa: BLE001
                return {}
        if isinstance(identity_context, dict):
            return dict(identity_context)
        if hasattr(identity_context, "to_dict") and callable(
            identity_context.to_dict,
        ):
            try:
                d = identity_context.to_dict()
                if isinstance(d, dict):
                    return d
            except Exception:  # noqa: BLE001
                pass
        try:
            return dict(identity_context.__dict__)
        except Exception:  # noqa: BLE001
            return {}

    # --------------------------------------------------------
    # 文本格式化
    # --------------------------------------------------------
    def format_for_prompt(
        self, ctx: Optional[IdentityContext] = None,
    ) -> str:
        """把 IdentityContext 拼成一段系统提示文本。

        无内容时返回 ""。
        """
        if ctx is None or not ctx.has_snapshot:
            return ""
        return self._format_text(ctx)

    def format_context_for_prompt(
        self, ctx: Optional[Any] = None,
    ) -> str:
        """与 format_for_prompt 同义(接口对齐其它 ContextProvider)。

        - ctx 可以是 IdentityContext / dict / None
        - dict 时按"has_snapshot / identity_id"判定是否非空
        - 任何异常返回 ""
        """
        try:
            if ctx is None:
                return ""
            if isinstance(ctx, IdentityContext):
                return self.format_for_prompt(ctx)
            if isinstance(ctx, dict):
                if not ctx.get("has_snapshot", False):
                    return ""
                # 临时包成 IdentityContext 来复用格式化
                try:
                    obj = IdentityContext.from_dict(ctx)
                except Exception:  # noqa: BLE001
                    return ""
                return self.format_for_prompt(obj)
            # 其它类型,尝试按 dict 处理
            if hasattr(ctx, "to_dict") and callable(ctx.to_dict):
                try:
                    return self.format_context_for_prompt(ctx.to_dict())
                except Exception:  # noqa: BLE001
                    return ""
            return ""
        except Exception:  # noqa: BLE001
            return ""

    def _format_text(self, ctx: IdentityContext) -> str:
        lines: List[str] = ["【运行时身份】"]

        # identity
        identity = ctx.identity
        if identity:
            name = identity.get("name") or identity.get("identity_name")
            archetype = identity.get("archetype")
            if name:
                lines.append(f"- 名字: {name}")
            if archetype:
                lines.append(f"- 原型: {archetype}")
            for k, v in identity.items():
                if k in ("name", "identity_name", "archetype"):
                    continue
                if v is None or v == "":
                    continue
                lines.append(f"- {k}: {_safe_str(v, MAX_TEXT_IDENTITY_FIELD)}")

        # core_values
        if ctx.core_values:
            try:
                cv_lines: List[str] = []
                for cv in ctx.core_values[:5]:
                    if isinstance(cv, dict):
                        label = (
                            cv.get("name")
                            or cv.get("label")
                            or cv.get("value")
                        )
                        if label:
                            cv_lines.append(
                                _safe_str(label, MAX_TEXT_IDENTITY_FIELD),
                            )
                    else:
                        cv_lines.append(_safe_str(cv, MAX_TEXT_IDENTITY_FIELD))
                if cv_lines:
                    lines.append("- 核心价值: " + "; ".join(cv_lines))
            except Exception:  # noqa: BLE001
                pass

        # stable_traits
        if ctx.stable_traits:
            try:
                t_lines: List[str] = []
                for t in ctx.stable_traits[:5]:
                    if isinstance(t, dict):
                        name = t.get("name") or t.get("trait")
                        value = (
                            t.get("value")
                            or t.get("current_value")
                            or t.get("description")
                        )
                        if name and value is not None:
                            t_lines.append(
                                f"{name}={_safe_str(value, MAX_TEXT_TRAIT)}",
                            )
                        elif name:
                            t_lines.append(str(name))
                        elif value is not None:
                            t_lines.append(_safe_str(value, MAX_TEXT_TRAIT))
                    else:
                        t_lines.append(_safe_str(t, MAX_TEXT_TRAIT))
                if t_lines:
                    lines.append("- 稳定特质: " + "; ".join(t_lines))
            except Exception:  # noqa: BLE001
                pass

        # preferences
        if ctx.preferences:
            try:
                p_lines: List[str] = []
                for p in ctx.preferences[:5]:
                    if isinstance(p, dict):
                        name = (
                            p.get("name") or p.get("label") or p.get("key")
                        )
                        value = p.get("value")
                        if name and value is not None:
                            p_lines.append(
                                f"{name}={_safe_str(value, MAX_TEXT_PREFERENCE)}",
                            )
                        elif name:
                            p_lines.append(str(name))
                        elif value is not None:
                            p_lines.append(
                                _safe_str(value, MAX_TEXT_PREFERENCE),
                            )
                    else:
                        p_lines.append(_safe_str(p, MAX_TEXT_PREFERENCE))
                if p_lines:
                    lines.append("- 偏好: " + "; ".join(p_lines))
            except Exception:  # noqa: BLE001
                pass

        # current_state
        if ctx.current_state:
            try:
                cs_lines: List[str] = []
                for k, v in ctx.current_state.items():
                    if v is None or v == "":
                        continue
                    cs_lines.append(
                        f"{k}={_safe_str(v, MAX_TEXT_IDENTITY_FIELD)}",
                    )
                    if len(cs_lines) >= 5:
                        break
                if cs_lines:
                    lines.append("- 当前状态: " + "; ".join(cs_lines))
            except Exception:  # noqa: BLE001
                pass

        # recent_changes
        if ctx.recent_changes:
            try:
                rc_lines: List[str] = []
                for rc in ctx.recent_changes:
                    if isinstance(rc, dict):
                        kind = rc.get("kind")
                        summary = rc.get("summary")
                        if summary:
                            if kind:
                                rc_lines.append(
                                    f"[{kind}] {_safe_str(summary, MAX_TEXT_REFLECTION_OBS)}",
                                )
                            else:
                                rc_lines.append(
                                    _safe_str(summary, MAX_TEXT_REFLECTION_OBS),
                                )
                if rc_lines:
                    lines.append("- 最近变化: " + " | ".join(rc_lines))
            except Exception:  # noqa: BLE001
                pass

        # reflection
        if ctx.reflection and isinstance(ctx.reflection, dict):
            try:
                obs = ctx.reflection.get("observation")
                interp = ctx.reflection.get("interpretation")
                kind = ctx.reflection.get("reflection_kind") or ctx.reflection.get(
                    "kind",
                )
                priority = ctx.reflection.get("priority")
                related = ctx.reflection.get("relation_to_values") or []
                header = "【自我反思】"
                if kind:
                    header += f"({kind})"
                if priority:
                    header += f"[{priority}]"
                lines.append(header)
                if obs:
                    lines.append(
                        "- 观察: "
                        + _safe_str(obs, MAX_TEXT_REFLECTION_OBS),
                    )
                if interp:
                    lines.append(
                        "- 解读: "
                        + _safe_str(interp, MAX_TEXT_REFLECTION_INTERP),
                    )
                if related:
                    try:
                        rel_str = ", ".join(
                            _safe_str(x, 32) for x in list(related)[:5]
                        )
                        if rel_str:
                            lines.append(f"- 关联价值: {rel_str}")
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass

        # behavior_signature(只列 scenario key,不展开)
        if ctx.behavior_signature and isinstance(
            ctx.behavior_signature, dict,
        ):
            try:
                scenarios = ctx.behavior_signature.get("scenarios") or {}
                if isinstance(scenarios, dict) and scenarios:
                    keys = list(scenarios.keys())[:5]
                    if keys:
                        lines.append(
                            "- 行为模板: "
                            + ", ".join(
                                _safe_str(k, MAX_TEXT_SCENARIO_KEY)
                                for k in keys
                            ),
                        )
            except Exception:  # noqa: BLE001
                pass

        if len(lines) == 1:
            return ""
        return "\n".join(lines)

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def build_count(self) -> int:
        return self._build_count

    @property
    def last_identity_id(self) -> Optional[str]:
        return self._last_identity_id

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "build_count": self._build_count,
            "last_identity_id": self._last_identity_id,
            "last_error": self._last_error,
        }

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "build_count": self._build_count,
            "last_identity_id": self._last_identity_id,
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        return result


__all__ = [
    "IDENTITY_CONTEXT_SCHEMA_VERSION",
    "IdentityContext",
    "IdentityContextBuilder",
    "MAX_RECENT_CHANGES",
    "MAX_RECENT_REFLECTIONS",
    "RECENT_CHANGE_KINDS",
]
