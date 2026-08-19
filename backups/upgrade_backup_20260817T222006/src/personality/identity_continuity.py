"""
Phase 3.5.9: Identity Continuity Layer

职责：
- compare_snapshots(): 比较两个 IdentitySnapshot，产出 IdentityChange
- calculate_continuity(): 基于 IdentityChange 计算连续性分数（0~1）
- detect_identity_break(): 检测身份断裂信号，产出 IdentityConflict 列表
- generate_change_report(): 生成完整 ContinuityReport

约束：
- 不修改 Persona 文档
- 不修改 TraitState
- 不生成自然语言叙事
- 只产出结构化 IdentityChangeReport
- 保留全部历史审计（reports 可被外部持久化）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.contracts.identity_schema import (
    IdentitySnapshot,
    IdentityChange,
    TraitChange,
    CoreValueChange,
    IdentityConflict,
    ContinuityReport,
)


def _parse_iso(ts: str) -> float:
    """安全解析 ISO 时间字符串为 timestamp float"""
    if not ts:
        return 0.0
    try:
        # 去掉末尾 'Z'
        clean = ts.rstrip("Z")
        return datetime.fromisoformat(clean).timestamp()
    except Exception:
        return 0.0


# ============================================================
# 连续性评估阈值配置
# ============================================================

@dataclass
class ContinuityThresholds:
    """连续性评估的可调阈值"""

    # --- 特质变化幅度分级 ---
    trait_minor_threshold: float = 0.02      # |delta| < 0.02 → negligible
    trait_moderate_threshold: float = 0.05    # 0.02 ~ 0.05 → minor; 0.05 ~ 0.10 → moderate
    trait_major_threshold: float = 0.10       # |delta| > 0.10 → major

    # --- 核心价值观变化幅度分级 ---
    cv_minor_threshold: float = 0.03
    cv_moderate_threshold: float = 0.08
    cv_major_threshold: float = 0.15

    # --- 连续性评分权重 ---
    weight_trait_stability: float = 0.35
    weight_core_value_stability: float = 0.25
    weight_understanding_progress: float = 0.20
    weight_structural_integrity: float = 0.20

    # --- 断裂检测 ---
    continuity_threshold: float = 0.7         # score >= 0.7 → is_continuous=True

    # --- 冲突触发 ---
    trait_reversal_delta: float = 0.08        # 特质反向变化超过此值 → trait_reversal
    value_drop_threshold: float = 0.10        # 核心价值观权重下降超过此值 → value_drop
    identity_break_trait_count: int = 3       # 同时大幅变化的特质数 ≥ 此值 → identity_break
    identity_break_trait_delta: float = 0.08  # 触发 identity_break 的单特质 delta 阈值
    understanding_regression_threshold: float = 0.05  # 理解水平下降超过此值 → understanding_regression


# ============================================================
# IdentityContinuityChecker: 核心实现
# ============================================================

class IdentityContinuityChecker:
    """
    身份连续性检测器。

    纯函数式：接收两个 IdentitySnapshot，输出 ContinuityReport。
    不修改任何外部状态。
    """

    def __init__(self, thresholds: Optional[ContinuityThresholds] = None):
        self.th = thresholds or ContinuityThresholds()

    # ============================================================
    # 主入口：生成完整 ContinuityReport
    # ============================================================

    def generate_change_report(
        self,
        before: IdentitySnapshot,
        after: IdentitySnapshot,
    ) -> ContinuityReport:
        """
        生成从 before → after 的完整连续性报告。
        """
        # 1. 比较快照
        changes = self.compare_snapshots(before, after)

        # 2. 计算分项评分
        trait_score = self._score_trait_stability(changes)
        cv_score = self._score_core_value_stability(changes)
        und_score = self._score_understanding_progress(before, after)
        struct_score = self._score_structural_integrity(changes)

        # 3. 加权综合
        continuity_score = (
            self.th.weight_trait_stability * trait_score
            + self.th.weight_core_value_stability * cv_score
            + self.th.weight_understanding_progress * und_score
            + self.th.weight_structural_integrity * struct_score
        )
        continuity_score = max(0.0, min(1.0, continuity_score))

        # 4. 检测冲突
        conflicts = self.detect_identity_break(before, after, changes)

        # 5. 时间间隔
        time_gap = abs(_parse_iso(after.timestamp) - _parse_iso(before.timestamp))

        # 6. 组装报告
        report = ContinuityReport(
            before_snapshot_id=before.snapshot_id,
            after_snapshot_id=after.snapshot_id,
            before_timestamp=before.timestamp,
            after_timestamp=after.timestamp,
            before_version=before.version,
            after_version=after.version,
            continuity_score=round(continuity_score, 4),
            is_continuous=continuity_score >= self.th.continuity_threshold,
            threshold=self.th.continuity_threshold,
            changes=changes,
            conflicts=conflicts,
            trait_stability_score=round(trait_score, 4),
            core_value_stability_score=round(cv_score, 4),
            understanding_progress_score=round(und_score, 4),
            structural_integrity_score=round(struct_score, 4),
            time_gap_seconds=time_gap,
        )

        # 7. 添加注释
        if changes.max_trait_delta() > self.th.trait_moderate_threshold:
            report.notes.append(
                f"最大特质变化: {changes.max_trait_delta():.4f}（超过 moderate 阈值）"
            )
        if changes.max_cv_delta() > self.th.cv_moderate_threshold:
            report.notes.append(
                f"最大核心价值观权重变化: {changes.max_cv_delta():.4f}（超过 moderate 阈值）"
            )
        if conflicts:
            report.notes.append(
                f"检测到 {len(conflicts)} 个身份冲突: "
                + ", ".join(f"{c.conflict_type}({c.severity})" for c in conflicts)
            )
        if not report.is_continuous:
            report.notes.append(
                f"连续性分数 {continuity_score:.3f} 低于阈值 {self.th.continuity_threshold}，身份可能断裂"
            )

        return report

    # ============================================================
    # compare_snapshots: 比较两个快照
    # ============================================================

    def compare_snapshots(
        self,
        before: IdentitySnapshot,
        after: IdentitySnapshot,
    ) -> IdentityChange:
        """
        比较两个 IdentitySnapshot，返回 IdentityChange。
        """
        change = IdentityChange()

        # --- 特质变化 ---
        before_traits: Dict[str, TraitSnapshot] = {t.trait: t for t in before.traits}
        after_traits: Dict[str, TraitSnapshot] = {t.trait: t for t in after.traits}

        all_trait_names = set(before_traits.keys()) | set(after_traits.keys())
        for name in all_trait_names:
            bt = before_traits.get(name)
            at = after_traits.get(name)
            if bt and at:
                delta = round(at.value - bt.value, 6)
                old_safe = max(abs(bt.value), 0.01)
                rel_delta = round(delta / old_safe, 6) if bt.value != 0 else 0.0
                direction = "increase" if delta > 0.001 else "decrease" if delta < -0.001 else "stable"
                magnitude = self._classify_trait_magnitude(abs(delta))
                change.changes.append(TraitChange(
                    trait=name,
                    old_value=bt.value,
                    new_value=at.value,
                    delta=delta,
                    relative_delta=rel_delta,
                    direction=direction,
                    magnitude=magnitude,
                ))
            elif at and not bt:
                change.added_traits.append(name)
            elif bt and not at:
                change.removed_traits.append(name)

        # --- 核心价值观变化 ---
        before_cvs: Dict[str, CoreValueSnapshot] = {v.value_id: v for v in before.core_values}
        after_cvs: Dict[str, CoreValueSnapshot] = {v.value_id: v for v in after.core_values}

        all_cv_ids = set(before_cvs.keys()) | set(after_cvs.keys())
        for vid in all_cv_ids:
            bcv = before_cvs.get(vid)
            acv = after_cvs.get(vid)
            if bcv and acv:
                delta = round(acv.weight - bcv.weight, 6)
                magnitude = self._classify_cv_magnitude(abs(delta))
                change.core_value_changes.append(CoreValueChange(
                    value_id=vid,
                    name=acv.name or bcv.name,
                    old_weight=bcv.weight,
                    new_weight=acv.weight,
                    delta=delta,
                    magnitude=magnitude,
                ))
            elif acv and not bcv:
                change.added_core_values.append(vid)
            elif bcv and not acv:
                change.removed_core_values.append(vid)

        # --- 统计变化 ---
        change.growth_history_delta = after.growth_history_count - before.growth_history_count
        change.preferences_delta = after.preferences_count - before.preferences_count
        change.patterns_delta = after.behavioral_patterns_count - before.behavioral_patterns_count
        change.contradictions_delta = after.contradictions_count - before.contradictions_count

        # --- 理解水平变化 ---
        change.understanding_delta = {
            "experience_awareness": round(after.experience_awareness - before.experience_awareness, 6),
            "trait_awareness": round(after.trait_awareness - before.trait_awareness, 6),
            "identity_continuity": round(after.identity_continuity - before.identity_continuity, 6),
            "overall": round(after.overall_understanding - before.overall_understanding, 6),
        }

        return change

    # ============================================================
    # calculate_continuity: 计算连续性分数（0~1）
    # ============================================================

    def calculate_continuity(
        self,
        before: IdentitySnapshot,
        after: IdentitySnapshot,
    ) -> float:
        """
        计算两个快照之间的连续性分数。

        返回 0~1 的浮点数，1 = 完全连续，0 = 完全断裂。
        """
        changes = self.compare_snapshots(before, after)
        trait_score = self._score_trait_stability(changes)
        cv_score = self._score_core_value_stability(changes)
        und_score = self._score_understanding_progress(before, after)
        struct_score = self._score_structural_integrity(changes)

        score = (
            self.th.weight_trait_stability * trait_score
            + self.th.weight_core_value_stability * cv_score
            + self.th.weight_understanding_progress * und_score
            + self.th.weight_structural_integrity * struct_score
        )
        return max(0.0, min(1.0, round(score, 4)))

    # ============================================================
    # detect_identity_break: 检测身份断裂
    # ============================================================

    def detect_identity_break(
        self,
        before: IdentitySnapshot,
        after: IdentitySnapshot,
        changes: Optional[IdentityChange] = None,
    ) -> List[IdentityConflict]:
        """
        检测身份断裂信号，返回冲突列表。

        冲突类型：
        - trait_reversal: 特质方向反转（幅度 > trait_reversal_delta）
        - value_drop: 核心价值观权重大幅下降
        - identity_break: 多个特质同时大幅变化
        - understanding_regression: 理解水平倒退
        """
        if changes is None:
            changes = self.compare_snapshots(before, after)

        conflicts: List[IdentityConflict] = []

        # 1. 特质方向反转
        for tc in changes.changes:
            if abs(tc.delta) >= self.th.trait_reversal_delta:
                # 检查是否是方向反转（before direction 与 delta 方向相反）
                before_trait = next(
                    (t for t in before.traits if t.trait == tc.trait),
                    None,
                )
                if before_trait:
                    before_dir = before_trait.direction
                    if (before_dir == "increase" and tc.delta < 0) or \
                       (before_dir == "decrease" and tc.delta > 0):
                        conflicts.append(IdentityConflict(
                            conflict_type="trait_reversal",
                            severity=self._severity_from_delta(abs(tc.delta)),
                            description=f"特质 {tc.trait} 方向反转: {before_dir} → {tc.direction}（Δ={tc.delta:+.4f}）",
                            details={
                                "trait": tc.trait,
                                "before_direction": before_dir,
                                "after_direction": tc.direction,
                                "delta": tc.delta,
                                "old_value": tc.old_value,
                                "new_value": tc.new_value,
                            },
                        ))

        # 2. 核心价值观权重大幅下降
        for cvc in changes.core_value_changes:
            if cvc.delta <= -self.th.value_drop_threshold:
                conflicts.append(IdentityConflict(
                    conflict_type="value_drop",
                    severity=self._severity_from_cv_delta(abs(cvc.delta)),
                    description=f"核心价值观 {cvc.name}({cvc.value_id}) 权重大幅下降: {cvc.old_weight:.3f} → {cvc.new_weight:.3f}（Δ={cvc.delta:+.4f}）",
                    details={
                        "value_id": cvc.value_id,
                        "name": cvc.name,
                        "old_weight": cvc.old_weight,
                        "new_weight": cvc.new_weight,
                        "delta": cvc.delta,
                    },
                ))

        # 3. 多特质同时大幅变化 → identity_break
        major_trait_changes = [
            tc for tc in changes.changes
            if abs(tc.delta) >= self.th.identity_break_trait_delta
        ]
        if len(major_trait_changes) >= self.th.identity_break_trait_count:
            conflicts.append(IdentityConflict(
                conflict_type="identity_break",
                severity="high",
                description=f"{len(major_trait_changes)} 个特质同时大幅变化（≥{self.th.identity_break_trait_delta}）",
                details={
                    "trait_count": len(major_trait_changes),
                    "traits": [
                        {"trait": tc.trait, "delta": tc.delta}
                        for tc in major_trait_changes
                    ],
                },
            ))

        # 4. 理解水平倒退
        und_delta = changes.understanding_delta
        for dim, val in und_delta.items():
            if val <= -self.th.understanding_regression_threshold:
                conflicts.append(IdentityConflict(
                    conflict_type="understanding_regression",
                    severity="medium" if abs(val) > self.th.understanding_regression_threshold * 2 else "low",
                    description=f"理解水平 {dim} 倒退: {val:+.4f}",
                    details={
                        "dimension": dim,
                        "delta": val,
                    },
                ))

        # 5. 特质集合剧烈变化（新增/消失过多）
        structural_changes = len(changes.added_traits) + len(changes.removed_traits)
        if structural_changes >= 3:
            conflicts.append(IdentityConflict(
                conflict_type="identity_break",
                severity="medium",
                description=f"特质集合剧烈变化: +{len(changes.added_traits)} / -{len(changes.removed_traits)}",
                details={
                    "added": changes.added_traits,
                    "removed": changes.removed_traits,
                },
            ))

        return conflicts

    # ============================================================
    # 内部：分类与评分
    # ============================================================

    def _classify_trait_magnitude(self, abs_delta: float) -> str:
        """分类特质变化幅度"""
        if abs_delta < self.th.trait_minor_threshold:
            return "negligible"
        elif abs_delta < self.th.trait_moderate_threshold:
            return "minor"
        elif abs_delta < self.th.trait_major_threshold:
            return "moderate"
        else:
            return "major"

    def _classify_cv_magnitude(self, abs_delta: float) -> str:
        """分类核心价值观变化幅度"""
        if abs_delta < self.th.cv_minor_threshold:
            return "negligible"
        elif abs_delta < self.th.cv_moderate_threshold:
            return "minor"
        elif abs_delta < self.th.cv_major_threshold:
            return "moderate"
        else:
            return "major"

    def _severity_from_delta(self, abs_delta: float) -> str:
        """从特质变化幅度推断严重性"""
        if abs_delta >= self.th.trait_major_threshold:
            return "critical"
        elif abs_delta >= self.th.trait_moderate_threshold:
            return "high"
        elif abs_delta >= self.th.trait_minor_threshold:
            return "medium"
        else:
            return "low"

    def _severity_from_cv_delta(self, abs_delta: float) -> str:
        """从核心价值观变化幅度推断严重性"""
        if abs_delta >= self.th.cv_major_threshold:
            return "critical"
        elif abs_delta >= self.th.cv_moderate_threshold:
            return "high"
        elif abs_delta >= self.th.cv_minor_threshold:
            return "medium"
        else:
            return "low"

    def _score_trait_stability(self, changes: IdentityChange) -> float:
        """
        特质稳定性评分（0~1）。

        - 无变化 = 1.0
        - 每个特质变化按幅度扣分
        - major 变化扣分最重
        """
        if not changes.changes:
            return 1.0
        penalty = 0.0
        for tc in changes.changes:
            if tc.magnitude == "negligible":
                penalty += 0.0
            elif tc.magnitude == "minor":
                penalty += 0.05
            elif tc.magnitude == "moderate":
                penalty += 0.15
            else:  # major
                penalty += 0.30
        # 特质新增/消失也扣分
        penalty += 0.10 * (len(changes.added_traits) + len(changes.removed_traits))
        return max(0.0, 1.0 - penalty)

    def _score_core_value_stability(self, changes: IdentityChange) -> float:
        """
        核心价值观稳定性评分（0~1）。
        """
        if not changes.core_value_changes:
            return 1.0
        penalty = 0.0
        for cvc in changes.core_value_changes:
            if cvc.magnitude == "negligible":
                penalty += 0.0
            elif cvc.magnitude == "minor":
                penalty += 0.03
            elif cvc.magnitude == "moderate":
                penalty += 0.10
            else:
                penalty += 0.25
        penalty += 0.15 * (len(changes.added_core_values) + len(changes.removed_core_values))
        return max(0.0, 1.0 - penalty)

    def _score_understanding_progress(
        self,
        before: IdentitySnapshot,
        after: IdentitySnapshot,
    ) -> float:
        """
        理解水平进步评分（0~1）。

        - 理解水平不变或进步 = 1.0
        - 倒退越多扣分越多
        """
        deltas = [
            after.experience_awareness - before.experience_awareness,
            after.trait_awareness - before.trait_awareness,
            after.identity_continuity - before.identity_continuity,
            after.overall_understanding - before.overall_understanding,
        ]
        penalty = 0.0
        for d in deltas:
            if d < 0:
                # 每倒退 0.05 扣 0.1 分
                penalty += min(0.5, abs(d) / 0.05 * 0.1)
        return max(0.0, 1.0 - penalty)

    def _score_structural_integrity(self, changes: IdentityChange) -> float:
        """
        结构完整性评分（0~1）。

        - 无新增/消失 = 1.0
        - 每个新增/消失扣分
        """
        penalty = 0.0
        penalty += 0.05 * len(changes.added_traits)
        penalty += 0.10 * len(changes.removed_traits)
        penalty += 0.05 * len(changes.added_core_values)
        penalty += 0.15 * len(changes.removed_core_values)
        return max(0.0, 1.0 - penalty)


# ============================================================
# IdentityContinuityHistory: 历史审计管理（可选）
# ============================================================

class IdentityContinuityHistory:
    """
    连续性报告历史管理器。

    保存所有生成的 ContinuityReport，支持查询和审计。
    不自动持久化，由外部调用方决定是否保存到文件。
    """

    MAX_REPORTS = 200

    def __init__(self):
        self._reports: List[ContinuityReport] = []
        self._snapshots: List[IdentitySnapshot] = []

    def add_snapshot(self, snapshot: IdentitySnapshot) -> None:
        """记录一个快照（用于审计）"""
        self._snapshots.append(snapshot)
        if len(self._snapshots) > self.MAX_REPORTS:
            self._snapshots = self._snapshots[-self.MAX_REPORTS:]

    def add_report(self, report: ContinuityReport) -> None:
        """记录一个报告"""
        self._reports.append(report)
        if len(self._reports) > self.MAX_REPORTS:
            self._reports = self._reports[-self.MAX_REPORTS:]

    def get_reports(self, limit: int = 50) -> List[ContinuityReport]:
        """获取最近的报告（最新在前）"""
        reports = list(self._reports)
        reports.reverse()
        return reports[:limit]

    def get_snapshots(self, limit: int = 50) -> List[IdentitySnapshot]:
        """获取最近的快照（最新在前）"""
        snaps = list(self._snapshots)
        snaps.reverse()
        return snaps[:limit]

    def get_latest_report(self) -> Optional[ContinuityReport]:
        """获取最新报告"""
        if not self._reports:
            return None
        return self._reports[-1]

    def get_latest_snapshot(self) -> Optional[IdentitySnapshot]:
        """获取最新快照"""
        if not self._snapshots:
            return None
        return self._snapshots[-1]

    def get_average_continuity(self, window: int = 10) -> float:
        """获取最近 N 次报告的平均连续性分数"""
        if not self._reports:
            return 1.0
        recent = self._reports[-window:]
        return sum(r.continuity_score for r in recent) / len(recent)

    def has_break_detected(self) -> bool:
        """是否检测到过断裂"""
        return any(not r.is_continuous for r in self._reports)

    def clear(self) -> None:
        """清空历史"""
        self._reports.clear()
        self._snapshots.clear()
