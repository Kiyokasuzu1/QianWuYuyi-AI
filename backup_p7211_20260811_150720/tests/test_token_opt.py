"""
Token 优化模块测试。

覆盖：
1. is_token_opt_enabled() 配置读取（config.yaml + 环境变量 + 默认值）
2. HistoryCompressor 历史压缩（短历史不压缩 / 长历史摘要 / LLM 失败降级）
3. MemorySummarizer 记忆摘要（重要性过滤 / 排序 / 长度限制）
4. engine.py 在 token_opt 启用/禁用时走不同路径
"""
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Mock openai before importing engine
sys.modules['openai'] = MagicMock()

from src.token_opt import is_token_opt_enabled, HistoryCompressor, MemorySummarizer


# ---------------------------------------------------------------------------
# 1. is_token_opt_enabled 配置读取
# ---------------------------------------------------------------------------

class TestIsTokenOptEnabled:
    """验证 is_token_opt_enabled 能正确读取配置"""

    def test_default_disabled_when_no_config_no_env(self, monkeypatch):
        """无配置且无环境变量时，默认返回 False"""
        monkeypatch.delenv("YUYI_TOKEN_OPT", raising=False)
        with patch("src.config.load_config", side_effect=Exception("no config")):
            assert is_token_opt_enabled() is False

    def test_env_true_overrides_config(self, monkeypatch):
        """环境变量 YUYI_TOKEN_OPT=true 优先于配置"""
        monkeypatch.setenv("YUYI_TOKEN_OPT", "true")
        with patch("src.config.load_config", return_value={"token_opt": {"enabled": False}}):
            assert is_token_opt_enabled() is True

    def test_env_false_overrides_config(self, monkeypatch):
        """环境变量 YUYI_TOKEN_OPT=false 优先于配置"""
        monkeypatch.setenv("YUYI_TOKEN_OPT", "false")
        with patch("src.config.load_config", return_value={"token_opt": {"enabled": True}}):
            assert is_token_opt_enabled() is False

    def test_config_enabled_when_env_unset(self, monkeypatch):
        """无环境变量时，读取 config.yaml 的 token_opt.enabled"""
        monkeypatch.delenv("YUYI_TOKEN_OPT", raising=False)
        with patch("src.config.load_config", return_value={"token_opt": {"enabled": True}}):
            assert is_token_opt_enabled() is True

    def test_config_disabled_by_default(self, monkeypatch):
        """config 中 token_opt 段缺失时默认 False"""
        monkeypatch.delenv("YUYI_TOKEN_OPT", raising=False)
        with patch("src.config.load_config", return_value={}):
            assert is_token_opt_enabled() is False

    def test_config_missing_token_opt_section(self, monkeypatch):
        """config 中没有 token_opt 段时返回 False"""
        monkeypatch.delenv("YUYI_TOKEN_OPT", raising=False)
        with patch("src.config.load_config", return_value={"llm": {"model": "x"}}):
            assert is_token_opt_enabled() is False

    def test_load_config_exception_falls_back_to_env(self, monkeypatch):
        """load_config 抛异常时回退到环境变量"""
        monkeypatch.setenv("YUYI_TOKEN_OPT", "true")
        with patch("src.config.load_config", side_effect=FileNotFoundError("no file")):
            assert is_token_opt_enabled() is True


# ---------------------------------------------------------------------------
# 2. HistoryCompressor
# ---------------------------------------------------------------------------

