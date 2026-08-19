# -*- coding: utf-8 -*-
"""
src/runtime/decision_proposal.py

Phase B.12 Runtime Integration —— Decision Evolution Loop

本文件是 Phase B.12 的"决策演化闭环",**不修改**任何核心模块,
也**不修改**已有 B.4~B.11 核心逻辑。

  - 不修改 RuntimeCore
  - 不修改 RuntimeSupervisor
  - 不修改 ActionDispatcher
  - 不修改 ProactiveEngine
  - 不修改 ActionConfidenceGate
  - 不修改 DecisionIntelligence
  - 不修改 B.4~B.11 已有 runtime 模块
  - 不自动执行任何策略
  - 不自动改变 Runtime 行为
  - 不绕过 ActionConfidenceGate
  - 不影响 Growth System
  - 不影响 Persona System
  - 所有状态变化通过 RuntimeB4Bridge 注入
  - 所有异常 fail-soft

集成原理(继续 B.4~B.11 的"零侵入包装"模式):
  ┌──────────────────────────────────────────────────────────────┐
  │  RuntimeB4Bridge(B.4-B.11)                                   │
  │     ├→ ActionLifecycleManager(B.4)                           │
  │     ├→ ActionPersistenceManager(B.5)                         │
  │     ├→ OutcomeTracker(B.6)                                   │
  │     ├→ ActionFeedbackManager(B.7)                            │
  │     ├→ DecisionFeedbackAdapter(B.8)                          │
  │     ├→ DecisionFeedbackRuntime(B.9)                          │
  │     ├→ DecisionObserver(B.10)                                │
  │     └→ DecisionIntelligence(B.11)                            │
  │              ↓                                                │
  │     DecisionEvolutionRuntime(B.12,新增)                      │
  │              ├→ DecisionProposalGenerator                    │
  │              │     ├→ boost proposal                         │
  │              │     ├→ suppress proposal                      │
  │              │     └→ monitor proposal                       │
  │              ├→ DecisionApprovalManager                      │
  │              │     ├→ approve()                              │
  │              │     ├→ reject()                               │
  │              │     └→ expire()                               │
  │              └→ DecisionProposal Store(append-only)          │
  │              ↓                                                │
  │     persistence.persist_event(                               │
  │         stage=decision_proposal_created / _approved / ...    │
  │     )                                                        │
  └──────────────────────────────────────────────────────────────┘

B.12 接入路径(零侵入,默认安全):
  - evolution 默认 enabled=True,但不阻塞
  - 写盘失败 → 计数 +1,自动降级为 memory-only
  - 任何异常都被 try/except 隔离,绝不抛
  - 复用 B.5 ActionPersistenceManager 作为底层 JSONL 通道
  - 全部只读 + 提案性:不修改 B.4~B.11 任何状态
  - 不修改 supervisor / runtime_core / proactive_engine 任何代码
  - 不修改 ActionConfidenceGate
  - 不发起任何 action
  - 不修改 Growth System

B.12 硬约束(项目红线):
  - 不修改任何核心模块
  - 不修改 B.4~B.11 已有核心逻辑(只通过 bridge 注入)
  - 不绕过 ActionConfidenceGate
  - 不自动发消息
  - 不自动执行策略(auto_apply=False)
  - 不启动 InitiativeBridge
  - 不新增线程(同步 in-tick)
  - 不新增事件总线
  - 默认开启但不阻塞(失败自动降级)
  - 全部 fail-soft
  - 全部 append-only(persistence 通道)
  - 支持空状态运行(没有数据时返回安全默认值)

本模块职责:
  1. DecisionProposal:    提案数据类
  2. DecisionProposalGenerator: 从 B.11 report 生成提案(只读)
  3. DecisionApprovalManager: 管理提案审批(approve/reject/expire)
  4. DecisionEvolutionRuntime: 串联 Generator + Approval + Store
  5. 全部状态变化通过 persistence.persist_event() 留痕
  6. 不执行任何策略,只生成可审计的提案
"""
from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.phase_b4_integration import RuntimeB4Bridge

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================

PHASE_B12_DEFAULT_CONFIG: Dict[str, Any] = {
    "decision_evolution": {
        "enabled": True,                       # 默认开启
        "path": "data/decision_proposals.jsonl",
        "max_proposals": 200,                  # 内存中保留的最大 proposal 数
        "max_age_seconds": 7 * 24 * 3600.0,    # 默认 7 天过期
        "auto_apply": False,                   # 禁止自动执行(B.12 硬约束)
        "min_confidence": 0.3,                 # 低于此置信度的提案直接 reject
        "boost_confidence_threshold": 0.7,     # boost 提案的最小 confidence
        "suppress_confidence_threshold": 0.6,  # suppress 提案的最小 confidence
        "monitor_drift_threshold": 0.15,       # monitor 提案的最小 drift_magnitude
        "auto_recover": True,                  # 启动时自动 load_state
    },
}

PHASE_B12_NAME = "phase_b12"
PHASE_B12_VERSION = "1.0.0"

SCHEMA_VERSION = "1.0"

# 提案类型常量
PROPOSAL_TYPE_BOOST = "boost"
PROPOSAL_TYPE_SUPPRESS = "suppress"
PROPOSAL_TYPE_MONITOR = "monitor"

ALL_PROPOSAL_TYPES = (PROPOSAL_TYPE_BOOST, PROPOSAL_TYPE_SUPPRESS, PROPOSAL_TYPE_MONITOR)

# 状态常量
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_APPLIED = "applied"
STATUS_EXPIRED = "expired"

ALL_STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_APPLIED, STATUS_EXPIRED)
TERMINAL_STATUSES = (STATUS_APPROVED, STATUS_REJECTED, STATUS_APPLIED, STATUS_EXPIRED)

