# -*- coding: utf-8 -*-
"""Governance Console ConnectionState —— 连接可见性（Phase 2B.1）。

只记录"事实"，不做任何判断：
- 状态来源 = HTTP 状态码 / 网络异常 / token 状态（禁止推理状态）
- 三态：CONNECTED / UNAUTHORIZED / FAILED（禁止新增 DEGRADED 等）
- staleness：刷新成功记录数据时间；连续 FAIL_THRESHOLD 次失败标记 stale
- 未请求 ≠ 健康：初始 status=None（"尚未探测"），不视为 CONNECTED
- 本模块不接触 token 内容、不输出任何日志
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional


class ConnectionState:
    CONNECTED = "CONNECTED"
    UNAUTHORIZED = "UNAUTHORIZED"
    FAILED = "FAILED"

    # 连续失败阈值：固定常量，不可配置、不可动态调整
    FAIL_THRESHOLD = 3

    def __init__(self):
        self.status: Optional[str] = None  # None = 尚未探测（≠ 健康）
        self.last_success_at: Optional[str] = None  # "HH:MM:SS"，刷新成功时记录
        self.last_error: Optional[dict] = None  # {at, kind, detail}（detail 截断 ≤120）
        self.consecutive_failures: int = 0

    # ---------- 记录（事实驱动） ----------
    def record_success(self) -> None:
        """治理端点请求成功。"""
        self.status = self.CONNECTED
        self.last_success_at = datetime.now().strftime("%H:%M:%S")
        self.consecutive_failures = 0
        self.last_error = None

    def record_failure(self, kind: str, detail: str) -> None:
        """失败记录。

        kind:
            "unauthorized" —— HTTP 401/403 → UNAUTHORIZED
            "network"      —— 超时 / 连接异常 / 非认证失败 → FAILED
            "http"         —— 其他非认证 HTTP 错误（如 500）→ FAILED
        detail 截断至 120 字符（防污染 UI/tooltip）。
        """
        self.consecutive_failures += 1
        self.last_error = {
            "at": datetime.now().strftime("%H:%M:%S"),
            "kind": kind,
            "detail": (detail or "")[:120],
        }
        self.status = self.UNAUTHORIZED if kind == "unauthorized" else self.FAILED

    @property
    def is_stale(self) -> bool:
        """连续 FAIL_THRESHOLD 次失败 → 数据可能陈旧。"""
        return self.consecutive_failures >= self.FAIL_THRESHOLD
