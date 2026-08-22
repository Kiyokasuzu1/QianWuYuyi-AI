"""
SelfModel 变化治理契约 (SelfModelGovernance) v1.0

Phase 4.0.3 — 统一 Governance Contract

职责：
- GovernancePolicy: 判断 GrowthRecord 能否进入 SelfModel（纯决策函数，无副作用）
- GovernanceDecision: 不可变决策结果（可审计、可比较）
- SelfModelApprovalQueue: 管理 APPROVAL_REQUIRED Proposal 的审批生命周期

设计原则：
1. growth_level 决定"能否进入 SelfModel"，不是"怎么写 SelfModel"
2. GovernancePolicy 是唯一决策点，任何路径不可绕过
3. Proposal 不携带治理状态（不污染数据模型）
4. 决策结果不可变、可审计、可测试

硬契约：
    任何 SelfModelStore.apply_change_proposal()
        ↑
        │
    必须来自：
        1. AUTO_APPLY 决策
        2. 已批准的 Proposal（经过 ApprovalQueue.approve()）

    不能出现第三条"某模块觉得可信所以直接写 Store"的路径。

数据流：
    GrowthRecord
        ↓
    GovernancePolicy.evaluate()
        ↓
    GovernanceDecision
        ├── DENY → 丢弃/audit
        ├── AUTO_APPLY → Proposal → Apply
        └── APPROVAL_REQUIRED → Proposal → Pending → Approve → Apply
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, List


def _record_governance_decision(decision, growth_record):
    """P2.6 Phase A：SelfModel 治理决策观察（只读，失败静默吞掉，不改变任何行为）。"""
    try:
        from src.governance.audit_probe import record_governance_audit

        rule = SelfModelGovernancePolicy.RULES.get(decision.growth_level)
        record_governance_audit(
            source_path="legacy_self_model_policy",
            domain="self_model",
            mutation_type="self_model_governance_decision",
            decision=decision.action.value,
            payload_summary={
                "growth_level": decision.growth_level,
                "confidence": decision.confidence,
                "threshold_context": (rule or {}).get("min_confidence"),
                "reason": decision.reason,
            },
            triggered_by="legacy_orchestrator",
            request_id=str(growth_record.get("record_id", "") or ""),
        )
    except Exception:
        pass


class GovernanceAction(Enum):
    """治理决策动作"""
    DENY = "deny"
    AUTO_APPLY = "auto_apply"
    APPROVAL_REQUIRED = "approval_required"


# ============================================================
# G-1.2: 本地 self_model 治理开关（默认关闭 → legacy 直写路径不变；
# 不翻转任何全局治理 flag）
# ============================================================
_SELF_MODEL_GOVERNANCE_ENABLED = False


def is_self_model_governance_enabled() -> bool:
    """G-1.2: 本地 self_model 治理模式是否开启（默认 False = legacy）。"""
    return _SELF_MODEL_GOVERNANCE_ENABLED


def set_self_model_governance_enabled(enabled: bool) -> None:
    """G-1.2: 显式开启/关闭 self_model 治理（测试/装配方调用；生产默认关闭）。"""
    global _SELF_MODEL_GOVERNANCE_ENABLED
    _SELF_MODEL_GOVERNANCE_ENABLED = bool(enabled)


@dataclass(frozen=True)
class GovernanceDecision:
    """不可变治理决策结果。

    可审计、可比较、无副作用。
    """
    action: GovernanceAction
    growth_level: str
    confidence: float
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __eq__(self, other) -> bool:
        """两条决策等价判定：action + level + confidence 一致。

        注意：不比较 metadata 和 reason，因为它们是辅助信息。
        这使得不同 Policy 实例对同一记录可以判定为等价的决策。
        """
        if not isinstance(other, GovernanceDecision):
            return False
        return (
            self.action == other.action
            and self.growth_level == other.growth_level
            and abs(self.confidence - other.confidence) < 0.001
        )

    def __hash__(self) -> int:
        return hash((self.action, self.growth_level, round(self.confidence, 3)))


class SelfModelGovernancePolicy:
    """SelfModel 变化治理契约 — 唯一规则定义点。

    设计原则：
    1. 纯函数：相同输入 → 相同输出，不依赖外部状态
    2. 不持有任何可变状态，不写任何 Store
    3. 所有路径共享同一规则定义，但不需要共享同一实例
    4. 规则是声明式的，不依赖调用方上下文
    """

    RULES = {
        "context": {
            "action": GovernanceAction.AUTO_APPLY,
            "min_confidence": 0.50,
        },
        "preference": {
            "action": GovernanceAction.AUTO_APPLY,
            "min_confidence": 0.65,
            "fallback": GovernanceAction.APPROVAL_REQUIRED,
        },
        "trait": {
            "action": GovernanceAction.APPROVAL_REQUIRED,
            "min_confidence": 0.80,
        },
        "identity": {
            "action": GovernanceAction.DENY,
            "min_confidence": 0.90,
            "reason": "identity 级别当前不可修改，需未来架构支持",
        },
        "trace": {
            "action": GovernanceAction.DENY,
            "min_confidence": 0.0,
            "reason": "trace 级别不产生 SelfModel 变化",
        },
    }

    DEFAULT_LEVEL = "context"

    def evaluate(self, growth_record: Dict[str, Any]) -> GovernanceDecision:
        """评估 GrowthRecord → GovernanceDecision（P2.6 Phase A：附带只读审计观察）。

        Args:
            growth_record: GrowthRecord 字典（来自 GrowthPipeline 或 GrowthIntegrationService）

        Returns:
            GovernanceDecision（不可变）——与 Phase A 之前完全一致；
            审计记录失败静默吞掉，不影响决策结果。
        """
        decision = self._evaluate_rule(growth_record)
        _record_governance_decision(decision, growth_record)
        return decision

    def _evaluate_rule(self, growth_record: Dict[str, Any]) -> GovernanceDecision:
        """纯规则决策（原 evaluate 逻辑，无副作用）。"""
        level = growth_record.get("growth_level", self.DEFAULT_LEVEL)
        confidence = float(growth_record.get("confidence", 0.5))

        rule = self.RULES.get(level)
        if rule is None:
            return GovernanceDecision(
                action=GovernanceAction.DENY,
                growth_level=level,
                confidence=confidence,
                reason=f"未知 growth_level: {level}",
            )

        # DENY 规则
        if rule["action"] == GovernanceAction.DENY:
            return GovernanceDecision(
                action=GovernanceAction.DENY,
                growth_level=level,
                confidence=confidence,
                reason=rule.get("reason", f"{level} 级别禁止 SelfModel 变化"),
            )

        # CONDITIONAL（有 fallback）：优先处理，min_confidence 是 AUTO_APPLY 的门槛
        if "fallback" in rule:
            if confidence >= rule["min_confidence"]:
                return GovernanceDecision(
                    action=GovernanceAction.AUTO_APPLY,
                    growth_level=level,
                    confidence=confidence,
                    reason=f"{level} confidence={confidence:.2f} >= {rule['min_confidence']} → auto_apply",
                )
            else:
                return GovernanceDecision(
                    action=rule["fallback"],
                    growth_level=level,
                    confidence=confidence,
                    reason=f"{level} confidence={confidence:.2f} < {rule['min_confidence']} → {rule['fallback'].value}",
                )

        # 非 CONDITIONAL：置信度低于最低门槛 → DENY
        if confidence < rule["min_confidence"]:
            return GovernanceDecision(
                action=GovernanceAction.DENY,
                growth_level=level,
                confidence=confidence,
                reason=f"{level} confidence={confidence:.2f} < min {rule['min_confidence']}",
            )

        # 直接决策（AUTO_APPLY / APPROVAL_REQUIRED）
        return GovernanceDecision(
            action=rule["action"],
            growth_level=level,
            confidence=confidence,
            reason=f"{level} 默认治理: {rule['action'].value}",
        )


class SelfModelApprovalQueue:
    """待审批 SelfModelChangeProposal 队列。

    职责：管理 APPROVAL_REQUIRED 的 Proposal 生命周期。
    不负责：判断能否进入（由 GovernancePolicy 负责）、写 Store（由 Updater 负责）。

    使用方式：
        queue = SelfModelApprovalQueue()
        queue.enqueue(proposal)          # 入队
        proposal = queue.approve(pid)    # 审批通过 → 返回 proposal 供 apply
        queue.reject(pid, reason="...")  # 拒绝 → 移出队列
    """

    def __init__(self):
        self._pending: Dict[str, Any] = {}   # proposal_id → proposal
        self._history: List[Dict[str, Any]] = []

    def enqueue(self, proposal) -> None:
        """将 proposal 加入待审批队列。"""
        pid = proposal.suggestion_id
        self._pending[pid] = proposal

    def approve(self, proposal_id: str):
        """批准 → 返回 proposal 供 Updater.apply_proposal() 使用。

        Phase 4.0.3-C: 设置 proposal.requires_approval = False，
        作为"已批准"标记，供 apply_suggestion() 安全闸验证。
        """
        proposal = self._pending.pop(proposal_id, None)
        if proposal is not None:
            proposal.requires_approval = False  # Phase 4.0.3-C: 批准标记
            self._history.append({
                "proposal_id": proposal_id,
                "action": "approved",
            })
        return proposal

    def reject(self, proposal_id: str, reason: str = "") -> None:
        """拒绝 → 移出队列。"""
        self._pending.pop(proposal_id, None)
        self._history.append({
            "proposal_id": proposal_id,
            "action": "rejected",
            "reason": reason,
        })

    def get_pending(self) -> list:
        """获取所有待审批 proposal。"""
        return list(self._pending.values())

    def get_pending_ids(self) -> List[str]:
        """获取所有待审批 proposal_id。"""
        return list(self._pending.keys())

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)