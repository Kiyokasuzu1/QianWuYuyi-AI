# -*- coding: utf-8 -*-
"""
tests/test_secondary_memory_authority.py

Phase 4.4.3 + 4.4.4 Secondary Memory Authority 收口测试。

验证 EventExtractor、EventHistoryMatcher、SelfChecker 使用 RuntimeCore Authority 的共享实例。

覆盖目标：
A. EventExtractor.store is RuntimeCore.get_memory_store()
B. EventHistoryMatcher.store is RuntimeCore.get_memory_store()
C. SelfChecker._get_memory_store() is RuntimeCore.get_memory_store()
D. Fallback: 没有 RuntimeBridge 时各模块自建 MemoryStore
E. 跨 Phase 回归保护
F. Phase 4.4.4: topic_tracker.py import 修复验证
"""
import os
import shutil
import tempfile
import importlib
from pathlib import Path

import pytest


# =====================================================================
# 临时工作目录 fixture
# =====================================================================
@pytest.fixture(scope="module", autouse=True)
def _tmp_workspace():
    """切换到临时工作目录，避免污染真实数据。"""
    original_cwd = os.getcwd()
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_secondary_mem_")
    os.chdir(tmp_dir)
    Path("data").mkdir(parents=True, exist_ok=True)
    yield tmp_dir
    os.chdir(original_cwd)
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_runtime_bridge():
    """每个测试前重置 RuntimeBridge 单例。"""
    try:
        from src.runtime.runtime_bridge import RuntimeBridge
        RuntimeBridge.reset_for_testing()
    except Exception:
        pass
    yield
    try:
        from src.runtime.runtime_bridge import RuntimeBridge
        RuntimeBridge.reset_for_testing()
    except Exception:
        pass


# =====================================================================
# 工具函数
# =====================================================================
def _init_runtime_bridge():
    """初始化 RuntimeBridge 和 RuntimeCore。"""
    from src.runtime.runtime_bridge import get_runtime_bridge
    bridge = get_runtime_bridge(config={
        "emotion_enabled": False,
        "adapters_enabled": False,
        "autostart": False,
    })
    bridge.initialize()
    return bridge


# =====================================================================
# A. EventExtractor Authority
# =====================================================================
class TestEventExtractorShared:

    def test_event_extractor_uses_runtime_core_memory_store(self):
        """EventExtractor.store is RuntimeCore.get_memory_store()"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.growth.event_extractor import EventExtractor
        extractor = EventExtractor()

        rc_store = rc.get_memory_store()
        assert rc_store is not None
        assert extractor.store is rc_store

    def test_multiple_extractors_share_same_store(self):
        """多个 EventExtractor 共享同一 MemoryStore。"""
        bridge = _init_runtime_bridge()

        from src.growth.event_extractor import EventExtractor
        e1 = EventExtractor()
        e2 = EventExtractor()

        assert e1.store is e2.store


# =====================================================================
# B. EventHistoryMatcher Authority
# =====================================================================
class TestEventHistoryMatcherShared:

    def test_event_history_matcher_uses_runtime_core_memory_store(self):
        """EventHistoryMatcher.store is RuntimeCore.get_memory_store()"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        # Phase 4.4.4: topic_tracker.py import 已修复，可正常导入
        from src.growth.topic_tracker import EventHistoryMatcher

        matcher = EventHistoryMatcher()

        rc_store = rc.get_memory_store()
        assert rc_store is not None
        assert matcher.store is rc_store

    def test_extractor_and_matcher_share_same_store(self):
        """EventExtractor 和 EventHistoryMatcher 共享同一 MemoryStore。"""
        bridge = _init_runtime_bridge()

        from src.growth.event_extractor import EventExtractor
        from src.growth.topic_tracker import EventHistoryMatcher

        extractor = EventExtractor()
        matcher = EventHistoryMatcher()

        assert extractor.store is matcher.store


