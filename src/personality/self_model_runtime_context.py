"""
SelfModel Runtime Context (Phase 6.2)

将 Phase 6.1 的 SelfBelief / SelfHistory / SelfReflection 转换为
Prompt 可消费的运行时上下文。

职责：
- 聚合 SelfBeliefStore + SelfHistory + SelfReflectionStore
- 过滤低 confidence 内容
- 限制返回数量（避免 prompt 膨胀）
- 生成结构化 dict + 渲染 prompt 文本

约束：
- 不修改 Phase 6.1 的 SelfBelief / SelfHistory / SelfReflection 任何字段
- 不直接调用 LLM
- 所有错误隔离为降级返回空 dict
- 仅做读取，不写入任何 store
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Union

from src.personality.self_belief import SelfBelief, SelfBeliefStore
from src.personality.self_history import SelfHistory, SelfHistoryEvent, SelfHistoryEventType
from src.personality.self_reflection import SelfReflectionNote, SelfReflectionStore

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 阈值默认值
# ============================================================

DEFAULT_MIN_BELIEF_CONFIDENCE: float = 0.4
DEFAULT_MIN_REFLECTION_CONFIDENCE: float = 0.4
DEFAULT_MAX_BELIEFS: int = 5
DEFAULT_MAX_HISTORY: int = 3
DEFAULT_MAX_REFLECTIONS: int = 3
DEFAULT_MAX_PROMPT_CHARS: int = 2400  # 整个 runtime context 文本上限

# Identity 允许的最小字段
DEFAULT_IDENTITY_FIELDS = (
    "identity_id",
    "identity_name",
    "version",
)


@dataclass
class SelfModelRuntimeContext:
    """
    Phase 6.2 SelfModel Runtime Context。

    不修改 self_belief / self_history / self_reflection 内部状态；
    仅消费其内容生成结构化运行时上下文。
    """

    beliefs: Optional[SelfBeliefStore] = None
    history: Optional[SelfHistory] = None
    reflections: Optional[SelfReflectionStore] = None
    identity_provider: Optional[Callable[[], Dict[str, Any]]] = None
    min_belief_confidence: float = DEFAULT_MIN_BELIEF_CONFIDENCE
    min_reflection_confidence: float = DEFAULT_MIN_REFLECTION_CONFIDENCE
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS
    # 可选：belief 内容净化器（返回安全文本）
    content_sanitizer: Optional[Callable[[str], str]] = None

    # ============================================================
    # 注入辅助
    # ============================================================

    def set_stores(
        self,
        beliefs: Optional[SelfBeliefStore] = None,
        history: Optional[SelfHistory] = None,
        reflections: Optional[SelfReflectionStore] = None,
    ) -> None:
        """延迟注入（适配 Adapter 与 Provider 不在同一时机构造的场景）"""
        if beliefs is not None:
            self.beliefs = beliefs
        if history is not None:
            self.history = history
        if reflections is not None:
            self.reflections = reflections

    def bind_identity_provider(self, provider: Callable[[], Dict[str, Any]]) -> None:
        """绑定 identity 提取函数（返回 dict，至少含 identity_id / identity_name）"""
        self.identity_provider = provider

    # ============================================================
    # 公开 API
    # ============================================================

    def build_context(
        self,
        max_beliefs: int = DEFAULT_MAX_BELIEFS,
        max_history: int = DEFAULT_MAX_HISTORY,
        max_reflections: int = DEFAULT_MAX_REFLECTIONS,
    ) -> Dict[str, Any]:
        """
        生成结构化 runtime context。

        Returns:
            {
                "identity": dict,
                "beliefs": List[dict],
                "recent_growth": List[dict],
                "self_reflections": List[dict],
                "continuity_notes": List[str],
            }
        """
        try:
            identity = self._build_identity()
            beliefs = self._build_beliefs(max_beliefs=max_beliefs)
            recent_growth = self._build_recent_growth(max_history=max_history)
            self_reflections = self._build_reflections(max_reflections=max_reflections)
            continuity_notes = self._build_continuity_notes(
                beliefs=beliefs, growth=recent_growth, reflections=self_reflections
            )
            return {
                "identity": identity,
                "beliefs": beliefs,
                "recent_growth": recent_growth,
                "self_reflections": self_reflections,
                "continuity_notes": continuity_notes,
            }
        except Exception as e:
            logger.warning(f"SelfModelRuntimeContext.build_context failed: {e}")
            return {
                "identity": {},
                "beliefs": [],
                "recent_growth": [],
                "self_reflections": [],
                "continuity_notes": [],
                "error": str(e),
            }

    def build_prompt_text(
        self,
        max_beliefs: int = DEFAULT_MAX_BELIEFS,
        max_history: int = DEFAULT_MAX_HISTORY,
        max_reflections: int = DEFAULT_MAX_REFLECTIONS,
    ) -> str:
        """
        渲染为 Prompt 可用的中文文本片段。

        长度受 self.max_prompt_chars 限制。
        """
        ctx = self.build_context(
            max_beliefs=max_beliefs,
            max_history=max_history,
            max_reflections=max_reflections,
        )
        return self._render_prompt_text(ctx)

    def snapshot(self) -> Dict[str, Any]:
        """返回当前 runtime context 的可序列化快照（不含错误标记）"""
        return self.build_context()

    # ============================================================
    # 内部构建：identity
    # ============================================================

    def _build_identity(self) -> Dict[str, Any]:
        if self.identity_provider is None:
            return {}
        try:
            data = self.identity_provider() or {}
            if not isinstance(data, dict):
                return {}
            out: Dict[str, Any] = {}
            for f in DEFAULT_IDENTITY_FIELDS:
                if f in data:
                    out[f] = data[f]
            # 抽取 core_values names
            core_values = data.get("core_values")
            if isinstance(core_values, list):
                names = []
                for cv in core_values:
                    if isinstance(cv, dict):
                        n = cv.get("name") or cv.get("value_id")
                        if n:
                            names.append(str(n))
                    else:
                        n = getattr(cv, "name", None) or getattr(cv, "value_id", None)
                        if n:
                            names.append(str(n))
                if names:
                    out["core_values"] = names
            return out
        except Exception as e:
            logger.warning(f"_build_identity failed: {e}")
            return {}

    # ============================================================
    # 内部构建：beliefs
    # ============================================================

    def _build_beliefs(self, max_beliefs: int) -> List[Dict[str, Any]]:
        if self.beliefs is None or self.beliefs.count() == 0:
            return []
        candidates = self.beliefs.query(
            min_confidence=self.min_belief_confidence, limit=max(50, max_beliefs * 5)
        )
        # 按 confidence 倒序，再按 last_confirmed 倒序
        candidates.sort(
            key=lambda b: (float(getattr(b, "confidence", 0.0)), getattr(b, "last_confirmed", "")),
            reverse=True,
        )
        out: List[Dict[str, Any]] = []
        for b in candidates[:max_beliefs]:
            try:
                content = self._sanitize(getattr(b, "content", ""))
                if not content:
                    continue
                out.append({
                    "belief_id": getattr(b, "belief_id", ""),
                    "domain": getattr(b, "domain", "value"),
                    "content": content,
                    "confidence": round(float(getattr(b, "confidence", 0.0)), 4),
                    "version": int(getattr(b, "version", 1)),
                    "evidence_count": int(getattr(b, "evidence_count", 1)),
                    "last_confirmed": getattr(b, "last_confirmed", ""),
                })
            except Exception:
                continue
        return out

    # ============================================================
    # 内部构建：recent_growth
    # ============================================================

    def _build_recent_growth(self, max_history: int) -> List[Dict[str, Any]]:
        if self.history is None or self.history.count() == 0:
            return []
        try:
            events = self.history.query(limit=max(20, max_history * 4))
        except Exception:
            return []
        # 优先 PCR_APPLIED / GROWTH_RECORD_APPLIED
        priority_types = {
            SelfHistoryEventType.PCR_APPLIED,
            SelfHistoryEventType.GROWTH_RECORD_APPLIED,
            SelfHistoryEventType.REFLECTION_INSIGHT_APPLIED,
            SelfHistoryEventType.IDENTITY_CORE_CHANGED,
        }
        ordered = sorted(
            events,
            key=lambda e: (
                0 if e.event_type in priority_types else 1,
                getattr(e, "timestamp", ""),
            ),
            reverse=True,
        )
        out: List[Dict[str, Any]] = []
        for e in ordered[:max_history]:
            try:
                summary = self._sanitize(getattr(e, "summary", ""))
                if not summary:
                    continue
                affected_traits = dict(getattr(e, "affected_traits", {}) or {})
                out.append({
                    "event_id": getattr(e, "event_id", ""),
                    "event_type": getattr(e, "event_type", ""),
                    "summary": summary,
                    "affected_traits": {k: round(float(v), 5) for k, v in affected_traits.items()},
                    "actor": getattr(e, "actor", ""),
                    "timestamp": getattr(e, "timestamp", ""),
                })
            except Exception:
                continue
        return out

    # ============================================================
    # 内部构建：self_reflections
    # ============================================================

    def _build_reflections(self, max_reflections: int) -> List[Dict[str, Any]]:
        if self.reflections is None or self.reflections.count() == 0:
            return []
        try:
            notes = self.reflections.query(
                min_confidence=self.min_reflection_confidence,
                limit=max(20, max_reflections * 4),
            )
        except Exception:
            return []
        notes.sort(key=lambda n: getattr(n, "timestamp", ""), reverse=True)
        out: List[Dict[str, Any]] = []
        for n in notes[:max_reflections]:
            try:
                content = self._sanitize(getattr(n, "content", ""))
                if not content:
                    continue
                out.append({
                    "note_id": getattr(n, "note_id", ""),
                    "reflection_type": getattr(n, "reflection_type", "identity"),
                    "content": content,
                    "confidence": round(float(getattr(n, "confidence", 0.0)), 4),
                    "related_belief_ids": list(getattr(n, "related_belief_ids", []) or []),
                    "timestamp": getattr(n, "timestamp", ""),
                })
            except Exception:
                continue
        return out

    # ============================================================
    # 内部：continuity notes
    # ============================================================

    def _build_continuity_notes(
        self,
        beliefs: List[Dict[str, Any]],
        growth: List[Dict[str, Any]],
        reflections: List[Dict[str, Any]],
    ) -> List[str]:
        notes: List[str] = []
        try:
            if beliefs:
                top = beliefs[0]
                notes.append(
                    f"你当前最坚信的自我认知（置信度 {top['confidence']:.2f}）：{top['content']}"
                )
            if growth:
                last = growth[0]
                tr = last.get("affected_traits") or {}
                if tr:
                    tr_text = "、".join(f"{k}{v:+.2f}" for k, v in list(tr.items())[:3])
                    notes.append(f"最近一次成长事件：{last['summary']}（{tr_text}）")
                else:
                    notes.append(f"最近一次成长事件：{last['summary']}")
            if reflections:
                last = reflections[0]
                notes.append(f"最近的自我反思：{last['content']}")
        except Exception:
            pass
        return notes

    # ============================================================
    # 渲染 prompt 文本
    # ============================================================

    def _render_prompt_text(self, ctx: Dict[str, Any]) -> str:
        try:
            sections: List[str] = []
            identity = ctx.get("identity") or {}
            if identity:
                name = identity.get("identity_name") or "羽依"
                cv = identity.get("core_values") or []
                if cv:
                    sections.append(f"【身份】我是{name}，我珍视的价值观：{'、'.join(str(x) for x in cv)}")
                else:
                    sections.append(f"【身份】我是{name}")

            beliefs = ctx.get("beliefs") or []
            if beliefs:
                belief_lines = []
                for b in beliefs:
                    belief_lines.append(
                        f"- {b['content']}（置信度 {b['confidence']:.2f}）"
                    )
                sections.append("【当前信念】\n" + "\n".join(belief_lines))

            growth = ctx.get("recent_growth") or []
            if growth:
                growth_lines = []
                for g in growth:
                    tr = g.get("affected_traits") or {}
                    tr_text = ""
                    if tr:
                        tr_text = "（" + "、".join(f"{k}{v:+.2f}" for k, v in list(tr.items())[:3]) + "）"
                    growth_lines.append(f"- {g['summary']}{tr_text}")
                sections.append("【近期成长】\n" + "\n".join(growth_lines))

            reflections = ctx.get("self_reflections") or []
            if reflections:
                refl_lines = [f"- {r['content']}" for r in reflections]
                sections.append("【自我反思】\n" + "\n".join(refl_lines))

            text = "\n\n".join(sections)
            if len(text) > self.max_prompt_chars:
                text = text[: self.max_prompt_chars - 3] + "..."
            return text
        except Exception as e:
            logger.warning(f"_render_prompt_text failed: {e}")
            return ""

    # ============================================================
    # 工具
    # ============================================================

    def _sanitize(self, text: str) -> str:
        if not isinstance(text, str):
            return ""
        s = text.strip()
        if not s:
            return ""
        if self.content_sanitizer is not None:
            try:
                s = self.content_sanitizer(s) or ""
            except Exception:
                pass
        # 简单截断防止单条超长
        if len(s) > 280:
            s = s[:277] + "..."
        return s


__all__ = [
    "SelfModelRuntimeContext",
    "DEFAULT_MIN_BELIEF_CONFIDENCE",
    "DEFAULT_MIN_REFLECTION_CONFIDENCE",
    "DEFAULT_MAX_BELIEFS",
    "DEFAULT_MAX_HISTORY",
    "DEFAULT_MAX_REFLECTIONS",
    "DEFAULT_MAX_PROMPT_CHARS",
]
