"""
Phase 6.3: Runtime SelfModel Audit

职责：
- 记录 SelfModel 读/写事件，提供可审计的历史
- 不阻塞主流程（异常隔离）
- 复用 src.audit 基础设施

行为契约：
- record_read(timestamp, source, context_type) → AuditRecord
- record_write(timestamp, source, proposal_id, reason, confidence) → AuditRecord
- query_self_model_audit(filters) → List[AuditRecord]

设计原则：
- SelfModel 读：每次读取（get_context / get_combined_view）都应留痕
- SelfModel 写：每次 write（apply_pcr / apply_external_change / rollback）都应留痕
- 目的：未来能回答"为什么羽依现在认为自己这样？"
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.audit.record import record_audit_log, AuditRecord

logger = logging.getLogger(__name__)


# ============================================================
# SelfModel Audit 常量
# ============================================================

# operation_type 命名空间
SELF_MODEL_READ: str = "self_model.read"
SELF_MODEL_WRITE: str = "self_model.write"
SELF_MODEL_PCR_APPLIED: str = "self_model.pcr_applied"
SELF_MODEL_EXTERNAL_CHANGE: str = "self_model.external_change"
SELF_MODEL_ROLLBACK: str = "self_model.rollback"
SELF_MODEL_BOOTSTRAP: str = "self_model.bootstrap"

# source 标识
SOURCE_ORCHESTRATOR: str = "orchestrator"
SOURCE_RUNTIME: str = "runtime_core"
SOURCE_ADAPTER: str = "self_model_adapter"
SOURCE_PERSISTENCE: str = "self_model_persistence"
SOURCE_GUARDIAN: str = "self_model_guardian"
SOURCE_SYNC: str = "self_model_sync_adapter"
SOURCE_CONTEXT_PROVIDER: str = "self_model_context_provider"


# ============================================================
# 读事件审计
# ============================================================

def record_self_model_read(
    source: str = SOURCE_CONTEXT_PROVIDER,
    context_type: str = "combined",
    detail: Optional[Dict[str, Any]] = None,
    result: str = "success",
    error_message: str = "",
) -> Optional[AuditRecord]:
    """
    记录 SelfModel 读事件。

    Args:
        source: 读取来源（orchestrator / context_provider / runtime）
        context_type: 读取的上下文类型（legacy / combined / runtime / beliefs_only / ...）
        detail: 额外信息（如返回的 beliefs 数量、history 数量）
        result: success / failure
        error_message: 错误信息（若有）

    Returns:
        AuditRecord 或 None（失败隔离）
    """
    try:
        return record_audit_log(
            operation_type=SELF_MODEL_READ,
            source=source,
            action=f"read_{context_type}",
            detail=detail or {},
            result=result,
            error_message=error_message,
        )
    except Exception as e:
        logger.warning(f"record_self_model_read 失败: {e}")
        return None


# ============================================================
# 写事件审计
# ============================================================

def record_self_model_write(
    source: str = SOURCE_ADAPTER,
    proposal_id: str = "",
    reason: str = "",
    confidence: float = 0.5,
    detail: Optional[Dict[str, Any]] = None,
    result: str = "success",
    error_message: str = "",
) -> Optional[AuditRecord]:
    """
    记录 SelfModel 写事件（apply_pcr / apply_external_change）。

    Args:
        source: 写来源（adapter / resolver / emotion / growth）
        proposal_id: 关联的 GrowthProposal ID（若有）
        reason: 修改原因
        confidence: 置信度 0~1
        detail: 额外信息（affected_traits / beliefs_added / ...）
        result: success / failure
        error_message: 错误信息（若有）

    Returns:
        AuditRecord 或 None
    """
    try:
        return record_audit_log(
            operation_type=SELF_MODEL_WRITE,
            source=source,
            action="write",
            detail={
                "proposal_id": proposal_id,
                "reason": reason,
                "confidence": float(confidence),
                **(detail or {}),
            },
            result=result,
            error_message=error_message,
        )
    except Exception as e:
        logger.warning(f"record_self_model_write 失败: {e}")
        return None


def record_pcr_applied(
    proposal_id: str,
    pcr_id: str,
    reason: str,
    confidence: float,
    beliefs_added: int = 0,
    beliefs_reinforced: int = 0,
    affected_traits: Optional[Dict[str, float]] = None,
    result: str = "success",
    error_message: str = "",
) -> Optional[AuditRecord]:
    """记录 PCR 应用的审计"""
    try:
        return record_audit_log(
            operation_type=SELF_MODEL_PCR_APPLIED,
            source=SOURCE_ADAPTER,
            action="pcr_applied",
            detail={
                "proposal_id": proposal_id,
                "pcr_id": pcr_id,
                "reason": reason,
                "confidence": float(confidence),
                "beliefs_added": beliefs_added,
                "beliefs_reinforced": beliefs_reinforced,
                "affected_traits": dict(affected_traits or {}),
            },
            result=result,
            error_message=error_message,
        )
    except Exception as e:
        logger.warning(f"record_pcr_applied 失败: {e}")
        return None


def record_external_change(
    change_type: str,
    source: str,
    reason: str,
    confidence: float,
    proposal_id: str = "",
    beliefs_added: int = 0,
    reflections_added: int = 0,
    result: str = "success",
    error_message: str = "",
) -> Optional[AuditRecord]:
    """记录外部修改事件的审计"""
    try:
        return record_audit_log(
            operation_type=SELF_MODEL_EXTERNAL_CHANGE,
            source=source,
            action=f"external_change_{change_type}",
            detail={
                "proposal_id": proposal_id,
                "reason": reason,
                "confidence": float(confidence),
                "beliefs_added": beliefs_added,
                "reflections_added": reflections_added,
            },
            result=result,
            error_message=error_message,
        )
    except Exception as e:
        logger.warning(f"record_external_change 失败: {e}")
        return None


def record_rollback(
    snapshot_id: str,
    reason: str,
    actor: str = "system",
    result: str = "success",
    error_message: str = "",
) -> Optional[AuditRecord]:
    """记录 rollback 审计"""
    try:
        return record_audit_log(
            operation_type=SELF_MODEL_ROLLBACK,
            source=SOURCE_ADAPTER,
            action="rollback",
            detail={
                "snapshot_id": snapshot_id,
                "reason": reason,
                "actor": actor,
            },
            result=result,
            error_message=error_message,
        )
    except Exception as e:
        logger.warning(f"record_rollback 失败: {e}")
        return None


def record_bootstrap(
    data_dir: str,
    load_counts: Dict[str, int],
    errors: Optional[List[str]] = None,
    result: str = "success",
) -> Optional[AuditRecord]:
    """记录 bootstrap 审计"""
    try:
        return record_audit_log(
            operation_type=SELF_MODEL_BOOTSTRAP,
            source=SOURCE_RUNTIME,
            action="bootstrap",
            detail={
                "data_dir": data_dir,
                "load_counts": dict(load_counts or {}),
                "errors": list(errors or []),
            },
            result=result,
        )
    except Exception as e:
        logger.warning(f"record_bootstrap 失败: {e}")
        return None


# ============================================================
# 查询接口
# ============================================================

def query_self_model_audit(
    operation_type: Optional[str] = None,
    source: Optional[str] = None,
    proposal_id: Optional[str] = None,
    limit: int = 100,
) -> List[AuditRecord]:
    """
    查询 SelfModel 审计记录。

    Args:
        operation_type: 过滤 operation_type（self_model.read / self_model.write / ...）
        source: 过滤 source
        proposal_id: 过滤关联的 proposal_id
        limit: 最多返回条数

    Returns:
        List[AuditRecord]
    """
    try:
        from src.audit.storage import load_audit_records
        all_records = load_audit_records() or []
        filtered: List[AuditRecord] = []
        for rec in all_records:
            if operation_type and rec.operation_type != operation_type:
                continue
            if operation_type and rec.operation_type.startswith("self_model.") is False:
                continue
            if source and rec.source != source:
                continue
            if proposal_id:
                # proposal_id 在 detail.proposal_id
                rec_pid = (rec.detail or {}).get("proposal_id", "")
                if rec_pid != proposal_id:
                    continue
            filtered.append(rec)
        # 按 timestamp desc
        try:
            filtered.sort(key=lambda r: r.timestamp, reverse=True)
        except Exception:
            pass
        return filtered[:limit]
    except Exception as e:
        logger.warning(f"query_self_model_audit 失败: {e}")
        return []


def get_self_model_audit_summary(limit: int = 50) -> Dict[str, Any]:
    """
    返回 SelfModel 审计摘要（用于 health check / 监控）。

    Returns:
        {
            "total_records": int,
            "by_operation": Dict[str, int],
            "by_source": Dict[str, int],
            "recent_records": List[Dict],
        }
    """
    try:
        records = query_self_model_audit(limit=limit * 5)
        by_op: Dict[str, int] = {}
        by_src: Dict[str, int] = {}
        for rec in records:
            by_op[rec.operation_type] = by_op.get(rec.operation_type, 0) + 1
            by_src[rec.source] = by_src.get(rec.source, 0) + 1
        recent = [
            {
                "operation_type": rec.operation_type,
                "source": rec.source,
                "action": rec.action,
                "timestamp": rec.timestamp,
                "result": rec.result,
                "detail": rec.detail,
            }
            for rec in records[:limit]
        ]
        return {
            "total_records": len(records),
            "by_operation": by_op,
            "by_source": by_src,
            "recent_records": recent,
        }
    except Exception as e:
        return {
            "error": str(e),
            "total_records": 0,
            "by_operation": {},
            "by_source": {},
            "recent_records": [],
        }


# ============================================================
# 便捷函数：回答"为什么羽依现在认为自己这样？"
# ============================================================

def trace_self_model_evolution(
    proposal_id: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    追溯 SelfModel 演化历史（用于解释"为什么羽依这样想"）。

    Returns:
        时间倒序的事件列表
    """
    try:
        records = query_self_model_audit(
            operation_type=None,  # 全部 self_model.*
            proposal_id=proposal_id,
            limit=limit,
        )
        out: List[Dict[str, Any]] = []
        for rec in records:
            out.append({
                "record_id": rec.record_id,
                "timestamp": rec.timestamp,
                "operation_type": rec.operation_type,
                "source": rec.source,
                "action": rec.action,
                "result": rec.result,
                "proposal_id": (rec.detail or {}).get("proposal_id", ""),
                "reason": (rec.detail or {}).get("reason", ""),
                "confidence": (rec.detail or {}).get("confidence", 0.0),
                "affected_traits": (rec.detail or {}).get("affected_traits", {}),
                "beliefs_added": (rec.detail or {}).get("beliefs_added", 0),
                "beliefs_reinforced": (rec.detail or {}).get("beliefs_reinforced", 0),
            })
        return out
    except Exception as e:
        logger.warning(f"trace_self_model_evolution 失败: {e}")
        return []


