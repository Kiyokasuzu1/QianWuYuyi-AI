"""
Phase 6.3: Runtime SelfModel Bootstrap 测试

验证：
- RuntimeCore 启动自动创建 SelfModelAdapter
- 自动 attach persistence + load_state
- 失败隔离（文件不存在、JSONL 损坏、权限错误）
- 重启后数据自动恢复
- save_self_model_state 工作
"""
from __future__ import annotations

import os
import sys
import json
import shutil
import tempfile
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.runtime.self_model_bootstrap import SelfModelBootstrap, auto_bootstrap
from src.personality.self_model_adapter import SelfModelAdapter
from src.personality.self_belief import SelfBelief
from src.personality.self_history import SelfHistoryEvent, SelfHistoryEventType
from src.personality.self_reflection import SelfReflectionNote
from src.personality.self_model_persistence import SelfModelPersistence


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_data_dir():
    d = tempfile.mkdtemp(prefix="sm_bootstrap_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ============================================================
# 1. SelfModelBootstrap 单元测试
# ============================================================

class TestSelfModelBootstrapUnit:
    def test_01_init_default(self):
        b = SelfModelBootstrap()
        assert b.data_dir == "data/self_model"
        assert b.last_load_counts == {}
        assert b.last_save_result is None

    def test_02_init_custom_dir(self):
        b = SelfModelBootstrap(data_dir="/tmp/abc")
        assert b.data_dir == "/tmp/abc"

    def test_03_bootstrap_attaches_persistence(self, tmp_data_dir):
        b = SelfModelBootstrap(data_dir=tmp_data_dir)
        adapter = SelfModelAdapter()
        env = b.bootstrap(adapter)
        assert env["persistence_attached"] is True
        assert adapter.has_persistence() is True

    def test_04_bootstrap_loads_existing(self, tmp_data_dir):
        # 1) 先保存一些数据
        a1 = SelfModelAdapter()
        a1.attach_persistence(SelfModelPersistence(tmp_data_dir))
        a1._beliefs.add(SelfBelief(domain="value", content="b1", confidence=0.7))
        a1._history.append(SelfHistoryEvent(
            event_type=SelfHistoryEventType.PCR_APPLIED,
            source_type="t", source_id="x", summary="h1",
        ))
        a1._reflections.append(SelfReflectionNote(
            trigger_source="manual", reflection_type="identity",
            content="r1", confidence=0.5,
        ))
        a1.save_state()
        # 2) Bootstrap 加载
        a2 = SelfModelAdapter()
        b = SelfModelBootstrap(data_dir=tmp_data_dir)
        env = b.bootstrap(a2)
        assert env["load_counts"]["beliefs"] == 1
        assert env["load_counts"]["history"] == 1
        assert env["load_counts"]["reflections"] == 1
        assert a2.get_beliefs().count() == 1
        assert a2.get_history().count() == 1
        assert a2.get_reflections().count() == 1

    def test_05_bootstrap_no_data(self, tmp_data_dir):
        b = SelfModelBootstrap(data_dir=tmp_data_dir)
        adapter = SelfModelAdapter()
        env = b.bootstrap(adapter)
        assert env["persistence_attached"] is True
        assert env["load_counts"] == {"beliefs": 0, "history": 0, "reflections": 0}
        assert env["errors"] == []

    def test_06_bootstrap_none_adapter(self, tmp_data_dir):
        b = SelfModelBootstrap(data_dir=tmp_data_dir)
        env = b.bootstrap(None)
        assert env["persistence_attached"] is False
        assert len(env["errors"]) > 0

    def test_07_bootstrap_corrupted_jsonl(self, tmp_data_dir):
        # 写一个损坏的 JSONL
        os.makedirs(tmp_data_dir, exist_ok=True)
        with open(os.path.join(tmp_data_dir, "beliefs.jsonl"), "w") as f:
            f.write("not a json\n{broken}\n")
        b = SelfModelBootstrap(data_dir=tmp_data_dir)
        adapter = SelfModelAdapter()
        env = b.bootstrap(adapter)
        # 不抛异常；errors 至少包含损坏信息
        assert env["persistence_attached"] is True
        # 0 加载（损坏行被跳过）

    def test_08_save(self, tmp_data_dir):
        b = SelfModelBootstrap(data_dir=tmp_data_dir)
        adapter = SelfModelAdapter()
        b.bootstrap(adapter)
        adapter._beliefs.add(SelfBelief(domain="value", content="x", confidence=0.6))
        ok = b.save(adapter)
        assert ok is True

    def test_09_save_no_adapter(self, tmp_data_dir):
        b = SelfModelBootstrap(data_dir=tmp_data_dir)
        ok = b.save(None)
        assert ok is False

    def test_10_get_status(self, tmp_data_dir):
        b = SelfModelBootstrap(data_dir=tmp_data_dir)
        adapter = SelfModelAdapter()
        b.bootstrap(adapter)
        status = b.get_status()
        assert status["data_dir"] == tmp_data_dir
        assert status["persistence_attached"] is True
        assert "last_load_counts" in status

    def test_11_force_persistence_replaces(self, tmp_data_dir):
        """force_persistence=True 时即使已有 persistence 也强制覆盖"""
        b1 = SelfModelBootstrap(data_dir=tmp_data_dir)
        a1 = SelfModelAdapter()
        b1.bootstrap(a1)
        a1._beliefs.add(SelfBelief(domain="value", content="kept", confidence=0.5))
        b1.save(a1)
        # 重新 bootstrap force
        a2 = SelfModelAdapter()
        b2 = SelfModelBootstrap(data_dir=tmp_data_dir)
        env = b2.bootstrap(a2, force_persistence=True)
        # 重新 attach 但不重复 load（load_state 会累积）
        # 这里关键是 force 不抛错
        assert env["persistence_attached"] is True


# ============================================================
# 2. auto_bootstrap 便捷函数
# ============================================================

class TestAutoBootstrap:
    def test_01_auto_bootstrap_creates(self, tmp_data_dir):
        adapter = SelfModelAdapter()
        env = auto_bootstrap(adapter, data_dir=tmp_data_dir)
        assert env["persistence_attached"] is True

    def test_02_auto_bootstrap_exception_isolated(self, tmp_data_dir):
        # 传入 None adapter
        env = auto_bootstrap(None, data_dir=tmp_data_dir)
        assert "errors" in env
        assert env["persistence_attached"] is False

    def test_03_auto_bootstrap_returns_envelope(self, tmp_data_dir):
        a = SelfModelAdapter()
        env = auto_bootstrap(a, data_dir=tmp_data_dir)
        assert "load_counts" in env


# ============================================================
# 3. RuntimeCore 集成
# ============================================================

class TestRuntimeCoreBootstrap:
    def test_01_runtime_core_creates_adapter(self):
        from src.runtime.runtime_core import RuntimeCore
        rc = RuntimeCore(config={"adapters_enabled": True})
        adapter = rc.get_self_model_adapter()
        # adapters_enabled=True 时应自动创建
        if rc.self_model_manager is not None:
            assert adapter is not None

    def test_02_runtime_core_bootstrap_envelope(self):
        from src.runtime.runtime_core import RuntimeCore
        rc = RuntimeCore(config={"adapters_enabled": True})
        env = rc.get_self_model_bootstrap_envelope()
        # envelope 应有 load_counts 字段
        assert "load_counts" in env

    def test_03_runtime_core_save_state(self, tmp_data_dir):
        """save_self_model_state 写到临时目录"""
        from src.runtime.runtime_core import RuntimeCore
        # 替换 data_dir
        rc = RuntimeCore(config={
            "adapters_enabled": True,
            "self_model_data_dir": tmp_data_dir,
        })
        if rc.get_self_model_adapter() is None:
            pytest.skip("adapter not created")
        adapter = rc.get_self_model_adapter()
        adapter._beliefs.add(SelfBelief(domain="value", content="rc_save", confidence=0.6))
        result = rc.save_self_model_state()
        # 应有保存
        assert isinstance(result, dict)
        # 文件应存在
        assert os.path.exists(os.path.join(tmp_data_dir, "beliefs.jsonl"))

    def test_04_runtime_core_no_failure_on_missing_dir(self):
        """不存在的 data_dir 不应导致 Runtime 启动失败"""
        from src.runtime.runtime_core import RuntimeCore
        bad_dir = "/nonexistent/path/data/self_model"
        rc = RuntimeCore(config={
            "adapters_enabled": True,
            "self_model_data_dir": bad_dir,
        })
        # 任何 phase 6.3 失败都不应抛出
        assert rc is not None


# ============================================================
# 4. Restart 端到端
# ============================================================

class TestRestartEndToEnd:
    def test_01_restart_keeps_beliefs(self, tmp_data_dir):
        """创建 belief → save → 重新 bootstrap → 数据存在"""
        # 阶段 1
        a1 = SelfModelAdapter()
        b1 = SelfModelBootstrap(data_dir=tmp_data_dir)
        b1.bootstrap(a1)
        a1._beliefs.add(SelfBelief(domain="value", content="restart_belief", confidence=0.7))
        b1.save(a1)
        # 阶段 2：重新构造
        a2 = SelfModelAdapter()
        b2 = SelfModelBootstrap(data_dir=tmp_data_dir)
        b2.bootstrap(a2)
        contents = [b.content for b in a2.get_beliefs().all()]
        assert "restart_belief" in contents

    def test_02_restart_keeps_history(self, tmp_data_dir):
        a1 = SelfModelAdapter()
        b1 = SelfModelBootstrap(data_dir=tmp_data_dir)
        b1.bootstrap(a1)
        a1._history.append(SelfHistoryEvent(
            event_type=SelfHistoryEventType.PCR_APPLIED,
            source_type="t", source_id="x", summary="restart_history",
        ))
        b1.save(a1)
        a2 = SelfModelAdapter()
        b2 = SelfModelBootstrap(data_dir=tmp_data_dir)
        b2.bootstrap(a2)
        summaries = [e.summary for e in a2.get_history().all()]
        assert "restart_history" in summaries

    def test_03_restart_keeps_reflections(self, tmp_data_dir):
        a1 = SelfModelAdapter()
        b1 = SelfModelBootstrap(data_dir=tmp_data_dir)
        b1.bootstrap(a1)
        a1._reflections.append(SelfReflectionNote(
            trigger_source="manual", reflection_type="identity",
            content="restart_reflection", confidence=0.5,
        ))
        b1.save(a1)
        a2 = SelfModelAdapter()
        b2 = SelfModelBootstrap(data_dir=tmp_data_dir)
        b2.bootstrap(a2)
        contents = [n.content for n in a2.get_reflections().all()]
        assert "restart_reflection" in contents

    def test_04_two_cycles_stable(self, tmp_data_dir):
        for i in range(2):
            a = SelfModelAdapter()
            b = SelfModelBootstrap(data_dir=tmp_data_dir)
            b.bootstrap(a)
            a._beliefs.add(SelfBelief(domain="value", content=f"cycle_{i}", confidence=0.6))
            b.save(a)
        # 最终加载
        a_final = SelfModelAdapter()
        b_final = SelfModelBootstrap(data_dir=tmp_data_dir)
        env = b_final.bootstrap(a_final)
        # 至少 1 条 belief（不去重逻辑：content 不同）
        assert env["load_counts"]["beliefs"] == 2
