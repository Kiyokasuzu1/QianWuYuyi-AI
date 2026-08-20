# -*- coding: utf-8 -*-
"""五道检查的公共基类与 CheckResult 构造辅助。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from src.governance.mutation_contract import CheckResult, MutationRequest


class BaseCheck(ABC):
    """检查器基类：只读决策，禁止写任何域状态。"""

    name: str = "base"

    @abstractmethod
    def check(self, request: MutationRequest) -> CheckResult:
        """对请求执行本道检查，返回 CheckResult。"""


def ok(reason: str = "", metadata: Dict[str, Any] | None = None) -> CheckResult:
    return CheckResult(passed=True, reason=reason, metadata=metadata or {})


def fail(
    reason: str,
    verdict: str,
    metadata: Dict[str, Any] | None = None,
) -> CheckResult:
    """构造失败结果；verdict ∈ {REJECT, NEED_REVIEW, DEFER}（Gateway 采纳）。"""
    meta = dict(metadata or {})
    meta["verdict"] = verdict
    return CheckResult(passed=False, reason=reason, metadata=meta)
