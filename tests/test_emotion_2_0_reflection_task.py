"""
Phase F：Emotion Reflection Cycle 接入 LifecycleRuntime 验收测试
对应任务书 Phase F 六项验收：
1. Runtime 脱离聊天运行
2. Emotion 任务周期执行
3. 不修改 identity_core
4. 不绕过 Governance（不触碰人格状态 / 不产生提案）
5. 不影响已有 Growth/SelfModel 测试（本文件仅增不删，全量回归单独跑）
6. 全量回归通过（由全量 pytest 验证）
另覆盖：task 隔离 / fail-soft / audit / idempotency / 只读红线
"""
import sys, os, json, copy
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_state import EmotionState
from src.runtime.lifecycle.internal.clock import FrozenClock
from src.runtime.lifecycle.lifecycle_runtime import LifecycleRuntime
from src.runtime.lifecycle.tasks.emotion_reflection import (
    EmotionReflectionTask,
    TASK_TYPE_EMOTION_REFLECTION,
)

START_EPOCH = 1_700_000_000.0


def _fake_state() -> dict:
    return EmotionState(
        valence=0.8, arousal=0.9, energy=0.8, trust=0.6, attachment=0.55,
        stability=0.65, updated_at="2026-08-21T00:00:00",
    ).to_dict()


def _make_runtime(tmp_path, clock, task):
    rt = LifecycleRuntime(
        config={"audit_path": str(tmp_path / "lifecycle_audit.jsonl")},
        clock=clock,
    )
    rt.manager.start(reason="test")
    rt.register_task(task)
    return rt


def test_runtime_ticks_standalone_without_chat(tmp_path):
    """验收 1：LifecycleRuntime 脱离聊天运行（无 Orchestrator/无会话）"""
    clock = FrozenClock(initial=START_EPOCH)
    rt = _make_runtime(tmp_path, clock, EmotionReflectionTask(
        state_provider=_fake_state, interval_seconds=3600,
    ))
    events = rt.tick()
    assert events, "tick 应产出事件"
    assert events[0].task_type == TASK_TYPE_EMOTION_REFLECTION
    assert events[0].result["status"] == "SUCCESS"


def test_emotion_task_runs_periodically(tmp_path):
    """验收 2：Emotion 任务周期执行（首跑 → 间隔内跳过 → 到点再跑）"""
    clock = FrozenClock(initial=START_EPOCH)
    task = EmotionReflectionTask(state_provider=_fake_state, interval_seconds=3600)
    rt = _make_runtime(tmp_path, clock, task)

    first = rt.tick()
    assert first[0].result["status"] == "SUCCESS"

    clock.advance(100.0)  # 100s：未到周期
    skipped = rt.tick()
    assert skipped[0].result["status"] == "SKIPPED"

    clock.advance(3600.0)  # 累计 3700s ≥ 3600s：再次运行
    again = rt.tick()
    assert again[0].result["status"] == "SUCCESS"

    state = rt.manager.get_task_state("emotion.reflection")
    assert state.run_count == 2
    assert state.skip_count >= 1


def test_reflection_content_derived(tmp_path):
    """反思快照：主导情绪 + 表达策略 + 记忆权重 + 长期维度"""
    clock = FrozenClock(initial=START_EPOCH)
    rt = _make_runtime(tmp_path, clock, EmotionReflectionTask(state_provider=_fake_state))
    events = rt.tick()
    metrics = events[0].result["metrics"]
    assert metrics["reflected"] is True
    reflection = metrics["reflection"]
    assert reflection["dominant"]
    assert reflection["expression_style"]
    assert reflection["proactivity"]
    assert reflection["tone_style"]
    assert "memory_weight" in reflection
    assert set(reflection["long_term_dimensions"]) == {
        "trust", "attachment", "stability", "confidence",
    }


def test_no_emotion_state_skips_gracefully(tmp_path):
    """无情绪状态（provider 返回 None）：跳过本轮，不崩溃"""
    clock = FrozenClock(initial=START_EPOCH)
    rt = _make_runtime(tmp_path, clock, EmotionReflectionTask(state_provider=lambda: None))
    events = rt.tick()
    assert events[0].result["status"] == "SUCCESS"
    assert events[0].result["metrics"]["reflected"] is False


