"""
Phase 1 — 羽依控制中心 (Yuyi Console) API 单元测试

覆盖：
- Dashboard 聚合数据（含模块依赖、羽依核心状态、角色状态）
- Character API（角色状态接口）
- 模块管理 CRUD（含依赖关系）
- Mock Mode（?mock=true 独立模式）
- 配置管理（读写/历史/回滚）
- 审计日志查询
- Schema 获取
- 快捷操作
- 静态页面服务
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


_PROJECT_ROOT = Path(__file__).parent.parent


class TestAdminDashboardAPI(unittest.TestCase):
    """Dashboard 聚合接口测试"""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls._orig_cwd = os.getcwd()
        os.chdir(str(_PROJECT_ROOT))

        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin

        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls._orig_cwd)

    def test_dashboard_returns_ok(self):
        """GET /admin/api/dashboard → 200"""
        resp = self.client.get("/admin/api/dashboard")
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_structure(self):
        """Dashboard 返回必要字段"""
        resp = self.client.get("/admin/api/dashboard")
        data = resp.get_json()
        self.assertIn("uptime", data)
        self.assertIn("health", data)
        self.assertIn("modules", data)
        self.assertIn("yuyi", data)
        self.assertIn("timestamp", data)

    def test_dashboard_modules_count(self):
        """Dashboard 应包含模块列表（至少 0 个）"""
        resp = self.client.get("/admin/api/dashboard")
        data = resp.get_json()
        self.assertIsInstance(data["modules"], list)
        self.assertGreaterEqual(len(data["modules"]), 0)

    def test_dashboard_module_structure(self):
        """每个模块应有必要字段（含依赖）"""
        resp = self.client.get("/admin/api/dashboard")
        data = resp.get_json()
        if data["modules"]:
            m = data["modules"][0]
            for key in ("name", "display", "version", "reload_mode", "enabled",
                         "dependencies", "config_key", "error"):
                self.assertIn(key, m, f"模块缺少字段: {key}")

    def test_dashboard_module_dependencies_format(self):
        """模块依赖关系格式正确"""
        resp = self.client.get("/admin/api/dashboard")
        data = resp.get_json()
        for m in data["modules"]:
            self.assertIsInstance(m["dependencies"], list)
            for dep in m["dependencies"]:
                self.assertIn("name", dep)
                self.assertIn("satisfied", dep)
                self.assertIsInstance(dep["satisfied"], bool)

    def test_dashboard_health_structure(self):
        """健康数据结构"""
        resp = self.client.get("/admin/api/dashboard")
        health = resp.get_json()["health"]
        self.assertIn("status", health)
        self.assertIn("score", health)
        self.assertIn("cpu_percent", health)
        self.assertIn("memory_mb", health)

    def test_dashboard_yuyi_structure(self):
        """羽依状态结构（含核心状态和角色状态）"""
        resp = self.client.get("/admin/api/dashboard")
        yuyi = resp.get_json()["yuyi"]
        self.assertIn("greeting", yuyi)
        self.assertIn("personality", yuyi)
        self.assertIn("emotion", yuyi)
        self.assertIn("core", yuyi)
        self.assertIn("character", yuyi)

    def test_dashboard_yuyi_core_structure(self):
        """羽依核心生命状态结构"""
        resp = self.client.get("/admin/api/dashboard")
        core = resp.get_json()["yuyi"]["core"]
        self.assertIn("thinking", core)
        self.assertIn("memory", core)
        self.assertIn("emotion_stability", core)
        self.assertIn("growth", core)
        # thinking
        self.assertIn("status", core["thinking"])
        self.assertIn("detail", core["thinking"])
        # memory
        self.assertIn("usage", core["memory"])
        self.assertIn("total", core["memory"])
        self.assertIn("percent", core["memory"])
        # growth
        self.assertIn("stage", core["growth"])
        self.assertIn("label", core["growth"])

    def test_dashboard_yuyi_character_structure(self):
        """羽依角色状态结构"""
        resp = self.client.get("/admin/api/dashboard")
        char = resp.get_json()["yuyi"]["character"]
        self.assertIn("emotion", char)
        self.assertIn("expression", char)
        self.assertIn("message", char)
        self.assertIn("energy", char)
        self.assertIsInstance(char["energy"], int)
        self.assertGreaterEqual(char["energy"], 0)
        self.assertLessEqual(char["energy"], 100)

    def test_dashboard_greeting_is_string(self):
        """问候语为非空字符串"""
        resp = self.client.get("/admin/api/dashboard")
        greeting = resp.get_json()["yuyi"]["greeting"]
        self.assertIsInstance(greeting, str)
        self.assertGreater(len(greeting), 0)

    def test_dashboard_uptime_format(self):
        """运行时长为可读字符串"""
        resp = self.client.get("/admin/api/dashboard")
        uptime = resp.get_json()["uptime"]
        self.assertIsInstance(uptime, str)
        self.assertGreater(len(uptime), 0)


class TestAdminModulesAPI(unittest.TestCase):
    """模块管理接口测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_list_modules(self):
        """GET /admin/api/modules → 200"""
        resp = self.client.get("/admin/api/modules")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("modules", data)
        self.assertIn("total", data)

    def test_start_unknown_module_returns_404(self):
        """启动不存在的模块 → 404"""
        resp = self.client.post("/admin/api/module/__nonexistent__/start")
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertFalse(data.get("success", True))

    def test_stop_unknown_module_returns_404(self):
        """停止不存在的模块 → 404"""
        resp = self.client.post("/admin/api/module/__nonexistent__/stop")
        self.assertEqual(resp.status_code, 404)

    def test_reload_unknown_module_returns_404(self):
        """重载不存在的模块 → 404"""
        resp = self.client.post("/admin/api/module/__nonexistent__/reload")
        self.assertEqual(resp.status_code, 404)

    def test_start_valid_module(self):
        """启动有效模块 → 成功"""
        resp = self.client.post("/admin/api/module/control/start")
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data.get("success"))

    def test_stop_valid_module(self):
        """停止有效模块 → 成功"""
        self.client.post("/admin/api/module/control/start")
        resp = self.client.post("/admin/api/module/control/stop")
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data.get("success"))

    def test_reload_valid_module(self):
        """重载有效模块 → 成功"""
        resp = self.client.post("/admin/api/module/control/reload")
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data.get("success"))

    def test_start_all_modules_and_stop(self):
        """遍历所有模块启停"""
        list_resp = self.client.get("/admin/api/modules")
        modules = list_resp.get_json()["modules"]
        self.assertGreater(len(modules), 0)

        for mod in modules:
            name = mod["name"]
            with self.subTest(module=name):
                resp = self.client.post(f"/admin/api/module/{name}/start")
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.get_json().get("success"))

                resp = self.client.post(f"/admin/api/module/{name}/stop")
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.get_json().get("success"))


