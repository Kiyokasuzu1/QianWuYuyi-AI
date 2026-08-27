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
# v1.3 RC 7.1: Context Budget 硬限制(生产缺陷修复——565 条来源撑爆 prompt)
DEFAULT_MAX_SOURCE_REFS_PER_GOAL = 10   # 每个 goal 渲染的来源引用上限
DEFAULT_MAX_GOAL_CONTEXT_CHARS = 6000   # Goal Context 整体字符预算(约 1500 tokens)

# GoalContext 结构版本(只读视图, 无状态写入)
GOAL_CONTEXT_SCHEMA_VERSION = "goal_context.1.0"


def resolve_active_goals(
    goal_store: Optional[GoalStateStore] = None,
    *,
    limit: int = DEFAULT_MAX_ACTIVE_GOALS,
    min_sources: int = 1,
    max_source_refs: int = DEFAULT_MAX_SOURCE_REFS_PER_GOAL,
) -> Dict[str, Any]:
    """读取 GoalState, 返回只读 GoalContext(不修改任何状态)。

    v1.3 RC 7.1: source_refs 按 max_source_refs 截断(读取层保护),
    总数保留在 evidence_total_count(历史巨型数据不影响下游)。

    Returns:
        {
            "active_goals": [
                {
                    "goal_id", "description", "reason",
                    "source_refs"(截断), "evidence_total_count",
                    "priority", "confidence", "proposal_id", "created_at"
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
        _ref_cap = max(1, int(max_source_refs or DEFAULT_MAX_SOURCE_REFS_PER_GOAL))
    except (TypeError, ValueError):
        _ref_cap = DEFAULT_MAX_SOURCE_REFS_PER_GOAL

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
        _refs = list(_rec.get("source_refs", []) or [])
        _total_refs = len(_refs)
        if _total_refs > _ref_cap:
            logger.warning(
                "[GoalResolver] goal=%s source_refs 超上限(%d -> %d 截断, 总数保留)",
                str(_rec.get("goal_id", "") or ""), _total_refs, _ref_cap,
            )
        _out.append({
            "goal_id": str(_rec.get("goal_id", "") or ""),
            "description": str(_rec.get("description", "") or ""),
            "reason": str(_rec.get("reason", "") or ""),
            "source_refs": _refs[:_ref_cap],
            "evidence_total_count": _total_refs,
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


def format_goal_context(
    goal_context: Optional[Dict[str, Any]],
    *,
    max_source_refs: int = DEFAULT_MAX_SOURCE_REFS_PER_GOAL,
    max_chars: int = DEFAULT_MAX_GOAL_CONTEXT_CHARS,
) -> str:
    """把 GoalContext dict 渲染为 Prompt 片段(空/无 goals → 空串)。

    v1.3 RC 7.1 Context Budget(fail-safe, 永不抛出):
    - 每条 goal 最多渲染 max_source_refs 条来源, 其余以 "等 N 条来源" 汇总;
    - 整体文本超过 max_chars 时硬截断并追加截断标记 + 记录 warning;
    - 任何异常返回空串, 绝不让聊天失败/空回复/fallback。
    """
    try:
        if not isinstance(goal_context, dict):
            return ""
        _goals = goal_context.get("active_goals") or []
        if not _goals:
            return ""
        try:
            _ref_cap = max(1, int(max_source_refs or DEFAULT_MAX_SOURCE_REFS_PER_GOAL))
        except (TypeError, ValueError):
            _ref_cap = DEFAULT_MAX_SOURCE_REFS_PER_GOAL
        try:
            _char_budget = max(200, int(max_chars or DEFAULT_MAX_GOAL_CONTEXT_CHARS))
        except (TypeError, ValueError):
            _char_budget = DEFAULT_MAX_GOAL_CONTEXT_CHARS
        _lines = [
            "【当前关注方向】",
            "以下是已通过治理链审批、当前活跃的关注方向。它只是理解上下文,",
            "不改变你的身份与原则, 不必机械复述。",
        ]
        # Phase B1c.3: goal aging（三态开关，默认 off = 旧输出逐字节兼容）。
        # 只允许 Temporal 纯函数计算时间差；禁止 LLM 判断重要性/推测意图。
        _aging_mode = "off"
        try:
            from src.config import get as _cfg_get
            _aging_cfg = _cfg_get("temporal.goal_aging", {}) or {}
            if isinstance(_aging_cfg, dict):
                _aging_mode = str(_aging_cfg.get("mode", "off") or "off").strip().lower()
        except Exception:  # noqa: BLE001
            _aging_mode = "off"
        for _g in _goals:
            if not isinstance(_g, dict):
                continue
            _refs = _g.get("source_refs") or []
            _total = int(_g.get("evidence_total_count", len(_refs)) or len(_refs))
            _shown = [r for r in _refs[:_ref_cap] if isinstance(r, dict)]
            _ref_text = ", ".join(
                f"{str(r.get('source_type', '') or '')}:{str(r.get('source_id', '') or '')}"
                for r in _shown
            )
            if _total > len(_shown):
                _ref_text += f" 等 {_total - len(_shown)} 条来源"
            _lines.append(
                f"- 关注方向({_g.get('goal_id', '')}): {_g.get('description', '')}"
            )
            if _g.get("reason"):
                _lines.append(f"  缘由: {_g.get('reason', '')}")
            if _ref_text:
                _lines.append(f"  来源: {_ref_text}")
            # B1c.3: aging 行（shadow=仅日志；active=注入；off=跳过）
            if _aging_mode in ("shadow", "active"):
                try:
                    from src.temporal.goal_aging import format_goal_age
                    _aging_text = format_goal_age(
                        _g.get("created_at"),
                        _g.get("updated_at") or _g.get("created_at"),
                    )
                    if _aging_mode == "shadow":
                        logger.info(
                            "[GoalAging][shadow] goal=%s text=%r",
                            _g.get("goal_id", ""), _aging_text,
                        )
                    elif _aging_text:
                        _lines.append(f"  {_aging_text}")
                except Exception as exc_aging:  # noqa: BLE001
                    logger.warning("[GoalAging] 计算失败（已隔离，不注入）: %s", exc_aging)
        _text = "\n".join(_lines)
        if len(_text) > _char_budget:
            logger.warning(
                "[GoalResolver] goal context truncated (%d -> %d chars)",
                len(_text), _char_budget,
            )
            _text = _text[:_char_budget] + "\n…（关注方向上下文已截断）"
        return _text
    except Exception as exc:  # noqa: BLE001
        logger.warning("[GoalResolver] 渲染失败(已隔离, 返回空串): %s", exc)
        return ""


def resolve_goal_context_text(
    goal_store: Optional[GoalStateStore] = None,
    *,
    enabled: bool = True,
    limit: int = DEFAULT_MAX_ACTIVE_GOALS,
    min_sources: int = 1,
    max_source_refs: int = DEFAULT_MAX_SOURCE_REFS_PER_GOAL,
    max_chars: int = DEFAULT_MAX_GOAL_CONTEXT_CHARS,
) -> str:
    """聊天层入口: enabled=False 时绝不触碰 GoalState(默认关闭语义)。

    返回可直接注入 Prompt 的文本; 无内容/失败 → 空串(fail-soft)。
    """
    if not enabled:
        return ""
    return format_goal_context(
        resolve_active_goals(
            goal_store=goal_store,
            limit=limit,
            min_sources=min_sources,
            max_source_refs=max_source_refs,
        ),
        max_source_refs=max_source_refs,
        max_chars=max_chars,
    )


__all__ = [
    "DEFAULT_MAX_ACTIVE_GOALS",
    "DEFAULT_MAX_SOURCE_REFS_PER_GOAL",
    "DEFAULT_MAX_GOAL_CONTEXT_CHARS",
    "GOAL_CONTEXT_SCHEMA_VERSION",
    "resolve_active_goals",
    "format_goal_context",
    "resolve_goal_context_text",
]
