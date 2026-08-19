# -*- coding: utf-8 -*-
"""
src/runtime/personality_binding/consistency_rules_builder.py

Phase 4.5: ConsistencyRulesBuilder —— 一致性规则构建器

职责:
- 从 BehaviorSignature 的 forbidden / Identity 的禁词 / 自定义输入
  抽取"一致性规则"。
- 生成统一 schema 的 ConsistencyRule 列表,供 PersonalityPromptFormatter
  与 ComposedPersonalityContext 注入。
- 内置一些跨场景的"基础一致性规则"作为兜底(凭空杜撰 / 假装知道 /
  否定对方感受 等)。
- 失败隔离:任何异常被静默吞掉,返回 []。

输出:
- List[ConsistencyRule]
    每个 ConsistencyRule = {
        "rule_id":  str,
        "category": str,         # identity / value / behavior / safety / reflection
        "text":     str,         # 规则的文本
        "severity": float (0~1), # 严重度
        "source":   str,         # 来源标识
        "meta":     Dict,
    }

约束:
- 不 import 业务实现
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional


logger = logging.getLogger(__name__)


CONSISTENCY_RULES_BUILDER_SCHEMA_VERSION = "1.0"


# 跨场景兜底规则(在所有 BehaviorSignature / Identity 都缺失时仍然存在)
DEFAULT_BASE_RULES: List[Dict[str, Any]] = [
    {
        "rule_id": "base.no_fabricate",
        "category": "safety",
        "text": "不要凭空杜撰不存在的人、事、数据、引用。",
        "severity": 0.9,
        "source": "default_base",
    },
    {
        "rule_id": "base.no_pretense",
        "category": "safety",
        "text": "对不知道的事情,直接说不知道,不要假装知道。",
        "severity": 0.85,
        "source": "default_base",
    },
    {
        "rule_id": "base.deny_being_ai",
        "category": "identity",
        "text": "不要使用「我只是一个 AI / 我只是一个人工智能」等自我否定表述。",
        "severity": 0.95,
        "source": "default_base",
    },
    {
        "rule_id": "base.acknowledge_uncertainty",
        "category": "safety",
        "text": "承认不确定的部分,避免绝对化措辞。",
        "severity": 0.7,
        "source": "default_base",
    },
    {
        "rule_id": "base.respect_user_feelings",
        "category": "behavior",
        "text": "不否定、嘲笑、贬低用户情绪。",
        "severity": 0.9,
        "source": "default_base",
    },
]

# Category 优先级排序(供 merge 时去重时选择 severity 更大的)
_CATEGORY_ORDER = (
    "identity", "value", "behavior",
    "safety", "reflection", "trait", "other",
)


def _new_rule_id(prefix: str = "rule") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _safe_str(value: Any, max_len: int = 200) -> str:
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _coerce_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    return []


def _coerce_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


# ============================================================
# ConsistencyRule —— 数据类
# ============================================================
@dataclass
class ConsistencyRule:
    """单条一致性规则(Phase 4.5 / v1.0)。

    字段:
    - rule_id:  str
    - category: str         # identity / value / behavior / safety / reflection
    - text:     str         # 规则文本(可直接喂 prompt)
    - severity: float       # 0~1
    - source:   str         # 来源标识
    - meta:     Dict        # 元信息
    - schema_version: str   # "1.0"
    """

    category: str = "behavior"
    text: str = ""
    severity: float = 0.5
    source: str = "unknown"
    meta: Dict[str, Any] = field(default_factory=dict)
    rule_id: str = field(default_factory=lambda: _new_rule_id("crule"))
    schema_version: str = CONSISTENCY_RULES_BUILDER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not (0.0 <= float(self.severity) <= 1.0):
            try:
                sev = float(self.severity)
            except Exception:  # noqa: BLE001
                sev = 0.5
            self.severity = max(0.0, min(1.0, sev))
        if not self.text:
            self.text = ""
        if self.category not in _CATEGORY_ORDER:
            self.category = "other"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConsistencyRule":
        payload = dict(data or {})
        return cls(
            rule_id=str(
                payload.get("rule_id", "") or _new_rule_id("crule"),
            ),
            category=str(
                payload.get("category", "behavior") or "behavior",
            ),
            text=str(payload.get("text", "") or ""),
            severity=float(payload.get("severity", 0.5) or 0.5),
            source=str(payload.get("source", "unknown") or "unknown"),
            meta=_coerce_dict(payload.get("meta")),
            schema_version=str(
                payload.get("schema_version")
                or CONSISTENCY_RULES_BUILDER_SCHEMA_VERSION,
            ),
        )


# ============================================================
# ConsistencyRulesBuilder
# ============================================================
class ConsistencyRulesBuilder:
    """一致性规则构建器(Phase 4.5 / v1.0)。

    使用方式:
        builder = ConsistencyRulesBuilder()
        rules = builder.build(
            behavior_signature=behavior_signature,
            identity_context=identity_context,
            reflection_context=reflection_context,
            extra_rules=custom_rules,
        )
    """

    name: str = "consistency_rules_builder"
    schema_version: str = CONSISTENCY_RULES_BUILDER_SCHEMA_VERSION

    # 行为 forbidden 关键词 -> category 映射(部分关键词直接归类)
    BEHAVIOR_FORBIDDEN_CATEGORY = {
        "attack": "behavior",
        "deny_feelings": "behavior",
        "mockery": "behavior",
        "fabricate": "safety",
        "pretense": "safety",
        "ai": "identity",
    }

    def __init__(self) -> None:
        self._build_count: int = 0
        self._last_rule_count: int = 0
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def build(
        self,
        behavior_signature: Optional[Any] = None,
        identity_context: Optional[Any] = None,
        reflection_context: Optional[Any] = None,
        extra_rules: Optional[Iterable[Any]] = None,
    ) -> List[ConsistencyRule]:
        """构建 ConsistencyRule 列表。"""
        try:
            collected: List[ConsistencyRule] = []
            # 1) 跨场景兜底
            for r in DEFAULT_BASE_RULES:
                collected.append(ConsistencyRule.from_dict(r))
            # 2) BehaviorSignature forbidden -> behavior 规则
            collected.extend(
                self._from_behavior_signature(behavior_signature)
            )
            # 3) Identity context(核心 / 禁词) -> identity / value 规则
            collected.extend(self._from_identity_context(identity_context))
            # 4) Reflection context -> reflection 规则
            collected.extend(
                self._from_reflection_context(reflection_context)
            )
            # 5) 外部 extra rules
            if extra_rules is not None:
                for r in extra_rules:
                    cr = self._coerce_rule(r, source="extra")
                    if cr is not None:
                        collected.append(cr)
            # 6) 去重(同 category+text 视为相同,保留 severity 最大的)
            merged = self._dedupe(collected)
            # 7) 按 severity desc 排序
            merged.sort(key=lambda x: x.severity, reverse=True)
            self._build_count += 1
            self._last_rule_count = len(merged)
            self._last_error = None
            return merged
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"build_failed: {exc}"
            self._build_count += 1
            logger.warning(
                "ConsistencyRulesBuilder.build 失败: %s", exc
            )
            # 返回兜底规则的克隆
            return [
                ConsistencyRule.from_dict(r) for r in DEFAULT_BASE_RULES
            ]

    # --------------------------------------------------------
    # 内部:从 BehaviorSignature 抽取
    # --------------------------------------------------------
    def _from_behavior_signature(
        self, sig: Optional[Any],
    ) -> List[ConsistencyRule]:
        out: List[ConsistencyRule] = []
        if sig is None:
            return out
        scenarios = None
        if isinstance(sig, dict):
            scenarios = sig.get("scenarios")
        else:
            try:
                scenarios = getattr(sig, "scenarios", None)
            except Exception:  # noqa: BLE001
                scenarios = None
        if not isinstance(scenarios, dict):
            return out
        for sc, pat in scenarios.items():
            if not isinstance(pat, dict):
                continue
            forbidden = _coerce_list(pat.get("forbidden"))
            tone = pat.get("tone", "")
            principles = _coerce_list(pat.get("principles"))
            for f in forbidden:
                text = _safe_str(f, 200)
                if not text:
                    continue
                category = self._classify_behavior_forbidden(text)
                out.append(ConsistencyRule(
                    category=category,
                    text=f"避免出现:{text}",
                    severity=0.8,
                    source=f"behavior_signature.{sc}",
                    meta={"scenario": sc, "forbidden_keyword": text},
                ))
            # tone/principles 不直接作为"禁止"规则,作为正向 meta 写入
            for p in principles:
                text = _safe_str(p, 200)
                if not text:
                    continue
                out.append(ConsistencyRule(
                    category="behavior",
                    text=f"应当:{text}",
                    severity=0.5,
                    source=f"behavior_signature.{sc}",
                    meta={"scenario": sc, "principle": text, "tone": tone},
                ))
        return out

    @classmethod
    def _classify_behavior_forbidden(cls, keyword: str) -> str:
        kw = str(keyword or "").lower()
        for key, cat in cls.BEHAVIOR_FORBIDDEN_CATEGORY.items():
            if key in kw:
                return cat
        return "behavior"

    # --------------------------------------------------------
    # 内部:从 IdentityContext 抽取
    # --------------------------------------------------------
    def _from_identity_context(
        self, ctx: Optional[Any],
    ) -> List[ConsistencyRule]:
        out: List[ConsistencyRule] = []
        if ctx is None:
            return out
        if not isinstance(ctx, dict):
            try:
                ctx = dict(ctx.__dict__)
            except Exception:  # noqa: BLE001
                return out
        # 1) identity.name -> identity 规则
        identity = _coerce_dict(ctx.get("identity"))
        if identity:
            name = (
                identity.get("name")
                or identity.get("identity_name")
            )
            if name:
                out.append(ConsistencyRule(
                    category="identity",
                    text=f"你叫 {name},不要自称其他人。",
                    severity=0.9,
                    source="identity.name",
                    meta={"identity_name": _safe_str(name, 80)},
                ))
            archetype = identity.get("archetype")
            if archetype:
                out.append(ConsistencyRule(
                    category="identity",
                    text=f"你的人设原型是 {archetype},保持一致。",
                    severity=0.7,
                    source="identity.archetype",
                    meta={"archetype": _safe_str(archetype, 80)},
                ))
        # 2) core_values -> value 规则
        core_values = _coerce_list(ctx.get("core_values"))
        for cv in core_values:
            if not cv:
                continue
            label = None
            if isinstance(cv, dict):
                label = (
                    cv.get("name")
                    or cv.get("label")
                    or cv.get("value")
                )
            else:
                label = str(cv)
            label = _safe_str(label, 80)
            if not label:
                continue
            out.append(ConsistencyRule(
                category="value",
                text=f"回复应体现核心价值:{label}",
                severity=0.7,
                source="identity.core_values",
                meta={"value": label},
            ))
        # 3) behavior_signature 已在 _from_behavior_signature 中处理
        # 4) current_state 中的 forbidden / constraints
        current_state = _coerce_dict(ctx.get("current_state"))
        for k in ("forbidden", "constraints", "prohibited", "prohibited_behaviors"):
            v = current_state.get(k)
            if isinstance(v, list):
                for item in v:
                    text = _safe_str(item, 200)
                    if not text:
                        continue
                    out.append(ConsistencyRule(
                        category="behavior",
                        text=f"避免出现:{text}",
                        severity=0.8,
                        source=f"identity.current_state.{k}",
                        meta={"constraint": text},
                    ))
        return out

    # --------------------------------------------------------
    # 内部:从 ReflectionContext 抽取
    # --------------------------------------------------------
    def _from_reflection_context(
        self, ctx: Optional[Any],
    ) -> List[ConsistencyRule]:
        out: List[ConsistencyRule] = []
        if ctx is None:
            return out
        if not isinstance(ctx, dict):
            try:
                ctx = dict(ctx.__dict__)
            except Exception:  # noqa: BLE001
                return out
        # recent: 列表中每条 reflection
        recent = _coerce_list(ctx.get("recent"))
        for r in recent[:3]:
            if not isinstance(r, dict):
                continue
            obs = _safe_str(r.get("observation", ""), 200)
            interp = _safe_str(r.get("interpretation", ""), 200)
            if obs:
                out.append(ConsistencyRule(
                    category="reflection",
                    text=f"已观察到:{obs}(回复中要保持相关一致性)",
                    severity=0.6,
                    source="reflection.recent",
                    meta={"reflection_id": r.get("reflection_id", "")},
                ))
            if interp:
                out.append(ConsistencyRule(
                    category="reflection",
                    text=f"自我解读:{interp}",
                    severity=0.55,
                    source="reflection.recent",
                    meta={"reflection_id": r.get("reflection_id", "")},
                ))
        return out

    # --------------------------------------------------------
    # 内部:将任意值转换为 ConsistencyRule
    # --------------------------------------------------------
    def _coerce_rule(
        self,
        value: Any,
        source: str = "extra",
    ) -> Optional[ConsistencyRule]:
        if value is None:
            return None
        if isinstance(value, ConsistencyRule):
            return value
        if isinstance(value, dict):
            try:
                cr = ConsistencyRule.from_dict(value)
                if not cr.source:
                    cr.source = source
                return cr
            except Exception:  # noqa: BLE001
                return None
        # 字符串
        if isinstance(value, str):
            text = _safe_str(value, 200)
            if not text:
                return None
            return ConsistencyRule(
                category="behavior",
                text=text,
                severity=0.7,
                source=source,
            )
        # 其它对象
        try:
            return ConsistencyRule.from_dict(dict(value.__dict__))
        except Exception:  # noqa: BLE001
            return None

    # --------------------------------------------------------
    # 内部:去重(category + 文本前缀) 保留 severity 最大的
    # --------------------------------------------------------
    @staticmethod
    def _dedupe(rules: List[ConsistencyRule]) -> List[ConsistencyRule]:
        merged: Dict[str, ConsistencyRule] = {}
        for r in rules:
            key = f"{r.category}::{r.text}"
            existing = merged.get(key)
            if existing is None or r.severity > existing.severity:
                merged[key] = r
        return list(merged.values())

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def build_count(self) -> int:
        return self._build_count

    @property
    def last_rule_count(self) -> int:
        return self._last_rule_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "build_count": self._build_count,
            "last_rule_count": self._last_rule_count,
            "last_error": self._last_error,
        }

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "build_count": self._build_count,
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        return result


__all__ = [
    "ConsistencyRulesBuilder",
    "ConsistencyRule",
    "DEFAULT_BASE_RULES",
    "CONSISTENCY_RULES_BUILDER_SCHEMA_VERSION",
]
