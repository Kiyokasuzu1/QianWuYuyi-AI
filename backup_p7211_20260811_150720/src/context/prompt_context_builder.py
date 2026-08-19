"""
Phase 4.0 — R2.6.4-C.2 PromptContextBuilder（Context Assembly Layer）

唯一职责：
    read → merge → format → return

红线（Schema 已经定义；本模块只装配，绝不触发）：
    - 不 modify 任何状态：Personality / Memory / Emotion / Relationship 只读
    - 不产生 GrowthCandidate / GrowthProposal / Reflection / Decision
    - 不 import LLM 模块 / 不拼接 Prompt 文本（属于 Response Engine）
    - self_context 只接受已经成型的 SelfContext dict（通常由 SelfContextBuilder 产出）
      → 本 Builder **不 import SelfContextBuilder**，避免跨层循环依赖

输出：
    通过 validate_prompt_context_shape(...) 的合法 PromptContext。
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.context.self_context_schema import validate_self_context_shape
from src.context.prompt_context_schema import (
    ASSEMBLY_MODE_WHITELIST,
    create_empty_prompt_context,
    validate_prompt_context_shape,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# 膨胀保护：防止任意上下文把 PromptContext 撑爆
# ─────────────────────────────────────────────────────────
DEFAULT_MAX_MEMORIES: int = 10
DEFAULT_MAX_RELATIONSHIP_TRAITS: int = 6
DEFAULT_MAX_STRING_LENGTH: int = 280
DEFAULT_MAX_ANCHOR_MARKERS: int = 10


def _clip_string(value: Optional[str], limit: int = DEFAULT_MAX_STRING_LENGTH) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if len(value) <= limit:
        return value
    return value[:limit]


def _clip_list_of_str(values: Any, limit_count: int, limit_len: int = DEFAULT_MAX_STRING_LENGTH) -> List[str]:
    if not isinstance(values, (list, tuple)):
        return []
    clipped: List[str] = []
    for item in values:
        if isinstance(item, str):
            clipped.append(item if len(item) <= limit_len else item[:limit_len])
        if len(clipped) >= limit_count:
            break
    return clipped


class PromptContextBuilder:
    """只读装配层。

    用法：
        prompt_ctx = PromptContextBuilder().build(
            system_identity={...},                 # 可选，默认 "羽依 / companion_ai"
            self_context=self_context_from_scb,    # 推荐：由 SelfContextBuilder.build() 返回
            user_info={...},                       # user_id/display_name/...（可选）
            context_memories=[...],                # 记忆文本列表（可选）
            emotion_state={...}/EmotionState,      # 情绪快照（可选）
            relationship_snapshot={...}/Snapshot,  # 关系快照（可选）
            task_info={...},                       # turn/session/topic/scenario（可选）
            assembly_mode="standard",
        )
    """

    def __init__(
        self,
        *,
        max_memories: int = DEFAULT_MAX_MEMORIES,
        max_relationship_traits: int = DEFAULT_MAX_RELATIONSHIP_TRAITS,
        max_string_length: int = DEFAULT_MAX_STRING_LENGTH,
        max_anchor_markers: int = DEFAULT_MAX_ANCHOR_MARKERS,
    ) -> None:
        if not isinstance(max_memories, int) or max_memories < 0:
            raise ValueError(f"max_memories 必须 >=0 int，实际 {max_memories}")
        if not isinstance(max_relationship_traits, int) or max_relationship_traits < 0:
            raise ValueError(f"max_relationship_traits 必须 >=0 int，实际 {max_relationship_traits}")
        if not isinstance(max_string_length, int) or max_string_length < 16:
            raise ValueError(f"max_string_length 必须 >=16 int，实际 {max_string_length}")
        if not isinstance(max_anchor_markers, int) or max_anchor_markers < 0:
            raise ValueError(f"max_anchor_markers 必须 >=0 int，实际 {max_anchor_markers}")

        self._max_memories = max_memories
        self._max_relationship_traits = max_relationship_traits
        self._max_string_length = max_string_length
        self._max_anchor_markers = max_anchor_markers
        self._version_counter: int = 0

    # ── public ────────────────────────────────────────────
    def build(
        self,
        *,
        system_identity: Optional[Mapping[str, Any]] = None,
        self_context: Optional[Mapping[str, Any]] = None,
        user_info: Optional[Mapping[str, Any]] = None,
        context_memories: Optional[Iterable[str]] = None,
        memory_scope: Optional[str] = None,
        emotion_state: Any = None,
        relationship_snapshot: Any = None,
        task_info: Optional[Mapping[str, Any]] = None,
        assembly_mode: str = "standard",
    ) -> Dict[str, Any]:
        # version 占坑（成功/异常都计数一次，避免跳号）
        self._version_counter += 1
        assigned_version = self._version_counter
        try:
            return self._build_safe(
                assigned_version=assigned_version,
                system_identity=system_identity,
                self_context=self_context,
                user_info=user_info,
                context_memories=context_memories,
                memory_scope=memory_scope,
                emotion_state=emotion_state,
                relationship_snapshot=relationship_snapshot,
                task_info=task_info,
                assembly_mode=assembly_mode,
            )
        except Exception as exc:  # 100% 异常隔离
            logger.exception("[prompt_context_builder_crash] error=%s", exc)
            return self._build_degraded(exc, assigned_version=assigned_version)

    # ── safe build：read / merge / format ──────────────────
    def _build_safe(
        self,
        *,
        assigned_version: int,
        system_identity: Optional[Mapping[str, Any]],
        self_context: Optional[Mapping[str, Any]],
        user_info: Optional[Mapping[str, Any]],
        context_memories: Optional[Iterable[str]],
        memory_scope: Optional[str],
        emotion_state: Any,
        relationship_snapshot: Any,
        task_info: Optional[Mapping[str, Any]],
        assembly_mode: str,
    ) -> Dict[str, Any]:
        # 1) system_identity：读取参数或默认；clip
        si = self._read_system_identity(system_identity)

        # 2) self_context：必须合法；若未传 → 使用 create_empty_self_context 版（v=1）
        sc_snapshot: Dict[str, Any]
        if self_context is None:
            from src.context.self_context_schema import create_empty_self_context
            sc_snapshot = create_empty_self_context(version=1)
        else:
            # 校验（必须通过 SelfContext 形状），拷贝后放入（避免调用方后续 mutate 影响我们的输出）
            validate_self_context_shape(dict(self_context))
            sc_snapshot = copy.deepcopy(dict(self_context))

        # 3) user / memory / emotion / relationship / task
        uc = self._read_user_context(user_info)
        mc = self._read_memory_context(context_memories, memory_scope)
        ec, emotion_source = self._read_emotion_context(emotion_state)
        rc = self._read_relationship_context(relationship_snapshot)
        tc = self._read_task_context(task_info)

        # 4) assembly_mode：fallback 到 standard（非法值不报错，按默认值处理并在 trace 标记）
        actual_assembly_mode = assembly_mode if assembly_mode in ASSEMBLY_MODE_WHITELIST else "standard"

        # 5) context_trace：审计轨迹（事实性取值，不做推理）
        trace = {
            "self_context_version": sc_snapshot["version"],
            "self_context_continuity": sc_snapshot["continuity_status"],
            "self_context_injection_mode": sc_snapshot["injection_policy"]["mode"],
            "memory_count": mc["memory_count"],
            "memory_scope": _clip_string(mc["recall_scope"], self._max_string_length),
            "emotion_source": emotion_source,
            "relationship_trust_level": rc["trust_level"],
            "build_version": assigned_version,
            "assembly_mode": actual_assembly_mode,
        }

        ctx: Dict[str, Any] = {
            "system_identity": si,
            "self_context": sc_snapshot,
            "user_context": uc,
            "memory_context": mc,
            "emotion_context": ec,
            "relationship_context": rc,
            "task_context": tc,
            "context_trace": trace,
            "version": assigned_version,
        }
        validate_prompt_context_shape(ctx)
        return ctx

    # ── 读取子片段：统一 clip / fallback / 无推理 ──────────
    def _read_system_identity(self, identity: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        default = {
            "name": "羽依",
            "system_role": "companion_ai",
            "anchor_markers": [],
            "build_tag": "phase40-r264c2",
        }
        if not identity:
            return default
        name = _clip_string(identity.get("name") or "羽依", self._max_string_length) or "羽依"
        role = identity.get("system_role") if identity.get("system_role") in ("companion_ai", "assistant_ai") else "companion_ai"
        anchors = _clip_list_of_str(identity.get("anchor_markers") or [], self._max_anchor_markers, self._max_string_length)
        build_tag = _clip_string(identity.get("build_tag") or "phase40-r264c2", self._max_string_length) or "phase40-r264c2"
        return {
            "name": name,
            "system_role": role,
            "anchor_markers": anchors,
            "build_tag": build_tag,
        }

    def _read_user_context(self, info: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        if not info:
            return {
                "user_id": None,
                "display_name": None,
                "conversation_role": "user",
                "relationship_tier": "unknown",
                "preferred_name": None,
            }
        user_id = _clip_string(info.get("user_id"), self._max_string_length)
        display_name = _clip_string(info.get("display_name"), self._max_string_length)
        role = info.get("conversation_role") if info.get("conversation_role") in ("user", "system", "observer") else "user"
        tier = info.get("relationship_tier") if info.get("relationship_tier") in ("stranger", "acquaintance", "friend", "confidant", "unknown") else "unknown"
        preferred = _clip_string(info.get("preferred_name"), self._max_string_length)
        return {
            "user_id": user_id,
            "display_name": display_name,
            "conversation_role": role,
            "relationship_tier": tier,
            "preferred_name": preferred,
        }

    def _read_memory_context(self, memories: Optional[Iterable[str]], scope: Optional[str]) -> Dict[str, Any]:
        # memory_count 是 clip 后实际塞入的条数（一致，便于 trace）
        clipped = _clip_list_of_str(list(memories or []), self._max_memories, self._max_string_length)
        scoped = _clip_string(scope, self._max_string_length)
        return {
            "context_memories": clipped,
            "memory_count": len(clipped),
            "recall_scope": scoped,
        }

    def _read_emotion_context(self, state: Any) -> Tuple[Dict[str, Any], str]:
        # 接受 dict 或带属性的 snapshot；一律只读
        if isinstance(state, Mapping):
            mood = state.get("emotion_mood")
            intensity = state.get("intensity")
            note = state.get("context_note")
            source_tag = "emotion_state:dict"
        elif state is None:
            mood, intensity, note, source_tag = None, None, None, "emotion_state:default_neutral"
        else:
            mood = getattr(state, "emotion_mood", None)
            intensity = getattr(state, "intensity", None)
            note = getattr(state, "context_note", None)
            source_tag = "emotion_state:snapshot"

        mood_whitelist = (
            "neutral", "calm", "happy", "excited", "thoughtful",
            "sad", "worried", "curious", "warm",
        )
        final_mood = mood if mood in mood_whitelist else "neutral"
        # intensity 范围：[0,1]
        try:
            intensity_val = float(intensity) if intensity is not None else 0.5
        except (TypeError, ValueError):
            intensity_val = 0.5
        if not (0.0 <= intensity_val <= 1.0):
            intensity_val = 0.5
        final_note = _clip_string(note, self._max_string_length)
        return (
            {
                "emotion_mood": final_mood,
                "intensity": round(intensity_val, 3),
                "context_note": final_note,
            },
            source_tag,
        )

    def _read_relationship_context(self, snap: Any) -> Dict[str, Any]:
        if isinstance(snap, Mapping):
            closeness = snap.get("closeness_score")
            dyn = snap.get("dynamic_traits")
            trust = snap.get("trust_level")
        elif snap is None:
            closeness, dyn, trust = None, None, None
        else:
            closeness = getattr(snap, "closeness_score", None)
            dyn = getattr(snap, "dynamic_traits", None)
            trust = getattr(snap, "trust_level", None)
        try:
            c_val = float(closeness) if closeness is not None else 0.0
        except (TypeError, ValueError):
            c_val = 0.0
        if not (0.0 <= c_val <= 1.0):
            c_val = 0.0
        traits = _clip_list_of_str(dyn, self._max_relationship_traits, self._max_string_length)
        trust_whitelist = ("low", "medium", "high", "unknown")
        trust_lvl = trust if trust in trust_whitelist else "unknown"
        return {
            "closeness_score": round(c_val, 3),
            "dynamic_traits": traits,
            "trust_level": trust_lvl,
        }

    def _read_task_context(self, info: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        if not info:
            return {
                "turn_number": 0,
                "session_id": None,
                "current_topic": None,
                "scenario": "unknown",
            }
        try:
            turn = int(info.get("turn_number") or 0)
        except (TypeError, ValueError):
            turn = 0
        if turn < 0:
            turn = 0
        session = _clip_string(info.get("session_id"), self._max_string_length)
        topic = _clip_string(info.get("current_topic"), self._max_string_length)
        scenario_whitelist = ("casual_chat", "question_answer", "creative_collaboration", "emotional_support", "planning", "unknown")
        scenario = info.get("scenario") if info.get("scenario") in scenario_whitelist else "unknown"
        return {
            "turn_number": turn,
            "session_id": session,
            "current_topic": topic,
            "scenario": scenario,
        }

    # ── degraded：shape 永远合法，trace 标记 degraded ─────
    def _build_degraded(self, exc: Exception, *, assigned_version: int) -> Dict[str, Any]:
        marker = f"prompt_context_builder_degraded: {type(exc).__name__}"
        ctx = create_empty_prompt_context(version=assigned_version, assembly_mode="degraded")
        # 标记：system_identity.build_tag 不变；trace 写 marker 到 emotion_source（合法，要求非空 str）
        ctx["context_trace"]["emotion_source"] = marker
        # 再标记一个 memory_scope marker（不影响连续性，也不写 personality）
        ctx["memory_context"]["recall_scope"] = marker[: self._max_string_length]
        validate_prompt_context_shape(ctx)
        return ctx
