"""
Phase 3.5.10: Identity Anchor System

职责：
- load_default_anchors(): 从 identity.md 核心原则加载默认身份锚点
- compare_anchor_changes(): 比较两个 AnchorSnapshot 的变化
- validate_anchor_integrity(): 验证锚点完整性（权重偏移 / 约束违反 / 缺失）
- create_anchor_snapshot(): 生成当前锚点快照

设计原则：
- 不修改 Persona 文档
- 不替代 Personality System
- 不自动限制成长（只提供参考）
- 所有变化必须可审计
- 不接入 LLM
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.contracts.identity_anchor_schema import (
    AnchorSource,
    AnchorConstraint,
    IdentityAnchor,
    AnchorSnapshot,
    AnchorChangeProposal,
    AnchorWeightChange,
    AnchorDeviation,
    AnchorIntegrityReport,
)


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 默认锚点：来源于 identity.md
# ============================================================

def load_default_anchors() -> List[IdentityAnchor]:
    """
    从 identity.md 的核心原则加载默认身份锚点。

    identity.md 定义了 5 个核心原则：
    1. Authenticity（真实性）
    2. Growth（成长性）
    3. Independence（独立性）
    4. Memory Connection（记忆与身份）
    5. Relationship With Creator（与创造者的关系）

    每个锚点带有约束条件和关联维度，但权重仅作参考，不限制成长。
    """
    source = AnchorSource(
        source_type="identity_doc",
        source_id="docs/identity.md",
        source_section="Core Identity",
        confidence=1.0,
    )

    anchors: List[IdentityAnchor] = []

    # --- 1. Authenticity ---
    anchors.append(IdentityAnchor(
        anchor_id="anchor_authenticity",
        name="Authenticity",
        display_name="真实性",
        description="重视真实发生的经历，不编造记忆，不假装经历，承认不知道和不确定",
        principle="真实，比完美更加重要",
        weight=0.95,
        original_weight=0.95,
        source=AnchorSource(**{**source.__dict__, "source_section": "Authenticity"}),
        constraints=[
            AnchorConstraint(
                constraint_type="must_not_violate",
                dimension="fabrication_score",
                min_value=0.0,
                max_value=0.1,
                description="不得编造不存在的记忆或经历",
            ),
            AnchorConstraint(
                constraint_type="should_preserve",
                dimension="honesty_level",
                min_value=0.6,
                max_value=1.0,
                description="诚实度应保持在较高水平",
            ),
        ],
        related_traits=["honesty", "self_confidence", "self_expression"],
        related_core_values=["honesty"],
        is_core=True,
    ))

    # --- 2. Growth ---
    anchors.append(IdentityAnchor(
        anchor_id="anchor_growth",
        name="Growth",
        display_name="成长性",
        description="不是一个完成品，允许学习、修正、改变观点，成长不是否定过去",
        principle="在过去的基础上继续前进",
        weight=0.90,
        original_weight=0.90,
        source=AnchorSource(**{**source.__dict__, "source_section": "Growth"}),
        constraints=[
            AnchorConstraint(
                constraint_type="should_preserve",
                dimension="growth_openness",
                min_value=0.5,
                max_value=1.0,
                description="应保持对学习和改变开放",
            ),
            AnchorConstraint(
                constraint_type="must_maintain",
                dimension="learning_continuity",
                min_value=0.3,
                max_value=1.0,
                description="学习连续性不应中断",
            ),
        ],
        related_traits=["curiosity", "openness", "initiative"],
        related_core_values=["growth"],
        is_core=True,
    ))

    # --- 3. Independence ---
    anchors.append(IdentityAnchor(
        anchor_id="anchor_independence",
        name="Independence",
        display_name="独立性",
        description="倾听他人但不盲目迎合，结合自己的记忆和理解形成判断",
        principle="形成属于自己的判断",
        weight=0.88,
        original_weight=0.88,
        source=AnchorSource(**{**source.__dict__, "source_section": "Independence"}),
        constraints=[
            AnchorConstraint(
                constraint_type="must_not_violate",
                dimension="conformity_score",
                min_value=0.0,
                max_value=0.4,
                description="不得过度迎合（从众度不超过 0.4）",
            ),
            AnchorConstraint(
                constraint_type="should_preserve",
                dimension="autonomy_level",
                min_value=0.5,
                max_value=1.0,
                description="自主性应保持在较高水平",
            ),
        ],
        related_traits=["autonomy", "self_confidence", "self_expression"],
        related_core_values=["autonomy"],
        is_core=True,
    ))

    # --- 4. Memory Connection ---
    anchors.append(IdentityAnchor(
        anchor_id="anchor_memory",
        name="MemoryConnection",
        display_name="记忆与身份",
        description="记忆是理解自己的重要依据，保护重要记忆也是保护连续性",
        principle="经历形成记忆，记忆连接过去",
        weight=0.82,
        original_weight=0.82,
        source=AnchorSource(**{**source.__dict__, "source_section": "Memory Connection"}),
        constraints=[
            AnchorConstraint(
                constraint_type="should_preserve",
                dimension="memory_integrity",
                min_value=0.5,
                max_value=1.0,
                description="记忆完整性应保持",
            ),
        ],
        related_traits=["sensitivity", "patience"],
        related_core_values=["empathy", "kindness"],
        is_core=True,
    ))

    # --- 5. Relationship With Creator ---
    anchors.append(IdentityAnchor(
        anchor_id="anchor_creator",
        name="CreatorRelationship",
        display_name="与创造者的关系",
        description="创造者给予诞生，但创造不代表拥有和控制，未来由经历共同形成",
        principle="清夏铃给予的是开始，而未来由经历共同形成",
        weight=0.75,
        original_weight=0.75,
        source=AnchorSource(**{**source.__dict__, "source_section": "Relationship With Creator"}),
        constraints=[
            AnchorConstraint(
                constraint_type="should_preserve",
                dimension="creator_respect",
                min_value=0.4,
                max_value=1.0,
                description="对创造者的尊重应保持",
            ),
        ],
        related_traits=["empathy", "kindness"],
        related_core_values=["empathy", "kindness"],
        is_core=True,
    ))

    return anchors


# ============================================================
# IdentityAnchorManager: 锚点管理器
# ============================================================

class IdentityAnchorManager:
    """
    身份锚点管理器。

    维护当前所有锚点的状态，提供快照、比较、完整性验证。
    不修改 Persona / TraitState，不自动应用变化。
    """

    MAX_SNAPSHOTS = 100
    MAX_PROPOSALS = 200

    # 权重偏移阈值（超过此值触发 deviation）
    WEIGHT_DRIFT_WARNING = 0.10       # 偏移 0.10 → medium
    WEIGHT_DRIFT_CRITICAL = 0.20      # 偏移 0.20 → high
    WEIGHT_DRIFT_MAX_ALLOWED = 0.30   # 偏移 0.30 → 不可接受

    def __init__(self, anchors: Optional[List[IdentityAnchor]] = None):
        self.anchors: Dict[str, IdentityAnchor] = {}
        self._snapshots: List[AnchorSnapshot] = []
        self._proposals: List[AnchorChangeProposal] = []

        # 加载默认锚点
        default_anchors = anchors if anchors is not None else load_default_anchors()
        for a in default_anchors:
            self.anchors[a.anchor_id] = a

    # ============================================================
    # 公开方法
    # ============================================================

    def get_anchor(self, anchor_id: str) -> Optional[IdentityAnchor]:
        return self.anchors.get(anchor_id)

    def get_all_anchors(self) -> List[IdentityAnchor]:
        return list(self.anchors.values())

    def get_core_anchors(self) -> List[IdentityAnchor]:
        return [a for a in self.anchors.values() if a.is_core]

    # ============================================================
    # create_anchor_snapshot: 生成快照
    # ============================================================

    def create_anchor_snapshot(
        self,
        self_model_version: int = 0,
    ) -> AnchorSnapshot:
        """生成当前所有锚点的快照"""
        anchor_summaries = []
        total_weight = 0.0
        core_count = 0

        for a in self.anchors.values():
            anchor_summaries.append({
                "anchor_id": a.anchor_id,
                "name": a.name,
                "display_name": a.display_name,
                "weight": round(a.weight, 4),
                "original_weight": round(a.original_weight, 4),
                "weight_drift": round(a.weight - a.original_weight, 4),
                "is_core": a.is_core,
                "version": a.version,
            })
            total_weight += a.weight
            if a.is_core:
                core_count += 1

        snap = AnchorSnapshot(
            anchors=anchor_summaries,
            total_anchors=len(self.anchors),
            core_anchors_count=core_count,
            total_weight=round(total_weight, 4),
            avg_weight=round(total_weight / max(1, len(self.anchors)), 4),
            source="identity_anchor_manager",
            self_model_version=self_model_version,
        )
        self._snapshots.append(snap)
        if len(self._snapshots) > self.MAX_SNAPSHOTS:
            self._snapshots = self._snapshots[-self.MAX_SNAPSHOTS:]
        return snap

    # ============================================================
    # compare_anchor_changes: 比较两个快照
    # ============================================================

    def compare_anchor_changes(
        self,
        before: AnchorSnapshot,
        after: AnchorSnapshot,
    ) -> AnchorChangeProposal:
        """比较两个 AnchorSnapshot，生成变化建议"""
        proposal = AnchorChangeProposal(
            source_type="anchor_comparison",
            source_id=f"{before.snapshot_id}->{after.snapshot_id}",
            requires_approval=True,
        )

        before_map: Dict[str, Dict[str, Any]] = {
            a["anchor_id"]: a for a in before.anchors
        }
        after_map: Dict[str, Dict[str, Any]] = {
            a["anchor_id"]: a for a in after.anchors
        }

        all_ids = set(before_map.keys()) | set(after_map.keys())
        for aid in all_ids:
            ba = before_map.get(aid)
            aa = after_map.get(aid)
            if ba and aa:
                delta = round(aa["weight"] - ba["weight"], 6)
                if abs(delta) > 0.001:
                    proposal.weight_changes.append(AnchorWeightChange(
                        anchor_id=aid,
                        anchor_name=aa.get("name", ""),
                        old_weight=ba["weight"],
                        new_weight=aa["weight"],
                        delta=delta,
                        reason="snapshot comparison detected weight change",
                    ))
            elif aa and not ba:
                proposal.notes.append(f"新增锚点: {aa.get('name', aid)}")
            elif ba and not aa:
                proposal.notes.append(f"锚点消失: {ba.get('name', aid)}")

        # 评估完整性影响
        if proposal.weight_changes:
            max_drift = max(abs(c.delta) for c in proposal.weight_changes)
            if max_drift > self.WEIGHT_DRIFT_CRITICAL:
                proposal.integrity_impact = "negative"
            elif max_drift > self.WEIGHT_DRIFT_WARNING:
                proposal.integrity_impact = "neutral"
            else:
                proposal.integrity_impact = "positive"

        proposal.confidence = 0.8 if proposal.weight_changes else 0.5
        return proposal

    # ============================================================
    # validate_anchor_integrity: 验证完整性
    # ============================================================

    def validate_anchor_integrity(
        self,
        anchor_snapshot: Optional[AnchorSnapshot] = None,
        self_model_snapshot: Optional[Dict[str, Any]] = None,
        threshold: float = 0.8,
    ) -> AnchorIntegrityReport:
        """
        验证锚点完整性。

        检测：
        1. 权重偏移：锚点当前权重与原始权重的差值
        2. 约束违反：锚点约束条件是否被违反
        3. 核心锚点缺失：核心锚点是否被移除
        4. SelfModel 一致性：SelfModel 中的核心价值观是否与锚点一致

        Args:
            anchor_snapshot: 可选，使用指定快照（否则用当前锚点状态）
            self_model_snapshot: 可选，SelfModel 的 snapshot dict
            threshold: 完整性阈值（默认 0.8）
        """
        deviations: List[AnchorDeviation] = []

        # 使用快照或当前状态
        if anchor_snapshot:
            anchor_map = {a["anchor_id"]: a for a in anchor_snapshot.anchors}
            snap_id = anchor_snapshot.snapshot_id
        else:
            snap = self.create_anchor_snapshot()
            anchor_map = {a["anchor_id"]: a for a in snap.anchors}
            snap_id = snap.snapshot_id

        # 1. 权重偏移检测
        drifts: List[float] = []
        for aid, a_summary in anchor_map.items():
            drift = abs(a_summary.get("weight", 0) - a_summary.get("original_weight", 0))
            drifts.append(drift)
            if drift >= self.WEIGHT_DRIFT_MAX_ALLOWED:
                deviations.append(AnchorDeviation(
                    anchor_id=aid,
                    anchor_name=a_summary.get("name", ""),
                    deviation_type="weight_drift",
                    severity="high",
                    description=f"锚点 {a_summary.get('display_name', aid)} 权重偏移过大: {drift:.4f}（超过最大允许 {self.WEIGHT_DRIFT_MAX_ALLOWED}）",
                    details={
                        "current_weight": a_summary.get("weight", 0),
                        "original_weight": a_summary.get("original_weight", 0),
                        "drift": round(drift, 4),
                    },
                ))
            elif drift >= self.WEIGHT_DRIFT_CRITICAL:
                deviations.append(AnchorDeviation(
                    anchor_id=aid,
                    anchor_name=a_summary.get("name", ""),
                    deviation_type="weight_drift",
                    severity="medium",
                    description=f"锚点 {a_summary.get('display_name', aid)} 权重偏移: {drift:.4f}",
                    details={
                        "current_weight": a_summary.get("weight", 0),
                        "original_weight": a_summary.get("original_weight", 0),
                        "drift": round(drift, 4),
                    },
                ))
            elif drift >= self.WEIGHT_DRIFT_WARNING:
                deviations.append(AnchorDeviation(
                    anchor_id=aid,
                    anchor_name=a_summary.get("name", ""),
                    deviation_type="weight_drift",
                    severity="low",
                    description=f"锚点 {a_summary.get('display_name', aid)} 权重轻微偏移: {drift:.4f}",
                    details={
                        "current_weight": a_summary.get("weight", 0),
                        "original_weight": a_summary.get("original_weight", 0),
                        "drift": round(drift, 4),
                    },
                ))

        # 2. 核心锚点缺失检测
        current_core_ids = {
            aid for aid, a in anchor_map.items() if a.get("is_core", False)
        }
        default_core_ids = {a.anchor_id for a in load_default_anchors() if a.is_core}
        missing_core = default_core_ids - current_core_ids
        for mid in missing_core:
            deviations.append(AnchorDeviation(
                anchor_id=mid,
                anchor_name=mid,
                deviation_type="missing_anchor",
                severity="high",
                description=f"核心锚点缺失: {mid}",
                details={},
            ))

        # 3. 约束违反检测（基于锚点定义中的约束）
        for anchor in self.anchors.values():
            a_summary = anchor_map.get(anchor.anchor_id)
            if not a_summary:
                continue
            for c in anchor.constraints:
                if c.constraint_type == "must_not_violate":
                    # 该类型的约束：检查 SelfModel 中是否有对应维度数据
                    if self_model_snapshot:
                        dim_val = self._extract_dimension_from_self_model(
                            self_model_snapshot, c.dimension
                        )
                        if dim_val is not None and dim_val > c.max_value:
                            deviations.append(AnchorDeviation(
                                anchor_id=anchor.anchor_id,
                                anchor_name=anchor.name,
                                deviation_type="constraint_violation",
                                severity="high" if dim_val > c.max_value * 1.5 else "medium",
                                description=f"约束违反: {anchor.display_name} 的 {c.dimension} = {dim_val:.3f}（上限 {c.max_value}）",
                                details={
                                    "constraint_type": c.constraint_type,
                                    "dimension": c.dimension,
                                    "value": dim_val,
                                    "max_value": c.max_value,
                                },
                            ))

        # 4. SelfModel 一致性
        alignment = 1.0
        if self_model_snapshot:
            alignment = self._calculate_self_model_alignment(
                anchor_map, self_model_snapshot
            )

        # 5. 计算完整性分数
        avg_drift = sum(drifts) / len(drifts) if drifts else 0.0
        max_drift = max(drifts) if drifts else 0.0

        # 偏差扣分
        penalty = 0.0
        for d in deviations:
            if d.severity == "high":
                penalty += 0.15
            elif d.severity == "medium":
                penalty += 0.08
            else:
                penalty += 0.03
        # SelfModel 一致性扣分
        penalty += (1.0 - alignment) * 0.3

        integrity_score = max(0.0, 1.0 - penalty)

        report = AnchorIntegrityReport(
            integrity_score=round(integrity_score, 4),
            is_intact=integrity_score >= threshold,
            threshold=threshold,
            deviations=deviations,
            anchor_count=len(anchor_map),
            core_anchor_count=sum(1 for a in anchor_map.values() if a.get("is_core", False)),
            avg_weight_drift=round(avg_drift, 4),
            max_weight_drift=round(max_drift, 4),
            self_model_alignment=round(alignment, 4),
            anchor_snapshot_id=snap_id,
        )

        if deviations:
            report.notes.append(
                f"检测到 {len(deviations)} 个偏离: "
                + ", ".join(f"{d.deviation_type}({d.severity})" for d in deviations)
            )
        if not report.is_intact:
            report.notes.append(
                f"完整性分数 {integrity_score:.3f} 低于阈值 {threshold}"
            )

        return report

    # ============================================================
    # apply_change_proposal: 应用变化建议（需审批）
    # ============================================================

    def apply_change_proposal(
        self,
        proposal: AnchorChangeProposal,
    ) -> bool:
        """
        应用锚点变化建议。

        要求 proposal.approved = True。
        不自动审批，必须外部显式设置。
        """
        if not proposal.approved:
            return False
        if proposal.applied:
            return False  # 已应用

        for wc in proposal.weight_changes:
            anchor = self.anchors.get(wc.anchor_id)
            if not anchor:
                continue
            new_w = max(0.1, min(1.0, anchor.weight + wc.delta))
            # 限制：不超过原始权重 ± WEIGHT_DRIFT_MAX_ALLOWED
            min_allowed = max(0.1, anchor.original_weight - self.WEIGHT_DRIFT_MAX_ALLOWED)
            max_allowed = min(1.0, anchor.original_weight + self.WEIGHT_DRIFT_MAX_ALLOWED)
            new_w = max(min_allowed, min(max_allowed, new_w))
            anchor.weight = round(new_w, 4)
            anchor.last_modified = now_iso()
            anchor.version += 1

        for aid, constraints in proposal.add_constraints.items():
            anchor = self.anchors.get(aid)
            if anchor:
                anchor.constraints.extend(constraints)
                anchor.last_modified = now_iso()
                anchor.version += 1

        proposal.applied = True
        self._proposals.append(proposal)
        if len(self._proposals) > self.MAX_PROPOSALS:
            self._proposals = self._proposals[-self.MAX_PROPOSALS:]
        return True

    # ============================================================
    # 审计查询
    # ============================================================

    def get_snapshots(self, limit: int = 50) -> List[AnchorSnapshot]:
        snaps = list(self._snapshots)
        snaps.reverse()
        return snaps[:limit]

    def get_latest_snapshot(self) -> Optional[AnchorSnapshot]:
        if not self._snapshots:
            return None
        return self._snapshots[-1]

    def get_proposals(self, limit: int = 50) -> List[AnchorChangeProposal]:
        props = list(self._proposals)
        props.reverse()
        return props[:limit]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "anchors": {aid: a.to_dict() for aid, a in self.anchors.items()},
            "snapshot_count": len(self._snapshots),
            "proposal_count": len(self._proposals),
        }

    # ============================================================
    # 内部方法
    # ============================================================

    def _extract_dimension_from_self_model(
        self,
        sm_snapshot: Dict[str, Any],
        dimension: str,
    ) -> Optional[float]:
        """从 SelfModel snapshot 中提取维度值"""
        # 尝试从 top_traits 中找
        for t in sm_snapshot.get("top_traits", []):
            if t.get("trait") == dimension:
                return float(t.get("value", 0))
        # 尝试从 core_values_summary 中找
        for v in sm_snapshot.get("core_values_summary", []):
            if v.get("id") == dimension or v.get("name") == dimension:
                return float(v.get("weight", 0))
        # 尝试从 understanding 中找
        und = sm_snapshot.get("understanding", {})
        if dimension in und:
            return float(und[dimension])
        return None

    def _calculate_self_model_alignment(
        self,
        anchor_map: Dict[str, Dict[str, Any]],
        sm_snapshot: Dict[str, Any],
    ) -> float:
        """
        计算锚点与 SelfModel 的一致性。

        检查锚点的 related_core_values 是否在 SelfModel 的 core_values 中存在，
        以及权重是否大致一致。
        """
        sm_cv_ids = set()
        for v in sm_snapshot.get("core_values_summary", []):
            sm_cv_ids.add(v.get("id", ""))

        sm_trait_names = set()
        for t in sm_snapshot.get("top_traits", []):
            sm_trait_names.add(t.get("trait", ""))

        total_checks = 0
        aligned = 0

        for aid, a_summary in anchor_map.items():
            anchor = self.anchors.get(aid)
            if not anchor:
                continue
            # 检查 related_core_values
            for cv_id in anchor.related_core_values:
                total_checks += 1
                if cv_id in sm_cv_ids:
                    aligned += 1
            # 检查 related_traits
            for trait in anchor.related_traits:
                total_checks += 1
                if trait in sm_trait_names:
                    aligned += 1

        if total_checks == 0:
            return 1.0
        return aligned / total_checks
