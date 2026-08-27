# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/tasks/emotion_cycle.py

P2.7 Phase D-1: Emotion Snapshot 周期任务(Thin Trigger)。

职责(任务书 Step 5):
- 周期读取已有情绪状态,生成 EmotionSnapshot(只读)
- 输出 LifecycleEvent(审计携带 snapshot)

红线:
- 禁止修改人格 / trait / identity
- 禁止写回任何情绪状态(EmotionRepository 只 load,绝不 save)
- 若未来需要情绪模式升级为人格提案,必须走
  Event → GrowthProposal → 审核 → Drain 已有治理链(本阶段不实现)

注入设计:
- snapshot_provider: Callable[[], dict](默认惰性 EmotionRepository.load
  只读快照;测试/实验注入 fake 或临时路径 provider)
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src.contracts.lifecycle_event_schema import TASK_TYPE_EMOTION_CYCLE
from src.runtime.lifecycle.lifecycle_decision import AfterInterval
from src.runtime.lifecycle.lifecycle_task import BaseLifecycleTask

DEFAULT_EMOTION_STATE_PATH = "data/emotion_state.json"
DEFAULT_INTERVAL_SECONDS = 3600.0  # 默认每小时一次
DEFAULT_BUDGET_MS = 2000
OWNER = "p2.7.d1"
TASK_ID = "emotion.cycle"


def _build_default_provider(path: str) -> Callable[[], Dict[str, Any]]:
    """惰性构造 EmotionRepository 只读快照 provider。"""

    def _snapshot() -> Dict[str, Any]:
        from src.emotion.emotion_repository import EmotionRepository

        repo = EmotionRepository(filepath=str(path))
        state = repo.load()
        data = state.to_dict() if state is not None else {}
        return {"emotion_state": data}

    return _snapshot


class EmotionCycleTask(BaseLifecycleTask):
    """情绪快照周期任务:只读 snapshot + 审计。"""

    TASK_TYPE = TASK_TYPE_EMOTION_CYCLE
    IDEMPOTENCY_BUCKET_SECONDS = 3600  # 幂等键按小时分桶

    def __init__(
        self,
        snapshot_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        *,
        task_id: str = TASK_ID,
        interval_seconds: Optional[float] = None,
        budget_ms: Optional[int] = DEFAULT_BUDGET_MS,
        emotion_state_path: str = DEFAULT_EMOTION_STATE_PATH,
    ) -> None:
        interval = float(
            interval_seconds if interval_seconds is not None else DEFAULT_INTERVAL_SECONDS
        )
        super().__init__(
            task_id,
            OWNER,
            display_name="Emotion Snapshot Cycle",
            condition=AfterInterval(interval),
            budget_ms=budget_ms,
            interval_seconds=interval,
            max_concurrent=1,
        )
        self._provider = (
            snapshot_provider
            if snapshot_provider is not None
            else _build_default_provider(emotion_state_path)
        )
        self.emotion_state_path = str(emotion_state_path)

    # --------------------------------------------------------
    # 执行
    # --------------------------------------------------------
    def execute(self, context) -> Dict[str, Any]:
        """只读执行:读取情绪状态快照 → 返回 metrics。

        返回 dict 由 run_safely 包装为 SUCCESS LifecycleResult;
        异常由 run_safely 隔离为 FAILED。
        """
        snapshot = self._provider() or {}
        return {
            "metrics": {
                "snapshot": snapshot,
            },
        }


__all__ = [
    "EmotionCycleTask",
    "DEFAULT_EMOTION_STATE_PATH",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_BUDGET_MS",
]
