# -*- coding: utf-8 -*-
"""Fact Candidate（v1.5.5 Governance C2-a）—— 候选事实层。

羽依身份连续性治理体系的第一层：
    真实经历 → FactCandidate（status=candidate）→ 人工治理 → Confirmed Fact

设计原则（GOVERNANCE_SYSTEM_DESIGN.md）：
- candidate ≠ confirmed：候选永不直接进入常驻身份层；
- append-only：review 产生新状态记录，不覆盖原始候选（lineage 保留）；
- source_type 严格五分类，system_observed 永不自动 confirmed；
- 每条候选必须有证据链（source_memory_ids 或明确说明无证据原因）；
- 无 LLM、无业务依赖；纯存储 + 状态机（仿 RelationshipCoreStore 容错模式）。
"""
import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()

#: 事实生命周期状态（任务书冻结）
STATUS_CANDIDATE = "candidate"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
STATUS_HELD = "held"
STATUS_SUPERSEDED = "superseded"
VALID_STATUSES = frozenset({
    STATUS_CANDIDATE, STATUS_CONFIRMED, STATUS_REJECTED, STATUS_HELD, STATUS_SUPERSEDED,
})

#: 来源类型（严格五分类；system_observed 永不自动 confirmed）
SOURCE_USER_CONFIRMED = "user_confirmed"
SOURCE_YUI_STATED = "yui_stated"
SOURCE_HISTORICAL_FACT = "historical_fact"
SOURCE_RELATIONAL_AGREEMENT = "relational_agreement"
SOURCE_SYSTEM_OBSERVED = "system_observed"
VALID_SOURCE_TYPES = frozenset({
    SOURCE_USER_CONFIRMED, SOURCE_YUI_STATED, SOURCE_HISTORICAL_FACT,
    SOURCE_RELATIONAL_AGREEMENT, SOURCE_SYSTEM_OBSERVED,
})

#: 审核操作（review API）
REVIEW_CONFIRM = "confirm"
REVIEW_REJECT = "reject"
REVIEW_MODIFY = "modify"
REVIEW_HOLD = "hold"
VALID_REVIEW_DECISIONS = frozenset({REVIEW_CONFIRM, REVIEW_REJECT, REVIEW_MODIFY, REVIEW_HOLD})

#: 目标层（confirm 后写入哪里）
LAYER_RELATIONSHIP_CORE = "relationship_core"
LAYER_SELF_MODEL = "self_model"
VALID_LAYERS = frozenset({LAYER_RELATIONSHIP_CORE, LAYER_SELF_MODEL})


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _path_lock(path: str) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _backup_corrupt(path: str) -> None:
    try:
        if os.path.exists(path):
            backup = f"{path}.corrupt.{datetime.now():%Y%m%dT%H%M%S%f}"
            shutil_copy2(path, backup)
            logger.warning("FactCandidateStore: 损坏文件已备份为 %s", backup)
    except Exception:  # noqa: BLE001
        pass


def shutil_copy2(src: str, dst: str) -> None:
    import shutil
    shutil.copy2(src, dst)


