# -*- coding: utf-8 -*-
"""
src/goal/goal_resolver.py

v1.3 Agency Phase 2: GoalResolver(只读 GoalContext)。

职责:
- 读取 GoalStateStore(经治理链审批后由 GoalDrain 写入的 Goal 状态);
- 过滤 status=active;
- 过滤无合法 source_refs 的 Goal(恶意/不完整数据 fail-closed, 不进入 context);
- 返回只读 GoalContext dict 或可直接注入 Prompt 的文本片段。

禁止:
- 修改 / 创建 / 审批 / 生成 Goal;
- 写任何状态文件; 调用 LLM / 网络 / Initiative。

红线:
- Goal 只是理解上下文(关注方向/行为倾向), 不是成长事实, 不进入 Narrative;
- 默认关闭语义由聊天层开关控制(enabled=False 时绝不触碰 GoalState)。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.goal.goal_source_validation import validate_goal_source_refs
from src.goal.goal_state import GOAL_STATUS, GoalStateStore

logger = logging.getLogger(__name__)

DEFAULT_MAX_ACTIVE_GOALS = 3

# GoalContext 结构版本(只读视图, 无状态写入)
GOAL_CONTEXT_SCHEMA_VERSION = "goal_context.1.0"


def resolve_active_goals(
    goal_store: Optional[GoalStateStore] = None,
    *,
    limit: int = DEFAULT_MAX_ACTIVE_GOALS,
    min_sources: int = 1,
) -> Dict[str, Any]:
    """读取 GoalState, 返回只读 GoalContext(不修改任何状态)。

    Returns:
        {
            "active_goals": [
                {
                    "goal_id", "description", "reason",
                    "source_refs", "priority", "confidence",
                    "proposal_id", "created_at"
                }, ...
            ]
        }
    失败/无数据时返回 {"active_goals": []}(fail-soft, 永不抛出)。
    """
    try:
        _limit = max(1, int(limit or DEFAULT_MAX_ACTIVE_GOALS))
    except (TypeError, ValueError):
        _limit = DEFAULT_MAX_ACTIVE_GOALS

    try:
        _store = goal_store if goal_store is not None else GoalStateStore()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[GoalResolver] store 构造失败(已隔离): %s", exc)
        return {"active_goals": []}

    try:
        _actives = _store.list_by_status(GOAL_STATUS["ACTIVE"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[GoalResolver] 读取 GoalState 失败(已隔离): %s", exc)
        return {"active_goals": []}

    _out: List[Dict[str, Any]] = []
    for _rec in _actives or []:
        if not isinstance(_rec, dict):
            continue
        # 来源真实性 fail-closed: 无合法 source_refs 的 Goal 不进入 context
        _src_ok, _src_reason = validate_goal_source_refs(
            _rec.get("source_refs"), min_sources=min_sources,
        )
        if not _src_ok:
            logger.warning(
                "[GoalResolver] 过滤无合法来源的 goal=%s(%s)",
                str(_rec.get("goal_id", "") or ""), _src_reason,
            )
            continue
        try:
            _confidence = round(float(_rec.get("confidence", 0.0) or 0.0), 6)
        except (TypeError, ValueError):
            _confidence = 0.0
        _out.append({
            "goal_id": str(_rec.get("goal_id", "") or ""),
            "description": str(_rec.get("description", "") or ""),
            "reason": str(_rec.get("reason", "") or ""),
            "source_refs": list(_rec.get("source_refs", []) or []),
            "priority": str(_rec.get("priority", "") or ""),
            "confidence": _confidence,
            "proposal_id": str(_rec.get("proposal_id", "") or ""),
            "created_at": str(_rec.get("created_at", "") or ""),
        })

    _out.sort(
        key=lambda g: (str(g.get("created_at", "") or ""), str(g.get("goal_id", "") or "")),
        reverse=True,
    )
    return {
        "active_goals": _out[:_limit],
        "schema_version": GOAL_CONTEXT_SCHEMA_VERSION,
    }


def format_goal_context(goal_context: Optional[Dict[str, Any]]) -> str:
    """把 GoalContext dict 渲染为 Prompt 片段(空/无 goals → 空串)。

    字段全部可追溯: goal_id / source_refs / proposal_id 均来自治理链。
    """
    if not isinstance(goal_context, dict):
        return ""
    _goals = goal_context.get("active_goals") or []
    if not _goals:
        return ""
    _lines = [
        "【当前关注方向】",
        "以下是已通过治理链审批、当前活跃的关注方向。它只是理解上下文,",
        "不改变你的身份与原则, 不必机械复述。",
    ]
    for _g in _goals:
        if not isinstance(_g, dict):
            continue
        _refs = _g.get("source_refs") or []
        _ref_text = ", ".join(
            f"{str(r.get('source_type', '') or '')}:{str(r.get('source_id', '') or '')}"
            for r in _refs
            if isinstance(r, dict)
        )
        _lines.append(
            f"- 关注方向({_g.get('goal_id', '')}): {_g.get('description', '')}"
        )
        if _g.get("reason"):
            _lines.append(f"  缘由: {_g.get('reason', '')}")
        if _ref_text:
            _lines.append(f"  来源: {_ref_text}")
    return "\n".join(_lines)


def resolve_goal_context_text(
    goal_store: Optional[GoalStateStore] = None,
    *,
    enabled: bool = True,
    limit: int = DEFAULT_MAX_ACTIVE_GOALS,
    min_sources: int = 1,
) -> str:
    """聊天层入口: enabled=False 时绝不触碰 GoalState(默认关闭语义)。

    返回可直接注入 Prompt 的文本; 无内容/失败 → 空串(fail-soft)。
    """
    if not enabled:
        return ""
    return format_goal_context(
        resolve_active_goals(goal_store=goal_store, limit=limit, min_sources=min_sources)
    )


__all__ = [
    "DEFAULT_MAX_ACTIVE_GOALS",
    "GOAL_CONTEXT_SCHEMA_VERSION",
    "resolve_active_goals",
    "format_goal_context",
    "resolve_goal_context_text",
]
