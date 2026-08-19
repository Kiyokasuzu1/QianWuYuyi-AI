"""
SelfModel 更新器 (SelfModelUpdater) v1.3

Phase 3.8.3 — Growth → SelfModel Cognitive Closure
Phase 3.8.5 — 通过 SelfModelStore.apply_change_proposal() 公开接口操作，不再直接访问 _current_model
Phase 3.8.6 — 拆分 create_proposal_from_growth() / update_from_growth()，支持两阶段生命周期
Phase 4.0.3 — 接入 SelfModelGovernancePolicy，统一治理规则

职责：
将 GrowthRecord 转化为 SelfModel 的自我理解更新。
不做人格修改，只更新"羽依认为自己是什么样的人"。

设计原则：
- RULE 1: SelfModel 不是人格修改器。不修改 PersonalityState。
- RULE 2: 所有变化可追溯。必须包含 source_growth_id / source_event_id / evidence / confidence。
- RULE 3: 不创建第二套 SelfModel。优先扩展 SelfModelStore 已有接口。
- RULE 4 (Phase 3.8.6): create_proposal 只生成不写入，update_from_growth 内部调用 create + apply。
- RULE 5 (Phase 4.0.3): Governance 决定能否 Apply，Updater 只负责 Proposal 生成和 Apply 执行。

硬契约：
    任何 SelfModelStore.apply_change_proposal() 必须来自：
        1. GovernancePolicy 返回 AUTO_APPLY 的路径
        2. 已批准的 Proposal（通过 ApprovalQueue.approve()）

数据流 (v1.3):
    GrowthRecord
        ↓
    GovernancePolicy.evaluate()
        ↓
    GovernanceDecision
        ├── DENY → 丢弃
        ├── AUTO_APPLY → create_proposal_from_growth() → apply_proposal()
        └── APPROVAL_REQUIRED → create_proposal_from_growth() → ApprovalQueue → apply_proposal()
"""

from __future__ import annotations

import uuid
import logging
from typing import Any, Dict, List, Optional, Callable
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ============================================================
# SelfModelChangeProposal
# ============================================================

@dataclass
class SelfModelChangeProposal:
    """自我模型变化提案。

    不是人格修改，而是"羽依如何理解自己"的更新。

    Phase 3.8.6: 新增 suggestion_id / requires_approval 字段，
    兼容 RuntimeCore 的 pending/approval 生命周期。
    """
    change_type: str  # "narrative_append" | "self_understanding_update"
    target: str       # 目标字段路径，如 "growth_narratives" / "self_understanding"
    change: Dict[str, Any] = field(default_factory=dict)
    source: Dict[str, Any] = field(default_factory=dict)  # {growth_id, source_event_id, evidence_ids, confidence}
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    # Phase 3.8.6: RuntimeCore 兼容字段
    suggestion_id: str = field(default_factory=lambda: f"sug_{uuid.uuid4().hex[:10]}")
    requires_approval: bool = True  # 默认需要审批

    @property
    def source_type(self) -> str:
        """兼容 SelfModelChangeSuggestion.source_type 访问"""
        return self.source.get("source_type", "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "change_type": self.change_type,
            "target": self.target,
            "change": self.change,
            "source": self.source,
            "timestamp": self.timestamp,
            "suggestion_id": self.suggestion_id,
            "requires_approval": self.requires_approval,
        }


# ============================================================
# SelfModelUpdater
# ============================================================

