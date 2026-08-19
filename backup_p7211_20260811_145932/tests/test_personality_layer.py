"""
Phase 1.5-B — Yuyi Personality Layer 单元测试

覆盖：
- 图标系统文件结构（25个 SVG 图标存在且合规）
- 角色状态控制器（YuyiCharacter）
- 背景粒子引擎（YuyiBackground）
- 微交互 CSS 存在性
- 静态资源路由可访问性
"""

import os
import sys
import json
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_STATIC_ADMIN = _PROJECT_ROOT / "static" / "admin"
sys.path.insert(0, str(_PROJECT_ROOT))

EXPECTED_ICONS = {
    "core": ["star", "heart", "cat-ear", "crystal", "wing"],
    "module": ["neural", "energy-ring", "screen", "control", "remote", "config", "audit"],
    "memory": ["water-drop", "stardust", "book", "database"],
    "emotion": ["heartbeat", "ripple", "smile"],
    "system": ["home", "modules", "mind", "log", "shield", "pulse"],
}


class TestIconSystem(unittest.TestCase):
    """图标系统测试"""

    @classmethod
    def setUpClass(cls):
        cls.icons_dir = _STATIC_ADMIN / "icons"

    def test_all_categories_exist(self):
        for cat in EXPECTED_ICONS:
            cat_dir = self.icons_dir / cat
            self.assertTrue(cat_dir.exists(), f"图标类别目录不存在: {cat}")

    def test_all_core_icons_exist(self):
        for name in EXPECTED_ICONS["core"]:
            f = self.icons_dir / "core" / f"{name}.svg"
            self.assertTrue(f.exists(), f"Core 图标缺失: {name}")

    def test_all_module_icons_exist(self):
        for name in EXPECTED_ICONS["module"]:
            f = self.icons_dir / "module" / f"{name}.svg"
            self.assertTrue(f.exists(), f"Module 图标缺失: {name}")

    def test_all_memory_icons_exist(self):
        for name in EXPECTED_ICONS["memory"]:
            f = self.icons_dir / "memory" / f"{name}.svg"
            self.assertTrue(f.exists(), f"Memory 图标缺失: {name}")

    def test_all_emotion_icons_exist(self):
        for name in EXPECTED_ICONS["emotion"]:
            f = self.icons_dir / "emotion" / f"{name}.svg"
            self.assertTrue(f.exists(), f"Emotion 图标缺失: {name}")

    def test_all_system_icons_exist(self):
        for name in EXPECTED_ICONS["system"]:
            f = self.icons_dir / "system" / f"{name}.svg"
            self.assertTrue(f.exists(), f"System 图标缺失: {name}")

    def test_icons_use_currentcolor(self):
        for cat in EXPECTED_ICONS:
            for name in EXPECTED_ICONS[cat]:
                f = self.icons_dir / cat / f"{name}.svg"
                content = f.read_text(encoding="utf-8")
                self.assertIn('stroke="currentColor"', content,
                              f"{cat}/{name}.svg 未使用 currentColor")

    def test_icons_have_viewbox_24(self):
        for cat in EXPECTED_ICONS:
            for name in EXPECTED_ICONS[cat]:
                f = self.icons_dir / cat / f"{name}.svg"
                content = f.read_text(encoding="utf-8")
                self.assertIn('viewBox="0 0 24 24"', content,
                              f"{cat}/{name}.svg viewBox 不标准")

    def test_icons_have_title(self):
        for cat in EXPECTED_ICONS:
            for name in EXPECTED_ICONS[cat]:
                f = self.icons_dir / cat / f"{name}.svg"
                content = f.read_text(encoding="utf-8")
                self.assertIn("<title>", content,
                              f"{cat}/{name}.svg 缺少 <title>")

    def test_icons_use_linecap_round(self):
        for cat in EXPECTED_ICONS:
            for name in EXPECTED_ICONS[cat]:
                f = self.icons_dir / cat / f"{name}.svg"
                content = f.read_text(encoding="utf-8")
                self.assertIn("stroke-linecap", content,
                              f"{cat}/{name}.svg 缺少 stroke-linecap")

    def test_total_icon_count(self):
        total = sum(len(v) for v in EXPECTED_ICONS.values())
        self.assertEqual(total, 25)


