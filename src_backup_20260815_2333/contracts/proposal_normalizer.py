# -*- coding: utf-8 -*-
"""
src/contracts/proposal_normalizer.py

Phase 3.6.2: GrowthProposalNormalizer —— 双 Schema 转换层（lossless）

背景：
- Schema A（canonical）: src/contracts/growth_schema.GrowthProposal
    字段: id / proposed_changes(List[ChangeItem]) / evidence_ids / confidence /
          evaluator_meta / status / timestamp
- Schema B（governance）: src/growth/proposal/proposal.GrowthProposal
    字段: proposal_id / affected_dimensions(Dict[str, float]) / evidence /
          metadata + 一组扩展字段(reviewer_id, priority, expires_at, ...)

职责：
- detect(proposal)        → "canonical" / "governance" / "unknown"
- normalize_to_canonical(proposal)
                          → 把任意 dict 转换为 canonical 形态（7 个核心字段 + 保留额外信息）
- to_governance_view(proposal)
                          → 把任意 dict 转换为 governance 形态
                          → affected_dimensions 由 proposed_changes 推导
                          → before/after 保留到 metadata

设计原则：
- 纯函数（不修改原 dict）
- 防御优先：None / {} / 未知 schema / 类型错误 / 空列表 / 缺 confidence 全部安全处理
- 不抛异常（任何失败都返回安全默认值）
- 不依赖 Runtime / Orchestrator / Growth / Personality
- 0 副作用：不写盘、不打 log、不发事件
- 字段保留：B → A 时把 B 的原字段打包到 evaluator_meta["_governance_origin"]，
           避免 round-trip 时丢字段
- 时间戳归一：B 的 timestamp 自动补 "Z" 后缀
- 状态机映射：B 的 pending/approved/rejected/applied/cancelled → A 的 proposed/accepted/rejected

不修改：
- src/runtime/*
- src/orchestrator.py
- src/growth/*
- src/personality/*
- src/admin/selfmodel_consumer.py
- src/admin/selfmodel_consumer_audit.py
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 常量
# ============================================================

CANONICAL = "canonical"
GOVERNANCE = "governance"
UNKNOWN = "unknown"

# B → A 状态机映射
STATUS_B_TO_A: Dict[str, str] = {
    "pending": "proposed",
    "proposed": "proposed",
    "approved": "accepted",
    "accepted": "accepted",
    "rejected": "rejected",
    "applied": "accepted",
    "cancelled": "cancelled",
    "expired": "expired",
}

# A → B 状态机映射
STATUS_A_TO_B: Dict[str, str] = {
    "proposed": "pending",
    "accepted": "approved",
    "rejected": "rejected",
    "cancelled": "cancelled",
    "expired": "expired",
}

# 检测置信度阈值（用于 detect 时的特征字段匹配度）
_CANONICAL_SIGNATURE = ("id", "proposed_changes", "evidence_ids")
_GOVERNANCE_SIGNATURE = ("proposal_id", "affected_dimensions", "evidence")


# ============================================================
# 工具函数
# ============================================================

def _now_iso() -> str:
    try:
        return datetime.utcnow().isoformat() + "Z"
    except Exception:
        return "1970-01-01T00:00:00Z"


def _safe_str(v: Any, default: str = "") -> str:
    """仅当 v 是 str 或 None 时返回对应值，其他类型一律返回 default。"""
    if v is None:
        return default
    if isinstance(v, str):
        return v
    return default


def _safe_str_loose(v: Any, default: str = "") -> str:
    """宽松版本：v 不是 str 时尝试 str(v)，失败返回 default。"""
    if v is None:
        return default
    if isinstance(v, str):
        return v
    try:
        return str(v)
    except Exception:
        return default


def _safe_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, (tuple, set, frozenset)):
        return list(v)
    return []


def _safe_list_of_dicts(v: Any) -> List[Dict[str, Any]]:
    """只保留 dict 元素；其他类型（None / str / int / list）一律过滤。"""
    out: List[Dict[str, Any]] = []
    for item in _safe_list(v):
        if isinstance(item, dict):
            out.append(item)
    return out


def _safe_dict(v: Any) -> Dict[str, Any]:
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    return {}


def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v is None:
        return default
    if isinstance(v, bool):
        # bool 是 int 子类，但要避免 True→1.0 / False→0.0 的隐式转换
        return default
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _extract_trait_name(path: Any) -> str:
    if not isinstance(path, str) or not path:
        return "unknown"
    parts = [p for p in path.split(".") if p]
    if not parts:
        return "unknown"
    return parts[-1]


def _ensure_z_suffix(ts: str) -> str:
    if not ts:
        return _now_iso()
    if ts.endswith("Z"):
        return ts
    # 尝试把 +00:00 转 Z；其他情况简单追加
    if ts.endswith("+00:00"):
        return ts[:-6] + "Z"
    return ts + "Z"


# ============================================================
# detect
# ============================================================

def _count_signature_match(proposal: dict, signature: Tuple[str, ...]) -> int:
    return sum(1 for k in signature if k in proposal)


def detect(proposal: Any) -> str:
    """
    探测 proposal 形态。

    优先级（同名冲突时）：
    1. canonical 特征字段（id + proposed_changes + evidence_ids）匹配数更高
    2. governance 特征字段（proposal_id + affected_dimensions + evidence）匹配数更高
    3. 仅有 id / 仅有 proposal_id → 各自对应形态
    4. 都无 → unknown

    Returns:
        "canonical" | "governance" | "unknown"
    """
    if not isinstance(proposal, dict):
        return UNKNOWN

    has_id = "id" in proposal
    has_proposal_id = "proposal_id" in proposal
    has_proposed_changes = "proposed_changes" in proposal
    has_affected_dimensions = "affected_dimensions" in proposal
    has_evidence_ids = "evidence_ids" in proposal
    has_evidence = "evidence" in proposal

    # 1) 完整特征匹配
    canonical_hits = _count_signature_match(proposal, _CANONICAL_SIGNATURE)
    governance_hits = _count_signature_match(proposal, _GOVERNANCE_SIGNATURE)

    if canonical_hits >= 2 and canonical_hits > governance_hits:
        return CANONICAL
    if governance_hits >= 2 and governance_hits > canonical_hits:
        return GOVERNANCE

    # 2) 部分特征 + 主键
    if has_id and (has_proposed_changes or has_evidence_ids):
        return CANONICAL
    if has_proposal_id and (has_affected_dimensions or has_evidence):
        return GOVERNANCE

    # 3) T10: id 与 proposal_id 冲突（同时存在）
    if has_id and has_proposal_id:
        # 看哪个辅助字段更明确
        if has_proposed_changes or has_evidence_ids:
            return CANONICAL
        if has_affected_dimensions or has_evidence:
            return GOVERNANCE
        # 完全对等：根据典型 canonical 字段（status/timestamp/evaluator_meta）进一步判断
        if "evaluator_meta" in proposal and "metadata" not in proposal:
            return CANONICAL
        if "metadata" in proposal and "evaluator_meta" not in proposal:
            return GOVERNANCE
        # 默认 canonical 优先（因为 Runtime 主链路用 canonical）
        return CANONICAL

    # 4) 单一主键
    if has_id:
        return CANONICAL
    if has_proposal_id:
        return GOVERNANCE

    return UNKNOWN


# ============================================================
# canonical 输出构造
# ============================================================

def _build_canonical(
    *,
    id_: str,
    proposed_changes: List[Dict[str, Any]],
    evidence_ids: List[Any],
    confidence: float,
    evaluator_meta: Dict[str, Any],
    status: str,
    timestamp: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构造 canonical dict。

    7 个核心字段（顺序固定）+ 额外保留字段。
    proposed_changes 自动过滤非 dict 项。
    """
    pcs_clean = _safe_list_of_dicts(proposed_changes)
    out: Dict[str, Any] = {
        "id": id_,
        "proposed_changes": pcs_clean,
        "evidence_ids": evidence_ids,
        "confidence": confidence,
        "evaluator_meta": evaluator_meta,
        "status": status,
        "timestamp": timestamp,
    }
    # 额外保留字段（不丢 source_event_id 等）
    if extra:
        for k, v in extra.items():
            if k not in out:
                out[k] = v
    return out


