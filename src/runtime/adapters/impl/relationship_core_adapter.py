# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段4:RelationshipCoreAdapter —— 关系核心的只读运行时适配器。

与 Phase C.5 RelationshipRuntimeAdapter 共存(那是 relationship_state 的只读桥,
本适配器是 RelationshipCore 治理数据的只读桥),只新增文件、不改旧文件。

**硬约束(只读)**:
- 绝不写 RelationshipCoreStore(不调 save / 任何写盘动作);
- 绝不写 relationship_state / personality / growth / emotion / 白名单;
- 绝不触发 RelationshipProposal 状态变化(无 approve/activate/reject);
- 所有异常 fail-soft:降级为 context=[] 并继续。

输出:
- ctx.relationship_core_context = 当前会话可见的关系核心 dict 列表
  可见规则:visibility == global(公开约束,跨会话生效)
          或 relationship_id == "yuyi:<当前用户>"(本人关系核心)
- 其他用户绝不会拿到清清的关系核心(relationship_only)内容。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from src.relationship.relationship_core import (
    DEFAULT_VISIBILITY,
    VISIBILITY_GLOBAL,
    RelationshipCore,
)

logger = logging.getLogger(__name__)

SOURCE_NAME = "relationship_core_adapter"
SCHEMA_VERSION = "1.0"

try:
    from src.identity.origin_identity import CREATOR_USER_ID
except Exception:  # noqa: BLE001
    CREATOR_USER_ID = "366648462"

try:
    from src.memory.memory_scope import CREATOR_LABEL
except Exception:  # noqa: BLE001
    CREATOR_LABEL = "清夏铃"


def _user_display(uid: Optional[str]) -> str:
    if uid in (None, ""):
        return "未知用户"
    if str(uid) == str(CREATOR_USER_ID):
        return CREATOR_LABEL
    return f"用户{uid}"


def _to_dicts(cores: Any) -> List[Dict[str, Any]]:
    """RelationshipCore / dict 混合输入统一为 dict 列表。"""
    out: List[Dict[str, Any]] = []
    for core in cores or []:
        if isinstance(core, RelationshipCore):
            out.append(core.to_dict())
        elif isinstance(core, dict):
            out.append(dict(core))
    return out


def _core_allowed(core: Dict[str, Any], uid: Optional[str]) -> bool:
    """可见规则:global 公开约束,或本人关系核心。"""
    if not isinstance(core, dict):
        return False
    if core.get("visibility") == VISIBILITY_GLOBAL:
        return True
    relationship_id = str(core.get("relationship_id") or "")
    return bool(uid) and relationship_id == f"yuyi:{uid}"


def _partner_id(core: Dict[str, Any]) -> str:
    source = str(core.get("source_user_id") or "")
    if source:
        return source
    relationship_id = str(core.get("relationship_id") or "")
    if relationship_id.startswith("yuyi:"):
        return relationship_id.split("yuyi:", 1)[1]
    return ""


def build_relationship_context_block(
    cores: Any,
    current_user_id: Optional[str] = None,
) -> str:
    """构造 [RELATIONSHIP_CONTEXT] 结构化块(供 PromptBuilder 使用)。

    - 内部再次过滤:非当前用户且非 global 的关系核心一律不渲染(纵深防御);
    - 无可见核心时返回空字符串(调用方可跳过该块);
    - 他人 global 核心的约定必须标注「与某人(对羽依的约束)」,禁止无主体呈现。
    """
    try:
        uid = str(current_user_id) if current_user_id is not None else ""
        allowed = [c for c in _to_dicts(cores) if _core_allowed(c, uid)]
        if not allowed:
            return ""

        lines: List[str] = ["[RELATIONSHIP_CONTEXT]"]
        lines.append(f"- 当前交互对象:{_user_display(current_user_id)}")

        lines.append("- 关系事实:")
        fact_lines: List[str] = []
        for core in allowed:
            for event in core.get("events") or []:
                if isinstance(event, dict):
                    fact_lines.append(f"  - {event.get('content') or str(event)}")
                else:
                    fact_lines.append(f"  - {event}")
        lines.extend(fact_lines if fact_lines else ["  - (暂无)"])

        lines.append("- 已审核关系约定:")
        agreement_lines: List[str] = []
        for core in allowed:
            partner = _partner_id(core)
            is_own = bool(uid) and str(core.get("relationship_id") or "") == f"yuyi:{uid}"
            suffix = ""
            if not is_own and partner:
                suffix = f" (与{_user_display(partner)}的长期约定,对羽依的约束)"
            for agreement in core.get("agreements") or []:
                agreement_lines.append(f"  - {agreement}{suffix}")
        lines.extend(agreement_lines if agreement_lines else ["  - (暂无)"])

        lines.append("- 可以影响行为的边界:")
        boundary_lines: List[str] = []
        for core in allowed:
            for boundary in core.get("boundaries") or []:
                boundary_lines.append(f"  - {boundary}")
        lines.extend(boundary_lines if boundary_lines else ["  - (暂无)"])

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.debug("build_relationship_context_block 失败(已降级): %s", exc)
        return ""


class RelationshipCoreAdapter:
    """关系核心只读运行时适配器。

    process_cycle(ctx) 把当前会话可见的关系核心写入
    ctx.relationship_core_context(不存在该属性也可容错)。
    """

    name: str = SOURCE_NAME
    schema_version: str = SCHEMA_VERSION

    def __init__(self, store: Any = None, user_id: str = "yuyi"):
        self._store = store
        self._user_id = str(user_id or "yuyi")
        self._lock = threading.RLock()
        self._read_count = 0
        self._degraded_count = 0
        self._last_error: Optional[str] = None

    def _load_cores(self) -> List[Dict[str, Any]]:
        try:
            if self._store is None:
                return []
            records = self._store.load() if hasattr(self._store, "load") else self._store.list_all()
            return [dict(r) for r in (records or []) if isinstance(r, dict)]
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] load 失败(已降级): %s", SOURCE_NAME, exc)
            self._last_error = repr(exc)
            return []

    def process_cycle(self, ctx: Any) -> Any:
        """把可见关系核心注入 ctx.relationship_core_context(只读,绝不写盘)。"""
        try:
            uid = getattr(ctx, "user_id", None) or self._user_id
            uid = str(uid)
            allowed = [c for c in self._load_cores() if _core_allowed(c, uid)]
            ctx.relationship_core_context = allowed
            with self._lock:
                self._read_count += 1
                if not allowed:
                    self._degraded_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("[%s] process_cycle 异常(已隔离): %s", SOURCE_NAME, exc)
            self._last_error = repr(exc)
            try:
                ctx.relationship_core_context = []
            except Exception:  # noqa: BLE001
                pass
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "store_available": self._store is not None,
                "read_count": int(self._read_count),
                "degraded_count": int(self._degraded_count),
                "user_id": str(self._user_id),
                "last_error": self._last_error,
            }


def create_relationship_core_adapter(
    store: Any = None,
    user_id: str = "yuyi",
) -> RelationshipCoreAdapter:
    """工厂:创建只读关系核心适配器。"""
    return RelationshipCoreAdapter(store=store, user_id=user_id)


__all__ = [
    "SOURCE_NAME",
    "SCHEMA_VERSION",
    "RelationshipCoreAdapter",
    "create_relationship_core_adapter",
    "build_relationship_context_block",
]
