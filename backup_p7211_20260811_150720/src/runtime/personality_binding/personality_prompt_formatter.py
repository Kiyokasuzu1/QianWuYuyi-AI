# -*- coding: utf-8 -*-
"""
src/runtime/personality_binding/personality_prompt_formatter.py

Phase 4.5: PersonalityPromptFormatter —— Prompt 格式化层

职责:
- 把 ComposedPersonalityContext / 各分区 dict 拼接成
  【Identity】【Values】【Behavior】【Reflection】【Consistency Rules】
  等文本片段,供 system_prompt 注入。
- 既支持从 ComposedPersonalityContext 拼,也支持从各分区 dict 单独拼。
- 失败隔离:任何异常被静默吞掉,返回 ""。

输出样式(默认):
    【Identity】
    你是 <name>
    - 原型: <archetype>
    【Values】
    - <value1>; <value2>
    【Behavior】
    - <scenario>: <opening_style>
    - 语气: <tone1> ...
    【Reflection】
    - <observation> → <interpretation>
    【Consistency Rules】
    - <text>(severity=<s>)

约束:
- 不 import 业务实现
- 不 import openai / qwen / llava / vision SDK
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.personality_binding.composed_personality_context import (
    ComposedPersonalityContext,
)
from src.runtime.personality_binding.consistency_rules_builder import (
    ConsistencyRule,
)


logger = logging.getLogger(__name__)


PERSONALITY_PROMPT_FORMATTER_SCHEMA_VERSION = "1.0"


# 文本截断
MAX_IDENTITY_FIELD = 80
MAX_VALUE_TEXT = 80
MAX_BEHAVIOR_LINE = 120
MAX_REFLECTION_OBS = 200
MAX_REFLECTION_INTERP = 240
MAX_RULE_TEXT = 200
MAX_TRAIT_VALUE = 80
MAX_SCENARIO_KEY = 32

# 显示上限
MAX_IDENTITY_KEYS = 8
MAX_VALUES = 5
MAX_TRAITS = 5
MAX_BEHAVIOR_SCENARIOS = 5
MAX_REFLECTIONS = 3
MAX_RULES = 20
MAX_RULE_SEVERITY_DIGITS = 3  # 1.00 形式


def _safe_str(value: Any, max_len: int = 80) -> str:
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _coerce_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _coerce_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    return []


class PersonalityPromptFormatter:
    """Prompt 格式化层(Phase 4.5 / v1.0)。

    使用方式:
        formatter = PersonalityPromptFormatter()
        text = formatter.format(composed_ctx)         # 一次性
        text = formatter.format_identity(id_dict)
        text = formatter.format_values(id_dict)
        text = formatter.format_behavior(beh_dict)
        text = formatter.format_reflection(ref_dict)
        text = formatter.format_consistency_rules(rules)
    """

    name: str = "personality_prompt_formatter"
    schema_version: str = PERSONALITY_PROMPT_FORMATTER_SCHEMA_VERSION

    def __init__(
        self,
        include_identity: bool = True,
        include_values: bool = True,
        include_behavior: bool = True,
        include_reflection: bool = True,
        include_consistency: bool = True,
    ) -> None:
        self._include_identity = bool(include_identity)
        self._include_values = bool(include_values)
        self._include_behavior = bool(include_behavior)
        self._include_reflection = bool(include_reflection)
        self._include_consistency = bool(include_consistency)
        self._format_count: int = 0
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def format(
        self,
        composed: Optional[ComposedPersonalityContext] = None,
    ) -> str:
        """把 ComposedPersonalityContext 拼成一段 system_prompt 友好文本。

        任何分区缺失 / 异常 → 跳过该分区,不影响其它。
        整体失败 → 返回 ""。
        """
        if composed is None:
            return ""
        try:
            sections: List[str] = []
            if self._include_identity:
                s = self.format_identity(
                    composed.identity_context
                    if composed.has_identity()
                    else None
                )
                if s:
                    sections.append(s)
            if self._include_values:
                s = self.format_values(
                    composed.identity_context
                    if composed.has_identity()
                    else None
                )
                if s:
                    sections.append(s)
            if self._include_behavior:
                s = self.format_behavior(
                    composed.behavior_context
                    if composed.has_behavior()
                    else None
                )
                if s:
                    sections.append(s)
            if self._include_reflection:
                s = self.format_reflection(
                    composed.reflection_context
                    if composed.has_reflection()
                    else None
                )
                if s:
                    sections.append(s)
            if self._include_consistency:
                s = self.format_consistency_rules(
                    composed.consistency_rules
                    if composed.has_consistency_rules()
                    else None
                )
                if s:
                    sections.append(s)
            self._format_count += 1
            self._last_error = None
            if not sections:
                return ""
            return "\n\n".join(sections)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"format_failed: {exc}"
            self._format_count += 1
            logger.warning(
                "PersonalityPromptFormatter.format 失败: %s", exc
            )
            return ""

    # --------------------------------------------------------
    # Identity
    # --------------------------------------------------------
    def format_identity(
        self, identity_section: Optional[Dict[str, Any]] = None,
    ) -> str:
        """拼出【Identity】+ 【Values】的 Identity 部分。

        注意:此处仅输出"身份/基础信息"部分;Values 由 format_values 输出。
        """
        if not identity_section:
            return ""
        try:
            identity = _coerce_dict(identity_section.get("identity"))
            if not identity:
                # 兼容直接传入 identity dict 的情况
                if any(
                    k in identity_section
                    for k in ("name", "identity_name", "archetype")
                ):
                    identity = identity_section
                else:
                    return ""
            lines: List[str] = ["【Identity】"]
            name = (
                identity.get("name")
                or identity.get("identity_name")
            )
            archetype = identity.get("archetype")
            if name:
                lines.append(f"你是 {_safe_str(name, MAX_IDENTITY_FIELD)}")
            if archetype:
                lines.append(
                    f"- 原型: {_safe_str(archetype, MAX_IDENTITY_FIELD)}"
                )
            # stable_traits(简短)
            traits = _coerce_list(identity_section.get("stable_traits"))
            if traits:
                t_lines: List[str] = []
                for t in traits[:MAX_TRAITS]:
                    if isinstance(t, dict):
                        tn = t.get("name") or t.get("trait")
                        tv = (
                            t.get("value")
                            or t.get("current_value")
                            or t.get("description")
                        )
                        if tn and tv is not None:
                            t_lines.append(
                                f"{tn}={_safe_str(tv, MAX_TRAIT_VALUE)}"
                            )
                        elif tn:
                            t_lines.append(str(tn))
                    else:
                        t_lines.append(_safe_str(t, MAX_TRAIT_VALUE))
                if t_lines:
                    lines.append("- 稳定特质: " + "; ".join(t_lines))
            # 其它 identity 字段
            count = 0
            for k, v in identity.items():
                if k in ("name", "identity_name", "archetype"):
                    continue
                if v is None or v == "":
                    continue
                lines.append(
                    f"- {k}: {_safe_str(v, MAX_IDENTITY_FIELD)}"
                )
                count += 1
                if count >= MAX_IDENTITY_KEYS:
                    break
            if len(lines) == 1:
                return ""
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"format_identity_failed: {exc}"
            return ""

    # --------------------------------------------------------
    # Values
    # --------------------------------------------------------
    def format_values(
        self, identity_section: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not identity_section:
            return ""
        try:
            core_values = _coerce_list(
                identity_section.get("core_values")
            )
            if not core_values:
                return ""
            cv_lines: List[str] = []
            for cv in core_values[:MAX_VALUES]:
                if isinstance(cv, dict):
                    label = (
                        cv.get("name")
                        or cv.get("label")
                        or cv.get("value")
                    )
                    if label:
                        cv_lines.append(
                            _safe_str(label, MAX_VALUE_TEXT)
                        )
                else:
                    cv_lines.append(_safe_str(cv, MAX_VALUE_TEXT))
            if not cv_lines:
                return ""
            return "【Values】\n- 核心价值: " + "; ".join(cv_lines)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"format_values_failed: {exc}"
            return ""

    # --------------------------------------------------------
    # Behavior
    # --------------------------------------------------------
    def format_behavior(
        self, behavior_section: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not behavior_section:
            return ""
        try:
            lines: List[str] = ["【Behavior】"]
            # tone 摘要
            tone_summary = behavior_section.get("speaking_style_summary")
            if tone_summary:
                lines.append(
                    f"- 语气: {_safe_str(tone_summary, MAX_BEHAVIOR_LINE)}"
                )
            # scenarios: 取 opening_style / tone
            scenarios = _coerce_dict(behavior_section.get("scenarios"))
            if scenarios:
                shown = 0
                for sc, pat in scenarios.items():
                    if shown >= MAX_BEHAVIOR_SCENARIOS:
                        break
                    if isinstance(pat, dict):
                        tone = pat.get("tone", "")
                        opening = pat.get("opening_style", "")
                        if opening or tone:
                            extra = (
                                f"({tone})" if tone else ""
                            )
                            lines.append(
                                f"- {_safe_str(sc, MAX_SCENARIO_KEY)}: "
                                f"{_safe_str(opening, MAX_BEHAVIOR_LINE)}"
                                f"{extra}"
                            )
                            shown += 1
            # opening_styles (BehaviorSignature 摘要的列表形式)
            opening_styles = _coerce_list(
                behavior_section.get("opening_styles")
            )
            if opening_styles:
                for os_line in opening_styles[:MAX_BEHAVIOR_SCENARIOS]:
                    lines.append(
                        f"- {_safe_str(os_line, MAX_BEHAVIOR_LINE)}"
                    )
            # forbidden_union (跨场景禁词)
            forbidden_union = _coerce_list(
                behavior_section.get("forbidden_union")
            )
            if forbidden_union:
                lines.append(
                    "- 避免出现: "
                    + "; ".join(
                        _safe_str(f, MAX_BEHAVIOR_LINE)
                        for f in forbidden_union[:MAX_BEHAVIOR_SCENARIOS]
                    )
                )
            if len(lines) == 1:
                return ""
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"format_behavior_failed: {exc}"
            return ""

    # --------------------------------------------------------
    # Reflection
    # --------------------------------------------------------
    def format_reflection(
        self, reflection_section: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not reflection_section:
            return ""
        try:
            recent = _coerce_list(reflection_section.get("recent"))
            if not recent:
                return ""
            lines: List[str] = ["【Reflection】"]
            count = 0
            for r in recent[:MAX_REFLECTIONS]:
                if not isinstance(r, dict):
                    continue
                kind = r.get("reflection_kind") or r.get("kind") or "silent"
                obs = _safe_str(
                    r.get("observation", ""), MAX_REFLECTION_OBS,
                )
                interp = _safe_str(
                    r.get("interpretation", ""), MAX_REFLECTION_INTERP,
                )
                if not obs and not interp:
                    continue
                if interp:
                    lines.append(
                        f"- [{_safe_str(kind, 24)}] {obs} → {interp}"
                    )
                else:
                    lines.append(
                        f"- [{_safe_str(kind, 24)}] {obs}"
                    )
                count += 1
            if count == 0:
                return ""
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"format_reflection_failed: {exc}"
            return ""

    # --------------------------------------------------------
    # Consistency Rules
    # --------------------------------------------------------
    def format_consistency_rules(
        self,
        rules: Optional[List[Any]] = None,
    ) -> str:
        if not rules:
            return ""
        try:
            lines: List[str] = ["【Consistency Rules】"]
            shown = 0
            for r in rules[:MAX_RULES]:
                # 兼容 ConsistencyRule 对象 / dict
                if isinstance(r, ConsistencyRule):
                    text = _safe_str(r.text, MAX_RULE_TEXT)
                    if not text:
                        continue
                    sev = r.severity
                    category = _safe_str(r.category, 16)
                    prefix = f"[{category}] " if category else ""
                    try:
                        sev_str = f" (severity={float(sev):.2f})"
                    except Exception:  # noqa: BLE001
                        sev_str = ""
                    lines.append(f"- {prefix}{text}{sev_str}")
                    shown += 1
                elif isinstance(r, dict):
                    text = _safe_str(r.get("text", ""), MAX_RULE_TEXT)
                    if not text:
                        continue
                    sev = r.get("severity")
                    category = _safe_str(r.get("category", ""), 16)
                    prefix = f"[{category}] " if category else ""
                    sev_str = ""
                    try:
                        if sev is not None:
                            sev_str = f" (severity={float(sev):.2f})"
                    except Exception:  # noqa: BLE001
                        sev_str = ""
                    lines.append(f"- {prefix}{text}{sev_str}")
                    shown += 1
            if shown == 0:
                return ""
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"format_consistency_failed: {exc}"
            return ""

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def format_count(self) -> int:
        return self._format_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "format_count": self._format_count,
            "last_error": self._last_error,
            "include": {
                "identity": self._include_identity,
                "values": self._include_values,
                "behavior": self._include_behavior,
                "reflection": self._include_reflection,
                "consistency": self._include_consistency,
            },
        }

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "format_count": self._format_count,
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        return result


__all__ = [
    "PersonalityPromptFormatter",
    "PERSONALITY_PROMPT_FORMATTER_SCHEMA_VERSION",
]
