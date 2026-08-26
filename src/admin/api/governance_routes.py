# -*- coding: utf-8 -*-
"""Governance API（v1.5.5 Governance C2-d）—— 羽依身份/关系/自我模型治理端点。

复用 admin_bp + X-Admin-Token 权限。所有写操作走治理层：
    candidate → confirm/reject/modify/hold → confirmed/superseded
LLM/系统永远不能绕过本 API 直接写 confirmed。

端点：
    GET  /api/governance/candidates             列表（status/source_type 筛选）
    POST /api/governance/candidates             人工提交候选
    POST /api/governance/candidates/<id>/review 审核（confirm/reject/modify/hold）
    GET  /api/governance/relationship-core      confirmed 关系事实
    POST /api/governance/relationship-core/<id>/supersede  冲突解决
    GET  /api/governance/self-model-statements  stated statements
    GET  /api/governance/memory-search          记忆检索（证据回跳）
    GET  /api/governance/audit-log              审计日志
"""
from __future__ import annotations

import functools
import hmac
import json
import logging
import os
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

gov_bp = Blueprint("governance", __name__)  # prefix 在注册时给定 /admin/api/governance

#: memory-search 元数据投影键（2026-08-27 修复：加入 reply——
#: 此前投影仅 3 键导致 assistant 回复在观测 API 中"看似丢失"，造成误判）
_MEMORY_META_KEYS = ("frontend", "source", "memory_type", "reply")


def _admin_token_required(view_func):
    """治理端点鉴权（fail-closed，与 api_server 的 admin 鉴权同语义）。

    P0 止血（v2.0）：移除 Tailscale/CGNAT 网段兜底放行——
      "能访问 Tailscale" 不再等价于 "管理员身份"。

    可信来源：
      1. 已配置 token（YUYI_ADMIN_TOKEN / config.yaml admin.token）且请求携带匹配
         X-Admin-Token / Authorization: Bearer
      2. 未配置 token 时，仅本机回环放行（其余来源 403，含 CGNAT）
    其余来源一律 401/403。
    """

    @functools.wraps(view_func)
    def _wrapped(*args, **kwargs):
        try:
            from api_server import _get_admin_token, _is_loopback_client
            token = _get_admin_token()
        except Exception:  # noqa: BLE001
            token = ""

        if not token:
            try:
                if _is_loopback_client():
                    return view_func(*args, **kwargs)
            except Exception:  # noqa: BLE001
                pass
            return jsonify({
                "error": "治理端点未配置访问 token，仅允许本机访问",
                "hint": "设置环境变量 YUYI_ADMIN_TOKEN（或 config.yaml admin.token）后，"
                        "携带 X-Admin-Token 请求头访问",
            }), 403
        provided = (request.headers.get("X-Admin-Token") or "").strip()
        if not provided:
            auth = (request.headers.get("Authorization") or "").strip()
            if auth.startswith("Bearer "):
                provided = auth[len("Bearer "):].strip()
        if not provided or not hmac.compare_digest(provided, token):
            return jsonify({"error": "未授权：缺失或无效的管理 token"}), 401
        return view_func(*args, **kwargs)
    return _wrapped


def _store_fact_candidates():
    from src.governance.fact_candidate import FactCandidateStore
    return FactCandidateStore()


def _store_self_model_statements():
    from src.governance.self_model_statements import SelfModelStatementsStore
    return SelfModelStatementsStore()


def _store_relationship_core():
    from src.relationship.relationship_core_store import RelationshipCoreStore
    return RelationshipCoreStore()


def _audit(action: str, obj_type: str, obj_id: str, before: Any, after: Any, reason: str, reviewer: str):
    """治理审计（append-only JSONL：data/governance/audit_log.jsonl）。"""
    try:
        path = os.path.join("data", "governance", "audit_log.jsonl")
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        from datetime import datetime
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "reviewer": reviewer or "unknown",
            "action": action,
            "object_type": obj_type,
            "object_id": obj_id,
            "before": before,
            "after": after,
            "reason": reason or "",
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Governance audit 写入失败: %s", exc)