class TestYuyiCharacterController(unittest.TestCase):
    """YuyiCharacter 控制器测试"""

    @classmethod
    def setUpClass(cls):
        cls.filepath = _STATIC_ADMIN / "js" / "yuyi-character.js"

    def test_file_exists(self):
        self.assertTrue(self.filepath.exists())

    def test_has_setstate(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("function setState", content)

    def test_has_getstate(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("function getState", content)

    def test_has_setexpression(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("function setExpression", content)

    def test_has_setenergy(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("function setEnergy", content)

    def test_has_setmessage(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("function setMessage", content)

    def test_has_valid_expressions(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("VALID_EXPRESSIONS", content)

    def test_has_expression_map(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("EXPRESSION_MAP", content)
        for exp in ("smile", "happy", "sad", "excited"):
            self.assertIn(f'"{exp}"', content)

    def test_has_mood_map(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("MOOD_MAP", content)

    def test_decoupled_rendering(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("function renderExpression", content)
        self.assertIn("function renderEnergy", content)
        self.assertIn("function renderMessage", content)
        self.assertIn("function renderGlow", content)
        self.assertIn("function renderMood", content)

    def test_notify_listeners(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("listeners", content)
        self.assertIn("function on(", content)

    def test_exports_to_window(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("window.YuyiCharacter", content)

    def test_has_blink_system(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("performBlink", content)
        self.assertIn("startBlinkLoop", content)

    def test_has_breathe_system(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("startBreatheLoop", content)
        self.assertIn("breatheTimer", content)

    def test_has_enhanced_expressions(self):
        content = self.filepath.read_text(encoding="utf-8")
        for exp in ("dazed", "sleepy", "curious"):
            self.assertIn(f"{exp}:", content)


class TestYuyiBackground(unittest.TestCase):
    """YuyiBackground 粒子引擎测试"""

    @classmethod
    def setUpClass(cls):
        cls.filepath = _STATIC_ADMIN / "js" / "yuyi-background.js"

    def test_file_exists(self):
        self.assertTrue(self.filepath.exists())

    def test_has_init(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("function init(", content)

    def test_has_setmood(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("function setMood", content)

    def test_has_getmood(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("function getMood", content)

    def test_has_mood_config(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("MOOD_CONFIG", content)
        for mood in ("calm", "happy", "sad", "excited", "angry"):
            self.assertIn(f"{mood}:", content)

    def test_mood_config_has_colors(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("colors", content)

    def test_mood_config_has_count(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("count", content)

    def test_mood_config_has_speed(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("speed", content)

    def test_has_particle_class(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("class Particle", content)

    def test_particle_has_sparkle_type(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("sparkle", content)

    def test_uses_canvas(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("canvas", content.lower())
        self.assertIn("getContext", content)

    def test_exports_to_window(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("window.YuyiBackground", content)

    def test_has_trail_effect(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("drawTrail", content)
        self.assertIn("trail", content)

    def test_has_connection_effect(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("drawConnections", content)
        self.assertIn("connect", content)

    def test_has_mouse_interaction(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("mouseActive", content)
        self.assertIn("mousemove", content)

    def test_has_glow_effect(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("globalAlpha *= 0.3", content)


class TestMicroInteractions(unittest.TestCase):
    """微交互 CSS 测试"""

    @classmethod
    def setUpClass(cls):
        cls.filepath = _STATIC_ADMIN / "css" / "style.css"

    def test_has_particle_canvas_style(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("#particle-canvas", content)

    def test_has_module_icon_style(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn(".module-icon", content)

    def test_has_trait_icon_style(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn(".trait-icon", content)

    def test_has_pulse_animation(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("yuyi-pulse-ring", content)

    def test_has_shake_animation(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("yuyi-shake", content)

    def test_micro_interactions_use_spring_easing(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("cubic-bezier(0.34, 1.56, 0.64, 1)", content)

    def test_status_text_uses_design_tokens(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("--state-calm-soft", content)
        self.assertIn("--state-worry-soft", content)

    def test_has_card_awaken_animation(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("yuyi-card-awaken", content)

    def test_has_card_sleep_animation(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("yuyi-card-sleep", content)

    def test_has_energy_pulse_animation(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("yuyi-energy-pulse", content)

    def test_has_message_pop_animation(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("yuyi-message-pop", content)

    def test_has_greeting_breathe_animation(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("yuyi-greeting-breathe", content)


class TestDashboardRefactoring(unittest.TestCase):
    """dashboard.js 重构验证"""

    @classmethod
    def setUpClass(cls):
        cls.filepath = _STATIC_ADMIN / "js" / "dashboard.js"

    def test_uses_yuyicharacter(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("YuyiCharacter", content)

    def test_has_icon_mapping(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("MODULE_ICONS", content)

    def test_has_module_icon_function(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("moduleIconHTML", content)

    def test_has_icon_categories_in_mapping(self):
        content = self.filepath.read_text(encoding="utf-8")
        for mod in ("screen", "control", "remote", "memory", "personality",
                     "emotion", "config", "audit"):
            self.assertIn(f"{mod}:", content)

    def test_has_character_fallback(self):
        content = self.filepath.read_text(encoding="utf-8")
        self.assertIn("renderCharacterStateFallback", content)


class TestAdminStaticRoutesPersonality(unittest.TestCase):
    """Admin 静态路由 — 人格层资源可访问性"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__, static_folder=None)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_core_icon_accessible(self):
        for name in EXPECTED_ICONS["core"]:
            resp = self.client.get(f"/admin/icons/core/{name}.svg")
            self.assertEqual(resp.status_code, 200)

    def test_module_icon_accessible(self):
        for name in EXPECTED_ICONS["module"]:
            resp = self.client.get(f"/admin/icons/module/{name}.svg")
            self.assertEqual(resp.status_code, 200)

    def test_memory_icon_accessible(self):
        for name in EXPECTED_ICONS["memory"]:
            resp = self.client.get(f"/admin/icons/memory/{name}.svg")
            self.assertEqual(resp.status_code, 200)

    def test_emotion_icon_accessible(self):
        for name in EXPECTED_ICONS["emotion"]:
            resp = self.client.get(f"/admin/icons/emotion/{name}.svg")
            self.assertEqual(resp.status_code, 200)

    def test_system_icon_accessible(self):
        for name in EXPECTED_ICONS["system"]:
            resp = self.client.get(f"/admin/icons/system/{name}.svg")
            self.assertEqual(resp.status_code, 200)

    def test_character_js_accessible(self):
        resp = self.client.get("/admin/js/yuyi-character.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"YuyiCharacter", resp.data)

    def test_background_js_accessible(self):
        resp = self.client.get("/admin/js/yuyi-background.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"YuyiBackground", resp.data)

    def test_unknown_icon_returns_404(self):
        resp = self.client.get("/admin/icons/core/nonexistent.svg")
        self.assertEqual(resp.status_code, 404)

    def test_dashboard_has_canvas_reference(self):
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"particle-canvas", resp.data)

    def test_dashboard_includes_character_js(self):
        resp = self.client.get("/admin/")
        self.assertIn(b"yuyi-character.js", resp.data)

    def test_dashboard_includes_background_js(self):
        resp = self.client.get("/admin/")
        self.assertIn(b"yuyi-background.js", resp.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
