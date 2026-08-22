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
        identity_context: dict = None,    # P4.0.1 Step 02-B R-4 兼容参数；P4.4-D5 起 str 形态经 context_prompt_blocks 注入
        user_meta: dict = None,           # Phase 4.0.4-Pre：对方是谁/怎么称呼/关系等级
        communication_profile = None,     # Phase 4.1.2-B：CommunicationStyle 表达倾向
    ) -> str:
        # P4.4-D5: legacy 链 identity_context 死参数恢复 —— 转为 system 块经
        # context_prompt_blocks 注入（original/opt 两链共用，位置在常规
        # section 之后、【核心原则】之前）。文本内容原样保留；无【】块头时
        # 用项目既有 wrap_section 包装，不修改身份文本本身。
        # dict 形态无消费者语义，保持旧行为（不注入）。
        if isinstance(identity_context, str) and identity_context.strip():
            from src.response.prompt_sections import wrap_section
            identity_block = {
                "role": "system",
                "content": wrap_section("【身份状态】", identity_context),
            }
            context_prompt_blocks = [identity_block] + list(context_prompt_blocks or [])

        token_opt_enabled = self._is_token_opt_enabled()

        if token_opt_enabled:
            messages = self._build_messages_opt(
                user_message=user_message,
                history=history,
                chat_memories=chat_memories,
                life_events=life_events,  # P4.4-D4：opt 链补接入（此前被静默丢弃）
                personality_context=personality_context,
                self_model_context=self_model_context,
                emotion_context=emotion_context,
                relationship_context=relationship_context,
                context_prompt_blocks=context_prompt_blocks,
                experience_context=experience_context,
                user_meta=user_meta,
                communication_profile=communication_profile,
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
                user_meta=user_meta,
                communication_profile=communication_profile,
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

    def _build_identity_prompt(self) -> str:
        """从 IDENTITY_CORE 构建核心身份提示（仅第一层 Core Identity，不含动态状态）。

        Phase 4.4-D2：canonical 实现已移至 src/response/prompt_sections.py
        （build_identity_block，include_full=True），此处保留原方法名做兼容委托。
        """
        from src.response.prompt_sections import build_identity_block
        return build_identity_block()

    @staticmethod
    def _format_user_meta_block(user_meta: dict | None, communication_profile=None) -> str:
        """Phase 4.1.2-B：组装 user_meta 块。

        Phase 4.4-D1：唯一 canonical 实现已移至 src/response/prompt_sections.py
        （build_user_meta_block），此处保留原方法名做兼容委托，调用方不变。
        """
        from src.response.prompt_sections import build_user_meta_block
        return build_user_meta_block(user_meta, communication_profile)

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
        user_meta: dict = None,           # Phase 4.0.4-Pre
        communication_profile = None,     # Phase 4.1.2-B
    ) -> list:
        """
        构建完整系统提示 — 包含所有可用上下文。
        """
        system_parts = []

        # === 核心身份（IDENTITY_CORE 驱动）===
        system_parts.append(self._build_identity_prompt())

        # === Phase 4.1.2-B：user_meta + communication_profile ===
        # 放在身份声明之后、人格/情绪/记忆之前，保证关系信息优先于内部状态
        user_meta_text = self._format_user_meta_block(user_meta, communication_profile)
        if user_meta_text:
            system_parts.append(user_meta_text)

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
                text = self_model_context.strip()
                # Phase 4.4-B：文本首行自带块头（如 SelfModelContextProvider 输出的
                # 【自我认知参考】）时不再包一层【自我认知】，避免嵌套双块头
                first_line = text.split("\n", 1)[0]
                if first_line.startswith("【") and "】" in first_line:
                    system_parts.append(text)
                else:
                    system_parts.append(f"【自我认知】\n{text}")

        # === Phase A.2 + P4.4-C: Historical Experience Context ===
        # 位置：自我认知之后、情绪/关系/记忆之前
        # 职责：仅提供历史背景，不影响人格计算
        # P4.4-C：与 Runtime 链共用 build_experience_context
        # （importance top-10 + 原始记忆元数据清洗 + evidence 截断）
        if experience_context:
            try:
                from src.personality.experience_context import build_experience_context
                exp_text = build_experience_context(experience_context)
                if exp_text:
                    system_parts.append(exp_text)
            except Exception:
                # 隔离：注入失败不影响主流程
                pass

        # === 关系状态 ===
        # P4.2-IMPL-C6C: str 为正式契约（RelationshipSnapshot 筛选后事实，
        # 由 render_relationship_facts 生成）；dict 为 legacy fallback。
        # P4.4-D3：位置移到情绪之前（目标序：关系优先于情绪/记忆/经历）。
        if relationship_context and isinstance(relationship_context, str) and relationship_context.strip():
            system_parts.append(f"【用户关系】\n{relationship_context.strip()}")
        elif relationship_context and isinstance(relationship_context, dict):
            rel_parts = []
            for k, v in relationship_context.items():
                if v and str(v).strip():
                    rel_parts.append(f"  {k}: {v}")
            if rel_parts:
                system_parts.append(f"【用户关系】\n" + "\n".join(rel_parts))

        # === 情绪状态 ===
        if emotion_context:
            emo = emotion_context.get("dominant", "") or emotion_context.get("primary_emotion", "")
            intensity = emotion_context.get("intensity", 0)
            if emo:
                emotion_block = (
                    f"【当前情绪】\n"
                    f"- 主导情绪: {emo}\n"
                    f"- 强度: {intensity:.1f}"
                )
                # Emotion System 2.0（E-Emotion-5）：可选表达策略行（描述性倾向，
                # 不覆盖人格；仅在调用方提供了策略文本时追加）
                strategy = (
                    emotion_context.get("response_strategy")
                    or emotion_context.get("strategy")
                )
                if isinstance(strategy, str) and strategy.strip():
                    emotion_block += f"\n- 表达策略: {strategy.strip()}"
                system_parts.append(emotion_block)

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

        # === 核心原则（Phase 4.4-B：与 Runtime 正式链共用统一文本）===
        from src.response.principles import build_principles_block
        system_parts.append(build_principles_block())

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
        user_meta: dict = None,           # Phase 4.0.4-Pre
        communication_profile = None,     # Phase 4.1.2-B
        life_events: list = None,         # P4.4-D4：opt 链补接入（带默认值，旧调用方零破坏）
    ) -> list:
        """Token 优化模式：记忆合并摘要 + 历史压缩"""
        try:
            from src.token_opt import MemorySummarizer, HistoryCompressor
        except Exception as e:
            return self._build_messages_original(
                user_message, history, chat_memories, life_events or [],
                personality_context, self_model_context,
                emotion_context, relationship_context, context_prompt_blocks,
                experience_context, user_meta=user_meta,
                communication_profile=communication_profile,
            )

        # 1. 记忆摘要
        try:
            summarizer = MemorySummarizer()
            memory_summary = summarizer.summarize(chat_memories)
        except Exception:
            memory_summary = ""

        # 2. 构建系统提示（与 original 模式保持一致）
        system_parts = []

        system_parts.append(self._build_identity_prompt())

        # Phase 4.1.2-B：user_meta + communication_profile
        user_meta_text = self._format_user_meta_block(user_meta, communication_profile)
        if user_meta_text:
            system_parts.append(user_meta_text)

        if personality_context:
            system_parts.append(f"【人格】\n{personality_context}")

        if self_model_context:
            if isinstance(self_model_context, dict):
                sm = "\n".join(f"  {k}: {v}" for k, v in self_model_context.items() if v)
                if sm:
                    system_parts.append(f"【自我认知】\n{sm}")
            elif isinstance(self_model_context, str) and self_model_context.strip():
                # P4.4-D4：str 型自我认知此前被静默丢弃，补上与 original 一致的逻辑
                # （首行自带块头如【自我认知参考】时不二次包装）。
                text = self_model_context.strip()
                first_line = text.split("\n", 1)[0]
                if first_line.startswith("【") and "】" in first_line:
                    system_parts.append(text)
                else:
                    system_parts.append(f"【自我认知】\n{text}")

        # === Phase A.2 + P4.4-C: Historical Experience Context (token-opt 模式) ===
        if experience_context:
            try:
                from src.personality.experience_context import build_experience_context
                exp_text = build_experience_context(experience_context)
                if exp_text:
                    system_parts.append(exp_text)
            except Exception:
                pass

        if emotion_context:
            emo = emotion_context.get("dominant", "")
            if emo:
                system_parts.append(f"【当前情绪】{emo}")

        # P4.2-IMPL-C6C: str 为正式契约（Snapshot 筛选后事实）；dict 为 legacy fallback
        if isinstance(relationship_context, str) and relationship_context.strip():
            system_parts.append(f"【用户关系】\n{relationship_context.strip()}")
        elif relationship_context:
            rel = "\n".join(f"  {k}: {v}" for k, v in relationship_context.items() if v)
            if rel:
                system_parts.append(f"【用户关系】\n{rel}")

        if memory_summary:
            system_parts.append(f"【相关记忆】\n{memory_summary}")

        # === P4.4-D4：重要经历（与 original 同一数据契约与格式）===
        if life_events:
            events_text = "\n".join(
                f"  - {e}" if isinstance(e, str) else f"  - {e.get('description', str(e))}"
                for e in life_events[:5]
            )
            system_parts.append(f"【重要经历】\n{events_text}")

        if context_prompt_blocks:
            for block in context_prompt_blocks:
                # Phase 4.4-B：空 content 不再 append，避免空 section 产生连续空行
                if block.get("role") == "system" and block.get("content"):
                    system_parts.append(block.get("content", ""))

        # === P4.4-D4：核心原则 + 当前时间（补齐 opt 链功能契约，与 original 一致）===
        from src.response.principles import build_principles_block
        system_parts.append(build_principles_block())

        from datetime import datetime
        system_parts.append(f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")

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
