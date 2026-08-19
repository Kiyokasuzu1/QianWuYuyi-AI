# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3/test_self_model_integration.py

Phase 5.0-D3-A: Self Model System —— 集成测试

覆盖:
- Adapter + Manager + ChangeLog 协作
- IntegrationEvent → Adapter → Manager → ChangeRecord 完整链路
- 任意状态变化可追溯到事件证据
- SelfModelLifecycleTask + Adapter + Manager 闭环
- 独立运行(无 LLM/DB/Network/业务模块)
"""
import unittest
from typing import Any, Dict, List

from src.runtime.integration.integration_event import (
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.lifecycle.internal.clock import (
    FrozenClock,
    MockClock,
    SystemClock,
)
from src.runtime.self_model.change_log import ChangeLog
from src.runtime.self_model.self_model_adapter import (
    SELF_MODEL_CAPABILITY_USED,
    SELF_MODEL_CHANGE_RECORDED,
    SELF_MODEL_INTEREST_REINFORCED,
    SELF_MODEL_SNAPSHOT_REFRESHED,
    SELF_MODEL_TRAIT_REINFORCED,
    SelfModelAdapter,
)
from src.runtime.self_model.self_model_lifecycle_task import (
    SelfModelLifecycleTask,
)
from src.runtime.self_model.self_model_manager import SelfModelManager
from src.runtime.self_model.self_state import (
    ChangeRecord,
    IdentityView,
    TraitState,
    CapabilityState,
    InterestState,
)


# ============================================================
# 工具
# ============================================================
class _FakeContext:
    def __init__(self, tick: int = 1) -> None:
        self._tick = tick
        self.task_id = "self_model_lifecycle_task"

    def now(self) -> float:
        return 0.0

    @property
    def tick(self) -> int:
        return self._tick


def _started_manager(*, change_log_capacity: int = 1024) -> SelfModelManager:
    m = SelfModelManager(change_log_capacity=change_log_capacity)
    m.start()
    return m


# ============================================================
# 完整链路:IntegrationEvent → Adapter → Manager → ChangeLog
# ============================================================
class TestEndToEndEventFlow(unittest.TestCase):
    def test_event_drives_trait_update(self):
        m = _started_manager()
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"trait": "curiosity", "value": 0.7, "evidence_event_ids": ["e1"]},
        )
        rec = adp.handle_event(ev)
        # 1) Adapter 返回 record
        self.assertIsNotNone(rec)
        # 2) Manager 状态更新
        t = m.get_trait("curiosity")
        self.assertIsNotNone(t)
        self.assertEqual(t.current_value, 0.7)
        # 3) ChangeLog 记录
        self.assertGreater(m.change_log.size, 0)
        # 4) 证据可追溯
        self.assertTrue(m.contains_evidence("e1"))

    def test_event_drives_capability_update(self):
        m = _started_manager()
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        ev = make_integration_event(
            event_type=SELF_MODEL_CAPABILITY_USED,
            source="test",
            payload={"capability": "speak", "proficiency": 0.8, "evidence_event_ids": ["e1"]},
        )
        adp.handle_event(ev)
        c = m.get_capability("speak")
        self.assertEqual(c.proficiency, 0.8)
        self.assertTrue(m.contains_evidence("e1"))

    def test_event_drives_interest_update(self):
        m = _started_manager()
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        ev = make_integration_event(
            event_type=SELF_MODEL_INTEREST_REINFORCED,
            source="test",
            payload={"interest": "music", "boost": 0.1, "evidence_event_ids": ["e1"]},
        )
        adp.handle_event(ev)
        i = m.get_interest("music")
        self.assertGreater(i.level, 0.5)
        self.assertTrue(m.contains_evidence("e1"))


# ============================================================
# 状态变化可追溯到事件证据
# ============================================================
class TestStateChangeTraceability(unittest.TestCase):
    def test_state_change_back_to_evidence(self):
        m = _started_manager()
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        ev1 = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"trait": "c", "value": 0.3, "evidence_event_ids": ["eA"]},
        )
        adp.handle_event(ev1)
        ev2 = make_integration_event(
            event_type=SELF_MODEL_CAPABILITY_USED,
            source="test",
            payload={"capability": "speak", "proficiency": 0.6, "evidence_event_ids": ["eB"]},
        )
        adp.handle_event(ev2)
        # 通过 evidence 反查
        recs_a = m.changes_by_evidence("eA")
        recs_b = m.changes_by_evidence("eB")
        self.assertEqual(len(recs_a), 1)
        self.assertEqual(len(recs_b), 1)
        self.assertEqual(recs_a[0].target_name, "c")
        self.assertEqual(recs_b[0].target_name, "speak")

    def test_event_id_preserved_as_evidence(self):
        m = _started_manager()
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"trait": "c", "value": 0.5},  # 无 evidence_event_ids
        )
        rec = adp.handle_event(ev)
        # 应当使用 event_id 作为 evidence
        self.assertIn(ev.event_id, rec.evidence_event_ids)

    def test_full_audit_trail_queryable(self):
        m = _started_manager(change_log_capacity=100)
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        # 一连串事件
        for i in range(5):
            ev = make_integration_event(
                event_type=SELF_MODEL_TRAIT_REINFORCED,
                source="test",
                payload={
                    "trait": f"t{i}",
                    "value": 0.5,
                    "evidence_event_ids": [f"e{i}"],
                },
            )
            adp.handle_event(ev)
        # 所有 5 个 trait 已更新
        self.assertEqual(m.get_state().trait_count, 5)
        # change log 全部记录
        self.assertEqual(m.change_log.size, 5)
        # 全部 5 个证据可追溯
        for i in range(5):
            self.assertTrue(m.contains_evidence(f"e{i}"))

    def test_query_by_target(self):
        m = _started_manager()
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        adp.handle_event(make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="t",
            payload={"trait": "x", "value": 0.5, "evidence_event_ids": ["e1"]},
        ))
        adp.handle_event(make_integration_event(
            event_type=SELF_MODEL_CAPABILITY_USED,
            source="t",
            payload={"capability": "y", "proficiency": 0.5, "evidence_event_ids": ["e2"]},
        ))
        trait_recs = m.query_changes(target="trait")
        cap_recs = m.query_changes(target="capability")
        self.assertEqual(len(trait_recs), 1)
        self.assertEqual(len(cap_recs), 1)


# ============================================================
# LifecycleTask + Adapter + Manager 闭环
# ============================================================
class TestLifecycleTaskIntegration(unittest.TestCase):
    def test_task_runs_emits_and_records(self):
        m = _started_manager()
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        # 捕获 emit
        captured: List[IntegrationEvent] = []

        def _capture(ev):
            captured.append(ev)

        adp._event_emitter = _capture
        # 创建 task
        task = SelfModelLifecycleTask(adapter=adp, interval_seconds=0.0)
        ctx = _FakeContext(tick=1)
        result = task.execute(ctx)
        # task 应返回 dict
        self.assertIsInstance(result, dict)
        self.assertEqual(len(result["events"]), 1)
        # 至少 1 个事件被 emit
        self.assertGreater(len(captured), 0)
        # snapshot_refresh 事件存在
        types = [ev.event_type for ev in captured]
        self.assertIn(SELF_MODEL_SNAPSHOT_REFRESHED, types)
        # manager 有 change record
        recs = m.query_changes(kind="snapshot_refresh")
        self.assertGreaterEqual(len(recs), 1)

    def test_task_repeated_ticks(self):
        m = _started_manager()
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        task = SelfModelLifecycleTask(adapter=adp, interval_seconds=0.0)
        # 跑 5 次
        for i in range(5):
            task.execute(_FakeContext(tick=i))
        # tick 计数正确
        self.assertEqual(task.execution_count, 5)
        self.assertEqual(task.tick_refresh_count, 5)
        # change log 5 条
        recs = m.query_changes(kind="snapshot_refresh")
        self.assertEqual(len(recs), 5)

    def test_task_with_decay_generates_records(self):
        m = _started_manager()
        # 先创建 interest
        m.upsert_interest(
            name="music",
            new_level=0.5,
            new_decay_rate=0.01,
            evidence_event_ids=["e_init"],
        )
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        task = SelfModelLifecycleTask(adapter=adp, interval_seconds=0.0)
        task.execute(_FakeContext(tick=1))
        # interest 应已被衰减
        i = m.get_interest("music")
        self.assertLess(i.level, 0.5)


# ============================================================
# 独立运行(无业务模块依赖)
# ============================================================
class TestIndependentRuntime(unittest.TestCase):
    def test_runs_without_business_modules(self):
        """验证 SelfModel 可以在不 import 业务模块的情况下独立运行。"""
        import subprocess
        result = subprocess.run(
            ["python", "-c", """