# B.12 使用的 stage 名(复用 B.5 persistence 通道)
STAGE_PROPOSAL_CREATED = "decision_proposal_created"
STAGE_PROPOSAL_APPROVED = "decision_proposal_approved"
STAGE_PROPOSAL_REJECTED = "decision_proposal_rejected"
STAGE_PROPOSAL_APPLIED = "decision_proposal_applied"
STAGE_PROPOSAL_EXPIRED = "decision_proposal_expired"

# 默认值
DEFAULT_CONFIDENCE = 0.5
DEFAULT_MAX_PROPOSALS = 200


# ============================================================
# 时间工具
# ============================================================

def _now_iso() -> str:
    """ISO 8601 UTC timestamp"""
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.utcnow().isoformat() + "Z"


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全的 float 转换"""
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """clip 数值到 [lo, hi]"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


# ============================================================
# 配置注入
# ============================================================

def apply_phase_b12_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.12 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B12_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B12_DEFAULT_CONFIG.items():
        if isinstance(v, dict):
            existing = merged.get(k)
            if isinstance(existing, dict):
                sub: Dict[str, Any] = {}
                sub.update(v)
                sub.update(existing)
                merged[k] = sub
            else:
                merged[k] = {k2: v2 for k2, v2 in v.items()}
        else:
            merged.setdefault(k, v)
    return merged


def is_phase_b12_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.12 evolution 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    p = cfg.get("decision_evolution", {})
    if not isinstance(p, dict):
        return False
    return bool(p.get("enabled", True))


# ============================================================
# DecisionProposal
# ============================================================

@dataclass
class DecisionProposal:
    """
    决策演化提案(只读 + 可审计)。

    字段:
      proposal_id:        提案唯一 ID
      proposal_type:      "boost" / "suppress" / "monitor"
      target_action_type: 目标行为类型
      current_state:      当前系统状态摘要(dict)
      suggested_change:   建议变更描述(str)
      reason:             提案理由
      evidence:           证据链(list of dict)
      confidence:         提案置信度(0.0~1.0)
      created_at:         ISO 8601 创建时间
      status:             "pending" / "approved" / "rejected" / "applied" / "expired"
      reviewed_at:        ISO 8601 审批时间
      reviewer_id:        审批者 ID
      review_comment:     审批意见
      applied_at:         ISO 8601 应用时间
      applied_by:         应用者 ID
      expires_at:         ISO 8601 过期时间
      metadata:           元数据 dict
      source_report_hash: 来源 B.11 report 的 hash(用于审计)
    """
    proposal_id: str = field(default_factory=lambda: f"dprop_{uuid.uuid4().hex[:12]}")
    proposal_type: str = PROPOSAL_TYPE_MONITOR
    target_action_type: str = ""
    current_state: Dict[str, Any] = field(default_factory=dict)
    suggested_change: str = ""
    reason: str = ""
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = DEFAULT_CONFIDENCE
    created_at: str = field(default_factory=_now_iso)
    status: str = STATUS_PENDING
    reviewed_at: Optional[str] = None
    reviewer_id: str = ""
    review_comment: str = ""
    applied_at: Optional[str] = None
    applied_by: str = ""
    expires_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    source_report_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "proposal_id": str(self.proposal_id),
            "proposal_type": str(self.proposal_type),
            "target_action_type": str(self.target_action_type),
            "current_state": dict(self.current_state or {}),
            "suggested_change": str(self.suggested_change),
            "reason": str(self.reason),
            "evidence": list(self.evidence or []),
            "confidence": _clip(self.confidence),
            "created_at": str(self.created_at),
            "status": str(self.status),
            "reviewed_at": self.reviewed_at,
            "reviewer_id": str(self.reviewer_id),
            "review_comment": str(self.review_comment),
            "applied_at": self.applied_at,
            "applied_by": str(self.applied_by),
            "expires_at": self.expires_at,
            "metadata": dict(self.metadata or {}),
            "source_report_hash": str(self.source_report_hash),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DecisionProposal":
        return cls(
            proposal_id=str(d.get("proposal_id", "") or f"dprop_{uuid.uuid4().hex[:12]}"),
            proposal_type=str(d.get("proposal_type", PROPOSAL_TYPE_MONITOR)),
            target_action_type=str(d.get("target_action_type", "")),
            current_state=dict(d.get("current_state", {}) or {}),
            suggested_change=str(d.get("suggested_change", "")),
            reason=str(d.get("reason", "")),
            evidence=list(d.get("evidence", []) or []),
            confidence=_safe_float(d.get("confidence", DEFAULT_CONFIDENCE)),
            created_at=str(d.get("created_at", "") or _now_iso()),
            status=str(d.get("status", STATUS_PENDING)),
            reviewed_at=d.get("reviewed_at"),
            reviewer_id=str(d.get("reviewer_id", "")),
            review_comment=str(d.get("review_comment", "")),
            applied_at=d.get("applied_at"),
            applied_by=str(d.get("applied_by", "")),
            expires_at=d.get("expires_at"),
            metadata=dict(d.get("metadata", {}) or {}),
            source_report_hash=str(d.get("source_report_hash", "")),
        )

    def is_expired(self, now: Optional[float] = None) -> bool:
        """检查是否过期"""
        if not self.expires_at:
            return False
        try:
            # ISO 8601 解析
            ts_str = str(self.expires_at).replace("Z", "+00:00")
            expire_dt = datetime.fromisoformat(ts_str)
            current_dt = datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now, tz=timezone.utc)
            return current_dt > expire_dt
        except Exception:
            return False

    def is_terminal(self) -> bool:
        """是否终态"""
        return self.status in TERMINAL_STATUSES


# ============================================================
# 内部 store —— proposal 内存索引 + append-only
# ============================================================

