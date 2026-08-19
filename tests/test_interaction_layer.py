"""
Phase 1.5-C: Yuyi Interaction Layer 测试
测试：State Manager, Avatar Interface, Audio Feedback, Replies, Timeline
"""

import os
import pytest


BASE_DIR = os.path.join(os.path.dirname(__file__), '..', 'static', 'admin')


class TestYuyiStateManager:
    """测试统一状态管理器"""

    @pytest.fixture
    def filepath(self):
        return os.path.join(BASE_DIR, 'js', 'yuyi-state-manager.js')

    def test_file_exists(self, filepath):
        assert os.path.exists(filepath), f"文件不存在: {filepath}"

    def test_exports_to_window(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "window.YuyiState" in content

    def test_has_getState(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "getState" in content

    def test_has_update(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "function update" in content

    def test_has_subscribe(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "subscribe" in content
        assert "notifyListeners" in content

    def test_has_history(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "getHistory" in content
        assert "pushHistory" in content

    def test_has_emotion_label(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "getEmotionLabel" in content
        assert "happy" in content
        assert "excited" in content

    def test_initial_state_keys(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "emotion" in content
        assert "energy" in content
        assert "activity" in content
        assert "message" in content


class TestYuyiAvatar:
    """测试 Avatar 接口层"""

    @pytest.fixture
    def filepath(self):
        return os.path.join(BASE_DIR, 'js', 'yuyi-avatar.js')

    def test_file_exists(self, filepath):
        assert os.path.exists(filepath), f"文件不存在: {filepath}"

    def test_exports_to_window(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "window.YuyiAvatar" in content

    def test_has_adapter_interface(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "AdapterInterface" in content
        assert "setEmotion" in content
        assert "speak" in content
        assert "playAction" in content

    def test_has_svg_adapter(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "SVGAdapter" in content
        assert "getType" in content

    def test_has_live2d_adapter_stub(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "Live2DAdapter" in content

    def test_has_useAdapter(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "useAdapter" in content

    def test_has_createSVGAdapter(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "createSVGAdapter" in content

    def test_adapter_validates_methods(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "适配器必须是对象" in content
        assert "适配器缺少方法" in content

    def test_playAction_types(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        for action in ("speak", "wave", "nod", "shake", "bounce", "think", "sleep"):
            assert action in content


class TestYuyiAudio:
    """测试音效反馈系统"""

    @pytest.fixture
    def filepath(self):
        return os.path.join(BASE_DIR, 'js', 'yuyi-audio.js')

    def test_file_exists(self, filepath):
        assert os.path.exists(filepath), f"文件不存在: {filepath}"

    def test_exports_to_window(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "window.YuyiAudio" in content

    def test_has_audio_context(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "AudioContext" in content

    def test_has_sound_effects(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        for effect in ("click", "wake", "success", "warning", "error",
                        "sleep", "notification", "sparkle", "reply"):
            assert effect in content

    def test_has_playTone(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "playTone" in content

    def test_has_play_volume(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "setVolume" in content

    def test_has_enabled_control(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "setEnabled" in content
        assert "isEnabled" in content

    def test_effects_use_different_waveforms(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "sine" in content
        assert "triangle" in content
        assert "square" in content
        assert "sawtooth" in content


class TestYuyiReplies:
    """测试对话式反馈系统"""

    @pytest.fixture
    def filepath(self):
        return os.path.join(BASE_DIR, 'js', 'yuyi-replies.js')

    def test_file_exists(self, filepath):
        assert os.path.exists(filepath), f"文件不存在: {filepath}"

    def test_exports_to_window(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "window.YuyiReplies" in content

    def test_has_reply_library(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "REPLY_LIBRARY" in content

    def test_reply_categories(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        for category in ("module_start", "module_stop", "module_reload",
                          "health_check", "config_save", "config_rollback",
                          "system_start", "error_generic", "memory_save",
                          "greeting", "processing"):
            assert category in content

    def test_emotion_variants(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        for emotion in ("happy", "calm", "excited", "sad", "worried", "sleepy"):
            assert f'{emotion}:' in content

    def test_has_interpolation(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "interpolate" in content
        assert "\\{\\w+\\}" in content or "{" in content and "}" in content

    def test_has_getReply(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "getReply" in content

    def test_has_getActions(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "getActions" in content

    def test_has_addReplyPool(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "addReplyPool" in content

    def test_fallback_reply(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "getFallbackReply" in content


class TestYuyiTimeline:
    """测试状态时间线组件"""

    @pytest.fixture
    def filepath(self):
        return os.path.join(BASE_DIR, 'js', 'yuyi-timeline.js')

    def test_file_exists(self, filepath):
        assert os.path.exists(filepath), f"文件不存在: {filepath}"

    def test_exports_to_window(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "window.YuyiTimeline" in content

    def test_has_event_types(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        for evt in ("module_start", "module_stop", "module_reload",
                     "config_change", "emotion_change", "task_complete",
                     "error", "memory_save", "user_interaction"):
            assert evt in content

    def test_has_addEvent(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "addEvent" in content

    def test_has_getEvents(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "getEvents" in content
        assert "getTodayEvents" in content
        assert "getStatistics" in content

    def test_has_render(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "function render" in content
        assert "timeline-item" in content
        assert "timeline-dot" in content

    def test_has_subscribe(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "subscribe" in content

    def test_has_auto_tracking(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "setupAutoTracking" in content

    def test_events_have_colors(self, filepath):
        content = open(filepath, encoding="utf-8").read()
        assert "color" in content


class TestIntegration:
    """测试模块间整合"""

    def test_app_js_integrates_state_manager(self):
        filepath = os.path.join(BASE_DIR, 'js', 'app.js')
        content = open(filepath, encoding="utf-8").read()
        assert "YuyiState" in content
        assert "YuyiAvatar" in content
        assert "YuyiAudio" in content
        assert "YuyiReplies" in content
        assert "YuyiTimeline" in content

    def test_app_js_has_module_action_handlers(self):
        filepath = os.path.join(BASE_DIR, 'js', 'app.js')
        content = open(filepath, encoding="utf-8").read()
        assert "handleModuleAction" in content
        assert "handleModuleError" in content

    def test_app_js_initializes_systems(self):
        filepath = os.path.join(BASE_DIR, 'js', 'app.js')
        content = open(filepath, encoding="utf-8").read()
        assert "YuyiAudio.init" in content
        assert "YuyiState.initialize" in content
        assert "YuyiAvatar.createSVGAdapter" in content
        assert "YuyiTimeline.setupAutoTracking" in content

    def test_html_includes_new_scripts(self):
        filepath = os.path.join(BASE_DIR, 'index.html')
        content = open(filepath, encoding="utf-8").read()
        assert "yuyi-state-manager.js" in content
        assert "yuyi-avatar.js" in content
        assert "yuyi-audio.js" in content
        assert "yuyi-replies.js" in content
        assert "yuyi-timeline.js" in content

    def test_html_has_timeline_section(self):
        filepath = os.path.join(BASE_DIR, 'index.html')
        content = open(filepath, encoding="utf-8").read()
        assert "timeline-container" in content
        assert "羽依的轨迹" in content

    def test_css_has_timeline_styles(self):
        filepath = os.path.join(BASE_DIR, 'css', 'style.css')
        content = open(filepath, encoding="utf-8").read()
        assert ".timeline-container" in content
        assert ".timeline-item" in content
        assert ".timeline-dot" in content
        assert ".timeline-empty" in content

    def test_css_has_character_dialog_styles(self):
        filepath = os.path.join(BASE_DIR, 'css', 'style.css')
        content = open(filepath, encoding="utf-8").read()
        assert ".character-dialog" in content
        assert ".character-dialog-text" in content

    def test_css_has_character_animations(self):
        filepath = os.path.join(BASE_DIR, 'css', 'style.css')
        content = open(filepath, encoding="utf-8").read()
        assert "yuyi-talking" in content
        assert "yuyi-think" in content
        assert "yuyi-sleep" in content
