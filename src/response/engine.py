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
        context_prompt_blocks: Optional[List[Any]] = None,  # P4.4-D5：透传至 PromptBuilder（此前为死参数）
        user_meta: Optional[Dict[str, Any]] = None,  # Phase 4.0.4-Pre：对方是谁/怎么称呼
        communication_profile: Optional[Any] = None,  # Phase 4.1.2-B：CommunicationStyle 表达倾向
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
            context_prompt_blocks=context_prompt_blocks,
            user_meta=user_meta,
            communication_profile=communication_profile,
        )
        return self.llm.generate(messages)