def _proposed_changes_from_affected(
    affected_dimensions: Dict[str, Any],
    before_state: Dict[str, Any],
    after_state: Dict[str, Any],
    metadata: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    从 B 的 affected_dimensions 派生 A 的 proposed_changes。

    优先级：
    1. after_state - before_state（如果两者都有）
    2. affected_dimensions 视为 delta
    3. after_state 单独（视为绝对值）
    """
    items: List[Dict[str, Any]] = []
    reason = _safe_str(metadata.get("reason"), "")

    # 1) 先从 after_state / before_state 推导（最丰富）
    after_keys = set(_safe_dict(after_state).keys())
    before_keys = set(_safe_dict(before_state).keys())
    all_keys = after_keys | before_keys

    for trait in all_keys:
        before_v = _safe_float(before_state.get(trait), None)
        after_v = _safe_float(after_state.get(trait), None)
        if before_v is None and after_v is None:
            continue
        if before_v is None:
            before_v = 0.5
        if after_v is None:
            after_v = before_v
        items.append({
            "path": f"personality.traits.{trait}" if "." not in str(trait) else str(trait),
            "before": before_v,
            "after": after_v,
            "reason": reason,
        })

    # 2) 受影响维度补充（如果 state 里没有）
    for trait, delta in _safe_dict(affected_dimensions).items():
        if any(_extract_trait_name(it.get("path", "")) == str(trait) for it in items):
            continue
        d = _safe_float(delta, None)
        if d is None:
            continue
        if abs(d) < 1e-9:
            continue
        before_v = _safe_float(before_state.get(trait), None)
        if before_v is None:
            before_v = 0.5
        items.append({
            "path": f"personality.traits.{trait}" if "." not in str(trait) else str(trait),
            "before": before_v,
            "after": round(before_v + d, 5),
            "reason": reason,
        })

    return items


def _canonical_from_canonical(p: dict) -> Dict[str, Any]:
    """A 形态 → canonical（保持字段，补默认值）。"""
    extra: Dict[str, Any] = {}
    for k, v in p.items():
        if k in (
            "id", "proposed_changes", "evidence_ids", "confidence",
            "evaluator_meta", "status", "timestamp",
        ):
            continue
        extra[k] = v

    return _build_canonical(
        id_=_safe_str(p.get("id"), ""),
        proposed_changes=_safe_list(p.get("proposed_changes")),
        evidence_ids=_safe_list(p.get("evidence_ids")),
        confidence=_safe_float(p.get("confidence"), 0.0) or 0.0,
        evaluator_meta=_safe_dict(p.get("evaluator_meta")),
        status=_safe_str(p.get("status"), "proposed") or "proposed",
        timestamp=_ensure_z_suffix(_safe_str(p.get("timestamp"), _now_iso())),
        extra=extra,
    )


def _canonical_from_governance(p: dict) -> Dict[str, Any]:
    """B 形态 → canonical（字段映射 + 保留 B 原数据到 _governance_origin）。"""
    pid = _safe_str(p.get("proposal_id"), "")
    affected = _safe_dict(p.get("affected_dimensions"))
    before_state = _safe_dict(p.get("before_state"))
    after_state = _safe_dict(p.get("after_state"))
    metadata = _safe_dict(p.get("metadata"))
    evaluator_meta_in = _safe_dict(p.get("evaluator_meta"))

    # 合并 metadata + evaluator_meta（evaluator_meta 优先）
    merged_meta: Dict[str, Any] = dict(metadata)
    merged_meta.update(evaluator_meta_in)

    # proposed_changes 由 B 派生
    pcs = _proposed_changes_from_affected(affected, before_state, after_state, merged_meta)

    # 状态映射
    b_status = _safe_str(p.get("status"), "pending") or "pending"
    a_status = STATUS_B_TO_A.get(b_status, "proposed")

    # 时间戳：归一带 Z
    ts = _ensure_z_suffix(_safe_str(p.get("timestamp"), _now_iso()))

    # 把 B 的扩展字段保留到 evaluator_meta（避免覆盖 metadata 已有的同名键）
    for bk, bdefault in (
        ("reviewer_id", ""),
        ("review_comment", ""),
        ("reviewed_at", ""),
        ("applied_at", ""),
        ("applied_by", ""),
        ("expires_at", ""),
        ("priority", ""),
        ("proposal_type", ""),
        ("user_id", ""),
    ):
        v = p.get(bk, bdefault)
        if v not in (None, "", bdefault):
            merged_meta[bk] = v

    # B 的 top-level `source` 仅通过 _governance_origin 保留，不写入 evaluator_meta
    # （因为 metadata.source 才是真正的 source 字段；top-level source 是治理来源标识）
    b_top_source = _safe_str(p.get("source"), "")

    # 接受/拒绝时间（如果 B 里有但 A 元数据没有）
    if b_status in ("approved", "applied") and "accepted_at" not in merged_meta:
        accepted_src = _safe_str(p.get("applied_at"), "") or _safe_str(p.get("reviewed_at"), "")
        if accepted_src:
            merged_meta["accepted_at"] = accepted_src
    if b_status in ("rejected", "cancelled") and "rejected_at" not in merged_meta:
        rejected_src = _safe_str(p.get("reviewed_at"), "")
        if rejected_src:
            merged_meta["rejected_at"] = rejected_src

    # reason 兼容
    if _safe_str(p.get("reason"), "") and "reason" not in merged_meta:
        merged_meta["reason"] = _safe_str(p.get("reason"), "")

    # 把 B 的原字段打包到 _governance_origin（lossless round-trip）
    merged_meta["_governance_origin"] = {
        "proposal_id": pid,
        "affected_dimensions": affected,
        "before_state": before_state,
        "after_state": after_state,
        "evidence": _safe_list(p.get("evidence")),
        "metadata": metadata,
        "reviewer_id": _safe_str(p.get("reviewer_id"), ""),
        "review_comment": _safe_str(p.get("review_comment"), ""),
        "reviewed_at": _safe_str(p.get("reviewed_at"), ""),
        "applied_at": _safe_str(p.get("applied_at"), ""),
        "applied_by": _safe_str(p.get("applied_by"), ""),
        "expires_at": _safe_str(p.get("expires_at"), ""),
        "priority": _safe_str(p.get("priority"), ""),
        "proposal_type": _safe_str(p.get("proposal_type"), ""),
        "source": _safe_str(p.get("source"), ""),
        "user_id": _safe_str(p.get("user_id"), ""),
        "status_b": b_status,
    }

    # source_event_id
    extra: Dict[str, Any] = {}
    sev = _safe_str(p.get("source_event_id"), "")
    if sev:
        extra["source_event_id"] = sev

    return _build_canonical(
        id_=pid,
        proposed_changes=pcs,
        evidence_ids=_safe_list(p.get("evidence")),
        confidence=_safe_float(p.get("confidence"), 0.0) or 0.0,
        evaluator_meta=merged_meta,
        status=a_status,
        timestamp=ts,
        extra=extra,
    )


def _canonical_from_unknown(p: Any) -> Dict[str, Any]:
    """未知形态 → canonical（尽量推断字段，安全失败）。"""
    if not isinstance(p, dict):
        p = {}
    # 找 id
    id_ = (
        _safe_str(p.get("id"), "")
        or _safe_str(p.get("proposal_id"), "")
    )
    # proposed_changes
    pcs = _safe_list(p.get("proposed_changes"))
    # evidence_ids
    ev = _safe_list(p.get("evidence_ids")) or _safe_list(p.get("evidence"))
    # meta
    meta = _safe_dict(p.get("evaluator_meta"))
    if not meta:
        meta = _safe_dict(p.get("metadata"))
    # status
    status = _safe_str(p.get("status"), "proposed") or "proposed"
    # timestamp
    ts = _ensure_z_suffix(_safe_str(p.get("timestamp"), _now_iso()))

    return _build_canonical(
        id_=id_,
        proposed_changes=pcs,
        evidence_ids=ev,
        confidence=_safe_float(p.get("confidence"), 0.0) or 0.0,
        evaluator_meta=meta,
        status=status,
        timestamp=ts,
    )


# ============================================================
# normalize_to_canonical
# ============================================================

def normalize_to_canonical(proposal: Any) -> Dict[str, Any]:
    """
    把任意 dict 转换为 canonical 形态。

    - A 形态 → canonical（直通）
    - B 形态 → canonical（字段映射 + 保留 B 原数据）
    - 未知 / 异常 → safe default canonical

    永不抛异常。
    """
    try:
        if not isinstance(proposal, dict):
            return _canonical_from_unknown(proposal)

        kind = detect(proposal)
        if kind == CANONICAL:
            return _canonical_from_canonical(proposal)
        if kind == GOVERNANCE:
            return _canonical_from_governance(proposal)
        return _canonical_from_unknown(proposal)
    except Exception:
        # 终极兜底
        return _canonical_from_unknown({})


# ============================================================
# governance 输出
# ============================================================

def _governance_from_canonical(c: dict) -> Dict[str, Any]:
    """canonical → governance view。"""
    pid = _safe_str(c.get("id"), "")

    # 优先从 _governance_origin 取原 proposal_id（round-trip 一致性）
    em = _safe_dict(c.get("evaluator_meta"))
    origin = _safe_dict(em.get("_governance_origin"))
    origin_pid = _safe_str(origin.get("proposal_id"), "")
    if origin_pid:
        pid = origin_pid

    # proposed_changes → affected_dimensions
    affected: Dict[str, float] = {}
    before_state: Dict[str, float] = {}
    after_state: Dict[str, float] = {}

    pcs = _safe_list(c.get("proposed_changes"))
    for ci in pcs:
        if not isinstance(ci, dict):
            continue
        path = _extract_trait_name(ci.get("path"))
        if path == "unknown":
            continue
        before_v = _safe_float(ci.get("before"), None)
        after_v = _safe_float(ci.get("after"), None)
        if before_v is None and after_v is None:
            continue
        if before_v is None:
            before_v = 0.5
        if after_v is None:
            after_v = before_v
        delta = round(after_v - before_v, 5)
        if abs(delta) < 1e-9:
            continue
        affected[path] = delta
        before_state[path] = before_v
        after_state[path] = after_v

    # evidence_ids
    evidence_ids = _safe_list(c.get("evidence_ids"))

    # metadata：从 evaluator_meta 派生（去掉 _governance_origin / 内部 _key）
    metadata: Dict[str, Any] = {}
    for k, v in em.items():
        if k.startswith("_"):
            # _governance_origin / _status 等内部字段不进 metadata
            if k == "_status" or k == "_status_canonical":
                # 保留 _status 映射
                pass
            continue
        metadata[k] = v

    # 把 before/after_state 放进 metadata（lossless 留存）
    if before_state:
        metadata["_before_state"] = before_state
    if after_state:
        metadata["_after_state"] = after_state

    # 状态映射
    a_status = _safe_str(c.get("status"), "proposed")
    b_status = STATUS_A_TO_B.get(a_status, "pending")

    # 如果有 _governance_origin 里的原 status，优先用
    origin_status_b = _safe_str(origin.get("status_b"), "")
    if origin_status_b:
        b_status = origin_status_b

    # reason
    reason = _safe_str(metadata.get("reason"), "")
    if not reason and isinstance(em.get("evaluator_meta"), dict):
        reason = _safe_str(em.get("evaluator_meta", {}).get("reason_summary"), "")

    out: Dict[str, Any] = {
        "proposal_id": pid,
        "affected_dimensions": affected,
        "evidence": evidence_ids,
        "metadata": metadata,
        "status": b_status,
        "confidence": _safe_float(c.get("confidence"), 0.0) or 0.0,
    }
    if reason:
        out["reason"] = reason

    # 如果 _governance_origin 有其他字段，回填
    for k in (
        "source_event_id", "reviewer_id", "review_comment", "reviewed_at",
        "applied_at", "applied_by", "expires_at", "priority",
        "proposal_type", "source", "user_id",
    ):
        v = origin.get(k, "")
        if v not in (None, ""):
            out[k] = v

    # 透传 source_event_id
    sev = _safe_str(c.get("source_event_id"), "")
    if sev and "source_event_id" not in out:
        out["source_event_id"] = sev

    return out


def _governance_from_governance(p: dict) -> Dict[str, Any]:
    """B 形态 → governance（直通，确保字段完整）。"""
    out: Dict[str, Any] = {
        "proposal_id": _safe_str(p.get("proposal_id"), ""),
        "affected_dimensions": _safe_dict(p.get("affected_dimensions")),
        "evidence": _safe_list(p.get("evidence")),
        "metadata": _safe_dict(p.get("metadata")),
        "status": _safe_str(p.get("status"), "pending") or "pending",
    }
    # 透传 reason
    reason = _safe_str(p.get("reason"), "")
    if reason:
        out["reason"] = reason
    # 透传 B 扩展字段
    for k in (
        "source_event_id", "timestamp", "before_state", "after_state",
        "user_id", "source", "priority", "reviewer_id", "review_comment",
        "reviewed_at", "applied_at", "applied_by", "expires_at",
        "proposal_type", "confidence",
    ):
        v = p.get(k)
        if v is not None and v != "":
            out[k] = v
    # 补 timestamp
    if "timestamp" not in out:
        out["timestamp"] = _ensure_z_suffix(_safe_str(p.get("timestamp"), _now_iso()))
    return out


def _governance_from_unknown(p: Any) -> Dict[str, Any]:
    """未知形态 → governance（从任意 dict 抽取治理字段）。"""
    if not isinstance(p, dict):
        p = {}
    pid = _safe_str(p.get("proposal_id"), "") or _safe_str(p.get("id"), "")
    # 尝试从 proposed_changes 推导
    pcs = _safe_list(p.get("proposed_changes"))
    affected: Dict[str, float] = {}
    before_state: Dict[str, float] = {}
    after_state: Dict[str, float] = {}
    for ci in pcs:
        if not isinstance(ci, dict):
            continue
        path = _extract_trait_name(ci.get("path"))
        if path == "unknown":
            continue
        before_v = _safe_float(ci.get("before"), None)
        after_v = _safe_float(ci.get("after"), None)
        if before_v is None and after_v is None:
            continue
        if before_v is None:
            before_v = 0.5
        if after_v is None:
            after_v = before_v
        delta = round(after_v - before_v, 5)
        if abs(delta) < 1e-9:
            continue
        affected[path] = delta
        before_state[path] = before_v
        after_state[path] = after_v

    evidence = _safe_list(p.get("evidence")) or _safe_list(p.get("evidence_ids"))
    metadata = _safe_dict(p.get("metadata"))
    if not metadata:
        metadata = _safe_dict(p.get("evaluator_meta"))

    out: Dict[str, Any] = {
        "proposal_id": pid,
        "affected_dimensions": affected,
        "evidence": evidence,
        "metadata": metadata,
        "status": _safe_str(p.get("status"), "pending") or "pending",
    }
    if before_state:
        out["before_state"] = before_state
    if after_state:
        out["after_state"] = after_state
    return out


# ============================================================
# to_governance_view
# ============================================================

def to_governance_view(proposal: Any) -> Dict[str, Any]:
    """
    把任意 dict 转换为 governance view 形态。

    - canonical → governance（派生 affected_dimensions + 保留 _governance_origin）
    - governance → governance（直通 + 字段补全）
    - 未知 → governance（尽量抽取）

    永不抛异常。
    """
    try:
        if not isinstance(proposal, dict):
            return _governance_from_unknown(proposal)

        kind = detect(proposal)
        if kind == CANONICAL:
            return _governance_from_canonical(proposal)
        if kind == GOVERNANCE:
            return _governance_from_governance(proposal)
        return _governance_from_unknown(proposal)
    except Exception:
        return _governance_from_unknown({})


# ============================================================
# 便捷别名（类风格接口）
# ============================================================

class GrowthProposalNormalizer:
    """
    GrowthProposalNormalizer —— 双 Schema 转换层（lossless）。

    用法：
        normalizer = GrowthProposalNormalizer()
        kind = normalizer.detect(proposal)
        canonical = normalizer.normalize_to_canonical(proposal)
        governance = normalizer.to_governance_view(proposal)

    所有方法为静态接口；保留类形式仅为上层调用便利与未来扩展。
    """

    @staticmethod
    def detect(proposal: Any) -> str:
        return detect(proposal)

    @staticmethod
    def normalize_to_canonical(proposal: Any) -> Dict[str, Any]:
        return normalize_to_canonical(proposal)

    @staticmethod
    def to_governance_view(proposal: Any) -> Dict[str, Any]:
        return to_governance_view(proposal)

    # ============================================================
    # 便捷方法
    # ============================================================

    @staticmethod
    def is_canonical(proposal: Any) -> bool:
        return detect(proposal) == CANONICAL

    @staticmethod
    def is_governance(proposal: Any) -> bool:
        return detect(proposal) == GOVERNANCE

    @staticmethod
    def is_unknown(proposal: Any) -> bool:
        return detect(proposal) == UNKNOWN

    @staticmethod
    def has_required_canonical_fields(canonical: Dict[str, Any]) -> bool:
        """检查 canonical dict 是否包含 7 个核心字段。"""
        if not isinstance(canonical, dict):
            return False
        required = (
            "id", "proposed_changes", "evidence_ids", "confidence",
            "evaluator_meta", "status", "timestamp",
        )
        return all(k in canonical for k in required)


# ============================================================
# 公开 API
# ============================================================

__all__ = [
    "GrowthProposalNormalizer",
    "detect",
    "normalize_to_canonical",
    "to_governance_view",
    "CANONICAL",
    "GOVERNANCE",
    "UNKNOWN",
    "STATUS_B_TO_A",
    "STATUS_A_TO_B",
]