class _ProposalIndex:
    """
    提案索引(内存 + append-only JSONL 通道)。

    设计:
      - 内存索引:proposal_id -> latest DecisionProposal
      - 持久化:通过外部 persistence.persist_event(stage=...) 写入
      - 全部 fail-soft:任何异常都不影响上层
    """

    def __init__(self, max_proposals: int = DEFAULT_MAX_PROPOSALS):
        self._max_proposals = max(1, int(max_proposals or DEFAULT_MAX_PROPOSALS))
        self._lock = threading.RLock()
        # proposal_id -> latest DecisionProposal
        self._index: Dict[str, DecisionProposal] = {}
        # 计数
        self._total_created = 0
        self._total_approved = 0
        self._total_rejected = 0
        self._total_applied = 0
        self._total_expired = 0

    def add(self, proposal: DecisionProposal) -> bool:
        """添加或更新一个 proposal"""
        try:
            with self._lock:
                self._index[proposal.proposal_id] = proposal
                self._total_created += 1
                # 容量保护:超过 max_proposals 时丢弃最旧的
                if len(self._index) > self._max_proposals:
                    # 按 created_at 排序,保留最新的
                    items = sorted(
                        self._index.items(),
                        key=lambda kv: kv[1].created_at or "",
                    )
                    keep = items[-self._max_proposals:]
                    self._index = dict(keep)
            return True
        except Exception:
            return False

    def get(self, proposal_id: str) -> Optional[DecisionProposal]:
        """根据 ID 获取 proposal"""
        try:
            with self._lock:
                return self._index.get(proposal_id)
        except Exception:
            return None

    def list(
        self,
        status: Optional[str] = None,
        proposal_type: Optional[str] = None,
        target_action_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[DecisionProposal]:
        """列出 proposal,按 created_at 降序"""
        try:
            with self._lock:
                items = list(self._index.values())
            if status is not None:
                items = [p for p in items if p.status == status]
            if proposal_type is not None:
                items = [p for p in items if p.proposal_type == proposal_type]
            if target_action_type is not None:
                items = [p for p in items if p.target_action_type == target_action_type]
            items.sort(key=lambda p: p.created_at or "", reverse=True)
            return items[: max(0, int(limit or 50))]
        except Exception:
            return []

    def list_pending(self, limit: int = 50) -> List[DecisionProposal]:
        """列出待审批 proposal"""
        return self.list(status=STATUS_PENDING, limit=limit)

    def update(self, proposal: DecisionProposal) -> bool:
        """更新一个 proposal(用于审批)"""
        return self.add(proposal)

    def count_by_status(self) -> Dict[str, int]:
        """按状态统计"""
        try:
            with self._lock:
                result: Dict[str, int] = {s: 0 for s in ALL_STATUSES}
                for p in self._index.values():
                    if p.status in result:
                        result[p.status] += 1
                    result["total"] = result.get("total", 0) + 1
                return result
        except Exception:
            return {s: 0 for s in ALL_STATUSES}

    def get_stats(self) -> Dict[str, int]:
        """统计信息"""
        with self._lock:
            return {
                "total_created": int(self._total_created),
                "total_approved": int(self._total_approved),
                "total_rejected": int(self._total_rejected),
                "total_applied": int(self._total_applied),
                "total_expired": int(self._total_expired),
                "in_memory": len(self._index),
                "max_proposals": int(self._max_proposals),
            }

    def increment_status(self, status: str) -> None:
        """累加状态计数"""
        try:
            with self._lock:
                if status == STATUS_APPROVED:
                    self._total_approved += 1
                elif status == STATUS_REJECTED:
                    self._total_rejected += 1
                elif status == STATUS_APPLIED:
                    self._total_applied += 1
                elif status == STATUS_EXPIRED:
                    self._total_expired += 1
        except Exception:
            pass

    def clear(self) -> None:
        """清空(测试用)"""
        with self._lock:
            self._index.clear()
            self._total_created = 0
            self._total_approved = 0
            self._total_rejected = 0
            self._total_applied = 0
            self._total_expired = 0


# ============================================================
# DecisionProposalGenerator
# ============================================================

class DecisionProposalGenerator:
    """
    决策提案生成器(只读)。

    输入:B.11 DecisionIntelligence report(或 report 的子集)
    输出:List[DecisionProposal](总是 pending 状态)

    行为:
      - 从 B.11 report 中提取 strategy_recommendations
      - 根据 action 映射为 boost/suppress/monitor 三类提案
      - 每个提案附加 current_state / evidence / source_report_hash
      - 全部只读,不修改任何 B.4~B.11 状态
      - 全部 fail-soft
    """

    def __init__(
        self,
        boost_confidence_threshold: float = 0.7,
        suppress_confidence_threshold: float = 0.6,
        monitor_drift_threshold: float = 0.15,
        min_confidence: float = 0.3,
    ) -> None:
        self._boost_threshold = _clip(boost_confidence_threshold, 0.0, 1.0)
        self._suppress_threshold = _clip(suppress_confidence_threshold, 0.0, 1.0)
        self._drift_threshold = max(0.0, _safe_float(monitor_drift_threshold, 0.15))
        self._min_confidence = _clip(min_confidence, 0.0, 1.0)

    def generate(self, intelligence_report: Optional[Dict[str, Any]]) -> List[DecisionProposal]:
        """
        从 B.11 report 生成提案列表。

        Args:
            intelligence_report: B.11 export_intelligence_report() 的输出
                                 (可空,为空时返回空 list)

        Returns:
            List[DecisionProposal](全部 status=pending)
        """
        proposals: List[DecisionProposal] = []
        try:
            if not isinstance(intelligence_report, dict):
                return proposals

            report_hash = self._compute_report_hash(intelligence_report)
            recs = intelligence_report.get("strategy_recommendations") or []
            drifts = intelligence_report.get("feedback_drifts") or []
            patterns = intelligence_report.get("failure_patterns") or []

            # 1) 处理 strategy_recommendations
            for rec in recs or []:
                if not isinstance(rec, dict):
                    continue
                p = self._build_from_recommendation(rec, drifts, patterns, report_hash)
                if p is not None:
                    proposals.append(p)

            # 2) 单独处理 drifts(anomalous)→ monitor 提案
            for d in drifts or []:
                if not isinstance(d, dict):
                    continue
                if not bool(d.get("is_anomalous", False)):
                    continue
                p = self._build_monitor_from_drift(d, report_hash)
                if p is not None:
                    proposals.append(p)

            return proposals
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] generate 异常(已隔离): {exc}")
            return []

    def _build_from_recommendation(
        self,
        rec: Dict[str, Any],
        drifts: List[Dict[str, Any]],
        patterns: List[Dict[str, Any]],
        report_hash: str,
    ) -> Optional[DecisionProposal]:
        """根据一条 StrategyRecommendation 构建 proposal"""
        try:
            action = str(rec.get("action", "") or "").lower()
            target = str(rec.get("target_action_type", "") or "")
            confidence = _safe_float(rec.get("confidence", 0.0))
            reason = str(rec.get("reason", "") or "")
            supporting = rec.get("supporting_metrics") or {}
            if not isinstance(supporting, dict):
                supporting = {}

            # 阈值过滤
            if confidence < self._min_confidence:
                return None

            if action == "boost" and confidence >= self._boost_threshold:
                ptype = PROPOSAL_TYPE_BOOST
                change = "increase confidence / frequency"
            elif action == "suppress" and confidence >= self._suppress_threshold:
                ptype = PROPOSAL_TYPE_SUPPRESS
                change = "reduce frequency / suppress"
            elif action == "monitor":
                ptype = PROPOSAL_TYPE_MONITOR
                change = "observe without changing behavior"
            else:
                # 其它(neutral / 不达阈值)→ 退化到 monitor
                ptype = PROPOSAL_TYPE_MONITOR
                change = "observe (insufficient signal)"

            evidence: List[Dict[str, Any]] = [
                {
                    "type": "strategy_recommendation",
                    "target_action_type": target,
                    "action": action,
                    "confidence": _clip(confidence),
                    "supporting_metrics": dict(supporting),
                }
            ]

            # 把相关的 drifts 和 patterns 附加到 evidence
            for d in drifts or []:
                if not isinstance(d, dict):
                    continue
                if str(d.get("action_type", "") or "") == target:
                    evidence.append({
                        "type": "feedback_drift",
                        "action_type": str(d.get("action_type", "")),
                        "direction": str(d.get("direction", "")),
                        "drift_magnitude": _safe_float(d.get("drift_magnitude", 0.0)),
                        "is_anomalous": bool(d.get("is_anomalous", False)),
                    })
            for p in patterns or []:
                if not isinstance(p, dict):
                    continue
                if str(p.get("action_type", "") or "") == target:
                    evidence.append({
                        "type": "failure_pattern",
                        "pattern_type": str(p.get("pattern_type", "")),
                        "action_type": str(p.get("action_type", "")),
                        "severity": str(p.get("severity", "")),
                    })

            return DecisionProposal(
                proposal_type=ptype,
                target_action_type=target,
                current_state=dict(supporting),
                suggested_change=change,
                reason=reason,
                evidence=evidence,
                confidence=_clip(confidence),
                status=STATUS_PENDING,
                source_report_hash=report_hash,
                metadata={
                    "source": "phase_b12_generator",
                    "source_action": action,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] _build_from_recommendation 异常(已隔离): {exc}")
            return None

    def _build_monitor_from_drift(
        self,
        drift: Dict[str, Any],
        report_hash: str,
    ) -> Optional[DecisionProposal]:
        """从 drift 构建 monitor 提案"""
        try:
            target = str(drift.get("action_type", "") or "")
            if not target:
                return None
            magnitude = _safe_float(drift.get("drift_magnitude", 0.0))
            if magnitude < self._drift_threshold:
                return None
            direction = str(drift.get("direction", "") or "")
            return DecisionProposal(
                proposal_type=PROPOSAL_TYPE_MONITOR,
                target_action_type=target,
                current_state={
                    "drift_magnitude": magnitude,
                    "direction": direction,
                },
                suggested_change="observe feedback drift",
                reason=f"feedback drift detected: magnitude={magnitude:.2f}, direction={direction}",
                evidence=[
                    {
                        "type": "feedback_drift",
                        "action_type": target,
                        "current_weight": _safe_float(drift.get("current_weight", 0.0)),
                        "historical_avg_weight": _safe_float(
                            drift.get("historical_avg_weight", 0.0)
                        ),
                        "drift_magnitude": magnitude,
                        "direction": direction,
                        "is_anomalous": bool(drift.get("is_anomalous", False)),
                    }
                ],
                confidence=_safe_float(drift.get("drift_magnitude", 0.0)),
                status=STATUS_PENDING,
                source_report_hash=report_hash,
                metadata={"source": "phase_b12_generator"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] _build_monitor_from_drift 异常(已隔离): {exc}")
            return None

    def _compute_report_hash(self, report: Dict[str, Any]) -> str:
        """计算 report 的 hash(用于审计)"""
        try:
            # 简化:基于关键字段计算 hash
            parts = [
                str(report.get("report_version", "")),
                str(report.get("generated_at", "")),
                str(len(report.get("strategy_recommendations", []) or [])),
                str(len(report.get("feedback_drifts", []) or [])),
                str(len(report.get("failure_patterns", []) or [])),
            ]
            raw = "|".join(parts)
            return __import__("hashlib").sha1(raw.encode("utf-8")).hexdigest()[:12]
        except Exception:
            return ""


# ============================================================
# DecisionApprovalManager
# ============================================================

class DecisionApprovalManager:
    """
    决策提案审批管理器。

    职责:
      - approve(proposal_id, reviewer_id, comment) → 标记 approved
      - reject(proposal_id, reviewer_id, comment)  → 标记 rejected
      - expire(proposal_id)                        → 标记 expired
      - 默认 auto_apply=False:不自动执行任何策略
      - 全部状态变化记录到 persistence
      - 全部 fail-soft
    """

    def __init__(
        self,
        store: _ProposalIndex,
        persistence: Any = None,
    ) -> None:
        self._store = store
        self._persistence = persistence
        self._lock = threading.RLock()

    def approve(
        self,
        proposal_id: str,
        reviewer_id: str = "",
        comment: str = "",
    ) -> bool:
        """批准一个 proposal"""
        try:
            with self._lock:
                p = self._store.get(proposal_id)
                if p is None:
                    return False
                if p.is_terminal():
                    # 已终态,不能再审批
                    return False
                p.status = STATUS_APPROVED
                p.reviewed_at = _now_iso()
                p.reviewer_id = str(reviewer_id or "")
                p.review_comment = str(comment or "")
                self._store.update(p)
                self._store.increment_status(STATUS_APPROVED)
            self._persist_proposal_event(p, stage=STAGE_PROPOSAL_APPROVED, decision="approved")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] approve 异常(已隔离): {exc}")
            return False

    def reject(
        self,
        proposal_id: str,
        reviewer_id: str = "",
        comment: str = "",
    ) -> bool:
        """拒绝一个 proposal"""
        try:
            with self._lock:
                p = self._store.get(proposal_id)
                if p is None:
                    return False
                if p.is_terminal():
                    return False
                p.status = STATUS_REJECTED
                p.reviewed_at = _now_iso()
                p.reviewer_id = str(reviewer_id or "")
                p.review_comment = str(comment or "")
                self._store.update(p)
                self._store.increment_status(STATUS_REJECTED)
            self._persist_proposal_event(p, stage=STAGE_PROPOSAL_REJECTED, decision="rejected")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] reject 异常(已隔离): {exc}")
            return False

    def expire(self, proposal_id: str) -> bool:
        """标记一个 proposal 为过期"""
        try:
            with self._lock:
                p = self._store.get(proposal_id)
                if p is None:
                    return False
                if p.is_terminal():
                    return False
                p.status = STATUS_EXPIRED
                self._store.update(p)
                self._store.increment_status(STATUS_EXPIRED)
            self._persist_proposal_event(p, stage=STAGE_PROPOSAL_EXPIRED, decision="expired")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] expire 异常(已隔离): {exc}")
            return False

    def mark_applied(
        self,
        proposal_id: str,
        applied_by: str = "",
    ) -> bool:
        """
        标记一个 approved proposal 为已应用(由未来 B.13 executor 调用)。

        B.12 阶段不会自动调用此方法,只为 B.13 留接口。
        """
        try:
            with self._lock:
                p = self._store.get(proposal_id)
                if p is None:
                    return False
                if p.status != STATUS_APPROVED:
                    # 只有 approved 才能 mark_applied
                    return False
                p.status = STATUS_APPLIED
                p.applied_at = _now_iso()
                p.applied_by = str(applied_by or "")
                self._store.update(p)
                self._store.increment_status(STATUS_APPLIED)
            self._persist_proposal_event(p, stage=STAGE_PROPOSAL_APPLIED, decision="applied")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] mark_applied 异常(已隔离): {exc}")
            return False

    def expire_old_proposals(self, now: Optional[float] = None) -> int:
        """扫描所有 pending proposal,把过期的标记为 expired"""
        count = 0
        try:
            pendings = self._store.list_pending(limit=1000)
            for p in pendings:
                if p.is_expired(now=now):
                    if self.expire(p.proposal_id):
                        count += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] expire_old_proposals 异常(已隔离): {exc}")
        return count

    def _persist_proposal_event(
        self,
        proposal: DecisionProposal,
        stage: str,
        decision: str,
    ) -> None:
        """写一条 proposal 状态变化到 persistence"""
        if self._persistence is None:
            return
        try:
            if not hasattr(self._persistence, "persist_event"):
                return
            ts = time.time()
            aid = f"dprop_{proposal.proposal_id}"
            lc_id = f"dprop_{proposal.proposal_id}_{int(ts * 1000)}_{uuid.uuid4().hex[:6]}"
            ok = self._persistence.persist_event(
                action_id=aid,
                lifecycle_id=lc_id,
                stage=stage,
                decision=decision,
                ts=ts,
                result=proposal.to_dict(),
                source="phase_b12_evolution",
            )
            if not ok:
                logger.debug(f"[phase_b12] persist_event 返回 False(已隔离)")
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] persist_event 异常(已隔离): {exc}")


