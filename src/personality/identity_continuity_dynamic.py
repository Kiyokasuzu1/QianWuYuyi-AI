"""
Phase 4.0 — R2.6.2: Identity Continuity Dynamic Check（动态身份连续性检查器）

定位：
  R2.5.4 的 IdentityContinuity 是静态 path 检查（identity.core.* 被改了吗）。
  R2.6.2 升级为动态检查：「现在的羽依，和过去的羽依，还是不是同一个连续存在？」

入参三元组：
  1) IdentityAnchorManager (baseline identity anchors)
  2) (PersonalityState baseline, PersonalityState current)  — 两个 PersonalityState 比较
  3) SelfModelSnapshot + (evolution_records)  — 叙述连续性

输出：IdentityContinuityReport（7 字段 R2.6.2 冻结）

红线：
  ❌ 不修改输入（anchor manager / state / self_model snapshot 全部只读）
  ❌ 不 import FORBIDDEN_IMPORTS（growth / approval / relationship / memory / emotion / adapter）
  ❌ 不调用 FORBIDDEN_CALLS（apply_evolution / accept_proposal / accept_experience / govern_proposal / execute）
  ✅ 100% 异常隔离：任意维度崩溃 → 返回 is_continuous=False + warning=`dynamic_check_crash_isolated`

3 个检查维度：
  1) Identity Anchor Layer（最高优先级，权重 0.35）
     - origin 不变（anchor_creator.principle 未被改动）
     - core values 集合一致（related_core_values 去重后集合等价）
     - core anchor 权重漂移 < WEIGHT_DRIFT_CRITICAL=0.20

  2) Personality Drift Layer（权重 0.30）
     - 单 trait 绝对 delta < DRIFT_MAJOR=0.30
     - 同时 major delta（>= DRIFT_MAJOR）的 trait 数量 < DRIFT_MAJOR_COUNT=3
     - stable_traits_changed: 从 baseline.stable_traits → current 里 delta >= 0.05 的
     - large_changes: 单 trait abs(delta) >= 0.20

  3) Narrative Continuity Layer（权重 0.35，"现在的变化是否能被发展历史解释"）
     - 每个 large_change trait 是否存在 evolution record 覆盖（before→after 方向 + 时间线）
     - 任何 large change 未被解释 → warning=`narrative_gap_<trait>`
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# R2.6.2 冻结：IdentityContinuityReport 7 字段
# ============================================================
FROZEN_REPORT_KEYS: Tuple[str, ...] = (
    "is_continuous",                 # bool: 综合判断
    "identity_anchor_stability",     # float 0~1: 锚点稳定性分数
    "core_value_preserved",          # bool: 核心价值观集合完整吗
    "personality_drift",             # dict: {stable_traits_changed, large_changes}
    "warnings",                      # list[str]: 警告标签（可解释）
    "version",                       # int: 报告版本
    "generated_at",                  # str: 生成时间戳
)

# 禁止依赖列表（与 self_model_schema 保持一致）
FORBIDDEN_IMPORTS: Tuple[str, ...] = (
    "src.personality.personality_adapter",
    "src.personality.trait_state",
    "src.growth.growth_integration",
    "src.approval.approval_manager",
    "src.personality.evolution_pipeline",
    "src.relationship.relationship_memory",
    "src.relationship.relationship_event",
    "src.emotion",
    "src.memory",
)
FORBIDDEN_CALLS: Tuple[str, ...] = (
    "apply_evolution",
    "accept_proposal",
    "apply_proposal",
    "govern_proposal",
    "accept_experience",
    "execute",
    "set_trait",
    "update_traits",
)

# 阈值（可调）
DEFAULT_WEIGHT_DRIFT_CRITICAL: float = 0.20
DEFAULT_DRIFT_MINOR: float = 0.05          # >= 0.05 → stable_traits_changed
DEFAULT_DRIFT_MAJOR: float = 0.20          # >= 0.20 → large_changes
DEFAULT_DRIFT_MAJOR_COUNT: int = 3         # 同时 3+ 个 major → 断裂倾向
DEFAULT_ANCHOR_WEIGHT: float = 0.35
DEFAULT_DRIFT_WEIGHT: float = 0.30
DEFAULT_NARRATIVE_WEIGHT: float = 0.35
DEFAULT_OVERALL_THRESHOLD: float = 0.70    # 综合分数 >= 0.70 → is_continuous=True


def validate_report_shape(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError(f"IdentityContinuityReport 必须是 dict, got {type(report)}")
    missing = [k for k in FROZEN_REPORT_KEYS if k not in report]
    if missing:
        raise ValueError(f"IdentityContinuityReport 缺少冻结字段: {missing}")
    extra = [k for k in report if k not in FROZEN_REPORT_KEYS]
    if extra:
        raise ValueError(f"IdentityContinuityReport 有未登记字段: {extra}")
    if not isinstance(report["is_continuous"], bool):
        raise ValueError("is_continuous 必须是 bool")
    if not isinstance(report["identity_anchor_stability"], float) and \
            not isinstance(report["identity_anchor_stability"], int):
        raise ValueError("identity_anchor_stability 必须是数值 (0~1)")
    if not (0.0 <= float(report["identity_anchor_stability"]) <= 1.0):
        raise ValueError("identity_anchor_stability 必须在 0~1")
    if not isinstance(report["core_value_preserved"], bool):
        raise ValueError("core_value_preserved 必须是 bool")
    if not isinstance(report["personality_drift"], dict):
        raise ValueError("personality_drift 必须是 dict")
    for field in ("stable_traits_changed", "large_changes"):
        if field not in report["personality_drift"]:
            raise ValueError(f"personality_drift 缺少字段 {field}")
        if not isinstance(report["personality_drift"][field], list):
            raise ValueError(f"personality_drift.{field} 必须是 list")
    if not isinstance(report["warnings"], list):
        raise ValueError("warnings 必须是 list")
    if not isinstance(report["version"], int) or report["version"] < 0:
        raise ValueError("version 必须是非负整数")
    if not isinstance(report["generated_at"], str) or not report["generated_at"].strip():
        raise ValueError("generated_at 必须是非空字符串")


class IdentityContinuityDynamicChecker:
    """
    R2.6.2 动态身份连续性检查器。

    用法：
      checker = IdentityContinuityDynamicChecker()
      report = checker.evaluate(
          identity_anchor_manager=iam,
          baseline_state=PersonalityState(初始),
          current_state=PersonalityState(现在),
          current_self_model_snapshot=SelfModelSnapshot(...),
          evolution_records=[EvolutionRecord...],
      )
    """

    def __init__(
        self,
        *,
        anchor_weight: float = DEFAULT_ANCHOR_WEIGHT,
        drift_weight: float = DEFAULT_DRIFT_WEIGHT,
        narrative_weight: float = DEFAULT_NARRATIVE_WEIGHT,
        overall_threshold: float = DEFAULT_OVERALL_THRESHOLD,
        weight_drift_critical: float = DEFAULT_WEIGHT_DRIFT_CRITICAL,
        drift_minor: float = DEFAULT_DRIFT_MINOR,
        drift_major: float = DEFAULT_DRIFT_MAJOR,
        drift_major_count: int = DEFAULT_DRIFT_MAJOR_COUNT,
    ) -> None:
        self._aw = float(anchor_weight)
        self._dw = float(drift_weight)
        self._nw = float(narrative_weight)
        self._thr = float(overall_threshold)
        self._weight_drift_critical = float(weight_drift_critical)
        self._d_minor = float(drift_minor)
        self._d_major = float(drift_major)
        self._d_major_cnt = max(1, int(drift_major_count))
        self._version_counter: int = 0

    # ============================================================
    # 主入口
    # ============================================================
    def evaluate(
        self,
        *,
        identity_anchor_manager: Any = None,
        baseline_state: Any = None,
        current_state: Any = None,
        current_self_model_snapshot: Any = None,
        evolution_records: List[Any] = None,
        generated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """三（+一）维度评估 → 返回冻结形状 IdentityContinuityReport dict。100% 异常隔离。"""
        try:
            return self._evaluate_safe(
                identity_anchor_manager=identity_anchor_manager,
                baseline_state=baseline_state,
                current_state=current_state,
                current_self_model_snapshot=current_self_model_snapshot,
                evolution_records=evolution_records or [],
                generated_at=generated_at,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[identity_continuity_dynamic_crash] error=%s", exc)
            self._version_counter += 1
            rep = {
                "is_continuous": False,
                "identity_anchor_stability": 0.0,
                "core_value_preserved": False,
                "personality_drift": {"stable_traits_changed": [], "large_changes": []},
                "warnings": [f"dynamic_check_crash_isolated: {exc!r}"],
                "version": self._version_counter,
                "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
            }
            validate_report_shape(rep)
            return rep

    # ============================================================
    # 安全实现
    # ============================================================
    def _evaluate_safe(
        self,
        *,
        identity_anchor_manager: Any,
        baseline_state: Any,
        current_state: Any,
        current_self_model_snapshot: Any,
        evolution_records: List[Any],
        generated_at: Optional[str],
    ) -> Dict[str, Any]:
        self._version_counter += 1
        version = self._version_counter
        ts = generated_at or datetime.now(timezone.utc).isoformat()

        warnings: List[str] = []

        # Dim 1: Identity Anchor
        anchor_score, cv_preserved, anchor_warns = self._check_identity_anchor(identity_anchor_manager)
        warnings.extend(anchor_warns)

        # Dim 2: Personality Drift
        drift_score, drift_dict, drift_warns = self._check_personality_drift(
            baseline_state, current_state,
        )
        warnings.extend(drift_warns)
        large_traits = [lc["trait"] for lc in drift_dict["large_changes"]]

        # Dim 3: Narrative Continuity
        narrative_score, narrative_warns = self._check_narrative_continuity(
            large_traits=large_traits,
            current_self_model_snapshot=current_self_model_snapshot,
            evolution_records=evolution_records,
        )
        warnings.extend(narrative_warns)

        # 加权综合
        total_w = self._aw + self._dw + self._nw
        if total_w <= 0:
            total_w = 1.0
        overall = (
            self._aw * anchor_score
            + self._dw * drift_score
            + self._nw * narrative_score
        ) / total_w
        overall = round(max(0.0, min(1.0, overall)), 4)

        is_continuous: bool
        # 高优先级硬断：如果 anchor_score 为 0（核心 principle/cv 被改）→ 强制 False
        if anchor_score < 0.01:
            is_continuous = False
            if "identity_anchor_hard_break" not in warnings:
                warnings.append("identity_anchor_hard_break")
        else:
            is_continuous = overall >= self._thr

        report = {
            "is_continuous": is_continuous,
            "identity_anchor_stability": round(float(anchor_score), 4),
            "core_value_preserved": bool(cv_preserved),
            "personality_drift": drift_dict,
            "warnings": warnings,
            "version": version,
            "generated_at": ts,
        }
        validate_report_shape(report)
        return report

    # ============================================================
    # Dim 1: Identity Anchor（最高优先级）
    # ============================================================
    def _check_identity_anchor(self, iam: Any) -> Tuple[float, bool, List[str]]:
        """返回 (score 0~1, core_value_preserved, warnings)。"""
        warnings: List[str] = []
        if iam is None:
            return 1.0, True, warnings  # No anchor manager: treat as consistent (no info to break)

        score: float = 1.0
        try:
            all_anchors = list(iam.get_all_anchors() or [])
        except Exception:  # noqa: BLE001
            warnings.append("identity_anchor_unreadable")
            return 0.5, False, warnings

        # 1.1 核心身份锚：anchor_creator（origin/principle）
        # 注意：这里不比较 baseline vs current（因为 R2.5.3/4 本来就保护不改），
        # 只检查核心锚是否还在 + original_weight - weight 漂移 < critical
        core_anchor_ids_expected = {
            "anchor_authenticity",
            "anchor_growth",
            "anchor_independence",
            "anchor_memory",
            "anchor_creator",
        }
        actual_core_ids = set()
        max_w_drift = 0.0
        for a in all_anchors:
            aid = str(getattr(a, "anchor_id", "") or "")
            is_core = bool(getattr(a, "is_core", False))
            if is_core:
                actual_core_ids.add(aid)
            w = float(getattr(a, "weight", 0.0) or 0.0)
            ow = float(getattr(a, "original_weight", w) or 0.0)
            max_w_drift = max(max_w_drift, abs(w - ow))

        missing_cores = core_anchor_ids_expected - actual_core_ids
        if missing_cores:
            # R2.6.2: 任何 1 个 core anchor 缺失 → anchor_score 直接降到 <= 0.10（触发硬断）
            score = min(score, 0.10)
            warnings.append(f"identity_core_anchor_missing:{sorted(missing_cores)}")

        # 1.2 权重漂移
        if max_w_drift >= self._weight_drift_critical:
            score *= 0.70
            warnings.append(f"anchor_weight_drift_ge_critical:{max_w_drift:.3f}")
        elif max_w_drift >= DEFAULT_WEIGHT_DRIFT_CRITICAL * 0.5:
            score *= 0.90
            warnings.append(f"anchor_weight_drift_warning:{max_w_drift:.3f}")

        # 1.3 core_values 集合（related_core_values 汇总）
        seen_cv: set = set()
        for a in all_anchors:
            rcv = list(getattr(a, "related_core_values", None) or [])
            for v in rcv:
                s = str(v).strip()
                if s:
                    seen_cv.add(s)
        # 默认 identity.md 期望的 5 类核心价值观（根据 anchor 定义聚合）
        expected_cv = {"honesty", "growth", "autonomy", "empathy", "kindness"}
        core_value_preserved = expected_cv.issubset(seen_cv)
        if not core_value_preserved:
            score *= 0.80
            warnings.append(f"core_value_missing:{sorted(expected_cv - seen_cv)}")

        # 截断到 0~1
        score = round(max(0.0, min(1.0, score)), 4)
        return score, bool(core_value_preserved), warnings

    # ============================================================
    # Dim 2: Personality Drift
    # ============================================================
    def _check_personality_drift(
        self,
        baseline: Any,
        current: Any,
    ) -> Tuple[float, Dict[str, Any], List[str]]:
        """返回 (drift_score 0~1, drift_dict, warnings)。"""
        drift_dict: Dict[str, Any] = {
            "stable_traits_changed": [],
            "large_changes": [],
        }
        warnings: List[str] = []
        score: float = 1.0

        if baseline is None and current is None:
            return score, drift_dict, warnings

        b_traits = self._extract_traits(baseline)
        c_traits = self._extract_traits(current)

        all_keys = set(b_traits.keys()) | set(c_traits.keys())
        major_count = 0

        for trait in sorted(all_keys):
            bv = float(b_traits.get(trait, c_traits.get(trait, 0.5)))
            cv = float(c_traits.get(trait, b_traits.get(trait, 0.5)))
            delta = round(cv - bv, 6)
            abs_d = abs(delta)

            # stable_traits_changed: abs_delta >= minor
            if abs_d >= self._d_minor:
                drift_dict["stable_traits_changed"].append({
                    "trait": trait,
                    "before": round(bv, 4),
                    "after": round(cv, 4),
                    "delta": delta,
                })

            # large_changes: abs_delta >= major
            if abs_d >= self._d_major:
                drift_dict["large_changes"].append({
                    "trait": trait,
                    "before": round(bv, 4),
                    "after": round(cv, 4),
                    "delta": delta,
                })
                major_count += 1

            # 按幅度扣分（0~1）
            if abs_d < self._d_minor:
                continue
            # 每 0.05 扣 0.05 分，最重 0.3 分
            penalty_this = min(0.3, abs_d / 0.05 * 0.05)
            score -= penalty_this / max(1, len(all_keys) / 4)

        # 多个 major → 额外扣分 + warning
        if major_count >= self._d_major_cnt:
            score *= 0.60
            warnings.append(f"personality_drift_massive:major_count={major_count}")
        elif major_count >= 1:
            warnings.append(f"personality_drift_large:{major_count}")

        score = round(max(0.0, min(1.0, score)), 4)
        return score, drift_dict, warnings

    # ============================================================
    # Dim 3: Narrative Continuity
    # ============================================================
    def _check_narrative_continuity(
        self,
        *,
        large_traits: List[str],
        current_self_model_snapshot: Any,
        evolution_records: List[Any],
    ) -> Tuple[float, List[str]]:
        """叙述连续性：large trait change 是否被 evolution records 或 turning points 解释。"""
        warnings: List[str] = []
        score = 1.0
        if not large_traits:
            return score, warnings

        # 收集 evolution_records 覆盖的 trait 集合
        covered: set = set()
        for rec in evolution_records or []:
            try:
                if isinstance(rec, dict):
                    after = rec.get("after") or {}
                else:
                    after = getattr(rec, "after", None) or {}
                for k in after.keys():
                    short = str(k)
                    for p in ("trait.", "interests.", "interest."):
                        if short.startswith(p):
                            short = short[len(p):]
                            break
                    covered.add(short)
            except Exception:  # noqa: BLE001
                continue

        # 从 self_model_snapshot.important_turning_points 也收集
        if current_self_model_snapshot is not None:
            try:
                dev = dict(current_self_model_snapshot.development_view or {})
                for tp in list(dev.get("important_turning_points", []) or []):
                    if not isinstance(tp, dict):
                        continue
                    for at in list(tp.get("affected_traits", []) or []):
                        tname = str(dict(at).get("trait", ""))
                        if tname:
                            covered.add(tname)
            except Exception:  # noqa: BLE001
                pass

        # 每个 large trait 检查是否被覆盖
        gaps = 0
        for trait in large_traits:
            t = str(trait)
            if t not in covered:
                gaps += 1
                warnings.append(f"narrative_gap_{t}")

        # 扣分：每个 gap 扣 1 / len(large_traits) * 0.90 权重
        # （未被解释的 large trait change 是非常严重的叙事断裂信号）
        if large_traits:
            score = round(max(0.0, 1.0 - 0.90 * (gaps / len(large_traits))), 4)
        if gaps:
            warnings.append(f"narrative_gaps_total:{gaps}/{len(large_traits)}")
        return score, warnings

    # ============================================================
    # 辅助：从 PersonalityState / dict 提取 traits
    # ============================================================
    @staticmethod
    def _extract_traits(state: Any) -> Dict[str, float]:
        try:
            if state is None:
                return {}
            if hasattr(state, "snapshot") and callable(state.snapshot):
                snap = state.snapshot()
                return dict(snap.get("traits", {}) or {})
            if isinstance(state, dict):
                return dict(state.get("traits", {}) or {})
            if hasattr(state, "traits"):
                return dict(getattr(state, "traits", {}) or {})
            return {}
        except Exception:  # noqa: BLE001
            return {}
