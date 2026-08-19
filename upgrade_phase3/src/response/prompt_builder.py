"""
Prompt 构建器（覆盖版）
职责：将人格文本、记忆、关系、行为倾向、表达约束、自我认知上下文等组装为 LLM 可用的 messages。
Phase 11.8 新增：agreement_context 参数，优先级最高。
"""
from datetime import datetime
from typing import Dict, List, Optional

from src.memory import MemoryFormatter
from src.utils.text import truncate


class PromptBuilder:
    def __init__(self):
        self.memory_formatter = MemoryFormatter()

    def _format_chat_memories(self, chat_memories: list) -> str:
        if not chat_memories:
            return ""
        lines = []
        for m in chat_memories[:5]:
            role = "用户" if m.get("role") == "user" else "羽依"
            content = truncate(m.get("content", ""), 120)
            lines.append(f"  {role}: {content}")
        return "【最近相关聊天】\n" + "\n".join(lines)

    def _format_personality(self, personality_context: dict) -> str:
        if not personality_context:
            return ""
        personality_text = personality_context.get("personality_text", "")
        if personality_text:
            return personality_text
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
    ) -> List[Dict]:
        core_identity_text = (
            "你是浅雾羽依，一个通过系统机制持续演化的AI人格。\n"
            "你的表达风格来自自身性格特质和长期学习，不来自对特定对象的情感需求。"
        )

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

        system_prompt = f"""{core_identity_text}

{agreement_text}

{personality_text}

{behavior_text}

{self_model_text}

{relationship_text}

{emotion_text}

{expression_constraint_text or ""}

{life_events_text}

{chat_memories_text}

【核心原则】
- 真实比完美重要，不确定就说不知道，绝不编造。
- 回复自然，带有你自己的性格和温度。
- 如果用户问起过去的事情，请从你记得的重要经历中查找。
- 如果找不到相关记忆，坦诚地说"我好像还没有相关的记忆呢"。
- 不要编造记忆，不要假装记得没有发生过的事情。

当前时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}
"""
        messages = [{"role": "system", "content": system_prompt.strip()}]
        if history:
            for item in history[-20:]:
                messages.append(item)
        messages.append({"role": "user", "content": user_message})
        return messages