class FactCandidateStore:
    """候选事实 append-only JSONL 存储（路径 data/governance/fact_candidates.jsonl）。

    - append-only：save() 只追加；review() 追加状态转换记录（不覆盖原始）；
    - 容错：坏行跳过、整体损坏备份降级、I/O 异常隔离；
    - 与 RelationshipCoreStore 同构，保持项目内一致性。
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join("data", "governance", "fact_candidates.jsonl")
        self._lock = _path_lock(self.path)

    # ============ 读 ============
    def load(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not os.path.exists(self.path):
                return []
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw_lines = f.readlines()
            except Exception:  # noqa: BLE001
                _backup_corrupt(self.path)
                return []
            records: List[Dict[str, Any]] = []
            for line in raw_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(parsed, dict):
                    records.append(parsed)
            if raw_lines and not records:
                _backup_corrupt(self.path)
            return records

    def get(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        for r in self.load():
            if r.get("candidate_id") == candidate_id:
                return r
        return None

    def list_candidates(self, status: Optional[str] = None, source_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """按状态/来源筛选；status=None 返回全部（含历史状态转换记录）。"""
        out = []
        for r in self.load():
            if status and r.get("status") != status:
                continue
            if source_type and r.get("source_type") != source_type:
                continue
            out.append(r)
        return out

    def list_projections(self) -> List[Dict[str, Any]]:
        """全部候选的族级投影（后端权威状态；前端只显示投影结果）。"""
        records = self.load()
        roots = []
        seen = set()
        for r in records:
            root = _base_of(r.get("candidate_id") or "")
            if root not in seen:
                seen.add(root)
                roots.append(root)
        out = []
        for root in sorted(roots):
            p = project_candidate(root, records)
            # 只返回原始候选（无审核后代的 family 用原始记录详情）
            origin = None
            for r in records:
                if (r.get("candidate_id") or "") == root:
                    origin = r
                    break
            if origin is None:
                continue
            p["fact"] = origin.get("fact", "")
            p["category"] = origin.get("category", "")
            p["source_type"] = origin.get("source_type", "")
            p["confidence"] = origin.get("confidence", 0)
            p["source_memory_ids"] = origin.get("source_memory_ids", [])
            p["evidence_summary"] = origin.get("evidence_summary", "")
            p["created_at"] = origin.get("created_at", "")
            p["frontend"] = origin.get("frontend", "unknown")
            out.append(p)
        return out

    # ============ 写 ============
    def save(self, candidate: Dict[str, Any]) -> bool:
        """追加一条候选（candidate_id 重复拒绝——append-only 不覆盖）。"""
        if not isinstance(candidate, dict):
            return False
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            return False
        with self._lock:
            try:
                if self.get(candidate_id) is not None:
                    return False
                parent = os.path.dirname(self.path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(candidate, ensure_ascii=False, default=str) + "\n")
                    f.flush()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("FactCandidateStore.save 失败: %s", exc)
                return False

    def review(self, candidate_id: str, decision: str, reviewer: str, note: str = "", modified_fact: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """人工审核：追加状态转换记录（append-only，不覆盖原始候选）。

        幂等语义（GOV-001/007 修复）：review 前投影 family 当前状态——
          - family 已 confirmed：再 confirm → {"already": True, "status": "confirmed"}，不追加；
          - family 已 rejected：任何后续操作 → {"already": True, "status": "rejected"}；
          - family 已 held：任何后续操作 → {"already": True, "status": "held"}；
          - modify 且与最后一次修改内容相同 → {"already": True, "status": "candidate"}。
        组合语义：confirm 同时携带 modified_fact → 一次性"修改后确认"，
        审核记录直接以修改文本进入 confirmed（防止第二阶段拿原始旧文本写目标层）。
        正常路径返回新记录。
        """
        if decision not in VALID_REVIEW_DECISIONS:
            return None
        existing = self.get(candidate_id)
        if existing is None:
            return None
        # 幂等检查：投影该 family 的当前有效状态
        proj = project_candidate(candidate_id, self.load())
        cur = proj.get("current_status")
        if cur == STATUS_CONFIRMED:
            return {"already": True, "status": STATUS_CONFIRMED,
                    "existing_record_id": proj.get("review_record_id") or proj.get("revision_id")}
        if cur == STATUS_REJECTED:
            return {"already": True, "status": STATUS_REJECTED}
        if cur == STATUS_HELD:
            return {"already": True, "status": STATUS_HELD}
        # modify 幂等：与最后一次 modify 内容相同 → 不再追加 revision
        if decision == REVIEW_MODIFY and modified_fact:
            last_modify = proj.get("last_modify_revision")
            if last_modify and last_modify.get("fact") == modified_fact:
                return {"already": True, "status": STATUS_CANDIDATE,
                        "existing_record_id": last_modify.get("candidate_id")}

        now = _now()
        new_status = {
            REVIEW_CONFIRM: STATUS_CONFIRMED,
            REVIEW_REJECT: STATUS_REJECTED,
            REVIEW_HOLD: STATUS_HELD,
            REVIEW_MODIFY: STATUS_CANDIDATE,  # modify 后仍为 candidate，等待再审
        }[decision]
        record = dict(existing)
        # 同秒多次 review 可生成相同时间戳 id → 加随机后缀保证唯一（append-only 去重依赖唯一 id）
        record["candidate_id"] = f"{candidate_id}#{now.replace(':', '').replace('-', '')}_{os.urandom(2).hex()}"
        record["status"] = new_status
        record["reviewed_at"] = now
        record["reviewed_by"] = reviewer or "unknown"
        record["review_note"] = note or ""
        record["lineage"] = existing.get("candidate_id")  # modify/后续审查追溯
        if decision == REVIEW_MODIFY and modified_fact:
            record["fact"] = modified_fact
            record["modified_from"] = existing.get("candidate_id")
        if decision == REVIEW_CONFIRM and modified_fact and modified_fact.strip():
            # 组合操作：修改后确认（一次操作完成改文本+确认）。
            # 若不在此处改写 fact，confirm 从原始记录取文本，会把旧文本
            # 作为确认事实写入目标层，导致"改后确认"落空。
            record["fact"] = modified_fact.strip()
            record["modified_from"] = existing.get("candidate_id")
        if decision == REVIEW_CONFIRM:
            record["confirmed_at"] = now
            record["confirmed_by"] = reviewer or "unknown"
        return record if self.save(record) else None

    def submit(self, fact: str, category: str, source_type: str, source_memory_ids: List[str],
               evidence_summary: str, confidence: float, frontend: str = "unknown",
               recommended_layer: Optional[str] = None, candidate_id: Optional[str] = None) -> Optional[str]:
        """提交一条候选事实（羽依/系统/人工均可）。返回 candidate_id 或 None。

        约束：
        - source_type 必须合法（五分类）；
        - source_memory_ids 至少 1 条，或 evidence_summary 明确说明无证据原因；
        - confidence 0-1；
        - system_observed 永远保持 candidate（review 时不强制，但 confirm 需人工）。
        """
        if source_type not in VALID_SOURCE_TYPES:
            return None
        if not fact or not fact.strip():
            return None
        confidence = float(confidence or 0.0)
        confidence = max(0.0, min(1.0, confidence))
        if not source_memory_ids and not evidence_summary:
            return None
        if not candidate_id:
            candidate_id = f"fc_{datetime.now():%Y%m%d%H%M%S}_{os.urandom(2).hex()}"
        rec = {
            "candidate_id": candidate_id,
            "fact": fact.strip(),
            "category": category or "",
            "source_type": source_type,
            "source_memory_ids": list(source_memory_ids or []),
            "evidence_summary": evidence_summary or "",
            "confidence": confidence,
            "status": STATUS_CANDIDATE,
            "frontend": frontend or "unknown",
            "recommended_layer": recommended_layer,
            "created_at": _now(),
            "reviewed_at": None,
            "reviewed_by": None,
            "review_note": "",
            "lineage": None,
        }
        return candidate_id if self.save(rec) else None


# ============ Family 投影（GOV-002/003/004 修复） ============
def _base_of(candidate_id: str) -> str:
    """fact family 根 id：去掉 #后缀、沿 lineage/modified_from 链追溯。"""
    parts = str(candidate_id or "").split("#")
    return parts[0]