# ============================================================
# DecisionEvolutionRuntime
# ============================================================

class DecisionEvolutionRuntime:
    """
    决策演化运行时 —— 串联 Generator + Approval + Store。

    职责:
      1. generate_from_intelligence(): 从 B.11 report 生成提案
      2. create_proposal(): 手动创建一个提案(供测试)
      3. approve_proposal() / reject_proposal(): 委托 ApprovalManager
      4. get_pending_proposals(): 列出 pending
      5. get_evolution_summary(): 整体摘要
      6. export_proposals(): 导出所有 proposal(只读)

    硬约束:
      - 不自动执行任何策略(auto_apply=False)
      - 不修改 B.4~B.11 任何状态
      - 所有异常 fail-soft
    """

    def __init__(
        self,
        persistence: Any = None,
        generator: Optional[DecisionProposalGenerator] = None,
        max_proposals: int = DEFAULT_MAX_PROPOSALS,
        max_age_seconds: float = 7 * 24 * 3600.0,
        auto_apply: bool = False,
        min_confidence: float = 0.3,
        boost_confidence_threshold: float = 0.7,
        suppress_confidence_threshold: float = 0.6,
        monitor_drift_threshold: float = 0.15,
        enabled: bool = True,
    ) -> None:
        self._persistence = persistence
        self._enabled = bool(enabled)
        self._max_proposals = max(1, int(max_proposals or DEFAULT_MAX_PROPOSALS))
        self._max_age_seconds = max(0.0, _safe_float(max_age_seconds, 7 * 24 * 3600.0))
        # B.12 硬约束:auto_apply 必须 False(由用户决定执行)
        self._auto_apply = False if auto_apply is None else bool(auto_apply) and False
        # 永远不自动执行
        self._auto_apply = False

        self._min_confidence = _clip(min_confidence, 0.0, 1.0)

        self._store = _ProposalIndex(max_proposals=self._max_proposals)
        self._generator = generator or DecisionProposalGenerator(
            boost_confidence_threshold=boost_confidence_threshold,
            suppress_confidence_threshold=suppress_confidence_threshold,
            monitor_drift_threshold=monitor_drift_threshold,
            min_confidence=min_confidence,
        )
        self._approval = DecisionApprovalManager(
            store=self._store,
            persistence=persistence,
        )
        self._lock = threading.RLock()

        # 统计
        self._create_count = 0
        self._generate_count = 0
        self._error_count = 0
        self._recover_count = 0
        self._last_error: Optional[str] = None
        self._last_generate_at: Optional[str] = None
        self._created_at: str = _now_iso()
        self._closed = False

        # 启动恢复
        if persistence is not None:
            try:
                self._load_state()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_b12] 启动 load_state 异常(已隔离): {exc}")

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def auto_apply(self) -> bool:
        return self._auto_apply

    @property
    def is_degraded(self) -> bool:
        if self._persistence is None:
            return True
        try:
            return bool(getattr(self._persistence, "is_degraded", False))
        except Exception:
            return False

    @property
    def generator(self) -> DecisionProposalGenerator:
        return self._generator

    @property
    def approval(self) -> DecisionApprovalManager:
        return self._approval

    @property
    def create_count(self) -> int:
        with self._lock:
            return self._create_count

    @property
    def generate_count(self) -> int:
        with self._lock:
            return self._generate_count

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._error_count

    @property
    def recover_count(self) -> int:
        with self._lock:
            return self._recover_count

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def set_persistence(self, persistence: Any) -> None:
        with self._lock:
            self._persistence = persistence
            self._approval = DecisionApprovalManager(
                store=self._store,
                persistence=persistence,
            )

    # --------------------------------------------------------
    # 核心 1: generate_from_intelligence()
    # --------------------------------------------------------

    def generate_from_intelligence(
        self,
        intelligence_report: Optional[Dict[str, Any]] = None,
    ) -> List[DecisionProposal]:
        """
        从 B.11 intelligence report 生成提案(全部 status=pending)。

        Args:
            intelligence_report: B.11 export_intelligence_report() 的输出
                                 若为空,自动从 bridge 获取

        Returns:
            List[DecisionProposal](全部 pending)
        """
        proposals: List[DecisionProposal] = []
        try:
            if not self._enabled or self._closed:
                return proposals

            # 自动获取 intelligence report
            if intelligence_report is None:
                intelligence_report = self._fetch_intelligence_report()

            proposals = self._generator.generate(intelligence_report)

            # 写入 store
            with self._lock:
                for p in proposals:
                    self._store.add(p)
                    self._persist_proposal_event(p, stage=STAGE_PROPOSAL_CREATED, decision="created")
                self._generate_count += 1
                self._last_generate_at = _now_iso()

            return proposals
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b12] generate_from_intelligence 异常(已隔离): {exc}")
            return proposals

    def _fetch_intelligence_report(self) -> Dict[str, Any]:
        """从 runtime bridge 获取 B.11 report(若可访问)"""
        # B.12 不直接持有 bridge;report 由调用方注入
        # 这里仅返回空 dict,让 generator 走空状态路径
        return {}

    # --------------------------------------------------------
    # 核心 2: create_proposal()
    # --------------------------------------------------------

    def create_proposal(
        self,
        proposal_type: str,
        target_action_type: str,
        suggested_change: str,
        reason: str = "",
        evidence: Optional[List[Dict[str, Any]]] = None,
        confidence: float = 0.5,
        source_report_hash: str = "",
    ) -> Optional[DecisionProposal]:
        """
        手动创建一个 proposal(供测试 + 外部调用)。

        Returns:
            DecisionProposal 实例(失败时返回 None)
        """
        try:
            if not self._enabled or self._closed:
                return None
            ptype = str(proposal_type or PROPOSAL_TYPE_MONITOR).lower()
            if ptype not in ALL_PROPOSAL_TYPES:
                ptype = PROPOSAL_TYPE_MONITOR

            conf = _clip(confidence)
            if conf < self._min_confidence:
                # 低于最小阈值,记录为 rejected_low_confidence
                return None

            expires_at: Optional[str] = None
            if self._max_age_seconds > 0:
                try:
                    from datetime import timedelta
                    expire_dt = datetime.now(timezone.utc) + timedelta(seconds=self._max_age_seconds)
                    expires_at = expire_dt.isoformat().replace("+00:00", "Z")
                except Exception:
                    expires_at = None

            p = DecisionProposal(
                proposal_type=ptype,
                target_action_type=str(target_action_type or ""),
                current_state={},
                suggested_change=str(suggested_change or ""),
                reason=str(reason or ""),
                evidence=list(evidence or []),
                confidence=conf,
                status=STATUS_PENDING,
                expires_at=expires_at,
                source_report_hash=str(source_report_hash or ""),
                metadata={"source": "phase_b12_runtime.create_proposal"},
            )
            with self._lock:
                self._store.add(p)
                self._create_count += 1
            self._persist_proposal_event(p, stage=STAGE_PROPOSAL_CREATED, decision="created")
            return p
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b12] create_proposal 异常(已隔离): {exc}")
            return None

    # --------------------------------------------------------
    # 核心 3: 审批接口
    # --------------------------------------------------------

    def approve_proposal(
        self,
        proposal_id: str,
        reviewer_id: str = "",
        comment: str = "",
    ) -> bool:
        """批准一个 proposal"""
        return self._approval.approve(proposal_id, reviewer_id, comment)

    def reject_proposal(
        self,
        proposal_id: str,
        reviewer_id: str = "",
        comment: str = "",
    ) -> bool:
        """拒绝一个 proposal"""
        return self._approval.reject(proposal_id, reviewer_id, comment)

    def mark_proposal_applied(
        self,
        proposal_id: str,
        applied_by: str = "",
    ) -> bool:
        """标记 approved proposal 为已应用(B.13 hook)"""
        return self._approval.mark_applied(proposal_id, applied_by)

    # --------------------------------------------------------
    # 核心 4: 查询接口
    # --------------------------------------------------------

    def get_proposal(self, proposal_id: str) -> Optional[DecisionProposal]:
        """获取单个 proposal"""
        return self._store.get(proposal_id)

    def get_pending_proposals(
        self,
        limit: int = 50,
    ) -> List[DecisionProposal]:
        """获取待审批 proposal"""
        return self._store.list_pending(limit=limit)

    def get_proposals_by_status(
        self,
        status: str,
        limit: int = 50,
    ) -> List[DecisionProposal]:
        """按状态获取 proposal"""
        return self._store.list(status=status, limit=limit)

    def get_proposals_by_type(
        self,
        proposal_type: str,
        limit: int = 50,
    ) -> List[DecisionProposal]:
        """按类型获取 proposal"""
        return self._store.list(proposal_type=proposal_type, limit=limit)

    def get_proposals_by_action_type(
        self,
        target_action_type: str,
        limit: int = 50,
    ) -> List[DecisionProposal]:
        """按 target_action_type 获取 proposal"""
        return self._store.list(
            target_action_type=target_action_type,
            limit=limit,
        )

    def export_proposals(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """导出 proposal 列表(只读)"""
        return [p.to_dict() for p in self._store.list(status=status, limit=limit)]

    # --------------------------------------------------------
    # 核心 5: 摘要
    # --------------------------------------------------------

    def get_evolution_summary(self) -> Dict[str, Any]:
        """演化闭环摘要"""
        with self._lock:
            counts = self._store.count_by_status()
            stats = self._store.get_stats()
            return {
                "name": PHASE_B12_NAME,
                "version": PHASE_B12_VERSION,
                "schema_version": SCHEMA_VERSION,
                "enabled": self._enabled,
                "auto_apply": self._auto_apply,
                "degraded": self.is_degraded,
                "closed": self._closed,
                "create_count": int(self._create_count),
                "generate_count": int(self._generate_count),
                "error_count": int(self._error_count),
                "recover_count": int(self._recover_count),
                "last_generate_at": self._last_generate_at,
                "last_error": self._last_error,
                "created_at": self._created_at,
                "in_memory_proposals": stats["in_memory"],
                "max_proposals": int(self._max_proposals),
                "max_age_seconds": float(self._max_age_seconds),
                "min_confidence": float(self._min_confidence),
                "pending_count": int(counts.get(STATUS_PENDING, 0)),
                "approved_count": int(counts.get(STATUS_APPROVED, 0)),
                "rejected_count": int(counts.get(STATUS_REJECTED, 0)),
                "applied_count": int(counts.get(STATUS_APPLIED, 0)),
                "expired_count": int(counts.get(STATUS_EXPIRED, 0)),
                "total_count": int(counts.get("total", 0)),
                "ts": time.time(),
            }

    def health_check(self) -> Dict[str, Any]:
        """模块健康度"""
        with self._lock:
            summary = self.get_evolution_summary()
            persistence_status: Dict[str, Any] = {"enabled": False}
            if self._persistence is not None:
                try:
                    if hasattr(self._persistence, "health_check"):
                        persistence_status = self._persistence.health_check() or persistence_status
                except Exception:  # noqa: BLE001
                    pass
            summary["persistence"] = persistence_status
            return summary

    # --------------------------------------------------------
    # 内部辅助
    # --------------------------------------------------------

    def _persist_proposal_event(
        self,
        proposal: DecisionProposal,
        stage: str,
        decision: str,
    ) -> None:
        """写一条 proposal 状态变化到 persistence"""
        if self._persistence is None:
            return
        try:
            if not hasattr(self._persistence, "persist_event"):
                return
            ts = time.time()
            aid = f"dprop_{proposal.proposal_id}"
            lc_id = f"dprop_{proposal.proposal_id}_{int(ts * 1000)}_{uuid.uuid4().hex[:6]}"
            ok = self._persistence.persist_event(
                action_id=aid,
                lifecycle_id=lc_id,
                stage=stage,
                decision=decision,
                ts=ts,
                result=proposal.to_dict(),
                source="phase_b12_evolution",
            )
            if not ok:
                with self._lock:
                    self._error_count += 1
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = repr(exc)
            logger.debug(f"[phase_b12] _persist_proposal_event 异常(已隔离): {exc}")

    def _load_state(self) -> int:
        """从 persistence 恢复 proposal 历史"""
        if self._persistence is None:
            return 0
        try:
            if not hasattr(self._persistence, "get_recent_actions"):
                return 0
            recents = self._persistence.get_recent_actions(
                limit=self._max_proposals * 2
            ) or []
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] _load_state 失败(已隔离): {exc}")
            return 0

        # latest-wins by proposal_id
        latest_map: Dict[str, Dict[str, Any]] = {}
        stages_of_interest = (
            STAGE_PROPOSAL_CREATED,
            STAGE_PROPOSAL_APPROVED,
            STAGE_PROPOSAL_REJECTED,
            STAGE_PROPOSAL_APPLIED,
            STAGE_PROPOSAL_EXPIRED,
        )
        try:
            for r in recents:
                if not isinstance(r, dict):
                    continue
                stage = str(r.get("stage", ""))
                if stage not in stages_of_interest:
                    continue
                result = r.get("result") or {}
                if not isinstance(result, dict):
                    continue
                pid = str(result.get("proposal_id", "") or "")
                if not pid:
                    continue
                ts = _safe_float(r.get("ts", 0.0) or 0.0)
                cur = latest_map.get(pid)
                if cur is None or ts >= _safe_float(cur.get("ts", 0.0)):
                    latest_map[pid] = {"result": dict(result), "ts": ts, "stage": stage}
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] _load_state 遍历异常(已隔离): {exc}")
            return 0

        if not latest_map:
            return 0

        count = 0
        try:
            for v in latest_map.values():
                result = v.get("result") or {}
                if not isinstance(result, dict):
                    continue
                p = DecisionProposal.from_dict(result)
                if self._store.add(p):
                    count += 1
            with self._lock:
                self._recover_count = count
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_b12] _load_state 灌库异常(已隔离): {exc}")
        return count

    def clear(self) -> None:
        """清空(测试用)"""
        with self._lock:
            self._store.clear()
            self._create_count = 0
            self._generate_count = 0
            self._error_count = 0
            self._recover_count = 0
            self._last_error = None
            self._last_generate_at = None


