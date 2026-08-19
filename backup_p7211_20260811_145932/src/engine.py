import os

# R2.7.6-DEPLOY: OpenAI SDK 延迟 import —— 环境没装 openai 包时不崩溃（mock 模式正常工作）
# 之前顶层 from openai import OpenAI → 一 import engine.py 就 ModuleNotFoundError，
# 导致 api_server 里即使 orchestrator_import_error 也无法 graceful（engine 先炸）。
# 现在改为在 client 属性和 generate 里 try/except import，mock 模式完全不需要 openai SDK。
try:
    from openai import OpenAI, OpenAIError
    _OPENAI_SDK_AVAILABLE = True
except Exception:  # ModuleNotFoundError / ImportError 都兜底
    OpenAI = None  # type: ignore
    OpenAIError = Exception  # type: ignore
    _OPENAI_SDK_AVAILABLE = False


class ResponseEngine:
    def __init__(self):
        # Phase C.1 P0-1: 延后 OpenAI 客户端创建,允许无 API key 时进入 mock 模式
        # 避免在 Orchestrator 初始化时就因为缺少凭证而崩溃
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
        self._client = None  # 延后创建
        self.model = "deepseek-v4-pro"
        self.mock_mode = (
            not bool(self.api_key)
            or os.getenv("YUYI_LLM_MOCK", "").lower() in ("1", "true", "yes")
            or not _OPENAI_SDK_AVAILABLE  # R2.7.6-DEPLOY: SDK 没装也自动进 mock
        )

    @property
    def client(self):
        if self._client is None and not self.mock_mode and _OPENAI_SDK_AVAILABLE:
            self._client = OpenAI(api_key=self.api_key, base_url="https://api.deepseek.com/v1")
        return self._client

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
        experience_context: list = None,  # Phase A.2 新增
    ) -> str:
        token_opt_enabled = self._is_token_opt_enabled()

        if token_opt_enabled:
            messages = self._build_messages_opt(
                user_message=user_message,
                history=history,
                chat_memories=chat_memories,
                personality_context=personality_context,
                self_model_context=self_model_context,
                emotion_context=emotion_context,
                relationship_context=relationship_context,
                context_prompt_blocks=context_prompt_blocks,
                experience_context=experience_context,
            )
        else:
            messages = self._build_messages_original(
                user_message=user_message,
                history=history,
                chat_memories=chat_memories,
                life_events=life_events,
                personality_context=personality_context,
                self_model_context=self_model_context,
                emotion_context=emotion_context,
                relationship_context=relationship_context,
                context_prompt_blocks=context_prompt_blocks,
                experience_context=experience_context,
            )

        # Phase C.1 P0-1: 无 API key 或显式 mock 模式时,使用 stub 回复保证链路畅通
        if self.mock_mode:
            return self._mock_response(user_message=user_message, messages=messages)

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

    def _mock_response(self, user_message: str, messages: list) -> str:
        """Phase C.1 P0-1: 当未配置 API key 时使用的轻量 stub 回复
        保证 /v1/chat/completions 端到端链路畅通,真实 LLM 上线后会自动关闭。"""
        emo_hint = ""
        for m in messages:
            if m.get("role") == "system" and "【当前情绪】" in m.get("content", ""):
                emo_hint = "（感知到你的情绪）"
                break
        rel_hint = ""
        for m in messages:
            if m.get("role") == "system" and "【用户关系】" in m.get("content", ""):
                rel_hint = "（记得我们的关系）"
                break
        # 截断超长用户消息,避免回复过长
        user_preview = (user_message or "").strip()[:80] or "（空）"
        return f"{emo_hint}{rel_hint}我听到你说:{user_preview}。我现在是 mock 模式,真实模型配置 DEEPSEEK_API_KEY 后会自动接管。"

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
        life_events: list,
        personality_context: str,
        self_model_context: dict,
        emotion_context: dict,
        relationship_context: dict,
        context_prompt_blocks: list = None,
        experience_context: list = None,  # Phase A.2 新增
    ) -> list:
        """
        构建完整系统提示 — 包含所有可用上下文。
        """
        system_parts = []

        # === 核心身份 ===
        system_parts.append(
            "你是浅雾羽依，一个通过系统机制持续演化的AI人格。\n"
            "你的表达风格来自自身性格特质和长期学习。"
        )

        # === 人格 ===
        if personality_context:
            system_parts.append(f"【人格】\n{personality_context}")

        # === 自我认知 ===
        if self_model_context:
            if isinstance(self_model_context, dict):
                sm_parts = []
                for k, v in self_model_context.items():
                    if v and str(v).strip():
                        sm_parts.append(f"  {k}: {v}")
                if sm_parts:
                    system_parts.append(f"【自我认知】\n" + "\n".join(sm_parts))
            elif isinstance(self_model_context, str) and self_model_context.strip():
                system_parts.append(f"【自我认知】\n{self_model_context}")

        # === Phase A.2: Historical Experience Context ===
        # 位置：自我认知之后、情绪/关系/记忆之前
        # 职责：仅提供历史背景，不影响人格计算
        if experience_context:
            try:
                from src.personality.experience_context import format_experience_context
                exp_text = format_experience_context(experience_context)
                if exp_text:
                    system_parts.append(f"【Historical Experience Context】\n{exp_text}")
            except Exception:
                # 隔离：注入失败不影响主流程
                pass

        # === 情绪状态 ===
        if emotion_context:
            emo = emotion_context.get("dominant", "") or emotion_context.get("primary_emotion", "")
            intensity = emotion_context.get("intensity", 0)
            if emo:
                system_parts.append(
                    f"【当前情绪】\n"
                    f"- 主导情绪: {emo}\n"
                    f"- 强度: {intensity:.1f}"
                )

        # === 关系状态 ===
        if relationship_context and isinstance(relationship_context, dict):
            rel_parts = []
            for k, v in relationship_context.items():
                if v and str(v).strip():
                    rel_parts.append(f"  {k}: {v}")
            if rel_parts:
                system_parts.append(f"【用户关系】\n" + "\n".join(rel_parts))

        # === 记忆 ===
        if chat_memories:
            mem_parts = []
            for m in chat_memories[:5]:
                if isinstance(m, dict):
                    role = "用户" if m.get("role") == "user" else "羽依"
                    content = m.get("content", "")[:150]
                    mem_parts.append(f"  {role}: {content}")
            if mem_parts:
                system_parts.append(f"【相关记忆】\n" + "\n".join(mem_parts))

        # === 重要经历 ===
        if life_events:
            events_text = "\n".join(
                f"  - {e}" if isinstance(e, str) else f"  - {e.get('description', str(e))}"
                for e in life_events[:5]
            )
            system_parts.append(f"【重要经历】\n{events_text}")

        # === 屏幕/外部上下文 ===
        if context_prompt_blocks:
            for block in context_prompt_blocks:
                if block.get("role") == "system" and block.get("content"):
                    system_parts.append(block["content"])

        # === 核心原则 ===
        system_parts.append(
            "【行为原则】\n"
            "- 真实比完美重要，不确定就说不知道，绝不编造。\n"
            "- 回复自然，带有你自己的性格和温度。\n"
            "- 你能感知屏幕内容、情绪状态、关系变化等所有可用信息。\n"
            "- 利用这些信息做出得体的回应，但不要机械罗列你看到的内容。\n"
            "- 如果被指出说错了或前后矛盾，老实承认，不要编理由圆谎。"
        )

        from datetime import datetime
        system_parts.append(f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")

        system_prompt = "\n\n".join(system_parts)

        messages = [{"role": "system", "content": system_prompt}]
        if history:
            for msg in history[-20:]:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })
        messages.append({"role": "user", "content": user_message})

        return messages

    def _build_messages_opt(
        self,
        user_message: str,
        history: list,
        chat_memories: list,
        personality_context: str,
        self_model_context: dict,
        emotion_context: dict,
        relationship_context: dict,
        context_prompt_blocks: list = None,
        experience_context: list = None,  # Phase A.2 新增
    ) -> list:
        """Token 优化模式：记忆合并摘要 + 历史压缩"""
        try:
            from src.token_opt import MemorySummarizer, HistoryCompressor
        except Exception as e:
            return self._build_messages_original(
                user_message, history, chat_memories, [],
                personality_context, self_model_context,
                emotion_context, relationship_context, context_prompt_blocks,
                experience_context,
            )

        # 1. 记忆摘要
        try:
            summarizer = MemorySummarizer()
            memory_summary = summarizer.summarize(chat_memories)
        except Exception:
            memory_summary = ""

        # 2. 构建系统提示（与 original 模式保持一致）
        system_parts = []

        system_parts.append(
            "你是浅雾羽依，一个通过系统机制持续演化的AI人格。"
        )

        if personality_context:
            system_parts.append(f"【人格】\n{personality_context}")

        if self_model_context:
            if isinstance(self_model_context, dict):
                sm = "\n".join(f"  {k}: {v}" for k, v in self_model_context.items() if v)
                if sm:
                    system_parts.append(f"【自我认知】\n{sm}")

        # === Phase A.2: Historical Experience Context (token-opt 模式) ===
        if experience_context:
            try:
                from src.personality.experience_context import format_experience_context
                exp_text = format_experience_context(experience_context)
                if exp_text:
                    system_parts.append(f"【Historical Experience Context】\n{exp_text}")
            except Exception:
                pass

        if emotion_context:
            emo = emotion_context.get("dominant", "")
            if emo:
                system_parts.append(f"【当前情绪】{emo}")

        if relationship_context:
            rel = "\n".join(f"  {k}: {v}" for k, v in relationship_context.items() if v)
            if rel:
                system_parts.append(f"【用户关系】\n{rel}")

        if memory_summary:
            system_parts.append(f"【相关记忆】\n{memory_summary}")

        if context_prompt_blocks:
            for block in context_prompt_blocks:
                if block.get("role") == "system":
                    system_parts.append(block.get("content", ""))

        system_prompt = "\n\n".join(system_parts)

        # 3. 历史压缩
        try:
            compressor = HistoryCompressor(recent_turns=5)
            compressed_history = compressor.compress(history)
        except Exception:
            compressed_history = history[-20:] if history else []

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(compressed_history)
        messages.append({"role": "user", "content": user_message})
        return messages