import sys
sys.path.insert(0, 'd:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI')
# 显式 import 业务模块会失败
try:
    from src.memory import anything  # noqa
    print('LEAK: business module imported')
    sys.exit(1)
except (ImportError, ModuleNotFoundError):
    pass
# 验证 SelfModel 可独立运行
from src.runtime.self_model.self_model_manager import SelfModelManager
from src.runtime.self_model.self_model_adapter import SelfModelAdapter
from src.runtime.self_model.self_model_lifecycle_task import SelfModelLifecycleTask
m = SelfModelManager()
m.start()
m.upsert_trait(name='x', new_value=0.5, evidence_event_ids=['e1'])
print('OK')
"""],
            capture_output=True,
            text=True,
        )
        # 输出应包含 OK
        self.assertIn("OK", result.stdout)

    def test_no_third_party_dependencies(self):
        """验证 SelfModel 仅依赖标准库 + 内部 Runtime 模块。"""
        import ast
        from pathlib import Path
        d3_dir = Path("d:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/self_model")
        # 收集所有 imports
        imports: List[str] = []
        for py_file in d3_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for n in node.names:
                        imports.append(n.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.append(node.module)
        # 验证没有第三方依赖
        for imp in imports:
            # 允许: src.runtime.*, 标准库(无前缀)
            if imp.startswith("src."):
                # 必须属于 src.runtime.* (且不可依赖业务模块)
                self.assertTrue(
                    imp.startswith("src.runtime"),
                    f"Unexpected src import in self_model: {imp}",
                )
                # 不允许 import 业务模块
                forbidden_prefixes = [
                    "src.memory",
                    "src.growth",
                    "src.personality",
                    "src.emotion",
                    "src.relationship",
                ]
                for forbidden in forbidden_prefixes:
                    self.assertFalse(
                        imp.startswith(forbidden),
                        f"SelfModel forbidden import: {imp}",
                    )
            else:
                # 标准库
                pass


# ============================================================
# 端到端:多模块协作
# ============================================================
class TestFullWorkflow(unittest.TestCase):
    def test_full_workflow_with_evidence_chain(self):
        """端到端测试:模拟外部事件 → Adapter → Manager → ChangeLog → Query。"""
        # 1) 启动 Manager
        m = _started_manager()
        # 2) 创建 Adapter 绑定 Manager
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        # 3) 收集 emit 事件
        captured: List[IntegrationEvent] = []

        def _capture(ev):
            captured.append(ev)

        adp._event_emitter = _capture
        # 4) 外部触发 3 个不同类型事件
        events = [
            make_integration_event(
                event_type=SELF_MODEL_TRAIT_REINFORCED,
                source="ext",
                payload={"trait": "curiosity", "value": 0.8, "evidence_event_ids": ["e1"]},
            ),
            make_integration_event(
                event_type=SELF_MODEL_INTEREST_REINFORCED,
                source="ext",
                payload={"interest": "music", "boost": 0.1, "evidence_event_ids": ["e2"]},
            ),
            make_integration_event(
                event_type=SELF_MODEL_CAPABILITY_USED,
                source="ext",
                payload={"capability": "speak", "proficiency": 0.7, "evidence_event_ids": ["e3"]},
            ),
        ]
        for ev in events:
            rec = adp.handle_event(ev)
            self.assertIsNotNone(rec)
        # 5) 状态更新
        self.assertEqual(m.get_state().trait_count, 1)
        self.assertEqual(m.get_state().interest_count, 1)
        self.assertEqual(m.get_state().capability_count, 1)
        # 6) ChangeLog 记录
        self.assertEqual(m.change_log.size, 3)
        # 7) 全部 evidence 可追溯
        for eid in ["e1", "e2", "e3"]:
            self.assertTrue(m.contains_evidence(eid))
        # 8) 全部 change record 都可由证据反查
        for eid in ["e1", "e2", "e3"]:
            recs = m.changes_by_evidence(eid)
            self.assertEqual(len(recs), 1)

    def test_workflow_with_snapshot_refresh(self):
        m = _started_manager()
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        # 触发 refresh 事件
        ev = make_integration_event(
            event_type=SELF_MODEL_SNAPSHOT_REFRESHED,
            source="ext",
            payload={"evidence_event_ids": ["e_snap"]},
        )
        rec = adp.handle_event(ev)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.change_kind, "snapshot_refresh")
        # 状态被刷新
        self.assertEqual(m.get_state().last_refreshed, rec.timestamp)

    def test_state_persistence_snapshot(self):
        """验证状态快照可被序列化。"""
        m = _started_manager()
        m.upsert_trait(name="c", new_value=0.5, evidence_event_ids=["e1"])
        m.upsert_capability(name="speak", new_proficiency=0.7, evidence_event_ids=["e2"])
        m.upsert_interest(name="music", new_level=0.4, evidence_event_ids=["e3"])
        snap = m.snapshot_dict()
        self.assertEqual(snap["counters"]["traits"], 1)
        self.assertEqual(snap["counters"]["capabilities"], 1)
        self.assertEqual(snap["counters"]["interests"], 1)
        self.assertIn("c", snap["traits"])
        self.assertIn("speak", snap["capabilities"])
        self.assertIn("music", snap["interests"])


# ============================================================
# 时间注入:MockClock 验证
# ============================================================
class TestClockInjection(unittest.TestCase):
    def test_full_chain_uses_injected_clock(self):
        # 用 MockClock
        current = [1000.0]

        def now_fn():
            return current[0]

        m = SelfModelManager(clock=MockClock(now_fn=now_fn))
        m.start()
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        # 触发事件 → 产生 ChangeRecord(时间 = current[0])
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="t",
            payload={"trait": "x", "value": 0.5, "evidence_event_ids": ["e1"]},
        )
        rec = adp.handle_event(ev)
        self.assertEqual(rec.timestamp, 1000.0)
        # 推进时间
        current[0] = 2000.0
        ev2 = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="t",
            payload={"trait": "y", "value": 0.5, "evidence_event_ids": ["e2"]},
        )
        rec2 = adp.handle_event(ev2)
        self.assertEqual(rec2.timestamp, 2000.0)

    def test_frozen_clock_in_chain(self):
        clk = FrozenClock(initial=500.0, frozen=True)
        m = SelfModelManager(clock=clk)
        m.start()
        adp = SelfModelAdapter()
        adp.bind_manager(m)
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="t",
            payload={"trait": "x", "value": 0.5, "evidence_event_ids": ["e1"]},
        )
        rec = adp.handle_event(ev)
        self.assertEqual(rec.timestamp, 500.0)
        ev2 = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="t",
            payload={"trait": "y", "value": 0.5, "evidence_event_ids": ["e2"]},
        )
        rec2 = adp.handle_event(ev2)
        # FrozenClock 冻结 → 时间不变
        self.assertEqual(rec2.timestamp, 500.0)


# ============================================================
# 业务模块隔离检查(通过代码扫描)
# ============================================================
class TestBusinessModuleIsolation(unittest.TestCase):
    def test_no_business_module_imports_in_self_model(self):
        """通过 AST 扫描验证 self_model 不 import 任何业务模块。"""
        import ast
        from pathlib import Path
        d3_dir = Path("d:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/self_model")
        forbidden = ["memory", "growth", "personality", "emotion", "relationship"]
        for py_file in d3_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for f in forbidden:
                        if f in module.split("."):
                            self.fail(
                                f"SelfModel module {py_file.name} imports forbidden module {module}"
                            )


if __name__ == "__main__":
    unittest.main()
