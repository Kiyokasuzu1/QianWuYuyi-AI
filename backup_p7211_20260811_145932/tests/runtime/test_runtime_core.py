"""
RuntimeCore 单元测试
"""

import unittest
import tempfile
import shutil
import time
from pathlib import Path

from src.runtime.runtime_core import RuntimeCore
from src.runtime.self_state import SelfState
from src.runtime.world_state import WorldState


class TestRuntimeCoreLifecycle(unittest.TestCase):
    """测试生命周期"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_start_stop(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        result = core.start()
        self.assertTrue(result)
        self.assertTrue(core.is_running)

        result = core.stop()
        self.assertTrue(result)
        self.assertFalse(core.is_running)

    def test_start_creates_state_file_after_stop(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()
        core.stop()

        self.assertTrue(self.state_file.exists())

    def test_restart_loads_state(self):
        # 第一次启动并修改状态
        core1 = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core1.start()
        core1.self_state.energy = 0.3
        core1.stop()

        # 第二次启动应恢复状态
        core2 = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core2.start()
        self.assertAlmostEqual(core2.self_state.energy, 0.3, places=2)
        core2.stop()


class TestRuntimeCoreSnapshot(unittest.TestCase):
    """测试快照"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_snapshot(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()
        snapshot = core.get_snapshot()

        self.assertIn("world_state", snapshot)
        self.assertIn("self_state", snapshot)
        self.assertIn("last_tick", snapshot)
        self.assertIn("is_running", snapshot)
        self.assertTrue(snapshot["is_running"])

        core.stop()

    def test_get_states(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()

        ss = core.get_self_state()
        self.assertIsInstance(ss, SelfState)

        ws = core.get_world_state()
        self.assertIsInstance(ws, WorldState)

        core.stop()


class TestRuntimeCoreTick(unittest.TestCase):
    """测试 tick"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tick_updates_time(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()

        old_time = core.world_state.current_time
        time.sleep(0.01)
        core.tick()
        new_time = core.world_state.current_time

        self.assertNotEqual(old_time, new_time)
        core.stop()

    def test_tick_decay(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()
        core.self_state.energy = 1.0

        # 模拟1小时过去
        core._last_tick = time.time() - 3600
        core.tick()

        self.assertLess(core.self_state.energy, 1.0)
        core.stop()


class TestRuntimeCoreEventHandling(unittest.TestCase):
    """测试事件处理"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_inject_event_updates_state(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()

        old_social = core.self_state.social_need
        core.inject_event("user.input", {"content": "hello"})

        self.assertLess(core.self_state.social_need, old_social)
        self.assertTrue(len(core.world_state.recent_events) > 0)

        core.stop()

    def test_event_records_in_world_state(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()

        core.inject_event("user.input", {"content": "test"})
        events = core.world_state.recent_events

        self.assertEqual(events[-1]["type"], "user.input")
        core.stop()


class TestRuntimeCoreDecisionDispatch(unittest.TestCase):
    """测试决策到行动的闭环"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state.json"
        self.dispatched = []

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_decision_to_action(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
            "decision_confidence_threshold": 0.5,
        })
        core.start()

        # 注册处理器
        def handler(action):
            self.dispatched.append(action.action_type)
            return {"status": "sent"}

        core.action_dispatcher.register("send_message", handler)

        # 制造高 initiative + 高 social_need 状态
        core.self_state.initiative = 0.8
        core.self_state.social_need = 0.8
        core.tick()

        # 应该触发 send_message
        self.assertIn("send_message", self.dispatched)
        core.stop()


class TestRuntimeCoreDecisionEngineConfigMerge(unittest.TestCase):
    """Phase 7.2.1-p3: RuntimeCore 应同时读取 config["decision"] 和 config["decision_engine"]。"""

    def setUp(self):
        import tempfile
        import shutil
        from pathlib import Path
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state_dummy.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_decision_engine_sub_dict_takes_effect(self):
        """config["decision_engine"] 中的阈值必须出现在 DecisionEngine.thresholds。"""
        from src.runtime.runtime_core import RuntimeCore
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "decision_engine": {
                "initiative_min": 0.9,
                "social_need_min": 0.9,
            },
        })
        engine = core.decision_engine
        self.assertEqual(engine.thresholds["initiative_min"], 0.9)
        self.assertEqual(engine.thresholds["social_need_min"], 0.9)
        if core.is_running:
            core.stop()

    def test_decision_engine_overrides_old_decision_key(self):
        """config["decision_engine"] 应覆盖旧版 config["decision"] 的同名字段。"""
        from src.runtime.runtime_core import RuntimeCore
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "decision": {"initiative_min": 0.1},
            "decision_engine": {"initiative_min": 0.7},
        })
        self.assertEqual(core.decision_engine.thresholds["initiative_min"], 0.7)
        if core.is_running:
            core.stop()

    def test_default_self_state_triggers_send_message_immediately(self):
        """Phase 7.2.1-p3 核心断言：默认状态下 RuntimeCore._maybe_decide() 应
        返回至少 1 条 send_message action。"""
        from src.runtime.runtime_core import RuntimeCore
        dispatched = []
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "decision_confidence_threshold": 0.5,
        })
        core.action_dispatcher.register("send_message", lambda act: dispatched.append(act))
        actions = core._maybe_decide()
        send_actions = [a for a in actions if a.action_type == "send_message"]
        self.assertGreaterEqual(
            len(send_actions), 1,
            f"RuntimeCore 默认状态应触发 send_message；实际 actions={actions}, "
            f"self_state initiative={core.self_state.initiative:.2f} "
            f"social_need={core.self_state.social_need:.2f}"
        )
        self.assertTrue(dispatched, "ActionDispatcher 应实际调用 send_message handler")
        if core.is_running:
            core.stop()


class TestPhase721p3LoadStateBaseline(unittest.TestCase):
    """Phase 7.2.1-p3：_load_state() 读到历史地板值时，要自动抬升到触发基线。

    服务器日志现象：initiative=0.27 / social_need=0.00 导致重启后几小时内无法触发主动消息。
    """

    def setUp(self):
        import tempfile
        import shutil
        from pathlib import Path
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_state_raises_floor_values(self):
        """历史 state 文件里 social_need=0, initiative=0.2 应被自动抬升到 0.6 左右。"""
        import json
        from src.runtime.runtime_core import RuntimeCore

        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "self_state": {
                        "mood": "平静", "energy": 0.9, "curiosity": 0.1,
                        "social_need": 0.0, "focus": 0.3, "trust": 0.0,
                        "initiative": 0.2, "last_updated": 0.0,
                    },
                    "world_state": {},
                },
                f, ensure_ascii=False,
            )

        core = RuntimeCore(config={"state_file": str(self.state_file)})
        try:
            # _load_state 在 start() 里触发
            core.start()
            # 因为 load_state 读到 0.0 / 0.2，应自动抬高
            self.assertGreaterEqual(
                core.self_state.social_need, 0.58,
                f"social_need 旧值 0.0 应抬到 ≥ 0.58，实际 {core.self_state.social_need}",
            )
            self.assertGreaterEqual(
                core.self_state.initiative, 0.58,
                f"initiative 旧值 0.2 应抬到 ≥ 0.58，实际 {core.self_state.initiative}",
            )
            # 应该能触发 send_message（阈值 0.55，新值 0.60+）
            actions = core._maybe_decide()
            send = [a for a in actions if a.action_type == "send_message"]
            self.assertGreaterEqual(len(send), 1, "抬高后应立即能触发 send_message")
        finally:
            if core.is_running:
                core.stop()

    def test_load_state_keeps_high_values(self):
        """本身已经高于基线的，不能被往下压。"""
        import json
        from src.runtime.runtime_core import RuntimeCore

        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(
                {"self_state": {"mood": "开心", "energy": 0.9, "curiosity": 0.8,
                                "social_need": 0.95, "focus": 0.7, "trust": 0.9,
                                "initiative": 0.95, "last_updated": 0.0},
                 "world_state": {}},
                f, ensure_ascii=False,
            )
        core = RuntimeCore(config={"state_file": str(self.state_file)})
        try:
            core.start()
            self.assertGreaterEqual(core.self_state.initiative, 0.9)
            self.assertGreaterEqual(core.self_state.social_need, 0.9)
        finally:
            if core.is_running:
                core.stop()


class TestRuntimeCoreLonelinessRebound(unittest.TestCase):
    """Phase 7.2.1-p3：超过 loneliness_threshold_seconds 无交互时，
    每 tick 要微量回升 social_need / initiative。"""

    def setUp(self):
        import tempfile
        import shutil
        from pathlib import Path
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "state_lonely.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_loneliness_tick_increases_social_and_initiative(self):
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            # 阈值 1 秒（单元测试立刻进入独处模式）
            "loneliness_threshold_seconds": 1.0,
            "loneliness_social_gain_per_min": 0.1,  # 每分钟 +0.1（60s tick 就 +0.1）
            "loneliness_initiative_gain_per_min": 0.1,
            "tick_interval_seconds": 60.0,
        })
        # 手动打到地板值，上次交互时间为 10 分钟前
        core.self_state.social_need = 0.0
        core.self_state.initiative = 0.0
        core.world_state.self_state = core.self_state
        import time as _t
        core._last_interaction_at = _t.time() - 600.0  # 10 分钟前
        core._last_tick = _t.time()

        core.tick()
        # 60s tick => minutes=1.0, gain=0.1 each
        self.assertAlmostEqual(core.self_state.social_need, 0.1, places=3,
                               msg="独处 10 分钟后，第一个 tick 社交需求应回升约 0.1")
        self.assertAlmostEqual(core.self_state.initiative, 0.1, places=3,
                               msg="独处 10 分钟后，第一个 tick 主动性应回升约 0.1")
        if core.is_running:
            core.stop()

    def test_recent_user_message_skips_loneliness_rebound(self):
        """刚有用户交互时，不进入独处回升。"""
        from src.runtime.runtime_core import RuntimeCore
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "loneliness_threshold_seconds": 300.0,
            "loneliness_social_gain_per_min": 0.1,
            "loneliness_initiative_gain_per_min": 0.1,
            "tick_interval_seconds": 60.0,
        })
        core.self_state.social_need = 0.2
        core.self_state.initiative = 0.2
        core.world_state.self_state = core.self_state
        import time as _t
        core._last_interaction_at = _t.time()  # 刚交互过
        core._last_tick = _t.time()

        core.tick()
        # decay 是 initiative/social_need 几乎不动（/3600 衰减量级）
        # loneliness 不会触发；social_need / initiative 应该 ≤0.21 或更低（只有衰减）
        self.assertLessEqual(core.self_state.social_need, 0.21)
        self.assertLessEqual(core.self_state.initiative, 0.21)
        if core.is_running:
            core.stop()


if __name__ == "__main__":
    unittest.main()