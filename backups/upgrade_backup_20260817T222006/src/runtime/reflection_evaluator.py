"""
Phase 3.5.12: Reflection Evaluation Layer

职责：
- 在 Reflection → Growth 之间增加结构化判断层
- 评估 ReflectionInsight / GrowthRecord / IdentityChange / RelationshipState 的「价值」
- 输出 ReflectionEvaluation（推荐结论 + 维度评分 + 风险标记）

评估维度（5 维）：
- novelty（新颖性）：是否提供新信息
- relevance（相关性）：与当前身份/状态的关联度
- consistency（一致性）：与已有记忆/特质的一致程度
- stability（稳定性）：是否为偶发噪声
- alignment（身份对齐度）：是否符合核心身份原则

约束：
- 不引入 LLM
- 不修改 Persona / TraitState
- 不自动接受 GrowthProposal（仅产出评估意见）
- 所有评估行为可审计（保留历史）

设计原则：
- 纯函数式评估，不修改输入对象
- 评分基于数值指标 + 规则，不做自然语言推理
- 推荐结论仅作为人工审批的参考，不直接驱动状态流转
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.contracts.reflection_evaluation_schema import (
    ALL_DIMENSIONS,
    DIMENSION_NOVELTY,
    DIMENSION_RELEVANCE,
    DIMENSION_CONSISTENCY,
    DIMENSION_STABILITY,
    DIMENSION_ALIGNMENT,
    RECOMMENDATION_ACCEPT,
    RECOMMENDATION_REJECT,
    RECOMMENDATION_DEFER,
    RECOMMENDATION_REVISIT,
    SOURCE_REFLECTION_INSIGHT,
    SOURCE_GROWTH_RECORD,
    SOURCE_IDENTITY_CHANGE,
    SOURCE_RELATIONSHIP,
    EvaluationDimension,
    EvaluationHistoryEntry,
    EvaluatorSnapshot,
    ReflectionEvaluation,
)


# ============================================================
# 评估配置
# ============================================================

@dataclass
class EvaluatorConfig:
    """评估器配置（阈值可调）"""
    # 综合分阈值
    accept_threshold: float = 0.65       # >= 则推荐 accept
    reject_threshold: float = 0.35       # < 则推荐 reject
    defer_evidence_threshold: int = 2    # 证据数 < 此值且非 reject → defer

    # 单维度阈值
    min_alignment_score: float = 0.5     # 低于此值触发 identity_misalignment 风险
    min_stability_score: float = 0.4      # 低于此值触发 instability 风险
    min_consistency_score: float = 0.4   # 低于此值触发 contradiction 风险

    # 风险标记阈值
    max_trait_delta: float = 0.15         # 特质变化超过此值触发 magnitude_risk
    max_cv_delta: float = 0.10            # 核心价值观变化超过此值触发 value_risk
    min_evidence_count: int = 1           # 最少证据数

    # 历史记录上限
    max_history: int = 200

    # 维度权重（综合分计算）
    weights: Dict[str, float] = field(default_factory=lambda: {
        DIMENSION_NOVELTY: 0.15,
        DIMENSION_RELEVANCE: 0.25,
        DIMENSION_CONSISTENCY: 0.25,
        DIMENSION_STABILITY: 0.20,
        DIMENSION_ALIGNMENT: 0.15,
    })


# ============================================================
# ReflectionEvaluator: 评估器主类
# ============================================================

class ReflectionEvaluator:
    """
    反思价值评估器。

    用法：
        evaluator = ReflectionEvaluator()
        evaluation = evaluator.evaluate_insight(insight)
        # evaluation.recommendation → accept / reject / defer / revisit
        # evaluation.overall_score → 0~1
    """

    def __init__(self, config: Optional[EvaluatorConfig] = None):
        self.config = config or EvaluatorConfig()
        self._history: List[EvaluationHistoryEntry] = []
        self._last_evaluation: Optional[ReflectionEvaluation] = None

        # 统计
        self._total_evaluations: int = 0
        self._total_accept: int = 0
        self._total_reject: int = 0
        self._total_defer: int = 0
        self._total_revisit: int = 0

    # ============================================================
    # 公开接口：评估入口
    # ============================================================

    def evaluate_insight(self, insight: Any) -> ReflectionEvaluation:
        """
        评估 ReflectionInsight。

        insight 可以是 dataclass 对象或 dict。
        """
        data = self._to_dict(insight)
        insight_id = str(data.get("insight_id", ""))
        source_summary = str(data.get("summary", ""))[:200]

        # 提取指标
        confidence = float(data.get("confidence", 0.5))
        pattern_frequency = int(data.get("pattern_frequency", 0))
        experience_count = len(data.get("experience_ids", []) or [])
        insight_type = str(data.get("insight_type", "pattern"))
        suggested_adjustments = data.get("suggested_adjustments", []) or []

        # 评估各维度
        dimensions: List[EvaluationDimension] = []

        # novelty: 基于 insight_type 和 pattern_frequency
        # pattern 频率低 = 新颖；problem/improvement 类型 = 新颖
        if insight_type in ("problem", "improvement"):
            novelty_score = 0.7
            novelty_reason = f"insight_type={insight_type} 提供新视角"
        elif pattern_frequency <= 1:
            novelty_score = 0.6
            novelty_reason = "低频模式，可能提供新信息"
        elif pattern_frequency >= 5:
            novelty_score = 0.3
            novelty_reason = f"高频模式 (freq={pattern_frequency})，信息量有限"
        else:
            novelty_score = 0.5
            novelty_reason = f"常规模式 (freq={pattern_frequency})"
        dimensions.append(EvaluationDimension(
            name=DIMENSION_NOVELTY,
            score=novelty_score,
            weight=self.config.weights.get(DIMENSION_NOVELTY, 0.15),
            reason=novelty_reason,
            evidence=[f"insight_type={insight_type}", f"pattern_frequency={pattern_frequency}"],
        ))

        # relevance: 基于经验数和建议数
        # 关联经验多 = 相关性高
        relevance_score = min(1.0, 0.3 + experience_count * 0.15)
        if suggested_adjustments:
            relevance_score = min(1.0, relevance_score + 0.1)
        dimensions.append(EvaluationDimension(
            name=DIMENSION_RELEVANCE,
            score=relevance_score,
            weight=self.config.weights.get(DIMENSION_RELEVANCE, 0.25),
            reason=f"关联 {experience_count} 条经验，{len(suggested_adjustments)} 条建议",
            evidence=[f"experience_count={experience_count}", f"adjustments={len(suggested_adjustments)}"],
        ))

        # consistency: 基于 confidence
        consistency_score = confidence
        dimensions.append(EvaluationDimension(
            name=DIMENSION_CONSISTENCY,
            score=consistency_score,
            weight=self.config.weights.get(DIMENSION_CONSISTENCY, 0.25),
            reason=f"insight confidence={confidence:.2f}",
            evidence=[f"confidence={confidence}"],
        ))

        # stability: 基于模式频率（频率高 = 稳定）
        if pattern_frequency >= 3:
            stability_score = 0.7
            stability_reason = f"模式出现 {pattern_frequency} 次，较稳定"
        elif pattern_frequency >= 1:
            stability_score = 0.5
            stability_reason = f"模式出现 {pattern_frequency} 次，需更多验证"
        else:
            stability_score = 0.3
            stability_reason = "无频率数据，可能为偶发"
        dimensions.append(EvaluationDimension(
            name=DIMENSION_STABILITY,
            score=stability_score,
            weight=self.config.weights.get(DIMENSION_STABILITY, 0.20),
            reason=stability_reason,
            evidence=[f"pattern_frequency={pattern_frequency}"],
        ))

        # alignment: 默认中性（insight 本身不直接体现身份对齐）
        alignment_score = 0.6
        dimensions.append(EvaluationDimension(
            name=DIMENSION_ALIGNMENT,
            score=alignment_score,
            weight=self.config.weights.get(DIMENSION_ALIGNMENT, 0.15),
            reason="insight 不直接体现身份对齐，默认中性",
            evidence=[],
        ))

        return self._finalize_evaluation(
            source_type=SOURCE_REFLECTION_INSIGHT,
            source_id=insight_id,
            source_summary=source_summary,
            dimensions=dimensions,
            evidence_count=experience_count + pattern_frequency,
            metadata={"insight_type": insight_type, "pattern_frequency": pattern_frequency},
        )

    def evaluate_growth_record(self, record: Any) -> ReflectionEvaluation:
        """
        评估 GrowthRecord。

        record 可以是 dict（TypedDict）或对象。
        """
        data = self._to_dict(record)
        record_id = str(data.get("record_id", ""))
        growth_signal = str(data.get("growth_signal", ""))
        source_summary = f"growth_signal={growth_signal}, level={data.get('growth_level', '')}"

        confidence = float(data.get("confidence", 0.0))
        affected = data.get("affected_dimensions", {}) or {}
        growth_level = str(data.get("growth_level", "context"))
        source_type_val = str(data.get("source_type", "creation"))
        reason = str(data.get("reason", ""))

        # 最大变化量
        max_delta = max((abs(v) for v in affected.values()), default=0.0)

        dimensions: List[EvaluationDimension] = []

        # novelty: 基于来源类型
        novelty_map = {
            "milestone": 0.8,
            "identity": 0.7,
            "preference": 0.5,
            "creation": 0.4,
        }
        novelty_score = novelty_map.get(source_type_val, 0.5)
        dimensions.append(EvaluationDimension(
            name=DIMENSION_NOVELTY,
            score=novelty_score,
            weight=self.config.weights.get(DIMENSION_NOVELTY, 0.15),
            reason=f"source_type={source_type_val}",
            evidence=[f"source_type={source_type_val}"],
        ))

        # relevance: 基于成长层级
        level_relevance = {
            "trait": 0.9,
            "preference": 0.6,
            "context": 0.4,
        }
        relevance_score = level_relevance.get(growth_level, 0.5)
        dimensions.append(EvaluationDimension(
            name=DIMENSION_RELEVANCE,
            score=relevance_score,
            weight=self.config.weights.get(DIMENSION_RELEVANCE, 0.25),
            reason=f"growth_level={growth_level}",
            evidence=[f"growth_level={growth_level}", f"affected_dimensions={list(affected.keys())}"],
        ))

        # consistency: 基于 confidence
        consistency_score = confidence
        dimensions.append(EvaluationDimension(
            name=DIMENSION_CONSISTENCY,
            score=consistency_score,
            weight=self.config.weights.get(DIMENSION_CONSISTENCY, 0.25),
            reason=f"record confidence={confidence:.2f}",
            evidence=[f"confidence={confidence}"],
        ))

        # stability: 基于变化幅度（变化越小越稳定）
        if max_delta <= 0.01:
            stability_score = 0.8
            stability_reason = "微小变化，稳定"
        elif max_delta <= 0.05:
            stability_score = 0.6
            stability_reason = f"适度变化 (max_delta={max_delta:.3f})"
        else:
            stability_score = 0.3
            stability_reason = f"大幅变化 (max_delta={max_delta:.3f})，需验证"
        dimensions.append(EvaluationDimension(
            name=DIMENSION_STABILITY,
            score=stability_score,
            weight=self.config.weights.get(DIMENSION_STABILITY, 0.20),
            reason=stability_reason,
            evidence=[f"max_delta={max_delta:.4f}"],
        ))

        # alignment: trait 层级需特别关注身份对齐
        if growth_level == "trait":
            alignment_score = 0.5  # trait 变化需更谨慎
            alignment_reason = "trait 层级变化，身份对齐待验证"
        else:
            alignment_score = 0.7
            alignment_reason = f"{growth_level} 层级变化，对齐风险低"
        dimensions.append(EvaluationDimension(
            name=DIMENSION_ALIGNMENT,
            score=alignment_score,
            weight=self.config.weights.get(DIMENSION_ALIGNMENT, 0.15),
            reason=alignment_reason,
            evidence=[f"growth_level={growth_level}"],
        ))

        return self._finalize_evaluation(
            source_type=SOURCE_GROWTH_RECORD,
            source_id=record_id,
            source_summary=source_summary,
            dimensions=dimensions,
            evidence_count=1,  # GrowthRecord 本身是一条证据
            metadata={
                "growth_signal": growth_signal,
                "growth_level": growth_level,
                "max_delta": max_delta,
                # 复用 identity_change 的风险标记阈值（影响的是人格维度）
                "max_trait_delta": max_delta if growth_level == "trait" else 0.0,
                "max_cv_delta": 0.0,
            },
        )

    def evaluate_identity_change(
        self,
        change: Any,
        report: Optional[Any] = None,
    ) -> ReflectionEvaluation:
        """
        评估 IdentityChange（可选传入 ContinuityReport 辅助判断）。

        change / report 可以是 dataclass 对象或 dict。
        """
        change_data = self._to_dict(change)
        report_data = self._to_dict(report) if report else {}

        # IdentityChange 没有自带 id，用 change 内容 hash
        trait_count = len(change_data.get("changes", []) or [])
        cv_count = len(change_data.get("core_value_changes", []) or [])
        added_traits = change_data.get("added_traits", []) or []
        removed_traits = change_data.get("removed_traits", []) or []
        added_cv = change_data.get("added_core_values", []) or []
        removed_cv = change_data.get("removed_core_values", []) or []

        source_id = f"ic_{trait_count}_{cv_count}_{len(added_traits)}_{len(removed_traits)}"
        source_summary = (
            f"traits_changed={trait_count}, cv_changed={cv_count}, "
            f"added={len(added_traits)}, removed={len(removed_traits)}"
        )

        # 最大变化量
        trait_deltas = [abs(float(t.get("delta", 0))) for t in (change_data.get("changes", []) or [])]
        cv_deltas = [abs(float(c.get("delta", 0))) for c in (change_data.get("core_value_changes", []) or [])]
        max_trait_delta = max(trait_deltas, default=0.0)
        max_cv_delta = max(cv_deltas, default=0.0)

        # 从 ContinuityReport 获取连续性评分
        continuity_score = float(report_data.get("continuity_score", 1.0))
        is_continuous = bool(report_data.get("is_continuous", True))
        conflicts = report_data.get("conflicts", []) or []

        dimensions: List[EvaluationDimension] = []

        # novelty: 基于新增项
        novelty_count = len(added_traits) + len(added_cv)
        novelty_score = min(1.0, 0.3 + novelty_count * 0.2)
        dimensions.append(EvaluationDimension(
            name=DIMENSION_NOVELTY,
            score=novelty_score,
            weight=self.config.weights.get(DIMENSION_NOVELTY, 0.15),
            reason=f"新增 {novelty_count} 项",
            evidence=[f"added_traits={len(added_traits)}", f"added_cv={len(added_cv)}"],
        ))

        # relevance: 基于 trait 变化数
        relevance_score = min(1.0, 0.4 + trait_count * 0.1)
        dimensions.append(EvaluationDimension(
            name=DIMENSION_RELEVANCE,
            score=relevance_score,
            weight=self.config.weights.get(DIMENSION_RELEVANCE, 0.25),
            reason=f"{trait_count} 个特质变化",
            evidence=[f"trait_changes={trait_count}", f"cv_changes={cv_count}"],
        ))

        # consistency: 基于连续性评分
        consistency_score = continuity_score
        dimensions.append(EvaluationDimension(
            name=DIMENSION_CONSISTENCY,
            score=consistency_score,
            weight=self.config.weights.get(DIMENSION_CONSISTENCY, 0.25),
            reason=f"continuity_score={continuity_score:.3f}, is_continuous={is_continuous}",
            evidence=[f"continuity_score={continuity_score}", f"conflicts={len(conflicts)}"],
        ))

        # stability: 基于变化幅度
        max_delta = max(max_trait_delta, max_cv_delta)
        if max_delta <= 0.05:
            stability_score = 0.8
            stability_reason = "变化幅度小，稳定"
        elif max_delta <= 0.10:
            stability_score = 0.6
            stability_reason = f"适度变化 (max_delta={max_delta:.3f})"
        else:
            stability_score = 0.3
            stability_reason = f"大幅变化 (max_delta={max_delta:.3f})，需验证"
        dimensions.append(EvaluationDimension(
            name=DIMENSION_STABILITY,
            score=stability_score,
            weight=self.config.weights.get(DIMENSION_STABILITY, 0.20),
            reason=stability_reason,
            evidence=[f"max_trait_delta={max_trait_delta:.4f}", f"max_cv_delta={max_cv_delta:.4f}"],
        ))

        # alignment: 基于冲突数和结构完整性
        if not is_continuous:
            alignment_score = 0.2
            alignment_reason = f"身份连续性断裂 (score={continuity_score:.3f})"
        elif conflicts:
            alignment_score = 0.4
            alignment_reason = f"存在 {len(conflicts)} 个身份冲突"
        elif removed_traits or removed_cv:
            alignment_score = 0.5
            alignment_reason = f"消失 {len(removed_traits) + len(removed_cv)} 项，对齐待验证"
        else:
            alignment_score = 0.8
            alignment_reason = "无冲突，结构完整"
        dimensions.append(EvaluationDimension(
            name=DIMENSION_ALIGNMENT,
            score=alignment_score,
            weight=self.config.weights.get(DIMENSION_ALIGNMENT, 0.15),
            reason=alignment_reason,
            evidence=[f"is_continuous={is_continuous}", f"conflicts={len(conflicts)}"],
        ))

        return self._finalize_evaluation(
            source_type=SOURCE_IDENTITY_CHANGE,
            source_id=source_id,
            source_summary=source_summary,
            dimensions=dimensions,
            evidence_count=trait_count + cv_count + len(conflicts),
            metadata={
                "max_trait_delta": max_trait_delta,
                "max_cv_delta": max_cv_delta,
                "continuity_score": continuity_score,
                "conflict_count": len(conflicts),
            },
        )

    def evaluate_relationship(self, state: Any) -> ReflectionEvaluation:
        """
        评估 RelationshipState。

        state 可以是 dataclass 对象或 dict。
        """
        data = self._to_dict(state)
        familiarity = float(data.get("familiarity", 0.0))
        trust = float(data.get("trust", 0.0))
        collaboration = float(data.get("collaboration", 0.0))
        interaction_freq = float(data.get("interaction_frequency", 0.0))
        stage = str(data.get("relationship_stage", "initial"))

        source_id = f"rel_{stage}"
        source_summary = (
            f"stage={stage}, familiarity={familiarity:.2f}, "
            f"trust={trust:.2f}, collaboration={collaboration:.2f}"
        )

        dimensions: List[EvaluationDimension] = []

        # novelty: 基于 stage（新阶段 = 新信息）
        stage_novelty = {
            "initial": 0.7,
            "developing": 0.6,
            "stable": 0.3,
            "deep_collaboration": 0.5,
        }
        novelty_score = stage_novelty.get(stage, 0.5)
        dimensions.append(EvaluationDimension(
            name=DIMENSION_NOVELTY,
            score=novelty_score,
            weight=self.config.weights.get(DIMENSION_NOVELTY, 0.15),
            reason=f"relationship_stage={stage}",
            evidence=[f"stage={stage}"],
        ))

        # relevance: 基于互动频率
        relevance_score = min(1.0, 0.3 + interaction_freq * 0.5)
        dimensions.append(EvaluationDimension(
            name=DIMENSION_RELEVANCE,
            score=relevance_score,
            weight=self.config.weights.get(DIMENSION_RELEVANCE, 0.25),
            reason=f"interaction_frequency={interaction_freq:.2f}",
            evidence=[f"interaction_frequency={interaction_freq}"],
        ))

        # consistency: 基于信任度
        consistency_score = trust
        dimensions.append(EvaluationDimension(
            name=DIMENSION_CONSISTENCY,
            score=consistency_score,
            weight=self.config.weights.get(DIMENSION_CONSISTENCY, 0.25),
            reason=f"trust={trust:.2f}",
            evidence=[f"trust={trust}"],
        ))

        # stability: 基于熟悉度和协作度
        stability_score = (familiarity + collaboration) / 2.0
        dimensions.append(EvaluationDimension(
            name=DIMENSION_STABILITY,
            score=stability_score,
            weight=self.config.weights.get(DIMENSION_STABILITY, 0.20),
            reason=f"familiarity={familiarity:.2f}, collaboration={collaboration:.2f}",
            evidence=[f"familiarity={familiarity}", f"collaboration={collaboration}"],
        ))

        # alignment: 关系状态对身份影响中性
        alignment_score = 0.7
        dimensions.append(EvaluationDimension(
            name=DIMENSION_ALIGNMENT,
            score=alignment_score,
            weight=self.config.weights.get(DIMENSION_ALIGNMENT, 0.15),
            reason="关系状态不直接威胁身份对齐",
            evidence=[],
        ))

        return self._finalize_evaluation(
            source_type=SOURCE_RELATIONSHIP,
            source_id=source_id,
            source_summary=source_summary,
            dimensions=dimensions,
            evidence_count=4,  # 4 个数值指标
            metadata={
                "stage": stage,
                "familiarity": familiarity,
                "trust": trust,
                "collaboration": collaboration,
            },
        )

    # ============================================================
    # 统一评估入口
    # ============================================================

    def evaluate(self, source_type: str, source: Any, *args, **kwargs) -> ReflectionEvaluation:
        """
        统一评估入口。

        Args:
            source_type: reflection_insight / growth_record / identity_change / relationship
            source: 待评估的对象
        """
        if source_type == SOURCE_REFLECTION_INSIGHT:
            return self.evaluate_insight(source)
        elif source_type == SOURCE_GROWTH_RECORD:
            return self.evaluate_growth_record(source)
        elif source_type == SOURCE_IDENTITY_CHANGE:
            report = kwargs.get("report") or (args[0] if args else None)
            return self.evaluate_identity_change(source, report)
        elif source_type == SOURCE_RELATIONSHIP:
            return self.evaluate_relationship(source)
        else:
            # 未知类型，返回低分评估
            return self._finalize_evaluation(
                source_type=source_type,
                source_id="",
                source_summary="unknown source type",
                dimensions=[],
                evidence_count=0,
                metadata={"error": f"unknown source_type: {source_type}"},
            )

    # ============================================================
    # 内部方法：综合评分与推荐结论
    # ============================================================

    def _finalize_evaluation(
        self,
        source_type: str,
        source_id: str,
        source_summary: str,
        dimensions: List[EvaluationDimension],
        evidence_count: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReflectionEvaluation:
        """计算综合分、推荐结论、风险标记，并记录历史"""
        risk_flags: List[str] = []
        notes: List[str] = []

        # 计算加权综合分
        if dimensions:
            total_weight = sum(d.weight for d in dimensions)
            if total_weight > 0:
                overall = sum(d.score * d.weight for d in dimensions) / total_weight
            else:
                overall = 0.0
        else:
            overall = 0.0
        overall = max(0.0, min(1.0, overall))

        # 评估自身可信度（基于证据数）
        confidence = min(1.0, evidence_count / 5.0)

        # 风险标记
        alignment_dim = next((d for d in dimensions if d.name == DIMENSION_ALIGNMENT), None)
        if alignment_dim and alignment_dim.score < self.config.min_alignment_score:
            risk_flags.append("identity_misalignment")
            notes.append(f"alignment={alignment_dim.score:.3f} < {self.config.min_alignment_score}")

        stability_dim = next((d for d in dimensions if d.name == DIMENSION_STABILITY), None)
        if stability_dim and stability_dim.score < self.config.min_stability_score:
            risk_flags.append("instability")
            notes.append(f"stability={stability_dim.score:.3f} < {self.config.min_stability_score}")

        consistency_dim = next((d for d in dimensions if d.name == DIMENSION_CONSISTENCY), None)
        if consistency_dim and consistency_dim.score < self.config.min_consistency_score:
            risk_flags.append("contradiction_risk")
            notes.append(f"consistency={consistency_dim.score:.3f} < {self.config.min_consistency_score}")

        if evidence_count < self.config.min_evidence_count:
            risk_flags.append("low_evidence")
            notes.append(f"evidence_count={evidence_count} < {self.config.min_evidence_count}")

        # 特质/价值观大幅变化风险
        max_trait_delta = float((metadata or {}).get("max_trait_delta", 0.0))
        max_cv_delta = float((metadata or {}).get("max_cv_delta", 0.0))
        if max_trait_delta > self.config.max_trait_delta:
            risk_flags.append("magnitude_risk")
            notes.append(f"max_trait_delta={max_trait_delta:.3f} > {self.config.max_trait_delta}")
        if max_cv_delta > self.config.max_cv_delta:
            risk_flags.append("value_risk")
            notes.append(f"max_cv_delta={max_cv_delta:.3f} > {self.config.max_cv_delta}")

        # 推荐结论
        if overall >= self.config.accept_threshold and "identity_misalignment" not in risk_flags:
            recommendation = RECOMMENDATION_ACCEPT
            recommendation_reason = f"overall={overall:.3f} >= {self.config.accept_threshold}，无身份对齐风险"
        elif overall < self.config.reject_threshold:
            recommendation = RECOMMENDATION_REJECT
            recommendation_reason = f"overall={overall:.3f} < {self.config.reject_threshold}"
        elif "identity_misalignment" in risk_flags:
            recommendation = RECOMMENDATION_REVISIT
            recommendation_reason = "存在身份对齐风险，需重新审视"
        elif evidence_count < self.config.defer_evidence_threshold:
            recommendation = RECOMMENDATION_DEFER
            recommendation_reason = f"证据不足 (evidence={evidence_count} < {self.config.defer_evidence_threshold})，暂缓"
        else:
            recommendation = RECOMMENDATION_DEFER
            recommendation_reason = f"overall={overall:.3f} 处于中间区间，暂缓等待更多证据"

        # 构造评估结果
        evaluation = ReflectionEvaluation(
            source_type=source_type,
            source_id=source_id,
            source_summary=source_summary,
            dimensions=dimensions,
            overall_score=overall,
            confidence=confidence,
            recommendation=recommendation,
            recommendation_reason=recommendation_reason,
            risk_flags=risk_flags,
            notes=notes,
            metadata=metadata or {},
        )

        # 记录历史
        self._last_evaluation = evaluation
        self._total_evaluations += 1
        if recommendation == RECOMMENDATION_ACCEPT:
            self._total_accept += 1
        elif recommendation == RECOMMENDATION_REJECT:
            self._total_reject += 1
        elif recommendation == RECOMMENDATION_DEFER:
            self._total_defer += 1
        elif recommendation == RECOMMENDATION_REVISIT:
            self._total_revisit += 1

        history_entry = EvaluationHistoryEntry(
            evaluation_id=evaluation.evaluation_id,
            source_type=source_type,
            source_id=source_id,
            overall_score=overall,
            recommendation=recommendation,
            risk_flags_count=len(risk_flags),
        )
        self._history.append(history_entry)
        if len(self._history) > self.config.max_history:
            self._history = self._history[-self.config.max_history:]

        return evaluation

    # ============================================================
    # 历史与快照
    # ============================================================

    def get_history(self, limit: int = 50) -> List[EvaluationHistoryEntry]:
        """获取评估历史（最新在前）"""
        hist = list(self._history)
        hist.reverse()
        return hist[:limit]

    def get_latest_evaluation(self) -> Optional[ReflectionEvaluation]:
        """获取最近一次完整评估"""
        return self._last_evaluation

    def get_snapshot(self) -> EvaluatorSnapshot:
        """生成评估器快照"""
        return EvaluatorSnapshot(
            enabled=True,
            total_evaluations=self._total_evaluations,
            total_accept=self._total_accept,
            total_reject=self._total_reject,
            total_defer=self._total_defer,
            total_revisit=self._total_revisit,
            last_evaluation_id=self._last_evaluation.evaluation_id if self._last_evaluation else "",
            last_recommendation=self._last_evaluation.recommendation if self._last_evaluation else "",
            last_overall_score=self._last_evaluation.overall_score if self._last_evaluation else 0.0,
        )

    def clear_history(self) -> int:
        """清空历史，返回清理数"""
        n = len(self._history)
        self._history.clear()
        return n

    # ============================================================
    # 工具方法
    # ============================================================

    @staticmethod
    def _to_dict(obj: Any) -> Dict[str, Any]:
        """将对象（dataclass 或 dict）转为 dict"""
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return obj
        # dataclass
        if hasattr(obj, "__dataclass_fields__"):
            from dataclasses import asdict
            return asdict(obj)
        # 尝试 __dict__
        if hasattr(obj, "__dict__"):
            return dict(obj.__dict__)
        return {}
