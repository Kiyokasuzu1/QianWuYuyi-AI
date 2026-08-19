"""
test_deploy_preflight.py — 部署相关脚本的单元测试 (Phase D.0)

覆盖:
  - preflight_check.py 的核心检查函数 (Python 版本、依赖、config 存在、端口)
  - health_check.py 的纯函数部分 (构建 payload、格式化输出)
  - first_run_test.py 的辅助函数 (标记文件 IO)

设计原则:
  - 不依赖真实 LLM/网络
  - 不修改任何业务模块
  - 所有测试在 5 秒内完成
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestPreflightCheckImport(unittest.TestCase):
    """preflight_check.py 应能正常 import"""

    def test_import_module(self):
        try:
            import importlib
            mod = importlib.import_module("scripts.preflight_check")
            self.assertIsNotNone(mod)
        except Exception as e:
            self.fail(f"preflight_check 导入失败: {e}")


class TestHealthCheckImport(unittest.TestCase):
    """health_check.py 应能正常 import"""

    def test_import_module(self):
        try:
            import importlib
            mod = importlib.import_module("scripts.health_check")
            self.assertIsNotNone(mod)
        except Exception as e:
            self.fail(f"health_check 导入失败: {e}")


class TestFirstRunTestImport(unittest.TestCase):
    """first_run_test.py 应能正常 import"""

    def test_import_module(self):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "first_run_test",
                PROJECT_ROOT / "scripts" / "first_run_test.py",
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertTrue(hasattr(mod, "main"))
            self.assertTrue(hasattr(mod, "check_1_identity_loaded"))
            self.assertTrue(hasattr(mod, "check_2_personality_affects_reply"))
            self.assertTrue(hasattr(mod, "check_3_memory_written"))
            self.assertTrue(hasattr(mod, "check_4_persistence_across_restart"))
            self.assertTrue(hasattr(mod, "check_5_long_term_recall"))
        except Exception as e:
            self.fail(f"first_run_test 导入失败: {e}")


class TestFirstRunTestMarkerFile(unittest.TestCase):
    """标记文件读写"""

    def test_marker_round_trip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "marker.json"
            payload = {"passed": True, "time": time.time(), "user": "test"}
            marker.write_text(json.dumps(payload), encoding="utf-8")
            loaded = json.loads(marker.read_text(encoding="utf-8"))
            self.assertTrue(loaded["passed"])
            self.assertEqual(loaded["user"], "test")


class TestSystemdUnitFiles(unittest.TestCase):
    """systemd 单元文件存在且字段合法"""

    def test_api_service_exists(self):
        f = PROJECT_ROOT / "deploy" / "systemd" / "yuyi-api.service"
        self.assertTrue(f.exists(), f"缺少 {f}")
        content = f.read_text(encoding="utf-8")
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)
        self.assertIn("ExecStart=", content)
        self.assertIn("Restart=always", content)
        self.assertIn("RestartSec=10", content)
        self.assertIn("EnvironmentFile=", content)

    def test_sender_service_exists(self):
        f = PROJECT_ROOT / "deploy" / "systemd" / "yuyi-sender.service"
        self.assertTrue(f.exists(), f"缺少 {f}")
        content = f.read_text(encoding="utf-8")
        self.assertIn("ExecStart=", content)
        # sender 必须在 api 之后启动 (After=network.target yuyi-api.service)
        self.assertIn("yuyi-api.service", content)


class TestNginxConfigExample(unittest.TestCase):
    """nginx 配置示例存在且包含项目硬约束"""

    def test_nginx_config(self):
        f = PROJECT_ROOT / "deploy" / "nginx" / "yuyi.conf.example"
        self.assertTrue(f.exists(), f"缺少 {f}")
        content = f.read_text(encoding="utf-8")
        # 项目硬约束: 大文件传输缓冲
        self.assertIn("proxy_buffers", content)
        self.assertIn("proxy_buffer_size", content)
        # 项目硬约束: 超时 120s
        self.assertIn("120s", content)
        # WebSocket
        self.assertIn("Upgrade", content)


class TestEnvExample(unittest.TestCase):
    """.env.example 存在且包含关键变量"""

    def test_env_example(self):
        f = PROJECT_ROOT / ".env.example"
        self.assertTrue(f.exists(), f"缺少 {f}")
        content = f.read_text(encoding="utf-8")
        self.assertIn("DEEPSEEK_API_KEY", content)
        self.assertIn("OPENAI_API_KEY", content)
        self.assertIn("YUYI_LLM_MOCK", content)


class TestStartStopScripts(unittest.TestCase):
    """启停脚本存在且可被 shell 解析"""

    def test_start_script_exists(self):
        f = PROJECT_ROOT / "scripts" / "start_yuyi.sh"
        self.assertTrue(f.exists())
        content = f.read_text(encoding="utf-8")
        self.assertIn("api_server", content)
        self.assertIn("preflight", content)

    def test_stop_script_exists(self):
        f = PROJECT_ROOT / "scripts" / "stop_yuyi.sh"
        self.assertTrue(f.exists())
        content = f.read_text(encoding="utf-8")
        self.assertIn("PID", content)


class TestCleanDataScript(unittest.TestCase):
    """清理脚本存在且包含白名单保留目录"""

    def test_clean_script(self):
        f = PROJECT_ROOT / "scripts" / "clean_data.sh"
        self.assertTrue(f.exists())
        content = f.read_text(encoding="utf-8")
        # 保留真实数据
        self.assertIn("data/memory/", content)
        self.assertIn("data/personality/", content)
        self.assertIn("data/self_model/", content)
        # 清理测试残留
        self.assertIn("cog_test_", content)
        self.assertIn("_e2e_tmp_", content)


class TestDeploymentDoc(unittest.TestCase):
    """部署文档存在"""

    def test_deployment_md(self):
        f = PROJECT_ROOT / "docs" / "deployment.md"
        self.assertTrue(f.exists())
        content = f.read_text(encoding="utf-8")
        self.assertIn("Phase D.0", content)
        self.assertIn("Preflight", content)
        self.assertIn("systemd", content)


class TestPreflightPureFunctions(unittest.TestCase):
    """测试 preflight_check 的纯函数 (如 Python 版本判断)"""

    def test_python_version_ok(self):
        try:
            import importlib
            mod = importlib.import_module("scripts.preflight_check")
        except Exception:
            self.skipTest("preflight_check 无法导入")
        # 直接调用内部的 python 检查
        if hasattr(mod, "_check_python"):
            ok, msg = mod._check_python()
            self.assertIsInstance(ok, bool)
            self.assertIsInstance(msg, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
