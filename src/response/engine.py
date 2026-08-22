from typing import Any, Dict, List, Optional

from src.response.llm import LLMClient
from src.response.prompt_builder import PromptBuilder


class ResponseEngine:
    def __init__(self):
        # 部署加固: LLM 客户端懒加载——缺少 API Key / openai SDK 时不再阻断
        # 服务启动（Orchestrator 构造链可完成），首次 generate 时才抛出与旧版
        # 一致的清晰 ValueError。Key 存在时行为与旧版完全相同（init 即构造）。
        self.prompt_builder = PromptBuilder()
        self._llm = None
        self._llm_init_error = None
        try:
            self._llm = LLMClient()
        except Exception as exc:  # noqa: BLE001
            self._llm_init_error = exc

    @property
    def llm(self):
        if self._llm is None:
            if self._llm_init_error is not None:
                raise self._llm_init_error
            self._llm = LLMClient()
        return self._llm

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
        goal_context: Optional[str] = None,  # v1.3 Phase 2：GoalContext（只读关注方向，默认 None）
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
            goal_context=goal_context,
        )
        return self.llm.generate(messages)