# -*- coding: utf-8 -*-
"""
src/runtime/adapters/response_adapter.py

Phase 3.8.4: ResponseAdapter —— Runtime ↔ ResponseEngine 桥接

职责：
- 消费 RuntimeContext + PersonalityRuntimeContext
- 生成 ResponseRequest（供 ResponseEngine.generate 使用）
- 不直接调用 LLM；不持有 openai 客户端。

约束：
- 不 import src.response.llm / src.engine（LLM 实现）
- 不 import src.personality / src.memory / src.emotion / src.growth 业务实现
- 依赖：仅 stdlib + 同包 base / context / personality_context

生命周期接口：
- build_request(ctx, prc, event) -> ResponseRequest
    把 RuntimeContext / PersonalityRuntimeContext 翻译为下游 ResponseEngine 可消费的 dict

ResponseRequest 字段（v1.0）：
- user_input:         str                 # 用户输入
- memory_context:     Optional[Any]       # 来自 ctx.memory_context
- emotion_state:      Optional[Any]       # 来自 ctx.emotion_state
- personality_context:Optional[Dict]      # 来自 PersonalityRuntimeContext
- relationship_state: Optional[Any]       # 来自 PersonalityRuntimeContext.relationship_state
- facts_boundary:     List[Dict]          # Phase 3.8.4 引入:本次回复允许使用的 fact 列表
- draft_facts:        List[Fact]          # Phase 3.8.4 引入:已产生的 Fact,供 PerceptionGuard 审计
- response_engine:    str                 # 目标 engine 标识
- schema_version:     str                 # "1.0"
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from src.runtime.adapters.base import AdapterBase
from src.runtime.context import RuntimeContext
from src.runtime.personality_context import PersonalityRuntimeContext
from src.runtime.perception.fact import Fact


logger = logging.getLogger(__name__)


RESPONSE_ADAPTER_SCHEMA_VERSION = "1.0"


@dataclass
class ResponseRequest:
    """由 ResponseAdapter.build_request() 生成的请求体（v1.1 — Phase 1 扩展）。

    该对象不直接调用 LLM；它由 ResponseEngine（外部）消费。

    v1.0 -> v1.1 扩展说明：
    - 新增 9 个「生成表达链」字段，修复 ResponseAdapterImpl 之前
      写死 history=[] / resolved_behavior=None 导致的「记忆/语气/上下文
      未进入 LLM」断路问题。
    - 所有新字段均为 Optional / 空默认值，完全向后兼容。
    """
    user_input: str = ""
    memory_context: Optional[Any] = None
    emotion_state: Optional[Any] = None
    personality_context: Optional[Dict[str, Any]] = None
    relationship_state: Optional[Any] = None
    facts_boundary: List[Dict[str, Any]] = field(default_factory=list)
    draft_facts: List[Fact] = field(default_factory=list)
    response_engine: str = "default"
    schema_version: str = RESPONSE_ADAPTER_SCHEMA_VERSION
    # ----------------------------------------------------------
    # Phase 1 新增：生成表达链 9 字段（全可选 / 空默认）
    # ----------------------------------------------------------
    history: List[Dict[str, Any]] = field(default_factory=list)
    chat_memories: List[Dict[str, Any]] = field(default_factory=list)
    life_events: List[Any] = field(default_factory=list)
    resolved_behavior: Optional[Dict[str, Any]] = None
    expression_constraint_text: Optional[str] = None
    self_model_context: Optional[str] = None
    emotion_context: Optional[str] = None
    relationship_context: Optional[str] = None
    experience_context: List[Dict[str, Any]] = field(default_factory=list)
    agreement_context: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Fact 不直接 asdict(转 dict)
        d["draft_facts"] = [f.to_dict() if isinstance(f, Fact) else f for f in self.draft_facts]
        return d


class ResponseAdapter(AdapterBase):
    """Runtime ↔ Response 桥接 Adapter（v1.0）。

    使用方式:
        adapter = ResponseAdapter()
        adapter.attach()
        req = adapter.build_request(ctx, prc, event)
        # 把 req 交给 ResponseEngine.generate(...)

    注：build_request() 不访问 LLM，仅做数据翻译。

    Phase 4.2.3 扩展:
    - 支持 SelfModelContextProvider(可选注入),从 ctx._self_model_snapshot
      读取 SelfModel 数据,转换为 personality_context 中的 self_model_data / self_model_text。
    - 默认 None 时完全向后兼容(无 SelfModel 时 personality_context 不含 self_model_* 字段)。

    Phase 4.3 扩展:
    - 支持 SelfReflectionContextProvider(可选注入),从 ctx._self_reflection_record
      读取最新反思记录,转换为 personality_context 中的 self_reflection_data / self_reflection_text。
    - 默认 None 时完全向后兼容(无 Reflection 时 personality_context 不含 self_reflection_* 字段)。

    Phase 4.4 扩展:
    - 支持 IdentityContextProvider(可选注入),从 ctx._identity_context
      读取运行时身份上下文,转换为 personality_context 中的 identity_context_data / identity_context_text。
    - 默认 None 时完全向后兼容(无 IdentityContext 时 personality_context 不含 identity_context_* 字段)。

    Phase 4.5 扩展:
    - 支持 PersonalityBindingProvider(可选注入),从 ctx._personality_runtime_context
      读取 ComposedPersonalityContext,转换为 personality_context 中的
      personality_runtime_data / personality_runtime_text(并保留 identity_context /
      reflection_context / behavior_signature / consistency_rules 四个关键子字段,
      供下游 LLM 消费)。
    - 默认 None 时完全向后兼容(无 PersonalityRuntimeBinding 时 personality_context 不含
      personality_runtime_* 字段)。
    """

    name: str = "response_adapter"
    schema_version: str = RESPONSE_ADAPTER_SCHEMA_VERSION

    def __init__(
        self,
        self_model_context_provider: Optional[Any] = None,
        self_reflection_context_provider: Optional[Any] = None,
        identity_context_provider: Optional[Any] = None,
        personality_binding_provider: Optional[Any] = None,
    ) -> None:
        super().__init__()
        self._last_request: Optional[ResponseRequest] = None
        self._build_count: int = 0
        # Phase 4.2.3: 可选 SelfModelContextProvider 注入
        self._self_model_provider: Optional[Any] = self_model_context_provider
        # Phase 4.3: 可选 SelfReflectionContextProvider 注入
        self._self_reflection_provider: Optional[Any] = (
            self_reflection_context_provider
        )
        # Phase 4.4: 可选 IdentityContextProvider 注入
        self._identity_provider: Optional[Any] = identity_context_provider
        # Phase 4.5: 可选 PersonalityBindingProvider 注入
        self._personality_binding_provider: Optional[Any] = (
            personality_binding_provider
        )

    def set_self_model_context_provider(
        self, provider: Optional[Any],
    ) -> None:
        """运行时注入 / 替换 SelfModelContextProvider(可选)。"""
        self._self_model_provider = provider

    def set_self_reflection_context_provider(
        self, provider: Optional[Any],
    ) -> None:
        """运行时注入 / 替换 SelfReflectionContextProvider(可选,Phase 4.3)。"""
        self._self_reflection_provider = provider

    def set_identity_context_provider(
        self, provider: Optional[Any],
    ) -> None:
        """运行时注入 / 替换 IdentityContextProvider(可选,Phase 4.4)。"""
        self._identity_provider = provider

    def set_personality_binding_provider(
        self, provider: Optional[Any],
    ) -> None:
        """运行时注入 / 替换 PersonalityBindingProvider(可选,Phase 4.5)。"""
        self._personality_binding_provider = provider

    @property
    def self_model_context_provider(self) -> Optional[Any]:
        return self._self_model_provider

    @property
    def self_reflection_context_provider(self) -> Optional[Any]:
        return self._self_reflection_provider

    @property
    def identity_context_provider(self) -> Optional[Any]:
        return self._identity_provider

    @property
    def personality_binding_provider(self) -> Optional[Any]:
        return self._personality_binding_provider

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        self._mark_attached()
        self._cache_health({
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
        })

    def detach(self) -> None:
        self._last_request = None
        self._mark_detached()

    def health_check(self) -> Dict[str, Any]:
        result = {
            "healthy": self.is_attached,
            "name": self.name,
            "schema_version": self.schema_version,
        }
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # 业务接口: build_request
    # --------------------------------------------------------
    def build_request(
        self,
        ctx: RuntimeContext,
        prc: Optional[PersonalityRuntimeContext] = None,
        event: Optional[Any] = None,
        facts: Optional[List[Fact]] = None,
        response_engine: str = "default",
    ) -> ResponseRequest:
        """把 RuntimeContext / PersonalityRuntimeContext 翻译为 ResponseRequest。

        参数:
        - ctx:   RuntimeContext（必填,至少含 user_input）
        - prc:   PersonalityRuntimeContext（可选;Runtime 在 PERSONALITY_CONTEXT_BUILD 阶段产出）
        - event: 原始 Event（可选;用于 event_id 关联）
        - facts: 本次回复依赖的 Fact 列表（可选;供 PerceptionGuard 审计）
        - response_engine: 目标 engine 标识
        """
        if not self.is_attached:
            # 即使未 attach 也允许构造,只是标记 unhealthy
            pass

        # 抽取 user_input
        user_input = (ctx.user_input if ctx is not None else "") or ""
        if not user_input and event is not None:
            payload = getattr(event, "payload", None)
            if isinstance(payload, dict):
                user_input = (
                    payload.get("text")
                    or payload.get("content")
                    or ""
                )

        # 聚合 personality_context
        personality_context: Optional[Dict[str, Any]] = None
        relationship_state: Optional[Any] = None
        communication_style: Dict[str, Any] = {}
        behavior_constraints: List[str] = []
        if prc is not None:
            snapshot = prc.personality_snapshot
            if isinstance(snapshot, dict):
                personality_context = dict(snapshot)
            communication_style = dict(prc.communication_style or {})
            behavior_constraints = list(prc.behavior_constraints or [])
            relationship_state = prc.relationship_state

        # 注入 communication_style / behavior_constraints 进 personality_context
        if personality_context is None:
            personality_context = {}
        if communication_style:
            personality_context.setdefault("communication_style", communication_style)
        if behavior_constraints:
            personality_context.setdefault("behavior_constraints", behavior_constraints)

        # Phase 4.2.3: 注入 SelfModel 上下文(可选,向后兼容)
        # 不修改 RuntimeContext schema,从 ctx 私有属性 _self_model_snapshot 读取
        self_model_snapshot = None
        if ctx is not None:
            self_model_snapshot = getattr(ctx, "_self_model_snapshot", None)
        if self_model_snapshot is not None and self._self_model_provider is not None:
            try:
                sm_data = self._self_model_provider.provide(self_model_snapshot)
                sm_text = self._self_model_provider.format_context_for_prompt(sm_data)
                if sm_data and sm_data.get("has_snapshot"):
                    personality_context["self_model_data"] = sm_data
                if sm_text:
                    personality_context["self_model_text"] = sm_text
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ResponseAdapter.build_request 注入 SelfModel 失败: %s", exc
                )

        # Phase 4.3: 注入 SelfReflection 上下文(可选,向后兼容)
        # 从 ctx._self_reflection_record 读取最新反思记录
        # 注意:本注入只关心"当前 ctx 的最新 reflection",
        # 如需历史注入(由 provider.format_for_prompt + store)可在外部调用。
        self_reflection_record = None
        if ctx is not None:
            self_reflection_record = getattr(
                ctx, "_self_reflection_record", None
            )
        if (
            self_reflection_record is not None
            and self._self_reflection_provider is not None
        ):
            try:
                sref_data = self._self_reflection_provider.provide(
                    [self_reflection_record],
                )
                sref_text = self._self_reflection_provider.format_context_for_prompt(
                    sref_data
                )
                if sref_data and sref_data.get("has_reflection"):
                    personality_context["self_reflection_data"] = sref_data
                if sref_text:
                    personality_context["self_reflection_text"] = sref_text
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ResponseAdapter.build_request 注入 SelfReflection 失败: %s",
                    exc,
                )

        # Phase 4.4: 注入 IdentityContext(可选,向后兼容)
        # 从 ctx._identity_context 读取运行时身份上下文(由 SelfIdentityRuntime 生成)
        identity_context_obj = None
        if ctx is not None:
            identity_context_obj = getattr(ctx, "_identity_context", None)
        if (
            identity_context_obj is not None
            and self._identity_provider is not None
        ):
            try:
                id_data = self._identity_provider.provide(identity_context_obj)
                id_text = self._identity_provider.format_context_for_prompt(
                    id_data if isinstance(id_data, dict)
                    else identity_context_obj,
                )
                if id_data and isinstance(id_data, dict):
                    personality_context["identity_context_data"] = id_data
                if id_text:
                    personality_context["identity_context_text"] = id_text
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ResponseAdapter.build_request 注入 IdentityContext 失败: %s",
                    exc,
                )

        # Phase 4.5: 注入 PersonalityRuntimeBinding 产物(可选,向后兼容)
        # 从 ctx._personality_runtime_context 读取 ComposedPersonalityContext,
        # 转换为 personality_context 中的:
        #   personality_runtime_data   # 完整 dict(含 identity/reflection/behavior/growth/context_rules)
        #   personality_runtime_text   # Prompt 格式化文本
        #   identity_context / reflection_context / behavior_signature / consistency_rules
        #     四个关键子字段(扁平展开,供下游 LLM 消费)
        personality_runtime_obj = None
        if ctx is not None:
            personality_runtime_obj = getattr(
                ctx, "_personality_runtime_context", None
            )
        if (
            personality_runtime_obj is not None
            and self._personality_binding_provider is not None
        ):
            try:
                pr_data = self._personality_binding_provider.provide(
                    personality_runtime_obj,
                )
                pr_text = self._personality_binding_provider.format_context_for_prompt(
                    pr_data if isinstance(pr_data, dict)
                    else personality_runtime_obj,
                )
                if pr_data and isinstance(pr_data, dict):
                    personality_context["personality_runtime_data"] = pr_data
                    # 关键子字段扁平展开(供下游 LLM 直接消费)
                    # Phase 4.5: 始终注入四个关键子字段(允许空 dict)
                    if "identity_context" in pr_data:
                        personality_context.setdefault(
                            "identity_context", pr_data["identity_context"],
                        )
                    if "reflection_context" in pr_data:
                        personality_context.setdefault(
                            "reflection_context",
                            pr_data["reflection_context"],
                        )
                    # behavior_signature 优先取 behavior_context.scenarios
                    if "behavior_context" in pr_data:
                        behavior_ctx = pr_data.get("behavior_context") or {}
                        if isinstance(behavior_ctx, dict):
                            personality_context.setdefault(
                                "behavior_signature", behavior_ctx,
                            )
                    if "consistency_rules" in pr_data:
                        personality_context.setdefault(
                            "consistency_rules",
                            pr_data["consistency_rules"],
                        )
                if pr_text:
                    personality_context["personality_runtime_text"] = pr_text
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ResponseAdapter.build_request 注入 "
                    "PersonalityRuntimeBinding 失败: %s",
                    exc,
                )

        # facts_boundary: 允许下游使用的 fact 列表
        facts_list = list(facts or [])
        facts_boundary = [
            f.to_dict() if isinstance(f, Fact) else f for f in facts_list
        ]

        # ================================================================
        # Phase 1 新增：填充「生成表达链」9 个字段（不强制，有就填）
        # 规则：
        #   - 不修改 RuntimeContext schema v1.0；只从 ctx 私有属性 /
        #     prc / personality_context 中已经存在的数据读取。
        #   - 每个字段都有合理的空默认值，全部向后兼容。
        # ================================================================
        fields: Dict[str, Any] = {
            "_history": [],
            "_chat_memories": [],
            "_life_events": [],
            "_resolved_behavior": None,
            "_expr_constraint": None,
            "_sm_text": None,
            "_emo_text": None,
            "_rel_text": None,
            "_experience_ctx": [],
            "_agree_text": None,
        }

        # 1. history / chat_memories / life_events
        if ctx is not None:
            # 通过 ctx 私有属性注入（与 _self_model_snapshot /
            # _identity_context 的既有模式保持一致），不改动 RuntimeContext
            # 公共 schema。
            for attr, target in (
                ("_recent_history", "_history"),
                ("_recent_chat_memories", "_chat_memories"),
                ("_recent_life_events", "_life_events"),
                ("_experience_context", "_experience_ctx"),
            ):
                raw = getattr(ctx, attr, None)
                if isinstance(raw, list):
                    fields[target] = list(raw)
            # 从 ctx.memory_context（MemoryContext 对象）尽力抽取
            mc = getattr(ctx, "memory_context", None)
            if isinstance(mc, dict):
                if isinstance(mc.get("recent_history"), list) and not fields["_history"]:
                    fields["_history"] = list(mc["recent_history"])
                if isinstance(mc.get("top_memories"), list) and not fields["_chat_memories"]:
                    fields["_chat_memories"] = list(mc["top_memories"])
                if isinstance(mc.get("life_events"), list) and not fields["_life_events"]:
                    fields["_life_events"] = list(mc["life_events"])
                if isinstance(mc.get("experience_context"), list) and not fields["_experience_ctx"]:
                    fields["_experience_ctx"] = list(mc["experience_context"])

        # 2. resolved_behavior（来自 personality_context.behavior_context /
        #    behavior_signature / behavior_snapshot / behavior_constraints）
        if isinstance(personality_context, dict):
            # resolved_behavior：优先用现成的 behavior_signature 或
            # behavior_context，其次从 behavior_constraints +
            # communication_style 合成（尽量少猜，缺项就不填）
            for candidate_key in (
                "resolved_behavior",
                "behavior_snapshot",
                "behavior_signature",
                "behavior_context",
            ):
                cb = personality_context.get(candidate_key)
                if isinstance(cb, dict) and any(cb.values()):
                    fields["_resolved_behavior"] = dict(cb)
                    break
            if fields["_resolved_behavior"] is None:
                _cs = personality_context.get("communication_style") or {}
                _bc = personality_context.get("behavior_constraints") or []
                _cons = personality_context.get("consistency_rules") or []
                if _cs or _bc or _cons:
                    _rb: Dict[str, Any] = {}
                    if _cs:
                        # communication_style -> 当前表达参考
                        _mapped_cs: Dict[str, Any] = {}
                        if _cs.get("tone"):
                            _mapped_cs["chosen_expression"] = str(_cs["tone"])
                        if _cs.get("directness") is not None:
                            _mapped_cs["chosen_directness"] = _cs["directness"]
                        warmth = _cs.get("warmth")
                        if isinstance(warmth, (int, float)):
                            _mapped_cs["warmth_level"] = float(warmth)
                        if _mapped_cs:
                            _rb["mapped_communication_style"] = _mapped_cs
                    if _bc:
                        sensitivity_notes = [
                            str(x) for x in _bc if str(x).strip()
                        ][:8]
                        if sensitivity_notes:
                            _rb["sensitivity_notes"] = sensitivity_notes
                    if _cons:
                        rules_text = "; ".join(
                            str(x) for x in _cons[:6] if str(x).strip()
                        )
                        if rules_text:
                            _rb["resolution_reason"] = rules_text
                    # 空壳不下发，避免污染 Prompt
                    if _rb and any(_rb.values()):
                        fields["_resolved_behavior"] = _rb

            # 3. expression_constraint_text（已有 personality_context 中透传）
            for k in (
                "expression_constraint_text",
                "expression_constraint",
                "expression_rules_text",
            ):
                v = personality_context.get(k)
                if isinstance(v, str) and v.strip():
                    fields["_expr_constraint"] = v.strip()
                    break

            # 4. self_model / emotion / relationship / agreement 已有文本
            for k, dest in (
                ("identity_context_text", "_sm_text"),  # Phase 4.4 注入的优先
                ("self_model_text", "_sm_text"),
            ):
                v = personality_context.get(k)
                if isinstance(v, str) and v.strip():
                    fields[dest] = v.strip()
                    break
            for k, dest in (
                ("emotion_context_text", "_emo_text"),
                ("emotion_text", "_emo_text"),
            ):
                v = personality_context.get(k)
                if isinstance(v, str) and v.strip():
                    fields[dest] = v.strip()
                    break
            for k, dest in (
                ("relationship_context_text", "_rel_text"),
                ("relationship_text", "_rel_text"),
            ):
                v = personality_context.get(k)
                if isinstance(v, str) and v.strip():
                    fields[dest] = v.strip()
                    break
            for k in ("agreement_context_text", "agreement_text"):
                v = personality_context.get(k)
                if isinstance(v, str) and v.strip():
                    fields["_agree_text"] = v.strip()
                    break

        req = ResponseRequest(
            user_input=user_input,
            memory_context=ctx.memory_context if ctx is not None else None,
            emotion_state=ctx.emotion_state if ctx is not None else None,
            personality_context=personality_context,
            relationship_state=relationship_state,
            facts_boundary=facts_boundary,
            draft_facts=facts_list,
            response_engine=response_engine,
            schema_version=RESPONSE_ADAPTER_SCHEMA_VERSION,
            # Phase 1：生成表达链字段
            history=fields["_history"],
            chat_memories=fields["_chat_memories"],
            life_events=fields["_life_events"],
            resolved_behavior=fields["_resolved_behavior"],
            expression_constraint_text=fields["_expr_constraint"],
            self_model_context=fields["_sm_text"],
            emotion_context=fields["_emo_text"],
            relationship_context=fields["_rel_text"],
            experience_context=fields["_experience_ctx"],
            agreement_context=fields["_agree_text"],
        )
        self._last_request = req
        self._build_count += 1
        return req

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def last_request(self) -> Optional[ResponseRequest]:
        return self._last_request

    @property
    def build_count(self) -> int:
        return self._build_count


__all__ = [
    "ResponseAdapter",
    "ResponseRequest",
    "RESPONSE_ADAPTER_SCHEMA_VERSION",
]