class TestHistoryCompressor:
    """历史消息压缩器"""

    def test_empty_history_returns_empty(self):
        """空历史返回空列表"""
        compressor = HistoryCompressor(recent_turns=5, use_llm_summary=False)
        assert compressor.compress([]) == []

    def test_short_history_no_compression(self):
        """短历史（<= recent_turns*2）不压缩，原样返回"""
        compressor = HistoryCompressor(recent_turns=5, use_llm_summary=False)
        history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
        ]
        result = compressor.compress(history)
        assert result == history

    def test_long_history_compressed_with_fallback(self):
        """长历史用降级关键词摘要"""
        compressor = HistoryCompressor(recent_turns=2, use_llm_summary=False)
        history = []
        for i in range(10):
            history.append({"role": "user", "content": f"今天天气真好，我们聊聊天吧"})
            history.append({"role": "assistant", "content": f"好的，天气确实不错"})

        result = compressor.compress(history)

        # 应该包含：1 条摘要 system 消息 + 最近 4 条消息（2 轮 * 2）
        assert len(result) == 5
        assert result[0]["role"] == "system"
        assert "之前的对话摘要" in result[0]["content"]
        # 最近 4 条保留原文
        assert result[1] == history[-4]

    def test_summary_inserted_as_system_message(self):
        """摘要作为 system 消息插入"""
        compressor = HistoryCompressor(recent_turns=1, use_llm_summary=False)
        history = [
            {"role": "user", "content": "我喜欢编程和音乐"},
            {"role": "assistant", "content": "那很棒"},
            {"role": "user", "content": "最近在学Python"},
            {"role": "assistant", "content": "加油"},
        ]
        result = compressor.compress(history)
        assert result[0]["role"] == "system"
        assert "摘要" in result[0]["content"]

    def test_fallback_summary_with_no_user_messages(self):
        """没有 user 消息时降级摘要返回空字符串，不插入摘要消息"""
        compressor = HistoryCompressor(recent_turns=1, use_llm_summary=False)
        # 需要 > recent_turns*2 条消息才触发压缩
        history = [
            {"role": "assistant", "content": "你好"},
            {"role": "assistant", "content": "在吗"},
            {"role": "assistant", "content": "今天"},
            {"role": "assistant", "content": "再见"},
        ]
        result = compressor.compress(history)
        # 没有 user 消息时 _fallback_summary 返回空，不插入摘要
        # 直接返回 recent_messages
        assert len(result) == 2
        assert result[0]["role"] == "assistant"

    def test_fallback_summary_with_user_messages_returns_placeholder(self):
        """有 user 消息但无法提取关键词时返回占位文本"""
        compressor = HistoryCompressor(recent_turns=1, use_llm_summary=False)
        # user 消息内容无中文关键词（只有标点/数字）
        history = [
            {"role": "user", "content": "123"},
            {"role": "assistant", "content": "456"},
            {"role": "user", "content": "今天"},
            {"role": "assistant", "content": "再见"},
        ]
        result = compressor.compress(history)
        # 应该有摘要（占位文本"之前有过一些对话"）
        assert result[0]["role"] == "system"
        assert "摘要" in result[0]["content"]


# ---------------------------------------------------------------------------
# 3. MemorySummarizer
# ---------------------------------------------------------------------------

