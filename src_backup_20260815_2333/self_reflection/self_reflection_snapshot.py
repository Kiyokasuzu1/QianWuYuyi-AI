"""
Phase 4.0 — R2.6.3: SelfReflectionSnapshot（不可变快照类）

定位：
  SelfReflection 契约冻结后的不可变快照载体。
  只做结构化数据容器 + deepcopy 只读访问器；不推理、不写、不触发 LLM。
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List
import uuid

from src.self_reflection.self_reflection_schema import validate_self_reflection_shape


class SelfReflectionSnapshot:
    """
    SelfReflection 的不可变快照。

    构建后 frozen：任何属性赋值 → AttributeError。
    所有 view 通过 property 返回 deepcopy（外部修改不影响内部）。
    """

    __slots__ = (
        "_reflection_id",
        "_self_model_version",
        "_observed_changes",
        "_interpreted_causes",
        "_identity_alignment",
        "_unresolved_tensions",
        "_current_self_summary",
        "_generated_at",
        "_version",
        "_frozen",
    )

    def __init__(
        self,
        *,
        reflection_id: str,
        self_model_version: int,
        observed_changes: List[Dict[str, Any]],
        interpreted_causes: List[Dict[str, Any]],
        identity_alignment: Dict[str, Any],
        unresolved_tensions: List[Dict[str, Any]],
        current_self_summary: Dict[str, Any],
        generated_at: str,
        version: int,
    ) -> None:
        self._reflection_id = str(reflection_id)
        self._self_model_version = int(self_model_version)
        self._observed_changes = [copy.deepcopy(x) for x in observed_changes]
        self._interpreted_causes = [copy.deepcopy(x) for x in interpreted_causes]
        self._identity_alignment = copy.deepcopy(identity_alignment)
        self._unresolved_tensions = [copy.deepcopy(x) for x in unresolved_tensions]
        self._current_self_summary = copy.deepcopy(current_self_summary)
        self._generated_at = str(generated_at)
        self._version = int(version)
        self._frozen = True

    # ============================================================
    # 只读访问器（deepcopy）
    # ============================================================
    @property
    def reflection_id(self) -> str:
        return self._reflection_id

    @property
    def self_model_version(self) -> int:
        return self._self_model_version

    @property
    def observed_changes(self) -> List[Dict[str, Any]]:
        return [copy.deepcopy(x) for x in self._observed_changes]

    @property
    def interpreted_causes(self) -> List[Dict[str, Any]]:
        return [copy.deepcopy(x) for x in self._interpreted_causes]

    @property
    def identity_alignment(self) -> Dict[str, Any]:
        return copy.deepcopy(self._identity_alignment)

    @property
    def unresolved_tensions(self) -> List[Dict[str, Any]]:
        return [copy.deepcopy(x) for x in self._unresolved_tensions]

    @property
    def current_self_summary(self) -> Dict[str, Any]:
        return copy.deepcopy(self._current_self_summary)

    @property
    def generated_at(self) -> str:
        return self._generated_at

    @property
    def version(self) -> int:
        return self._version

    # ============================================================
    # 序列化
    # ============================================================
    def to_dict(self) -> Dict[str, Any]:
        return {
            "reflection_id": self._reflection_id,
            "self_model_version": self._self_model_version,
            "observed_changes": [copy.deepcopy(x) for x in self._observed_changes],
            "interpreted_causes": [copy.deepcopy(x) for x in self._interpreted_causes],
            "identity_alignment": copy.deepcopy(self._identity_alignment),
            "unresolved_tensions": [copy.deepcopy(x) for x in self._unresolved_tensions],
            "current_self_summary": copy.deepcopy(self._current_self_summary),
            "generated_at": self._generated_at,
            "version": self._version,
        }

    # ============================================================
    # Frozen 保护
    # ============================================================
    def __setattr__(self, name: str, value: Any) -> None:
        # __init__ 期间：_frozen 还未 True，允许设置
        if not hasattr(self, "_frozen") or not self._frozen:
            object.__setattr__(self, name, value)
            return
        raise AttributeError(f"SelfReflectionSnapshot is frozen; cannot set {name!r}")


def build_snapshot(data: Dict[str, Any]) -> SelfReflectionSnapshot:
    """从 SelfReflection dict（已符合 schema）构建不可变 Snapshot。先 validate 再构造。"""
    validate_self_reflection_shape(data)
    return SelfReflectionSnapshot(
        reflection_id=data["reflection_id"],
        self_model_version=data["self_model_version"],
        observed_changes=data["observed_changes"],
        interpreted_causes=data["interpreted_causes"],
        identity_alignment=data["identity_alignment"],
        unresolved_tensions=data["unresolved_tensions"],
        current_self_summary=data["current_self_summary"],
        generated_at=data["generated_at"],
        version=data["version"],
    )
