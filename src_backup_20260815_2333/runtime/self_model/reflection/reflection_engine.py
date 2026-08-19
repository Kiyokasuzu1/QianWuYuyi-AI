# -*- coding: utf-8 -*-
"""
src/runtime/self_model/reflection/reflection_engine.py

Phase 4.3: ReflectionEngine —— 自我反思引擎

职责:
- 读取 GrowthAuditRecord
- 基于 diff 字段生成 evidence-based 的 ReflectionRecord
- 不创造没有证据的事实
- 不直接修改人格
- 不调用 LLM(纯启发式 / 模板化解读)

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine
- 异常隔离:任何 reflect 失败返回 None

设计:
- priority:
    identity / value → HIGH
    trait / preference → MEDIUM
    state / entry → LOW
    无变化 / 纯版本号 → NONE(可能直接跳过)
- kind:
    identity_change → IDENTITY_DRIFT
    value_change → VALUE_SHIFT
    trait_change → TRAIT_TREND
    preference → PREFERENCE
    state_change → STATE_NOTE
    entry_added / entry_removed → GROWTH_NOTE
    initial → INITIAL
    version_bump only → SILENT(可由 Engine 配置是否产出)
- confidence:
    0.5 (无变化) ~ 0.9 (单一明确变化)
    变化越多 / 越模糊,confidence 越低
- relation_to_values:
    基于核心价值关键词映射(无侵入)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.self_model.reflection.reflection_record import (
    ReflectionRecord,
    ReflectionKind,
    ReflectionPriority,
    ReflectionType,
    ConflictType,
)
from src.runtime.self_model.reflection.contradiction_detector import (
    ContradictionDetector,
    ContradictionRecord,
    CONTRADICTION_DETECTOR_SCHEMA_VERSION,
)
from src.runtime.self_model.reflection.consistency_checker import (
    ConsistencyChecker,
    ConsistencyReport,
    CONSISTENCY_CHECKER_SCHEMA_VERSION,
)


logger = logging.getLogger(__name__)


REFLECTION_ENGINE_SCHEMA_VERSION = "1.0"

# 核心价值关键词映射:key 关键词 → [关联价值]
# 这是启发式,无侵入,不引用 personality 业务模块
_VALUE_KEYWORDS: Dict[str, List[str]] = {
    # 温暖 / 同理
    "warmth": ["kindness", "companionship"],
    "warm": ["kindness"],
    "kindness": ["kindness", "companionship"],
    "patience": ["patience", "mindfulness"],
    "patient": ["patience"],
    "empathy": ["kindness", "companionship"],
    "compassion": ["kindness"],
    "caring": ["kindness", "companionship"],
    # 真诚
    "honesty": ["sincerity"],
    "honest": ["sincerity"],
    "sincerity": ["sincerity"],
    "sincere": ["sincerity"],
    "truth": ["sincerity"],
    # 勇气
    "courage": ["courage"],
    "brave": ["courage"],
    "bold": ["courage"],
    # 好奇心
    "curiosity": ["curiosity", "growth"],
    "curious": ["curiosity"],
    "exploration": ["curiosity", "growth"],
    # 成长
    "growth": ["growth", "continuity"],
    "learning": ["growth", "curiosity"],
    "improvement": ["growth"],
    # 陪伴
    "companionship": ["companionship", "kindness"],
    "presence": ["companionship", "mindfulness"],
    "connection": ["companionship"],
    # 稳定
    "stability": ["continuity", "stability"],
    "continuity": ["continuity", "identity"],
    "consistency": ["continuity"],
    # 边界
    "boundary": ["boundaries", "self_preservation"],
    "boundaries": ["boundaries"],
    "autonomy": ["autonomy", "identity"],
    # 审美 / 风格
    "aesthetic": ["aesthetic"],
    "elegance": ["aesthetic"],
    "style": ["aesthetic"],
    "softness": ["aesthetic", "kindness"],
    # 觉察
    "mindfulness": ["mindfulness", "patience"],
    "awareness": ["mindfulness"],
    "reflection": ["mindfulness", "continuity"],
}


def _norm_key(s: Any) -> str:
    """把 value 名称标准化。"""
    if s is None:
        return ""
    try:
        return str(s).strip().lower()
    except Exception:  # noqa: BLE001
        return ""


def _extract_name(item: Any) -> str:
    """从 dict / 对象中提取 'name' 字段。"""
    if isinstance(item, dict):
        return _norm_key(item.get("name") or item.get("key") or item.get("label"))
    return _norm_key(getattr(item, "name", None) or getattr(item, "key", None))


def _extract_value(item: Any) -> Any:
    """从 dict / 对象中提取 'value' 字段。"""
    if isinstance(item, dict):
        return item.get("value")
    return getattr(item, "value", None)


def _lookup_values(name: str) -> List[str]:
    """在关键词表中查找关联价值。"""
    if not name:
        return []
    matches: List[str] = []
    for kw, vals in _VALUE_KEYWORDS.items():
        if kw in name or name in kw:
            for v in vals:
                if v not in matches:
                    matches.append(v)
    return matches


def _resolve_diff(
    diff_or_audit: Any,
) -> Dict[str, Any]:
    """从 audit record 或 diff dict 中安全提取 diff 字段。

    支持:
    - dict(已经是 diff 结构)
    - GrowthAuditRecord 实例(有 .diff 属性)
    - 任何带 .diff 属性的对象
    """
    if diff_or_audit is None:
        return {}
    if isinstance(diff_or_audit, dict):
        return dict(diff_or_audit)
    diff_attr = getattr(diff_or_audit, "diff", None)
    if isinstance(diff_attr, dict):
        return dict(diff_attr)
    return {}


def _audit_categories(audit_or_diff: Any) -> List[str]:
    """从 audit record 或 diff 中提取 categories 列表。"""
    if audit_or_diff is None:
        return []
    if isinstance(audit_or_diff, dict):
        cats = audit_or_diff.get("categories")
        if isinstance(cats, list):
            return [str(c) for c in cats]
    cats_attr = getattr(audit_or_diff, "categories", None)
    if isinstance(cats_attr, list):
        return [str(c) for c in cats_attr]
    return []


def _audit_summary(audit_or_diff: Any) -> str:
    """从 audit 中提取 summary。"""
    if audit_or_diff is None:
        return ""
    if isinstance(audit_or_diff, dict):
        return str(audit_or_diff.get("summary", "") or "")
    return str(getattr(audit_or_diff, "summary", "") or "")


def _audit_id(audit: Any) -> str:
    if audit is None:
        return ""
    if isinstance(audit, dict):
        return str(audit.get("record_id", "") or "")
    return str(getattr(audit, "record_id", "") or "")


def _audit_identity_id(audit: Any) -> str:
    if audit is None:
        return ""
    if isinstance(audit, dict):
        return str(audit.get("identity_id", "") or "")
    return str(getattr(audit, "identity_id", "") or "")


def _audit_versions(audit: Any) -> tuple:
    if audit is None:
        return (0, 0)
    if isinstance(audit, dict):
        return (
            int(audit.get("from_version", 0) or 0),
            int(audit.get("to_version", 0) or 0),
        )
    return (
        int(getattr(audit, "from_version", 0) or 0),
        int(getattr(audit, "to_version", 0) or 0),
    )


def _truncate_evidence(diff: Dict[str, Any], max_field_items: int = 5) -> Dict[str, Any]:
    """截断 diff 摘要,避免 evidence 太大。"""
    if not isinstance(diff, dict):
        return {}
    ev: Dict[str, Any] = {}
    for key, val in diff.items():
        if key == "summary":
            ev[key] = val
            continue
        if isinstance(val, dict):
            # identity / current_state 等
            ev[key] = {
                "added_keys": list((val.get("added") or {}).keys())[:max_field_items]
                if isinstance(val.get("added"), dict)
                else [],
                "removed_keys": list((val.get("removed") or {}).keys())[:max_field_items]
                if isinstance(val.get("removed"), dict)
                else [],
                "changed_keys": list((val.get("changed") or {}).keys())[:max_field_items]
                if isinstance(val.get("changed"), dict)
                else [],
            }
        elif isinstance(val, list):
            # list 类型(如 core_values 的 added/removed/changed)
            ev[key] = {"count": len(val)}
        else:
            ev[key] = val
    return ev


class ReflectionEngine:
    """自我反思引擎(Phase 4.3 / v1.0)。

    字段:
    - _reflect_count:   int            # 累计 reflect 次数
    - _last_reflection: Optional[ReflectionRecord]
    - _last_error:      Optional[str]
    - _skip_silent:     bool           # 纯版本号 / 无变化是否产出 SILENT record

    方法:
    - reflect(audit_or_diff, snapshot_history=None) -> Optional[ReflectionRecord]
    - _build_observation(diff, categories) -> str
    - _build_interpretation(kind, diff, related_values) -> str
    - _infer_priority(categories) -> ReflectionPriority
    - _infer_kind(categories) -> ReflectionKind
    - _compute_confidence(diff, categories) -> float
    - health_check() / describe()
    """

    def __init__(self, skip_silent: bool = True) -> None:
        self._reflect_count: int = 0
        self._last_reflection: Optional[ReflectionRecord] = None
        self._last_error: Optional[str] = None
        self._skip_silent: bool = skip_silent

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def reflect(
        self,
        audit_or_diff: Any,
        snapshot_history: Optional[Sequence[Any]] = None,
        source: str = "runtime",
        categories: Optional[List[str]] = None,
    ) -> Optional[ReflectionRecord]:
        """从 GrowthAuditRecord(或 diff dict)生成一条 ReflectionRecord。

        Args:
            audit_or_diff:   GrowthAuditRecord 或 diff dict
            snapshot_history: 可选 snapshot 列表(留作扩展,当前未使用)
            source:          来源
            categories:      可选类别列表(若提供,优先于从 audit / diff 推断)

        Returns:
            ReflectionRecord;若纯版本号 / 无变化且 skip_silent=True,可能返回 None。
        """
        if audit_or_diff is None:
            self._last_error = "reflect_input_none"
            return None
        try:
            diff = _resolve_diff(audit_or_diff)
            cats = _audit_categories(audit_or_diff)
            # 若调用方显式提供 categories,优先使用
            if categories:
                cats = list(categories)
            # 若 audit 没带 categories,尝试从 diff 推断
            if not cats:
                cats = self._infer_categories_from_diff(diff)
            priority = self._infer_priority(cats)
            if priority == ReflectionPriority.NONE.value and self._skip_silent:
                self._last_error = "skipped_silent"
                return None
            kind = self._infer_kind(cats, audit_or_diff)
            observation = self._build_observation(diff, cats)
            related_values = self._extract_related_values(diff, cats)
            interpretation = self._build_interpretation(
                kind, diff, related_values, cats,
            )
            confidence = self._compute_confidence(diff, cats)
            evidence = _truncate_evidence(diff)
            from_v, to_v = _audit_versions(audit_or_diff)
            fid = _audit_identity_id(audit_or_diff) or _identity_from_diff(diff)
            aid = _audit_id(audit_or_diff)
            trigger = self._primary_trigger(cats)
            record = ReflectionRecord(
                identity_id=str(fid or ""),
                source_audit_id=str(aid or ""),
                trigger_category=str(trigger or ""),
                reflection_kind=kind,
                priority=priority,
                observation=observation,
                interpretation=interpretation,
                relation_to_values=list(related_values),
                confidence=confidence,
                from_version=from_v,
                to_version=to_v,
                evidence=evidence,
                source=source,
                metadata={
                    "all_categories": list(cats),
                },
            )
            self._reflect_count += 1
            self._last_reflection = record
            self._last_error = None
            return record
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"reflect_failed: {exc}"
            logger.warning("ReflectionEngine.reflect 失败: %s", exc)
            return None

    # --------------------------------------------------------
    # 推断
    # --------------------------------------------------------
    def _infer_categories_from_diff(self, diff: Dict[str, Any]) -> List[str]:
        cats: List[str] = []
        if not isinstance(diff, dict):
            return cats
        if diff.get("identity", {}).get("added") or \
           diff.get("identity", {}).get("removed") or \
           diff.get("identity", {}).get("changed"):
            cats.append("identity")
        if diff.get("core_values", {}).get("added") or \
           diff.get("core_values", {}).get("removed") or \
           diff.get("core_values", {}).get("changed"):
            cats.append("value_change")
        if diff.get("stable_traits", {}).get("added") or \
           diff.get("stable_traits", {}).get("removed") or \
           diff.get("stable_traits", {}).get("changed"):
            cats.append("trait_change")
        if diff.get("preferences", {}).get("added") or \
           diff.get("preferences", {}).get("removed") or \
           diff.get("preferences", {}).get("changed"):
            cats.append("preference")
        if diff.get("current_state", {}).get("added") or \
           diff.get("current_state", {}).get("removed") or \
           diff.get("current_state", {}).get("changed"):
            cats.append("state_change")
        if diff.get("entries", {}).get("added"):
            cats.append("entry_added")
        if diff.get("entries", {}).get("removed"):
            cats.append("entry_removed")
        if not cats:
            cats.append("version_bump")
        return cats

    def _infer_priority(self, categories: List[str]) -> str:
        if not categories:
            return ReflectionPriority.NONE.value
        if "identity" in categories or "value_change" in categories:
            return ReflectionPriority.HIGH.value
        if "trait_change" in categories or "preference" in categories:
            return ReflectionPriority.MEDIUM.value
        if (
            "state_change" in categories
            or "entry_added" in categories
            or "entry_removed" in categories
        ):
            return ReflectionPriority.LOW.value
        if "version_bump" in categories or "initial" in categories:
            # initial 仍要记
            if "initial" in categories:
                return ReflectionPriority.LOW.value
            return ReflectionPriority.NONE.value
        return ReflectionPriority.NONE.value

    def _infer_kind(self, categories: List[str], audit: Any) -> str:
        if "initial" in categories:
            return ReflectionKind.INITIAL.value
        if "identity" in categories:
            return ReflectionKind.IDENTITY_DRIFT.value
        if "value_change" in categories:
            return ReflectionKind.VALUE_SHIFT.value
        if "trait_change" in categories:
            return ReflectionKind.TRAIT_TREND.value
        if "preference" in categories:
            return ReflectionKind.PREFERENCE.value
        if "state_change" in categories:
            return ReflectionKind.STATE_NOTE.value
        if "entry_added" in categories or "entry_removed" in categories:
            return ReflectionKind.GROWTH_NOTE.value
        if "version_bump" in categories:
            return ReflectionKind.SILENT.value
        return ReflectionKind.SILENT.value

    def _primary_trigger(self, categories: List[str]) -> str:
        """从 categories 列表中取最高优先级的类别作为 trigger。"""
        order = [
            "identity", "value_change", "trait_change",
            "preference", "state_change",
            "entry_added", "entry_removed",
            "initial", "version_bump",
        ]
        for c in order:
            if c in categories:
                return c
        return categories[0] if categories else ""

    def _compute_confidence(
        self, diff: Dict[str, Any], categories: List[str],
    ) -> float:
        """基于变化数量计算 confidence。"""
        summary = diff.get("summary", {}) if isinstance(diff, dict) else {}
        try:
            total = int(summary.get("total_changes", 0) or 0)
        except (TypeError, ValueError):
            total = 0
        if total == 0:
            return 0.5
        if total == 1:
            return 0.9
        if total == 2:
            return 0.8
        if total <= 4:
            return 0.7
        if total <= 8:
            return 0.6
        return 0.5

    # --------------------------------------------------------
    # 文本生成
    # --------------------------------------------------------
    def _build_observation(
        self, diff: Dict[str, Any], categories: List[str],
    ) -> str:
        """构造客观观察陈述(基于 diff 数据)。"""
        if not diff:
            return "SelfModel snapshot recorded."
        if not categories or "version_bump" in categories:
            return "SelfModel version bumped without structural changes."
        if "initial" in categories:
            v = diff.get("to_version", 1)
            return f"Initial SelfModel snapshot established at v{v}."
        parts: List[str] = []
        if "identity" in categories:
            identity_diff = diff.get("identity") or {}
            added = list((identity_diff.get("added") or {}).keys())
            removed = list((identity_diff.get("removed") or {}).keys())
            changed = list((identity_diff.get("changed") or {}).keys())
            detail: List[str] = []
            if added:
                detail.append(f"added={added}")
            if removed:
                detail.append(f"removed={removed}")
            if changed:
                detail.append(f"changed={changed}")
            if detail:
                parts.append("identity " + "; ".join(detail))
        if "value_change" in categories:
            cv = diff.get("core_values") or {}
            added_names = [_extract_name(x) for x in (cv.get("added") or [])]
            removed_names = [_extract_name(x) for x in (cv.get("removed") or [])]
            changed_names = [
                c.get("key", "?") for c in (cv.get("changed") or [])
            ]
            detail = []
            if added_names:
                detail.append(f"added={added_names}")
            if removed_names:
                detail.append(f"removed={removed_names}")
            if changed_names:
                detail.append(f"changed={changed_names}")
            if detail:
                parts.append("core_values " + "; ".join(detail))
        if "trait_change" in categories:
            st = diff.get("stable_traits") or {}
            added = [_extract_name(x) for x in (st.get("added") or [])]
            removed = [_extract_name(x) for x in (st.get("removed") or [])]
            changed = st.get("changed") or []
            detail: List[str] = []
            if added:
                detail.append(f"added={added}")
            if removed:
                detail.append(f"removed={removed}")
            if changed:
                # 描述 before/after
                items = []
                for c in changed:
                    key = c.get("key", "?")
                    b = _extract_value(c.get("before"))
                    a = _extract_value(c.get("after"))
                    items.append(f"{key} {b}->{a}")
                detail.append("changed=" + items)
            if detail:
                parts.append("stable_traits " + "; ".join(detail))
        if "preference" in categories:
            pref = diff.get("preferences") or {}
            added = [_extract_name(x) for x in (pref.get("added") or [])]
            removed = [_extract_name(x) for x in (pref.get("removed") or [])]
            changed = pref.get("changed") or []
            detail: List[str] = []
            if added:
                detail.append(f"added={added}")
            if removed:
                detail.append(f"removed={removed}")
            if changed:
                items = []
                for c in changed:
                    key = c.get("key", "?")
                    b = _extract_value(c.get("before"))
                    a = _extract_value(c.get("after"))
                    items.append(f"{key} {b}->{a}")
                detail.append("changed=" + items)
            if detail:
                parts.append("preferences " + "; ".join(detail))
        if "state_change" in categories:
            cs = diff.get("current_state") or {}
            added = list((cs.get("added") or {}).keys())
            removed = list((cs.get("removed") or {}).keys())
            changed = list((cs.get("changed") or {}).keys())
            detail: List[str] = []
            if added:
                detail.append(f"added={added}")
            if removed:
                detail.append(f"removed={removed}")
            if changed:
                detail.append(f"changed={changed}")
            if detail:
                parts.append("current_state " + "; ".join(detail))
        if "entry_added" in categories or "entry_removed" in categories:
            entries = diff.get("entries") or {}
            added_n = len(entries.get("added") or [])
            removed_n = len(entries.get("removed") or [])
            parts.append(
                f"entries +{added_n}/-{removed_n}"
            )
        if not parts:
            return "SelfModel recorded a version update."
        return "SelfModel changes: " + "; ".join(parts)

    def _extract_related_values(
        self, diff: Dict[str, Any], categories: List[str],
    ) -> List[str]:
        """基于 diff 内容,启发式映射到核心价值。"""
        found: List[str] = []
        if not isinstance(diff, dict):
            return found
        # 检查 identity
        identity = diff.get("identity") or {}
        for sec in (identity.get("added"), identity.get("changed")):
            if isinstance(sec, dict):
                for k in sec.keys():
                    for v in _lookup_values(_norm_key(k)):
                        if v not in found:
                            found.append(v)
        # 检查 stable_traits
        st = diff.get("stable_traits") or {}
        for lst in (st.get("added"), st.get("changed")):
            for item in lst or []:
                name = _extract_name(item)
                for v in _lookup_values(name):
                    if v not in found:
                        found.append(v)
        # 检查 preferences
        pref = diff.get("preferences") or {}
        for lst in (pref.get("added"), pref.get("changed")):
            for item in lst or []:
                name = _extract_name(item)
                for v in _lookup_values(name):
                    if v not in found:
                        found.append(v)
        # 检查 core_values
        cv = diff.get("core_values") or {}
        for lst in (cv.get("added"), cv.get("changed")):
            for item in lst or []:
                name = _extract_name(item)
                for v in _lookup_values(name):
                    if v not in found:
                        found.append(v)
        return found

    def _build_interpretation(
        self,
        kind: str,
        diff: Dict[str, Any],
        related_values: List[str],
        categories: List[str],
    ) -> str:
        """构造主观理解(模板化,evidence-based)。"""
        if kind == ReflectionKind.INITIAL.value:
            return (
                "Initial self-model established. Future reflections will be "
                "based on observed changes over time."
            )
        if kind == ReflectionKind.IDENTITY_DRIFT.value:
            base = (
                "Identity-level changes detected. This kind of shift is "
                "treated as a high-priority signal because identity anchors "
                "long-term continuity."
            )
        elif kind == ReflectionKind.VALUE_SHIFT.value:
            base = (
                "Core value change observed. Such shifts may require "
                "downstream GrowthProposal review before consolidation."
            )
        elif kind == ReflectionKind.TRAIT_TREND.value:
            base = (
                "Stable trait trend observed. This may reflect accumulated "
                "interaction patterns rather than a single event."
            )
        elif kind == ReflectionKind.PREFERENCE.value:
            base = (
                "Preference consolidation observed. This reflects an emerging "
                "tendency rather than a fixed rule."
            )
        elif kind == ReflectionKind.STATE_NOTE.value:
            base = (
                "Current state changed. State-level changes are ephemeral and "
                "do not by themselves indicate identity drift."
            )
        elif kind == ReflectionKind.GROWTH_NOTE.value:
            base = (
                "New growth or reflection entry recorded. Such entries are "
                "candidates for future narrative consolidation."
            )
        else:
            base = "SelfModel recorded a change."
        if related_values:
            base += f" Related to: {', '.join(related_values)}."
        return base

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def reflect_count(self) -> int:
        return self._reflect_count

    @property
    def last_reflection(self) -> Optional[ReflectionRecord]:
        return self._last_reflection

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "schema_version": REFLECTION_ENGINE_SCHEMA_VERSION,
            "reflect_count": self._reflect_count,
            "skip_silent": self._skip_silent,
            "last_error": self._last_error,
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "schema_version": REFLECTION_ENGINE_SCHEMA_VERSION,
            "reflect_count": self._reflect_count,
            "skip_silent": self._skip_silent,
            "last_error": self._last_error,
        }


def _identity_from_diff(diff: Dict[str, Any]) -> str:
    if not isinstance(diff, dict):
        return ""
    return str(diff.get("identity_id", "") or "")


# ============================================================
# Phase 4.7: SelfModelReflectionEngine —— 一致性验证协调器
# ============================================================


SELF_MODEL_REFLECTION_ENGINE_SCHEMA_VERSION = "1.0"


def _avg(values: List[float]) -> float:
    if not values:
        return 0.0
    try:
        return sum(values) / len(values)
    except Exception:  # noqa: BLE001
        return 0.0


def _max(values: List[float]) -> float:
    if not values:
        return 0.0
    try:
        return max(values)
    except Exception:  # noqa: BLE001
        return 0.0


class SelfModelReflectionEngine:
    """SelfModel 反思 + 一致性验证协调器(Phase 4.7 / v1.0)。

    职责:
    - 协调 ContradictionDetector + ConsistencyChecker
    - 把两类检测结果汇总为 ReflectionRecord
    - 不修改 snapshot,只产生 reflection 输出
    - 异常隔离:失败返回空 ReflectionRecord(标记 is_consistent=True 兜底)

    入口:
    - reflect(old_snapshot, new_snapshot, evolution_history=None) -> ReflectionRecord

    字段:
    - _detector:               ContradictionDetector
    - _checker:                ConsistencyChecker
    - _reflect_count:          int
    - _last_reflection:        Optional[ReflectionRecord]
    - _last_error:             Optional[str]
    """

    def __init__(
        self,
        detector: Optional[ContradictionDetector] = None,
        checker: Optional[ConsistencyChecker] = None,
    ) -> None:
        self._detector = detector or ContradictionDetector()
        self._checker = checker or ConsistencyChecker()
        self._reflect_count: int = 0
        self._last_reflection: Optional[ReflectionRecord] = None
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def reflect(
        self,
        old_snapshot: Any,
        new_snapshot: Any,
        evolution_history: Optional[Sequence[Any]] = None,
        source: str = "runtime",
    ) -> ReflectionRecord:
        """协调两个 detector,生成 ReflectionRecord。

        流程:
        1. ContradictionDetector.detect(old, new) -> List[ContradictionRecord]
        2. ConsistencyChecker.check(new, history) -> ConsistencyReport
        3. 汇总成 ReflectionRecord(reflection_type / affected_fields /
           conflicts / severity / evidence_ids / confidence)

        Returns:
            ReflectionRecord(失败时返回空 record)。
        """
        try:
            self._last_error = None
            if old_snapshot is None and new_snapshot is None:
                self._last_error = "both_snapshots_missing"
                return self._empty_record(source=source)

            # 1. 冲突检测
            try:
                contradictions = self._detector.detect(old_snapshot, new_snapshot)
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"detector_failed: {exc}"
                logger.warning("ContradictionDetector 失败: %s", exc)
                contradictions = []

            # 2. 一致性验证
            try:
                report = self._checker.check(new_snapshot, evolution_history)
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"checker_failed: {exc}"
                logger.warning("ConsistencyChecker 失败: %s", exc)
                report = ConsistencyReport(score=0.5, is_consistent=True)

            # 3. 汇总
            record = self._synthesize(
                contradictions=contradictions,
                report=report,
                old_snapshot=old_snapshot,
                new_snapshot=new_snapshot,
                source=source,
            )
            self._reflect_count += 1
            self._last_reflection = record
            return record
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"reflect_failed: {exc}"
            logger.warning("SelfModelReflectionEngine.reflect 失败: %s", exc)
            return self._empty_record(source=source)

    # --------------------------------------------------------
    # 汇总逻辑
    # --------------------------------------------------------
    def _synthesize(
        self,
        contradictions: List[ContradictionRecord],
        report: ConsistencyReport,
        old_snapshot: Any,
        new_snapshot: Any,
        source: str,
    ) -> ReflectionRecord:
        # 1. 决定 reflection_type
        if contradictions:
            reflection_type = ReflectionType.CONTRADICTION.value
        elif report.drift_score > 0:
            reflection_type = ReflectionType.DRIFT.value
        elif report.has_issues():
            reflection_type = ReflectionType.VALIDATION.value
        elif report.is_consistent:
            reflection_type = ReflectionType.CONSISTENT.value
        else:
            reflection_type = ReflectionType.VALIDATION.value

        # 2. 决定 severity
        severities = [c.severity for c in contradictions if c.is_real_conflict()]
        if severities:
            severity = _max(severities)
        else:
            # 用 drift_score 作为兜底
            severity = report.drift_score

        # 3. affected_fields
        affected: List[str] = []
        seen = set()
        for c in contradictions:
            if c.field_name and c.field_name not in seen:
                affected.append(c.field_name)
                seen.add(c.field_name)
        if report.issues:
            for issue in report.issues:
                code = str(_safe_get(issue, "code", default="") or "")
                if code and code not in seen:
                    affected.append(f"issue.{code}")
                    seen.add(code)

        # 4. conflicts dict
        conflicts = [c.to_dict() for c in contradictions if c.is_real_conflict()]
        # 5. evidence_ids: 用 reflection_id / evolution record ids
        evidence_ids: List[str] = []
        if hasattr(old_snapshot, "identity_id") and old_snapshot.identity_id:
            evidence_ids.append(f"snapshot:{old_snapshot.identity_id}")
        if hasattr(new_snapshot, "identity_id") and new_snapshot.identity_id:
            evidence_ids.append(f"snapshot:{new_snapshot.identity_id}")
        if hasattr(new_snapshot, "version"):
            try:
                evidence_ids.append(f"version:{int(new_snapshot.version)}")
            except (TypeError, ValueError):
                pass

        # 6. confidence:综合 contradictions confidence avg + report score
        confidences = [c.confidence for c in contradictions if c.is_real_conflict()]
        if confidences:
            base_conf = _avg(confidences)
        else:
            base_conf = 0.6 if report.is_consistent else 0.5
        # 如果有报告 score,融合一下
        if report and report.score is not None:
            try:
                base_conf = (base_conf + float(report.score)) / 2
            except (TypeError, ValueError):
                pass

        # 7. observation 文本
        observation = self._build_observation(contradictions, report)

        # 8. affected identity
        identity_id = ""
        try:
            if new_snapshot is not None:
                identity_id = str(getattr(new_snapshot, "identity_id", "") or "")
            elif old_snapshot is not None:
                identity_id = str(getattr(old_snapshot, "identity_id", "") or "")
        except Exception:  # noqa: BLE001
            identity_id = ""

        return ReflectionRecord(
            identity_id=identity_id,
            observation=observation,
            confidence=base_conf,
            source=source,
            # Phase 4.7 扩展
            reflection_type=reflection_type,
            affected_fields=affected,
            conflicts=conflicts,
            severity=severity,
            evidence_ids=evidence_ids,
            metadata={
                "is_consistent": report.is_consistent,
                "consistency_score": report.score,
                "drift_score": report.drift_score,
                "issue_count": report.issue_count(),
                "warning_count": report.warning_count(),
                "contradiction_count": len(conflicts),
                "checker_schema": CONSISTENCY_CHECKER_SCHEMA_VERSION,
                "detector_schema": CONTRADICTION_DETECTOR_SCHEMA_VERSION,
                "engine_schema": SELF_MODEL_REFLECTION_ENGINE_SCHEMA_VERSION,
            },
        )

    def _build_observation(
        self,
        contradictions: List[ContradictionRecord],
        report: ConsistencyReport,
    ) -> str:
        parts: List[str] = []
        if contradictions:
            real = [c for c in contradictions if c.is_real_conflict()]
            parts.append(
                f"Detected {len(real)} field contradiction(s)."
            )
        if report.has_issues():
            parts.append(
                f"Consistency issues: {report.issue_count()}."
            )
        if report.warning_count() > 0:
            parts.append(f"Warnings: {report.warning_count()}.")
        if report.drift_score > 0:
            parts.append(f"Drift score: {report.drift_score:.3f}.")
        if not parts:
            parts.append(
                f"SelfModel consistent (score={report.score:.3f})."
            )
        return " ".join(parts)

    def _empty_record(self, source: str = "runtime") -> ReflectionRecord:
        return ReflectionRecord(
            source=source,
            reflection_type=ReflectionType.UNKNOWN.value,
            observation="Reflection skipped due to error.",
            confidence=0.0,
            severity=0.0,
            metadata={"error": self._last_error or "unknown"},
        )

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def reflect_count(self) -> int:
        return self._reflect_count

    @property
    def last_reflection(self) -> Optional[ReflectionRecord]:
        return self._last_reflection

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def detector(self) -> ContradictionDetector:
        return self._detector

    @property
    def checker(self) -> ConsistencyChecker:
        return self._checker

    def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "schema_version": SELF_MODEL_REFLECTION_ENGINE_SCHEMA_VERSION,
            "reflect_count": self._reflect_count,
            "last_error": self._last_error,
            "detector_health": self._detector.health_check(),
            "checker_health": self._checker.health_check(),
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "schema_version": SELF_MODEL_REFLECTION_ENGINE_SCHEMA_VERSION,
            "reflect_count": self._reflect_count,
            "last_error": self._last_error,
        }


def _safe_get(obj: Any, *path: str, default: Any = None) -> Any:
    cur: Any = obj
    for p in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(p, default)
        else:
            cur = getattr(cur, p, default)
    return cur


__all__ = [
    "ReflectionEngine",
    "REFLECTION_ENGINE_SCHEMA_VERSION",
    # Phase 4.7
    "SelfModelReflectionEngine",
    "SELF_MODEL_REFLECTION_ENGINE_SCHEMA_VERSION",
]
