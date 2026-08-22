# -*- coding: utf-8 -*-
"""
src/personality/self_narrative_assembler.py

v1.2 Self Understanding — SelfNarrativeAssembler（Phase 1 最小闭环）

职责（宪法约束）：
- 把已有真实数据组装为三类叙事：Recent / Growth / Identity。
- 只读取、只组合：不推理人格、不创造经历、不修改任何状态。

硬性约束：
1. 禁止调用任何大模型（不引入模型客户端、提示词构建器或任何模型请求）。
2. Narrative 不拥有状态修改权限：本模块没有任何写入口，
   输出只供渲染/持久化为叙事快照，禁止回写 personality/self_model/
   emotion/relationship/growth，禁止回流治理链（Narrative 不是事件）。
3. IdentityCore 永远最高优先级：叙事不得生成身份断言
   （禁止「羽依现在是一个外向的人」这类结论句），只能使用
   「记录显示……」式观察性表述。
4. 真实性规则：空数据返回 None（不编造默认故事）；每条叙事带
   source_reference / timestamp / confidence / origin 四元元数据；
   成长叙事未提供审计证据时不得声称「已审批」，只能标记「来源不足」。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.personality.self_narrative_history import NarrativeSnapshot

# 叙事类型常量（与任务书约定一致）
NARRATIVE_TYPE_RECENT = "recent"
NARRATIVE_TYPE_GROWTH = "growth"
NARRATIVE_TYPE_IDENTITY = "identity"

# 组装产物的 origin 标记：v1.2 只有模板组装；未来若引入模型润色，
# 必须改用 "generated" 并在 metadata 中保留 source_reference（宪法约束）。
ORIGIN_ASSEMBLED = "assembled"

# 成长叙事在缺少审计证据时的降级标记与置信度折扣
_MISSING_AUDIT_MARKER = "（治理审批记录缺失：来源不足）"
_AUDIT_MISSING_CONFIDENCE_FACTOR = 0.7

_SNAPSHOT_EMPTY_TEXT = "正在积累中"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(text: str, limit: int = 80) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _min_confidence(values: List[float], default: float = 0.5) -> float:
    vals = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0.0 <= f <= 1.0:
            vals.append(f)
    return min(vals) if vals else default


@dataclass
class NarrativeItem:
    """单条叙事（理解层产物，非状态）。"""

    narrative_type: str  # recent / growth / identity
    content: str
    source_reference: List[str]
    timestamp: str
    confidence: float
    origin: str = ORIGIN_ASSEMBLED
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def narrative_id(self) -> str:
        """幂等键：同一组来源引用永远得到相同 id（任务书要求）。"""
        key = "|".join(sorted(self.source_reference))
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "narrative_type": self.narrative_type,
            "content": self.content,
            "source_reference": list(self.source_reference),
            "timestamp": self.timestamp,
            "confidence": float(self.confidence),
            "origin": self.origin,
            "narrative_id": self.narrative_id,
            "metadata": dict(self.metadata),
        }


class SelfNarrativeAssembler:
    """把真实数据组装为三层叙事。纯代码逻辑，无副作用。"""

    def __init__(self, max_recent_items: int = 5) -> None:
        try:
            self.max_recent_items = int(max_recent_items)
        except (TypeError, ValueError):
            self.max_recent_items = 5
        if self.max_recent_items <= 0:
            self.max_recent_items = 5

    # ------------------------------------------------------------
    # Recent Narrative：最近发生了什么
    # ------------------------------------------------------------
    def assemble_recent_narrative(
        self,
        memories: Optional[List[Dict[str, Any]]] = None,
        experiences: Optional[List[Dict[str, Any]]] = None,
        reflections: Optional[List[Any]] = None,
    ) -> Optional[NarrativeItem]:
        lines: List[str] = []
        refs: List[str] = []
        confs: List[float] = []
        budget = self.max_recent_items

        for m in (memories or [])[:budget]:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or "")
            ts = str(m.get("timestamp") or "")
            if not mid and not ts:
                # 真实性守卫: 无任何来源标识的记忆不得进入叙事（宪法 M1）
                continue
            text = _clip(m.get("content") or "")
            if not text:
                continue
            lines.append(f"近期记忆（{ts}）：{text}")
            refs.append(f"memory:{mid}" if mid else f"memory:{ts}")
            confs.append(0.8)

        for e in (experiences or [])[:budget]:
            if not isinstance(e, dict):
                continue
            eid = str(e.get("id") or "")
            ts = str(e.get("timestamp") or "")
            if not eid and not ts:
                # 真实性守卫: 无来源标识的经历不得进入叙事
                continue
            text = _clip(e.get("content") or e.get("summary") or e.get("text") or "")
            if not text:
                continue
            lines.append(f"近期经历（{ts}）：{text}")
            refs.append(f"experience:{eid}" if eid else f"experience:{ts}")
            confs.append(0.7)

        for r in (reflections or [])[:budget]:
            rid = str(getattr(r, "reflection_id", "") or "")
            if isinstance(r, dict):
                rid = str(r.get("reflection_id") or "")
            if not rid:
                # 真实性守卫: 无 reflection_id 的反思不得进入叙事
                continue
            insights = getattr(r, "insights", None)
            if isinstance(r, dict):
                insights = r.get("insights")
            if not insights:
                continue
            for ins in list(insights)[:2]:
                desc = _clip(getattr(ins, "description", "") if not isinstance(ins, str) else ins)
                if not desc:
                    continue
                lines.append(f"近期反思：{desc}")
                refs.append(f"reflection:{rid}")
            confs.append(
                float(getattr(r, "confidence", 0.5) or 0.5)
                if not isinstance(r, dict)
                else float(r.get("confidence", 0.5) or 0.5)
            )

        if not lines:
            return None
        return NarrativeItem(
            narrative_type=NARRATIVE_TYPE_RECENT,
            content="\n".join(lines),
            source_reference=sorted(set(refs)),
            timestamp=_utc_now_iso(),
            confidence=round(_min_confidence(confs), 4),
        )

    # ------------------------------------------------------------
    # Growth Narrative：为什么某个倾向发生变化
    # ------------------------------------------------------------
    def assemble_growth_narrative(
        self,
        growth_records: Optional[List[Dict[str, Any]]] = None,
        audit_entries: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[NarrativeItem]:
        lines: List[str] = []
        refs: List[str] = []
        confs: List[float] = []

        for rec in (growth_records or []):
            if not isinstance(rec, dict):
                continue
            rid = str(rec.get("record_id") or "")
            ts = str(rec.get("timestamp") or "")
            if not rid and not ts:
                # 真实性守卫: 无来源标识的成长记录不得进入叙事
                continue
            conf = float(rec.get("confidence", 0.5) or 0.5)
            changes = rec.get("changes") or {}
            for trait, ch in (changes.items() if isinstance(changes, dict) else []):
                ch = ch if isinstance(ch, dict) else {}
                before = ch.get("before")
                after = ch.get("after")
                reason = str(ch.get("reason") or rec.get("meaning") or "").strip()
                if before is None or after is None:
                    continue
                try:
                    before_s = f"{float(before):.3f}"
                    after_s = f"{float(after):.3f}"
                except (TypeError, ValueError):
                    before_s, after_s = str(before), str(after)
                seg = f"成长评估（{ts}）：{trait} 维度由 {before_s} 调整为 {after_s}"
                if reason:
                    seg += f"（理由：{_clip(reason, 60)}）"
                seg += f" [record:{rid}]"
                lines.append(seg)
                refs.append(f"growth:{rid}" if rid else f"growth:{ts}")
                confs.append(conf)

        if not lines:
            return None

        has_audit = bool(audit_entries)
        for a in (audit_entries or []):
            if not isinstance(a, dict):
                continue
            aid = str(a.get("id") or "")
            pid = str(a.get("proposal_id") or "")
            if not aid and not pid:
                # 真实性守卫: 无来源标识的审计条目不得进入叙事
                continue
            comp = str(a.get("component") or "")
            approval = str(a.get("approval_id") or "")
            ts = str(a.get("timestamp") or "")
            seg = f"治理应用（{ts}）：提案 {pid} 经审批（{approval}）应用于 {comp}"
            lines.append(seg)
            refs.append(f"audit:{aid}" if aid else f"audit:{pid}")
            confs.append(1.0)

        if not has_audit:
            lines.append(_MISSING_AUDIT_MARKER)
            refs.append("audit:missing")

        confidence = _min_confidence(confs)
        if not has_audit:
            confidence = round(confidence * _AUDIT_MISSING_CONFIDENCE_FACTOR, 4)

        return NarrativeItem(
            narrative_type=NARRATIVE_TYPE_GROWTH,
            content="\n".join(lines),
            source_reference=sorted(set(refs)),
            timestamp=_utc_now_iso(),
            confidence=confidence,
        )

    # ------------------------------------------------------------
    # Identity Narrative：这些经历如何影响现在的我（观察性表述）
    # ------------------------------------------------------------
    def assemble_identity_narrative(
        self,
        growth_narratives: Optional[List[Dict[str, Any]]] = None,
        recent: Optional[NarrativeItem] = None,
        growth: Optional[NarrativeItem] = None,
    ) -> Optional[NarrativeItem]:
        lines: List[str] = []
        refs: List[str] = []
        confs: List[float] = []

        for n in (growth_narratives or [])[:3]:
            if not isinstance(n, dict):
                continue
            rid = str(n.get("record_id") or "")
            if not rid:
                # 真实性守卫: 无 record_id 的治理叙事条目不得进入叙事
                continue
            text = _clip(n.get("text") or n.get("change") or n.get("content") or "", 100)
            if not text:
                continue
            lines.append(f"长期自我理解（治理记录 {rid}）：{text}")
            refs.append(f"growth_narrative:{rid}")
            confs.append(1.0)

        if recent is not None and recent.content:
            lines.append(f"记录显示，近期关注点：{_clip(recent.content, 60)}")
            refs.extend(recent.source_reference)
            confs.append(recent.confidence)
        if growth is not None and growth.content:
            lines.append(f"记录显示，近期成长脉络：{_clip(growth.content, 80)}")
            refs.extend(growth.source_reference)
            confs.append(growth.confidence)

        if not lines:
            return None
        return NarrativeItem(
            narrative_type=NARRATIVE_TYPE_IDENTITY,
            content="\n".join(lines),
            source_reference=sorted(set(refs)),
            timestamp=_utc_now_iso(),
            confidence=round(_min_confidence(confs), 4),
        )

    # ------------------------------------------------------------
    # 快照组装：三层叙事 → NarrativeSnapshot（供 SelfNarrativeHistory 持久化）
    # ------------------------------------------------------------
    def assemble_snapshot(
        self,
        *,
        memories: Optional[List[Dict[str, Any]]] = None,
        experiences: Optional[List[Dict[str, Any]]] = None,
        reflections: Optional[List[Any]] = None,
        growth_records: Optional[List[Dict[str, Any]]] = None,
        audit_entries: Optional[List[Dict[str, Any]]] = None,
        growth_narratives: Optional[List[Dict[str, Any]]] = None,
    ) -> NarrativeSnapshot:
        recent = self.assemble_recent_narrative(memories, experiences, reflections)
        growth = self.assemble_growth_narrative(growth_records, audit_entries)
        identity = self.assemble_identity_narrative(growth_narratives, recent, growth)

        # 分层标记（渲染层按 [Recent]/[Growth]/[Identity] 切分三节；
        # 仍是一个字符串字段, 不新增 schema）
        parts: List[str] = []
        if recent is not None and recent.content:
            parts.append(f"[Recent]\n{recent.content}")
        if growth is not None and growth.content:
            parts.append(f"[Growth]\n{growth.content}")
        if identity is not None and identity.content:
            parts.append(f"[Identity]\n{identity.content}")
        text = "\n\n".join(parts) if parts else _SNAPSHOT_EMPTY_TEXT

        changed_traits: Dict[str, float] = {}
        major_changes: List[str] = []
        for rec in (growth_records or []):
            if not isinstance(rec, dict):
                continue
            for trait, ch in ((rec.get("changes") or {}).items()):
                ch = ch if isinstance(ch, dict) else {}
                if ch.get("after") is None:
                    continue
                try:
                    changed_traits[str(trait)] = float(ch["after"])
                except (TypeError, ValueError):
                    continue
        if growth is not None:
            major_changes = [line for line in growth.content.splitlines()]

        return NarrativeSnapshot(
            major_changes=major_changes,
            changed_traits=changed_traits,
            narrative_text=text,
        )


__all__ = [
    "SelfNarrativeAssembler",
    "NarrativeItem",
    "NARRATIVE_TYPE_RECENT",
    "NARRATIVE_TYPE_GROWTH",
    "NARRATIVE_TYPE_IDENTITY",
    "ORIGIN_ASSEMBLED",
]
