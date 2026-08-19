# -*- coding: utf-8 -*-
"""
src/runtime/self_model/reflection/contradiction_detector.py

Phase 4.7: ContradictionDetector —— SelfModel 字段冲突检测器

职责:
- 检测 SelfModelSnapshot 与新变化之间的不一致
- 不禁止变化,只产生检测结果
- 不修改 snapshot
- 不调用 LLM
- 不依赖 src.personality.*

约束:
- 不 import openai / qwen / llava / anthropic / google.generativeai
- 不 import src.personality.*
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- 异常隔离:任何 detect 失败返回空列表

检测类型 (ConflictType):
- IDENTITY_CONFLICT      identity_id / creator_origin / core_identity
- VALUE_CONFLICT         核心价值变化
- TRAIT_CONFLICT         stable_traits 明显变化
- PREFERENCE_CONFLICT    preferences 出现明显相反倾向
- BEHAVIOR_CONFLICT      behavior_tendencies 变化冲突
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.self_model.reflection.reflection_record import (
    ConflictType,
    _clamp_confidence,
    _clamp_severity,
)


logger = logging.getLogger(__name__)


CONTRADICTION_DETECTOR_SCHEMA_VERSION = "1.0"

# 阈值配置
DEFAULT_VALUE_CONFLICT_THRESHOLD = 0.6  # 价值变化量阈值(0~1)
DEFAULT_TRAIT_CONFLICT_THRESHOLD = 0.5  # trait 变化量阈值
DEFAULT_PREFERENCE_OPPOSITE_THRESHOLD = 0.6  # 偏好相反性阈值
DEFAULT_BEHAVIOR_CONFLICT_THRESHOLD = 0.5  # 行为冲突阈值

# identity 不可变字段
IMMUTABLE_IDENTITY_FIELDS = frozenset({
    "identity_id",
    "creator_origin",
    "core_identity",
})

# 语义相反词表(用于检测 PREFERENCE_CONFLICT / BEHAVIOR_CONFLICT)
_OPPOSITE_PAIRS: List[List[str]] = [
    ["gentle", "aggressive"],
    ["calm", "agitated"],
    ["kind", "harsh"],
    ["patient", "impatient"],
    ["open", "closed"],
    ["honest", "deceptive"],
    ["caring", "indifferent"],
    ["curious", "indifferent"],
    ["quiet", "loud"],
    ["soft", "harsh"],
    ["warm", "cold"],
    ["mindful", "reckless"],
]


# ============================================================
# ContradictionRecord
# ============================================================
@dataclass
class ContradictionRecord:
    """字段冲突检测结果 —— Phase 4.7 / v1.0。

    字段:
    - field_name:    str                  # 冲突字段名
    - old_value:     Any                  # 旧值(深拷贝,可能为 None)
    - new_value:     Any                  # 新值
    - conflict_type: str                  # ConflictType
    - severity:      float                # 严重程度 [0.0, 1.0]
    - description:   str                  # 描述
    - confidence:    float                # 置信度 [0.0, 1.0]
    """

    field_name: str = ""
    old_value: Any = None
    new_value: Any = None
    conflict_type: str = ConflictType.NONE.value
    severity: float = 0.0
    description: str = ""
    confidence: float = 0.5

    def __post_init__(self) -> None:
        self.field_name = str(self.field_name or "")
        # 校验 conflict_type
        valid_types = {t.value for t in ConflictType}
        if self.conflict_type not in valid_types:
            self.conflict_type = ConflictType.NONE.value
        # clamp
        self.severity = _clamp_severity(self.severity)
        self.confidence = _clamp_confidence(self.confidence)
        # description 截断
        try:
            self.description = str(self.description or "")
        except Exception:  # noqa: BLE001
            self.description = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_name": self.field_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "conflict_type": self.conflict_type,
            "severity": self.severity,
            "description": self.description,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]] = None) -> "ContradictionRecord":
        d = dict(data or {})
        return cls(
            field_name=str(d.get("field_name", "") or ""),
            old_value=d.get("old_value"),
            new_value=d.get("new_value"),
            conflict_type=str(d.get("conflict_type", ConflictType.NONE.value)),
            severity=_clamp_severity(d.get("severity", 0.0)),
            description=str(d.get("description", "") or ""),
            confidence=_clamp_confidence(d.get("confidence", 0.5)),
        )

    def is_real_conflict(self) -> bool:
        """是否为真实冲突(conflict_type != NONE 且 severity > 0)。"""
        return (
            self.conflict_type != ConflictType.NONE.value
            and self.severity > 0.0
        )


# ============================================================
# 工具函数
# ============================================================
def _safe_get_field(obj: Any, *path: str, default: Any = None) -> Any:
    """从 obj(支持 dict / dataclass / 普通对象)按路径安全读取字段。"""
    cur: Any = obj
    for p in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(p, default)
        else:
            cur = getattr(cur, p, default)
    return cur


def _extract_items_from_list_field(obj: Any, *path: str) -> List[Dict[str, Any]]:
    """从 obj 中提取 list 类型的字段(可能来自 dict / dataclass / 普通对象)。"""
    raw = _safe_get_field(obj, *path, default=[])
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
        else:
            # dataclass 或普通对象:尝试 to_dict()
            if hasattr(item, "to_dict"):
                try:
                    d = item.to_dict()
                    if isinstance(d, dict):
                        out.append(d)
                        continue
                except Exception:  # noqa: BLE001
                    pass
            # 普通对象:用 __dict__
            try:
                d = dict(item.__dict__)
                out.append(d)
            except Exception:  # noqa: BLE001
                # 实在不行,转字符串
                out.append({"value": str(item)})
    return out


def _extract_name(item: Any) -> str:
    """从 dict / 对象中提取 'name' 字段。"""
    if isinstance(item, dict):
        return str(item.get("name") or item.get("key") or item.get("label") or "")
    return str(getattr(item, "name", "") or getattr(item, "key", "") or "")


def _extract_value(item: Any) -> Any:
    """从 dict / 对象中提取 'value' 字段。"""
    if isinstance(item, dict):
        return item.get("value")
    return getattr(item, "value", None)


def _norm_str(s: Any) -> str:
    """把 value 标准化为字符串。"""
    if s is None:
        return ""
    try:
        return str(s).strip().lower()
    except Exception:  # noqa: BLE001
        return ""


def _is_opposite(a: str, b: str) -> bool:
    """判断两个值是否在 _OPPOSITE_PAIRS 中语义相反。"""
    if not a or not b:
        return False
    a_n = _norm_str(a)
    b_n = _norm_str(b)
    if not a_n or not b_n:
        return False
    if a_n == b_n:
        return False
    for pair in _OPPOSITE_PAIRS:
        if (a_n in pair and b_n in pair) and a_n != b_n:
            return True
    return False


def _numeric_distance(a: Any, b: Any) -> Optional[float]:
    """如果 a / b 都是数值,返回 |a - b|,否则 None。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        try:
            return abs(float(a) - float(b))
        except (TypeError, ValueError):
            return None
    return None


