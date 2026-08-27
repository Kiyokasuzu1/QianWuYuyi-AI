# -*- coding: utf-8 -*-
"""Governance Entity Projection v1 —— 事实事件映射（Phase 2B.2）。

只做"字段映射"，禁止任何解释/总结/推断：
- 事件结构仅 {ts, action, object_id, status, reason}，全部来自原字段或明确字段转换
- 不生成任何自然语言解释或意义总结（禁止因果叙述、心理分析、成长评价）
- 不缓存、不保存状态、不落盘 —— 纯函数，渲染时现算
- 本模块不调用任何外部模型/服务

四类实体 lineage 来源（契约，2026-08-27 实证）：
    pattern            家族记录聚合（append-only 多行，无显式 history 字段）
    self_model         family 投影的 history（同 append-only 多行）
    relationship_core  audit-log entry（不自行拼接业务历史）
    growth             proposal 状态/时间字段（不解释为什么成长、不评价成败）
"""
from __future__ import annotations

from typing import List, Optional


def family_root(record_id: str) -> str:
    """家族根 id（去掉 # 后缀链）。纯字符串操作，非语义判断。"""
    return str(record_id or "").split("#")[0]


def _first_ts(record: dict, *keys: str) -> str:
    """取第一个非空时间字段（原样返回，不格式化）。"""
    for k in keys:
        v = record.get(k)
        if v:
            return str(v)
    return ""


def to_event(kind: str, record: dict) -> List[dict]:
    """实体记录 → 事实事件列表（仅字段映射，零解释）。

    参数：
        kind:
          "pattern"            record = pattern family（project_pattern_families 输出）
          "self_model"         record = SM family（project_sm_families 输出）
          "relationship_core"  record = audit-log entry
          "growth"             record = proposal（proposals API 条目）

    返回事件（每条均为独立历史记录的事实）：
        {ts, action, object_id, status, reason}
    """
    if kind == "pattern":
        return _pattern_events(record)
    if kind == "self_model":
        return _self_model_events(record)
    if kind == "relationship_core":
        return _rc_events(record)
    if kind == "growth":
        return _growth_events(record)
    return []


# ---------- Pattern：家族记录聚合（无显式 history 字段） ----------
def _pattern_events(family: dict) -> List[dict]:
    fid = str(family.get("family_id") or "")
    out = []
    for entry in family.get("history") or []:
        status = str(entry.get("status") or "")
        out.append({
            "ts": _first_ts(entry, "reviewed_at", "created_at"),
            "action": f"pattern:{status}",      # 明确字段转换（status 原样）
            "object_id": fid,                   # 展示用 family root
            "status": status,
            "reason": str(entry.get("review_note") or ""),
        })
    return out


# ---------- Self Model：family 投影的 history 直接映射 ----------
def _self_model_events(family: dict) -> List[dict]:
    fid = str(family.get("family_id") or "")
    out = []
    for entry in family.get("history") or []:
        status = str(entry.get("status") or "")
        out.append({
            "ts": _first_ts(entry, "reviewed_at", "created_at"),
            "action": f"sm:{status}",           # 明确字段转换（status 原样）
            "object_id": fid,
            "status": status,
            "reason": str(entry.get("review_note") or ""),
        })
    return out


# ---------- Relationship Core：audit-log 事实 ----------
def _rc_events(entry: dict) -> List[dict]:
    after = entry.get("after") or {}
    return [{
        "ts": str(entry.get("ts") or ""),
        "action": str(entry.get("action") or ""),          # audit action 原样
        "object_id": family_root(str(entry.get("object_id") or "")),  # 展示用 root
        "status": str(after.get("status") or after.get("current_status") or ""),
        "reason": str(entry.get("reason") or ""),          # 人工/原始 reason 原样
    }]


# ---------- Growth：proposal 状态/时间字段 ----------
def _growth_events(proposal: dict) -> List[dict]:
    status = str(proposal.get("status") or "")
    return [{
        "ts": _first_ts(proposal, "created_at", "reviewed_at"),
        "action": f"growth:{status}",          # 明确字段转换（status 原样）
        "object_id": str(proposal.get("proposal_id") or ""),
        "status": status,
        "reason": str(proposal.get("reason") or ""),       # proposal reason 原样
    }]


def collect_timeline_events(patterns: list, sm_families: list,
                            audit_entries: list, proposals: list) -> List[dict]:
    """聚合四类实体事件（供 Timeline 渲染）。

    仅做：字段筛选（RC 审计按 object_type/前缀事实筛选）+ 字段映射 + 时间排序。
    不做：解释、总结、补全、跨源拼接。

    sm_families 传入投影后结果（project_sm_families 输出）。
    """
    events: List[dict] = []
    for fam in patterns:
        for ev in to_event("pattern", fam):
            ev["kind"] = "pattern"
            events.append(ev)
    for fam in sm_families:
        for ev in to_event("self_model", fam):
            ev["kind"] = "self_model"
            events.append(ev)
    for entry in audit_entries or []:
        # 关系核心审计事实筛选：对象类型 + 生产 id 前缀（RC-xxx）
        otype = str(entry.get("object_type") or "")
        oid = str(entry.get("object_id") or "")
        if otype in ("fact_candidate", "relationship_core") and family_root(oid).startswith("RC"):
            for ev in to_event("relationship_core", entry):
                ev["kind"] = "relationship_core"
                events.append(ev)
    for p in proposals:
        for ev in to_event("growth", p):
            ev["kind"] = "growth"
            events.append(ev)
    # 时间排序：最近优先；无时间戳（ts=""）自然排最后
    events.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
    return events
