"""
测试 engine.py 中的上下文注入 — 确保所有上下文都能正确进入系统提示
"""
import sys
import pytest
from unittest.mock import MagicMock

# Mock openai before importing engine
sys.modules['openai'] = MagicMock()
sys.modules['src.token_opt'] = MagicMock()

from src.engine import ResponseEngine  # noqa: E402


@pytest.fixture
def engine():
    return ResponseEngine()


class TestBuildMessagesOriginal:
    """测试 _build_messages_original 方法的上下文注入"""

    def test_includes_self_model_context(self, engine):
        """自我认知必须出现在系统提示中"""
        self_model = {
            "identity": "浅雾羽依",
            "status": "活跃",
            "confidence": "高"
        }
        messages = engine._build_messages_original(
            user_message="你好",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="温柔",
            self_model_context=self_model,
            emotion_context={"dominant": "平静", "intensity": 0.5},
            relationship_context={"closeness": "朋友"},
            context_prompt_blocks=[],
        )
        system_content = messages[0]["content"]
        assert "浅雾羽依" in system_content
        assert "活跃" in system_content
        assert "自我认知" in system_content

    def test_includes_emotion_context(self, engine):
        """情绪必须出现在系统提示中"""
        messages = engine._build_messages_original(
            user_message="你好",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={"dominant": "开心", "intensity": 0.8},
            relationship_context={},
            context_prompt_blocks=[],
        )
        system_content = messages[0]["content"]
        assert "开心" in system_content
        assert "当前情绪" in system_content

    def test_includes_relationship_context(self, engine):
        """关系必须出现在系统提示中"""
        messages = engine._build_messages_original(
            user_message="你好",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={},
            relationship_context={"closeness": "亲密", "trust": "高"},
            context_prompt_blocks=[],
        )
        system_content = messages[0]["content"]
        assert "用户关系" in system_content
        assert "亲密" in system_content

    def test_includes_chat_memories(self, engine):
        """聊天记忆必须出现在系统提示中"""
        memories = [
            {"role": "user", "content": "我喜欢打游戏"},
            {"role": "assistant", "content": "好厉害呀"},
        ]
        messages = engine._build_messages_original(
            user_message="再来一局",
            history=[],
            chat_memories=memories,
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={},
            relationship_context={},
            context_prompt_blocks=[],
        )
        system_content = messages[0]["content"]
        assert "相关记忆" in system_content
        assert "打游戏" in system_content

    def test_includes_life_events(self, engine):
        """重要经历必须出现在系统提示中"""
        events = [{"description": "用户完成了一个大任务"}]
        messages = engine._build_messages_original(
            user_message="你好",
            history=[],
            chat_memories=[],
            life_events=events,
            personality_context="",
            self_model_context={},
            emotion_context={},
            relationship_context={},
            context_prompt_blocks=[],
        )
        system_content = messages[0]["content"]
        assert "重要经历" in system_content
        assert "大任务" in system_content

    def test_includes_screen_context_from_blocks(self, engine):
        """屏幕上下文（通过 prompt_blocks）必须出现在系统提示中"""
        blocks = [
            {"role": "system", "content": "【屏幕内容】用户正在玩原神"}
        ]
        messages = engine._build_messages_original(
            user_message="你好",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={},
            relationship_context={},
            context_prompt_blocks=blocks,
        )
        system_content = messages[0]["content"]
        assert "原神" in system_content

    def test_includes_behavior_principles(self, engine):
        """人格原则必须出现在系统提示中（v1.5-T2：断言对齐 P4.4-D3 收敛后的实际 prompt）。

        旧断言查找的【行为原则】块头已被收敛移除；当前系统提示由身份段、
        【核心价值观】、【核心原则】块构成。测试适配真实输出，不改代码。
        """
        messages = engine._build_messages_original(
            user_message="你好",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={},
            relationship_context={},
            context_prompt_blocks=[],
        )
        system_content = messages[0]["content"]
        # 身份段：羽依自我定位
        assert "浅雾羽依" in system_content
        # 核心价值观块（身份层声明）
        assert "核心价值观" in system_content
        assert "真实比完美更重要" in system_content
        # 【核心原则】块（行为原则的现载体）
        assert "【核心原则】" in system_content
        assert "不确定就说不知道" in system_content

    def test_user_message_is_last(self, engine):
        """用户消息必须是最后一条"""
        messages = engine._build_messages_original(
            user_message="今天天气真好",
            history=[{"role": "user", "content": "之前的消息"}],
            chat_memories=[],
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={},
            relationship_context={},
            context_prompt_blocks=[],
        )
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "今天天气真好"

    def test_empty_contexts_produce_default_system_prompt(self, engine):
        """空上下文仍应产生有效的系统提示"""
        messages = engine._build_messages_original(
            user_message="测试",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={},
            relationship_context={},
            context_prompt_blocks=[],
        )
        system_content = messages[0]["content"]
        assert "浅雾羽依" in system_content
        assert len(messages) == 2  # system + user


