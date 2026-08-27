# -*- coding: utf-8 -*-
"""tests/test_emotion_cycle_task.py

P2.7 Phase D-1: EmotionCycleTask 单测(只读快照)。

红线: 任务只读取情绪状态,禁止修改人格/trait/identity(provider 为只读注入)。
"""

from src.runtime.lifecycle.internal.clock import FrozenClock
from src.runtime.lifecycle.lifecycle_context import LifecycleContext
from src.runtime.lifecycle.tasks.emotion_cycle import EmotionCycleTask


def _ctx(now=1000.0):
    return LifecycleContext(clock=FrozenClock(initial=now), task_id="emotion.cycle")


def test_snapshot_provider_result():
    fake_state = {"valence": 0.4, "arousal": 0.6, "dominant": "positive"}
    task = EmotionCycleTask(snapshot_provider=lambda: {"emotion_state": fake_state})
    result = task.run_safely(_ctx())
    assert result.is_success()
    snap = result.metrics["snapshot"]
    assert snap["emotion_state"]["valence"] == 0.4
    assert snap["emotion_state"]["dominant"] == "positive"


def test_provider_failure_isolated():
    def _boom():
        raise RuntimeError("provider boom")

    task = EmotionCycleTask(snapshot_provider=_boom)
    result = task.run_safely(_ctx())
    assert result.is_failure()
    assert result.error is not None


def test_task_metadata():
    task = EmotionCycleTask(snapshot_provider=lambda: {}, interval_seconds=7200.0)
    assert task.task_id == "emotion.cycle"
    assert task.owner == "p2.7.d1"
    assert task.interval_seconds == 7200.0
    assert task.TASK_TYPE == "emotion_cycle"
    assert task.IDEMPOTENCY_BUCKET_SECONDS == 3600
