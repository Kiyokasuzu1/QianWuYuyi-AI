"""
Phase 4.0 — R2.6.3: SelfReflectionBuilder（自我反思构造器：纯结构化数据聚合，不推理、不写、不接 LLM）

职责（只做 4 件事）：
  1) observed_changes          — 最近发生了什么变化？（EvolutionRecord → 4 个维度事实）
  2) interpreted_causes        — 为什么发生？（GrowthProposal.reasons + ApprovalDecision.reasons + evidence 聚合；cause_tag 规则化映射）
  3) identity_alignment        — 变化还是不是羽依？（IdentityContinuityReport → Reflection 映射；不重新计算）
  4) unresolved_tensions       — 暂时无法解决的矛盾？（SelfModel.contradiction_view.detected_tensions + ICR warnings 标记 gap）
  5) current_self_summary      — 3 段摘要（origin/core/recent；只读以上对象提取，**不做自然语言生成**）

红线：
  ❌ 不修改 PersonalityState（apply_evolution / set_trait / update_traits）
  ❌ 不产生 GrowthProposal（create_proposal / accept_proposal）
  ❌ 不调用 Approval / Growth pipeline（govern_proposal / accept_experience / execute）
  ❌ 不覆盖 IdentityAnchor（register_anchor / set_anchor_weight / update_anchor_weight）
  ❌ 不产生 prompt / 不接 LLM（永远返回结构化数据）

数据流（严格只读）：
  SelfModelSnapshot          ──readonly──→ identity_view / contradiction_view
  IdentityContinuityReport   ──readonly──→ overall assessment + warnings
  EvolutionRecord[]          ──readonly──→ observed_changes (facts) + evidence count/summary
  {proposal_id: GrowthProposal}           ──readonly──→ evaluator_meta.reasons / evidence_ids
  {approval_id: ApprovalDecision}         ──readonly──→ reasons / decision / confidence

cause_tag 规则映射（不 AI 推理）：
  - approval_reasons 含 identity_consistent + enough_evidence
        → identity_consistent
  - transition_protected_flag 或 approval_reasons 含 interest_transition_gradual_requires_observation
        → transition_protected
  - approval_decision=deferred 或 under_review 类 tag
        → under_review
  - >=2 条 evidence + 高置信度 proposal.confidence>=0.7
        → evidence_driven
  - 其他
        → unknown
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.self_reflection.self_reflection_schema import (
    validate_self_reflection_shape,
    create_empty_self_reflection,
)
from src.self_reflection.self_reflection_snapshot import (
    SelfReflectionSnapshot,
    build_snapshot,
)

logger = logging.getLogger(__name__)


# cause_tag 白名单集合
_CAUSE_EVIDENCE = "evidence_driven"
_CAUSE_IDENTITY = "identity_consistent"
_CAUSE_TRANSITION = "transition_protected"
_CAUSE_UNDER_REVIEW = "under_review"
_CAUSE_UNKNOWN = "unknown"

# IdentityContinuityReport → Reflection.identity_alignment.overall_assessment 映射
_IC_TO_REFLECTION_ASSESSMENT = {
    "continuous": "identity_compatible",
    "warning": "identity_tension_detected",
    "break_risk": "identity_break_risk",
    "is_continuous_true": "identity_compatible",
    "is_continuous_false": "identity_tension_detected",
}


class SelfReflectionBuilder:
    """R2.6.3: 纯结构化数据聚合。只读输入 → SelfReflectionSnapshot。"""

    def __init__(self, *, observed_changes_limit: int = 20) -> None:
        self._obs_limit = max(1, int(observed_changes_limit))
        self._version_counter: int = 0

    # ============================================================
    # 主入口
    # ============================================================
    def build(
        self,
        *,
        self_model_snapshot: Any = None,
        identity_continuity_report: Optional[Mapping[str, Any]] = None,
        evolution_records: Optional[Iterable[Any]] = None,
        proposals_by_id: Optional[Mapping[str, Any]] = None,
        approvals_by_id: Optional[Mapping[str, Any]] = None,
        generated_at: Optional[str] = None,
    ) -> SelfReflectionSnapshot:
        """
        从只读输入构建 SelfReflectionSnapshot。100% 异常隔离（永远返回合法 snapshot）。

        Args:
            self_model_snapshot: SelfModelSnapshot 或 duck-typed（带 identity_view / contradiction_view 属性或字典）
            identity_continuity_report: 由 IdentityContinuityDynamicChecker.evaluate() 返回的 dict；可选
            evolution_records: 迭代器，每个 EvolutionRecord（dict 或 TypedDict 或 duck-typed 对象）
            proposals_by_id: {proposal_id -> GrowthProposal (dataclass/dict 带 evaluator_meta/evidence_ids/confidence)}
            approvals_by_id: {approval_id -> ApprovalDecision dict}
            generated_at: 覆盖时间戳（测试用）
        """
        # 先占版本号：成功或失败都只 +1（degraded 内部不会再 +1，使用外部传入）
        self._version_counter += 1
        assigned_version = self._version_counter
        try:
            return self._build_safe(
                assigned_version=assigned_version,
                self_model_snapshot=self_model_snapshot,
                identity_continuity_report=dict(identity_continuity_report or {}),
                evolution_records=list(evolution_records or []),
                proposals_by_id=dict(proposals_by_id or {}),
                approvals_by_id=dict(approvals_by_id or {}),
                generated_at=generated_at,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[self_reflection_builder_crash] error=%s", exc)
            return self._build_degraded(exc, assigned_version=assigned_version, generated_at=generated_at)

    # ============================================================
    # 安全实现
    # ============================================================
    def _build_safe(
        self,
        *,
        assigned_version: int,
        self_model_snapshot: Any,
        identity_continuity_report: Dict[str, Any],
        evolution_records: List[Any],
        proposals_by_id: Dict[str, Any],
        approvals_by_id: Dict[str, Any],
        generated_at: Optional[str],
    ) -> SelfReflectionSnapshot:
        self._version_counter = assigned_version
        version = assigned_version
        reflection_id = f"ref_{uuid.uuid4().hex[:12]}"
        ts = generated_at or datetime.now(timezone.utc).isoformat()

        # 1. observed_changes
        observed, rec_ids_map = self._build_observed_changes(evolution_records)

        # 2. interpreted_causes（每条 observed 一条 cause；映射不到则 cause_tag=unknown）
        causes = self._build_interpreted_causes(
            observed_changes=observed,
            evolution_records=evolution_records,
            proposals_by_id=proposals_by_id,
            approvals_by_id=approvals_by_id,
        )

        # 3. identity_alignment（读 IdentityContinuityReport，不重新计算）
        identity_alignment = self._build_identity_alignment(identity_continuity_report)

        # 4. unresolved_tensions（SelfModel.contradiction_view + ICR warnings narrative gaps）
        unresolved = self._build_unresolved_tensions(
            self_model_snapshot=self_model_snapshot,
            identity_continuity_report=identity_continuity_report,
        )

        # 5. current_self_summary（3 段，纯结构化、不自然语言）
        summary = self._build_current_self_summary(
            self_model_snapshot=self_model_snapshot,
            observed_changes=observed,
        )

        # 来源 SelfModel.version 锚定
        sm_version = self._extract_sm_version(self_model_snapshot)

        data = {
            "reflection_id": reflection_id,
            "self_model_version": sm_version,
            "observed_changes": observed,
            "interpreted_causes": causes,
            "identity_alignment": identity_alignment,
            "unresolved_tensions": unresolved,
            "current_self_summary": summary,
            "generated_at": ts,
            "version": version,
        }
        validate_self_reflection_shape(data)
        return build_snapshot(data)

    # ============================================================
    # 1. observed_changes
    # ============================================================
    def _build_observed_changes(
        self,
        evolution_records: List[Any],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        out: List[Dict[str, Any]] = []
        # record_id → 索引映射（interpreted_causes 用 change_id 回链）
        rid_to_idx: Dict[str, int] = {}

        def _strip_prefix(k: str) -> str:
            for p in ("trait.", "interests.", "interest."):
                if k.startswith(p):
                    return k[len(p):]
            return k

        for rec in evolution_records:
            try:
                if isinstance(rec, dict):
                    rid = str(rec.get("record_id") or rec.get("id") or "")
                    ts = str(rec.get("timestamp") or rec.get("created_at") or "")
                    ct = str(rec.get("change_type") or "trait_delta")
                    before = rec.get("before") or {}
                    after = rec.get("after") or {}
                else:
                    rid = str(getattr(rec, "record_id", "") or getattr(rec, "id", "") or "")
                    ts = str(getattr(rec, "timestamp", "") or getattr(rec, "created_at", "") or "")
                    ct = str(getattr(rec, "change_type", "") or "trait_delta")
                    before = getattr(rec, "before", None) or {}
                    after = getattr(rec, "after", None) or {}
                before = dict(before)
                after = dict(after)
                if not rid and not before and not after:
                    continue

                # 展开：每条 trait 一条 observed change（保持 before/after 对应）
                for key, av in after.items():
                    try:
                        av_f = float(av)
                    except Exception:  # noqa: BLE001
                        continue
                    bv = before.get(key, av)
                    try:
                        bv_f = float(bv)
                    except Exception:  # noqa: BLE001
                        bv_f = av_f
                    delta = round(av_f - bv_f, 6)
                    entry = {
                        "change_id": rid or f"auto_{len(out)}",
                        "trait": _strip_prefix(str(key)),
                        "before": round(bv_f, 6),
                        "after": round(av_f, 6),
                        "delta": delta,
                        "change_type": ct,
                        "timestamp": ts,
                    }
                    out.append(entry)
                    if rid:
                        rid_to_idx[rid] = len(out) - 1  # 最末一条对应；多条无所谓，cause 是按 record_id 回链
            except Exception as exc:  # noqa: BLE001
                logger.debug("[self_reflection_obs_skip] error=%s", exc)
                continue

        # 按 timestamp 倒序；截取 limit
        out.sort(key=lambda x: (x["timestamp"] == "", x["timestamp"]), reverse=True)
        return out[: self._obs_limit], rid_to_idx

    # ============================================================
    # 2. interpreted_causes
    # ============================================================
    def _build_interpreted_causes(
        self,
        *,
        observed_changes: List[Dict[str, Any]],
        evolution_records: List[Any],
        proposals_by_id: Dict[str, Any],
        approvals_by_id: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        causes: List[Dict[str, Any]] = []

        for obs in observed_changes:
            cause = {
                "for_change_id": obs["change_id"],
                "proposal_id": "",
                "approval_id": "",
                "proposal_reasons": [],
                "approval_reasons": [],
                "evidence_summary": "无可追溯成长提案与审批记录",
                "cause_tag": _CAUSE_UNKNOWN,
            }
            evo_rec = self._rec_by_id(evolution_records, obs["change_id"])
            pid = ""
            aid = ""
            if evo_rec is not None:
                pid = str(self._prop_from_rec(evo_rec) or "")
                aid = str(self._appr_from_rec(evo_rec) or "")
            if pid:
                cause["proposal_id"] = pid
                p = proposals_by_id.get(pid)
                if p is not None:
                    cause["proposal_reasons"] = self._extract_proposal_reasons(p)
                    evidence_ids = self._extract_proposal_evidence_ids(p)
                    confidence = self._extract_proposal_confidence(p)
                    if evidence_ids:
                        cause["evidence_summary"] = (
                            f"经 {len(evidence_ids)} 次经历验证"
                            + (f"，提案置信度 {confidence:.2f}" if confidence is not None else "")
                        )
            if aid:
                cause["approval_id"] = aid
                ad = approvals_by_id.get(aid)
                if ad is not None:
                    cause["approval_reasons"] = list(ad.get("reasons") or [])

            cause["cause_tag"] = self._classify_cause(cause)
            causes.append(cause)

        return causes

    # helper：（不需要保存跨调用状态；evolution_records 作为参数传入 _rec_by_id 即可）
    __init_subclass__ = object.__init_subclass__  # 保留默认实现

    @staticmethod
    def _rec_by_id(last_records: List[Any], change_id: str) -> Any:
        if not change_id or change_id.startswith("auto_") or not last_records:
            return None
        for r in last_records:
            try:
                if isinstance(r, dict):
                    rid = str(r.get("record_id") or r.get("id") or "")
                else:
                    rid = str(getattr(r, "record_id", "") or getattr(r, "id", "") or "")
                if rid == change_id:
                    return r
            except Exception:  # noqa: BLE001
                continue
        return None

    @staticmethod
    def _prop_from_rec(rec: Any) -> Optional[str]:
        try:
            if isinstance(rec, dict):
                v = rec.get("proposal_id")
            else:
                v = getattr(rec, "proposal_id", None)
            return str(v) if v else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _appr_from_rec(rec: Any) -> Optional[str]:
        try:
            if isinstance(rec, dict):
                v = rec.get("approval_id")
            else:
                v = getattr(rec, "approval_id", None)
            return str(v) if v else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _extract_proposal_reasons(p: Any) -> List[str]:
        try:
            if isinstance(p, dict):
                em = p.get("evaluator_meta") or {}
            else:
                em = getattr(p, "evaluator_meta", None) or {}
            rs = (em or {}).get("reasons") or []
            return [str(x).strip() for x in rs if str(x).strip()]
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _extract_proposal_evidence_ids(p: Any) -> List[str]:
        try:
            if isinstance(p, dict):
                ids = p.get("evidence_ids") or []
            else:
                ids = getattr(p, "evidence_ids", None) or []
            return [str(x).strip() for x in ids if str(x).strip()]
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _extract_proposal_confidence(p: Any) -> Optional[float]:
        try:
            if isinstance(p, dict):
                c = p.get("confidence")
            else:
                c = getattr(p, "confidence", None)
            if c is None:
                return None
            return float(c)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _classify_cause(cause: Dict[str, Any]) -> str:
        appr = set(cause["approval_reasons"])
        prop = set(cause["proposal_reasons"])

        if ("interest_transition_gradual_requires_observation" in appr) or \
                ("transition_gradual_marked_needs_review" in prop):
            return _CAUSE_TRANSITION

        if ("conflict_detected_under_review" in appr) or \
                ("conflict_with_existing_trait_deferred" in appr) or \
                ("insufficient_evidence_deferred" in appr):
            return _CAUSE_UNDER_REVIEW

        if "identity_consistent" in appr and "enough_evidence" in appr:
            return _CAUSE_IDENTITY

        # evidence_driven：>= 2 条 evidence 且 evidence_summary 匹配 "经 N 次经历验证"（N≥2）
        es = cause["evidence_summary"]
        ev_count_ok = False
        try:
            import re as _re
            m = _re.search(r"经\s*(\d+)\s*次经历验证", str(es))
            if m:
                n = int(m.group(1))
                if n >= 2:
                    ev_count_ok = True
        except Exception:  # noqa: BLE001
            ev_count_ok = False
        if ev_count_ok:
            return _CAUSE_EVIDENCE

        # 最后 fallback：identity_consistent 单条也放 identity（通常说明是兼容变化）
        if "identity_consistent" in appr or "governance_policy_approved" in appr:
            return _CAUSE_IDENTITY
        return _CAUSE_UNKNOWN

    # ============================================================
    # 3. identity_alignment（读 ICR，不重新计算）
    # ============================================================
    @staticmethod
    def _build_identity_alignment(icr: Dict[str, Any]) -> Dict[str, Any]:
        # core_value_check（来自 ICR.core_value_preserved）
        cv_preserved = bool(icr.get("core_value_preserved", True))
        missing = []
        for w in (icr.get("warnings") or []):
            w_s = str(w)
            if w_s.startswith("core_value_missing:"):
                try:
                    body = w_s[len("core_value_missing:"):]
                    missing = [x.strip() for x in body.strip() if False]  # placeholder
                    # 解析形如 "[a, b]" 的字符串
                    s = body.strip()
                    if s.startswith("[") and s.endswith("]"):
                        items = [i.strip().strip("'").strip('"') for i in s[1:-1].split(",") if i.strip()]
                        missing = items
                except Exception:  # noqa: BLE001
                    missing = []
        cvc_details = "核心价值观完整" if cv_preserved else (
            f"缺失: {missing}" if missing else "核心价值观不完整（原因不可解析）"
        )

        continuity_score: float
        try:
            cs = icr.get("identity_anchor_stability")
            continuity_score = round(max(0.0, min(1.0, float(cs))), 4) if cs is not None else 1.0
        except Exception:  # noqa: BLE001
            continuity_score = 1.0

        # assessment：优先读 ICR 里的自定义 overall_assessment（如果没有，按映射规则推导）
        assessment: str
        overall = icr.get("overall_assessment") or icr.get("assessment")
        if overall in {"identity_compatible", "identity_tension_detected", "identity_break_risk"}:
            assessment = overall
        else:
            warnings_list = list(icr.get("warnings") or [])
            has_hard = any(
                str(x).startswith("identity_core_anchor_missing") or
                "identity_break" in str(x) or
                "identity_anchor_hard_break" in str(x)
                for x in warnings_list
            )
            if has_hard:
                assessment = "identity_break_risk"
            elif "is_continuous" in icr and icr.get("is_continuous") is False and not warnings_list:
                assessment = "identity_tension_detected"
            elif warnings_list:
                # 只有 narrative_gap / drift_large / weight_drift_warning → tension 级别
                assessment = "identity_tension_detected"
            else:
                assessment = "identity_compatible"

        assessment_details: str
        if assessment == "identity_compatible":
            assessment_details = "本次变化符合核心价值观并通过连续性检查"
        elif assessment == "identity_tension_detected":
            items = [str(x) for x in (icr.get("warnings") or [])]
            assessment_details = "存在张力或大变化：" + ", ".join(items[:3]) if items else "存在身份张力"
        else:  # identity_break_risk
            items = [str(x) for x in (icr.get("warnings") or [])]
            assessment_details = "检测到身份断裂风险：" + ", ".join(items[:3]) if items else "身份可能断裂"

        return {
            "core_value_check": {
                "preserved": cv_preserved,
                "missing_values": list(missing),
                "details": cvc_details,
            },
            "continuity_score": continuity_score,
            "overall_assessment": assessment,
            "assessment_details": assessment_details,
        }

    # ============================================================
    # 4. unresolved_tensions（永远 status=unresolved）
    # ============================================================
    @staticmethod
    def _build_unresolved_tensions(
        *,
        self_model_snapshot: Any,
        identity_continuity_report: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        counter = 0

        def _new_tnid() -> str:
            nonlocal counter
            counter += 1
            return f"tn_{counter:03d}"

        # 4.1 SelfModel.contradiction_view.detected_tensions
        try:
            tensions_from_sm: List[Any]
            if isinstance(self_model_snapshot, dict):
                cv = (self_model_snapshot.get("contradiction_view") or {}).get("detected_tensions") or []
            else:
                attr = getattr(self_model_snapshot, "contradiction_view", None)
                cv = (attr or {}).get("detected_tensions") or [] if isinstance(attr, Mapping) else list(attr or [])
            tensions_from_sm = list(cv or [])
            for t in tensions_from_sm:
                try:
                    if isinstance(t, Mapping):
                        trait_a = str(t.get("trait_a") or "")
                        trait_b = str(t.get("trait_b") or "")
                        desc = str(t.get("description") or t.get("title") or "与自我张力相关")
                    else:
                        trait_a = str(getattr(t, "trait_a", "") or "")
                        trait_b = str(getattr(t, "trait_b", "") or "")
                        desc = str(getattr(t, "description", "") or getattr(t, "title", "") or "与自我张力相关")
                    title = desc or f"{trait_a} 与 {trait_b} 之间的张力"
                    result.append({
                        "tension_id": _new_tnid(),
                        "kind": "trait_tension",
                        "title": title,
                        "details": {"trait_a": trait_a, "trait_b": trait_b},
                        "status": "unresolved",
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[self_reflection_tn_skip_sm] error=%s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[self_reflection_tn_skip_sm_all] error=%s", exc)

        # 4.2 IdentityContinuityReport.warnings 中 narrative_gap
        for w in (identity_continuity_report.get("warnings") or []):
            w_s = str(w)
            if w_s.startswith("narrative_gap_") and not w_s.startswith("narrative_gaps_total:"):
                trait = w_s[len("narrative_gap_"):]
                result.append({
                    "tension_id": _new_tnid(),
                    "kind": "narrative_gap",
                    "title": f"缺少对 {trait} 大变化的历史解释（叙事断裂）",
                    "details": {"trait": trait},
                    "status": "unresolved",
                })
            elif w_s.startswith("identity_core_anchor_missing") or \
                    w_s.startswith("core_value_missing") or \
                    "identity_break" in w_s or "hard_break" in w_s:
                result.append({
                    "tension_id": _new_tnid(),
                    "kind": "identity_mismatch",
                    "title": f"身份核心检查告警：{w_s}",
                    "details": {"raw_warning": w_s},
                    "status": "unresolved",
                })

        # personality_drift.large_changes >= 3 massive 视作张力
        pd = identity_continuity_report.get("personality_drift") or {}
        large = list(pd.get("large_changes") or [])
        if len(large) >= 3:
            result.append({
                "tension_id": _new_tnid(),
                "kind": "trait_tension",
                "title": f"存在 {len(large)} 项大于 0.20 的特质同时变化：需要观察",
                "details": {"large_changes": large},
                "status": "unresolved",
            })

        return result

    # ============================================================
    # 5. current_self_summary 3 段（纯结构化：不生成自然语言片段）
    # ============================================================
    @staticmethod
    def _build_current_self_summary(
        *,
        self_model_snapshot: Any,
        observed_changes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        # 5.1 origin_bullet：来自 SelfModel.identity_view.origin
        origin_bullet = ""
        core_values_bullets: List[str] = []
        try:
            if isinstance(self_model_snapshot, dict):
                iv = self_model_snapshot.get("identity_view") or {}
            else:
                iv = getattr(self_model_snapshot, "identity_view", None) or {}
            if isinstance(iv, Mapping):
                origin_bullet = str(iv.get("origin") or "")
                core_values_bullets = [str(x) for x in list(iv.get("core_values") or [])]
        except Exception:  # noqa: BLE001
            origin_bullet = ""
            core_values_bullets = []

        # 5.3 recent_changes_bullets：来自 observed_changes 前 5 条（按 delta 幅度降序或时间倒序，时间已经排序）
        recent_bullets: List[str] = []
        for obs in observed_changes[:5]:
            trait = obs["trait"]
            d = float(obs["delta"])
            direction = "增强" if d > 0 else "减弱"
            ct = str(obs.get("change_type") or "trait_delta")
            # 纯结构化 bullet：trait + direction + magnitude + change_type
            bullet = f"{trait}: {direction} {abs(d):.3f}（{ct}）"
            recent_bullets.append(bullet)

        # 如果 core_values_bullets 是空（SelfModel 为空），至少保持空列表，合法 shape
        return {
            "origin_bullet": origin_bullet,
            "core_values_bullets": core_values_bullets,
            "recent_changes_bullets": recent_bullets,
        }

    # ============================================================
    # 辅助：提取 sm_version
    # ============================================================
    @staticmethod
    def _extract_sm_version(self_model_snapshot: Any) -> int:
        try:
            if isinstance(self_model_snapshot, dict):
                v = self_model_snapshot.get("version", 0)
            else:
                v = getattr(self_model_snapshot, "version", 0)
            n = int(v)
            return max(0, n)
        except Exception:  # noqa: BLE001
            return 0

    # ============================================================
    # 降级：build 全局异常时返回合法空快照
    # ============================================================
    def _build_degraded(self, exc: BaseException, *, assigned_version: int, generated_at: Optional[str]) -> SelfReflectionSnapshot:
        self._version_counter = assigned_version
        version = assigned_version
        ts = generated_at or datetime.now(timezone.utc).isoformat()
        data = create_empty_self_reflection(
            version=version,
            self_model_version=0,
        )
        data["generated_at"] = ts
        data["identity_alignment"]["assessment_details"] = f"self_reflection_builder_degraded: {exc!r}"
        # 新增一条 unresolved tension：构建异常本身就是一个 unresolved issue
        data["unresolved_tensions"] = [{
            "tension_id": "tn_degraded",
            "kind": "identity_mismatch",
            "title": "自我反思构建发生异常（已隔离）",
            "details": {"raw_error": repr(exc)},
            "status": "unresolved",
        }]
        # shape 二次保证
        try:
            validate_self_reflection_shape(data)
        except Exception as exc2:  # noqa: BLE001
            logger.warning("[self_reflection_degraded_shape_fix_needed] error=%s", exc2)
        return build_snapshot(data)
