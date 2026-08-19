"""
Phase 2: Yuyi Cognitive Dashboard 测试

覆盖：
- 前端文件（yuyi-cognitive.js）结构与导出
- app.js / index.html 整合
- 后端认知 API（mind / memory / growth / relationship）
- Mock 数据返回结构
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_PROJECT_ROOT = Path(__file__).parent.parent
_STATIC_ADMIN = _PROJECT_ROOT / "static" / "admin"


class TestCognitiveFrontendFiles(unittest.TestCase):
    """前端文件存在性与结构测试"""

    def test_cognitive_js_exists(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        self.assertTrue(path.exists(), f"文件不存在: {path}")

    def test_cognitive_js_exports_window(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("window.YuyiCognitive", content)

    def test_cognitive_js_has_loadAll(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("loadAll", content)

    def test_cognitive_js_has_renderMind(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("renderMind", content)

    def test_cognitive_js_has_renderMemory(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("renderMemory", content)

    def test_cognitive_js_has_renderGrowth(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("renderGrowth", content)

    def test_cognitive_js_has_renderRelationship(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("renderRelationship", content)

    def test_cognitive_js_has_handleProposal(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("handleProposal", content)
        self.assertIn("window.handleProposal", content)

    def test_cognitive_js_has_fetchCognitive(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("fetchCognitive", content)
        self.assertIn("/admin/api/cognitive", content)

    def test_cognitive_js_has_emotion_chart(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("emotion-chart", content)

    def test_cognitive_js_has_attention_display(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("attention-display", content)

    def test_cognitive_js_has_memory_identity(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("memory-identity", content)

    def test_cognitive_js_has_growth_stage(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("growth-stage", content)

    def test_cognitive_js_has_trust_gauge(self):
        path = _STATIC_ADMIN / "js" / "yuyi-cognitive.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("trust-gauge", content)


class TestCognitiveIntegration(unittest.TestCase):
    """整合测试：app.js 与 index.html"""

    def test_app_js_has_cognitive_view(self):
        path = _STATIC_ADMIN / "js" / "app.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("showCognitiveView", content)
        self.assertIn("createCognitiveView", content)
        self.assertIn("cognitive", content)

    def test_app_js_switchView_handles_cognitive(self):
        path = _STATIC_ADMIN / "js" / "app.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("view === 'cognitive'", content)

    def test_app_js_calls_YuyiCognitive_loadAll(self):
        path = _STATIC_ADMIN / "js" / "app.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("YuyiCognitive.loadAll", content)

    def test_index_html_includes_cognitive_script(self):
        path = _STATIC_ADMIN / "index.html"
        content = path.read_text(encoding="utf-8")
        self.assertIn("yuyi-cognitive.js", content)

    def test_index_html_has_cognitive_nav(self):
        path = _STATIC_ADMIN / "index.html"
        content = path.read_text(encoding="utf-8")
        self.assertIn("data-view=\"cognitive\"", content)
        self.assertIn("switchView('cognitive')", content)

    def test_css_has_cognitive_styles(self):
        path = _STATIC_ADMIN / "css" / "style.css"
        content = path.read_text(encoding="utf-8")
        self.assertIn(".cognitive-view", content)
        self.assertIn(".mind-grid", content)
        self.assertIn(".gauge-ring", content)
        self.assertIn(".relation-gauge", content)
        self.assertIn(".growth-stage-card", content)


class TestCognitiveAPI(unittest.TestCase):
    """后端认知 API 测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_cognitive_mind_returns_ok(self):
        resp = self.client.get("/admin/api/cognitive/mind?mock=true")
        self.assertEqual(resp.status_code, 200)

    def test_cognitive_mind_structure(self):
        resp = self.client.get("/admin/api/cognitive/mind?mock=true")
        data = resp.get_json()
        self.assertIn("emotion_trend", data)
        self.assertIn("attention", data)
        self.assertIn("thinking", data)
        self.assertIn("timestamp", data)

    def test_cognitive_mind_attention_has_fields(self):
        resp = self.client.get("/admin/api/cognitive/mind?mock=true")
        att = resp.get_json()["attention"]
        self.assertIn("focus", att)
        self.assertIn("target", att)
        self.assertIn("level", att)

    def test_cognitive_mind_thinking_has_fields(self):
        resp = self.client.get("/admin/api/cognitive/mind?mock=true")
        thinking = resp.get_json()["thinking"]
        self.assertIn("state", thinking)
        self.assertIn("detail", thinking)

    def test_cognitive_memory_returns_ok(self):
        resp = self.client.get("/admin/api/cognitive/memory?mock=true")
        self.assertEqual(resp.status_code, 200)

    def test_cognitive_memory_structure(self):
        resp = self.client.get("/admin/api/cognitive/memory?mock=true")
        data = resp.get_json()
        self.assertIn("identity", data)
        self.assertIn("events", data)
        self.assertIn("stats", data)

    def test_cognitive_memory_stats_has_totals(self):
        resp = self.client.get("/admin/api/cognitive/memory?mock=true")
        stats = resp.get_json()["stats"]
        self.assertIn("total_events", stats)
        self.assertIn("total_memories", stats)
        self.assertIn("memory_types", stats)

    def test_cognitive_growth_returns_ok(self):
        resp = self.client.get("/admin/api/cognitive/growth?mock=true")
        self.assertEqual(resp.status_code, 200)

    def test_cognitive_growth_structure(self):
        resp = self.client.get("/admin/api/cognitive/growth?mock=true")
        data = resp.get_json()
        self.assertIn("stage", data)
        self.assertIn("records", data)
        self.assertIn("proposals", data)
        self.assertIn("stats", data)

    def test_cognitive_growth_stage_has_level(self):
        resp = self.client.get("/admin/api/cognitive/growth?mock=true")
        stage = resp.get_json()["stage"]
        self.assertIn("level", stage)
        self.assertIn("label", stage)
        self.assertIn("progress", stage)

    def test_cognitive_relationship_returns_ok(self):
        resp = self.client.get("/admin/api/cognitive/relationship?mock=true")
        self.assertEqual(resp.status_code, 200)

    def test_cognitive_relationship_structure(self):
        resp = self.client.get("/admin/api/cognitive/relationship?mock=true")
        data = resp.get_json()
        self.assertIn("trust", data)
        self.assertIn("familiarity", data)
        self.assertIn("milestones", data)
        self.assertIn("stats", data)

    def test_cognitive_relationship_trust_has_score(self):
        resp = self.client.get("/admin/api/cognitive/relationship?mock=true")
        trust = resp.get_json()["trust"]
        self.assertIn("score", trust)
        self.assertIn("level", trust)
        self.assertIn("trend", trust)

    def test_cognitive_relationship_stats_has_interactions(self):
        resp = self.client.get("/admin/api/cognitive/relationship?mock=true")
        stats = resp.get_json()["stats"]
        self.assertIn("total_interactions", stats)
        self.assertIn("total_words", stats)
        self.assertIn("days_together", stats)

    def test_cognitive_mind_mock_flag(self):
        resp = self.client.get("/admin/api/cognitive/mind?mock=true")
        self.assertTrue(resp.get_json().get("mock"))

    def test_cognitive_all_endpoints_without_mock(self):
        """无 mock 时也应返回 200（使用默认数据）"""
        for endpoint in ("mind", "memory", "growth", "relationship"):
            with self.subTest(endpoint=endpoint):
                resp = self.client.get(f"/admin/api/cognitive/{endpoint}")
                self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
