# -*- coding: utf-8 -*-
"""T1-E Replay Engine —— 成长过程确定性重放验证（只读验证器，非执行器）。

验证五项事实一致性（强约束）：
  C1 trace 存在性：evidence_trace_ids 全部可解析到 ExperienceTrace
  C2 snapshot hash：按 path/old_value/source 重算 hash，与原值一致
  C3 validator 重放：当前 validator 对提案重新校验（原通过 → 必须仍通过）
  C4 ledger 状态重放：事件流 reduce 状态 == 提案记录状态
  C5 provenance 链：trace/snapshot/proposal 来源标记存在且合法

Replay 只读：不修改 proposal/personality/growth_state/memory；
不触发 apply；不创建成长记录；不重新生成人格解释。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Callable, List, Optional

from src.growth.experience_trace import (
    DEFAULT_TRACE_PATH, resolve_trace_ids,
)
from src.growth.growth_audit import DEFAULT_EVENTS_PATH
from src.growth.proposal_ledger import reduce_events
from src.growth.proposal_validator import validate_proposal
from src.growth.replay_schema import CheckResult, ReplayResult
from src.growth.snapshot_hash import calculate_snapshot_hash

PROVENANCE_VALUES = ("system_rule", "llm_candidate", "system_state_read")


def _default_proposal_loader(proposal_id: str) -> Optional[dict]:
    try:
        from src.growth.proposal.storage import load_proposal
        p = load_proposal(proposal_id)
        return p.to_dict() if p is not None else None
    except Exception:  # noqa: BLE001
        return None


class ReplayEngine:
    def __init__(
        self,
        *,
        events_path: str = DEFAULT_EVENTS_PATH,
        trace_path: str = DEFAULT_TRACE_PATH,
        proposal_loader: Optional[Callable[[str], Optional[dict]]] = None,
    ):
        self.events_path = events_path
        self.trace_path = trace_path
        self.proposal_loader = proposal_loader or _default_proposal_loader

    # ---------- 只读加载 ----------
    def _load_events(self) -> List[dict]:
        try:
            if not os.path.exists(self.events_path):
                return []
            with open(self.events_path, encoding="utf-8") as f:
                out = []
                for line in f:
                    if line.strip():
                        try:
                            out.append(__import__("json").loads(line))
                        except Exception:  # noqa: BLE001
                            continue
                return out
        except Exception:  # noqa: BLE001
            return []

    def _adapt_proposal(self, prop: dict) -> dict:
        """适配层：evidence_ids（schema 旧名）→ evidence_trace_ids（validator 期待名）。"""
        d = dict(prop)
        if "evidence_trace_ids" not in d or not d.get("evidence_trace_ids"):
            d["evidence_trace_ids"] = d.get("evidence_ids") or []
        return d

    # ---------- 五项检查 ----------
    def _check_trace_exists(self, prop: dict, checks: List[CheckResult]) -> None:
        ids = prop.get("evidence_trace_ids") or []
        if not ids:
            checks.append(CheckResult("trace_exists", False, "无 evidence_trace_ids"))
            return
        resolved = resolve_trace_ids(ids, self.trace_path)
        missing = [i for i in ids if not resolved.get(i)]
        checks.append(CheckResult(
            "trace_exists", not missing,
            "missing=" + ",".join(missing) if missing else f"all {len(ids)} resolved"))

    def _check_snapshot_hash(self, prop: dict, checks: List[CheckResult]) -> None:
        snaps = prop.get("before_snapshot") or []
        if not snaps:
            checks.append(CheckResult("snapshot_hash", False, "无 before_snapshot"))
            return
        bad = []
        for s in snaps:
            recalc = calculate_snapshot_hash(s.get("path"), s.get("old_value"),
                                             s.get("source"))
            if recalc != s.get("hash"):
                bad.append(s.get("path"))
        checks.append(CheckResult("snapshot_hash", not bad,
                                  "mismatch=" + ",".join(bad) if bad else "all consistent"))

    def _check_validator_replay(self, prop: dict, checks: List[CheckResult]) -> None:
        errors = validate_proposal(prop, lambda ids: resolve_trace_ids(ids, self.trace_path))
        checks.append(CheckResult("validator_replay", not errors,
                                  "; ".join(errors) if errors else "re-passed"))

    def _check_ledger_state(self, prop: dict, checks: List[CheckResult],
                            replay_result: ReplayResult) -> None:
        events = [e for e in self._load_events()
                  if e.get("proposal_id") == prop.get("proposal_id")]
        if not events:
            replay_result.mismatch.append("ledger 事件流中无该提案事件")
            checks.append(CheckResult("ledger_state", False, "无事件可重放"))
            return
        replayed = reduce_events(events).get(prop.get("proposal_id"))
        original = prop.get("status")
        replay_result.original_state = original
        replay_result.replayed_state = replayed
        ok = replayed == original
        if not ok:
            replay_result.mismatch.append(f"状态不一致: 记录={original} 重放={replayed}")
        checks.append(CheckResult("ledger_state", ok,
                                  f"original={original} replayed={replayed}"))

    def _check_provenance(self, prop: dict, checks: List[CheckResult]) -> None:
        bad = []
        if prop.get("provenance") not in PROVENANCE_VALUES:
            bad.append(f"proposal.provenance={prop.get('provenance')}")
        for s in prop.get("before_snapshot") or []:
            if s.get("provenance") != "system_state_read":
                bad.append(f"snapshot.provenance={s.get('provenance')}")
        resolved = resolve_trace_ids(prop.get("evidence_trace_ids") or [], self.trace_path)
        for tr in resolved.values():
            if tr and tr.get("provenance") not in PROVENANCE_VALUES:
                bad.append(f"trace.provenance={tr.get('provenance')}")
        checks.append(CheckResult("provenance", not bad,
                                  "; ".join(bad) if bad else "chain valid"))

    # ---------- 入口 ----------
    def replay(self, proposal_id: str) -> ReplayResult:
        result = ReplayResult(timestamp=datetime.now(timezone.utc).isoformat(),
                              proposal_id=proposal_id)
        prop = self.proposal_loader(proposal_id)
        if prop is None:
            result.mismatch.append("proposal 不存在")
            result.checks.append(CheckResult("proposal_load", False, "proposal not found"))
            return result
        prop = self._adapt_proposal(prop)
        self._check_trace_exists(prop, result.checks)
        self._check_snapshot_hash(prop, result.checks)
        self._check_validator_replay(prop, result.checks)
        self._check_ledger_state(prop, result.checks, result)
        self._check_provenance(prop, result.checks)
        result.result = "pass" if all(c.ok for c in result.checks) else "fail"
        return result
