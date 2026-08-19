# -*- coding: utf-8 -*-
"""
src/runtime/personality_binding/personality_context_composer.py

Phase 4.5: PersonalityContextComposer —— 多来源人格上下文合并器

职责:
- 接收来自 Phase 4.2 SelfModel / Phase 4.3 SelfReflection /
  Phase 4.4 SelfIdentity 的运行时上下文数据。
- 按"身份 / 反思 / 行为 / 成长"四个语义分区进行合并。
- 输出统一的 ComposedPersonalityContext 数据类。
- 任意来源缺失时,只是该分区为空,不影响其它分区。
- 失败隔离:任何异常被静默吞掉,返回只含 schema_version 的空 context。

输入 contract:
- identity_context:   Dict | IdentityContext | None
- reflection_context: Dict | None
- behavior_context:   Dict | None
- growth_context:     Dict | None
- behavior_signature: Dict | BehaviorSignature | None
- consistency_constraints: Dict | List | None

输出: ComposedPersonalityContext

约束:
- 不 import 业务实现(不 import openai / qwen / llava / vision SDK)
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- Runtime → Service → Snapshot 单向依赖
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
    ConsistencyRule,
)


logger = logging.getLogger(__name__)


PERSONALITY_CONTEXT_COMPOSER_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.utcnow().isoformat() + "Z"


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


def _extract_identity_id(payload: Any) -> str:
    """从多种来源中尽力抽出 identity_id 字符串。"""
    if payload is None:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("identity_id", "") or "")
    # dataclass / object
    return str(getattr(payload, "identity_id", "") or "")


def _extract_identity_context(
    raw: Optional[Any],
) -> Dict[str, Any]:
    """从 IdentityContext / dict / 对象抽出"身份上下文" dict。

    Phase 4.4 IdentityContext 关键字段:
    - identity / core_values / stable_traits / preferences
    - current_state / recent_changes
    - reflection / behavior_signature
    - meta / identity_id
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    # dataclass / object
    out: Dict[str, Any] = {}
    for key in (
        "identity", "core_values", "stable_traits", "preferences",
        "current_state", "recent_changes",
        "reflection", "behavior_signature",
        "meta", "identity_id", "version", "timestamp",
        "has_snapshot", "schema_version",
    ):
        try:
            v = getattr(raw, key, None)
        except Exception:  # noqa: BLE001
            v = None
        if v is None:
            continue
        if isinstance(v, (dict, list, str, int, float, bool)):
            out[key] = v
        else:
            try:
                out[key] = dict(v.__dict__)
            except Exception:  # noqa: BLE001
                out[key] = str(v)
    return out


def _extract_reflection_context(
    raw: Optional[Any],
) -> Dict[str, Any]:
    """从 SelfReflectionContextProvider 输出抽出"反思上下文" dict。"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _extract_behavior_context(
    raw: Optional[Any],
    behavior_signature: Optional[Any] = None,
) -> Dict[str, Any]:
    """从 BehaviorSignature / BehaviorSignatureProvider 输出抽出"行为上下文" dict。

    优先取 behavior_signature;若没有,再退回到 raw 行为 dict。
    """
    if behavior_signature is not None:
        if isinstance(behavior_signature, dict):
            sig = dict(behavior_signature)
        else:
            # dataclass / object
            sig = {}
            for key in (
                "signature_id", "identity_id", "default_scenario",
                "scenarios", "meta", "created_at", "schema_version",
            ):
                try:
                    v = getattr(behavior_signature, key, None)
                except Exception:  # noqa: BLE001
                    v = None
                if v is None:
                    continue
                if isinstance(v, (dict, list, str, int, float, bool)):
                    sig[key] = v
                else:
                    try:
                        sig[key] = dict(v.__dict__)
                    except Exception:  # noqa: BLE001
                        sig[key] = str(v)
        # 提取 speaking style 摘要
        try:
            scenarios = sig.get("scenarios") or {}
            if isinstance(scenarios, dict):
                tones = []
                openings = []
                forbidden_union: List[str] = []
                for sc, pat in scenarios.items():
                    if isinstance(pat, dict):
                        tone = pat.get("tone")
                        opening = pat.get("opening_style")
                        forbidden = pat.get("forbidden") or []
                        if tone:
                            tones.append(f"{sc}={tone}")
                        if opening:
                            openings.append(f"{sc}: {opening}")
                        if isinstance(forbidden, list):
                            for f in forbidden:
                                fs = _safe_str(f, 80)
                                if fs and fs not in forbidden_union:
                                    forbidden_union.append(fs)
                if tones:
                    sig["speaking_style_summary"] = "; ".join(tones)
                if openings:
                    sig["opening_styles"] = openings
                if forbidden_union:
                    sig["forbidden_union"] = forbidden_union
        except Exception:  # noqa: BLE001
            pass
        return sig
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _extract_growth_context(raw: Optional[Any]) -> Dict[str, Any]:
    """从 SelfModelContextProvider 输出抽出"成长 / 基础人格" dict。"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _extract_consistency_constraints(
    raw: Optional[Any],
) -> List[ConsistencyRule]:
    """从 consistency_constraints 抽取规则列表(转换为 ConsistencyRule 对象)。

    支持:
    - ConsistencyRule 对象:直接保留
    - dict:转换为 ConsistencyRule(失败时跳过)
    - 其它可序列化对象:尝试 dict() 转换
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        out: List[ConsistencyRule] = []
        for item in raw:
            cr = _coerce_to_consistency_rule(item)
            if cr is not None:
                out.append(cr)
        return out
    if isinstance(raw, dict):
        # 兼容把"rules"作为 key 的 dict
        if "rules" in raw and isinstance(raw["rules"], list):
            return _extract_consistency_constraints(raw["rules"])
        # 单条 rule
        cr = _coerce_to_consistency_rule(raw)
        return [cr] if cr is not None else []
    return []


def _coerce_to_consistency_rule(
    value: Any,
) -> Optional[ConsistencyRule]:
    """将任意值转换为 ConsistencyRule。失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, ConsistencyRule):
        return value
    if isinstance(value, dict):
        try:
            return ConsistencyRule.from_dict(value)
        except Exception:  # noqa: BLE001
            return None
    # 其它对象:尝试 dict() 转换
    try:
        return ConsistencyRule.from_dict(dict(value.__dict__))
    except Exception:  # noqa: BLE001
        return None


