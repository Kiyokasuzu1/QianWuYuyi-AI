# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/tasks/emotion_reflection.py

Phase F: Emotion Reflection Cycle 周期任务（Emotion System 2.0 接入
LifecycleRuntime 的第四个认知周期任务，与 Memory/Growth/SelfModel Cycle 并列）。

职责（任务书 Phase F）：
- 周期读取当前情绪状态，派生「反思快照」：
  主导情绪 / 强度 / 表达策略（表达方式·主动程度·语气倾向）/ 记忆权重 / 长期维度
- 输出 LifecycleEvent 审计（audit sink 可注入，落 JSONL 由 LifecycleRuntime 收口）
- 只读反思：不写回任何情绪状态，不产生情绪变化提案

红线（与 AGENTS.md / 任务书一致）：
- 禁止修改 identity_core / personality_state / trait —— 情绪只描述
  「我现在感觉如何」，绝不修改「我是谁」
- 禁止写回情绪状态（EmotionRepository 只 load，绝不 save，与
  EmotionCycleTask 红线一致）
- 禁止绕过治理链：若未来情绪模式需升级为人格提案，必须走
  Event → GrowthProposal → 审核 → Drain 治理链（本任务不做）
- 任务隔离：provider 异常由 LifecycleManager.run_safely 隔离为 FAILED，
  不影响其他周期任务

注入设计（测试注入 fake provider，生产默认惰性构造）：
- state_provider: Callable[[], Optional[dict]]——返回 EmotionState.to_dict()
  快照（None 表示无状态，跳过本轮）；默认读 data/emotion_state.json
- audit_sink: Callable[[dict], None]——反思审计条目出口
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src.runtime.lifecycle.lifecycle_decision import AfterInterval
from src.runtime.lifecycle.lifecycle_task import BaseLifecycleTask

TASK_TYPE_EMOTION_REFLECTION = "emotion_reflection"

DEFAULT_EMOTION_STATE_PATH = "data/emotion_state.json"
DEFAULT_INTERVAL_SECONDS = 3600.0  # 默认每小时反思一次
DEFAULT_BUDGET_MS = 2000
OWNER = "emotion.2_0.f"
TASK_ID = "emotion.reflection"

# 审计版本标记（组件名与 EmotionUpdater 区分：reflection 只读，updater 才写）
AUDIT_COMPONENT = "emotion_reflection"
AUDIT_VERSION = "emotion.2.0"


def _build_default_provider(path: str) -> Callable[[], Optional[Dict[str, Any]]]:
    """惰性构造 EmotionRepository 只读快照 provider（绝不 save）。"""

    def _snapshot() -> Optional[Dict[str, Any]]:
        from src.emotion.emotion_repository import EmotionRepository

        repo = EmotionRepository(filepath=str(path))
        state = repo.load()
        return state.to_dict() if state is not None else None

    return _snapshot


class EmotionReflectionTask(BaseLifecycleTask):
    """情绪反思周期任务：只读派生反思快照 + 审计。"""

    TASK_TYPE = TASK_TYPE_EMOTION_REFLECTION
    IDEMPOTENCY_BUCKET_SECONDS = 3600  # 幂等键按小时分桶

    def __init__(
        self,
        state_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        *,
        task_id: str = TASK_ID,
        interval_seconds: Optional[float] = None,
        budget_ms: Optional[int] = DEFAULT_BUDGET_MS,
        emotion_state_path: str = DEFAULT_EMOTION_STATE_PATH,
        audit_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        interval = float(
            interval_seconds if interval_seconds is not None else DEFAULT_INTERVAL_SECONDS
        )
        super().__init__(
            task_id,
            OWNER,
            display_name="Emotion Reflection Cycle",
            condition=AfterInterval(interval),
            budget_ms=budget_ms,
            interval_seconds=interval,
            max_concurrent=1,
        )
        self._provider = (
            state_provider
            if state_provider is not None
            else _build_default_provider(emotion_state_path)
        )
        self._audit_sink = audit_sink
        self.emotion_state_path = str(emotion_state_path)

    # --------------------------------------------------------
    # 执行（只读）
    # --------------------------------------------------------
    def execute(self, context) -> Dict[str, Any]:
        """只读反思：读状态 → 派生策略/权重/维度快照 → 审计。

        返回 dict 由 run_safely 包装为 SUCCESS LifecycleResult；
        异常由 run_safely 隔离为 FAILED。
        """
        from src.emotion.emotion_state import EmotionState
        from src.emotion.emotion_response_strategy import EmotionResponseStrategyBuilder
        from src.emotion.emotion_memory_weight import EmotionMemoryWeightBridge

        snapshot = self._provider()
        if not snapshot:
            return {"metrics": {"reflected": False, "reason": "no_emotion_state"}}

        state = EmotionState.from_dict(snapshot)
        strategy = EmotionResponseStrategyBuilder().build(state)
        weight = EmotionMemoryWeightBridge().compute_weight(state)

        reflection = {
            "dominant": state.dominant,
            "intensity": round(state.intensity, 4),
            "expression_style": strategy.expression_style,
            "proactivity": strategy.proactivity,
            "tone_style": strategy.tone_style,
            "memory_weight": weight,
            "long_term_dimensions": {
                "trust": state.trust,
                "attachment": state.attachment,
                "stability": state.stability,
                "confidence": state.confidence,
            },
        }

        if self._audit_sink is not None:
            self._audit_sink({
                "component": AUDIT_COMPONENT,
                "actor": self.task_id,
                "reason": "emotion_reflection_read_only",
                "before": {"dominant": state.dominant, "intensity": state.intensity},
                "after": reflection,
                "version": AUDIT_VERSION,
            })

        return {
            "metrics": {
                "reflected": True,
                "reflection": reflection,
            },
        }


__all__ = [
    "EmotionReflectionTask",
    "TASK_TYPE_EMOTION_REFLECTION",
    "DEFAULT_EMOTION_STATE_PATH",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_BUDGET_MS",
]
