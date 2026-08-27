# -*- coding: utf-8 -*-
"""Fact Differences —— 可验证文本关系（Phase 2C.1）。

只显示"候选事实 vs 已确认事实"的文本关系（相等/包含），
不做任何判断：
- 无语义相似度、无评分、无结论、无动作
- 文本预处理仅 strip 空白（不分词、不向量化、不调用模型）
- 纯函数：无状态、无缓存、不写文件、不修改输入
"""
from __future__ import annotations

from typing import List

MAX_RESULTS = 200


def _norm(text: str) -> str:
    return str(text or "").strip()


def compare_facts(candidates: list, confirmed: list) -> List[dict]:
    """候选事实与已确认事实的可验证文本关系。

    参数：
        candidates: list[dict]，须含 candidate_id / fact
        confirmed:  list[tuple (matched_id, text)]，已确认事实的 id 与文本
                    （调用方负责从 RC/SM/Pattern 组装，id 带类型前缀）

    输出（最多 MAX_RESULTS 条，超出截断）：
        {"candidate_id", "candidate_text", "matched_id", "matched_text", "relation"}
    relation: "equal"（完全相同）| "contains"（候选包含已确认）
            | "contained"（候选被已确认包含）

    无匹配关系的不输出；空输入安全返回 []。
    """
    out: List[dict] = []
    confirmed_norm = [(str(mid or ""), _norm(mtext)) for mid, mtext in (confirmed or [])]
    for c in candidates or []:
        ctext = _norm(c.get("fact") or "")
        if not ctext:
            continue
        for matched_id, mtext in confirmed_norm:
            if not matched_id or not mtext:
                continue
            if ctext == mtext:
                relation = "equal"
            elif mtext in ctext:
                relation = "contains"
            elif ctext in mtext:
                relation = "contained"
            else:
                continue
            out.append({
                "candidate_id": str(c.get("candidate_id") or ""),
                "candidate_text": ctext,
                "matched_id": matched_id,
                "matched_text": mtext,
                "relation": relation,
            })
            if len(out) >= MAX_RESULTS:
                return out
    return out
