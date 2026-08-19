"""
Phase 2.5: Yuyi Understanding Layer 单元测试

测试羽依理解层的核心组件：
- Memory Graph（记忆关系网络）
- Cognitive Radar（认知雷达）
- Reasoning Explanation（行为原因解释）
- Relationship Timeline（关系成长时间线）
- Growth Impact Preview（成长影响预览）
"""

import os
import sys
import unittest
from pathlib import Path

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


class TestUnderstandingLayerFrontendFiles(unittest.TestCase):
    """前端文件结构测试"""

    def test_yuyi_radar_js_exists(self):
        """yuyi-radar.js 文件存在"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "yuyi-radar.js"
        self.assertTrue(path.exists(), f"{path} 不存在")

    def test_yuyi_reasoning_js_exists(self):
        """yuyi-reasoning.js 文件存在"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "yuyi-reasoning.js"
        self.assertTrue(path.exists(), f"{path} 不存在")

    def test_yuyi_relationship_timeline_js_exists(self):
        """yuyi-relationship-timeline.js 文件存在"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "yuyi-relationship-timeline.js"
        self.assertTrue(path.exists(), f"{path} 不存在")

    def test_yuyi_growth_preview_js_exists(self):
        """yuyi-growth-preview.js 文件存在"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "yuyi-growth-preview.js"
        self.assertTrue(path.exists(), f"{path} 不存在")

    def test_yuyi_memory_graph_js_exists(self):
        """yuyi-memory-graph.js 文件存在"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "yuyi-memory-graph.js"
        self.assertTrue(path.exists(), f"{path} 不存在")

    def test_radar_js_exports_yuyi_radar(self):
        """yuyi-radar.js 导出 YuyiRadar"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "yuyi-radar.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("window.YuyiRadar", content)

    def test_reasoning_js_exports_yuyi_reasoning(self):
        """yuyi-reasoning.js 导出 YuyiReasoning"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "yuyi-reasoning.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("window.YuyiReasoning", content)

    def test_relationship_timeline_js_exports_yuyi_relationship_timeline(self):
        """yuyi-relationship-timeline.js 导出 YuyiRelationshipTimeline"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "yuyi-relationship-timeline.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("window.YuyiRelationshipTimeline", content)

    def test_growth_preview_js_exports_yuyi_growth_preview(self):
        """yuyi-growth-preview.js 导出 YuyiGrowthPreview"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "yuyi-growth-preview.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("window.YuyiGrowthPreview", content)

    def test_memory_graph_js_exports_yuyi_memory_graph(self):
        """yuyi-memory-graph.js 导出 YuyiMemoryGraph"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "yuyi-memory-graph.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("window.YuyiMemoryGraph", content)


class TestUnderstandingLayerCSS(unittest.TestCase):
    """CSS 样式测试"""

    def test_radar_container_style_exists(self):
        """.radar-container 样式存在"""
        path = _PROJECT_ROOT / "static" / "admin" / "css" / "style.css"
        content = path.read_text(encoding="utf-8")
        self.assertIn(".radar-container", content)

    def test_reasoning_container_style_exists(self):
        """.reasoning-container 样式存在"""
        path = _PROJECT_ROOT / "static" / "admin" / "css" / "style.css"
        content = path.read_text(encoding="utf-8")
        self.assertIn(".reasoning-container", content)

    def test_relationship_timeline_container_style_exists(self):
        """.relationship-timeline-container 样式存在"""
        path = _PROJECT_ROOT / "static" / "admin" / "css" / "style.css"
        content = path.read_text(encoding="utf-8")
        self.assertIn(".relationship-timeline-container", content)

    def test_growth_preview_modal_style_exists(self):
        """.growth-preview-modal 样式存在"""
        path = _PROJECT_ROOT / "static" / "admin" / "css" / "style.css"
        content = path.read_text(encoding="utf-8")
        self.assertIn(".growth-preview-modal", content)

    def test_memory_graph_container_style_exists(self):
        """.memory-graph-container 样式存在"""
        path = _PROJECT_ROOT / "static" / "admin" / "css" / "style.css"
        content = path.read_text(encoding="utf-8")
        self.assertIn(".memory-graph-container", content)


