# src/contracts/existence_adapters.py
"""
Phase D.6.1.1: 三个来源 → ExistenceMilestone 纯转换 Adapter

严格规则(禁止违反):
1. Adapter = 纯数据翻译,不调用 LLM / API / Storage。
   输入:源系统 Dict;输出:List[ExistenceMilestone]。
2. 不伪造:如果源对象缺少字段(例如没有 timestamp),
   不会用 now_iso() / "猜一个 0.72" 补齐;
   Adapter 允许生成 is_well_formed() == False 的 Milestone,
   TimelineBuilder 在 D.6.1.2 阶段会统一过滤掉。
3. Title / Summary 只写事实性文字:
   Good: "Growth Proposal (applied): warmth = 0.62 → 0.64"
   Bad :  "羽依第一次感受到了温柔的意义"
4. 所有证据都带上 source_type ∈ VALID_SOURCE_TYPES + 真实 source_id。
   绝不为了"让列表有长度"而塞假 EvidenceReference。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from .existence import (
    EvidenceReference,
    ExistenceMilestone,
    ExplanationMetadata,
    ImpactMap,
    MILESTONE_BELIEF,
    MILESTONE_GROWTH,
    MILESTONE_IDENTITY,
    MILESTONE_REFLECTION,
    MILESTONE_TRAIT_CHANGE,
    SOURCE_GROWTH_PROPOSAL,
    SOURCE_PERSONALITY_EVOLUTION,
    SOURCE_REFLECTION_INSIGHT,
    SOURCE_SELF_MODEL_BELIEF,
    SOURCE_SELF_MODEL_HISTORY,
    SOURCE_SELF_MODEL_TRAIT,
    now_iso,
)


# ============================================================
# 工具:纯函数提取器
# ============================================================

def _s(v: Any, default: str = "") -> str:
    return v if isinstance(v, str) else default


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _norm_ts(ts: Any) -> str:
    """把 timestamp 规范成 UTC ISO with Z。无法识别则返回 ""。"""
    if not isinstance(ts, str) or not ts:
        return ""
    # 已经带 Z
    if ts.endswith("Z"):
        return ts
    # datetime.isoformat(): "+00:00" → 替换成 Z
    if ts.endswith("+00:00"):
        return ts[:-6] + "Z"
    if ts.endswith("-00:00"):
        return ts[:-6] + "Z"
    # 看起来像 ISO 8601 (有 T),就补 Z(假定 UTC)
    if "T" in ts and len(ts) >= 10:
        return ts + "Z"
    return ts  # 非 ISO 格式原样返回,由 has_valid_timestamp 来拒绝


# ============================================================
# 1. Growth Proposal Adapter (D.6.1.1-a)
# ============================================================

@dataclass
class ProposalMilestoneAdapter:
    """
    GovernanceProvider.list_proposals → List[ExistenceMilestone]

    输入:每个 proposal 的 to_dict() 结果(growth_schema.py GrowthProposal):
      - id / timestamp (ISO Z) / status / confidence / evidence_ids /
        proposed_changes (List[ChangeItem{path,before,after,reason}]) /
        accepted_at / rejected_at / evaluator_meta / source_event_id

    输出:
      milestone_type = MILESTONE_GROWTH
      timestamp:优先 accepted_at,否则 rejected_at,否则 proposal.timestamp
      仅当 status 是终态(accepted/rejected/applied/cancelled/expired)时
        才算有意义的 Milestone; "proposed" 不进入 Timeline(会变)。
    """

    adapter_name: str = "proposal_milestone_adapter_v1"

    # 终态 proposal 才会产出 Milestone (proposed 阶段仍在流动,不算存在记录)
    FINAL_STATUSES = frozenset({
        "accepted", "rejected", "applied", "cancelled", "expired",
    })

    # --------------------------------------------------------
    def convert_many(self, proposals: Iterable[Dict[str, Any]]) -> List[ExistenceMilestone]:
        out: List[ExistenceMilestone] = []
        for p in proposals:
            if not isinstance(p, dict):
                continue
            m = self.convert_one(p)
            if m is not None:
                out.append(m)
        return out

    def convert_one(self, proposal: Dict[str, Any]) -> Optional[ExistenceMilestone]:
        if not isinstance(proposal, dict):
            return None
        status = _s(proposal.get("status"))
        if status not in self.FINAL_STATUSES:
            # "proposed" 中:还在流动,不算存在记录
            return None

        prop_id = _s(proposal.get("id"))
        if not prop_id:
            return None

        # 时间:优先应用/接受时间
        ts = (
            _norm_ts(proposal.get("accepted_at"))
            or _norm_ts(proposal.get("rejected_at"))
            or _norm_ts(proposal.get("applied_at"))
            or _norm_ts(proposal.get("timestamp"))
        )
        confidence = _f(proposal.get("confidence"))
        evidence_ids: List[str] = []
        ev_ids = proposal.get("evidence_ids") or []
        if isinstance(ev_ids, list):
            evidence_ids = [_s(e) for e in ev_ids if _s(e)]

        proposed_changes = proposal.get("proposed_changes") or []
        changes_fact: List[str] = []
        affected_keys: List[str] = []
        if isinstance(proposed_changes, list):
            for ch in proposed_changes:
                if not isinstance(ch, dict):
                    continue
                path = _s(ch.get("path"))
                if path and path not in affected_keys:
                    affected_keys.append(path)
                before = ch.get("before")
                after = ch.get("after")
                if path:
                    changes_fact.append(f"{path}={before!r}→{after!r}")

        # 事实标题/摘要(不叙事,纯文字拼接)
        if status == "applied" or status == "accepted":
            title = f"Growth Proposal ({status}): {len(changes_fact)} changes"
        else:
            title = f"Growth Proposal ({status})"
        if changes_fact:
            summary = "Changes: " + "; ".join(changes_fact[:5])
            if len(changes_fact) > 5:
                summary += f" (+{len(changes_fact) - 5} more)"
        else:
            summary = f"proposal_id={prop_id}"

        # 证据:至少 1 条 (该 proposal 自身),如果 evidence_ids 有再加
        sources: List[EvidenceReference] = [
            EvidenceReference(
                source_type=SOURCE_GROWTH_PROPOSAL,
                source_id=prop_id,
                confidence=confidence,
                note=f"status={status}",
                metadata={
                    "evaluator_meta": dict(proposal.get("evaluator_meta") or {})
                    if isinstance(proposal.get("evaluator_meta"), dict)
                    else {},
                    "source_event_id": _s(proposal.get("source_event_id")),
                },
            )
        ]
        for ev_id in evidence_ids[:20]:  # 截断避免过长
            if ev_id == prop_id:
                continue
            sources.append(EvidenceReference(
                source_type=SOURCE_GROWTH_PROPOSAL,
                source_id=ev_id,
                note="evidence_chain",
            ))

        # Impact:任何 proposal 都影响 personality(如果改了 traits)
        impact = ImpactMap(
            personality=any(
                ("trait" in k or "personality" in k) for k in affected_keys
            ),
            belief=any("belief" in k for k in affected_keys),
            identity=any("identity" in k for k in affected_keys),
            memory=any("memory" in k for k in affected_keys),
        )

        explanation = ExplanationMetadata(
            evidence_count=len(sources),
            confidence=confidence,
            # why_created 留空给 D.8 Explanation Engine
        )

        return ExistenceMilestone(
            timestamp=ts,
            milestone_type=MILESTONE_GROWTH,
            title=title,
            summary=summary,
            sources=sources,
            impact=impact,
            explanation=explanation,
            affected_keys=affected_keys,
            adapter_name=self.adapter_name,
        )


# ============================================================
# 2. SelfModel Adapter (D.6.1.1-b)
# ============================================================

@dataclass
class SelfModelMilestoneAdapter:
    """
    SelfModelProvider 的 4 类输出 → List[ExistenceMilestone]

    覆盖 4 种 SelfModel 输出信封:
      - list_beliefs envelope       → MILESTONE_BELIEF
      - list_history envelope       → MILESTONE_TRAIT_CHANGE / MILESTONE_BELIEF / MILESTONE_IDENTITY
      - list_reflections envelope   → MILESTONE_REFLECTION
      - list_stable_traits envelope → MILESTONE_TRAIT_CHANGE (仅稳定特质)
    """

    adapter_name: str = "selfmodel_milestone_adapter_v1"

    # 只有 belief 证据数 ≥ 此值才产出 Milestone (避免噪声)
    BELIEF_MIN_EVIDENCE = 2
    BELIEF_MIN_CONFIDENCE = 0.55
    # 只有 stable_trait stability ≥ 此值才算存在记录
    TRAIT_MIN_STABILITY = 0.45
    # Reflection 阈值
    REFLECT_MIN_CONFIDENCE = 0.5
    REFLECT_MIN_CONTENT_LEN = 20

    # --------------------------------------------------------
    def convert_all(
        self,
        *,
        beliefs_envelope: Optional[Dict[str, Any]] = None,
        history_envelope: Optional[Dict[str, Any]] = None,
        reflections_envelope: Optional[Dict[str, Any]] = None,
        stable_traits_envelope: Optional[Dict[str, Any]] = None,
    ) -> List[ExistenceMilestone]:
        out: List[ExistenceMilestone] = []
        out.extend(self.convert_beliefs(beliefs_envelope or {}))
        out.extend(self.convert_history(history_envelope or {}))
        out.extend(self.convert_reflections(reflections_envelope or {}))
        out.extend(self.convert_stable_traits(stable_traits_envelope or {}))
        return out

    # --------------------------------------------------------
    # 2.1 Beliefs
    # --------------------------------------------------------
    def convert_beliefs(self, envelope: Dict[str, Any]) -> List[ExistenceMilestone]:
        out: List[ExistenceMilestone] = []
        if not isinstance(envelope, dict):
            return out
        items = envelope.get("items") or []
        if not isinstance(items, list):
            return out
        for b in items:
            if not isinstance(b, dict):
                continue
            conf = _f(b.get("confidence"))
            evid_cnt = _i(b.get("evidence_count"))
            active = bool(b.get("active", True))
            if (
                conf < self.BELIEF_MIN_CONFIDENCE
                or evid_cnt < self.BELIEF_MIN_EVIDENCE
                or not active
            ):
                continue
            bid = _s(b.get("belief_id"))
            if not bid:
                continue
            ts = (
                _norm_ts(b.get("last_confirmed"))
                or _norm_ts(b.get("first_seen"))
                or ""
            )
            content = _s(b.get("content"))
            if not content:
                continue
            domain = _s(b.get("domain")) or "value"
            version = _i(b.get("version"), 1)

            title = f"Belief formed: {domain} (v{version})"
            summary = content[:160] if len(content) > 160 else content
            sources = [EvidenceReference(
                source_type=SOURCE_SELF_MODEL_BELIEF,
                source_id=bid,
                confidence=conf,
                note=f"domain={domain} active={active}",
                metadata={"sources": list(b.get("sources") or [])[:20]},
            )]
            impact = ImpactMap(belief=True, identity=False)
            if domain in ("identity", "core", "self"):
                impact.identity = True
            explanation = ExplanationMetadata(
                evidence_count=evid_cnt,
                confidence=conf,
            )
            out.append(ExistenceMilestone(
                timestamp=ts,
                milestone_type=MILESTONE_BELIEF,
                title=title,
                summary=summary,
                sources=sources,
                impact=impact,
                explanation=explanation,
                affected_keys=[bid],
                adapter_name=self.adapter_name + ":beliefs",
            ))
        return out

    # --------------------------------------------------------
    # 2.2 History
    # --------------------------------------------------------
    def convert_history(self, envelope: Dict[str, Any]) -> List[ExistenceMilestone]:
        out: List[ExistenceMilestone] = []
        if not isinstance(envelope, dict):
            return out
        items = envelope.get("items") or []
        if not isinstance(items, list):
            return out
        for ev in items:
            if not isinstance(ev, dict):
                continue
            ts = _norm_ts(ev.get("timestamp"))
            if not ts:
                continue
            etype = _s(ev.get("event_type"))
            eid = _s(ev.get("event_id"))
            if not eid:
                continue
            # 根据 event_type 推断 milestone_type
            mtype = self._history_type_to_milestone_type(etype)
            if mtype is None:
                continue
            summary = _s(ev.get("summary")) or f"event_type={etype}"
            actor = _s(ev.get("actor"))
            title = f"SelfHistory: {etype}"
            src_type = _s(ev.get("source_type")) or SOURCE_SELF_MODEL_HISTORY
            # 如果 source_type 不在合法集合,回退到 history
            if src_type not in {
                SOURCE_SELF_MODEL_HISTORY,
                SOURCE_SELF_MODEL_BELIEF,
                SOURCE_SELF_MODEL_TRAIT,
                SOURCE_GROWTH_PROPOSAL,
                SOURCE_PERSONALITY_EVOLUTION,
                SOURCE_REFLECTION_INSIGHT,
                SOURCE_IDENTITY_ANCHOR := "identity_anchor",
                SOURCE_RELATIONSHIP_SNAPSHOT := "relationship_snapshot",
            }:
                src_type = SOURCE_SELF_MODEL_HISTORY
            src_id = _s(ev.get("source_id")) or eid
            affected_traits = ev.get("affected_traits") or {}
            affected_beliefs = ev.get("affected_beliefs") or []
            akeys: List[str] = []
            if isinstance(affected_traits, dict):
                akeys.extend([k for k in affected_traits.keys() if _s(k)])
            if isinstance(affected_beliefs, list):
                akeys.extend([_s(k) for k in affected_beliefs if _s(k)])
            sources = [EvidenceReference(
                source_type=src_type,
                source_id=src_id,
                note=f"event_id={eid} actor={actor}",
            )]
            imp = ImpactMap(
                personality=mtype == MILESTONE_TRAIT_CHANGE,
                belief=mtype == MILESTONE_BELIEF,
                identity=mtype == MILESTONE_IDENTITY,
            )
            out.append(ExistenceMilestone(
                timestamp=ts,
                milestone_type=mtype,
                title=title,
                summary=summary[:200],
                sources=sources,
                impact=imp,
                explanation=ExplanationMetadata(evidence_count=1),
                affected_keys=akeys[:30],
                adapter_name=self.adapter_name + ":history",
            ))
        return out

    @staticmethod
    def _history_type_to_milestone_type(event_type: str) -> Optional[str]:
        if not event_type:
            return None
        # 严格映射(默认 None → 不产出),避免"乱猜 milestone 类型"
        MAP = {
            "trait_change": MILESTONE_TRAIT_CHANGE,
            "trait_update": MILESTONE_TRAIT_CHANGE,
            "trait_shift": MILESTONE_TRAIT_CHANGE,
            "pcr_applied": MILESTONE_TRAIT_CHANGE,
            "pcr_accepted": MILESTONE_GROWTH,
            "belief_added": MILESTONE_BELIEF,
            "belief_updated": MILESTONE_BELIEF,
            "belief_confirmed": MILESTONE_BELIEF,
            "identity_anchor": MILESTONE_IDENTITY,
            "identity_change": MILESTONE_IDENTITY,
            "self_reflection": MILESTONE_REFLECTION,
            "reflection_recorded": MILESTONE_REFLECTION,
            "growth_recorded": MILESTONE_GROWTH,
            "relationship_change": MILESTONE_IDENTITY,  # 暂时无独立 MILESTONE_RELATIONSHIP 默认归类到 identity
        }
        return MAP.get(event_type)

    # --------------------------------------------------------
    # 2.3 Reflections
    # --------------------------------------------------------
    def convert_reflections(self, envelope: Dict[str, Any]) -> List[ExistenceMilestone]:
        out: List[ExistenceMilestone] = []
        if not isinstance(envelope, dict):
            return out
        items = envelope.get("items") or []
        if not isinstance(items, list):
            return out
        for n in items:
            if not isinstance(n, dict):
                continue
            nid = _s(n.get("note_id"))
            if not nid:
                continue
            ts = _norm_ts(n.get("timestamp"))
            conf = _f(n.get("confidence"))
            content = _s(n.get("content"))
            if conf < self.REFLECT_MIN_CONFIDENCE:
                continue
            if len(content) < self.REFLECT_MIN_CONTENT_LEN:
                # 过短的反思没价值
                continue
            rt = _s(n.get("reflection_type")) or "reflection"
            ts2 = _s(n.get("trigger_source")) or ""
            title = f"Reflection ({rt}): {ts2 or 'insight'}"
            summary = content[:200]
            sources_list = n.get("sources") or []
            sources: List[EvidenceReference] = [
                EvidenceReference(
                    source_type=SOURCE_REFLECTION_INSIGHT,
                    source_id=nid,
                    confidence=conf,
                    note=f"trigger_source={ts2}",
                )
            ]
            if isinstance(sources_list, list):
                for si in sources_list[:10]:
                    if isinstance(si, dict):
                        sit = _s(si.get("source_type"))
                        sid = _s(si.get("source_id")) or _s(si.get("id"))
                        if sid and sit:
                            sources.append(EvidenceReference(
                                source_type=sit if sit in {
                                    SOURCE_GROWTH_PROPOSAL,
                                    SOURCE_SELF_MODEL_BELIEF,
                                    SOURCE_SELF_MODEL_TRAIT,
                                    SOURCE_SELF_MODEL_HISTORY,
                                    SOURCE_REFLECTION_INSIGHT,
                                    SOURCE_PERSONALITY_EVOLUTION,
                                    SOURCE_IDENTITY_ANCHOR := "identity_anchor",
                                    SOURCE_RELATIONSHIP_SNAPSHOT := "relationship_snapshot",
                                } else SOURCE_REFLECTION_INSIGHT,
                                source_id=sid,
                                note=_s(si.get("note")),
                            ))
            rel_belief_ids = n.get("related_belief_ids") or []
            rel_traits = n.get("related_trait_changes") or {}
            akeys: List[str] = []
            if isinstance(rel_belief_ids, list):
                akeys.extend([_s(x) for x in rel_belief_ids if _s(x)])
            if isinstance(rel_traits, dict):
                akeys.extend([k for k in rel_traits.keys() if _s(k)])
            imp = ImpactMap(
                belief=bool(rel_belief_ids and isinstance(rel_belief_ids, list) and len(rel_belief_ids) > 0),
                personality=bool(rel_traits and isinstance(rel_traits, dict) and len(rel_traits) > 0),
            )
            explanation = ExplanationMetadata(
                evidence_count=len(sources),
                confidence=conf,
            )
            out.append(ExistenceMilestone(
                timestamp=ts,
                milestone_type=MILESTONE_REFLECTION,
                title=title,
                summary=summary,
                sources=sources,
                impact=imp,
                explanation=explanation,
                affected_keys=akeys[:30],
                adapter_name=self.adapter_name + ":reflections",
            ))
        return out

    # --------------------------------------------------------
    # 2.4 Stable Traits (SelfModelV3)
    # --------------------------------------------------------
    def convert_stable_traits(self, envelope: Dict[str, Any]) -> List[ExistenceMilestone]:
        out: List[ExistenceMilestone] = []
        if not isinstance(envelope, dict):
            return out
        items = envelope.get("items") or []
        if not isinstance(items, list):
            return out
        for t in items:
            if not isinstance(t, dict):
                continue
            tid = _s(t.get("trait_id")) or _s(t.get("name"))
            if not tid:
                continue
            stab = _f(t.get("stability"))
            if stab < self.TRAIT_MIN_STABILITY:
                continue
            evid_cnt = _i(t.get("evidence_count"))
            val = _i(t.get("value_pct"))
            ts = (
                _norm_ts(t.get("last_observed"))
                or _norm_ts(t.get("first_observed"))
                or ""
            )
            trend = t.get("trend_30d")
            trend_v: Optional[float] = None
            if trend is not None:
                try:
                    trend_v = float(trend)
                except (TypeError, ValueError):
                    trend_v = None
            name = _s(t.get("name")) or tid
            # 只对"数据足够充分(stab 高 OR 有趋势)"的 trait 做 Milestone
            if stab < 0.60 and (trend_v is None or abs(trend_v) < 0.05):
                continue
            if trend_v is not None and trend_v > 0:
                direction = "rising"
            elif trend_v is not None and trend_v < 0:
                direction = "falling"
            else:
                direction = "stable"
            title = f"Stable trait: {name} ({direction}, stab={stab:.2f})"
            summary = (
                f"value_pct={val} evidence_count={evid_cnt} "
                f"trend_30d={trend_v}"
            )
            sources = [EvidenceReference(
                source_type=SOURCE_SELF_MODEL_TRAIT,
                source_id=tid,
                confidence=stab,
            )]
            imp = ImpactMap(personality=True)
            explanation = ExplanationMetadata(
                evidence_count=evid_cnt,
                confidence=stab,
            )
            out.append(ExistenceMilestone(
                timestamp=ts,
                milestone_type=MILESTONE_TRAIT_CHANGE,
                title=title,
                summary=summary,
                sources=sources,
                impact=imp,
                explanation=explanation,
                affected_keys=[tid],
                adapter_name=self.adapter_name + ":stable_traits",
            ))
        return out


# ============================================================
# 3. Personality Evolution Adapter (D.6.1.1-c)
# ============================================================

@dataclass
class EvolutionMilestoneAdapter:
    """
    PersonalityEvolutionRecord / evolution timeline → ExistenceMilestone

    覆盖两类输入:
      A) List[PersonalityEvolutionRecord.to_dict()]
         - 来自 Personality Evolution Engine (personality_evolution_schema.py)
         - 字段:record_id / timestamp / proposal_id / status (applied/blocked/failed)
               / trait_states_before / trait_states_after / evolution_record

      B) SelfModelProvider.get_evolution_timeline() 输出 envelope {"items":[...]}
         - 每个 item = {"source": str, "timestamp": str, "kind": str, ...}
         - 复用 audit 级别的 evolution 聚合
    """

    adapter_name: str = "evolution_milestone_adapter_v1"

    # --------------------------------------------------------
    # 公共 API:自动识别信封类型
    # --------------------------------------------------------
    def convert_mixed(self, payload: Any) -> List[ExistenceMilestone]:
        """
        自动分派:
          - dict 且含 "items" → 调用 convert_evolution_envelope(B)
          - list → 调用 convert_evolution_records(A)
        """
        if isinstance(payload, dict):
            return self.convert_evolution_envelope(payload)
        if isinstance(payload, list):
            return self.convert_evolution_records(payload)
        return []

    # --------------------------------------------------------
    # 3.A PersonalityEvolutionRecord list
    # --------------------------------------------------------
    def convert_evolution_records(
        self, records: Iterable[Dict[str, Any]]
    ) -> List[ExistenceMilestone]:
        out: List[ExistenceMilestone] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            rid = _s(rec.get("record_id"))
            if not rid:
                continue
            status = _s(rec.get("status")) or "unknown"
            if status not in {"applied", "blocked", "failed"}:
                continue
            ts = _norm_ts(rec.get("timestamp"))
            proposal_id = _s(rec.get("proposal_id"))
            actor = _s(rec.get("actor")) or "runtime"
            # Diff trait_states_before vs after
            before = rec.get("trait_states_before") or {}
            after = rec.get("trait_states_after") or {}
            changes: List[str] = []
            akeys: List[str] = []
            if isinstance(before, dict) and isinstance(after, dict):
                for k in sorted(set(list(before.keys()) + list(after.keys()))):
                    bv = before.get(k)
                    av = after.get(k)
                    if str(bv) != str(av):
                        changes.append(f"{k}:{bv!r}→{av!r}")
                        if _s(k) not in akeys:
                            akeys.append(_s(k))
            title = f"Personality evolution ({status}): {len(changes)} trait changes"
            if not changes:
                summary = f"record_id={rid} proposal={proposal_id} actor={actor}"
            else:
                summary = "; ".join(changes[:8])
                if len(changes) > 8:
                    summary += f" (+{len(changes) - 8} more)"
            sources: List[EvidenceReference] = [
                EvidenceReference(
                    source_type=SOURCE_PERSONALITY_EVOLUTION,
                    source_id=rid,
                    note=f"status={status}",
                )
            ]
            if proposal_id:
                sources.append(EvidenceReference(
                    source_type=SOURCE_GROWTH_PROPOSAL,
                    source_id=proposal_id,
                    note="triggered_by",
                ))
            imp = ImpactMap(
                personality=bool(changes),
                identity=status in {"blocked", "failed"},  # 被阻止=identity保护(锚点)
            )
            ev_rec = rec.get("evolution_record")
            conf = 0.7 if status == "applied" else 0.4
            if isinstance(ev_rec, dict):
                conf = _f(ev_rec.get("confidence"), conf)
            explanation = ExplanationMetadata(
                evidence_count=len(sources),
                confidence=conf,
            )
            out.append(ExistenceMilestone(
                timestamp=ts,
                milestone_type=MILESTONE_TRAIT_CHANGE,
                title=title,
                summary=summary,
                sources=sources,
                impact=imp,
                explanation=explanation,
                affected_keys=akeys[:50],
                adapter_name=self.adapter_name + ":records",
            ))
        return out

    # --------------------------------------------------------
    # 3.B Evolution Timeline envelope
    # --------------------------------------------------------
    def convert_evolution_envelope(
        self, envelope: Dict[str, Any]
    ) -> List[ExistenceMilestone]:
        """
        envelope.items 通用格式 (来自 audit.build_evolution_timeline):
          {
            "source": "history"/"beliefs"/"reflections"/"audit"/...,
            "timestamp": ISO,
            "kind": str,
            "title": str,
            "summary": str,
            "id": str,
            "confidence": float,
            ...
          }
        """
        out: List[ExistenceMilestone] = []
        if not isinstance(envelope, dict):
            return out
        items = envelope.get("items") or []
        if not isinstance(items, list):
            return out
        for it in items:
            if not isinstance(it, dict):
                continue
            iid = _s(it.get("id")) or _s(it.get("event_id"))
            if not iid:
                continue
            ts = _norm_ts(it.get("timestamp"))
            source = _s(it.get("source")) or "unknown"
            kind = _s(it.get("kind")) or "event"
            conf = _f(it.get("confidence"))
            # source → milestone_type
            if source in {"beliefs", "belief"}:
                mtype = MILESTONE_BELIEF
                stype = SOURCE_SELF_MODEL_BELIEF
            elif source in {"history", "self_history"}:
                mtype = MILESTONE_TRAIT_CHANGE  # 默认
                stype = SOURCE_SELF_MODEL_HISTORY
            elif source in {"reflections", "reflection"}:
                mtype = MILESTONE_REFLECTION
                stype = SOURCE_REFLECTION_INSIGHT
            elif source in {"growth", "proposal", "pcr"}:
                mtype = MILESTONE_GROWTH
                stype = SOURCE_GROWTH_PROPOSAL
            elif source in {"evolution", "personality_evolution"}:
                mtype = MILESTONE_TRAIT_CHANGE
                stype = SOURCE_PERSONALITY_EVOLUTION
            elif source in {"audit"}:
                mtype = MILESTONE_IDENTITY
                stype = SOURCE_PERSONALITY_EVOLUTION
            else:
                # 未知来源:拒绝产出(防伪造)
                continue
            # kind 细化映射:
            if kind in {"trait_change", "trait_update"} and mtype != MILESTONE_TRAIT_CHANGE:
                mtype = MILESTONE_TRAIT_CHANGE
            title = _s(it.get("title")) or f"{mtype}:{kind}"
            summary = _s(it.get("summary")) or f"source={source} kind={kind}"
            sources = [EvidenceReference(
                source_type=stype,
                source_id=iid,
                confidence=conf,
                note=f"source={source} kind={kind}",
            )]
            imp = ImpactMap(
                personality=mtype == MILESTONE_TRAIT_CHANGE,
                belief=mtype == MILESTONE_BELIEF,
                identity=mtype == MILESTONE_IDENTITY,
            )
            explanation = ExplanationMetadata(
                evidence_count=1,
                confidence=conf,
            )
            out.append(ExistenceMilestone(
                timestamp=ts,
                milestone_type=mtype,
                title=title[:120],
                summary=summary[:240],
                sources=sources,
                impact=imp,
                explanation=explanation,
                adapter_name=self.adapter_name + ":envelope",
            ))
        return out


# ============================================================
# Module-level shortcuts (便于调用)
# ============================================================

def proposals_to_milestones(proposals: Iterable[Dict[str, Any]]) -> List[ExistenceMilestone]:
    return ProposalMilestoneAdapter().convert_many(proposals)


def selfmodel_to_milestones(
    *,
    beliefs_envelope: Optional[Dict[str, Any]] = None,
    history_envelope: Optional[Dict[str, Any]] = None,
    reflections_envelope: Optional[Dict[str, Any]] = None,
    stable_traits_envelope: Optional[Dict[str, Any]] = None,
) -> List[ExistenceMilestone]:
    return SelfModelMilestoneAdapter().convert_all(
        beliefs_envelope=beliefs_envelope,
        history_envelope=history_envelope,
        reflections_envelope=reflections_envelope,
        stable_traits_envelope=stable_traits_envelope,
    )


def evolution_to_milestones(payload: Any) -> List[ExistenceMilestone]:
    return EvolutionMilestoneAdapter().convert_mixed(payload)


__all__ = [
    "ProposalMilestoneAdapter",
    "SelfModelMilestoneAdapter",
    "EvolutionMilestoneAdapter",
    "proposals_to_milestones",
    "selfmodel_to_milestones",
    "evolution_to_milestones",
]
