import os
from openai import OpenAI

class ResponseEngine:
    def __init__(self):
        # 请将 "你的DeepSeek API Key" 替换为真实的 API Key
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com/v1")
        self.model = "deepseek-v4-pro"

    def generate(
        self,
        user_message: str,
        history: list,
        chat_memories: list,
        life_events: list,
        personality_context: str,
        resolved_behavior: dict,
        self_model_context: dict,
        emotion_context: dict,
        relationship_context: dict,
        context_prompt_blocks: list = None,
    ) -> str:
        # 检查是否启用 token 优化（默认关闭，保持向后兼容）
        token_opt_enabled = self._is_token_opt_enabled()

        if token_opt_enabled:
            # === Token 优化模式 ===
            messages = self._build_messages_opt(
                user_message=user_message,
                history=history,
                chat_memories=chat_memories,
                personality_context=personality_context,
                emotion_context=emotion_context,
                context_prompt_blocks=context_prompt_blocks,
            )
        else:
            # === 原始模式（向后兼容） ===
            messages = self._build_messages_original(
                user_message=user_message,
                history=history,
                chat_memories=chat_memories,
                personality_context=personality_context,
                emotion_context=emotion_context,
                context_prompt_blocks=context_prompt_blocks,
            )

        # 调用 DeepSeek
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.8,
                max_tokens=2048,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"[DeepSeek] 调用失败: {e}")
            return f"抱歉，我遇到了一点问题：{str(e)}"

    def _is_token_opt_enabled(self) -> bool:
        """
        检查是否启用 token 优化。

        优先检查环境变量，再检查配置文件。
        默认关闭，保持向后兼容。
        """
        try:
            from src.token_opt import is_token_opt_enabled
            return is_token_opt_enabled()
        except Exception:
            return False

    def _build_messages_original(
        self,
        user_message: str,
        history: list,
        chat_memories: list,
        personality_context: str,
        emotion_context: dict,
        context_prompt_blocks: list = None,
    ) -> list:
        """
        原始模式：构建消息列表（与修改前完全一致）。
        """
        # 构建系统提示
        system_parts = []
        if personality_context:
            system_parts.append(f"人格：{personality_context}")
        if emotion_context and emotion_context.get("dominant"):
            system_parts.append(f"情绪：{emotion_context.get('dominant')}")
        if context_prompt_blocks:
            for block in context_prompt_blocks:
                if block.get("role") == "system":
                    system_parts.append(block.get("content", ""))
        system_prompt = "\n\n".join(system_parts) if system_parts else "你是浅雾羽依，一个温柔、害羞但正在成长的AI个体。"

        # 构建消息列表
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            for msg in history[-20:]:  # 最近10轮
                messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        messages.append({"role": "user", "content": user_message})

        return messages

    def _build_messages_opt(
        self,
        user_message: str,
        history: list,
        chat_memories: list,
        personality_context: str,
        emotion_context: dict,
        context_prompt_blocks: list = None,
    ) -> list:
        """
        Token 优化模式：
        - 记忆合并为摘要，放入系统提示
        - 历史消息压缩（最近 N 轮原文 + 更早的 LLM 摘要）
        """
        try:
            from src.token_opt import MemorySummarizer, HistoryCompressor
        except Exception as e:
            print(f"[TokenOpt] 模块加载失败，回退到原始模式: {e}")
            return self._build_messages_original(
                user_message, history, chat_memories,
                personality_context, emotion_context, context_prompt_blocks
            )

        # 1. 记忆摘要
        try:
            summarizer = MemorySummarizer()
            memory_summary = summarizer.summarize(chat_memories)
        except Exception as e:
            print(f"[TokenOpt] 记忆摘要失败: {e}")
            memory_summary = ""

        # 2. 构建系统提示（包含记忆摘要）
        system_parts = []
        if personality_context:
            system_parts.append(f"人格：{personality_context}")
        if emotion_context and emotion_context.get("dominant"):
            system_parts.append(f"情绪：{emotion_context.get('dominant')}")
        if memory_summary:
            system_parts.append(f"相关记忆：\n{memory_summary}")
        if context_prompt_blocks:
            for block in context_prompt_blocks:
                if block.get("role") == "system":
                    system_parts.append(block.get("content", ""))
        system_prompt = "\n\n".join(system_parts) if system_parts else "你是浅雾羽依，一个温柔、害羞但正在成长的AI个体。"

        # 3. 历史消息压缩
        try:
            compressor = HistoryCompressor(recent_turns=5)
            compressed_history = compressor.compress(history)
        except Exception as e:
            print(f"[TokenOpt] 历史压缩失败，使用原始历史: {e}")
            compressed_history = history[-20:] if history else []

        # 4. 组装消息
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(compressed_history)
        messages.append({"role": "user", "content": user_message})

        return messages