class TestUnderstandingLayerAPI(unittest.TestCase):
    """后端 API 测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    # ==================== Memory Graph API ====================

    def test_memory_graph_endpoint_exists(self):
        """Memory Graph API 端点存在"""
        resp = self.client.get("/admin/api/cognitive/memory-graph")
        self.assertEqual(resp.status_code, 200)

    def test_memory_graph_returns_json(self):
        """Memory Graph 返回 JSON"""
        resp = self.client.get("/admin/api/cognitive/memory-graph?mock=true")
        data = resp.get_json()
        self.assertIsInstance(data, dict)

    def test_memory_graph_has_nodes(self):
        """Memory Graph 包含 nodes"""
        resp = self.client.get("/admin/api/cognitive/memory-graph?mock=true")
        data = resp.get_json()
        self.assertIn("nodes", data)
        self.assertIsInstance(data["nodes"], list)

    def test_memory_graph_has_links(self):
        """Memory Graph 包含 links"""
        resp = self.client.get("/admin/api/cognitive/memory-graph?mock=true")
        data = resp.get_json()
        self.assertIn("links", data)
        self.assertIsInstance(data["links"], list)

    def test_memory_graph_has_stats(self):
        """Memory Graph 包含 stats"""
        resp = self.client.get("/admin/api/cognitive/memory-graph?mock=true")
        data = resp.get_json()
        self.assertIn("stats", data)

    def test_memory_graph_node_has_required_fields(self):
        """Memory Graph 节点包含必需字段"""
        resp = self.client.get("/admin/api/cognitive/memory-graph?mock=true")
        data = resp.get_json()
        if data["nodes"]:
            node = data["nodes"][0]
            self.assertIn("id", node)
            self.assertIn("type", node)
            self.assertIn("title", node)

    def test_memory_graph_link_has_required_fields(self):
        """Memory Graph 连线包含必需字段"""
        resp = self.client.get("/admin/api/cognitive/memory-graph?mock=true")
        data = resp.get_json()
        if data["links"]:
            link = data["links"][0]
            self.assertIn("source", link)
            self.assertIn("target", link)
            self.assertIn("strength", link)

    # ==================== Cognitive Radar API ====================

    def test_radar_endpoint_exists(self):
        """Radar API 端点存在"""
        resp = self.client.get("/admin/api/cognitive/radar")
        self.assertEqual(resp.status_code, 200)

    def test_radar_returns_json(self):
        """Radar 返回 JSON"""
        resp = self.client.get("/admin/api/cognitive/radar?mock=true")
        data = resp.get_json()
        self.assertIsInstance(data, dict)

    def test_radar_has_creativity(self):
        """Radar 包含 creativity"""
        resp = self.client.get("/admin/api/cognitive/radar?mock=true")
        data = resp.get_json()
        self.assertIn("creativity", data)
        self.assertIsInstance(data["creativity"], (int, float))

    def test_radar_has_curiosity(self):
        """Radar 包含 curiosity"""
        resp = self.client.get("/admin/api/cognitive/radar?mock=true")
        data = resp.get_json()
        self.assertIn("curiosity", data)
        self.assertIsInstance(data["curiosity"], (int, float))

    def test_radar_has_stability(self):
        """Radar 包含 stability"""
        resp = self.client.get("/admin/api/cognitive/radar?mock=true")
        data = resp.get_json()
        self.assertIn("stability", data)
        self.assertIsInstance(data["stability"], (int, float))

    def test_radar_has_memory(self):
        """Radar 包含 memory"""
        resp = self.client.get("/admin/api/cognitive/radar?mock=true")
        data = resp.get_json()
        self.assertIn("memory", data)
        self.assertIsInstance(data["memory"], (int, float))

    def test_radar_has_social(self):
        """Radar 包含 social"""
        resp = self.client.get("/admin/api/cognitive/radar?mock=true")
        data = resp.get_json()
        self.assertIn("social", data)
        self.assertIsInstance(data["social"], (int, float))

    def test_radar_values_in_range(self):
        """Radar 值在 0-100 范围内"""
        resp = self.client.get("/admin/api/cognitive/radar?mock=true")
        data = resp.get_json()
        for key in ["creativity", "curiosity", "stability", "memory", "social"]:
            self.assertGreaterEqual(data[key], 0)
            self.assertLessEqual(data[key], 100)

    # ==================== Reasoning Explanation API ====================

    def test_explain_endpoint_exists(self):
        """Explain API 端点存在"""
        resp = self.client.get("/admin/api/cognitive/explain")
        self.assertEqual(resp.status_code, 200)

    def test_explain_returns_json(self):
        """Explain 返回 JSON"""
        resp = self.client.get("/admin/api/cognitive/explain?mock=true")
        data = resp.get_json()
        self.assertIsInstance(data, dict)

    def test_explain_has_state_type(self):
        """Explain 包含 state_type"""
        resp = self.client.get("/admin/api/cognitive/explain?mock=true")
        data = resp.get_json()
        self.assertIn("state_type", data)

    def test_explain_has_reasons(self):
        """Explain 包含 reasons"""
        resp = self.client.get("/admin/api/cognitive/explain?mock=true")
        data = resp.get_json()
        self.assertIn("reasons", data)
        self.assertIsInstance(data["reasons"], list)

    def test_explain_has_impact(self):
        """Explain 包含 impact"""
        resp = self.client.get("/admin/api/cognitive/explain?mock=true")
        data = resp.get_json()
        self.assertIn("impact", data)
        self.assertIsInstance(data["impact"], dict)

    def test_explain_reason_has_factor(self):
        """Explain 原因包含 factor"""
        resp = self.client.get("/admin/api/cognitive/explain?mock=true")
        data = resp.get_json()
        if data["reasons"]:
            reason = data["reasons"][0]
            self.assertIn("factor", reason)

    def test_explain_reason_has_weight(self):
        """Explain 原因包含 weight"""
        resp = self.client.get("/admin/api/cognitive/explain?mock=true")
        data = resp.get_json()
        if data["reasons"]:
            reason = data["reasons"][0]
            self.assertIn("weight", reason)

    # ==================== Relationship Timeline API ====================

    def test_relationship_timeline_endpoint_exists(self):
        """Relationship Timeline API 端点存在"""
        resp = self.client.get("/admin/api/cognitive/relationship-timeline")
        self.assertEqual(resp.status_code, 200)

    def test_relationship_timeline_returns_json(self):
        """Relationship Timeline 返回 JSON"""
        resp = self.client.get("/admin/api/cognitive/relationship-timeline?mock=true")
        data = resp.get_json()
        self.assertIsInstance(data, dict)

    def test_relationship_timeline_has_timeline(self):
        """Relationship Timeline 包含 timeline"""
        resp = self.client.get("/admin/api/cognitive/relationship-timeline?mock=true")
        data = resp.get_json()
        self.assertIn("timeline", data)
        self.assertIsInstance(data["timeline"], list)

    def test_relationship_timeline_has_current_level(self):
        """Relationship Timeline 包含 current_level"""
        resp = self.client.get("/admin/api/cognitive/relationship-timeline?mock=true")
        data = resp.get_json()
        self.assertIn("current_level", data)

    def test_relationship_timeline_has_days_together(self):
        """Relationship Timeline 包含 days_together"""
        resp = self.client.get("/admin/api/cognitive/relationship-timeline?mock=true")
        data = resp.get_json()
        self.assertIn("days_together", data)
        self.assertIsInstance(data["days_together"], int)

    def test_relationship_timeline_item_has_day(self):
        """Relationship Timeline 项包含 day"""
        resp = self.client.get("/admin/api/cognitive/relationship-timeline?mock=true")
        data = resp.get_json()
        if data["timeline"]:
            item = data["timeline"][0]
            self.assertIn("day", item)

    def test_relationship_timeline_item_has_title(self):
        """Relationship Timeline 项包含 title"""
        resp = self.client.get("/admin/api/cognitive/relationship-timeline?mock=true")
        data = resp.get_json()
        if data["timeline"]:
            item = data["timeline"][0]
            self.assertIn("title", item)

    def test_relationship_timeline_item_has_level(self):
        """Relationship Timeline 项包含 level"""
        resp = self.client.get("/admin/api/cognitive/relationship-timeline?mock=true")
        data = resp.get_json()
        if data["timeline"]:
            item = data["timeline"][0]
            self.assertIn("level", item)

    # ==================== Growth Impact Preview API ====================

    def test_proposal_preview_endpoint_exists(self):
        """Proposal Preview API 端点存在"""
        resp = self.client.get("/admin/api/cognitive/proposal/1/preview")
        self.assertEqual(resp.status_code, 200)

    def test_proposal_preview_returns_json(self):
        """Proposal Preview 返回 JSON"""
        resp = self.client.get("/admin/api/cognitive/proposal/1/preview?mock=true")
        data = resp.get_json()
        self.assertIsInstance(data, dict)

    def test_proposal_preview_has_before(self):
        """Proposal Preview 包含 before"""
        resp = self.client.get("/admin/api/cognitive/proposal/1/preview?mock=true")
        data = resp.get_json()
        self.assertIn("before", data)
        self.assertIsInstance(data["before"], dict)

    def test_proposal_preview_has_after(self):
        """Proposal Preview 包含 after"""
        resp = self.client.get("/admin/api/cognitive/proposal/1/preview?mock=true")
        data = resp.get_json()
        self.assertIn("after", data)
        self.assertIsInstance(data["after"], dict)

    def test_proposal_preview_has_effects(self):
        """Proposal Preview 包含 effects"""
        resp = self.client.get("/admin/api/cognitive/proposal/1/preview?mock=true")
        data = resp.get_json()
        self.assertIn("effects", data)
        self.assertIsInstance(data["effects"], list)

    def test_proposal_preview_has_confidence(self):
        """Proposal Preview 包含 confidence"""
        resp = self.client.get("/admin/api/cognitive/proposal/1/preview?mock=true")
        data = resp.get_json()
        self.assertIn("confidence", data)
        self.assertIsInstance(data["confidence"], (int, float))

    def test_proposal_preview_confidence_in_range(self):
        """Proposal Preview confidence 在 0-1 范围内"""
        resp = self.client.get("/admin/api/cognitive/proposal/1/preview?mock=true")
        data = resp.get_json()
        self.assertGreaterEqual(data["confidence"], 0)
        self.assertLessEqual(data["confidence"], 1)


class TestUnderstandingLayerIntegration(unittest.TestCase):
    """集成测试"""

    def test_app_js_initializes_radar(self):
        """app.js 初始化 Radar"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "app.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("YuyiRadar.init", content)
        self.assertIn("YuyiRadar.load", content)

    def test_app_js_initializes_reasoning(self):
        """app.js 初始化 Reasoning"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "app.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("YuyiReasoning.init", content)
        self.assertIn("YuyiReasoning.load", content)

    def test_app_js_initializes_relationship_timeline(self):
        """app.js 初始化 Relationship Timeline"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "app.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("YuyiRelationshipTimeline.init", content)
        self.assertIn("YuyiRelationshipTimeline.load", content)

    def test_app_js_initializes_memory_graph(self):
        """app.js 初始化 Memory Graph"""
        path = _PROJECT_ROOT / "static" / "admin" / "js" / "app.js"
        content = path.read_text(encoding="utf-8")
        self.assertIn("YuyiMemoryGraph.init", content)
        self.assertIn("YuyiMemoryGraph.load", content)

    def test_index_html_includes_radar_script(self):
        """index.html 引入 Radar 脚本"""
        path = _PROJECT_ROOT / "static" / "admin" / "index.html"
        content = path.read_text(encoding="utf-8")
        self.assertIn("yuyi-radar.js", content)

    def test_index_html_includes_reasoning_script(self):
        """index.html 引入 Reasoning 脚本"""
        path = _PROJECT_ROOT / "static" / "admin" / "index.html"
        content = path.read_text(encoding="utf-8")
        self.assertIn("yuyi-reasoning.js", content)

    def test_index_html_includes_relationship_timeline_script(self):
        """index.html 引入 Relationship Timeline 脚本"""
        path = _PROJECT_ROOT / "static" / "admin" / "index.html"
        content = path.read_text(encoding="utf-8")
        self.assertIn("yuyi-relationship-timeline.js", content)

    def test_index_html_includes_growth_preview_script(self):
        """index.html 引入 Growth Preview 脚本"""
        path = _PROJECT_ROOT / "static" / "admin" / "index.html"
        content = path.read_text(encoding="utf-8")
        self.assertIn("yuyi-growth-preview.js", content)

    def test_index_html_includes_memory_graph_script(self):
        """index.html 引入 Memory Graph 脚本"""
        path = _PROJECT_ROOT / "static" / "admin" / "index.html"
        content = path.read_text(encoding="utf-8")
        self.assertIn("yuyi-memory-graph.js", content)


if __name__ == "__main__":
    unittest.main()