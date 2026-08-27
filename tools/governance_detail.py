# -*- coding: utf-8 -*-
"""Governance Detail Explorer（Phase 2C.3）。

把已有数据放到一起展示，不创造新的解释层：
- 输入：对象记录 + 已聚合 timeline 事件 + audit 条目 + 固定映射影响字段
- 输出：展示结构（行列表），全部来自输入字段的过滤与复制
- 无总结、无解释、无判断、无动作
- 纯函数：无状态、无缓存、不写文件、不修改输入
"""
from __future__ import annotations

from typing import List, Optional

KIND_LABEL = {"candidate": "候选", "pattern": "共同生活模式",
              "growth": "Growth 提案", "relationship_core": "关系核心"}


def _family_root(record_id: str) -> str:
    return str(record_id or "").split("#")[0]


def _name_of(obj: dict, kind: str) -> str:
    """对象名称（真实字段；未实证字段不取）。"""
    if kind == "candidate":
        return str(obj.get("fact") or "")
    if kind == "pattern":
        return str(obj.get("title") or obj.get("summary") or "")
    if kind == "growth":
        return str(obj.get("proposal_type") or "")
    if kind == "relationship_core":
        ag = obj.get("agreements") or []
        return str(ag[0] if ag else "")
    return ""


def _status_of(obj: dict) -> str:
    return str(obj.get("status") or obj.get("current_status") or "")


def build_detail(
    obj: Optional[dict],
    kind: str,
    timeline_events: Optional[list],
    audit_entries: Optional[list],
    affected_fields: Optional[list],
) -> List[str]:
    """组装对象详情展示行（全部字段复制/过滤，零解释）。"""
    if not obj:
        return ["对象: —", "（无选中对象）"]
    rid = str(obj.get("candidate_id") or obj.get("pattern_id")
              or obj.get("proposal_id") or obj.get("fact_id") or "—")
    root = _family_root(rid)
    lines = [
        f"对象: {rid}（{KIND_LABEL.get(kind, kind)}）",
        f"名称: {_name_of(obj, kind) or '—'}",
        f"当前状态: {_status_of(obj) or '—'}",
        "原始事实字段:",
    ]
    raw_shown = False
    for key in ("fact", "summary", "reason", "evidence_summary"):
        v = obj.get(key)
        if v:
            lines.append(f"  {key}: {v}")
            raw_shown = True
    if kind == "relationship_core":
        ag = obj.get("agreements") or []
        if ag:
            lines.append(f"  agreements: {ag[0]}")
            raw_shown = True
    if not raw_shown:
        lines.append("  （无原始事实字段）")

    # 相关 Timeline 事件（object_id root 匹配；事件已含 kind 标签）
    related = [e for e in (timeline_events or [])
               if _family_root(str(e.get("object_id") or "")) == root]
    lines.append(f"相关 Timeline 事件: {len(related)} 条")
    for e in related[:10]:
        lines.append(f"  {e.get('ts') or '—'} [{e.get('action') or ''}] "
                     f"{e.get('status') or ''}"
                     + (f" · {e.get('reason')}" if e.get("reason") else ""))

    # 相关 Audit 记录（object_id root 匹配）
    related_a = [a for a in (audit_entries or [])
                 if _family_root(str(a.get("object_id") or "")) == root]
    lines.append(f"相关 Audit 记录: {len(related_a)} 条")
    for a in related_a[:10]:
        lines.append(f"  {a.get('ts') or '—'} [{a.get('action') or ''}] "
                     f"{a.get('reviewer') or ''}"
                     + (f" · {a.get('reason')}" if a.get("reason") else ""))

    # Explain 上下文（固定映射的影响字段）
    fields = list(affected_fields or [])
    lines.append("Explain 上下文:"
                 + (" " + " · ".join(fields) if fields else " （无）"))
    return lines
