# -*- coding: utf-8 -*-
"""
src/runtime/adapters/impl/response_adapter_impl.py

Phase 3.8.4: ResponseAdapterImpl —— ResponseEngine 桥接

职责:
- 在 Runtime RESPONSE_GENERATION 阶段,接受 ResponseAdapter 产出的 ResponseRequest,
  调用 ResponseEngine.generate(...) 生成 LLM 回复,
  并把回复回填到 ctx,供 Guard Chain 审计。

设计:
- 不持有 openai 客户端
- 不修改 ResponseEngine / LLMClient / PromptBuilder
- 失败隔离:任何异常都会被 Runtime 捕获,不中断整个 process()

注意:
- ResponseEngine 可能在没有 DEEPSEEK_API_KEY 的环境抛错,
  Impl 应捕获并返回 fallback 文本。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.adapters.base import AdapterBase
from src.runtime.adapters.response_adapter import (
    ResponseAdapter,
    ResponseRequest,
    RESPONSE_ADAPTER_SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)


class ResponseAdapterImpl(ResponseAdapter):
    """ResponseEngine 桥接实现（Phase 3.8.4 / v1.0）。

    使用方式:
        impl = ResponseAdapterImpl()
        impl.attach()
        req = impl.build_request(ctx, prc, event)
        reply = impl.generate(req)
    """

    name: str = "response_adapter_impl"
    schema_version: str = RESPONSE_ADAPTER_SCHEMA_VERSION

    def __init__(
        self,
        response_engine: Optional[Any] = None,
        fallback_reply: str = "（回复生成暂不可用）",
        self_model_context_provider: Optional[Any] = None,
        self_reflection_context_provider: Optional[Any] = None,
        identity_context_provider: Optional[Any] = None,
        personality_binding_provider: Optional[Any] = None,
    ) -> None:
        super().__init__(
            self_model_context_provider=self_model_context_provider,
            self_reflection_context_provider=self_reflection_context_provider,
            identity_context_provider=identity_context_provider,
            personality_binding_provider=personality_binding_provider,
        )
        self._response_engine = response_engine
        self._fallback_reply = fallback_reply
        self._last_reply: Optional[str] = None
        self._generate_count: int = 0

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """注入 ResponseEngine（默认延迟加载,失败不影响 Runtime 启动）。"""
        if self._response_engine is None:
            try:
                # 仅在 attach 时尝试 import,避免循环依赖
                from src.response.engine import ResponseEngine
                self._response_engine = ResponseEngine()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ResponseAdapterImpl.attach() 加载 ResponseEngine 失败: %s", exc
                )
                self._response_engine = None
        self._mark_attached()
        self._cache_health({
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "engine_loaded": self._response_engine is not None,
        })

    def detach(self) -> None:
        self._response_engine = None
        self._last_reply = None
        self._mark_detached()

    def health_check(self) -> Dict[str, Any]:
        result = {
            "healthy": self.is_attached,
            "name": self.name,
            "schema_version": self.schema_version,
            "engine_loaded": self._response_engine is not None,
        }
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # 业务接口
    # --------------------------------------------------------
    def generate(self, request: ResponseRequest) -> str:
        """调用 ResponseEngine.generate(...) 返回回复。

        Phase 1 修复：
            - 所有 14 个 PromptBuilder 参数都从 ResponseRequest 透传；
            - 不再写死 history=[] / resolved_behavior=None /
              expression_constraint_text=None；
            - identity_context 预留 Phase 2 注入（优先用
              request.personality_context["identity_context_text"]，
              与 Phase 4.4 Provider 注入一致）。

        失败时返回 self._fallback_reply,不抛异常（保证 Runtime 阶段不中断）。
        """
        if not self.is_attached:
            return self._fallback_reply

        if self._response_engine is None:
            return self._fallback_reply

        try:
            # 翻译 ResponseRequest → ResponseEngine.generate 参数
            personality_context = request.personality_context or {}
            # 兼容 personality_text 形式（Phase 3.8 已有逻辑）
            if "personality_text" not in personality_context:
                cs = personality_context.get("communication_style") or {}
                if cs:
                    style = " ".join(f"{k}={v}" for k, v in cs.items())
                    personality_context["personality_text"] = (
                        f"【表达风格】{style}"
                    )

            # Phase 2 预留：优先用 Provider 注入好的 identity_context_text
            # 作为 identity_context 注入 PromptBuilder。
            identity_context_text: Optional[str] = None
            if isinstance(personality_context, dict):
                for k in ("identity_context_text", "identity_context"):
                    v = personality_context.get(k)
                    if isinstance(v, str) and v.strip():
                        identity_context_text = v.strip()
                        break

            # v1.3 Phase 2: goal_context 预留——优先用 Provider 注入好的
            # goal_context_text(默认未注入 → None, 行为与 v1.2 完全一致;
            # 聊天层开关 goal_context_enabled 由上游 Provider/开关控制)。
            goal_context_text: Optional[str] = None
            if isinstance(personality_context, dict):
                _gv = personality_context.get("goal_context_text")
                if isinstance(_gv, str) and _gv.strip():
                    goal_context_text = _gv.strip()

            # Phase 4.0.4-Pre：user_meta —— 对方是谁/怎么称呼/关系等级
            # 单处调用 UserResolver.build_user_meta，保证 RuntimeCore 主路径
            # 的昵称和语气稳定。失败（None/异常）时不传，等价于旧行为。
            user_meta: Optional[Dict[str, Any]] = None
            try:
                from src.identity.user_resolver import UserResolver
                user_meta = UserResolver.build_user_meta(
                    user_id=request.user_id,
                    relationship_profile=request.relationship_profile,
                )
            except Exception as exc_meta:  # noqa: BLE001
                logger.debug(
                    "ResponseAdapterImpl.generate 组装 user_meta 失败（已隔离）: %s",
                    exc_meta,
                )
                user_meta = None

            # Phase 4.1.2-B：CommunicationStyle — 从 Relationship 状态推导表达倾向
            # 新链：CommunicationStyleResolver → CommunicationRenderer → Prompt
            # 失败（异常/None）不影响主流程，回退到 user_meta 的 calling_rule/intimacy_rule
            #
            # P4.2-IMPL-C7: snapshot 为 Runtime 正式路径（与 legacy 分支互斥——
            # snapshot 存在时禁止同时让 resolver 读取 legacy relationship_state）。
            communication_profile = None
            try:
                from src.communication.style_resolver import CommunicationStyleResolver
                resolver = CommunicationStyleResolver()
                relationship_snapshot = getattr(request, "relationship_snapshot", None)
                if relationship_snapshot is not None:
                    communication_profile = resolver.resolve(
                        None,
                        user_identity=user_meta,
                        snapshot=relationship_snapshot,
                    )
                else:
                    # Legacy fallback：无 snapshot 时走 relationship_state 推导
                    communication_profile = resolver.resolve(
                        request.relationship_state,
                        user_identity=user_meta,
                    )
            except Exception as exc_comm:  # noqa: BLE001
                logger.debug(
                    "ResponseAdapterImpl.generate 生成 communication_profile 失败（已隔离）: %s",
                    exc_comm,
                )
                communication_profile = None

            reply = self._response_engine.generate(
                user_message=request.user_input or "",
                history=list(request.history or []),
                chat_memories=list(request.chat_memories or []),
                life_events=list(request.life_events or []),
                personality_context=personality_context,
                resolved_behavior=(
                    dict(request.resolved_behavior)
                    if isinstance(request.resolved_behavior, dict)
                    else None
                ),
                expression_constraint_text=(
                    request.expression_constraint_text
                    if isinstance(request.expression_constraint_text, str)
                    else None
                ),
                self_model_context=(
                    request.self_model_context
                    if isinstance(request.self_model_context, str)
                    else None
                ),
                emotion_context=(
                    request.emotion_context
                    if isinstance(request.emotion_context, str)
                    else None
                ),
                relationship_context=(
                    request.relationship_context
                    if isinstance(request.relationship_context, str)
                    else None
                ),
                agreement_context=(
                    request.agreement_context
                    if isinstance(request.agreement_context, str)
                    else None
                ),
                experience_context=list(request.experience_context or []),
                identity_context=identity_context_text,
                # P4.4-D5: 已生成提示块原样透传（None/空列表均兼容）
                context_prompt_blocks=request.context_prompt_blocks,
                user_meta=user_meta,
                communication_profile=communication_profile,
                # v1.3 Phase 2: GoalContext（未注入 → None, 不进入 Prompt）
                goal_context=goal_context_text,
            )
            self._last_reply = reply
            self._generate_count += 1
            return reply
        except Exception as exc:  # noqa: BLE001
            logger.warning("ResponseAdapterImpl.generate 失败: %s", exc)
            self._last_reply = self._fallback_reply
            return self._fallback_reply

    @property
    def last_reply(self) -> Optional[str]:
        return self._last_reply

    @property
    def generate_count(self) -> int:
        return self._generate_count


__all__ = ["ResponseAdapterImpl"]