# ============================================================
# 工厂 / 便捷函数
# ============================================================

def create_decision_evolution_runtime(
    persistence: Any = None,
    bridge: Any = None,
    cfg: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
) -> DecisionEvolutionRuntime:
    """
    工厂:根据 cfg 构造 DecisionEvolutionRuntime

    Args:
        persistence: ActionPersistenceManager 实例(可选)
        bridge:      RuntimeB4Bridge 实例(可选,B.12 暂不直接使用,保留接口)
        cfg:         完整 cfg,会取 cfg["decision_evolution"]
        enabled:     显式覆盖 enabled

    Returns:
        DecisionEvolutionRuntime 实例
    """
    r_cfg: Dict[str, Any] = {}
    if isinstance(cfg, dict):
        raw = cfg.get("decision_evolution", {}) or {}
        if isinstance(raw, dict):
            r_cfg = raw

    is_enabled = (
        bool(enabled) if enabled is not None else bool(r_cfg.get("enabled", True))
    )
    max_proposals = int(r_cfg.get("max_proposals", DEFAULT_MAX_PROPOSALS))
    max_age_seconds = float(r_cfg.get("max_age_seconds", 7 * 24 * 3600.0))
    # B.12 硬约束:auto_apply 始终 False
    auto_apply = False
    min_confidence = float(r_cfg.get("min_confidence", 0.3))
    boost_confidence_threshold = float(r_cfg.get("boost_confidence_threshold", 0.7))
    suppress_confidence_threshold = float(r_cfg.get("suppress_confidence_threshold", 0.6))
    monitor_drift_threshold = float(r_cfg.get("monitor_drift_threshold", 0.15))

    # 如果 bridge 传入了,自动获取 persistence
    if persistence is None and bridge is not None:
        try:
            persistence = getattr(bridge, "persistence", None)
        except Exception:
            persistence = None

    return DecisionEvolutionRuntime(
        persistence=persistence,
        max_proposals=max_proposals,
        max_age_seconds=max_age_seconds,
        auto_apply=auto_apply,  # 永远 False
        min_confidence=min_confidence,
        boost_confidence_threshold=boost_confidence_threshold,
        suppress_confidence_threshold=suppress_confidence_threshold,
        monitor_drift_threshold=monitor_drift_threshold,
        enabled=is_enabled,
    )


