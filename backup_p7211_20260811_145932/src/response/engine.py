from typing import Any, Dict, List, Optional

from src.response.llm import LLMClient
from src.response.prompt_builder import PromptBuilder


class ResponseEngine:
    def __init__(self):
        self.llm = LLMClient()
        self.prompt_builder = PromptBuilder()

    def generate(
        self,
        user_message: str,
        history: list = None,
        chat_memories: list = None,
        life_events: list = None,
        personality_context: dict = None,
        resolved_behavior: dict = None,
        expression_constraint_text: str = None,  # Phase 7.4 新增
        self_model_context: str = None,
        emotion_context: str = None,
        relationship_context: str = None,
        agreement_context: str = None,  # Phase 11.8 新增
        experience_context: list = None,  # Phase A.2 新增
        identity_context: str = None,  # Phase 2 新增（IdentityContext）
        context_prompt_blocks: list = None,  # Phase 7.x：RuntimeCore 注入的角色级块（[{"role":"system","content":...}]）
        **_kwargs: Any,  # 静默吞掉未来新增字段，不阻塞回复
    ) -> str:
        messages = self.prompt_builder.build_messages(
            user_message=user_message,
            history=history,
            chat_memories=chat_memories,
            life_events=life_events,
            personality_context=personality_context,
            resolved_behavior=resolved_behavior,
            expression_constraint_text=expression_constraint_text,
            self_model_context=self_model_context,
            emotion_context=emotion_context,
            relationship_context=relationship_context,
            agreement_context=agreement_context,
            experience_context=experience_context,
            identity_context=identity_context,
        )
        # Phase 7.x：context_prompt_blocks 格式与 messages 一致（List[{"role":..., "content":...}]），
        # 把合法的块插到最前面，作为 system 级锚点优先于普通 system prompt。
        if isinstance(context_prompt_blocks, list):
            prepend: List[Dict[str, str]] = []
            for blk in context_prompt_blocks:
                if (
                    isinstance(blk, dict)
                    and isinstance(blk.get("role"), str)
                    and isinstance(blk.get("content"), str)
                    and blk["content"].strip()
                ):
                    prepend.append({
                        "role": blk["role"],
                        "content": blk["content"],
                    })
            if prepend:
                messages = prepend + messages
        return self.llm.generate(messages)