class TestAdminLogsAPI(unittest.TestCase):
    """日志接口测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_logs_returns_ok(self):
        """GET /admin/api/logs → 200"""
        resp = self.client.get("/admin/api/logs")
        self.assertEqual(resp.status_code, 200)

    def test_logs_structure(self):
        """日志返回结构"""
        resp = self.client.get("/admin/api/logs")
        data = resp.get_json()
        self.assertIn("logs", data)
        self.assertIn("total", data)
        self.assertIsInstance(data["logs"], list)

    def test_logs_with_filter(self):
        """带过滤器的日志请求"""
        resp = self.client.get("/admin/api/logs?module=test")
        self.assertEqual(resp.status_code, 200)

    def test_logs_with_custom_lines(self):
        """自定义行数限制"""
        resp = self.client.get("/admin/api/logs?lines=10")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertLessEqual(len(data["logs"]), 10)


class TestAdminConfigAPI(unittest.TestCase):
    """配置管理接口测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_get_config(self):
        """GET /admin/api/config → 200"""
        resp = self.client.get("/admin/api/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("config", data)

    def test_get_config_history(self):
        """GET /admin/api/config/history → 200"""
        resp = self.client.get("/admin/api/config/history")
        self.assertEqual(resp.status_code, 200)

    def test_put_config_without_body(self):
        """PUT /admin/api/config 无 body → 400"""
        resp = self.client.put("/admin/api/config", json={})
        self.assertEqual(resp.status_code, 400)

    def test_put_config_valid(self):
        """PUT /admin/api/config 有效数据"""
        resp = self.client.put("/admin/api/config", json={
            "config": {"test": True},
            "operator": "unit_test",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("success", data)

    def test_rollback_without_filename(self):
        """POST /admin/api/config/rollback 无 filename → 400"""
        resp = self.client.post("/admin/api/config/rollback", json={})
        self.assertEqual(resp.status_code, 400)


class TestAdminAuditAPI(unittest.TestCase):
    """审计日志接口测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_get_audit_logs(self):
        """GET /admin/api/audit → 200"""
        resp = self.client.get("/admin/api/audit")
        self.assertEqual(resp.status_code, 200)

    def test_get_audit_with_filter(self):
        """带过滤的审计查询"""
        resp = self.client.get("/admin/api/audit?event_type=MODULE_START")
        self.assertEqual(resp.status_code, 200)

    def test_get_audit_with_limit(self):
        """带 limit 的审计查询"""
        resp = self.client.get("/admin/api/audit?limit=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("logs", data)
        self.assertIn("summary", data)


class TestAdminSchemaAPI(unittest.TestCase):
    """Schema 接口测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_get_schema(self):
        """GET /admin/api/schema → 200"""
        resp = self.client.get("/admin/api/schema")
        self.assertEqual(resp.status_code, 200)


class TestAdminActionsAPI(unittest.TestCase):
    """快捷操作接口测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_reload_config(self):
        """POST /admin/api/action/reload_config"""
        resp = self.client.post("/admin/api/action/reload_config")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))

    def test_save_backup(self):
        """POST /admin/api/action/save_backup"""
        resp = self.client.post("/admin/api/action/save_backup")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))

    def test_system_check(self):
        """POST /admin/api/action/system_check"""
        resp = self.client.post("/admin/api/action/system_check")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertIn("health", data)

    def test_clear_cache(self):
        """POST /admin/api/action/clear_cache"""
        resp = self.client.post("/admin/api/action/clear_cache")
        self.assertEqual(resp.status_code, 200)

    def test_unknown_action(self):
        """未知操作 → 400"""
        resp = self.client.post("/admin/api/action/__unknown__")
        self.assertEqual(resp.status_code, 400)


class TestAdminStaticFiles(unittest.TestCase):
    """静态文件服务测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_index_page(self):
        """GET /admin/ → index.html"""
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode("utf-8")
        self.assertIn("羽依", html)

    def test_index_no_slash(self):
        """GET /admin → 应返回 index.html"""
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)

    def test_css_file(self):
        """GET /admin/css/style.css → 200"""
        resp = self.client.get("/admin/css/style.css")
        self.assertEqual(resp.status_code, 200)
        css = resp.data.decode("utf-8").lower()
        self.assertIn("glass", css)

    def test_js_app_file(self):
        """GET /admin/js/app.js → 200"""
        resp = self.client.get("/admin/js/app.js")
        self.assertEqual(resp.status_code, 200)

    def test_js_dashboard_file(self):
        """GET /admin/js/dashboard.js → 200"""
        resp = self.client.get("/admin/js/dashboard.js")
        self.assertEqual(resp.status_code, 200)

    def test_unknown_static_returns_404(self):
        """不存在的静态文件 → 404"""
        resp = self.client.get("/admin/css/__nonexistent__.css")
        self.assertEqual(resp.status_code, 404)


class TestAdminEndpointsIntegration(unittest.TestCase):
    """端到端集成测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_full_workflow(self):
        """完整工作流：查看→启动→刷新→停止"""
        # 1. 获取初始状态
        resp = self.client.get("/admin/api/dashboard")
        self.assertEqual(resp.status_code, 200)
        initial_modules = resp.get_json()["modules"]

        # 2. 启动第一个模块
        if initial_modules:
            first = initial_modules[0]["name"]
            resp = self.client.post(f"/admin/api/module/{first}/start")
            self.assertEqual(resp.status_code, 200)

            # 3. 刷新确认
            resp = self.client.get("/admin/api/dashboard")
            self.assertEqual(resp.status_code, 200)

            # 4. 停止模块
            resp = self.client.post(f"/admin/api/module/{first}/stop")
            self.assertEqual(resp.status_code, 200)

            # 5. 最终刷新
            resp = self.client.get("/admin/api/dashboard")
            self.assertEqual(resp.status_code, 200)

    def test_dashboard_reflects_module_changes(self):
        """启停操作应反映在 Dashboard 中"""
        resp = self.client.get("/admin/api/modules")
        modules = resp.get_json()["modules"]
        if not modules:
            self.skipTest("无可用模块")

        name = modules[0]["name"]
        resp = self.client.post(f"/admin/api/module/{name}/start")
        self.assertTrue(resp.get_json().get("success"))

        resp = self.client.get("/admin/api/modules")
        updated = next((m for m in resp.get_json()["modules"] if m["name"] == name), None)
        self.assertIsNotNone(updated)
        self.assertTrue(updated["enabled"])

        resp = self.client.post(f"/admin/api/module/{name}/stop")
        self.assertTrue(resp.get_json().get("success"))

        resp = self.client.get("/admin/api/modules")
        updated = next((m for m in resp.get_json()["modules"] if m["name"] == name), None)
        self.assertIsNotNone(updated)
        self.assertFalse(updated["enabled"])


class TestAdminCharacterAPI(unittest.TestCase):
    """Character API 测试 — 角色状态接口"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_character_returns_ok(self):
        """GET /admin/api/character → 200"""
        resp = self.client.get("/admin/api/character")
        self.assertEqual(resp.status_code, 200)

    def test_character_structure(self):
        """角色状态包含必要字段"""
        resp = self.client.get("/admin/api/character")
        data = resp.get_json()
        self.assertIn("emotion", data)
        self.assertIn("expression", data)
        self.assertIn("message", data)
        self.assertIn("energy", data)

    def test_character_expression_is_valid(self):
        """表情值在预定义集合中"""
        resp = self.client.get("/admin/api/character")
        expression = resp.get_json()["expression"]
        valid = {"smile", "happy", "excited", "sad", "angry",
                 "surprised", "fearful", "worried"}
        self.assertIn(expression, valid)

    def test_character_energy_range(self):
        """能量值在 0-100"""
        resp = self.client.get("/admin/api/character")
        energy = resp.get_json()["energy"]
        self.assertGreaterEqual(energy, 0)
        self.assertLessEqual(energy, 100)

    def test_character_message_not_empty(self):
        """角色消息非空"""
        resp = self.client.get("/admin/api/character")
        message = resp.get_json()["message"]
        self.assertIsInstance(message, str)
        self.assertGreater(len(message), 0)


class TestAdminMockMode(unittest.TestCase):
    """Mock Mode 测试 — ?mock=true 独立模式"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_mock_dashboard(self):
        """GET /admin/api/dashboard?mock=true → 200, mock=True"""
        resp = self.client.get("/admin/api/dashboard?mock=true")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("mock"))

    def test_mock_character(self):
        """GET /admin/api/character?mock=true → 200, mock=True"""
        resp = self.client.get("/admin/api/character?mock=true")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("mock"))

    def test_mock_dashboard_has_modules(self):
        """Mock Dashboard 包含模拟模块"""
        resp = self.client.get("/admin/api/dashboard?mock=true")
        data = resp.get_json()
        self.assertGreater(len(data["modules"]), 0)
        self.assertEqual(data["modules"][0]["name"], "mock_mod")

    def test_mock_dashboard_has_core(self):
        """Mock Dashboard 包含核心状态"""
        resp = self.client.get("/admin/api/dashboard?mock=true")
        core = resp.get_json()["yuyi"]["core"]
        self.assertTrue(core.get("mock"))

    def test_mock_dashboard_has_character(self):
        """Mock Dashboard 包含角色状态"""
        resp = self.client.get("/admin/api/dashboard?mock=true")
        char = resp.get_json()["yuyi"]["character"]
        self.assertTrue(char.get("mock"))

    def test_normal_dashboard_no_mock_flag(self):
        """正常请求不带 mock 标记"""
        resp = self.client.get("/admin/api/dashboard")
        data = resp.get_json()
        self.assertNotIn("mock", data)

    def test_mock_false_is_normal(self):
        """?mock=false 等同于正常请求"""
        resp = self.client.get("/admin/api/dashboard?mock=false")
        data = resp.get_json()
        self.assertNotIn("mock", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