# ============================================================
# 兼容 / 安全包装
# ============================================================

def safe_get_evolution_summary(
    evolution: Optional["DecisionEvolutionRuntime"],
) -> Dict[str, Any]:
    """全局安全 evolution summary(任何异常都吸收)"""
    empty = {
        "name": PHASE_B12_NAME,
        "version": PHASE_B12_VERSION,
        "enabled": False,
        "degraded": True,
        "closed": False,
        "create_count": 0,
        "generate_count": 0,
        "error_count": 0,
        "pending_count": 0,
        "approved_count": 0,
        "rejected_count": 0,
        "applied_count": 0,
        "expired_count": 0,
        "total_count": 0,
    }
    if evolution is None:
        return empty
    try:
        return evolution.get_evolution_summary() or empty
    except Exception:
        return empty


__all__ = [
    "PHASE_B12_DEFAULT_CONFIG",
    "PHASE_B12_NAME",
    "PHASE_B12_VERSION",
    "SCHEMA_VERSION",
    "PROPOSAL_TYPE_BOOST",
    "PROPOSAL_TYPE_SUPPRESS",
    "PROPOSAL_TYPE_MONITOR",
    "ALL_PROPOSAL_TYPES",
    "STATUS_PENDING",
    "STATUS_APPROVED",
    "STATUS_REJECTED",
    "STATUS_APPLIED",
    "STATUS_EXPIRED",
    "ALL_STATUSES",
    "TERMINAL_STATUSES",
    "STAGE_PROPOSAL_CREATED",
    "STAGE_PROPOSAL_APPROVED",
    "STAGE_PROPOSAL_REJECTED",
    "STAGE_PROPOSAL_APPLIED",
    "STAGE_PROPOSAL_EXPIRED",
    "apply_phase_b12_config",
    "is_phase_b12_enabled",
    "DecisionProposal",
    "DecisionProposalGenerator",
    "DecisionApprovalManager",
    "DecisionEvolutionRuntime",
    "create_decision_evolution_runtime",
    "safe_get_evolution_summary",
]