# ============================================================
# ContradictionDetector
# ============================================================
class ContradictionDetector:
    """SelfModel 字段冲突检测器(Phase 4.7 / v1.0)。

    字段:
    - _value_threshold:   float          # VALUE_CONFLICT 阈值
    - _trait_threshold:   float          # TRAIT_CONFLICT 阈值
    - _preference_threshold: float       # PREFERENCE_CONFLICT 阈值
    - _behavior_threshold: float         # BEHAVIOR_CONFLICT 阈值
    - _detect_count:      int
    - _last_error:        Optional[str]

    方法:
    - detect(old_snapshot, new_snapshot) -> List[ContradictionRecord]
    - health_check()
    - describe()
    """

    def __init__(
        self,
        value_threshold: float = DEFAULT_VALUE_CONFLICT_THRESHOLD,
        trait_threshold: float = DEFAULT_TRAIT_CONFLICT_THRESHOLD,
        preference_threshold: float = DEFAULT_PREFERENCE_OPPOSITE_THRESHOLD,
        behavior_threshold: float = DEFAULT_BEHAVIOR_CONFLICT_THRESHOLD,
    ) -> None:
        self._value_threshold = float(value_threshold)
        self._trait_threshold = float(trait_threshold)
        self._preference_threshold = float(preference_threshold)
        self._behavior_threshold = float(behavior_threshold)
        self._detect_count: int = 0
        self._last_error: Optional[str] = None
        self._last_contradictions: List[ContradictionRecord] = []

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def detect(
        self,
        old_snapshot: Any,
        new_snapshot: Any,
    ) -> List[ContradictionRecord]:
        """检测 old_snapshot -> new_snapshot 之间的字段冲突。

        Returns:
            List[ContradictionRecord],可能为空列表。
       异常隔离:失败返回空列表。
        """
        try:
            self._last_error = None
            if old_snapshot is None or new_snapshot is None:
                self._last_error = "missing_snapshot"
                return []
            if old_snapshot is new_snapshot:
                # 同一个对象无变化
                return []

            # 深拷贝避免污染
            old_copy = copy.deepcopy(old_snapshot)
            new_copy = copy.deepcopy(new_snapshot)

            contradictions: List[ContradictionRecord] = []

            # 1. identity_id 不变检测
            contradictions.extend(self._detect_identity_conflicts(old_copy, new_copy))
            # 2. 核心价值冲突
            contradictions.extend(self._detect_value_conflicts(old_copy, new_copy))
            # 3. 稳定特质冲突
            contradictions.extend(self._detect_trait_conflicts(old_copy, new_copy))
            # 4. 偏好冲突
            contradictions.extend(self._detect_preference_conflicts(old_copy, new_copy))
            # 5. 行为倾向冲突
            contradictions.extend(self._detect_behavior_conflicts(old_copy, new_copy))

            self._detect_count += 1
            self._last_contradictions = contradictions
            return contradictions
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"detect_failed: {exc}"
            logger.warning("ContradictionDetector.detect 失败: %s", exc)
            return []

    # --------------------------------------------------------
    # 各类冲突检测
    # --------------------------------------------------------
    def _detect_identity_conflicts(
        self, old: Any, new: Any,
    ) -> List[ContradictionRecord]:
        out: List[ContradictionRecord] = []
        for fname in IMMUTABLE_IDENTITY_FIELDS:
            # 1) 尝试从嵌套 identity 字典中读取(如 identity.identity_id)
            old_v = _safe_get_field(old, "identity", fname)
            new_v = _safe_get_field(new, "identity", fname)
            # 2) 若未找到,尝试从顶层字段读取(如 identity_id, creator_origin, core_identity)
            if fname == "identity_id":
                if old_v is None:
                    old_v = getattr(old, "identity_id", None) if not isinstance(old, dict) else old.get("identity_id")
                if new_v is None:
                    new_v = getattr(new, "identity_id", None) if not isinstance(new, dict) else new.get("identity_id")
            elif fname == "creator_origin":
                if old_v is None:
                    old_v = getattr(old, "creator_origin", None) if not isinstance(old, dict) else old.get("creator_origin")
                if new_v is None:
                    new_v = getattr(new, "creator_origin", None) if not isinstance(new, dict) else new.get("creator_origin")
            elif fname == "core_identity":
                # core_identity 通常在 identity dict 中
                pass
            # 只检测明确出现的变化(旧值存在且不等于新值)
            if old_v is None and new_v is None:
                continue
            if old_v == new_v:
                continue
            # identity_id 完全相同才合法
            severity = 1.0
            description = (
                f"Identity field '{fname}' changed from "
                f"{old_v!r} to {new_v!r}. This is an identity-level change."
            )
            out.append(
                ContradictionRecord(
                    field_name=f"identity.{fname}",
                    old_value=old_v,
                    new_value=new_v,
                    conflict_type=ConflictType.IDENTITY_CONFLICT.value,
                    severity=severity,
                    description=description,
                    confidence=0.95,
                )
            )
        return out

    def _detect_value_conflicts(
        self, old: Any, new: Any,
    ) -> List[ContradictionRecord]:
        out: List[ContradictionRecord] = []
        old_values = _extract_items_from_list_field(old, "core_values")
        new_values = _extract_items_from_list_field(new, "core_values")
        old_map = {_extract_name(v): v for v in old_values}
        new_map = {_extract_name(v): v for v in new_values}

        # 1. 名称级别的删除/添加(移除核心价值通常是冲突)
        for name, v in old_map.items():
            if not name:
                continue
            if name in new_map:
                continue
            out.append(
                ContradictionRecord(
                    field_name=f"core_values.{name}",
                    old_value=v,
                    new_value=None,
                    conflict_type=ConflictType.VALUE_CONFLICT.value,
                    severity=0.7,
                    description=(
                        f"Core value '{name}' was removed. "
                        f"Core values are usually long-term anchors."
                    ),
                    confidence=0.8,
                )
            )
        # 2. 同一核心价值的 weight 显著变化
        for name in old_map.keys() & new_map.keys():
            old_v = old_map[name]
            new_v = new_map[name]
            old_w = _numeric_distance(
                _safe_get_field(old_v, "weight", default=None),
                _safe_get_field(new_v, "weight", default=None),
            )
            # 显式计算 weight 距离
            old_w_raw = _safe_get_field(old_v, "weight", default=None)
            new_w_raw = _safe_get_field(new_v, "weight", default=None)
            if old_w_raw is not None and new_w_raw is not None:
                dist = _numeric_distance(old_w_raw, new_w_raw)
                if dist is not None and dist >= self._value_threshold:
                    out.append(
                        ContradictionRecord(
                            field_name=f"core_values.{name}.weight",
                            old_value=old_w_raw,
                            new_value=new_w_raw,
                            conflict_type=ConflictType.VALUE_CONFLICT.value,
                            severity=min(1.0, dist),
                            description=(
                                f"Core value '{name}' weight changed "
                                f"significantly ({old_w_raw} -> {new_w_raw})."
                            ),
                            confidence=0.7,
                        )
                    )
        return out

    def _detect_trait_conflicts(
        self, old: Any, new: Any,
    ) -> List[ContradictionRecord]:
        out: List[ContradictionRecord] = []
        old_traits = _extract_items_from_list_field(old, "stable_traits")
        new_traits = _extract_items_from_list_field(new, "stable_traits")
        old_map = {_extract_name(t): t for t in old_traits}
        new_map = {_extract_name(t): t for t in new_traits}

        # 同名 trait 数值大幅变化
        for name in old_map.keys() & new_map.keys():
            old_v = _safe_get_field(old_map[name], "value", default=None)
            new_v = _safe_get_field(new_map[name], "value", default=None)
            dist = _numeric_distance(old_v, new_v)
            if dist is not None and dist >= self._trait_threshold:
                out.append(
                    ContradictionRecord(
                        field_name=f"stable_traits.{name}",
                        old_value=old_v,
                        new_value=new_v,
                        conflict_type=ConflictType.TRAIT_CONFLICT.value,
                        severity=min(1.0, dist),
                        description=(
                            f"Stable trait '{name}' changed significantly "
                            f"({old_v} -> {new_v}). Stable traits should be stable."
                        ),
                        confidence=0.75,
                    )
                )

        # 同名 trait 但值字符串相反
        for name in old_map.keys() & new_map.keys():
            old_v = _safe_get_field(old_map[name], "value", default=None)
            new_v = _safe_get_field(new_map[name], "value", default=None)
            if old_v is None or new_v is None:
                continue
            if isinstance(old_v, (int, float)) or isinstance(new_v, (int, float)):
                continue
            if _is_opposite(str(old_v), str(new_v)):
                out.append(
                    ContradictionRecord(
                        field_name=f"stable_traits.{name}",
                        old_value=old_v,
                        new_value=new_v,
                        conflict_type=ConflictType.TRAIT_CONFLICT.value,
                        severity=0.8,
                        description=(
                            f"Stable trait '{name}' flipped to an opposite value "
                            f"({old_v!r} -> {new_v!r})."
                        ),
                        confidence=0.85,
                    )
                )
        return out

    def _detect_preference_conflicts(
        self, old: Any, new: Any,
    ) -> List[ContradictionRecord]:
        out: List[ContradictionRecord] = []
        old_prefs = _extract_items_from_list_field(old, "preferences")
        new_prefs = _extract_items_from_list_field(new, "preferences")
        old_map = {_extract_name(p): p for p in old_prefs}
        new_map = {_extract_name(p): p for p in new_prefs}

        # 同名 preference 但倾向相反
        for name in old_map.keys() & new_map.keys():
            old_v = _extract_value(old_map[name])
            new_v = _extract_value(new_map[name])
            if old_v is None or new_v is None:
                continue
            if _is_opposite(str(old_v), str(new_v)):
                out.append(
                    ContradictionRecord(
                        field_name=f"preferences.{name}",
                        old_value=old_v,
                        new_value=new_v,
                        conflict_type=ConflictType.PREFERENCE_CONFLICT.value,
                        severity=0.7,
                        description=(
                            f"Preference '{name}' flipped to an opposite "
                            f"({old_v!r} -> {new_v!r})."
                        ),
                        confidence=0.8,
                    )
                )
        # 移除偏好
        for name, p in old_map.items():
            if not name:
                continue
            if name in new_map:
                continue
            out.append(
                ContradictionRecord(
                    field_name=f"preferences.{name}",
                    old_value=p,
                    new_value=None,
                    conflict_type=ConflictType.PREFERENCE_CONFLICT.value,
                    severity=0.4,
                    description=(
                        f"Preference '{name}' was removed."
                    ),
                    confidence=0.6,
                )
            )
        return out

    def _detect_behavior_conflicts(
        self, old: Any, new: Any,
    ) -> List[ContradictionRecord]:
        """检测 behavior_tendencies 字段(可能存放在 current_state 或独立字段)。"""
        out: List[ContradictionRecord] = []
        # 1. 检查 current_state.behavior_tendencies
        old_bt = _safe_get_field(old, "current_state", "behavior_tendencies", default={})
        new_bt = _safe_get_field(new, "current_state", "behavior_tendencies", default={})
        # 2. 也可能直接在 current_state 中作为 key
        if not old_bt or not new_bt:
            old_bt2 = _safe_get_field(old, "current_state", default={}) or {}
            new_bt2 = _safe_get_field(new, "current_state", default={}) or {}
            if not old_bt:
                old_bt = old_bt2.get("behavior_tendencies") if isinstance(old_bt2, dict) else None
            if not new_bt:
                new_bt = new_bt2.get("behavior_tendencies") if isinstance(new_bt2, dict) else None
        if not isinstance(old_bt, dict) or not isinstance(new_bt, dict):
            return out

        for k in old_bt.keys() & new_bt.keys():
            old_v = old_bt.get(k)
            new_v = new_bt.get(k)
            if old_v == new_v:
                continue
            dist = _numeric_distance(old_v, new_v)
            if dist is not None and dist >= self._behavior_threshold:
                out.append(
                    ContradictionRecord(
                        field_name=f"behavior_tendencies.{k}",
                        old_value=old_v,
                        new_value=new_v,
                        conflict_type=ConflictType.BEHAVIOR_CONFLICT.value,
                        severity=min(1.0, dist),
                        description=(
                            f"Behavior tendency '{k}' changed significantly "
                            f"({old_v} -> {new_v})."
                        ),
                        confidence=0.7,
                    )
                )
            elif _is_opposite(str(old_v), str(new_v)):
                out.append(
                    ContradictionRecord(
                        field_name=f"behavior_tendencies.{k}",
                        old_value=old_v,
                        new_value=new_v,
                        conflict_type=ConflictType.BEHAVIOR_CONFLICT.value,
                        severity=0.8,
                        description=(
                            f"Behavior tendency '{k}' flipped to opposite "
                            f"({old_v!r} -> {new_v!r})."
                        ),
                        confidence=0.8,
                    )
                )
        return out

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def detect_count(self) -> int:
        return self._detect_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def last_contradictions(self) -> List[ContradictionRecord]:
        return list(self._last_contradictions or [])

    def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "schema_version": CONTRADICTION_DETECTOR_SCHEMA_VERSION,
            "detect_count": self._detect_count,
            "value_threshold": self._value_threshold,
            "trait_threshold": self._trait_threshold,
            "preference_threshold": self._preference_threshold,
            "behavior_threshold": self._behavior_threshold,
            "last_error": self._last_error,
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "schema_version": CONTRADICTION_DETECTOR_SCHEMA_VERSION,
            "detect_count": self._detect_count,
            "value_threshold": self._value_threshold,
            "trait_threshold": self._trait_threshold,
            "preference_threshold": self._preference_threshold,
            "behavior_threshold": self._behavior_threshold,
            "last_error": self._last_error,
        }


__all__ = [
    "ContradictionRecord",
    "ContradictionDetector",
    "CONTRADICTION_DETECTOR_SCHEMA_VERSION",
    "DEFAULT_VALUE_CONFLICT_THRESHOLD",
    "DEFAULT_TRAIT_CONFLICT_THRESHOLD",
    "DEFAULT_PREFERENCE_OPPOSITE_THRESHOLD",
    "DEFAULT_BEHAVIOR_CONFLICT_THRESHOLD",
    "IMMUTABLE_IDENTITY_FIELDS",
]
