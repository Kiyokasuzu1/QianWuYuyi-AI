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
    ) -> str:
        messages = self.prompt_builder.build_messages(
            user_message=user_message,
            history=history,
            chat_memories=chat_memories,
            life_events=life_events,
            personality_context=personality_context,
            resolved_behavior=resolved_behavior,
            expression_constraint_text=expression_constraint_text,
        )
        return self.llm.generate(messages)