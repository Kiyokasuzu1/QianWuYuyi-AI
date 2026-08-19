"""
Phase 1.5-A — Yuyi Design System 单元测试

覆盖：
- Design Tokens 存在性
- 主题配置 JSON 校验
- 组件 CSS 文件结构
- HTML 模板组件引用
- 静态资源路由可访问
"""

import os
import sys
import json
import re
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_STATIC_ADMIN = _PROJECT_ROOT / "static" / "admin"
sys.path.insert(0, str(_PROJECT_ROOT))


class TestDesignTokensFile(unittest.TestCase):
    """Design Tokens CSS 文件结构测试"""

    @classmethod
    def setUpClass(cls):
        cls.filepath = _STATIC_ADMIN / "css" / "design-tokens.css"

    def test_file_exists(self):
        self.assertTrue(self.filepath.exists())

    def test_has_root_selector(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn(":root", content)

    def test_color_tokens_exist(self):
        content = self.filepath.read_text(encoding="utf-8")
        expected = ["--yuyi-blue-", "--yuyi-purple-", "--yuyi-pink-",
                    "--yuyi-mint-", "--yuyi-amber-"]
        for token in expected:
            self.assertIn(token, content, f"缺少色彩 token: {token}")

    def test_state_tokens_exist(self):
        content = self.filepath.read_text(encoding="utf-8")
        for state in ("calm", "happy", "thinking", "notice", "worry", "growth"):
            self.assertIn(f"--state-{state}:", content)
            self.assertIn(f"--state-{state}-soft:", content)
            self.assertIn(f"--state-{state}-glow:", content)

    def test_glass_tokens_exist(self):
        content = self.filepath.read_text(encoding="utf-8")
        for i in range(1, 5):
            self.assertIn(f"--glass-{i}:", content)
        self.assertIn("--glass-border:", content)
        self.assertIn("--glass-blur:", content)

    def test_typography_tokens_exist(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("--text-primary:", content)
        self.assertIn("--text-secondary:", content)
        self.assertIn("--text-muted:", content)
        self.assertIn("--font-family:", content)

    def test_spacing_tokens_exist(self):
        content = self.filepath.read_text(encoding="utf-8")
        for n in [1, 2, 4, 6, 8, 12, 16, 20, 24]:
            self.assertIn(f"--space-{n}:", content)

    def test_radius_tokens_exist(self):
        content = self.filepath.read_text(encoding="utf-8")
        for size in ["sm", "md", "lg", "xl", "full"]:
            self.assertIn(f"--radius-{size}:", content)

    def test_shadow_tokens_exist(self):
        content = self.filepath.read_text(encoding="utf-8")
        for size in ["xs", "sm", "md", "lg", "xl", "glow"]:
            self.assertIn(f"--shadow-{size}:", content)

    def test_animation_tokens_exist(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("--ease-soft:", content)
        self.assertIn("--ease-spring:", content)
        self.assertIn("--duration-fast:", content)
        self.assertIn("--duration-normal:", content)

    def test_glow_tokens_exist(self):
        content = self.filepath.read_text(encoding="utf-8")
        for color in ["primary", "blue", "pink", "amber", "mint"]:
            self.assertIn(f"--glow-{color}:", content)


class TestThemeConfiguration(unittest.TestCase):
    """主题配置文件测试"""

    @classmethod
    def setUpClass(cls):
        cls.themes_dir = _STATIC_ADMIN / "themes"

    def test_themes_directory_exists(self):
        self.assertTrue(self.themes_dir.exists())

    def test_default_theme_exists(self):
        f = self.themes_dir / "yuyi-default.json"
        self.assertTrue(f.exists())

    def test_default_theme_valid_json(self):
        f = self.themes_dir / "yuyi-default.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertIn("name", data)
        self.assertEqual(data["name"], "yuyi-default")
        for key in ("name", "label", "version", "background", "glass",
                    "character", "states", "text", "animation", "sound"):
            self.assertIn(key, data)

    def test_theme_background_structure(self):
        f = self.themes_dir / "yuyi-default.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        bg = data["background"]
        self.assertIn("gradient", bg)
        self.assertIn("particle", bg)
        self.assertIn("count", bg["particle"])

    def test_theme_glass_structure(self):
        f = self.themes_dir / "yuyi-default.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertIn("card", data["glass"])
        self.assertIn("overlay", data["glass"])

    def test_theme_character_mood_overrides(self):
        f = self.themes_dir / "yuyi-default.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        char = data["character"]
        self.assertIn("moodOverrides", char)
        for mood in ("calm", "happy", "excited", "sad"):
            self.assertIn(mood, char["moodOverrides"])

    def test_theme_states_have_all_types(self):
        f = self.themes_dir / "yuyi-default.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        for s in ("calm", "happy", "thinking", "notice", "worry", "growth"):
            self.assertIn(s, data["states"])
            self.assertIn("color", data["states"][s])
            self.assertIn("soft", data["states"][s])
            self.assertIn("glow", data["states"][s])

    def test_theme_animation_structure(self):
        f = self.themes_dir / "yuyi-default.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertIn("easing", data["animation"])
        self.assertIn("spring", data["animation"])
        self.assertIn("duration", data["animation"])


class TestComponentCSSFiles(unittest.TestCase):
    """组件 CSS 文件结构测试"""

    @classmethod
    def setUpClass(cls):
        cls.components_dir = _STATIC_ADMIN / "css" / "components"

    def test_components_dir_exists(self):
        self.assertTrue(self.components_dir.exists())

    def test_card_css_exists(self):
        f = self.components_dir / "card.css"
        self.assertTrue(f.exists())
        content = f.read_text(encoding="utf-8")
        self.assertIn(".yuyi-card", content)
        self.assertIn(".yuyi-card--active", content)
        self.assertIn(".yuyi-card--warning", content)
        self.assertIn(".yuyi-card--error", content)
        self.assertIn(".yuyi-card--sm", content)
        self.assertIn(".yuyi-card__header", content)

    def test_button_css_exists(self):
        f = self.components_dir / "button.css"
        self.assertTrue(f.exists())
        content = f.read_text(encoding="utf-8")
        self.assertIn(".yuyi-btn", content)
        self.assertIn(".yuyi-btn--primary", content)
        self.assertIn(".yuyi-btn--secondary", content)
        self.assertIn(".yuyi-btn--ghost", content)
        self.assertIn(".yuyi-btn--danger", content)
        self.assertIn(".yuyi-btn--icon", content)
        self.assertIn(".yuyi-btn-group", content)

    def test_status_css_exists(self):
        f = self.components_dir / "status.css"
        self.assertTrue(f.exists())
        content = f.read_text(encoding="utf-8")
        self.assertIn(".yuyi-status", content)
        self.assertIn(".yuyi-status-dot", content)
        self.assertIn(".yuyi-progress", content)
        for s in ("calm", "happy", "thinking", "notice", "worry", "growth"):
            self.assertIn(f".yuyi-status--{s}", content)

    def test_toast_css_exists(self):
        f = self.components_dir / "toast.css"
        self.assertTrue(f.exists())
        content = f.read_text(encoding="utf-8")
        self.assertIn(".yuyi-toast", content)
        self.assertIn(".yuyi-toast-container", content)
        self.assertIn(".yuyi-toast--show", content)
        for t in ("info", "success", "warning", "error"):
            self.assertIn(f".yuyi-toast--{t}", content)

    def test_card_uses_design_tokens(self):
        f = self.components_dir / "card.css"
        content = f.read_text(encoding="utf-8")
        self.assertIn("var(--glass-", content)
        self.assertIn("var(--shadow-", content)
        self.assertIn("var(--radius-", content)

    def test_button_uses_design_tokens(self):
        f = self.components_dir / "button.css"
        content = f.read_text(encoding="utf-8")
        self.assertIn("var(--yuyi-purple-", content)
        self.assertIn("var(--duration-", content)

    def test_status_uses_design_tokens(self):
        f = self.components_dir / "status.css"
        content = f.read_text(encoding="utf-8")
        self.assertIn("var(--state-", content)


class TestThemeManagerJS(unittest.TestCase):
    """theme-manager.js 结构测试"""

    @classmethod
    def setUpClass(cls):
        cls.filepath = _STATIC_ADMIN / "js" / "theme-manager.js"

    def test_file_exists(self):
        self.assertTrue(self.filepath.exists())

    def test_has_yuyi_thememanager(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("YuyiThemeManager", content)

    def test_has_init_function(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("async function init", content)

    def test_has_loadtheme_function(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("async function loadTheme", content)

    def test_has_applytheme_function(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("function applyTheme", content)

    def test_has_mood_override(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("applyMoodOverride", content)

    def test_has_localstorage_persistence(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("localStorage", content)

    def test_exports_to_window(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("window.YuyiThemeManager", content)


class TestToastJS(unittest.TestCase):
    """toast.js 结构测试"""

    @classmethod
    def setUpClass(cls):
        cls.filepath = _STATIC_ADMIN / "js" / "toast.js"

    def test_file_exists(self):
        self.assertTrue(self.filepath.exists())

    def test_has_yuyitoast(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("YuyiToast", content)

    def test_has_show_method(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("function show(", content)

    def test_has_all_types(self):
        content = self.filepath.read_text(encoding="utf-8")
        for t in ("info", "success", "warning", "error"):
            self.assertIn(f"{t}: (msg", content)

    def test_exports_to_window(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("window.YuyiToast", content)


class TestStyleCSSMigration(unittest.TestCase):
    """style.css 迁移到 design tokens 验证"""

    @classmethod
    def setUpClass(cls):
        cls.filepath = _STATIC_ADMIN / "css" / "style.css"

    def test_file_exists(self):
        self.assertTrue(self.filepath.exists())

    def test_uses_token_variables(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("var(--yuyi-purple-", content)
        self.assertIn("var(--yuyi-pink-", content)
        self.assertIn("var(--yuyi-blue-", content)
        self.assertIn("var(--text-primary", content)
        self.assertIn("var(--text-muted", content)
        self.assertIn("var(--glass-2", content)
        self.assertIn("var(--glass-border", content)
        self.assertIn("var(--radius-", content)
        self.assertIn("var(--shadow-", content)
        self.assertIn("var(--duration-", content)


class TestAdminStaticRoutesDesign(unittest.TestCase):
    """Admin 静态路由设计系统资源可访问性测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__, static_folder=None)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_design_tokens_css_accessible(self):
        resp = self.client.get("/admin/css/design-tokens.css")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b":root", resp.data)

    def test_card_css_accessible(self):
        resp = self.client.get("/admin/css/components/card.css")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"yuyi-card", resp.data)

    def test_button_css_accessible(self):
        resp = self.client.get("/admin/css/components/button.css")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"yuyi-btn", resp.data)

    def test_status_css_accessible(self):
        resp = self.client.get("/admin/css/components/status.css")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"yuyi-status", resp.data)

    def test_toast_css_accessible(self):
        resp = self.client.get("/admin/css/components/toast.css")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"yuyi-toast", resp.data)

    def test_theme_json_accessible(self):
        resp = self.client.get("/admin/themes/yuyi-default.json")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["name"], "yuyi-default")

    def test_theme_manager_js_accessible(self):
        resp = self.client.get("/admin/js/theme-manager.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"YuyiThemeManager", resp.data)

    def test_toast_js_accessible(self):
        resp = self.client.get("/admin/js/toast.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"YuyiToast", resp.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