# ============================================================
# Phase 6.4 Audit Enhancement
# ============================================================
#
# 新增三个核心追溯能力：
# 1. build_evolution_timeline(start, end) — 跨 belief/history/reflection/audit 的统一时间线
# 2. trace_belief_origin(belief_id, history, beliefs) — 单条 belief 的完整来源链
# 3. find_pcr_related_events(proposal_id, ...) — 通过 PCR 关联所有相关事件
#
# 目的：回答 "为什么羽依现在认为自己喜欢安静？"
# 返回：belief → source → proposal_id → PCR → history


# 新增的 operation_type 命名空间
SELF_MODEL_EVOLUTION_TIMELINE: str = "self_model.evolution_timeline"
SELF_MODEL_BELIEF_TRACE: str = "self_model.belief_trace"
SELF_MODEL_PCR_LINK: str = "self_model.pcr_link"


# ============================================================
# Evolution Timeline
# ============================================================

def build_evolution_timeline(
    beliefs_store: Any = None,
    history: Any = None,
    reflections_store: Any = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    构建 SelfModel 演化时间线。

    聚合：
    - SelfBelief（add / reinforce）
    - SelfHistory（pcr_applied / snapshot_created / rollback / ...）
    - SelfReflectionNote
    - Audit 记录（self_model.*）

    按 timestamp 升序合并，可选时间窗口过滤。

    Returns:
        [
            {
                "timestamp": str,
                "source": "belief" | "history" | "reflection" | "audit",
                "event_type": str,
                "summary": str,
                "id": str,
                "metadata": dict
            },
            ...
        ]
    """
    timeline: List[Dict[str, Any]] = []
    try:
        # beliefs
        if beliefs_store is not None and hasattr(beliefs_store, "all"):
            for b in beliefs_store.all():
                try:
                    ts = getattr(b, "first_seen", "")
                    if not ts:
                        continue
                    if start and ts < start:
                        continue
                    if end and ts > end:
                        continue
                    timeline.append({
                        "timestamp": ts,
                        "source": "belief",
                        "event_type": "self_belief_added" if b.version == 1 else "self_belief_reinforced",
                        "summary": f"belief: {getattr(b, 'content', '')[:60]}",
                        "id": getattr(b, "belief_id", ""),
                        "metadata": {
                            "domain": getattr(b, "domain", ""),
                            "confidence": float(getattr(b, "confidence", 0.0) or 0.0),
                            "version": getattr(b, "version", 1),
                            "sources": list(getattr(b, "sources", []) or []),
                        },
                    })
                except Exception:
                    continue

        # history
        if history is not None and hasattr(history, "all"):
            for ev in history.all():
                try:
                    ts = getattr(ev, "timestamp", "")
                    if not ts:
                        continue
                    if start and ts < start:
                        continue
                    if end and ts > end:
                        continue
                    timeline.append({
                        "timestamp": ts,
                        "source": "history",
                        "event_type": getattr(ev, "event_type", ""),
                        "summary": getattr(ev, "summary", "")[:80],
                        "id": getattr(ev, "event_id", ""),
                        "metadata": {
                            "source_type": getattr(ev, "source_type", ""),
                            "source_id": getattr(ev, "source_id", ""),
                            "affected_traits": dict(getattr(ev, "affected_traits", {}) or {}),
                            "affected_beliefs": list(getattr(ev, "affected_beliefs", []) or []),
                            "actor": getattr(ev, "actor", ""),
                        },
                    })
                except Exception:
                    continue

        # reflections
        if reflections_store is not None and hasattr(reflections_store, "all"):
            for n in reflections_store.all():
                try:
                    ts = getattr(n, "timestamp", "")
                    if not ts:
                        continue
                    if start and ts < start:
                        continue
                    if end and ts > end:
                        continue
                    timeline.append({
                        "timestamp": ts,
                        "source": "reflection",
                        "event_type": f"reflection:{getattr(n, 'reflection_type', '')}",
                        "summary": getattr(n, "content", "")[:80],
                        "id": getattr(n, "note_id", ""),
                        "metadata": {
                            "trigger_source": getattr(n, "trigger_source", ""),
                            "related_belief_ids": list(getattr(n, "related_belief_ids", []) or []),
                            "confidence": float(getattr(n, "confidence", 0.0) or 0.0),
                        },
                    })
                except Exception:
                    continue

        # audit (self_model.*)
        try:
            audit_records = query_self_model_audit(limit=limit * 2)
            for rec in audit_records:
                try:
                    ts = getattr(rec, "timestamp", "")
                    if not ts:
                        continue
                    if start and ts < start:
                        continue
                    if end and ts > end:
                        continue
                    timeline.append({
                        "timestamp": ts,
                        "source": "audit",
                        "event_type": getattr(rec, "operation_type", ""),
                        "summary": getattr(rec, "action", "")[:80],
                        "id": getattr(rec, "record_id", ""),
                        "metadata": {
                            "proposal_id": (rec.detail or {}).get("proposal_id", ""),
                            "reason": (rec.detail or {}).get("reason", ""),
                            "result": getattr(rec, "result", ""),
                        },
                    })
                except Exception:
                    continue
        except Exception:
            pass

        # 排序：按 timestamp 升序
        try:
            timeline.sort(key=lambda x: x.get("timestamp", ""))
        except Exception:
            pass

        # 截断
        return timeline[:limit]
    except Exception as e:
        logger.warning(f"build_evolution_timeline 失败: {e}")
        return []


# ============================================================
# Belief Source Trace
# ============================================================

def trace_belief_origin(
    belief_id: str,
    beliefs_store: Any = None,
    history: Any = None,
) -> Dict[str, Any]:
    """
    追溯单条 belief 的完整来源链。

    回答"为什么羽依现在认为自己这样？"。

    追溯流程：
        belief
          ↓
        sources (proposal_id, pcr_id, ...)
          ↓
        history 事件 (filter source_id in sources)
          ↓
        返回链

    Returns:
        {
            "belief_id": str,
            "belief_content": str,
            "belief_confidence": float,
            "belief_version": int,
            "sources": List[str],          # 来源 ID
            "linked_events": List[Dict],   # 关联 history 事件
            "explanation": str,            # 自然语言解释
        }
    """
    result: Dict[str, Any] = {
        "belief_id": belief_id,
        "belief_content": "",
        "belief_confidence": 0.0,
        "belief_version": 1,
        "sources": [],
        "linked_events": [],
        "explanation": "",
    }
    try:
        # 1. 找到 belief
        if beliefs_store is None or not hasattr(beliefs_store, "all"):
            result["explanation"] = "无法访问 beliefs store"
            return result
        target = None
        for b in beliefs_store.all():
            if getattr(b, "belief_id", "") == belief_id:
                target = b
                break
        if target is None:
            result["explanation"] = f"未找到 belief: {belief_id}"
            return result
        result["belief_content"] = getattr(target, "content", "")
        result["belief_confidence"] = float(getattr(target, "confidence", 0.0) or 0.0)
        result["belief_version"] = int(getattr(target, "version", 1))
        sources = list(getattr(target, "sources", []) or [])
        result["sources"] = sources

        # 2. 在 history 中查找相关事件
        if history is not None and hasattr(history, "all") and sources:
            for ev in history.all():
                try:
                    src_id = getattr(ev, "source_id", "")
                    if src_id and src_id in sources:
                        result["linked_events"].append({
                            "event_id": getattr(ev, "event_id", ""),
                            "timestamp": getattr(ev, "timestamp", ""),
                            "event_type": getattr(ev, "event_type", ""),
                            "source_id": src_id,
                            "summary": getattr(ev, "summary", "")[:80],
                            "affected_traits": dict(getattr(ev, "affected_traits", {}) or {}),
                            "actor": getattr(ev, "actor", ""),
                        })
                except Exception:
                    continue

        # 3. 构造自然语言解释
        if not sources:
            result["explanation"] = "该 belief 没有来源记录（可能是手动创建或迁移）"
        elif not result["linked_events"]:
            result["explanation"] = (
                f"该 belief 来自 {len(sources)} 个来源，"
                f"但未在 history 中找到对应事件（来源可能为 {', '.join(sources[:3])}）"
            )
        else:
            event_types = set(e["event_type"] for e in result["linked_events"])
            result["explanation"] = (
                f"该 belief 由 {len(result['linked_events'])} 个 history 事件强化，"
                f"事件类型: {', '.join(sorted(event_types))}。"
                f"当前 confidence={result['belief_confidence']:.2f}, version={result['belief_version']}。"
            )
    except Exception as e:
        result["explanation"] = f"追溯失败: {e}"
        logger.warning(f"trace_belief_origin 失败: {e}")
    return result


# ============================================================
# PCR Link Query
# ============================================================

def find_pcr_related_events(
    proposal_id: str,
    beliefs_store: Any = None,
    history: Any = None,
    reflections_store: Any = None,
    include_audit: bool = True,
) -> Dict[str, Any]:
    """
    查找与指定 proposal_id 相关的所有事件。

    PCR（PersonalityChangeRequest）→ SelfModel 的关联。

    关联路径：
        proposal_id
          ↓
        history (source_id == proposal_id)
          ↓
        belief (sources contains proposal_id)
          ↓
        reflection (related_belief_ids or sources)
          ↓
        audit (detail.proposal_id)

    Returns:
        {
            "proposal_id": str,
            "history_events": List[Dict],
            "linked_beliefs": List[Dict],
            "linked_reflections": List[Dict],
            "audit_records": List[Dict],
            "summary": Dict,
        }
    """
    result: Dict[str, Any] = {
        "proposal_id": proposal_id,
        "history_events": [],
        "linked_beliefs": [],
        "linked_reflections": [],
        "audit_records": [],
        "summary": {
            "history_count": 0,
            "belief_count": 0,
            "reflection_count": 0,
            "audit_count": 0,
        },
    }
    if not proposal_id:
        return result
    try:
        # 1. history 事件
        if history is not None and hasattr(history, "all"):
            for ev in history.all():
                try:
                    src_id = getattr(ev, "source_id", "")
                    metadata = getattr(ev, "metadata", {}) or {}
                    pcr_id = metadata.get("pcr_id", "") if isinstance(metadata, dict) else ""
                    if src_id == proposal_id or pcr_id == proposal_id:
                        result["history_events"].append({
                            "event_id": getattr(ev, "event_id", ""),
                            "timestamp": getattr(ev, "timestamp", ""),
                            "event_type": getattr(ev, "event_type", ""),
                            "source_id": src_id,
                            "pcr_id": pcr_id,
                            "affected_traits": dict(getattr(ev, "affected_traits", {}) or {}),
                            "affected_beliefs": list(getattr(ev, "affected_beliefs", []) or []),
                            "summary": getattr(ev, "summary", "")[:100],
                        })
                except Exception:
                    continue
        result["summary"]["history_count"] = len(result["history_events"])

        # 2. beliefs
        if beliefs_store is not None and hasattr(beliefs_store, "all"):
            for b in beliefs_store.all():
                try:
                    sources = list(getattr(b, "sources", []) or [])
                    if proposal_id in sources:
                        result["linked_beliefs"].append({
                            "belief_id": getattr(b, "belief_id", ""),
                            "content": getattr(b, "content", "")[:100],
                            "domain": getattr(b, "domain", ""),
                            "confidence": float(getattr(b, "confidence", 0.0) or 0.0),
                            "version": getattr(b, "version", 1),
                            "active": bool(getattr(b, "active", True)),
                            "sources": sources,
                            "first_seen": getattr(b, "first_seen", ""),
                        })
                except Exception:
                    continue
        result["summary"]["belief_count"] = len(result["linked_beliefs"])

        # 3. reflections
        if reflections_store is not None and hasattr(reflections_store, "all"):
            # 先收集 belief_ids（来自 history）
            belief_ids = set()
            for ev in result["history_events"]:
                belief_ids.update(ev.get("affected_beliefs", []))
            belief_ids.update(b.get("belief_id", "") for b in result["linked_beliefs"])

            for n in reflections_store.all():
                try:
                    related = list(getattr(n, "related_belief_ids", []) or [])
                    sources = list(getattr(n, "sources", []) or [])
                    if proposal_id in sources or any(bid in belief_ids for bid in related):
                        result["linked_reflections"].append({
                            "note_id": getattr(n, "note_id", ""),
                            "timestamp": getattr(n, "timestamp", ""),
                            "trigger_source": getattr(n, "trigger_source", ""),
                            "reflection_type": getattr(n, "reflection_type", ""),
                            "content": getattr(n, "content", "")[:100],
                            "related_belief_ids": related,
                            "confidence": float(getattr(n, "confidence", 0.0) or 0.0),
                        })
                except Exception:
                    continue
        result["summary"]["reflection_count"] = len(result["linked_reflections"])

        # 4. audit
        if include_audit:
            try:
                audit_records = query_self_model_audit(proposal_id=proposal_id, limit=100)
                for rec in audit_records:
                    result["audit_records"].append({
                        "record_id": getattr(rec, "record_id", ""),
                        "timestamp": getattr(rec, "timestamp", ""),
                        "operation_type": getattr(rec, "operation_type", ""),
                        "action": getattr(rec, "action", ""),
                        "result": getattr(rec, "result", ""),
                        "reason": (rec.detail or {}).get("reason", "")[:80],
                    })
            except Exception:
                pass
        result["summary"]["audit_count"] = len(result["audit_records"])
    except Exception as e:
        logger.warning(f"find_pcr_related_events 失败: {e}")
    return result


def explain_why_belief(
    belief_id: str,
    beliefs_store: Any = None,
    history: Any = None,
    reflections_store: Any = None,
) -> Dict[str, Any]:
    """
    高级封装：解释"为什么羽依现在认为自己这样"。

    流程：
        belief_id → trace_belief_origin → 找到 proposal_id
                    ↓
                    → find_pcr_related_events → 完整 PCR 上下文

    Returns:
        {
            "belief": {...},
            "origin": {...},
            "pcr_link": {...},
            "answer": str,   # 自然语言回答
        }
    """
    result: Dict[str, Any] = {
        "belief": None,
        "origin": None,
        "pcr_link": None,
        "answer": "",
    }
    try:
        origin = trace_belief_origin(belief_id, beliefs_store, history)
        result["origin"] = origin
        result["belief"] = {
            "belief_id": origin.get("belief_id", ""),
            "content": origin.get("belief_content", ""),
            "confidence": origin.get("belief_confidence", 0.0),
            "version": origin.get("belief_version", 1),
            "sources": origin.get("sources", []),
        }

        # 找第一个看起来像 proposal_id 的来源
        proposal_id = ""
        for src in origin.get("sources", []):
            if src and src.startswith(("prop_", "pcr_", "prp_", "gp_")):
                proposal_id = src
                break
        if not proposal_id and origin.get("sources"):
            proposal_id = origin["sources"][0]

        if proposal_id:
            pcr_link = find_pcr_related_events(
                proposal_id, beliefs_store, history, reflections_store, include_audit=True
            )
            result["pcr_link"] = pcr_link
            history_count = pcr_link["summary"]["history_count"]
            belief_count = pcr_link["summary"]["belief_count"]
            result["answer"] = (
                f"羽依的这条信念（{origin.get('belief_content', '')[:50]}）"
                f"由 {history_count} 个 history 事件和 {belief_count} 条相关 belief 支撑，"
                f"根源是 proposal {proposal_id}。"
                f"当前 confidence={origin.get('belief_confidence', 0.0):.2f}。"
            )
        else:
            result["answer"] = origin.get("explanation", "无来源信息")
    except Exception as e:
        result["answer"] = f"解释失败: {e}"
        logger.warning(f"explain_why_belief 失败: {e}")
    return result
