"""
Phase 4.0 — R2.6.0: SelfModelSnapshot（自我模型快照 冻结形状）

定位：
  SelfModel 的不可变快照。每次 SelfModelBuilder.build() 产出一个 Snapshot。
  Snapshot 是只读的，不持有对 PersonalityState 的引用。

红线：
  - Snapshot 构建后不可修改（frozen）
  - 不存储 PersonalityState 对象引用，只存储值拷贝
  - 不包含 emotion / relationship / memory 数据
  - 可安全传递给 Prompt Builder 或 IdentityContinuity Checker
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import uuid

from src.self_model.self_model_schema import (
    SelfModel,
    FROZEN_TOP_LEVEL_KEYS,
    validate_self_model_shape,
)


# ============================================================
# SelfModelSnapshot: 不可变快照
# ============================================================
class SelfModelSnapshot:
    """
    SelfModel 的不可变快照。

    构建后只读；所有 view 数据是值拷贝，不持有源对象引用。
    """

    __slots__ = (
        "_identity_view",
        "_personality_view",
        "_development_view",
        "_contradiction_view",
        "_capability_view",
        "_version",
        "_generated_at",
        "_snapshot_id",
        "_frozen",
    )

    def __init__(
        self,
        *,
        identity_view: Dict[str, Any],
        personality_view: Dict[str, Any],
        development_view: Dict[str, Any],
        contradiction_view: Dict[str, Any],
        capability_view: Dict[str, Any],
        version: int,
        generated_at: str,
    ) -> None:
        # 深拷贝所有 view（防止外部修改）
        self._identity_view: Dict[str, Any] = dict(identity_view)
        self._personality_view: Dict[str, Any] = dict(personality_view)
        self._development_view: Dict[str, Any] = dict(development_view)
        self._contradiction_view: Dict[str, Any] = dict(contradiction_view)
        self._capability_view: Dict[str, Any] = dict(capability_view)
        self._version: int = int(version)
        self._generated_at: str = str(generated_at)
        self._snapshot_id: str = f"sm_snap_{uuid.uuid4().hex[:12]}"
        self._frozen: bool = True

    # ============================================================
    # 只读访问器
    # ============================================================
    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    @property
    def identity_view(self) -> Dict[str, Any]:
        return copy.deepcopy(self._identity_view)

    @property
    def personality_view(self) -> Dict[str, Any]:
        return copy.deepcopy(self._personality_view)

    @property
    def development_view(self) -> Dict[str, Any]:
        return copy.deepcopy(self._development_view)

    @property
    def contradiction_view(self) -> Dict[str, Any]:
        return copy.deepcopy(self._contradiction_view)

    @property
    def capability_view(self) -> Dict[str, Any]:
        return copy.deepcopy(self._capability_view)

    @property
    def version(self) -> int:
        return self._version

    @property
    def generated_at(self) -> str:
        return self._generated_at

    # ============================================================
    # 转换为 dict（用于序列化 / Prompt Builder）
    # ============================================================
    def to_dict(self) -> Dict[str, Any]:
        """返回完整 SelfModel dict（深拷贝）。"""
        return {
            "identity_view": copy.deepcopy(self._identity_view),
            "personality_view": copy.deepcopy(self._personality_view),
            "development_view": copy.deepcopy(self._development_view),
            "contradiction_view": copy.deepcopy(self._contradiction_view),
            "capability_view": copy.deepcopy(self._capability_view),
            "version": self._version,
            "generated_at": self._generated_at,
            "snapshot_id": self._snapshot_id,
        }

    # ============================================================
    # 冻结保护：阻止属性赋值
    # ============================================================
    def __setattr__(self, name: str, value: Any) -> None:
        # __init__ 期间通过特殊标记允许设置
        if not hasattr(self, "_frozen") or not self._frozen:
            object.__setattr__(self, name, value)
            return
        # frozen 后任何属性赋值都被拒绝
        raise AttributeError(f"SelfModelSnapshot is frozen; cannot set {name!r}")


# ============================================================
# build_snapshot: 从 SelfModel dict 构建不可变快照
# ============================================================
def build_snapshot(model: Dict[str, Any]) -> SelfModelSnapshot:
    """
    从 SelfModel dict 构建不可变 SelfModelSnapshot。

    会先验证 model 的冻结形状。
    """
    validate_self_model_shape(model)
    return SelfModelSnapshot(
        identity_view=model["identity_view"],
        personality_view=model["personality_view"],
        development_view=model["development_view"],
        contradiction_view=model["contradiction_view"],
        capability_view=model["capability_view"],
        version=model["version"],
        generated_at=model["generated_at"],
    )
