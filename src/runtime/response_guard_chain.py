# -*- coding: utf-8 -*-
"""
src/runtime/response_guard_chain.py

Phase 3.8.0 + 3.9.0: Response Guard Chain

Phase 3.8.0: 双 Guard 串联
    LLM 生成的 draft reply
        ↓
    PerceptionGuard.check(facts)         # 真实性:是否包含 INFERENCE 推测
        ↓
    PersonalityGuard.check(reply, prc)   # 人格:是否像羽依
        ↓
    最终回复 (或 fallback)

Phase 3.9.0: 三 Guard 串联(Reality → Perception → Personality)
    LLM 生成的 draft reply
        ↓
    RealityGuard.check(reply, facts, obs_state)   # 现实边界:禁止虚构屏幕/动作/环境
        ↓
    PerceptionGuard.check(facts)                  # 真实性:INFERENCE 降级
        ↓
    PersonalityGuard.check(reply, prc)            # 人格
        ↓
    最终回复 (或 fallback)

向后兼容:
- run(reply, facts, prc) 仍接受原有参数;RealityGuard 可选用 kwargs 注入
- perception_guard / personality_guard 仍可单独传入(自定义)
- 如未显式传入 reality_guard,默认构造一个(开启 Reality 检查)

约束:
- 不 import src.personality.* 任何具体实现
- 不修改 RuntimeContext schema(perception_state 挂在内部属性)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.runtime.perception import (
    Fact,
    PerceptionGuard,
    GuardReport as PerceptionGuardReport,
    RealityGuard,
    RealityGuardReport,
    ObservationState,
)
from src.runtime.personality_context import PersonalityRuntimeContext
from src.runtime.personality_guard import (
    PersonalityGuard,
    PersonalityGuardReport,
)


@dataclass
class ResponseGuardResult:
    """Guard 链路的最终结果。"""

    final_reply: str
    original_reply: str
    perception_report: Optional[PerceptionGuardReport] = None
    personality_report: Optional[PersonalityGuardReport] = None
    reality_report: Optional[RealityGuardReport] = None  # Phase 3.9.0
    blocked_by: Optional[str] = None  # None / "reality" / "perception" / "personality"
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "final_reply": self.final_reply,
            "original_reply": self.original_reply,
            "blocked_by": self.blocked_by,
            "notes": list(self.notes),
            "reality": (
                self.reality_report.to_dict()
                if self.reality_report
                else None
            ),
            "perception": (
                self.perception_report.to_dict()
                if self.perception_report
                else None
            ),
            "personality": (
                self.personality_report.to_dict()
                if self.personality_report
                else None
            ),
        }


class ResponseGuardChain:
    """Phase 3.9.0: Reality + Perception + Personality 三 Guard 串联。"""

    def __init__(
        self,
        perception_guard: Optional[PerceptionGuard] = None,
        personality_guard: Optional[PersonalityGuard] = None,
        reality_guard: Optional[RealityGuard] = None,  # Phase 3.9.0
    ) -> None:
        self.perception_guard = perception_guard or PerceptionGuard()
        self.personality_guard = personality_guard or PersonalityGuard()
        self.reality_guard = reality_guard or RealityGuard()  # Phase 3.9.0

    def run(
        self,
        reply: str,
        facts: List[Fact],
        prc: Optional[PersonalityRuntimeContext] = None,
        obs_state: Optional[ObservationState] = None,  # Phase 3.9.0
    ) -> ResponseGuardResult:
        """依次执行三 Guard,返回最终回复与报告。

        顺序固定:Reality → Perception → Personality(不可逆)。
        """
        notes: List[str] = []
        original = reply

        # ========== Phase 3.9.0: RealityGuard ==========
        # 现实边界:无 Observation 不得虚构屏幕/动作/环境
        reality_report = self.reality_guard.check(reply, facts, obs_state)
        if reality_report.needs_refusal:
            return ResponseGuardResult(
                final_reply=reality_report.suggested_text or reply,
                original_reply=original,
                reality_report=reality_report,
                perception_report=None,
                personality_report=None,
                blocked_by="reality",
                notes=notes + ["reality_refusal"],
            )
        if reality_report.needs_modify and reality_report.suggested_text:
            reply = reality_report.suggested_text
            notes.append("reality_modified_reply")
        elif not reality_report.allowed:
            # 至少标记为"有 violation 但未阻断"
            notes.append(
                "reality_violation:" + ",".join(reality_report.violations)
            )

        # ========== Phase 3.8.x: PerceptionGuard ==========
        # 真实性:INFERENCE 降级
        perception_report = self.perception_guard.check(facts)
        reply_after_perception = self.perception_guard.wrap_reply(facts, reply)
        if reply_after_perception != reply:
            notes.append("perception_modified_reply")
        if perception_report.needs_refusal:
            return ResponseGuardResult(
                final_reply=reply_after_perception,
                original_reply=original,
                reality_report=reality_report,
                perception_report=perception_report,
                personality_report=None,
                blocked_by="perception",
                notes=notes + ["perception_refusal"],
            )

        # ========== Phase 3.8.x: PersonalityGuard ==========
        personality_report = self.personality_guard.check(reply_after_perception, prc)
        if not personality_report.valid:
            return ResponseGuardResult(
                final_reply=reply_after_perception,
                original_reply=original,
                reality_report=reality_report,
                perception_report=perception_report,
                personality_report=personality_report,
                blocked_by="personality",
                notes=notes
                + ["personality_blocked:" + ",".join(personality_report.violations)],
            )

        return ResponseGuardResult(
            final_reply=reply_after_perception,
            original_reply=original,
            reality_report=reality_report,
            perception_report=perception_report,
            personality_report=personality_report,
            blocked_by=None,
            notes=notes,
        )


__all__ = ["ResponseGuardChain", "ResponseGuardResult"]
