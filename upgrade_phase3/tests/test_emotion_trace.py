"""
Phase 9.3 v2.1 最终测试（适配 Phase 9.4 event_type 字段）：EmotionalTrace
所有文件操作使用临时目录，确保测试环境隔离。
"""
import sys, os, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotional_trace import EmotionalTrace, EmotionCause
from src.emotion.emotion_trace_repository import EmotionTraceRepository
from src.emotion.emotion_memory_bridge import EmotionMemoryBridge
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_manager import EmotionManager
from src.emotion.emotion_repository import EmotionRepository


def test_create_trace_with_memory():
    bridge = EmotionMemoryBridge()
    event = EmotionEvent("user_praise", intensity=0.8)
    trace = bridge.bind(event, memory_id="mem_001")
    assert trace.memory_id == "mem_001"
    assert trace.emotion == "joy"
    # Phase 9.4 新增：event_type 应被正确传递
    assert trace.event_type == "user_praise"


def test_trace_without_memory():
    bridge = EmotionMemoryBridge()
    event = EmotionEvent("new_topic", intensity=0.5)
    trace = bridge.bind(event)
    assert trace.memory_id is None
    assert trace.event_type == "new_topic"


def test_trace_repository_save_load():
    traces = [
        EmotionalTrace(
            emotion="joy",
            cause=EmotionCause.USER_INTERACTION,
            intensity=0.8,
            event_type="user_praise",
            memory_id="mem_001",
        ),
        EmotionalTrace(
            emotion="anxiety",
            cause=EmotionCause.USER_INTERACTION,
            intensity=0.6,
            event_type="user_conflict",
            memory_id=None,
        ),
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        path = f"{tmpdir}/traces.json"
        repo = EmotionTraceRepository(path)
        repo.save_all(traces)
        loaded = repo.load_all()
        assert len(loaded) == 2
        assert loaded[0].memory_id == "mem_001"
        assert loaded[0].event_type == "user_praise"
        assert loaded[1].memory_id is None
        assert loaded[1].event_type == "user_conflict"


def test_trace_repository_append():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = f"{tmpdir}/traces.json"
        repo = EmotionTraceRepository(path)
        trace = EmotionalTrace(
            emotion="joy",
            cause=EmotionCause.USER_INTERACTION,
            intensity=0.9,
            event_type="user_praise",
            memory_id="mem_new",
        )
        repo.append(trace)
        loaded = repo.load_all()
        assert len(loaded) == 1
        assert loaded[0].memory_id == "mem_new"
        assert loaded[0].event_type == "user_praise"


def test_trace_does_not_duplicate_memory_content():
    bridge = EmotionMemoryBridge()
    event = EmotionEvent(
        "user_praise",
        intensity=0.9,
        description="用户说喜欢羽依的声音",
    )
    trace = bridge.bind(event, memory_id="mem_003")
    trace_dict = trace.to_dict()
    assert "description" not in trace_dict
    assert "用户说喜欢羽依的声音" not in str(trace_dict)
    assert trace.memory_id == "mem_003"
    assert trace.event_type == "user_praise"


def test_trace_prompt_safe():
    trace = EmotionalTrace(
        emotion="joy",
        cause=EmotionCause.USER_INTERACTION,
        intensity=0.8,
        event_type="user_praise",
        memory_id="mem_004",
    )
    data = trace.to_dict()
    assert "source_ids" not in str(data)
    assert "reflection_id" not in str(data)


def test_intensity_clamped():
    trace = EmotionalTrace(
        emotion="joy",
        cause=EmotionCause.ACHIEVEMENT,
        intensity=1.5,
        event_type="achievement",
    )
    assert trace.intensity == 1.0


def test_manager_does_not_store_traces_internally():
    """EmotionManager 不应在实例内部缓存轨迹列表，且测试不污染开发环境"""
    with tempfile.TemporaryDirectory() as tmpdir:
        trace_path = f"{tmpdir}/traces.json"
        state_path = f"{tmpdir}/state.json"
        trace_repo = EmotionTraceRepository(trace_path)
        state_repo = EmotionRepository(state_path)
        manager = EmotionManager(
            repository=state_repo,
            trace_repository=trace_repo,
        )
        event = EmotionEvent("user_praise", intensity=0.7)
        manager.process_event(event, memory_id="mem_test")

        assert not hasattr(manager, "_traces")
        assert not hasattr(manager, "traces")

        recent = manager.get_recent_traces(limit=1)
        assert len(recent) > 0
        assert recent[0].memory_id == "mem_test"
        assert recent[0].event_type == "user_praise"


def test_old_trace_without_event_type():
    """旧版本 trace（无 event_type）反序列化后 event_type 应为空字符串，不崩溃"""
    old_data = {
        "emotion": "joy",
        "cause": "user_interaction",
        "intensity": 0.8,
        "memory_id": "old_mem"
    }
    trace = EmotionalTrace.from_dict(old_data)
    assert trace.event_type == ""
    assert trace.emotion == "joy"


def test_empty_event_type_safe():
    """event_type 为空字符串时，分析器应能正常处理（此处仅测序列化）"""
    trace = EmotionalTrace(
        emotion="joy",
        cause=EmotionCause.USER_INTERACTION,
        intensity=0.6,
        event_type="",
    )
    data = trace.to_dict()
    assert data["event_type"] == ""


if __name__ == "__main__":
    test_create_trace_with_memory()
    print("✅ 1/10 轨迹关联记忆 + event_type")
    test_trace_without_memory()
    print("✅ 2/10 无记忆的轨迹 + event_type")
    test_trace_repository_save_load()
    print("✅ 3/10 持久化保存加载（含 event_type）")
    test_trace_repository_append()
    print("✅ 4/10 增量添加（含 event_type）")
    test_trace_does_not_duplicate_memory_content()
    print("✅ 5/10 不重复存储记忆内容")
    test_trace_prompt_safe()
    print("✅ 6/10 Prompt 安全")
    test_intensity_clamped()
    print("✅ 7/10 强度限制")
    test_manager_does_not_store_traces_internally()
    print("✅ 8/10 Manager 不缓存轨迹（环境隔离）")
    test_old_trace_without_event_type()
    print("✅ 9/10 旧 trace 兼容（无 event_type）")
    test_empty_event_type_safe()
    print("✅ 10/10 空 event_type 序列化安全")
    print("\n🎉 Phase 9.3 v2.1 + 9.4 event_type 适配测试全部通过")