class SelfModelUpdater:
    """
    Growth → SelfModel 桥梁。

    接收 GrowthRecord，生成 SelfModelChangeProposal，
    并更新 SelfModelStore 的自我理解。
    """

    def __init__(
        self,
        self_model_store: Optional[Any] = None,
        narrative_generator: Optional[Callable] = None,
        governance_policy: Optional[Any] = None,
    ):
        """
        Args:
            self_model_store: SelfModelStore 实例（必需）
            narrative_generator: 可选的外部叙事生成函数（LLM 调用），
                                 默认使用内置规则生成。
            governance_policy: 可选的外部治理策略实例。
                               为 None 时延迟创建默认 SelfModelGovernancePolicy。
                               推荐在 Orchestrator 和 RuntimeCore 注入同一规则定义。
        """
        self.store = self_model_store
        self._narrative_generator = narrative_generator
        self._applied_proposals: List[SelfModelChangeProposal] = []

        # Phase 4.0.3: GovernancePolicy — 延迟加载，避免循环导入
        if governance_policy is not None:
            self._governance = governance_policy
        else:
            self._governance = None  # 延迟创建

    # ============================================================
    # 主入口：从 GrowthRecord 更新 SelfModel
    # ============================================================

    def create_proposal_from_growth(
        self,
        growth_record: Dict[str, Any],
    ) -> Optional[SelfModelChangeProposal]:
        """
        Phase 3.8.6: 仅生成 SelfModelChangeProposal，不写入 Store。

        供 SelfModelUpdaterAdapter 使用，保持 RuntimeCore 的
        "生成 → 审批 → 应用" 两阶段生命周期。

        Args:
            growth_record: GrowthRecord TypedDict（来自 src/growth/growth_record.py）

        Returns:
            SelfModelChangeProposal 或 None（当无意义更新时）

        契约：
        - 调用此方法后，Store 状态不变
        - 提案不会记录到 _applied_proposals
        """
        if not self._validate_growth_record(growth_record):
            logger.debug("SelfModelUpdater: GrowthRecord 验证失败，跳过")
            return None

        # 1. 生成成长叙事
        narrative = self._generate_narrative(growth_record)

        # 2. 构建变化提案（不写入 Store）
        proposal = SelfModelChangeProposal(
            change_type="narrative_append",
            target="growth_narratives",
            change={
                "narrative": narrative,
                "dimension": self._primary_dimension(growth_record),
                "growth_level": growth_record.get("growth_level", ""),
                "growth_signal": growth_record.get("growth_signal", ""),
                "event": growth_record.get("reason", ""),
                "meaning": self._extract_meaning(growth_record),
            },
            source={
                "growth_id": growth_record.get("record_id", ""),
                "source_event_id": growth_record.get("source_event_id", ""),
                "source_type": growth_record.get("source_type", ""),
                "affected_dimensions": growth_record.get("affected_dimensions", {}),
                "evidence_ids": self._collect_evidence(growth_record),
                "confidence": growth_record.get("confidence", 0.5),
            },
        )

        return proposal

    def _ensure_governance(self):
        """延迟创建默认 GovernancePolicy（避免循环导入）。"""
        if self._governance is None:
            from src.personality.self_model_governance import SelfModelGovernancePolicy
            self._governance = SelfModelGovernancePolicy()
        return self._governance

    def update_from_growth(
        self,
        growth_record: Dict[str, Any],
    ) -> Optional[SelfModelChangeProposal]:
        """
        从一条 GrowthRecord 生成 SelfModel 更新。

        Phase 4.0.3: 通过 GovernancePolicy 决定是否 Apply。
        - AUTO_APPLY → 生成 + 立即应用
        - APPROVAL_REQUIRED → 生成 proposal 但不应用（调用方需入队 ApprovalQueue）
        - DENY → 丢弃

        这是向后兼容的便捷入口。新主链推荐使用:
            GovernancePolicy.evaluate() → create_proposal_from_growth() → apply_proposal()

        Args:
            growth_record: GrowthRecord TypedDict

        Returns:
            SelfModelChangeProposal 或 None
            - AUTO_APPLY: 已应用的 proposal
            - APPROVAL_REQUIRED: 未应用的 proposal（需入队审批）
            - DENY: None
        """
        governance = self._ensure_governance()
        decision = governance.evaluate(growth_record)

        if decision.action.value == "deny":
            return None

        proposal = self.create_proposal_from_growth(growth_record)
        if proposal is None:
            return None

        if decision.action.value == "auto_apply":
            self._apply_to_store(proposal)
            self._applied_proposals.append(proposal)
            return proposal

        # APPROVAL_REQUIRED: 生成 proposal 但不 apply
        # 调用方需将 proposal 入队到 SelfModelApprovalQueue
        return proposal

    def apply_proposal(self, proposal: SelfModelChangeProposal) -> Optional[SelfModelChangeProposal]:
        """
        公开方法：应用已批准的 Proposal。

        供 ApprovalQueue.approve() 之后调用。
        APPROVAL_REQUIRED 路径的 Proposal 必须通过此方法应用到 Store。

        Args:
            proposal: SelfModelChangeProposal（已通过审批）

        Returns:
            已应用的 proposal
        """
        if proposal is None:
            return None
        self._apply_to_store(proposal)
        self._applied_proposals.append(proposal)
        return proposal

    def update_from_growth_records(
        self,
        growth_records: List[Dict[str, Any]],
    ) -> List[SelfModelChangeProposal]:
        """
        批量处理多条 GrowthRecord。

        Phase 4.0.3: 保留作为向后兼容的便捷方法。
        新主链推荐逐个 evaluate + create_proposal + apply_proposal。

        Args:
            growth_records: GrowthRecord 列表

        Returns:
            已应用的提案列表（不含 APPROVAL_REQUIRED 的未应用提案）
        """
        applied = []
        for record in growth_records:
            proposal = self.update_from_growth(record)
            if proposal is not None:
                applied.append(proposal)
        return applied

    # ============================================================
    # 内部方法
    # ============================================================

    def _validate_growth_record(self, record: Dict) -> bool:
        """验证 GrowthRecord 是否值得更新 SelfModel。

        条件：
        - 有 record_id 和 source_event_id
        - confidence >= 0.5（至少中等置信度）
        - 有 affected_dimensions
        - 有 reason 或 growth_signal
        """
        if not record:
            return False
        if not record.get("record_id"):
            return False
        confidence = record.get("confidence", 0)
        if confidence < 0.5:
            return False
        if not record.get("affected_dimensions"):
            return False
        if not record.get("reason") and not record.get("growth_signal"):
            return False
        return True

    def _primary_dimension(self, record: Dict) -> str:
        """获取主要影响维度"""
        dims = record.get("affected_dimensions", {})
        if not dims:
            return ""
        # 返回 delta 最大的维度
        if isinstance(dims, dict):
            return max(dims, key=lambda k: abs(dims[k]))
        return list(dims.keys())[0] if dims else ""

    def _extract_meaning(self, record: Dict) -> str:
        """从 GrowthRecord 提取意义描述"""
        reason = record.get("reason", "")
        growth_signal = record.get("growth_signal", "")
        if reason:
            return reason
        if growth_signal:
            return f"成长信号: {growth_signal}"
        return ""

    def _collect_evidence(self, record: Dict) -> List[str]:
        """收集证据 ID 列表"""
        evidence = []
        sid = record.get("source_event_id", "")
        if sid:
            evidence.append(sid)
        rid = record.get("record_id", "")
        if rid:
            evidence.append(rid)
        return evidence

    def _generate_narrative(self, record: Dict) -> str:
        """
        生成第一人称成长叙事。

        优先使用外部注入的 narrative_generator（LLM），
        否则使用内置规则生成。
        """
        if self._narrative_generator is not None:
            try:
                return self._narrative_generator(record)
            except Exception:
                logger.warning("SelfModelUpdater: 外部叙事生成失败，回退到内置规则")

        # 内置规则生成
        dim = self._primary_dimension(record)
        signal = record.get("growth_signal", "")
        confidence = record.get("confidence", 0.5)

        # 维度友好名称映射
        dim_names = {
            "creativity": "创造力",
            "self_confidence": "自信",
            "self_expression": "自我表达",
            "curiosity": "好奇心",
            "initiative": "主动性",
            "trust": "信任",
            "closeness": "亲密感",
            "warmth": "温暖",
            "attachment": "依恋",
            "security": "安全感",
            "emotional_memory": "情感记忆",
            "identity_strength": "身份强度",
            "self_awareness": "自我意识",
            "long_term_focus": "长期关注",
        }

        dim_name = dim_names.get(dim, dim)
        confidence_desc = "逐渐" if confidence < 0.7 else "明确"

        if signal:
            return f"我注意到{confidence_desc}形成了{dim_name}方面的成长（{signal}）"
        return f"我{confidence_desc}感受到自己在{dim_name}方面有所变化"

    def _apply_to_store(self, proposal: SelfModelChangeProposal) -> None:
        """
        将提案应用到 SelfModelStore。

        Phase 3.8.5: 优先通过 Store 公开接口 apply_change_proposal() 操作，
        保持状态一致性。旧 Store 无此方法时回退到直接操作（向后兼容）。

        约束：
        - 不重建整个 SelfModel（不调用 store.update()）
        - 只追加 growth_narratives
        - 更新 self_understanding
        - store 为 None 时静默跳过
        """
        if self.store is None:
            return

        try:
            # Phase 3.8.5: 通过 Store 公开接口操作
            if hasattr(self.store, "apply_change_proposal"):
                self.store.apply_change_proposal(proposal)
            else:
                # 向后兼容：旧 Store 无此方法时，回退到直接操作
                self._apply_to_store_legacy(proposal)
        except Exception as e:
            logger.warning("SelfModelUpdater: 应用到 Store 失败（已隔离）: %s", e)

    def _apply_to_store_legacy(self, proposal: SelfModelChangeProposal) -> None:
        """
        旧路径：直接操作 Store._current_model（向后兼容）。

        仅当 Store 不支持 apply_change_proposal() 公开接口时使用。
        未来版本可移除。
        """
        if self.store is None:
            return

        try:
            current = self.store.get()
            if current is None:
                # 尚无 SelfModel，留待后续 Builder 构建
                return

            # 1. 追加 growth_narratives
            if proposal.change_type == "narrative_append":
                narratives = current.get("growth_narratives", [])
                if not isinstance(narratives, list):
                    narratives = []

                narrative_entry = {
                    "record_id": proposal.source.get("growth_id", ""),
                    "dimension": proposal.change.get("dimension", ""),
                    "event": proposal.change.get("event", ""),
                    "narrative": proposal.change.get("narrative", ""),
                    "meaning": proposal.change.get("meaning", ""),
                    "timestamp": proposal.timestamp,
                    # 可追溯字段
                    "_source_growth_id": proposal.source.get("growth_id", ""),
                    "_source_event_id": proposal.source.get("source_event_id", ""),
                    "_evidence_ids": proposal.source.get("evidence_ids", []),
                    "_confidence": proposal.source.get("confidence", 0.5),
                }
                narratives.append(narrative_entry)

                # 控制 narratives 数量（最多保留 20 条）
                if len(narratives) > 20:
                    narratives = narratives[-20:]

                current["growth_narratives"] = narratives

            # 2. 更新 self_understanding
            self._update_self_understanding(current, proposal)

            # 3. 更新 last_updated
            current["last_updated"] = datetime.now().isoformat()

        except Exception as e:
            logger.warning(f"SelfModelUpdater: 应用到 Store 失败（已隔离）: {e}")

    def _update_self_understanding(
        self,
        current: Dict[str, Any],
        proposal: SelfModelChangeProposal,
    ) -> None:
        """
        更新 self_understanding 指标。

        基于新增的成长叙事，微调自我理解水平。
        不做大幅跳跃，每次最多 +0.05。
        """
        understanding = current.get("self_understanding", {})
        if not isinstance(understanding, dict):
            understanding = {}

        confidence = proposal.source.get("confidence", 0.5)
        increment = min(0.05, confidence * 0.05)

        # experience_awareness: "我记得发生过什么"
        understanding["experience_awareness"] = min(
            1.0,
            understanding.get("experience_awareness", 0.3) + increment,
        )

        # trait_awareness: "我知道这些经历如何影响我"
        understanding["trait_awareness"] = min(
            1.0,
            understanding.get("trait_awareness", 0.2) + increment * 0.8,
        )

        # identity_continuity: "我知道变化后的自己仍然是我"
        growth_level = proposal.change.get("growth_level", "context")
        if growth_level in ("trait", "preference"):
            understanding["identity_continuity"] = min(
                1.0,
                understanding.get("identity_continuity", 0.4) + increment * 0.5,
            )

        # overall: 综合理解水平
        exp = understanding.get("experience_awareness", 0.3)
        trt = understanding.get("trait_awareness", 0.2)
        idn = understanding.get("identity_continuity", 0.4)
        understanding["overall"] = round((exp + trt + idn) / 3, 3)

        current["self_understanding"] = understanding

    # ============================================================
    # 查询接口
    # ============================================================

    def get_applied_proposals(self) -> List[SelfModelChangeProposal]:
        """获取本轮已应用的提案列表"""
        return self._applied_proposals

    def get_recent_narratives(self, n: int = 5) -> List[Dict]:
        """获取最近 n 条成长叙事"""
        if self.store is None:
            return []
        current = self.store.get()
        if current is None:
            return []
        narratives = current.get("growth_narratives", [])
        if not isinstance(narratives, list):
            return []
        return narratives[-n:][::-1]

    def get_self_understanding(self) -> Dict[str, float]:
        """获取当前自我理解水平"""
        if self.store is None:
            return {}
        current = self.store.get()
        if current is None:
            return {}
        return dict(current.get("self_understanding", {}))