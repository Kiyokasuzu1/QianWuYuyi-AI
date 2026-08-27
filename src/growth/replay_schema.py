# -*- coding: utf-8 -*-
"""T1-E Replay Result Schema（dataclass，schema_version=1）。

Replay 输出只作为验证报告，不改变任何真实状态（唯一状态源 = proposal_events.jsonl）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

SCHEMA_VERSION = 1


@dataclass
class CheckResult:
    check: str          # trace_exists / snapshot_hash / validator_replay / ledger_state / provenance
    ok: bool
    detail: str = ""


@dataclass
class ReplayResult:
    schema_version: int = SCHEMA_VERSION
    timestamp: str = ""
    proposal_id: str = ""
    checks: List[CheckResult] = field(default_factory=list)
    result: str = "fail"                     # pass | fail
    original_state: Optional[str] = None     # ledger reduce 出的原始状态
    replayed_state: Optional[str] = None     # 重放 reduce 状态
    mismatch: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "proposal_id": self.proposal_id,
            "checks": [{"check": c.check, "ok": c.ok, "detail": c.detail}
                       for c in self.checks],
            "result": self.result,
            "original_state": self.original_state,
            "replayed_state": self.replayed_state,
            "mismatch": self.mismatch,
        }
