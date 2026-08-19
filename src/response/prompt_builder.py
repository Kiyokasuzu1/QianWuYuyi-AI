"""
Prompt 构建器（覆盖版）
职责：将人格文本、记忆、关系、行为倾向、表达约束、自我认知上下文等组装为 LLM 可用的 messages。
Phase 11.8 新增：agreement_context 参数，优先级最高。
Phase A.2 新增：experience_context 参数，注入 Historical Experience Context。
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.memory import MemoryFormatter
from src.utils.text import truncate
from src.response.principles import build_principles_block


class PromptBuilder:
    def __init__(self):
        self.memory_formatter = MemoryFormatter()

    def _format_chat_memories(self, chat_memories: list) -> str:
        if not chat_memories:
            return ""
        # Phase 2.5-B: 每条记忆必须带主体标注(清夏铃曾说/其他用户曾说/羽依自身/未知来源记录),
        # 禁止无主体的「用户: xxx」——防止 LLM 把所有 user 消息都当成清清说的。
        try:
            from src.memory.memory_scope import label_for_prompt
        except Exception:  # noqa: BLE001
            label_for_prompt = None
        lines = []
        for m in chat_memories[:5]:
            if not isinstance(m, dict):
                continue
            if label_for_prompt is not None:
                try:
                    label = label_for_prompt(m)
                except Exception:  # noqa: BLE001
                    label = "用户" if m.get("role") == "user" else "羽依"
            else:
                label = "用户" if m.get("role") == "user" else "羽依"
            content = truncate(m.get("content", ""), 120)
            lines.append(f"  {label}: {content}")
        return "【最近相关聊天】\n" + "\n".join(lines)

    def _format_personality(self, personality_context: dict) -> str:
        if not personality_context:
            return ""
        personality_text = personality_context.get("personality_text", "")
        # Phase 4.2.3: SelfModel 文本(由 SelfModelContextProvider 注入)
        # 不修改 ResponseEngine.generate(),通过 personality_context["self_model_text"] 透传
        self_model_text = personality_context.get("self_model_text", "")
        if personality_text and self_model_text:
            return f"{personality_text}\n{self_model_text}"
        if personality_text:
            return personality_text
        if self_model_text:
            return self_model_text
        style = personality_context.get("style_instruction", "")
        if style:
            return f"【表达风格】\n{style}"
        return ""

    def _format_behavior(self, resolved_behavior: Optional[dict]) -> str:
        if not resolved_behavior:
            return ""
        lines = [
            "【当前表达参考】",
            "以下倾向来自人格状态推理，请自然体现，不要机械说明："
        ]
        lines.append(f"- 表达风格：{resolved_behavior.get('chosen_expression', '自然')}")
        lines.append(f"- 直接程度：{resolved_behavior.get('chosen_directness', '适中')}")
        if resolved_behavior.get("conflict_detected"):
            lines.append(f"- 内部权衡：{resolved_behavior.get('resolution_reason', '')}")
        notes = resolved_behavior.get("sensitivity_notes", [])
        if notes:
            lines.append("- 敏感度提示：")
            for n in notes:
                lines.append(f"  - {n}")
        return "\n".join(lines)

    def _format_experience_context(self, experience_context) -> str:
        """
        Phase A.2 + P4.4-C: 格式化 experience_context 为 Historical Experience Context 文本。

        约束：
        - experience_context 仅作为历史经验背景，不会改变人格。
        - 输入为 None / 空列表 → 返回空字符串（不增加空 Prompt）。
        - P4.4-C 起与 legacy engine.py 共用 build_experience_context
          （importance top-10 + 原始记忆元数据清洗 + evidence 80 字符截断）。
        """
        if not experience_context:
            return ""
        from src.personality.experience_context import build_experience_context
        return build_experience_context(experience_context)

    def _format_user_meta_block(
        self,
        user_meta: Optional[Dict],
        communication_profile=None,
    ) -> str:
        """Phase 4.1.2-B: 组装 user_meta 块。

        Phase 4.4-D1：唯一 canonical 实现已移至 src/response/prompt_sections.py
        （build_user_meta_block），此处保留原方法名做兼容委托，调用方不变。
        """
        from src.response.prompt_sections import build_user_meta_block
        return build_user_meta_block(user_meta, communication_profile)

    def build_messages(
        self,
        user_message: str,
        history: Optional[List[Dict]] = None,
        chat_memories: Optional[List] = None,
        life_events: Optional[List] = None,
        personality_context: Optional[Dict] = None,
        resolved_behavior: Optional[Dict] = None,
        expression_constraint_text: Optional[str] = None,
        self_model_context: Optional[str] = None,
        emotion_context: Optional[str] = None,
        relationship_context: Optional[str] = None,
        agreement_context: Optional[str] = None,  # Phase 11.8 新增
        experience_context: Optional[List[Dict]] = None,  # Phase A.2 新增
        identity_context: Optional[str] = None,  # Phase 2 新增（Identity State 注入）
        user_meta: Optional[Dict] = None,  # Phase 4.0.4-Pre 新增：对方是谁/如何称呼/关系等级
        communication_profile: Optional[Any] = None,  # Phase 4.1.2-B 新增：CommunicationStyle 表达倾向
        context_prompt_blocks: Optional[List[str]] = None,  # P4.4-D5 新增：已生成提示块，每块独立成节
    ) -> List[Dict]:
        # Phase 4.0.2-P1：核心身份不再硬编码，统一由 IDENTITY_CORE 驱动（章程阶段一）
        # Phase 4.4-D2：canonical 组装移至 src/response/prompt_sections.py
        # （build_identity_block，include_full=False —— Runtime 链维持精简身份，
        # 只含 name + essence，不扩充为 Orchestrator 全量身份）
        from src.response.prompt_sections import build_identity_block

        core_identity_text = build_identity_block(include_full=False)

        # Phase 2: Identity State（放在 core_identity 之后、其他 Prompt 之前，
        # 让 LLM 先建立「这是我是谁」而不是「这是我要遵守的规则」。
        # identity_context 只在有实际内容时才插入；空字符串/None 不塞空行占位
        identity_text = ""
        if isinstance(identity_context, str) and identity_context.strip():
            identity_text = identity_context.strip()

        # Phase 4.1.2-B: communication_profile — 表达倾向（低约束描述）。
        # 优先级：communication_profile > user_meta.calling_rule/intimacy_rule。
        # communication_profile 可用时，user_meta 中的 calling_rule/intimacy_rule 降级为
        # 不注入；只保留 preferred_name/display_name/relationship_tier 作为身份事实。
        # communication_profile 不可用时，回退到 user_meta 的 calling_rule/intimacy_rule。
        user_meta_text = self._format_user_meta_block(user_meta, communication_profile)

        personality_text = self._format_personality(personality_context)
        behavior_text = self._format_behavior(resolved_behavior)

        life_events_text = ""
        if life_events:
            life_events_text = self.memory_formatter.format_for_prompt(life_events)
        chat_memories_text = self._format_chat_memories(chat_memories)

        self_model_text = self_model_context or ""
        relationship_text = relationship_context or ""
        emotion_text = emotion_context or ""
        agreement_text = agreement_context or ""
        experience_text = self._format_experience_context(experience_context)

        # Phase 4.4-D3：section 顺序与块头收敛。
        # - user_meta 块提到 identity_text 之后，保证身份声明之后尽早出现
        #   关系/表达约束（与 Orchestrator 链一致；块文案仍由
        #   build_user_meta_block 唯一生成）；
        # - relationship_text 通过 wrap_section 统一加【用户关系】块头
        #   （C6C render_relationship_facts 契约不含块头，由 Prompt 层添加）。
        from src.response.prompt_sections import wrap_section

        sections = [core_identity_text.strip()]

        # Phase 2.5-B: 羽依核心身份事实常驻块——任何会话(含陌生人会话)都可见,
        # 保证其他用户窗口不会让羽依忘记「自己是谁、清清是谁、核心约束」。
        try:
            from src.identity.yui_core_profile import build_yui_core_block
            yui_core_text = build_yui_core_block()
            if yui_core_text and yui_core_text.strip():
                sections.append(yui_core_text.strip())
        except Exception:  # noqa: BLE001
            pass

        relationship_text = wrap_section("【用户关系】", relationship_text)
        optional_sections = (
            identity_text,
            user_meta_text,
            agreement_text,
            personality_text,
            behavior_text,
            self_model_text,
            experience_text,
            relationship_text,
            emotion_text,
            expression_constraint_text or "",
            life_events_text,
            chat_memories_text,
        )
        for section in optional_sections:
            if section and section.strip():
                sections.append(section.strip())

        # P4.4-D5：额外上下文块（Runtime 正式链已生成的 identity/strategy/behavior
        # 等文本），每块作为独立 section 注入，位置与 legacy engine.py 一致：
        # 常规 section 之后、【核心原则】之前。文本原样使用，不加工。
        if context_prompt_blocks:
            for block in context_prompt_blocks:
                if isinstance(block, str) and block.strip():
                    sections.append(block.strip())

        # 原则块：与 legacy engine.py 共用统一文本（src/response/principles.py）
        sections.append(build_principles_block())
        sections.append(f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")

        system_prompt = "\n\n".join(sections)
        messages = [{"role": "system", "content": system_prompt.strip()}]
        if history:
            for item in history[-20:]:
                messages.append(item)
        messages.append({"role": "user", "content": user_message})
        return messages