"""
Phase 9.7 v2 最终测试：情绪系统接入集成
覆盖：事件检测（含否定）、状态持久化、衰减、GrowthService、真实参数、SelfModel 不污染
"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_manager import EmotionManager
from src.emotion.emotion_repository import EmotionRepository
from src.emotion.emotion_trace_repository import EmotionTraceRepository
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_event_detector import EmotionEventDetector
from src.emotion.emotion_growth_service import EmotionGrowthService
from src.emotion.emotion_belief_extractor import EmotionBeliefExtractor
from src.emotion.emotion_pattern_analyzer import EmotionPatternAnalyzer
from src.personality.self_model_v3 import SelfModelV3


def make_temp_manager(base_dir: str) -> EmotionManager:
    """使用同一目录创建 Manager，保证持久化路径一致"""
    state_path = f"{base_dir}/state.json"
    traces_path = f"{base_dir}/traces.json"
    counter_path = f"{base_dir}/counter.json"
    return EmotionManager(
        EmotionRepository(state_path),
        EmotionTraceRepository(traces_path),
        counter_file=counter_path,
    )


class FakeSelfModelStore:
    """模拟 SelfModelStore，仅用于测试保存功能"""
    def __init__(self):
        self.model = None

    def get_active_self_model(self):
        return self.model or SelfModelV3()

    def save(self, model):
        self.model = model


def test_detector_negation():
    """否定词检测：'我不喜欢' 应返回负面事件"""
    detector = EmotionEventDetector()
    event = detector.detect("我不喜欢这个设计")
    assert event is not None
    assert event.event_type == "user_conflict"


def test_detector_praise():
    """正向消息应返回 user_praise"""
    detector = EmotionEventDetector()
    event = detector.detect("你真是太棒了！")
    assert event is not None
    assert event.event_type == "user_praise"


def test_detector_neutral():
    """中性消息不应产生事件"""
    detector = EmotionEventDetector()
    event = detector.detect("今天天气不错")
    assert event is None


def test_full_pipeline_persists_state():
    """状态持久化：前后两次加载应一致"""
    base_dir = tempfile.mkdtemp()
    try:
        mgr1 = make_temp_manager(base_dir)
        mgr1.update()
        mgr1.process_event(EmotionEvent("user_praise", intensity=0.9))
        assert mgr1.state.valence > 0

        # 重新从同一目录加载
        mgr2 = make_temp_manager(base_dir)
        assert mgr2.state.valence == mgr1.state.valence
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_decay_reduces_emotion():
    """衰减后情绪强度应降低"""
    base_dir = tempfile.mkdtemp()
    try:
        mgr = make_temp_manager(base_dir)
        mgr.process_event(EmotionEvent("user_praise", intensity=1.0))
        before = abs(mgr.state.valence)

        # 模拟时间流逝 2 小时
        from datetime import datetime, timedelta
        mgr.state.updated_at = (datetime.now() - timedelta(hours=2)).isoformat()
        mgr.update()
        after = abs(mgr.state.valence)
        assert after < before
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_growth_service_merges_to_self_model():
    """GrowthService 能正确合并信念并保存（使用10个事件以确保达到阈值）"""
    store = FakeSelfModelStore()
    base_dir = tempfile.mkdtemp()
    try:
        mgr = make_temp_manager(base_dir)
        # 产生足够多的正面事件以通过默认阈值
        for _ in range(10):
            mgr.process_event(EmotionEvent("user_praise", intensity=0.8))

        service = EmotionGrowthService(mgr, store, analysis_interval=1)
        service.analyze_and_merge()

        saved = store.model
        assert saved is not None
        assert len(saved.emotional_self_understanding) > 0
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_real_parameters_produce_belief():
    """默认生产参数在足够数据下可产出信念"""
    base_dir = tempfile.mkdtemp()
    try:
        mgr = make_temp_manager(base_dir)
        # 需要足够样本量以满足默认的 min_occurrences=3, confidence>=0.6, stability>=0.5
        for _ in range(10):
            mgr.process_event(EmotionEvent("user_praise", intensity=0.8))

        analyzer = EmotionPatternAnalyzer()      # 默认阈值
        extractor = EmotionBeliefExtractor()     # 默认阈值
        traces = mgr.get_recent_traces(limit=100)
        patterns = analyzer.analyze(traces)
        beliefs = extractor.extract(patterns)
        assert len(beliefs) > 0
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


def test_regular_beliefs_not_polluted():
    """普通 beliefs 字段不会被情绪信念污染"""
    store = FakeSelfModelStore()
    initial = SelfModelV3(beliefs=["原有信念"])
    store.model = initial

    base_dir = tempfile.mkdtemp()
    try:
        mgr = make_temp_manager(base_dir)
        for _ in range(10):
            mgr.process_event(EmotionEvent("user_praise", intensity=0.8))

        service = EmotionGrowthService(mgr, store, analysis_interval=1)
        service.analyze_and_merge()

        assert len(store.model.beliefs) == 1
        assert store.model.beliefs[0] == "原有信念"
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


if __name__ == "__main__":
    test_detector_negation()
    print("✅ 1/8 否定词检测")
    test_detector_praise()
    print("✅ 2/8 正向词检测")
    test_detector_neutral()
    print("✅ 3/8 中性消息安全")
    test_full_pipeline_persists_state()
    print("✅ 4/8 状态持久化（修复）")
    test_decay_reduces_emotion()
    print("✅ 5/8 衰减验证")
    test_growth_service_merges_to_self_model()
    print("✅ 6/8 GrowthService 合并与保存")
    test_real_parameters_produce_belief()
    print("✅ 7/8 真实参数信念产出")
    test_regular_beliefs_not_polluted()
    print("✅ 8/8 普通信念防污染")
    print("\n🎉 Phase 9.7 v2 最终修正版全部通过")