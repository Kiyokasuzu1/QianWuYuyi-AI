"""
Phase 0.5 集成测试

模拟真实场景：
  加载模块 → 启动模块 → 修改配置 → 记录审计 → 事件通知
  验证各组件协同工作是否正常
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestModuleLifecycleIntegration(unittest.TestCase):
    """模块生命周期集成测试"""

    def setUp(self):
        from src.core.module_interface import ModuleBase, ModuleLifeCycleState
        from src.core.heartbeat import HeartbeatCollector
        from src.core.event_bus import EventBus
        from src.admin.core.audit import AuditLogger

        # 重置全局单例
        HeartbeatCollector.reset_instance()
        EventBus.reset_instance()
        AuditLogger.reset_instance()

        self.tmpdir = tempfile.mkdtemp()
        self.audit = AuditLogger(log_dir=self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_module_full_lifecycle(self):
        """完整生命周期：init → start → reload → stop"""
        from src.core.module_interface import (
            ModuleBase, ModuleLifeCycleState
        )

        class TestModule(ModuleBase):
            def __init__(self):
                super().__init__("test_mod")
                self.started = False
                self.stopped = False
                self.reloaded = False

            def _start(self):
                self.started = True
                return True

            def _stop(self):
                self.stopped = True
                return True

            def _reload(self):
                self.reloaded = True
                return True

        mod = TestModule()
        self.assertEqual(mod.state, ModuleLifeCycleState.UNINITIALIZED)
        self.assertFalse(mod.is_running)

        # 启动
        self.assertTrue(mod.start())
        self.assertEqual(mod.state, ModuleLifeCycleState.RUNNING)
        self.assertTrue(mod.is_running)
        self.assertTrue(mod.started)

        # 重复启动
        self.assertTrue(mod.start())  # 不应报错

        # 重载
        self.assertTrue(mod.reload())
        self.assertEqual(mod.state, ModuleLifeCycleState.RUNNING)
        self.assertTrue(mod.reloaded)

        # 健康检查
        health = mod.health_check()
        self.assertEqual(health.status, ModuleLifeCycleState.RUNNING)
        self.assertGreater(health.last_heartbeat, 0)

        # 停止
        self.assertTrue(mod.stop())
        self.assertEqual(mod.state, ModuleLifeCycleState.STOPPED)
        self.assertFalse(mod.is_running)
        self.assertTrue(mod.stopped)

    def test_module_start_failure(self):
        """模块启动失败的场景"""
        from src.core.module_interface import ModuleBase, ModuleLifeCycleState

        class FailingModule(ModuleBase):
            def _start(self):
                raise RuntimeError("启动失败")

        mod = FailingModule("failing")
        self.assertFalse(mod.start())
        self.assertEqual(mod.state, ModuleLifeCycleState.ERROR)
        self.assertGreater(mod.health_check().error_count, 0)

    def test_module_with_event_bus(self):
        """模块 + 事件总线集成"""
        from src.core.module_interface import ModuleBase
        from src.core.event_bus import EventBus

        events_received = []

        class EventedModule(ModuleBase):
            def _start(self):
                bus = EventBus.get_instance()
                bus.emit("module.started", {"module": "evented"}, source="evented")
                return True

            def _stop(self):
                bus = EventBus.get_instance()
                bus.emit("module.stopped", {"module": "evented"}, source="evented")
                return True

        bus = EventBus.get_instance()
        bus.subscribe("module.*", lambda e: events_received.append(e))

        mod = EventedModule("evented")
        mod.start()
        mod.stop()

        self.assertEqual(len(events_received), 2)
        self.assertEqual(events_received[0].event_type, "module.started")
        self.assertEqual(events_received[1].event_type, "module.stopped")
        self.assertEqual(events_received[0].source, "evented")

    def test_module_with_heartbeat(self):
        """模块 + 心跳集成"""
        from src.core.module_interface import ModuleBase
        from src.core.heartbeat import HeartbeatCollector, HeartbeatReporter, ModuleStatus

        class HeartbeatModule(ModuleBase):
            def _start(self):
                self.reporter = HeartbeatReporter(
                    "hb_mod", interval=9999,
                    collector=HeartbeatCollector.get_instance()
                )
                self.reporter.start(status=ModuleStatus.RUNNING)
                return True

            def _stop(self):
                if hasattr(self, "reporter"):
                    self.reporter.stop()
                return True

        mod = HeartbeatModule("hb_mod")
        mod.start()

        collector = HeartbeatCollector.get_instance()
        status = collector.get_status("hb_mod")
        self.assertIsNotNone(status)
        self.assertEqual(status["status"], "running")

        mod.stop()
        status2 = collector.get_status("hb_mod")
        self.assertEqual(status2["status"], "stopped")

    def test_config_change_triggers_audit_and_event(self):
        """配置修改 → 审计记录 → 事件通知 完整流程"""
        from src.admin.core.config_manager import ConfigManager
        from src.admin.core.audit import AuditLogger, AuditEventType, AuditOperatorType
        from src.core.event_bus import EventBus
        import yaml

        # 准备配置文件
        config_path = Path(self.tmpdir) / "config.yaml"
        initial = {"remote": {"enabled": False, "host": "0.0.0.0", "port": 8765}}
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(initial, f, allow_unicode=True)

        config_mgr = ConfigManager(config_path=str(config_path))
        audit = AuditLogger(log_dir=self.tmpdir)
        bus = EventBus.get_instance()

        # 订阅配置变更事件
        changed_events = []
        bus.subscribe("config.changed", lambda e: changed_events.append(e))

        # 修改配置
        result = config_mgr.toggle_module("remote", True, operator="admin")
        self.assertTrue(result["success"])

        # 记录审计
        audit.record(
            event_type=AuditEventType.MODULE_TOGGLE,
            operator="admin",
            operator_type=AuditOperatorType.HUMAN,
            target="remote",
            details={"enabled": True},
            result="success" if result["success"] else "failure",
        )

        # 发布事件
        bus.emit(
            "config.changed",
            {"section": "remote", "changes": {"enabled": True}},
            source="config_manager",
        )

        # 验证审计
        logs = audit.query(event_type=AuditEventType.MODULE_TOGGLE)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["operator"], "admin")
        self.assertEqual(logs[0]["operator_type"], "human")
        self.assertIn("request_id", logs[0])

        # 验证事件
        self.assertEqual(len(changed_events), 1)
        self.assertEqual(changed_events[0].payload["section"], "remote")

    def test_schema_validation_with_config_manager(self):
        """Schema 验证 + ConfigManager 集成"""
        from src.admin.core.schema_validator import SchemaValidator
        from src.admin.core.config_manager import ConfigManager
        import yaml

        config_path = Path(self.tmpdir) / "config.yaml"
        initial = {"remote": {"enabled": "not_bool", "host": "0.0.0.0", "port": 99999}}
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(initial, f, allow_unicode=True)

        # Schema 验证
        validator = SchemaValidator(
            src_root=str(Path(self.tmpdir)),  # 空 src，使用默认集中式 schema
        )
        all_errors = validator.validate_all(initial)

        self.assertIn("remote", all_errors)
        remote_errors = all_errors["remote"]
        # 应该有 enabled 类型错误 和 port 超出范围
        self.assertTrue(any("enabled" in e and "类型" in e for e in remote_errors))
        self.assertTrue(any("port" in e and "最大" in e for e in remote_errors))

    def test_backward_compat_imports(self):
        """向后兼容：旧导入路径仍可使用"""
        # 从旧路径导入
        from src.admin.config_manager import ConfigManager, ConfigValidationError
        from src.admin.audit import AuditLogger, AuditEventType, AuditOperatorType

        self.assertTrue(callable(ConfigManager))
        self.assertTrue(callable(AuditLogger))
        self.assertTrue(hasattr(AuditEventType, "CONFIG_UPDATE"))
        self.assertTrue(hasattr(AuditOperatorType, "HUMAN"))

    def test_request_id_correlation(self):
        """request_id 跨系统追踪"""
        import uuid
        from src.admin.core.audit import AuditLogger
        from src.core.event_bus import EventBus

        request_id = uuid.uuid4().hex[:12]
        audit = AuditLogger(log_dir=self.tmpdir)
        bus = EventBus.get_instance()

        # 同一个 request_id 贯穿审计和事件
        audit.record(
            event_type="config_update",
            operator="admin",
            request_id=request_id,
            target="screen",
            details={"enabled": True},
        )

        bus.emit(
            "config.changed",
            {"section": "screen"},
            source="config_manager",
            request_id=request_id,
        )

        # 审计中可按 request_id 查询
        logs = audit.query(request_id=request_id)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["request_id"], request_id)

        # 事件历史中可按 request_id 关联
        history = bus.get_history("config.*")
        self.assertTrue(any(e.request_id == request_id for e in history))


class TestEventBusIntegration(unittest.TestCase):
    """事件总线集成测试"""

    def setUp(self):
        from src.core.event_bus import EventBus
        EventBus.reset_instance()

    def test_wildcard_subscription(self):
        """通配符订阅测试"""
        from src.core.event_bus import EventBus

        bus = EventBus.get_instance()
        module_events = []
        all_events = []

        bus.subscribe("module.*", lambda e: module_events.append(e))
        bus.subscribe("*", lambda e: all_events.append(e))

        bus.emit("module.started", {"name": "test"}, source="test")
        bus.emit("config.changed", {"key": "val"}, source="config")

        self.assertEqual(len(module_events), 1)
        self.assertEqual(len(all_events), 2)

    def test_event_history(self):
        """事件历史记录"""
        from src.core.event_bus import EventBus

        bus = EventBus.get_instance()

        for i in range(5):
            bus.emit(f"test.event_{i}", {"i": i})

        history = bus.get_history("test.*", limit=3)
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0].event_type, "test.event_4")
        self.assertEqual(history[-1].event_type, "test.event_2")

    def test_unsubscribe(self):
        """取消订阅"""
        from src.core.event_bus import EventBus

        bus = EventBus.get_instance()
        received = []
        cb = lambda e: received.append(e)

        bus.subscribe("test.event", cb)
        bus.emit("test.event", {"n": 1})
        self.assertEqual(len(received), 1)

        bus.unsubscribe("test.event", cb)
        bus.emit("test.event", {"n": 2})
        self.assertEqual(len(received), 1)  # 不再增加


if __name__ == "__main__":
    unittest.main()