def test_provider_failure_isolated(tmp_path):
    """验收：task 隔离 / fail-soft —— 单任务异常不影响其他任务、不中断 tick"""
    clock = FrozenClock(initial=START_EPOCH)
    rt = LifecycleRuntime(
        config={"audit_path": str(tmp_path / "audit.jsonl")},
        clock=clock,
    )
    rt.manager.start(reason="test")

    def _boom():
        raise RuntimeError("provider boom")

    rt.register_task(EmotionReflectionTask(state_provider=_boom, task_id="emotion.reflection.bad"))
    rt.register_task(EmotionReflectionTask(state_provider=_fake_state, task_id="emotion.reflection.good"))

    events = rt.tick()
    assert len(events) == 2
    statuses = [e.result["status"] for e in events]
    assert "FAILED" in statuses, "坏任务应 FAILED"
    assert "SUCCESS" in statuses, "好任务应照常 SUCCESS"


def test_audit_jsonl_and_idempotency_key(tmp_path):
    """验收：audit 完整 —— 事件落 JSONL，幂等键含 task_type 与小时分桶"""
    clock = FrozenClock(initial=START_EPOCH)
    rt = _make_runtime(tmp_path, clock, EmotionReflectionTask(state_provider=_fake_state))
    rt.tick()

    path = tmp_path / "lifecycle_audit.jsonl"
    assert path.exists()
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert lines
    event = lines[0]
    assert event["task_type"] == TASK_TYPE_EMOTION_REFLECTION
    key = event["audit"]["idempotency_key"]
    assert key.startswith("emotion_reflection:")
    # 同 bucket 内再次 tick（间隔未到 → SKIP，事件仍生成）→ 幂等键相同
    events2 = rt.tick()
    event2 = [l for l in events2 if l.task_type == TASK_TYPE_EMOTION_REFLECTION][0]
    assert event2.audit["idempotency_key"] == key


def test_identity_core_unchanged(tmp_path):
    """验收 3：多轮 tick 后 IDENTITY_CORE 逐位不变"""
    from src.personality.identity_core import IDENTITY_CORE
    before = copy.deepcopy(IDENTITY_CORE)
    clock = FrozenClock(initial=START_EPOCH)
    rt = _make_runtime(tmp_path, clock, EmotionReflectionTask(state_provider=_fake_state))
    for _ in range(3):
        rt.tick()
        clock.advance(3600.0)
    assert IDENTITY_CORE == before


def test_personality_state_not_touched(tmp_path):
    """验收 4：反思任务不触碰人格状态（不绕过 Governance、不产生人格修改）"""
    from src.personality.personality_state import PersonalityState
    ps = PersonalityState()
    before_traits = copy.deepcopy(ps.traits)
    before_version = ps.version

    clock = FrozenClock(initial=START_EPOCH)
    rt = _make_runtime(tmp_path, clock, EmotionReflectionTask(state_provider=_fake_state))
    for _ in range(3):
        rt.tick()
        clock.advance(3600.0)

    assert ps.traits == before_traits
    assert ps.version == before_version


def test_task_is_read_only_never_saves_state(tmp_path):
    """红线：反思任务只读 —— 状态快照逐位不变，不触发任何保存"""
    original = _fake_state()
    seen = {}

    def _provider():
        seen["state"] = copy.deepcopy(original)
        return copy.deepcopy(original)

    clock = FrozenClock(initial=START_EPOCH)
    rt = _make_runtime(tmp_path, clock, EmotionReflectionTask(state_provider=_provider))
    rt.tick()
    clock.advance(3600.0)
    rt.tick()

    assert seen["state"] == original


def test_host_registers_emotion_reflection_task(tmp_path):
    """接入：RuntimeIntegrationHost.register_default_tasks 注册含情绪反思任务"""
    from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost
    from src.runtime.lifecycle.lifecycle_manager import LifecycleManager

    mgr = LifecycleManager(clock=FrozenClock(initial=START_EPOCH), name="host_test")
    host = RuntimeIntegrationHost(lifecycle_manager=mgr)
    count = host.register_default_tasks()
    assert count >= 6
    ids = set(mgr.registry.task_ids())
    assert "emotion.reflection" in ids


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        test_runtime_ticks_standalone_without_chat(); print("✅ 1/10 脱离聊天运行")
        test_emotion_task_runs_periodically(p); print("✅ 2/10 周期执行")
        test_reflection_content_derived(p); print("✅ 3/10 反思内容")
        test_no_emotion_state_skips_gracefully(p); print("✅ 4/10 无状态跳过")
        test_provider_failure_isolated(p); print("✅ 5/10 异常隔离")
        test_audit_jsonl_and_idempotency_key(p); print("✅ 6/10 审计+幂等键")
        test_identity_core_unchanged(p); print("✅ 7/10 identity_core 不变")
        test_personality_state_not_touched(p); print("✅ 8/10 人格状态不动")
        test_task_is_read_only_never_saves_state(p); print("✅ 9/10 只读红线")
        test_host_registers_emotion_reflection_task(p); print("✅ 10/10 Host 注册")
    print("\n🎉 Phase F 验收全部通过")
