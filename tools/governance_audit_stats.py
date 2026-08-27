# -*- coding: utf-8 -*-
"""Governance Audit 聚合统计（Phase 2C.2.2）。

仅纯计数：action 计数 / object_type 计数。
- 不解释原因、不生成总结、不判断趋势
- 无状态、无缓存、不写文件、不修改输入
"""
from __future__ import annotations

from typing import Optional


def action_counts(entries: Optional[list]) -> dict:
    """audit 条目按 action 计数（纯 count，键按出现顺序）。"""
    out: dict = {}
    for e in entries or []:
        act = str(e.get("action") or "")
        if act:
            out[act] = out.get(act, 0) + 1
    return out


def object_type_counts(entries: Optional[list]) -> dict:
    """audit 条目按 object_type 计数（纯 count）。"""
    out: dict = {}
    for e in entries or []:
        ot = str(e.get("object_type") or "")
        if ot:
            out[ot] = out.get(ot, 0) + 1
    return out