# =====================================================================
# C. SelfChecker Authority
# =====================================================================
class TestSelfCheckerShared:

    def test_self_checker_uses_runtime_core_memory_store(self):
        """SelfChecker._get_memory_store() is RuntimeCore.get_memory_store()"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.thinking.self_check import SelfChecker
        checker = SelfChecker()

        sc_store = checker._get_memory_store()
        rc_store = rc.get_memory_store()
        assert rc_store is not None
        assert sc_store is rc_store

    def test_self_checker_caches_store(self):
        """SelfChecker 多次调用返回同一 MemoryStore。"""
        bridge = _init_runtime_bridge()

        from src.thinking.self_check import SelfChecker
        checker = SelfChecker()

        s1 = checker._get_memory_store()
        s2 = checker._get_memory_store()
        assert s1 is s2


# =====================================================================
# D. Fallback 兼容性
# =====================================================================
class TestFallbackCompatibility:

    def test_event_extractor_fallback(self):
        """没有 RuntimeBridge 时 EventExtractor 自建 MemoryStore。"""
        from src.growth.event_extractor import EventExtractor
        extractor = EventExtractor()
        assert extractor.store is not None

    def test_event_history_matcher_fallback(self):
        """没有 RuntimeBridge 时 EventHistoryMatcher 自建 MemoryStore。"""
        from src.growth.topic_tracker import EventHistoryMatcher
        matcher = EventHistoryMatcher()
        assert matcher.store is not None

    def test_self_checker_fallback(self):
        """没有 RuntimeBridge 时 SelfChecker 自建 MemoryStore。"""
        from src.thinking.self_check import SelfChecker
        checker = SelfChecker()
        assert checker._get_memory_store() is not None


# =====================================================================
# E. 跨 Phase 回归保护
# =====================================================================
class TestCrossPhaseRegression:

    def test_emotion_manager_still_works(self):
        """EmotionManager Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        em = bridge.get_emotion_manager()
        rc = bridge._runtime_core
        em_rc = rc.get_emotion_manager()
        assert em is em_rc

    def test_self_model_store_still_works(self):
        """SelfModelStore Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        sms1 = bridge.get_self_model_store()
        sms2 = bridge._runtime_core.get_self_model_store()
        assert sms1 is sms2

    def test_personality_resolver_still_works(self):
        """PersonalityResolver Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        pr1 = bridge.get_personality_resolver()
        pr2 = bridge._runtime_core.get_personality_resolver()
        assert pr1 is pr2

    def test_memory_store_still_works(self):
        """MemoryStore Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        ms1 = bridge.get_memory_store()
        ms2 = bridge._runtime_core.get_memory_store()
        assert ms1 is ms2

    def test_vector_memory_still_works(self):
        """VectorMemory Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        vm1 = bridge.get_vector_memory()
        vm2 = bridge._runtime_core.get_vector_memory()
        assert vm1 is vm2

    def test_growth_state_still_works(self):
        """GrowthState Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        gs1 = bridge.get_growth_state()
        gs2 = bridge._runtime_core.get_growth_state()
        assert gs1 is gs2

    def test_all_secondary_modules_share_memory_store(self):
        """所有次要模块共享同一 MemoryStore。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        rc_store = rc.get_memory_store()
        from src.growth.event_extractor import EventExtractor
        from src.growth.topic_tracker import EventHistoryMatcher
        from src.thinking.self_check import SelfChecker

        extractor = EventExtractor()
        matcher = EventHistoryMatcher()
        checker = SelfChecker()

        assert extractor.store is rc_store
        assert matcher.store is rc_store
        assert checker._get_memory_store() is rc_store


# =====================================================================
# F. Phase 4.4.4: topic_tracker.py import 修复验证
# =====================================================================
class TestTopicTrackerImportFix:
    """
    Phase 4.4.4 专项验证：
    - topic_tracker.py 中 EventHistoryMatcher 可被正常 import
    - 不再依赖 src.memory.store（破损路径）
    - 正确引用 src.memory.memory_store
    """

    def test_topic_tracker_module_imports_successfully(self):
        """topic_tracker 模块可直接 import，不应抛 ModuleNotFoundError。"""
        mod = importlib.import_module("src.growth.topic_tracker")
        assert mod is not None
        assert hasattr(mod, "EventHistoryMatcher")
        assert hasattr(mod, "Consolidator")

    def test_event_history_matcher_class_is_available(self):
        """EventHistoryMatcher 类可被直接导入。"""
        from src.growth.topic_tracker import EventHistoryMatcher
        assert EventHistoryMatcher is not None

    def test_topic_tracker_does_not_use_broken_import(self):
        """topic_tracker.py 不应包含破损 import。"""
        # 由于 _tmp_workspace 会切换到临时目录，需要用绝对路径定位源文件
        import src
        src_root = Path(src.__file__).parent
        topic_tracker_path = src_root / "growth" / "topic_tracker.py"
        assert topic_tracker_path.exists(), f"topic_tracker.py 应存在: {topic_tracker_path}"

        content = topic_tracker_path.read_text(encoding="utf-8")

        # 破损 import 不应存在
        assert "from src.memory.store import" not in content, (
            "破损 import 'from src.memory.store import' 不应存在"
        )
        assert "from src.growth.consolidation import" not in content, (
            "破损 import 'from src.growth.consolidation import' 不应存在"
        )
        # 正确 import 必须存在
        assert "from src.memory.memory_store import MemoryStore" in content, (
            "正确 import 'from src.memory.memory_store import MemoryStore' 必须存在"
        )
        assert "from src.growth.event_extractor import EventExtractor" in content, (
            "正确 import 'from src.growth.event_extractor import EventExtractor' 必须存在"
        )

    def test_event_history_matcher_uses_correct_memory_store_class(self):
        """EventHistoryMatcher.store 应是 src.memory.memory_store.MemoryStore 实例。"""
        from src.growth.topic_tracker import EventHistoryMatcher
        from src.memory.memory_store import MemoryStore

        matcher = EventHistoryMatcher()
        assert isinstance(matcher.store, MemoryStore)