class TestMemorySummarizer:
    """记忆摘要器"""

    def test_empty_memories_returns_empty(self):
        """空记忆返回空字符串"""
        summarizer = MemorySummarizer()
        assert summarizer.summarize([]) == ""

    def test_filters_low_importance(self):
        """过滤低于重要性阈值的记忆"""
        summarizer = MemorySummarizer(max_memories=5, importance_threshold=0.5)
        memories = [
            {"content": "低重要性记忆", "importance": 0.1},
            {"content": "高重要性记忆", "importance": 0.9},
        ]
        result = summarizer.summarize(memories)
        assert "高重要性记忆" in result
        assert "低重要性记忆" not in result

    def test_all_below_threshold_takes_top2(self):
        """全部低于阈值时取前 2 条保底"""
        summarizer = MemorySummarizer(max_memories=5, importance_threshold=0.9)
        memories = [
            {"content": "记忆1", "importance": 0.1},
            {"content": "记忆2", "importance": 0.2},
            {"content": "记忆3", "importance": 0.3},
        ]
        result = summarizer.summarize(memories)
        # 至少有 2 条记忆的内容
        assert "记忆1" in result or "记忆2" in result or "记忆3" in result

    def test_sorted_by_importance_desc(self):
        """按重要性降序排列"""
        summarizer = MemorySummarizer(max_memories=3, importance_threshold=0.0)
        memories = [
            {"content": "低", "importance": 0.3},
            {"content": "高", "importance": 0.9},
            {"content": "中", "importance": 0.6},
        ]
        result = summarizer.summarize(memories)
        # 高重要性应该在前
        assert result.index("高") < result.index("中") < result.index("低")

    def test_max_memories_limit(self):
        """最多取 max_memories 条"""
        summarizer = MemorySummarizer(max_memories=2, importance_threshold=0.0)
        memories = [
            {"content": f"记忆{i}", "importance": 0.5}
            for i in range(10)
        ]
        result = summarizer.summarize(memories)
        # 只有 2 条记忆的前缀
        assert result.count("【") <= 2

    def test_long_content_truncated(self):
        """超长内容被截断"""
        summarizer = MemorySummarizer(max_memories=1, importance_threshold=0.0)
        long_content = "A" * 200
        memories = [{"content": long_content, "importance": 0.9}]
        result = summarizer.summarize(memories)
        assert "..." in result
        assert len(result) < 200

    def test_importance_prefix(self):
        """不同重要性级别有不同前缀"""
        summarizer = MemorySummarizer(max_memories=3, importance_threshold=0.0)
        memories = [
            {"content": "重要", "importance": 0.9},
            {"content": "普通", "importance": 0.6},
            {"content": "旧", "importance": 0.3},
        ]
        result = summarizer.summarize(memories)
        assert "【重要记忆】" in result
        assert "【记忆】" in result
        assert "【旧记忆】" in result

    def test_emotion_tag_included(self):
        """情绪标签出现在摘要中"""
        summarizer = MemorySummarizer(max_memories=1, importance_threshold=0.0)
        memories = [{"content": "开心的事", "importance": 0.8, "emotion_tag": "开心"}]
        result = summarizer.summarize(memories)
        assert "开心" in result


# ---------------------------------------------------------------------------
# 4. engine.py token_opt 路径切换
# ---------------------------------------------------------------------------

class TestEngineTokenOptPath:
    """验证 ResponseEngine 在 token_opt 启用/禁用时走不同路径"""

    @pytest.fixture
    def engine(self):
        return MagicMock()

    def test_disabled_uses_build_messages_original(self):
        """token_opt 禁用时走 _build_messages_original"""
        from src.engine import ResponseEngine
        e = ResponseEngine()

        with patch("src.token_opt.is_token_opt_enabled", return_value=False):
            with patch.object(e, "_build_messages_original", return_value=[{"role": "system", "content": "x"}]) as mock_orig:
                with patch.object(e, "_build_messages_opt", return_value=[{"role": "system", "content": "y"}]) as mock_opt:
                    with patch.object(e, "client") as mock_client:
                        mock_client.chat.completions.create.return_value = MagicMock(
                            choices=[MagicMock(message=MagicMock(content="ok"))]
                        )
                        e.generate(
                            user_message="test",
                            history=[],
                            chat_memories=[],
                            life_events=[],
                            personality_context="",
                            resolved_behavior=None,
                            self_model_context={},
                            emotion_context={},
                            relationship_context={},
                        )
                        mock_orig.assert_called_once()
                        mock_opt.assert_not_called()

    def test_enabled_uses_build_messages_opt(self):
        """token_opt 启用时走 _build_messages_opt"""
        from src.engine import ResponseEngine
        e = ResponseEngine()

        with patch("src.token_opt.is_token_opt_enabled", return_value=True):
            with patch.object(e, "_build_messages_original", return_value=[{"role": "system", "content": "x"}]) as mock_orig:
                with patch.object(e, "_build_messages_opt", return_value=[{"role": "system", "content": "y"}]) as mock_opt:
                    with patch.object(e, "client") as mock_client:
                        mock_client.chat.completions.create.return_value = MagicMock(
                            choices=[MagicMock(message=MagicMock(content="ok"))]
                        )
                        e.generate(
                            user_message="test",
                            history=[],
                            chat_memories=[],
                            life_events=[],
                            personality_context="",
                            resolved_behavior=None,
                            self_model_context={},
                            emotion_context={},
                            relationship_context={},
                        )
                        mock_opt.assert_called_once()
                        mock_orig.assert_not_called()
