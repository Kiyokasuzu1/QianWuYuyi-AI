# -*- coding: utf-8 -*-
"""GrowthProposal Validator（T0-B）—— 生成侧硬校验，只拒绝，不批准。

八项校验（主架构师审核批准的规则 1-8，规则 8 为逐条 path 断言加强版）：
  1. schema_version 存在且受支持
  2. evidence_trace_ids 非空且全部可解析（T1-A 前：ID 格式 + 非 test 前缀）
  3. pattern_detected 不以 test_ 开头
  4. pattern_frequency ≥ 1
  5. before_snapshot 每项完整（path/old_value/captured_at/source/hash）
  6. used_llm=false，或 true 时必须带 llm_source 标注
  7. evidence_trace_ids 无重复（len(set)==len）
  8. 每条 proposed_changes[].path 必须存在于 before_snapshot.paths（逐条断言）

fail-closed：任何一项失败 → 拒绝（返回原因列表）；通过 → 空列表。
纯函数：无 IO、无状态、不修改输入、不触碰人格。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

SCHEMA_VERSION = 1
_SUPPORTED_SCHEMAS = (1,)
_SNAPSHOT_FIELDS = ("path", "old_value", "captured_at", "source", "hash")
_TRACE_ID_RE = re.compile(r"^(exp|trc)_[A-Za-z0-9]+$")


def _check_schema(proposal: dict, errors: list) -> None:
    if proposal.get("schema_version") not in _SUPPORTED_SCHEMAS:
        errors.append(f"schema_version 缺失或不受支持: {proposal.get('schema_version')}")


def _check_evidence(proposal: dict, errors: list, trace_resolver=None) -> None:
    ids = proposal.get("evidence_trace_ids") or []
    if not ids:
        errors.append("evidence_trace_ids 为空")
        return
    for eid in ids:
        if not isinstance(eid, str) or not _TRACE_ID_RE.match(eid):
            errors.append(f"evidence id 不可解析: {eid}")
        if eid.startswith("test_") or "test" in eid.lower():
            errors.append(f"evidence id 疑似测试数据: {eid}")
    if len(set(ids)) != len(ids):
        errors.append("evidence_trace_ids 存在重复（不增加证据强度）")
    # T1-A：trace 解析实装——证据必须已固化（Trace 早于 Proposal）
    if trace_resolver is not None:
        resolved = trace_resolver(ids)
        for eid in ids:
            if not resolved.get(eid):
                errors.append(f"evidence trace 未固化（Trace 必须早于 Proposal）: {eid}")


def _check_pattern(proposal: dict, errors: list) -> None:
    meta = proposal.get("evaluator_meta") or {}
    pat = str(meta.get("pattern_detected") or "")
    if pat.startswith("test_"):
        errors.append(f"pattern_detected 为测试模式: {pat}")
    freq = meta.get("pattern_frequency")
    if not isinstance(freq, (int, float)) or freq < 1:
        errors.append(f"pattern_frequency 非法: {freq}")


def _check_snapshot(proposal: dict, errors: list) -> None:
    snap = proposal.get("before_snapshot") or []
    if not snap:
        errors.append("before_snapshot 缺失（before=null 不可审计）")
        return
    paths = set()
    for item in snap:
        if not isinstance(item, dict):
            errors.append("before_snapshot 条目非对象")
            continue
        for field in _SNAPSHOT_FIELDS:
            if field not in item:
                errors.append(f"before_snapshot 条目缺字段: {field}")
        if item.get("path"):
            paths.add(item["path"])
    # 规则 8（加强版）：逐条断言 change.path 在 snapshot.paths 中
    for change in proposal.get("proposed_changes") or []:
        p = (change or {}).get("path")
        if p and p not in paths:
            errors.append(f"proposed_change.path 不在 before_snapshot 中: {p}")


def _check_llm(proposal: dict, errors: list) -> None:
    meta = proposal.get("evaluator_meta") or {}
    used = bool(meta.get("used_llm"))
    if used and not (meta.get("llm_source") or meta.get("llm_prompt_id")):
        errors.append("used_llm=true 但无 llm_source/llm_prompt_id 标注（禁止凭空意义）")


def validate_proposal(proposal: Optional[dict], trace_resolver=None) -> List[str]:
    """校验提案。通过 → []; 拒绝 → 原因列表（fail-closed，只拒绝不批准）。

    trace_resolver: 可选，callable(ids) -> {id: trace_or_None}；
    T1-A 后传入 experience_trace.resolve_trace_ids 实装"证据必须已固化"。
    """
    if not isinstance(proposal, dict):
        return ["proposal 非对象"]
    errors: List[str] = []
    _check_schema(proposal, errors)
    _check_evidence(proposal, errors, trace_resolver)
    _check_pattern(proposal, errors)
    _check_snapshot(proposal, errors)
    _check_llm(proposal, errors)
    return errors


def validator_rejection_event(proposal: dict, errors: List[str],
                              reviewer: str = "validator") -> Dict[str, str]:
    """拒绝 → 审计事件（入事件账本，供回放）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "event": "proposal_validator_rejected",
        "proposal_id": str(proposal.get("proposal_id") or proposal.get("id") or ""),
        "reason": "; ".join(errors),
        "reviewer": reviewer,
        "timestamp": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
    }
