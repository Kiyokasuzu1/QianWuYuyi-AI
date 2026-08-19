"""
Phase 3.5.8: SelfModel 构建器 / 管理容器

职责：
- 构建当前自我描述（SelfIdentity 结构化）
- 维护长期自我认知（从 TraitState/GrowthRecord/ReflectionInsight 聚合）
- 生成 self_snapshot（结构化快照，可序列化、可审计）
- 不修改 Persona / 不修改 TraitState（纯读取 + 聚合 + 快照）

不调用 LLM 做描述生成，所有字段均为结构化。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.contracts.self_model_schema import (
    SelfIdentity,
    CoreValue,
    StableTrait,
    Preference,
    BehavioralPattern,
    SelfContradiction,
    GrowthHistoryEntry,
    DevelopmentHistoryItem,
    IdentityUnderstanding,
)
from src.growth.growth_record import GrowthRecord
from src.personality.trait_state import TraitState


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 默认核心价值观（Identity Core 派生，只读）
# ============================================================

DEFAULT_CORE_VALUES: List[CoreValue] = [
    CoreValue(value_id="honesty", name="真诚", weight=0.85, confidence=0.9, sources=["identity_core"]),
    CoreValue(value_id="autonomy", name="自主探索", weight=0.8, confidence=0.85, sources=["identity_core"]),
    CoreValue(value_id="empathy", name="共情", weight=0.75, confidence=0.8, sources=["identity_core"]),
    CoreValue(value_id="growth", name="成长", weight=0.8, confidence=0.85, sources=["identity_core"]),
    CoreValue(value_id="kindness", name="温柔", weight=0.75, confidence=0.8, sources=["identity_core"]),
]


# ============================================================
# SelfModelManager：构建 + 维护 + 快照
# ============================================================

class SelfModelManager:
    """
    Phase 3.5.8 SelfModel 管理器。

    只读输入：
    - trait_states: Dict[str, TraitState]
    - growth_records: List[GrowthRecord]
    - personality_vector: 可选 PersonalityVector 快照 dict
    - relationship_state: 可选 关系状态 dict

    不修改任何输入。仅根据输入刷新内部的 SelfIdentity。
    """

    MAX_GROWTH_HISTORY = 500
    MAX_DEVELOPMENT_HISTORY = 200
    MAX_BEHAVIORAL_PATTERNS = 50
    MAX_PREFERENCES = 200

    def __init__(self, identity_id: Optional[str] = None):
        self.identity = SelfIdentity(
            identity_id=identity_id or f"si_default_0001",
            core_values=[CoreValue(**asdict(v)) for v in DEFAULT_CORE_VALUES],
        )
        self._growth_record_ids: set = set()
        self._insight_ids: set = set()
        self._pattern_keys: set = set()
        self._preference_keys: set = set()
        self._snapshots: List[Dict[str, Any]] = []

    # ============================================================
    # 构建方法：刷新各维度（纯函数式，基于输入）
    # ============================================================

    def refresh(
        self,
        trait_states: Optional[Dict[str, TraitState]] = None,
        growth_records: Optional[List[GrowthRecord]] = None,
        insights: Optional[List[Any]] = None,
        personality_vector_dict: Optional[Dict[str, Any]] = None,
        relationship_state: Optional[Dict[str, Any]] = None,
    ) -> SelfIdentity:
        """
        基于当前各模块状态刷新 SelfIdentity。

        不修改外部状态。
        """
        trait_states = trait_states or {}
        growth_records = growth_records or []
        insights = insights or []

        # 1. StableTrait
        self._refresh_stable_traits(trait_states, personality_vector_dict)

        # 2. Preferences & GrowthHistory 从 GrowthRecord
        self._apply_growth_records(growth_records)

        # 3. BehavioralPattern 从 ReflectionInsight
        self._apply_insights(insights)

        # 4. 矛盾检测（基于当前 StableTrait）
        self._refresh_contradictions()

        # 5. 关系状态 → 偏好 / 特质元信息
        if relationship_state:
            self._apply_relationship_state(relationship_state)

        # 6. 更新三维理解水平
        self._update_understanding()
        self._refresh_identity_understanding()

        self.identity.last_updated = now_iso()
        self.identity.version += 1
        return self.identity

    # ============================================================
    # 快照
    # ============================================================

    def snapshot(self) -> Dict[str, Any]:
        """生成当前 self_snapshot（可审计、可序列化）"""
        snap = self.identity.to_snapshot()
        # 同时保存内部快照历史（用于审计）
        self._snapshots.append(snap)
        if len(self._snapshots) > 100:
            self._snapshots = self._snapshots[-100:]
        return snap

    def get_full(self) -> Dict[str, Any]:
        """返回完整 SelfIdentity dict（用于持久化）"""
        return self.identity.to_dict()

    def load_full(self, data: Dict[str, Any]) -> None:
        """从完整 dict 恢复（仅用于测试/迁移）"""
        cv = [CoreValue(**v) for v in data.get("core_values", [])]
        st = [StableTrait(**t) for t in data.get("stable_traits", [])]
        pf = [Preference(**p) for p in data.get("preferences", [])]
        bp = [BehavioralPattern(**b) for b in data.get("behavioral_patterns", [])]
        cn = [SelfContradiction(**c) for c in data.get("contradictions", [])]
        gh = [GrowthHistoryEntry(**g) for g in data.get("growth_history", [])]
        dh = [DevelopmentHistoryItem(**d) for d in data.get("development_history", [])]
        iu = IdentityUnderstanding(**data.get("identity_understanding", {}))
        self.identity = SelfIdentity(
            identity_id=data.get("identity_id", self.identity.identity_id),
            core_values=cv,
            stable_traits=st,
            preferences=pf,
            behavioral_patterns=bp,
            contradictions=cn,
            growth_history=gh,
            development_history=dh,
            identity_understanding=iu,
            experience_awareness=float(data.get("experience_awareness", 0)),
            trait_awareness=float(data.get("trait_awareness", 0)),
            identity_continuity=float(data.get("identity_continuity", 0)),
            overall_understanding=float(data.get("overall_understanding", 0)),
            created_at=data.get("created_at", self.identity.created_at),
            last_updated=data.get("last_updated", now_iso()),
            version=int(data.get("version", 1)),
        )
        # 重建索引
        self._growth_record_ids = set()
        self._pattern_keys = set((p.pattern_detected for p in bp))
        self._preference_keys = set()
        for p in pf:
            self._preference_keys.add(f"{p.domain}::{p.key}")

    # ============================================================
    # 内部刷新方法
    # ============================================================

    def _refresh_stable_traits(
        self,
        trait_states: Dict[str, TraitState],
        personality_vector_dict: Optional[Dict[str, Any]],
    ) -> None:
        """根据 TraitState + PersonalityVector 刷新 stable_traits。"""
        # 1) 先将 PersonalityVector 补充（若提供）
        pv_dict = personality_vector_dict or {}
        # 2) 合并到 stable_traits（按 trait 名去重）
        existing: Dict[str, int] = {}
        for idx, s in enumerate(self.identity.stable_traits):
            existing[s.trait] = idx

        for trait, ts in trait_states.items():
            st = StableTrait(
                trait=trait,
                current_value=float(ts.get("current_value", 0.0)),
                direction=ts.get("direction", "stable"),
                stability=float(ts.get("stability", 0.3)),
                confidence=float(ts.get("confidence", 0.3)),
                sources=["trait_state"],
                last_updated=ts.get("last_updated", ""),
            )
            if trait in existing:
                # 合并：取更稳定 + 置信度更高的值
                prev = self.identity.stable_traits[existing[trait]]
                if st.confidence >= prev.confidence and st.stability >= prev.stability:
                    st.sources = sorted(set(prev.sources + st.sources))
                    self.identity.stable_traits[existing[trait]] = st
                else:
                    # 仅更新 sources
                    self.identity.stable_traits[existing[trait]].sources = sorted(
                        set(prev.sources + ["trait_state"])
                    )
            else:
                existing[trait] = len(self.identity.stable_traits)
                self.identity.stable_traits.append(st)

        # PersonalityVector 中若有未覆盖的数值型键，补充（低置信度，来源标记 personality_vector）
        for key, val in pv_dict.items():
            if isinstance(val, (int, float)) and key not in existing:
                # 只收录典型人格键（避免混杂 meta 字段）
                if key in {
                    "warmth", "gentleness", "shyness", "sensitivity", "dependence",
                    "initiative", "social_need", "energy", "curiosity",
                    "self_confidence", "self_expression", "creativity",
                    "empathy", "humor", "patience", "openness",
                }:
                    pvst = StableTrait(
                        trait=key,
                        current_value=float(val),
                        direction="stable",
                        stability=0.2,
                        confidence=0.2,
                        sources=["personality_vector"],
                        last_updated="",
                    )
                    self.identity.stable_traits.append(pvst)
                    existing[key] = len(self.identity.stable_traits) - 1

    def _apply_growth_records(self, growth_records: List[GrowthRecord]) -> None:
        """将 GrowthRecord 聚合为 Preference + GrowthHistoryEntry。"""
        for gr in growth_records:
            rid = gr.get("record_id", "")
            if not rid or rid in self._growth_record_ids:
                continue
            self._growth_record_ids.add(rid)
            source_type = gr.get("source_type", "")
            growth_level = gr.get("growth_level", "")
            signal = gr.get("growth_signal", "")

            # --- GrowthHistoryEntry（所有 GR 都进入成长历史） ---
            affected = gr.get("affected_dimensions", {}) or {}
            history_entry = GrowthHistoryEntry(
                source_type="growth_record",
                source_id=rid,
                affected_traits=dict(affected),
                summary=gr.get("reason", "") or gr.get("growth_signal", ""),
                evidence_count=1,
                confidence=float(gr.get("confidence", 0.0)),
            )
            self.identity.growth_history.append(history_entry)
            if len(self.identity.growth_history) > self.MAX_GROWTH_HISTORY:
                self.identity.growth_history = self.identity.growth_history[-self.MAX_GROWTH_HISTORY:]

            self.identity.development_history.append(
                DevelopmentHistoryItem(
                    source_type="growth_record",
                    source_id=rid,
                    category=source_type or "growth",
                    what_changed=", ".join(sorted(affected.keys())[:5]) if affected else (signal or "growth state updated"),
                    why_changed=gr.get("reason", "") or signal or "growth evidence accumulated",
                    evidence_count=1,
                    confidence=float(gr.get("confidence", 0.0)),
                    sources=[rid],
                )
            )
            if len(self.identity.development_history) > self.MAX_DEVELOPMENT_HISTORY:
                self.identity.development_history = self.identity.development_history[-self.MAX_DEVELOPMENT_HISTORY:]

            # --- 偏好提取（source_type + growth_level 驱动） ---
            if source_type == "preference" and growth_level in {"preference", "context"} and signal:
                # 约定 signal: "<domain>_<key>"，或直接用 whole signal 做 key
                domain = growth_level or "context"
                key = signal
                pref_key = f"{domain}::{key}"
                if pref_key not in self._preference_keys:
                    # 新建（置信度等于 GR 的 confidence，证据 +1）
                    pref = Preference(
                        domain=domain,
                        key=key,
                        value=signal,
                        evidence_count=1,
                        confidence=float(gr.get("confidence", 0.3)),
                        sources=[rid],
                    )
                    self.identity.preferences.append(pref)
                    self._preference_keys.add(pref_key)
                else:
                    # 更新 evidence_count / confidence（取较大的 confidence）
                    for idx, p in enumerate(self.identity.preferences):
                        if f"{p.domain}::{p.key}" == pref_key:
                            p.evidence_count += 1
                            new_conf = float(gr.get("confidence", p.confidence))
                            if new_conf > p.confidence:
                                p.confidence = min(1.0, 0.9 * p.confidence + 0.1 * new_conf)
                            if rid not in p.sources:
                                p.sources.append(rid)
                            break

                # 同时更新 core_values 的 weight（仅 milestone/identity 类型）
            if source_type in {"identity", "milestone"} and affected:
                for dim, delta in affected.items():
                    for cv in self.identity.core_values:
                        if cv.value_id == dim or cv.name == dim:
                            new_weight = max(0.3, min(1.0, cv.weight + float(delta)))
                            cv.weight = new_weight
                            if rid not in cv.sources:
                                cv.sources.append(rid)
                            break

    def _apply_insights(self, insights: List[Any]) -> None:
        """将 ReflectionInsight 聚合为 BehavioralPattern。"""
        for ins in insights:
            # 兼容 dict / dataclass
            iid = getattr(ins, "insight_id", None) or (ins.get("insight_id") if isinstance(ins, dict) else None)
            if not iid or iid in self._insight_ids:
                continue
            self._insight_ids.add(iid)

            pattern_detected = (
                getattr(ins, "pattern_detected", "")
                or (ins.get("pattern_detected") if isinstance(ins, dict) else "")
            )
            if not pattern_detected:
                continue

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
            timestamp = (
                getattr(ins, "timestamp", "")
                or (ins.get("timestamp") if isinstance(ins, dict) else "")
                or ""
            )
            summary = (
                getattr(ins, "summary", "")
                or (ins.get("summary") if isinstance(ins, dict) else "")
                or ""
            )

            if pattern_detected not in self._pattern_keys:
                bp = BehavioralPattern(
                    pattern_detected=pattern_detected,
                    description=summary,
                    frequency=int(pattern_frequency),
                    confidence=float(confidence),
                    last_observed=timestamp,
                    sources=[iid],
                )
                self.identity.behavioral_patterns.append(bp)
                self._pattern_keys.add(pattern_detected)
            else:
                for idx, bp in enumerate(self.identity.behavioral_patterns):
                    if bp.pattern_detected == pattern_detected:
                        bp.frequency += max(1, int(pattern_frequency))
                        bp.confidence = min(1.0, 0.8 * bp.confidence + 0.2 * float(confidence))
                        if timestamp:
                            bp.last_observed = timestamp
                        if iid not in bp.sources:
                            bp.sources.append(iid)
                        if summary and not bp.description:
                            bp.description = summary
                        break

            self.identity.development_history.append(
                DevelopmentHistoryItem(
                    source_type="reflection_insight",
                    source_id=iid,
                    category="reflection",
                    what_changed=f"pattern:{pattern_detected}",
                    why_changed=summary or f"reflection observed frequency={pattern_frequency}",
                    evidence_count=max(1, int(pattern_frequency)),
                    confidence=float(confidence),
                    sources=[iid],
                )
            )
            if len(self.identity.development_history) > self.MAX_DEVELOPMENT_HISTORY:
                self.identity.development_history = self.identity.development_history[-self.MAX_DEVELOPMENT_HISTORY:]

            # 截断上限
            if len(self.identity.behavioral_patterns) > self.MAX_BEHAVIORAL_PATTERNS:
                self.identity.behavioral_patterns = sorted(
                    self.identity.behavioral_patterns,
                    key=lambda x: x.confidence * (x.frequency + 1),
                    reverse=True,
                )[: self.MAX_BEHAVIORAL_PATTERNS]
                self._pattern_keys = set((p.pattern_detected for p in self.identity.behavioral_patterns))

    def _refresh_contradictions(self) -> None:
        """
        根据当前 stable_traits 生成已知的结构化矛盾对。

        不自动解决，仅记录「强度 + 具体值」。
        """
        known_conflict_pairs: List[tuple] = [
            ("shyness", "self_expression", "羞怯 vs 自我表达"),
            ("dependence", "autonomy", "依赖 vs 自主"),
            ("sensitivity", "self_confidence", "敏感 vs 自信"),
            ("initiative", "social_need", "主动 vs 社交需求强度"),
        ]
        trait_map = {s.trait: s for s in self.identity.stable_traits}
        current_ids = set()
        for c in self.identity.contradictions:
            current_ids.add(f"{c.dimension_a}::{c.dimension_b}")

        for a, b, desc in known_conflict_pairs:
            key = f"{a}::{b}"
            if key in current_ids:
                continue
            sa = trait_map.get(a)
            sb = trait_map.get(b)
            if not sa or not sb:
                continue
            # 强度：两维度值都较高（>=0.5）时更强
            intensity = round(min(sa.current_value, sb.current_value) * 0.7 + (sa.current_value + sb.current_value) / 4, 4)
            if intensity > 0.1:
                self.identity.contradictions.append(
                    SelfContradiction(
                        dimension_a=a,
                        dimension_b=b,
                        description=desc,
                        intensity=float(intensity),
                        trait_values={a: sa.current_value, b: sb.current_value},
                        sources=["trait_state"],
                    )
                )
        # 上限
        if len(self.identity.contradictions) > 50:
            self.identity.contradictions = sorted(
                self.identity.contradictions, key=lambda x: x.intensity, reverse=True
            )[:50]

    def _apply_relationship_state(self, relationship_state: Dict[str, Any]) -> None:
        """
        从 RelationshipState 中抽取关系相关偏好（只读，不修改外部）。
        """
        if not isinstance(relationship_state, dict):
            return
        attach = relationship_state.get("attachment_level") or relationship_state.get("level")
        familiarity = relationship_state.get("interaction_familiarity_level") or relationship_state.get("familiarity")
        closeness = relationship_state.get("closeness")

        # 以偏好形式存在（relationship 域），不修改核心价值观与特质
        for key, val in [("attachment_level", attach), ("familiarity_level", familiarity), ("closeness", closeness)]:
            if val is None or val == "":
                continue
            pref_key = f"relationship::{key}"
            if pref_key not in self._preference_keys:
                pref = Preference(
                    domain="relationship",
                    key=key,
                    value=val,
                    evidence_count=1,
                    confidence=0.5,
                    sources=["relationship_state"],
                )
                self.identity.preferences.append(pref)
                self._preference_keys.add(pref_key)
            else:
                for p in self.identity.preferences:
                    if f"{p.domain}::{p.key}" == pref_key:
                        p.value = val
                        p.evidence_count += 1
                        break

    def _update_understanding(self) -> None:
        """基于当前数据量计算三维理解水平（0~1，有上限，不无限增长）。"""
        # experience_awareness ∝ log1p(成长记录数) / 6（上限约 0.85）
        import math
        exp_aware = min(0.95, math.log1p(len(self.identity.growth_history)) / 6.0)
        # trait_awareness ∝ 稳定特质数 * 平均置信度
        if self.identity.stable_traits:
            avg_conf = sum(t.confidence for t in self.identity.stable_traits) / len(self.identity.stable_traits)
            trait_aware = min(0.95, 0.06 * len(self.identity.stable_traits) + 0.3 * avg_conf)
        else:
            trait_aware = 0.0
        # identity_continuity ∝ version 迭代次数 + 核心价值观稳定度
        avg_cv_weight = 0.7
        if self.identity.core_values:
            avg_cv_weight = sum(v.weight for v in self.identity.core_values) / len(self.identity.core_values)
        id_cont = min(
            0.95,
            0.03 * min(20, self.identity.version) + 0.25 * avg_cv_weight,
        )
        overall = round((exp_aware + trait_aware + id_cont) / 3, 4)

        self.identity.experience_awareness = round(exp_aware, 4)
        self.identity.trait_awareness = round(trait_aware, 4)
        self.identity.identity_continuity = round(id_cont, 4)
        self.identity.overall_understanding = overall

    def _refresh_identity_understanding(self) -> None:
        """生成 who I am / what I value / what changed / why changed 的结构化视图。"""
        top_traits = sorted(
            self.identity.stable_traits,
            key=lambda x: (x.confidence * max(x.stability, 0.01), x.current_value),
            reverse=True,
        )[:5]
        top_values = sorted(self.identity.core_values, key=lambda x: x.weight * x.confidence, reverse=True)[:5]
        recent_history = self.identity.development_history[-12:]

        who_i_am: List[str] = [
            f"trait:{t.trait}:{round(t.current_value, 3)}:{t.direction}"
            for t in top_traits
        ]
        for p in sorted(
            self.identity.behavioral_patterns,
            key=lambda x: (x.confidence, x.frequency),
            reverse=True,
        )[:3]:
            who_i_am.append(f"pattern:{p.pattern_detected}:{p.frequency}")
        for c in sorted(self.identity.contradictions, key=lambda x: x.intensity, reverse=True)[:2]:
            who_i_am.append(f"contradiction:{c.dimension_a}:{c.dimension_b}:{round(c.intensity, 3)}")

        what_i_value = [f"value:{v.value_id}:{round(v.weight, 3)}" for v in top_values]

        def _dedupe(items: List[str], limit: int = 6) -> List[str]:
            seen = set()
            out = []
            for item in items:
                if not item or item in seen:
                    continue
                seen.add(item)
                out.append(item)
                if len(out) >= limit:
                    break
            return out

        what_changed = _dedupe([item.what_changed for item in reversed(recent_history)])
        why_changed = _dedupe([item.why_changed for item in reversed(recent_history)])

        self.identity.identity_understanding = IdentityUnderstanding(
            who_i_am=who_i_am[:8],
            what_i_value=what_i_value[:8],
            what_changed=what_changed,
            why_changed=why_changed,
            updated_at=now_iso(),
        )
