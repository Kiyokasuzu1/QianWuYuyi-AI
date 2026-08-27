# -*- coding: utf-8 -*-
"""Governance Explain Mode + Impact Preview（Phase 2B.4）。

只展示事实映射，不做任何判断：
- 影响字段列表来自固定常量表（ACTION_FIELD_MAP），仅表示字段变化关系
- DRAIN_RULES 为固定常量（不配置化、不动态调整、不调用模型）
- "不能证明什么"为固定人工标注文本，禁止系统生成
- 不生成自然语言解释，不预测未来，不评价对象
- 纯函数：无状态、无缓存、不写任何存储
"""
from __future__ import annotations

from typing import List

# ---------- 固定影响映射表（仅字段影响关系，非业务评价） ----------
# 键 = 已有操作（POST 契约实证）；值 = 受影响字段列表
ACTION_FIELD_MAP = {
    "confirm":           ["fact_candidate.status"],
    "reject":            ["fact_candidate.status"],
    "hold":              ["fact_candidate.status"],
    "modify":            ["fact_candidate.fact"],
    "pattern_confirm":   ["shared_life_pattern.status"],
    "pattern_reject":    ["shared_life_pattern.status"],
    "pattern_supersede": ["shared_life_pattern.status"],
    "pattern_archive":   ["shared_life_pattern.status"],
    "rc_supersede":      ["relationship_core.status"],
    "growth_approve":    ["proposal.status"],
    "growth_reject":     ["proposal.status"],
}

# 固定状态映射：操作后 status 字段值（仅字段值，非评价）
STATUS_AFTER_MAP = {
    "confirm": "confirmed",
    "reject": "rejected",
    "hold": "held",
    "pattern_confirm": "confirmed",
    "pattern_reject": "rejected",
    "pattern_supersede": "superseded",
    "pattern_archive": "archived",
    "rc_supersede": "superseded",
    "growth_approve": "approved",
    "growth_reject": "rejected",
}

# 固定 Drain 提示：仅表示"操作后是否需要 Runtime 阶段生效"，非评价
DRAIN_RULES = {
    "confirm": "NO",
    "reject": "NO",
    "hold": "NO",
    "modify": "NO",
    "pattern_confirm": "NO",
    "pattern_reject": "NO",
    "pattern_supersede": "NO",
    "pattern_archive": "NO",
    "rc_supersede": "NO",
    "growth_approve": "YES",
    "growth_reject": "NO",
}

# 各对象类型可用操作（固定，按已有操作契约）
OPERATIONS_BY_KIND = {
    "candidate": ["confirm", "reject", "hold", "modify"],
    "pattern":   ["pattern_confirm", "pattern_reject", "pattern_supersede", "pattern_archive"],
    "growth":    ["growth_approve", "growth_reject"],
}

# 人工标注区固定文本（系统不生成任何"不能证明"内容）
NOT_PROVEN_NOTE = "本页面仅展示已存在字段。无法证明未展示的数据状态。"

KIND_LABEL = {"candidate": "候选", "pattern": "共同生活模式", "growth": "Growth 提案"}


def _name_of(record: dict, kind: str) -> str:
    """对象主字段（原文，未实证字段一律不取）。"""
    if kind == "candidate":
        return str(record.get("fact") or "")
    if kind == "pattern":
        return str(record.get("title") or record.get("summary") or record.get("pattern_id") or "")
    if kind == "growth":
        return str(record.get("proposal_type") or "")
    return ""


def _status_of(record: dict, kind: str) -> str:
    """当前状态（原字段；pattern 家族用 current_status 投影）。"""
    if kind == "pattern":
        return str(record.get("status") or record.get("current_status") or "")
    return str(record.get("status") or record.get("current_status") or "")


def build_explain(record: dict, kind: str, fetched_at: str) -> List[str]:
    """生成 Explain 文本行（事实映射，零推理）。

    参数：
        record:     实时 GET 的最新对象记录（缺失字段用占位，不假设）
        kind:       "candidate" | "pattern" | "growth"
        fetched_at: "HH:MM:SS"（实时数据获取时间）
    """
    record = record or {}
    rid = str(record.get("candidate_id") or record.get("pattern_id")
              or record.get("proposal_id") or "—")
    cur = _status_of(record, kind) or "—"
    name = _name_of(record, kind) or "—"

    lines = [
        f"对象: {rid}（{KIND_LABEL.get(kind, kind)}）",
        f"名称: {name}",
        f"当前状态: {cur}",
        f"数据截至: {fetched_at}",
        "",
        "影响映射（固定表，非评价）:",
    ]
    for op in OPERATIONS_BY_KIND.get(kind, []):
        fields = ACTION_FIELD_MAP.get(op, [])
        after = STATUS_AFTER_MAP.get(op, "—")
        drain = DRAIN_RULES.get(op, "—")
        if op == "modify":
            after_desc = "（人工输入文本）"  # 占位说明，非预测
        else:
            after_desc = after
        lines.append(
            f"[{op}] → {', '.join(fields) or '—'}: {cur} → {after_desc} · drain: {drain}")
    lines.append("")
    lines.append(NOT_PROVEN_NOTE)
    return lines
