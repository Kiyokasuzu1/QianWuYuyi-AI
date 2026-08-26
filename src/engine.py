import os
import logging

logger = logging.getLogger(__name__)

# v1.5.5 Memory Recall Fix: 记忆注入硬预算兜底（显式化，非隐式裁决）。
# 最终条数由 orchestrator selection 层决定（memory.injection_max_total，默认 6）；
# 此处仅防未来调用方传入超长列表导致 prompt 膨胀。
MEMORY_INJECTION_HARD_CAP = 6

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


def _record_injection_legacy(local_vars: dict) -> None:
    """v1.5-T3b: 顶层链（legacy，生产实际主链）注入台账挂点。

    生产实测：审计日志 100% 由本文件产出，说明生产走 orchestrator→顶层
    engine 链（runtime 链静默降级），故台账必须在此挂点。fail-soft：任何
    异常静默跳过。变量经 locals() 取，缺失记 0。
    """
    try:
        from src.audit.injection_ledger import record_injection, is_ledger_enabled
        if not is_ledger_enabled():
            return

        def _l(name):
            v = local_vars.get(name)
            return len(v) if isinstance(v, str) else 0

        record_injection({
            # 顶层链身份块(index0)合并了 yui_core，全部计入 identity
            "yui_core": 0,
            "identity": _l("identity_block_text") or (
                len(local_vars.get("system_parts")[0])
                if isinstance(local_vars.get("system_parts"), list) and local_vars["system_parts"]
                else 0
            ),
            "user_meta": _l("user_meta_text"),
            "agreement": 0,
            "personality": _l("personality_context"),
            "behavior": 0,  # 原则块并入 system（粗粒度不计）
            "self_model": _l("self_model_text") or _l("text"),
            "goal": _l("goal_context"),
            "experience": _l("exp_text") or _l("experience_text"),
            "relationship": _l("relationship_context"),
            "emotion": _l("emotion_block") or _l("emotion_context"),
            "temporal": _l("temporal_context"),
            "context_blocks": sum(
                len(b.get("content", "")) if isinstance(b, dict) else len(b)
                for b in (local_vars.get("context_prompt_blocks") or [])
                if isinstance(b, (str, dict))
            ),
            # v1.5.5 Memory Recall Fix: 记忆注入字符 = 实际渲染的记忆块字符数。
            # original 路径渲染结果为 mem_parts（行列表，含【相关记忆】头未计入，
            # 只计内容字符）；opt 路径为 memory_summary 字符串。
            "chat_memories": (
                sum(len(str(p)) for p in local_vars.get("mem_parts") or [])
                if isinstance(local_vars.get("mem_parts"), list)
                else _l("memory_summary")
            ),
        })
    except Exception:  # noqa: BLE001
        pass


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
        # v1.5-T1: 空回复有界重试次数（HTTP 200 但 content 为空时）。
        # 与 src/response/llm.py 的重试语义对齐；<0 视为 0（等价旧行为）。
        try:
            from src.config import get as _cfg_get
            _retry = int(_cfg_get("llm.empty_content_retry", 2) or 2)
        except Exception:  # noqa: BLE001
            _retry = 2
        self._empty_retry_max = _retry if _retry >= 0 else 0

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
        goal_context: str = None,         # v1.3 Phase 2：GoalContext（legacy 链兼容参数；None/空=旧行为，不注入）
        temporal_context: str = None,     # B1b：非空时替换"当前时间"裸行为 temporal_context 块（None=旧行为逐字节兼容）
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

        # v1.3 Phase 2 补丁(B1f): legacy 链 goal_context 兼容 —— orchestrator legacy
        # 路径会传入 goal_context(goal_context_enabled 开启时非空)。此前签名缺失该
        # 参数导致 TypeError → 整链降级为兜底文案。resolve_goal_context_text 的
        # 产出已自带块头,此处作为独立 system 块追加,不二次包装;None/空=旧行为。
        if isinstance(goal_context, str) and goal_context.strip():
            context_prompt_blocks = list(context_prompt_blocks or []) + [
                {"role": "system", "content": goal_context.strip()}
            ]

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
                temporal_context=temporal_context,
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
                temporal_context=temporal_context,
            )

        # Phase C.1 P0-1: 无 API key 或显式 mock 模式时,使用 stub 回复保证链路畅通
        if self.mock_mode:
            return self._mock_response(user_message=user_message, messages=messages)

        # 调用 DeepSeek
        _t0 = None
        try:
            import time as _time
            # B1f: max_tokens 由硬编码 2048 改为读配置 llm.max_tokens(与
            # src/response/llm.py 同源)。2048 对推理型模型不够——推理先消耗补全
            # 预算,曾导致 finish_reason=length 且 content_len=0(空回复→降级)。
            try:
                from src.config import get as _cfg_get
                _max_tokens = int(_cfg_get("llm.max_tokens", 4096) or 4096)
            except Exception:  # noqa: BLE001
                _max_tokens = 4096
            # v1.5-T1: 空回复有界重试——仅对 HTTP 200 但 content 为空的情况。
            # 语义与 src/response/llm.py 对齐：重试耗尽返回空串（不抛异常，
            # 由上层 orchestrator 兜底）；异常处理仍在最外层 try/except。
            for attempt in range(self._empty_retry_max + 1):
                _t0 = _time.monotonic()
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.8,
                    max_tokens=_max_tokens,
                )
                # Phase B1e P0: 结构化响应日志（只增加可观测性；不改变返回值/异常处理/
                # 不打印用户与回复全文、不泄露 token）
                try:
                    _latency = int((_time.monotonic() - _t0) * 1000)
                    _choices = getattr(response, "choices", None) or []
                    _choice0 = _choices[0] if _choices else None
                    _msg = getattr(_choice0, "message", None) if _choice0 is not None else None
                    _content = getattr(_msg, "content", None) if _msg is not None else None
                    _finish = getattr(_choice0, "finish_reason", None) if _choice0 is not None else None
                    _usage = getattr(response, "usage", None)
                    logger.info(
                        "[LLM_RESPONSE_AUDIT] status=200 endpoint=/v1/chat/completions model=%s "
                        "latency_ms=%d finish_reason=%s content_len=%d choices=%d has_content=%s "
                        "prompt_tokens=%s completion_tokens=%s total_tokens=%s",
                        getattr(response, "model", "") or "",
                        _latency,
                        _finish,
                        len(_content) if isinstance(_content, str) else 0,
                        len(_choices),
                        bool(_content),
                        getattr(_usage, "prompt_tokens", "") if _usage is not None else "",
                        getattr(_usage, "completion_tokens", "") if _usage is not None else "",
                        getattr(_usage, "total_tokens", "") if _usage is not None else "",
                    )
                except Exception:  # noqa: BLE001
                    pass  # 日志失败绝不影响主链路
                if isinstance(_content, str) and _content.strip():
                    return _content
                # 空 content：有界退避重试（仅空内容场景，不吞异常）
                if attempt < self._empty_retry_max:
                    logger.warning(
                        "[LLM_EMPTY_RETRY] attempt=%d/%d", attempt + 1, self._empty_retry_max + 1,
                    )
                    _time.sleep(min(2.0 ** attempt, 4.0))
            # 重试耗尽仍空 → 返回空串（保持旧语义：上层 orchestrator 走兜底）
            return ""
        except Exception as e:
            try:
                _latency = int((_time.monotonic() - _t0) * 1000) if _t0 is not None else -1
                logger.warning(
                    "[LLM_RESPONSE_AUDIT] status=exception endpoint=/v1/chat/completions "
                    "latency_ms=%d exception=%s",
                    _latency, type(e).__name__,
                )
            except Exception:  # noqa: BLE001
                pass
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
        temporal_context: str = None,     # B1b：非空时替换"当前时间"裸行（None=旧行为）
    ) -> list:
        """
        构建完整系统提示 — 包含所有可用上下文。
        """
        system_parts = []

        # === 核心身份（IDENTITY_CORE 驱动）===
        system_parts.append(self._build_identity_prompt())

        # === v1.5.5 Governance C2-e: YUI_CORE + Relationship Core 常驻块 ===
        # 修复：YUI_CORE 此前只在 runtime 链注入，生产链缺失。
        # 两链共用同一构建函数；flag 保护（relationship_core.enabled）。
        # 常驻层不参与 episodic 窗口竞争——检索失效/记忆洪峰不影响。
        # M1-5: YUI_CORE 解除 relationship_core.enabled 连带门控——
        # 身份核心事实必须无条件注入；开关只控制 Relationship Core 块。
        try:
            from src.config import get as _cfg_get
            _rc_enabled = bool(_cfg_get("relationship_core.enabled", True))
        except Exception:  # noqa: BLE001
            _rc_enabled = True
        try:
            from src.identity.yui_core_profile import (
                build_yui_core_block, build_relationship_core_block,
                build_shared_life_block,
            )
            _yui = build_yui_core_block()
            if _yui and _yui.strip():
                system_parts.append(_yui.strip())
            if _rc_enabled:
                _rel = build_relationship_core_block()
                if _rel and _rel.strip():
                    system_parts.append(_rel.strip())
            # Phase 2 Shared-Life：长期共同生活模式常驻背景
            # （query-independent；数量 ≤3；空 store 安全降级）
            _shared_life = build_shared_life_block()
            if _shared_life and _shared_life.strip():
                system_parts.append(_shared_life.strip())
        except Exception:  # noqa: BLE001
            pass

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
            # Phase B1c.1: 记忆时间标签（开关 temporal.memory_time_labels，默认 off=旧输出逐字节兼容）。
            # 标签只来自 record["timestamp"]，禁止分析 content。
            _mem_label_fn = None
            try:
                from src.config import get as _ml_cfg_get
                if str(_ml_cfg_get("temporal.memory_time_labels", "off") or "off").strip().lower() == "on":
                    from src.temporal.temporal_context import format_memory_time_label
                    _mem_label_fn = format_memory_time_label
            except Exception:  # noqa: BLE001
                _mem_label_fn = None
            # v1.5.5 Memory Recall Fix: 不再隐式重排/截断——
            # 最终注入列表由 orchestrator 的 selection 层决定（semantic 优先 + recent 补足）。
            # 此处仅保留硬预算兜底（防未来调用方传入超长列表导致 prompt 膨胀）。
            _ordered_memories = chat_memories[:MEMORY_INJECTION_HARD_CAP]
            for m in _ordered_memories:
                if isinstance(m, dict):
                    role = "用户" if m.get("role") == "user" else "羽依"
                    content = m.get("content", "")[:150]
                    _time_label = _mem_label_fn(m.get("timestamp")) if _mem_label_fn is not None else ""
                    mem_parts.append(f"  {_time_label}{role}: {content}")
            if mem_parts:
                system_parts.append(f"【相关记忆】\n" + "\n".join(mem_parts))
        else:
            # M1-4: 检索为空时的防历史幻觉约束（认知约束，非人格改写）。
            # 若检索没有提供证据，不得凭模型先验补写具体历史细节。
            system_parts.append(
                "【相关记忆】\n"
                "  本次没有检索到相关的具体历史细节。你的过去是连续的，检索不到"
                "（第一次见面、某次对话、过去说过的话、发生过的事），"
                "请说明自己记不清具体是哪一次、具体说了什么；检索不到不等于这段经历不存在，不要编造具体历史。"
            )

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
        # B1b：temporal_context 非空时注入标准块；None 保持旧裸时间行（逐字节兼容）
        if temporal_context:
            system_parts.append(temporal_context)
        else:
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

        _record_injection_legacy(locals())  # v1.5-T3b: 生产主链台账挂点
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
        temporal_context: str = None,     # B1b：非空时替换"当前时间"裸行（None=旧行为）
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
                temporal_context=temporal_context,
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
        else:
            # M1-4: 检索为空时的防历史幻觉约束（与 original 模式一致）。
            system_parts.append(
                "【相关记忆】\n"
                "  本次没有检索到相关的具体历史细节。你的过去是连续的，检索不到"
                "（第一次见面、某次对话、过去说过的话、发生过的事），"
                "请说明自己记不清具体是哪一次、具体说了什么；检索不到不等于这段经历不存在，不要编造具体历史。"
            )

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
        # B1b：temporal_context 非空时注入标准块；None 保持旧裸时间行（逐字节兼容）
        if temporal_context:
            system_parts.append(temporal_context)
        else:
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
        _record_injection_legacy(locals())  # v1.5-T3b: 生产主链台账挂点
        return messages