def _family_records(candidate_id: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """收集该 family 的全部记录（原始 + 所有 revision/review 后代）。

    判定：记录 candidate_id 的 base 与家族根 base 相同（#后缀剥离），
    或 lineage/modified_from 指向家族内任一记录。
    """
    root = _base_of(candidate_id)
    related = []
    seen = set()
    for r in records:
        rid = str(r.get("candidate_id") or "")
        base = _base_of(rid)
        lin = _base_of(r.get("lineage") or "")
        mf = _base_of(r.get("modified_from") or "")
        if base == root or lin == root or mf == root:
            if rid in seen:
                continue
            seen.add(rid)
            related.append(r)
    return related


def project_candidate(candidate_id: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """单一候选的当前状态投影（后端权威，前端不得自行推断）。

    返回：
        candidate_id 原始 id
        family_id    family 根 id
        current_status  candidate/confirmed/rejected/held（family 最终有效状态）
        reviewed     是否已审核
        confirmed     是否 confirmed
        review_record_id 审核记录 id（confirmed/rejected/held 时）
        target       目标层（resolve_confirmation_target 结果）
        revisions    [modify revision 列表]
    """
    root = _base_of(candidate_id)
    fam = _family_records(root, records)
    if not fam:
        return {"candidate_id": candidate_id, "family_id": root,
                "current_status": "candidate", "reviewed": False,
                "confirmed": False, "review_record_id": None, "target": None, "revisions": []}
    # 状态推导：按时间序取最后一条 review 记录（status != candidate 的）
    # 注意 family 中会有多条：原始 candidate + review 后代
    ordered = sorted(fam, key=lambda r: str(r.get("reviewed_at") or r.get("created_at") or ""))
    current = STATUS_CANDIDATE
    review_record_id = None
    for r in ordered:
        st = r.get("status")
        if st == STATUS_CANDIDATE and r.get("reviewed_at"):
            # modify revision → 仍是 candidate，但审核过
            current = STATUS_CANDIDATE
            review_record_id = r.get("candidate_id")
        elif st in (STATUS_CONFIRMED, STATUS_REJECTED, STATUS_HELD):
            current = st
            review_record_id = r.get("candidate_id")
    revisions = [r for r in ordered if r.get("modified_from")]
    last_modify = revisions[-1] if revisions else None
    origin = fam[0] if fam else {}
    return {
        "candidate_id": candidate_id,
        "family_id": root,
        "current_status": current,
        "reviewed": bool(review_record_id) or current != STATUS_CANDIDATE,
        "confirmed": current == STATUS_CONFIRMED,
        "review_record_id": review_record_id,
        "target": resolve_confirmation_target(origin),
        "revisions": revisions,
        "last_modify_revision": last_modify,
    }


def resolve_confirmation_target(candidate: Dict[str, Any]) -> Optional[str]:
    """candidate 确认后的目标层（GOV-004：统一解析，不散落前端）。

    规则：
      - candidate_id 前缀 SM-* → self_model（Self Model Statements）
      - candidate_id 前缀 RC-* → relationship_core
      - 否则 recommended_layer 合法则用之
      - 否则 None（待人工指定）
    """
    cid = str(candidate.get("candidate_id") or "")
    if cid.startswith("SM-"):
        return LAYER_SELF_MODEL
    if cid.startswith("RC-"):
        return LAYER_RELATIONSHIP_CORE
    rl = str(candidate.get("recommended_layer") or "")
    return rl if rl in VALID_LAYERS else None