# ============ Fact Candidates ============
@gov_bp.route("/candidates", methods=["GET"])
@_admin_token_required
def list_candidates():
    try:
        store = _store_fact_candidates()
        # GOV-009: 返回族级投影（current_status/reviewed/target），前端只显示投影，
        # 不得自行从原始 status 猜业务状态。
        projections = store.list_projections()
        # 兼容筛选参数（基于投影字段）
        status = request.args.get("status")
        source_type = request.args.get("source_type")
        if status or source_type:
            projections = [
                p for p in projections
                if (not status or p.get("current_status") == status)
                and (not source_type or p.get("source_type") == source_type)
            ]
        return jsonify({"ok": True, "candidates": projections, "count": len(projections)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@gov_bp.route("/candidates", methods=["POST"])
@_admin_token_required
def submit_candidate():
    try:
        data = request.get_json(silent=True) or {}
        store = _store_fact_candidates()
        cid = store.submit(
            fact=data.get("fact", ""),
            category=data.get("category", ""),
            source_type=data.get("source_type", ""),
            source_memory_ids=data.get("source_memory_ids") or [],
            evidence_summary=data.get("evidence_summary", ""),
            confidence=float(data.get("confidence", 0) or 0),
            frontend=data.get("frontend", "unknown"),
            recommended_layer=data.get("recommended_layer"),
            candidate_id=data.get("candidate_id"),
        )
        if cid is None:
            return jsonify({"ok": False, "error": "候选提交失败（source_type 非法/缺证据/重复 id）"}), 400
        _audit("submit", "fact_candidate", cid, None, store.get(cid), data.get("note", ""), "api")
        return jsonify({"ok": True, "candidate_id": cid})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@gov_bp.route("/candidates/<candidate_id>/review", methods=["POST"])
@_admin_token_required
def review_candidate(candidate_id: str):
    try:
        data = request.get_json(silent=True) or {}
        decision = data.get("decision", "")
        reviewer = data.get("reviewer", "") or "admin"
        note = data.get("note", "")
        store = _store_fact_candidates()
        before = store.get(candidate_id)
        if before is None:
            return jsonify({"ok": False, "error": "候选不存在"}), 404
        result = store.review(candidate_id, decision, reviewer=reviewer, note=note,
                              modified_fact=data.get("modified_fact"))
        if result is None:
            return jsonify({"ok": False, "error": "审核失败（decision 非法或候选不存在）"}), 400
        if result.get("already"):
            # 幂等命中：不重复写 target、不追加记录，明确返回 already 状态（GOV-001/007）
            _audit(f"review:{decision}(already)", "fact_candidate", candidate_id,
                   before, {"status": result["status"]}, note or "idempotent", reviewer)
            return jsonify({"ok": True, "status": f"already_{result['status']}", "record": result})
        if decision == "confirm":
            _confirm_to_target_layer(before, result)
        _audit(f"review:{decision}", "fact_candidate", candidate_id, before, result, note, reviewer)
        return jsonify({"ok": True, "record": result})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


def _confirm_to_target_layer(before: Dict[str, Any], confirmed: Dict[str, Any]) -> None:
    """confirm 后写入目标层（Relationship Core 或 Self Model Statements）。

    GOV-004: 目标层由 resolve_confirmation_target 统一解析（不依赖前端字符串）；
    GOV-005/006: 幂等——目标层 fact_id/statement_id 已存在则跳过，不重复写。
    """
    from src.governance.fact_candidate import resolve_confirmation_target
    layer = resolve_confirmation_target(before) or (before.get("recommended_layer") or "")
    if layer == "self_model":
        try:
            sm = _store_self_model_statements()
            sm.submit(
                fact=confirmed.get("fact", ""),
                category=before.get("category", ""),
                source_type=before.get("source_type", ""),
                source_memory_ids=before.get("source_memory_ids") or [],
                evidence_summary=before.get("evidence_summary", ""),
                confidence=before.get("confidence", 0),
                statement_id=confirmed.get("candidate_id"),
            )
            sm.review(confirmed.get("candidate_id"), "confirm", reviewer=confirmed.get("reviewed_by", "admin"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("confirm→self_model 写入失败: %s", exc)
    elif layer == "relationship_core":
        try:
            from src.relationship.relationship_core import RelationshipCore
            core = RelationshipCore()
            core.fact_id = confirmed.get("candidate_id")
            core.relationship_id = "yuyi:366648462"
            core.source_user_id = "366648462"
            core.status = "confirmed"
            core.source_type = before.get("source_type", "historical_fact")
            core.source_memory_ids = before.get("source_memory_ids") or []
            core.evidence_summary = before.get("evidence_summary", "")
            core.confirmed_by = confirmed.get("reviewed_by", "admin")
            core.confirmed_at = confirmed.get("reviewed_at", "")
            core.agreements = [confirmed.get("fact", "")] if confirmed.get("fact") else []
            # 幂等：fact_id 已存在则不重复写
            exists = any(
                str((r.get("fact_id") or "")).split("#")[0] == str(core.fact_id).split("#")[0]
                for r in _store_relationship_core().list_all()
            )
            if not exists:
                _store_relationship_core().save(core)
        except Exception as exc:  # noqa: BLE001
            logger.warning("confirm→relationship_core 写入失败: %s", exc)


# ============ Relationship Core ============
@gov_bp.route("/relationship-core", methods=["GET"])
@_admin_token_required
def list_relationship_core():
    try:
        store = _store_relationship_core()
        recs = [r for r in store.list_all() if r.get("status") in (None, "confirmed")]
        return jsonify({"ok": True, "facts": recs, "count": len(recs)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@gov_bp.route("/relationship-core/<fact_id>/supersede", methods=["POST"])
@_admin_token_required
def supersede_relationship_core(fact_id: str):
    try:
        data = request.get_json(silent=True) or {}
        reviewer = data.get("reviewer", "") or "admin"
        note = data.get("note", "")
        store = _store_relationship_core()
        recs = store.list_all()
        target = None
        for r in recs:
            if r.get("fact_id") == fact_id:
                target = r
                break
        if target is None:
            return jsonify({"ok": False, "error": "事实不存在"}), 404
        # 幂等（GOV-011）：该 fact 已 superseded → already，不重复追加
        if any(str(r.get("fact_id") or "").split("#")[0] == str(fact_id).split("#")[0]
               and r.get("status") == "superseded"
               for r in recs):
            return jsonify({"ok": True, "status": "already_superseded"})
        # 追加 superseded 记录（append-only，不覆盖）
        from src.relationship.relationship_core import RelationshipCore
        from datetime import datetime
        sup = RelationshipCore.from_dict(target)
        sup.fact_id = f"{fact_id}#s{datetime.now():%Y%m%dT%H%M%S}"
        sup.status = "superseded"
        sup.superseded_by = data.get("new_fact_id", "")
        sup.confirmed_by = reviewer
        store.save(sup)
        _audit("supersede", "relationship_core", fact_id, target, sup.to_dict(), note, reviewer)
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


# ============ Self Model Statements ============
@gov_bp.route("/self-model-statements", methods=["GET"])
@_admin_token_required
def list_self_model_statements():
    try:
        store = _store_self_model_statements()
        recs = store.load()
        return jsonify({"ok": True, "statements": recs, "count": len(recs)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


# ============ Shared-Life Patterns（Phase 2 长期共同生活模式治理） ============
def _store_shared_life_patterns():
    from src.governance.shared_life_pattern import SharedLifePatternStore
    return SharedLifePatternStore()


@gov_bp.route("/patterns", methods=["GET"])
@_admin_token_required
def list_shared_life_patterns():
    try:
        store = _store_shared_life_patterns()
        recs = store.load()
        return jsonify({"ok": True, "patterns": recs, "count": len(recs)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@gov_bp.route("/patterns/active", methods=["GET"])
@_admin_token_required
def list_active_patterns():
    try:
        store = _store_shared_life_patterns()
        recs = store.list_active()
        return jsonify({"ok": True, "patterns": recs, "count": len(recs)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@gov_bp.route("/patterns/<pattern_id>/review", methods=["POST"])
@_admin_token_required
def review_shared_life_pattern(pattern_id: str):
    """人工治理：confirm/reject/modify/supersede/archive（append-only）。"""
    try:
        data = request.get_json(silent=True) or {}
        decision = data.get("decision", "")
        reviewer = data.get("reviewer", "") or "admin"
        note = data.get("note", "")
        store = _store_shared_life_patterns()
        before = store.get(pattern_id)
        if before is None:
            return jsonify({"ok": False, "error": "模式不存在"}), 404
        result = store.review(
            pattern_id, decision, reviewer=reviewer, note=note,
            modified_summary=data.get("modified_summary"),
        )
        if result is None:
            return jsonify({"ok": False, "error": "decision 非法"}), 400
        if result.get("already"):
            _audit(f"review_pattern:{decision}(already)", "shared_life_pattern",
                   pattern_id, before, {"status": result["status"]}, note or "idempotent", reviewer)
            return jsonify({"ok": True, "status": f"already_{result['status']}"})
        _audit(f"review_pattern:{decision}", "shared_life_pattern",
               pattern_id, before, result, note, reviewer)
        return jsonify({"ok": True, "record": result})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


# ============ Memory Search（证据回跳） ============
@gov_bp.route("/memory-search", methods=["GET"])
@_admin_token_required
def memory_search():
    try:
        from src.memory.memory_provider import MemoryProvider
        q = (request.args.get("q") or "").strip().lower()
        frontend = request.args.get("frontend")
        source = request.args.get("source")
        limit = min(int(request.args.get("limit", 50)), 200)
        store = MemoryProvider.get_store()
        recs = store.load() or []
        out = []
        for r in recs:
            if not isinstance(r, dict):
                continue
            md = r.get("metadata") or {}
            if frontend and md.get("frontend") != frontend:
                continue
            if source and md.get("source") != source:
                continue
            if q:
                # 支持 memory_id 直接命中（点证据回跳）——id 不在 content 里，
                # 需与记录 id 匹配；否则走内容/回复关键词检索。
                rid = str(r.get("id") or "")
                if q == rid.lower() or (rid and rid.lower().startswith(q)):
                    # 精确 id 命中：直接返回，不截断内容
                    out.append({"id": r.get("id"), "ts": r.get("timestamp"),
                                "content": str(r.get("content", "")),
                                "metadata": {k: md.get(k) for k in _MEMORY_META_KEYS}})
                    if len(out) >= limit:
                        break
                    continue
                hay = f"{r.get('content','')} {md.get('reply','')}".lower()
                if q not in hay:
                    continue
            out.append({"id": r.get("id"), "ts": r.get("timestamp"),
                        "content": str(r.get("content", ""))[:200],
                        "metadata": {k: md.get(k) for k in _MEMORY_META_KEYS}})
            if len(out) >= limit:
                break
        return jsonify({"ok": True, "results": out, "count": len(out)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


# ============ Audit Log ============
@gov_bp.route("/audit-log", methods=["GET"])
@_admin_token_required
def audit_log():
    try:
        path = os.path.join("data", "governance", "audit_log.jsonl")
        if not os.path.exists(path):
            return jsonify({"ok": True, "entries": []})
        entries = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
        limit = min(int(request.args.get("limit", 100)), 500)
        return jsonify({"ok": True, "entries": entries[-limit:]})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500
