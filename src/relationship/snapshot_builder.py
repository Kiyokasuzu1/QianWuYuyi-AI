# -*- coding: utf-8 -*-
"""
Phase 4.2 — RelationshipSnapshotBuilder

职责：从 v0.6 + v3.5.27 RelationshipState 组装 RelationshipSnapshot。

设计原则：
- 只读：不修改任何 RelationshipState
- 不推理：缺失字段用默认值，不猜测
- 不合并：current.trust 和 long_term.trust 独立
- 兼容 dict 和 object 输入

禁止：
- 修改 v0.6 / v3.5.27 RelationshipState
- 修改 persistence
- 触发 relationship update
- 用 trust × 0.7 推导 bond_strength
- 把 communication_style 标签当关系事实
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from src.contracts.relationship_snapshot import (
    CurrentRelationshipState,
    LongTermRelationshipState,
    RelationshipSnapshot,
    RelationshipSnapshotProvenance,
)


# ============================================================
# 内部辅助：输入解析
# ============================================================

def _to_dict(input_obj: Any) -> Optional[Dict[str, Any]]:
    """将输入对象安全转换为 dict。

    支持：
    - dict → 直接返回
    - 有 to_dict() 方法 → 调用 to_dict()
    - 有 get() 方法 → 调用 get()
    - None → 返回 None
    """
    if input_obj is None:
        return None
    if isinstance(input_obj, dict):
        return input_obj

    # 优先 to_dict()（v3.5.27 dataclass 路径）
    to_dict = getattr(input_obj, "to_dict", None)
    if callable(to_dict):
        try:
            result = to_dict()
            if isinstance(result, dict):
                return result
        except Exception:
            pass

    # 其次 get()（v0.6 dict 包装路径）
    get_method = getattr(input_obj, "get", None)
    if callable(get_method):
        try:
            result = get_method()
            if isinstance(result, dict):
                return result
        except Exception:
            pass

    return None


def _safe_float(data: Dict[str, Any], key: str, default: float = 0.0) -> float:
    """安全提取浮点数，clamp 到 [0, 1]。"""
    val = data.get(key, default)
    if val is None:
        return default
    try:
        return max(0.0, min(1.0, float(val)))
    except (TypeError, ValueError):
        return default


def _safe_str(data: Dict[str, Any], key: str, default: str = "") -> str:
    """安全提取字符串。"""
    val = data.get(key, default)
    if val is None:
        return default
    try:
        return str(val)
    except (TypeError, ValueError):
        return default


def _safe_list(data: Dict[str, Any], key: str) -> list:
    """安全提取列表。"""
    val = data.get(key, [])
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return []


def _detect_version(data: Dict[str, Any]) -> str:
    """从 dict 中检测版本号。"""
    return _safe_str(data, "version", "")


def _detect_source(data: Optional[Dict[str, Any]], *, default: str = "") -> str:
    """根据数据特征推断来源标识。

    检测规则：
    - 有 bond_strength / bond 字段 → v0.6
    - 有 collaboration 字段 → v3.5.27
    - 否则返回 default
    """
    if data is None:
        return default
    if "bond_strength" in data or "bond" in data:
        return "personality.relationship.v0.6"
    if "collaboration" in data:
        return "relationship.v3.5.27"
    return default


# ============================================================
# SnapshotBuilder
# ============================================================

class RelationshipSnapshotBuilder:
    """Phase 4.2: 从 v0.6 + v3.5.27 组装 RelationshipSnapshot。

    使用示例：
        builder = RelationshipSnapshotBuilder()
        snapshot = builder.build(
            user_id="366648462",
            preferred_name="清清",
            current_state=v3_5_27_state,
            long_term_state=v0_6_state,
        )
    """

    def build(
        self,
        *,
        user_id: str = "",
        preferred_name: str = "",
        current_state: Any = None,
        long_term_state: Any = None,
    ) -> RelationshipSnapshot:
        """组装 RelationshipSnapshot。

        Args:
            user_id: 用户 ID
            preferred_name: 对方希望你称呼的名字
            current_state: v3.5.27 RelationshipState (dict / object / None)
            long_term_state: v0.6 RelationshipState (dict / object / None)

        Returns:
            RelationshipSnapshot（frozen dataclass，不可变）
        """
        current_dict = _to_dict(current_state)
        long_term_dict = _to_dict(long_term_state)

        current = self._build_current(current_dict)
        long_term = self._build_long_term(long_term_dict)
        provenance = self._build_provenance(
            current_dict=current_dict,
            long_term_dict=long_term_dict,
        )

        return RelationshipSnapshot(
            user_id=user_id,
            preferred_name=preferred_name,
            current=current,
            long_term=long_term,
            provenance=provenance,
        )

    # ============================================================
    # 子构建
    # ============================================================

    @staticmethod
    def _build_current(data: Optional[Dict[str, Any]]) -> CurrentRelationshipState:
        """从 v3.5.27 dict 构建 CurrentRelationshipState。

        禁止：
        - 引入 bond_strength / shared_history 等 long_term 字段
        - 引入 communication_style 标签
        """
        if data is None:
            return CurrentRelationshipState()

        return CurrentRelationshipState(
            trust=_safe_float(data, "trust", 0.0),
            familiarity=_safe_float(data, "familiarity", 0.0),
            collaboration=_safe_float(data, "collaboration", 0.0),
            interaction_frequency=_safe_float(data, "interaction_frequency", 0.0),
            relationship_stage=_safe_str(data, "relationship_stage", "initial"),
        )

    @staticmethod
    def _build_long_term(data: Optional[Dict[str, Any]]) -> LongTermRelationshipState:
        """从 v0.6 dict 构建 LongTermRelationshipState。

        禁止：
        - 引入 collaboration / interaction_frequency 等 current 字段
        - 用 trust × 0.7 推导 bond_strength
        """
        if data is None:
            return LongTermRelationshipState()

        return LongTermRelationshipState(
            trust=_safe_float(data, "trust", 0.0),
            familiarity=_safe_float(data, "familiarity", 0.0),
            bond_strength=_safe_float(
                data, "bond_strength",
                _safe_float(data, "bond", 0.0)
            ),
            promise_level=_safe_float(data, "promise_level", 0.0),
            shared_history=_safe_float(data, "shared_history", 0.0),
            activity_level=_safe_float(data, "activity_level", 0.0),
            milestones=_safe_list(data, "milestones"),
            important_events=_safe_list(data, "important_events"),
        )

    @staticmethod
    def _build_provenance(
        *,
        current_dict: Optional[Dict[str, Any]],
        long_term_dict: Optional[Dict[str, Any]],
    ) -> RelationshipSnapshotProvenance:
        """构建 provenance 信息。"""
        # 有数据才检测来源；无数据时空字符串表示"没有提供数据"
        current_source = _detect_source(current_dict, default="")
        long_term_source = _detect_source(long_term_dict, default="")

        current_version = _detect_version(current_dict) if current_dict else ""
        long_term_version = _detect_version(long_term_dict) if long_term_dict else ""

        return RelationshipSnapshotProvenance(
            current_source=current_source,
            long_term_source=long_term_source,
            current_version=current_version,
            long_term_version=long_term_version,
            captured_at=datetime.now().isoformat(),
        )