"""
历史消息压缩器。

- 最近 N 轮对话保留原文
- 更早的对话用 LLM 生成摘要
- 摘要作为一条 system 消息插入，不占 user/assistant 消息位
"""

from typing import Dict, List, Optional


class HistoryCompressor:
    """
    历史消息压缩器。

    设计原则：
    - 最近 recent_turns 轮保留原文（保证上下文连续性）
    - 更早的对话用 LLM 生成摘要（节省 token）
    - 摘要生成失败时降级为关键词摘要
    """

    def __init__(self, recent_turns: int = 5, use_llm_summary: bool = True):
        self.recent_turns = recent_turns
        self.use_llm_summary = use_llm_summary

    def compress(
        self,
        history: List[Dict],
        user_id: str = "default",
    ) -> List[Dict]:
        """
        压缩对话历史。

        Args:
            history: 完整对话历史，格式为 [{"role": "...", "content": "..."}]
            user_id: 用户 ID（预留，用于未来缓存）

        Returns:
            压缩后的消息列表
        """
        if not history:
            return []

        # 对话不多，不用压缩
        if len(history) <= self.recent_turns * 2:
            return history

        # 拆分：旧对话 + 最近 N 轮
        split_index = len(history) - self.recent_turns * 2
        old_messages = history[:split_index]
        recent_messages = history[split_index:]

        # 生成旧对话摘要
        summary = self._generate_summary(old_messages)

        # 组装结果
        result = []
        if summary:
            result.append({
                "role": "system",
                "content": f"【之前的对话摘要】{summary}",
            })
        result.extend(recent_messages)

        return result

    def _generate_summary(self, messages: List[Dict]) -> str:
        """
        生成旧对话摘要。

        优先使用 LLM 生成，失败时降级为关键词摘要。
        """
        if not messages:
            return ""

        if self.use_llm_summary:
            try:
                llm_summary = self._llm_summarize(messages)
                if llm_summary:
                    return llm_summary
            except Exception as e:
                print(f"[HistoryCompressor] LLM 摘要失败，降级: {e}")

        # 降级方案
        return self._fallback_summary(messages)

    def _llm_summarize(self, messages: List[Dict]) -> str:
        """用 LLM 生成对话摘要"""
        # 最多取 20 轮旧对话做摘要（避免摘要本身太费 token）
        sample = messages[-20:] if len(messages) > 20 else messages

        # 拼接对话文本
        dialog_lines = []
        for m in sample:
            role_label = "你" if m.get("role") == "user" else "我"
            content = m.get("content", "")[:80]
            dialog_lines.append(f"{role_label}：{content}")

        dialog_text = "\n".join(dialog_lines)

        # 调用 LLM 生成摘要
        from src.engine import ResponseEngine
        engine = ResponseEngine()

        summary_prompt = (
            "请用一两句话概括以下对话的核心内容和情感基调，"
            "要简洁，不要超过 80 个字，直接说结果不要加前缀：\n\n"
            f"{dialog_text}"
        )

        summary = engine.generate(
            user_message=summary_prompt,
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="你是一个对话摘要助手，擅长简洁概括对话内容。",
            resolved_behavior=None,
            self_model_context={},
            emotion_context={},
            relationship_context={},
        )

        return summary.strip() if summary else ""

    @staticmethod
    def _fallback_summary(messages: List[Dict]) -> str:
        """降级方案：关键词摘要"""
        user_contents = [
            m.get("content", "")
            for m in messages
            if m.get("role") == "user"
        ]
        if not user_contents:
            return ""

        import re
        from collections import Counter

        all_text = " ".join(user_contents)
        words = re.findall(r'[\u4e00-\u9fa5]{2,}', all_text)
        counter = Counter(words)
        top_words = [w for w, _ in counter.most_common(5)]

        if top_words:
            return f"之前聊了关于{', '.join(top_words)}的话题"
        return "之前有过一些对话"
