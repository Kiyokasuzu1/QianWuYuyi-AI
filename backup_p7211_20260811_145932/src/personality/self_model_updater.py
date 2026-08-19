"""
Phase 3.5.8: SelfModelUpdater

输入：
  - GrowthRecord
  - ReflectionInsight
  - PersonalityChangeRequest (PCR)

输出：
  - SelfModelChangeSuggestion (变化建议，需要外部审批后应用)

约束：
  - 不直接修改 TraitState / SelfModelManager 的 identity
  - 不直接写入 Persona 文档
  - 建议是「增量」，不做替换式覆盖
  - requires_approval 默认 True，保留审批机制
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.contracts.self_model_schema import (
    SelfModelChangeSuggestion,
    CoreValue,
    StableTrait,
    Preference,
    BehavioralPattern,
    SelfContradiction,
    GrowthHistoryEntry,
)
from src.growth.growth_record import GrowthRecord
from src.personality.trait_state import TraitState


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class SelfModelUpdaterConfig:
    """SelfModelUpdater 可调阈值（可通过 RuntimeCore config 配置）"""

    # GrowthRecord → Preference
    min_preference_confidence: float = 0.3
    min_preference_evidence_boost: float = 0.05

    # Insight → Pattern
    min_pattern_confidence: float = 0.3
    min_pattern_frequency: int = 1

    # PCR → StableTrait / History
    min_trait_delta_for_suggestion: float = 0.002
    pcr_history_always_add: bool = True

    # CoreValue 权重波动上限（单次）
    max_single_corevalue_weight_delta: float = 0.05
    corevalue_min_weight: float = 0.3
    corevalue_max_weight: float = 1.0

    # 建议审批默认
    require_approval_default: bool = True


class SelfModelUpdater:
    """
    Phase 3.5.8 SelfModel 更新建议生成器。

    纯函数式：接收输入 → 产出 SelfModelChangeSuggestion。
    调用方（RuntimeCore/外部流程）显式 accept/reject，
    然后通过 SelfModelManager 真正应用。
    """

    def __init__(self, config: Optional[SelfModelUpdaterConfig] = None):
        self.cfg = config or SelfModelUpdaterConfig()

    # ============================================================
    # 主入口：根据输入源生成建议
    # ============================================================

    def from_growth_records(self, records: List[GrowthRecord]) -> List[SelfModelChangeSuggestion]:
        """
        为每条 GrowthRecord 生成一条建议（或合并多条）。
        """
        suggestions: List[SelfModelChangeSuggestion] = []
        for gr in records:
            sug = self._from_single_growth_record(gr)
            if sug and (
                sug.add_preferences
                or sug.add_history_entries
                or sug.update_core_value_weights
            ):
                suggestions.append(sug)
        return suggestions

    def from_insights(self, insights: List[Any]) -> List[SelfModelChangeSuggestion]:
        """为每个 ReflectionInsight 生成建议。"""
        suggestions = []
        for ins in insights:
            sug = self._from_single_insight(ins)
            if sug and (sug.add_patterns or sug.add_history_entries):
                suggestions.append(sug)
        return suggestions

    def from_pcr(self, pcr: Dict[str, Any]) -> Optional[SelfModelChangeSuggestion]:
        """从 PersonalityChangeRequest（dict）生成建议。"""
        if not pcr:
            return None
        return self._from_single_pcr(pcr)

    # ============================================================
    # 辅助：单条输入 → Suggestion
    # ============================================================

    def _from_single_growth_record(self, gr: GrowthRecord) -> SelfModelChangeSuggestion:
        rid = gr.get("record_id", "")
        confidence = float(gr.get("confidence", 0.0))
        affected = gr.get("affected_dimensions", {}) or {}
        signal = gr.get("growth_signal", "")
        source_type = gr.get("source_type", "")
        growth_level = gr.get("growth_level", "")
        reason = gr.get("reason", "")

        sug = SelfModelChangeSuggestion(
            source_type="growth_record",
            source_id=rid,
            confidence=confidence,
            evidence_count=1,
            requires_approval=self.cfg.require_approval_default,
        )
        reasons_list: List[str] = []

        # --- 偏好建议 ---
        if (
            confidence >= self.cfg.min_preference_confidence
            and source_type == "preference"
            and growth_level in {"preference", "context"}
            and signal
        ):
            pref = Preference(
                domain=growth_level or "context",
                key=signal,
                value=signal,
                evidence_count=1,
                confidence=confidence,
                sources=[rid],
            )
            sug.add_preferences.append(pref)
            reasons_list.append(f"GrowthRecord {rid} signal={signal} 产生偏好建议")

        # --- CoreValue 权重建议（identity/milestone 类型） ---
        if source_type in {"identity", "milestone"} and affected:
            for dim, delta in affected.items():
                d = float(delta)
                if abs(d) < self.cfg.min_trait_delta_for_suggestion:
                    continue
                # 单次上限
                d = max(-self.cfg.max_single_corevalue_weight_delta,
                        min(self.cfg.max_single_corevalue_weight_delta, d))
                sug.update_core_value_weights[dim] = round(d, 5)
            if sug.update_core_value_weights:
                reasons_list.append(
                    f"GrowthRecord {rid} {source_type} 触发 CoreValue 权重建议: {sug.update_core_value_weights}"
                )

        # --- 成长历史条目（始终追加，保证可审计） ---
        history_entry = GrowthHistoryEntry(
            source_type="growth_record",
            source_id=rid,
            affected_traits=dict(affected),
            summary=reason or signal or "",
            evidence_count=1,
            confidence=confidence,
        )
        sug.add_history_entries.append(history_entry)

        # --- trait 建议（针对 trait 级别 GR） ---
        if growth_level == "trait" and affected:
            for trait, delta in affected.items():
                d = float(delta)
                if abs(d) < self.cfg.min_trait_delta_for_suggestion:
                    continue
                if trait not in sug.update_stable_traits:
                    sug.update_stable_traits[trait] = {}
                sug.update_stable_traits[trait] = {
                    "delta": round(d, 5),
                    "confidence": confidence,
                    "sources": [rid],
                }
            if sug.update_stable_traits:
                reasons_list.append(
                    f"GrowthRecord {rid} trait-level 触发稳定特质更新建议"
                )

        # --- understanding 增量建议 ---
        sug.understanding_delta["experience_awareness"] = round(
            min(0.02, 0.005 + 0.001 * confidence), 5
        )
        if growth_level == "trait":
            sug.understanding_delta["trait_awareness"] = round(0.002 + 0.002 * confidence, 5)

        sug.reasons = reasons_list
        return sug

    def _from_single_insight(self, ins: Any) -> SelfModelChangeSuggestion:
        iid = getattr(ins, "insight_id", None) or (ins.get("insight_id") if isinstance(ins, dict) else "")
        pattern_detected = (
            getattr(ins, "pattern_detected", "")
            or (ins.get("pattern_detected") if isinstance(ins, dict) else "")
        )
        pattern_frequency = (
            getattr(ins, "pattern_frequency", 0)
            or (ins.get("pattern_frequency") if isinstance(ins, dict) else 0)
            or 0
        )
        confidence = (
            getattr(ins, "confidence", 0.0)
            or (ins.get("confidence") if isinstance(ins, dict) else 0.0)
            or 0.0
        )
        summary = (
            getattr(ins, "summary", "")
            or (ins.get("summary") if isinstance(ins, dict) else "")
        )
        timestamp = (
            getattr(ins, "timestamp", "")
            or (ins.get("timestamp") if isinstance(ins, dict) else "")
        )

        sug = SelfModelChangeSuggestion(
            source_type="reflection_insight",
            source_id=iid,
            confidence=float(confidence),
            evidence_count=max(1, int(pattern_frequency)),
            requires_approval=self.cfg.require_approval_default,
        )
        reasons_list: List[str] = []

        if (
            pattern_detected
            and confidence >= self.cfg.min_pattern_confidence
            and pattern_frequency >= self.cfg.min_pattern_frequency
        ):
            bp = BehavioralPattern(
                pattern_detected=pattern_detected,
                description=summary,
                frequency=int(pattern_frequency),
                confidence=float(confidence),
                last_observed=timestamp,
                sources=[iid],
            )
            sug.add_patterns.append(bp)
            reasons_list.append(f"Insight {iid} pattern={pattern_detected} 产生行为模式建议")

        # 历史条目（即使没有 pattern 也记录反思行为）
        sug.add_history_entries.append(
            GrowthHistoryEntry(
                source_type="reflection_insight",
                source_id=iid,
                affected_traits={},
                summary=summary or pattern_detected or "",
                evidence_count=max(1, int(pattern_frequency)),
                confidence=float(confidence),
            )
        )

        sug.understanding_delta["experience_awareness"] = round(0.003 + 0.003 * confidence, 5)
        sug.understanding_delta["identity_continuity"] = round(0.001 + 0.001 * confidence, 5)

        sug.reasons = reasons_list
        return sug

    def _from_single_pcr(self, pcr: Dict[str, Any]) -> SelfModelChangeSuggestion:
        pcr_id = pcr.get("request_id", "")
        proposal_id = pcr.get("source_proposal_id", "")
        insight_id = pcr.get("source_insight_id", "") or ""
        confidence = float(pcr.get("confidence", 0.0))
        evidence_count = int(pcr.get("evidence_count", 0) or 0)
        reason = pcr.get("reason", "") or ""

        sug = SelfModelChangeSuggestion(
            source_type="personality_change_request",
            source_id=pcr_id,
            confidence=confidence,
            evidence_count=evidence_count,
            requires_approval=self.cfg.require_approval_default,
        )
        reasons_list: List[str] = []

        # 从 PCR.evolution_record 提取 trait 更新
        evo = pcr.get("evolution_record") or {}
        changes = evo.get("changes") or {}
        affected_traits: Dict[str, float] = {}
        if isinstance(changes, dict):
            for trait, change in changes.items():
                if isinstance(change, dict):
                    delta = float(change.get("delta", 0.0) or 0.0)
                else:
                    delta = float(change or 0.0)
                if abs(delta) < self.cfg.min_trait_delta_for_suggestion:
                    continue
                if trait not in sug.update_stable_traits:
                    sug.update_stable_traits[trait] = {}
                sug.update_stable_traits[trait] = {
                    "delta": round(delta, 5),
                    "confidence": confidence,
                    "sources": [pcr_id, proposal_id] if proposal_id else [pcr_id],
                }
                affected_traits[trait] = round(delta, 5)

        # 从 PCR.growth_records 提取偏好建议
        for gr in (pcr.get("growth_records") or []):
            signal = gr.get("growth_signal", "")
            src_type = gr.get("source_type", "")
            level = gr.get("growth_level", "")
            gr_conf = float(gr.get("confidence", confidence))
            if (
                gr_conf >= self.cfg.min_preference_confidence
                and src_type == "preference"
                and level in {"preference", "context"}
                and signal
            ):
                pref = Preference(
                    domain=level or "context",
                    key=signal,
                    value=signal,
                    evidence_count=max(1, evidence_count),
                    confidence=gr_conf,
                    sources=[pcr_id, gr.get("record_id", "")],
                )
                sug.add_preferences.append(pref)

            # 合并 affected_dimensions 到 history
            ad = gr.get("affected_dimensions", {}) or {}
            for k, v in ad.items():
                affected_traits[k] = affected_traits.get(k, 0.0) + float(v)

        if sug.update_stable_traits:
            reasons_list.append(f"PCR {pcr_id} 触发稳定特质更新建议: {list(sug.update_stable_traits.keys())}")

        # 历史条目
        his_sources = [pcr_id]
        if proposal_id:
            his_sources.append(proposal_id)
        if insight_id:
            his_sources.append(insight_id)

        history_entry = GrowthHistoryEntry(
            source_type="personality_change_request",
            source_id=pcr_id,
            affected_traits=affected_traits,
            summary=reason or f"proposal={proposal_id}",
            evidence_count=evidence_count,
            confidence=confidence,
        )
        sug.add_history_entries.append(history_entry)

        # Understanding 增量（PCR 是较强信号）
        sug.understanding_delta["trait_awareness"] = round(
            0.004 + 0.003 * confidence + 0.001 * min(10, evidence_count), 5
        )
        sug.understanding_delta["experience_awareness"] = round(0.002 + 0.002 * confidence, 5)
        sug.understanding_delta["identity_continuity"] = round(0.001 + 0.001 * confidence, 5)

        sug.reasons = reasons_list
        return sug

    # ============================================================
    # 工具：批量 → 单条合并建议
    # ============================================================

    def merge_suggestions(self, suggestions: List[SelfModelChangeSuggestion]) -> SelfModelChangeSuggestion:
        """
        将多条建议合并为一条。
        """
        merged = SelfModelChangeSuggestion(
            source_type="merged",
            source_id=",".join(s.source_id for s in suggestions if s.source_id)[:128],
            requires_approval=self.cfg.require_approval_default,
        )
        for s in suggestions:
            merged.add_core_values.extend(s.add_core_values)
            merged.add_stable_traits.extend(s.add_stable_traits)
            for k, d in s.update_core_value_weights.items():
                merged.update_core_value_weights[k] = merged.update_core_value_weights.get(k, 0.0) + d
            for k, v in s.update_stable_traits.items():
                if k not in merged.update_stable_traits:
                    merged.update_stable_traits[k] = {
                        "delta": 0.0,
                        "confidence": 0.0,
                        "sources": [],
                    }
                tgt = merged.update_stable_traits[k]
                tgt["delta"] = round((tgt.get("delta") or 0.0) + float(v.get("delta") or 0.0), 5)
                tgt["confidence"] = max(float(tgt.get("confidence") or 0.0), float(v.get("confidence") or 0.0))
                tgt["sources"] = sorted(set((tgt.get("sources") or []) + list(v.get("sources") or [])))
            merged.add_preferences.extend(s.add_preferences)
            merged.add_patterns.extend(s.add_patterns)
            merged.add_contradictions.extend(s.add_contradictions)
            merged.add_history_entries.extend(s.add_history_entries)
            for k, v in s.understanding_delta.items():
                merged.understanding_delta[k] = merged.understanding_delta.get(k, 0.0) + float(v)
            merged.confidence = max(merged.confidence, s.confidence)
            merged.evidence_count += s.evidence_count
            merged.reasons.extend(s.reasons)

        # 归一化核心权重 delta（上限）
        for k, v in list(merged.update_core_value_weights.items()):
            merged.update_core_value_weights[k] = max(
                -self.cfg.max_single_corevalue_weight_delta,
                min(self.cfg.max_single_corevalue_weight_delta, v),
            )
        return merged

    # ============================================================
    # 工具：Apply Suggestion → SelfModelManager（外部调用方显式调用）
    # ============================================================

    def apply_suggestion(
        self,
        manager: Any,
        suggestion: SelfModelChangeSuggestion,
        *,
        apply_preferences: bool = True,
        apply_patterns: bool = True,
        apply_core_weights: bool = True,
        apply_stable_updates: bool = True,
        apply_history: bool = True,
        apply_understanding: bool = True,
    ) -> None:
        """
        将 Suggestion 显式应用到 SelfModelManager。

        注意：仅建议的 apply_* 开关可部分应用；默认所有都应用。
        这不是强制修改，仍需调用方明确调用本方法。
        """
        # CoreValue 新增（少见）
        if apply_core_weights:
            existing_cv = {cv.value_id: cv for cv in manager.identity.core_values}
            for cv in suggestion.add_core_values:
                if cv.value_id not in existing_cv:
                    manager.identity.core_values.append(cv)
                    existing_cv[cv.value_id] = cv
            # 权重更新
            for value_id, delta in suggestion.update_core_value_weights.items():
                target = existing_cv.get(value_id)
                if not target:
                    # 尝试按 name 找
                    for cv in manager.identity.core_values:
                        if cv.name == value_id:
                            target = cv
                            break
                if target:
                    new_w = max(
                        self.cfg.corevalue_min_weight,
                        min(
                            self.cfg.corevalue_max_weight,
                            target.weight + float(delta),
                        ),
                    )
                    target.weight = round(new_w, 5)

        # StableTrait 新增 / 更新
        if apply_stable_updates:
            existing_st = {st.trait: idx for idx, st in enumerate(manager.identity.stable_traits)}
            for st in suggestion.add_stable_traits:
                if st.trait not in existing_st:
                    manager.identity.stable_traits.append(st)
                    existing_st[st.trait] = len(manager.identity.stable_traits) - 1
                else:
                    # 合并
                    prev = manager.identity.stable_traits[existing_st[st.trait]]
                    if st.confidence >= prev.confidence:
                        st.sources = sorted(set(prev.sources + st.sources))
                        manager.identity.stable_traits[existing_st[st.trait]] = st
            # update_stable_traits: 按 delta 更新 current_value + confidence
            for trait, upd in suggestion.update_stable_traits.items():
                delta = float(upd.get("delta", 0.0) or 0.0)
                conf = float(upd.get("confidence", 0.0) or 0.0)
                srcs = list(upd.get("sources") or [])
                if trait in existing_st:
                    prev = manager.identity.stable_traits[existing_st[trait]]
                    new_val = max(0.0, min(1.0, prev.current_value + delta))
                    prev.current_value = round(new_val, 5)
                    prev.confidence = round(min(1.0, prev.confidence + 0.1 * conf), 4)
                    if delta > 0.001:
                        prev.direction = "increase"
                    elif delta < -0.001:
                        prev.direction = "decrease"
                    else:
                        prev.direction = "stable"
                    prev.sources = sorted(set(prev.sources + srcs))
                    prev.last_updated = now_iso()
                else:
                    new_st = StableTrait(
                        trait=trait,
                        current_value=round(max(0.0, min(1.0, delta)), 5),
                        direction="increase" if delta > 0 else "decrease" if delta < 0 else "stable",
                        stability=0.2,
                        confidence=round(min(1.0, 0.5 + 0.1 * conf), 4),
                        sources=srcs,
                        last_updated=now_iso(),
                    )
                    manager.identity.stable_traits.append(new_st)
                    existing_st[trait] = len(manager.identity.stable_traits) - 1

        # Preference 新增 / 更新
        if apply_preferences:
            for p in suggestion.add_preferences:
                k = f"{p.domain}::{p.key}"
                if k not in manager._preference_keys:
                    manager.identity.preferences.append(p)
                    manager._preference_keys.add(k)
                else:
                    for idx, prev in enumerate(manager.identity.preferences):
                        if f"{prev.domain}::{prev.key}" == k:
                            prev.evidence_count += max(1, p.evidence_count)
                            if p.confidence > prev.confidence:
                                prev.confidence = round(min(1.0, 0.9 * prev.confidence + 0.1 * p.confidence), 4)
                            prev.sources = sorted(set(prev.sources + p.sources))
                            prev.value = p.value or prev.value
                            break

        # Pattern 新增 / 更新
        if apply_patterns:
            for bp in suggestion.add_patterns:
                k = bp.pattern_detected
                if not k:
                    continue
                if k not in manager._pattern_keys:
                    manager.identity.behavioral_patterns.append(bp)
                    manager._pattern_keys.add(k)
                else:
                    for idx, prev in enumerate(manager.identity.behavioral_patterns):
                        if prev.pattern_detected == k:
                            prev.frequency += max(1, bp.frequency)
                            prev.confidence = round(min(1.0, 0.8 * prev.confidence + 0.2 * bp.confidence), 4)
                            if bp.last_observed:
                                prev.last_observed = bp.last_observed
                            prev.sources = sorted(set(prev.sources + bp.sources))
                            if bp.description and not prev.description:
                                prev.description = bp.description
                            break
            # 截断
            if len(manager.identity.behavioral_patterns) > manager.MAX_BEHAVIORAL_PATTERNS:
                manager.identity.behavioral_patterns = sorted(
                    manager.identity.behavioral_patterns,
                    key=lambda x: x.confidence * (x.frequency + 1),
                    reverse=True,
                )[: manager.MAX_BEHAVIORAL_PATTERNS]
                manager._pattern_keys = set(p.pattern_detected for p in manager.identity.behavioral_patterns)

        # 历史
        if apply_history and suggestion.add_history_entries:
            manager.identity.growth_history.extend(suggestion.add_history_entries)
            # 对 GR/PCR/Insight 的 source_id 注册避免重复（如需要）
            for h in suggestion.add_history_entries:
                if h.source_type == "growth_record" and h.source_id:
                    manager._growth_record_ids.add(h.source_id)
                if h.source_type == "reflection_insight" and h.source_id:
                    manager._insight_ids.add(h.source_id)
            if len(manager.identity.growth_history) > manager.MAX_GROWTH_HISTORY:
                manager.identity.growth_history = manager.identity.growth_history[
                    -manager.MAX_GROWTH_HISTORY:
                ]

        # Understanding 增量
        if apply_understanding and suggestion.understanding_delta:
            manager.identity.experience_awareness = round(
                min(0.95, manager.identity.experience_awareness + suggestion.understanding_delta.get("experience_awareness", 0.0)),
                4,
            )
            manager.identity.trait_awareness = round(
                min(0.95, manager.identity.trait_awareness + suggestion.understanding_delta.get("trait_awareness", 0.0)),
                4,
            )
            manager.identity.identity_continuity = round(
                min(0.95, manager.identity.identity_continuity + suggestion.understanding_delta.get("identity_continuity", 0.0)),
                4,
            )
            manager.identity.overall_understanding = round(
                (
                    manager.identity.experience_awareness
                    + manager.identity.trait_awareness
                    + manager.identity.identity_continuity
                )
                / 3,
                4,
            )

        # 版本 & 时间
        manager.identity.version += 1
        manager.identity.last_updated = now_iso()