class TestBuildMessagesOpt:
    """测试 _build_messages_opt 方法"""

    def test_fallback_to_original_when_token_opt_unavailable(self, engine):
        """token 优化不可用时回退到 original 模式"""
        messages = engine._build_messages_opt(
            user_message="你好",
            history=[],
            chat_memories=[],
            personality_context="温柔",
            self_model_context={"identity": "羽依"},
            emotion_context={"dominant": "平静"},
            relationship_context={},
            context_prompt_blocks=[],
        )
        system_content = messages[0]["content"]
        assert "浅雾羽依" in system_content
        assert messages[-1]["content"] == "你好"

    def test_preserves_self_model_in_opt_mode(self, engine):
        """即使在 opt 模式下，自我认知也必须保留"""
        messages = engine._build_messages_opt(
            user_message="你好",
            history=[],
            chat_memories=[],
            personality_context="",
            self_model_context={"identity": "羽依", "status": "在线"},
            emotion_context={},
            relationship_context={},
            context_prompt_blocks=[],
        )
        system_content = messages[0]["content"]
        assert "自我认知" in system_content
        assert "羽依" in system_content


class TestIdentityContextCompatibility:
    """Phase 4.0.1 Step 02-B R-4: engine.generate() 兼容 identity_context 参数。"""

    def test_generate_accepts_identity_context(self, engine):
        """engine.generate() 接受 identity_context 参数，不抛 TypeError。"""
        try:
            result = engine.generate(
                user_message="你好",
                history=[],
                chat_memories=[],
                life_events=[],
                personality_context="",
                resolved_behavior={},
                self_model_context={},
                emotion_context={},
                relationship_context={},
                identity_context={
                    "essence": "浅雾羽依",
                    "origin": "CREATOR",
                    "core_values": ["真实", "成长"],
                },
            )
            # mock 模式下直接返回字符串
            assert isinstance(result, str)
        except TypeError as e:
            pytest.fail(f"engine.generate() 不应因 identity_context 参数抛 TypeError: {e}")

    def test_generate_without_identity_context_still_works(self, engine):
        """不传 identity_context 时，generate() 行为不变（向后兼容）。"""
        result = engine.generate(
            user_message="测试",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="",
            resolved_behavior={},
            self_model_context={},
            emotion_context={},
            relationship_context={},
        )
        assert isinstance(result, str)


class TestIdentityCoreInjection:
    """Phase 4.0.2-A1: IDENTITY_CORE 必须进入 Prompt，替换旧硬编码。"""

    def test_identity_core_injected_to_prompt(self, engine):
        """Prompt 中必须包含 IDENTITY_CORE 的关键字段。"""
        messages = engine._build_messages_original(
            user_message="你好",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={},
            relationship_context={},
            context_prompt_blocks=[],
        )
        system_content = messages[0]["content"]

        from src.personality.identity_core import IDENTITY_CORE

        # 必须包含 essence
        assert IDENTITY_CORE["essence"] in system_content, (
            f"Prompt 中应包含 essence，实际内容:\n{system_content[:500]}"
        )

        # 必须包含 fundamental_nature 的关键短语
        assert "温柔而独立" in system_content or "重视理解胜过迎合" in system_content, (
            f"Prompt 中应包含 fundamental_nature 关键词，实际内容:\n{system_content[:500]}"
        )

        # 必须包含至少一条 core_value
        assert any(v in system_content for v in IDENTITY_CORE["core_values"]), (
            f"Prompt 中应包含 core_values 至少一条，实际内容:\n{system_content[:500]}"
        )

        # 必须包含至少一条 immutable_principle
        flat = [p for plist in IDENTITY_CORE["immutable_principles"].values() for p in plist]
        assert any(p in system_content for p in flat), (
            f"Prompt 中应包含 immutable_principles 至少一条，实际内容:\n{system_content[:500]}"
        )

    def test_old_hardcoded_identity_removed(self, engine):
        """旧硬编码字符串必须消失。"""
        messages = engine._build_messages_original(
            user_message="你好",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={},
            relationship_context={},
            context_prompt_blocks=[],
        )
        system_content = messages[0]["content"]

        # 旧硬编码不应出现
        assert "通过系统机制持续演化的AI人格" not in system_content, (
            f"旧硬编码仍存在于 Prompt 中:\n{system_content[:500]}"
        )
        assert "表达风格来自自身性格特质和长期学习" not in system_content, (
            f"旧硬编码仍存在于 Prompt 中:\n{system_content[:500]}"
        )

    def test_backward_compatibility_without_identity_context(self, engine):
        """不传 identity_context 时，generate() 仍应正常工作。"""
        result = engine.generate(
            user_message="测试",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="",
            resolved_behavior={},
            self_model_context={},
            emotion_context={},
            relationship_context={},
        )
        assert isinstance(result, str)
        assert len(result) > 0
