# -*- coding: utf-8 -*-
"""Governance Health Dashboard 事实行生成（Phase 2B.3）。

只输出机械事实（数量 / 状态 / API 响应 / 时间），禁止任何判断：
- 禁止判断词：正常/异常/健康/不健康/有问题/存在风险/建议处理/应该修复
- 禁止推导业务结论（如"人格系统运行良好"）
- 输入字段全部显式传入（测试可直接构造），本模块不做任何字段假设
- 纯函数：无状态、无缓存、不调用任何外部模型/服务、不写任何存储
"""
from __future__ import annotations

from typing import List, Optional


def health_facts(
    growth_counts: Optional[dict],
    conn_status: Optional[str] = None,
    token_source: Optional[str] = None,
    last_success_at: Optional[str] = None,
    last_error: Optional[dict] = None,
) -> List[str]:
    """生成 Health 主检查区事实行。

    参数（均为已解析字段，调用方负责 API 结构解析）：
        growth_counts: {"pending": int, "approved": int, "applied": int}
        conn_status:   "CONNECTED" | "UNAUTHORIZED" | "FAILED" | None
        token_source:  "DPAPI" | "ENV" | "MANUAL" | "NONE"
        last_success_at: "HH:MM:SS"
        last_error:    {"kind": str, "detail": str}

    缺失字段一律不假设：.get 默认值 / "—" 占位，不抛异常。
    """
    g = growth_counts or {}
    facts: List[str] = []
    facts.append(
        f"growth counts: pending {g.get('pending', 0)} · "
        f"approved {g.get('approved', 0)} · applied {g.get('applied', 0)}")
    facts.append(f"connection: {conn_status or '—'}")
    facts.append(f"token source: {token_source or '—'}")
    facts.append(f"last refresh: {last_success_at or '—'}")
    if last_error:
        facts.append(
            f"last error: [{last_error.get('kind') or '?'}] {last_error.get('detail') or ''}")
    return facts
