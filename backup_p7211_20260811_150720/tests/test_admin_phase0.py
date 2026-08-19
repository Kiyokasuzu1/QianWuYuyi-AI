"""
Phase 0 基础设施单元测试

覆盖：ModuleLoader、HeartbeatCollector/Reporter、ConfigManager、AuditLogger
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

# 确保项目根目录在 sys.path 中
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestModuleLoader(unittest.TestCase):
    """模块自动发现与注册机制测试"""

    def setUp(self):
        """创建临时目录结构，模拟 src/ 目录"""
        self.tmpdir = tempfile.mkdtemp()
        self.src_dir = Path(self.tmpdir) / "src"
        self.src_dir.mkdir()

        # 创建一个带 MODULE_INFO 的模块
        mod_dir = self.src_dir / "test_mod"
        mod_dir.mkdir()
        (mod_dir / "module.py").write_text(
            'MODULE_INFO = {\n'
            '    "name": "test_mod",\n'
            '    "display": "测试模块",\n'
            '    "version": "1.0",\n'
            '    "description": "仅用于测试",\n'
            '    "reload_mode": "hot",\n'
            '    "dependencies": [],\n'
            '    "config_key": "test_mod",\n'
            '}\n',
            encoding="utf-8",
        )

        # 创建一个不带 MODULE_INFO 的模块（应该被跳过）
        empty_dir = self.src_dir / "empty_mod"
        empty_dir.mkdir()
        (empty_dir / "module.py").write_text("# no MODULE_INFO\n", encoding="utf-8")

        # 创建一个没有 module.py 的目录（应该被跳过）
        no_file_dir = self.src_dir / "no_module_py"
        no_file_dir.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_discover_finds_module_with_info(self):
        from src.core.module_loader import ModuleLoader
        loader = ModuleLoader(src_root=str(self.src_dir), config={})
        modules = loader.discover()
        self.assertIn("test_mod", modules)
        self.assertEqual(modules["test_mod"].display, "测试模块")
        self.assertEqual(modules["test_mod"].version, "1.0")

    def test_discover_skips_empty_module(self):
        from src.core.module_loader import ModuleLoader
        loader = ModuleLoader(src_root=str(self.src_dir), config={})
        modules = loader.discover()
        self.assertNotIn("empty_mod", modules)

    def test_discover_skips_no_module_py(self):
        from src.core.module_loader import ModuleLoader
        loader = ModuleLoader(src_root=str(self.src_dir), config={})
        modules = loader.discover()
        self.assertNotIn("no_module_py", modules)

    def test_config_enabled_state(self):
        from src.core.module_loader import ModuleLoader
        config = {"test_mod": {"enabled": True}}
        loader = ModuleLoader(src_root=str(self.src_dir), config=config)
        modules = loader.discover()
        self.assertTrue(modules["test_mod"].enabled)

    def test_config_disabled_state(self):
        from src.core.module_loader import ModuleLoader
        config = {"test_mod": {"enabled": False}}
        loader = ModuleLoader(src_root=str(self.src_dir), config=config)
        modules = loader.discover()
        self.assertFalse(modules["test_mod"].enabled)

    def test_reload_mode_parsing(self):
        from src.core.module_loader import ModuleLoader, ReloadMode
        loader = ModuleLoader(src_root=str(self.src_dir), config={})
        modules = loader.discover()
        self.assertEqual(modules["test_mod"].reload_mode, ReloadMode.HOT)

    def test_to_dict(self):
        from src.core.module_loader import ModuleLoader
        loader = ModuleLoader(src_root=str(self.src_dir), config={})
        modules = loader.discover()
        d = modules["test_mod"].to_dict()
        self.assertEqual(d["name"], "test_mod")
        self.assertEqual(d["reload_mode"], "hot")
        self.assertIn("display", d)

    def test_update_enabled_state(self):
        from src.core.module_loader import ModuleLoader
        loader = ModuleLoader(src_root=str(self.src_dir), config={})
        modules = loader.discover()
        loader.update_enabled_state("test_mod", True)
        self.assertTrue(loader.get_module("test_mod").enabled)

    def test_sync_config(self):
        from src.core.module_loader import ModuleLoader
        loader = ModuleLoader(src_root=str(self.src_dir), config={})
        loader.discover()
        loader.sync_config({"test_mod": {"enabled": True}})
        self.assertTrue(loader.get_module("test_mod").enabled)

    def test_discover_only_once(self):
        from src.core.module_loader import ModuleLoader
        loader = ModuleLoader(src_root=str(self.src_dir), config={})
        loader.discover()
        # 第二次调用应返回缓存结果
        modules2 = loader.discover()
        self.assertIn("test_mod", modules2)


class TestHeartbeatCollector(unittest.TestCase):
    """心跳收集器测试"""

    def setUp(self):
        from src.core.heartbeat import HeartbeatCollector
        HeartbeatCollector.reset_instance()

    def tearDown(self):
        from src.core.heartbeat import HeartbeatCollector
        HeartbeatCollector.reset_instance()

    def test_beat_and_get(self):
        from src.core.heartbeat import HeartbeatCollector, ModuleStatus
        collector = HeartbeatCollector(timeout=60.0)
        collector.beat("test_mod", status=ModuleStatus.RUNNING, message="正常")
        status = collector.get_status("test_mod")
        self.assertIsNotNone(status)
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["message"], "正常")

    def test_register_stopped(self):
        from src.core.heartbeat import HeartbeatCollector, ModuleStatus
        collector = HeartbeatCollector(timeout=60.0)
        collector.register_stopped("stopped_mod", "未启用")
        status = collector.get_status("stopped_mod")
        self.assertEqual(status["status"], "stopped")

    def test_get_all_status(self):
        from src.core.heartbeat import HeartbeatCollector, ModuleStatus
        collector = HeartbeatCollector(timeout=60.0)
        collector.beat("mod_a", status=ModuleStatus.RUNNING)
        collector.beat("mod_b", status=ModuleStatus.IDLE)
        all_status = collector.get_all_status()
        self.assertIn("mod_a", all_status)
        self.assertIn("mod_b", all_status)

    def test_timeout_marks_stopped(self):
        from src.core.heartbeat import HeartbeatCollector, ModuleStatus
        # 超短超时
        collector = HeartbeatCollector(timeout=0.01)
        collector.beat("mod_c", status=ModuleStatus.RUNNING)
        time.sleep(0.05)
        status = collector.get_status("mod_c")
        self.assertEqual(status["status"], "stopped")

    def test_get_nonexistent(self):
        from src.core.heartbeat import HeartbeatCollector
        collector = HeartbeatCollector(timeout=60.0)
        self.assertIsNone(collector.get_status("no_such_mod"))

    def test_remove_module(self):
        from src.core.heartbeat import HeartbeatCollector, ModuleStatus
        collector = HeartbeatCollector(timeout=60.0)
        collector.beat("to_remove", status=ModuleStatus.RUNNING)
        collector.remove_module("to_remove")
        self.assertIsNone(collector.get_status("to_remove"))

    def test_callback(self):
        from src.core.heartbeat import HeartbeatCollector, ModuleStatus
        collector = HeartbeatCollector(timeout=60.0)
        received = []
        collector.on_heartbeat(lambda name, data: received.append((name, data.status)))
        collector.beat("cb_mod", status=ModuleStatus.RUNNING)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "cb_mod")


class TestHeartbeatReporter(unittest.TestCase):
    """心跳上报器测试"""

    def setUp(self):
        from src.core.heartbeat import HeartbeatCollector
        HeartbeatCollector.reset_instance()

    def tearDown(self):
        from src.core.heartbeat import HeartbeatCollector
        HeartbeatCollector.reset_instance()

    def test_manual_tick(self):
        from src.core.heartbeat import HeartbeatCollector, HeartbeatReporter, ModuleStatus
        collector = HeartbeatCollector(timeout=60.0)
        reporter = HeartbeatReporter("test", interval=999, collector=collector)
        reporter.update(status=ModuleStatus.RUNNING, message="测试中")
        reporter.tick()
        status = collector.get_status("test")
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["message"], "测试中")

    def test_stop(self):
        from src.core.heartbeat import HeartbeatCollector, HeartbeatReporter, ModuleStatus
        collector = HeartbeatCollector(timeout=60.0)
        reporter = HeartbeatReporter("test", interval=999, collector=collector)
        reporter.start(status=ModuleStatus.RUNNING)
        reporter.stop()
        status = collector.get_status("test")
        self.assertEqual(status["status"], "stopped")


class TestConfigManager(unittest.TestCase):
    """配置版本管理器测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = Path(self.tmpdir) / "config.yaml"

        # 写入一个合法的初始配置
        import yaml
        initial_config = {
            "remote": {"enabled": False, "host": "0.0.0.0", "port": 8765},
            "screen": {"enabled": False, "ocr_language": "chi_sim+eng"},
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(initial_config, f, allow_unicode=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_read_config(self):
        from src.admin.config_manager import ConfigManager
        mgr = ConfigManager(config_path=str(self.config_path))
        config = mgr.read()
        self.assertFalse(config["remote"]["enabled"])

    def test_write_config(self):
        from src.admin.config_manager import ConfigManager
        mgr = ConfigManager(config_path=str(self.config_path))
        config = mgr.read()
        config["remote"]["enabled"] = True
        result = mgr.write(config, operator="test")
        self.assertTrue(result["success"])

        # 重新读取验证
        config2 = mgr.read(use_cache=False)
        self.assertTrue(config2["remote"]["enabled"])

    def test_validate_valid_config(self):
        from src.admin.config_manager import ConfigManager
        mgr = ConfigManager(config_path=str(self.config_path))
        config = mgr.read()
        errors = mgr.validate(config)
        self.assertEqual(len(errors), 0)

    def test_validate_invalid_port(self):
        from src.admin.config_manager import ConfigManager
        mgr = ConfigManager(config_path=str(self.config_path))
        config = mgr.read()
        config["remote"]["port"] = 999999
        errors = mgr.validate(config)
        self.assertTrue(any("port" in e for e in errors))

    def test_validate_negative_timeout(self):
        from src.admin.config_manager import ConfigManager
        mgr = ConfigManager(config_path=str(self.config_path))
        config = mgr.read()
        config["screen"]["capture_timeout"] = -1
        errors = mgr.validate(config)
        self.assertTrue(any("capture_timeout" in e for e in errors))

    def test_validate_type_error(self):
        from src.admin.config_manager import ConfigManager
        mgr = ConfigManager(config_path=str(self.config_path))
        config = mgr.read()
        config["remote"]["enabled"] = "not_a_bool"
        errors = mgr.validate(config)
        self.assertTrue(any("enabled" in e and "类型错误" in e for e in errors))

    def test_toggle_module(self):
        from src.admin.config_manager import ConfigManager
        mgr = ConfigManager(config_path=str(self.config_path))
        result = mgr.toggle_module("remote", True, operator="test")
        self.assertTrue(result["success"])

        config = mgr.read(use_cache=False)
        self.assertTrue(config["remote"]["enabled"])

    def test_backup_created_on_write(self):
        from src.admin.config_manager import ConfigManager
        mgr = ConfigManager(config_path=str(self.config_path))
        config = mgr.read()
        result = mgr.write(config, operator="test")
        self.assertTrue(result["success"])
        self.assertTrue(result["backup_file"])

    def test_rollback(self):
        from src.admin.config_manager import ConfigManager
        mgr = ConfigManager(config_path=str(self.config_path))

        # 先写一次，产生备份
        config = mgr.read()
        config["remote"]["enabled"] = True
        result = mgr.write(config, operator="test")
        self.assertTrue(result["success"])

        # 获取备份列表（按时间倒序）
        backups = mgr.list_backups()
        self.assertTrue(len(backups) > 0)

        # 回滚到最后一个备份（最旧的，即 write 之前的 enabled=False 版本）
        # backups[0] 是最新的（rollback 自动创建的或最近的）
        # 取最后一个（最旧）才是 write 前的原始配置
        rollback_result = mgr.rollback(backups[-1]["filename"], operator="test")
        self.assertTrue(rollback_result["success"])

        # 验证已回滚
        config2 = mgr.read(use_cache=False)
        self.assertFalse(config2["remote"]["enabled"])

    def test_update_section(self):
        from src.admin.config_manager import ConfigManager
        mgr = ConfigManager(config_path=str(self.config_path))
        result = mgr.update_section("screen", {"ocr_language": "eng"}, operator="test")
        self.assertTrue(result["success"])

        config = mgr.read(use_cache=False)
        self.assertEqual(config["screen"]["ocr_language"], "eng")


class TestAuditLogger(unittest.TestCase):
    """审计日志测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from src.admin.audit import AuditLogger
        AuditLogger.reset_instance()

    def tearDown(self):
        from src.admin.audit import AuditLogger
        AuditLogger.reset_instance()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_record_and_query(self):
        from src.admin.audit import AuditLogger, AuditEventType
        logger = AuditLogger(log_dir=self.tmpdir)

        entry = logger.record(
            event_type=AuditEventType.MODULE_TOGGLE,
            operator="admin",
            target="screen",
            details={"enabled": True},
        )
        self.assertEqual(entry["event_type"], "module_toggle")
        self.assertEqual(entry["operator"], "admin")

        # 查询
        results = logger.query(event_type="module_toggle")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["target"], "screen")

    def test_query_by_operator(self):
        from src.admin.audit import AuditLogger
        logger = AuditLogger(log_dir=self.tmpdir)

        logger.record(event_type="test", operator="alice")
        logger.record(event_type="test", operator="bob")

        results = logger.query(operator="alice")
        self.assertEqual(len(results), 1)

    def test_query_by_target(self):
        from src.admin.audit import AuditLogger
        logger = AuditLogger(log_dir=self.tmpdir)

        logger.record(event_type="test", target="remote")
        logger.record(event_type="test", target="screen")

        results = logger.query(target="screen")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["target"], "screen")

    def test_get_summary(self):
        from src.admin.audit import AuditLogger, AuditEventType
        logger = AuditLogger(log_dir=self.tmpdir)

        logger.record(event_type=AuditEventType.MODULE_TOGGLE, operator="admin", target="screen")
        logger.record(event_type=AuditEventType.CONFIG_UPDATE, operator="admin", target="remote")
        logger.record(event_type=AuditEventType.MODULE_TOGGLE, operator="admin", target="control",
                       result="failure", error="模块未加载")

        summary = logger.get_summary(hours=1)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["by_type"]["module_toggle"], 2)
        self.assertEqual(summary["by_result"]["failure"], 1)

    def test_record_failure(self):
        from src.admin.audit import AuditLogger
        logger = AuditLogger(log_dir=self.tmpdir)

        entry = logger.record(
            event_type="test",
            result="failure",
            error="出错了",
        )
        self.assertEqual(entry["result"], "failure")
        self.assertEqual(entry["error"], "出错了")


if __name__ == "__main__":
    unittest.main()