def _has_non_default_rules(rules: List[ConsistencyRule]) -> bool:
    """判断是否包含非 default_base 规则。"""
    if not rules:
        return False
    for r in rules:
        source = ""
        if isinstance(r, ConsistencyRule):
            source = r.source
        elif isinstance(r, dict):
            source = r.get("source", "") or ""
        if source and source != "default_base":
            return True
    return False


class PersonalityContextComposer:
    """多来源人格上下文合并器(Phase 4.5 / v1.0)。

    使用方式:
        composer = PersonalityContextComposer()
        ctx = composer.compose(
            identity_context=id_ctx,
            reflection_context=ref_ctx,
            behavior_context=beh_ctx,
            growth_context=growth_ctx,
            behavior_signature=behavior_signature,
            consistency_constraints=constraints,
        )

    任何来源缺失或异常 → 对应分区为空,其它分区照常合并。
    整体失败隔离 → 返回 has_binding=False 的空 context。
    """

    name: str = "personality_context_composer"
    schema_version: str = PERSONALITY_CONTEXT_COMPOSER_SCHEMA_VERSION

    def __init__(self) -> None:
        self._compose_count: int = 0
        self._last_identity_id: str = ""
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def compose(
        self,
        identity_context: Optional[Any] = None,
        reflection_context: Optional[Any] = None,
        behavior_context: Optional[Any] = None,
        growth_context: Optional[Any] = None,
        behavior_signature: Optional[Any] = None,
        consistency_constraints: Optional[Any] = None,
        identity_id: Optional[str] = None,
    ) -> ComposedPersonalityContext:
        """合并多来源上下文,生成 ComposedPersonalityContext。"""
        try:
            id_section = _extract_identity_context(identity_context)
            ref_section = _extract_reflection_context(reflection_context)
            beh_section = _extract_behavior_context(
                behavior_context, behavior_signature,
            )
            growth_section = _extract_growth_context(growth_context)
            rules = _extract_consistency_constraints(consistency_constraints)

            # identity_id 优先级: 参数 > identity_context > behavior_signature > growth
            resolved_id = (
                identity_id
                or _extract_identity_id(id_section)
                or _extract_identity_id(beh_section)
                or _extract_identity_id(growth_section)
            )

            # has_binding: 只在有"非兜底"内容时为 True
            # (default_base 兜底规则不算 binding,只有真实输入才算)
            has_binding = bool(
                id_section or ref_section or beh_section
                or growth_section or _has_non_default_rules(rules)
            )

            meta: Dict[str, Any] = {
                "composed_at": _now_iso(),
                "has_identity": bool(id_section),
                "has_reflection": bool(ref_section),
                "has_behavior": bool(beh_section),
                "has_growth": bool(growth_section),
                "has_constraints": bool(rules),
                "has_non_default_rules": _has_non_default_rules(rules),
                "composer": self.name,
                "composer_schema_version": self.schema_version,
            }

            ctx = ComposedPersonalityContext(
                identity_context=id_section,
                reflection_context=ref_section,
                behavior_context=beh_section,
                growth_context=growth_section,
                consistency_rules=rules,
                meta=meta,
                schema_version=PERSONA_BINDING_CONTEXT_SCHEMA_VERSION,
                has_binding=has_binding,
                identity_id=resolved_id,
                timestamp=_now_iso(),
                version=self._compose_count + 1,
                source="personality_runtime_binding",
            )
            self._compose_count += 1
            self._last_identity_id = resolved_id
            self._last_error = None
            return ctx
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"compose_failed: {exc}"
            self._compose_count += 1
            logger.warning(
                "PersonalityContextComposer.compose 失败: %s", exc
            )
            return self._empty_context()

    @staticmethod
    def _empty_context() -> ComposedPersonalityContext:
        return ComposedPersonalityContext(
            identity_context={},
            reflection_context={},
            behavior_context={},
            growth_context={},
            consistency_rules=[],
            meta={
                "composed_at": _now_iso(),
                "has_identity": False,
                "has_reflection": False,
                "has_behavior": False,
                "has_growth": False,
                "has_constraints": False,
            },
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
    def compose_count(self) -> int:
        return self._compose_count

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
            "compose_count": self._compose_count,
            "last_identity_id": self._last_identity_id,
            "last_error": self._last_error,
        }

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "compose_count": self._compose_count,
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        return result


__all__ = [
    "PersonalityContextComposer",
    "PERSONALITY_CONTEXT_COMPOSER_SCHEMA_VERSION",
]
