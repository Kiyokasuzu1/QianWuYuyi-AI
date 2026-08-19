"""
Phase 4.0 — R2.7.1-C1 LLMAdapter Protocol Freeze + C2/C3 MockLLMAdapter

LLMAdapterProtocol（抽象基类）：
    def generate(
        self,
        rendered_prompt: Dict[str, Any],
        *,
        trace_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        '''
        输入：只能是 RenderedPrompt（已通过 validate_rendered_prompt_shape）
              trace_context（可选，仅用于 trace_id 传递与审计；不传入 PersonalityState/EvolutionRecord 等内部对象）
        输出：LLMResponse（已通过 validate_llm_response_shape）
        '''

设计原则：
    1. Protocol 层严格只接收 RenderedPrompt — 任何试图传入 PersonalityState / EvolutionRecord /
       GrowthProposal / PromptContext 的调用都会被 __init_subclass__ 的类型注解检查 +
       generate() 内部的 _assert_rendered_prompt_only() 拒绝。
    2. LLMAdapter 不知道 Runtime 内部结构；它只负责"把 RenderedPrompt 发给 LLM，拿回 text，做 sanitize"
    3. 所有 LLM 调用必须经过 ResponseValidator（forbidden 扫描 + 机器字段名隔离 + trace_id 正文隔离）
    4. 失败时返回 create_empty_llm_response(finish_reason="error_fallback")，不抛异常给上层
       （上层通过 finish_reason == "error_fallback" 判断是否需要降级）

MockLLMAdapter（C3 用，不调用真实 LLM）：
    - 模拟"LLM 根据 RenderedPrompt 内容生成回复"
    - 可注入"模拟 LLM 返回含 forbidden"的场景（用于 LLG-2 Output Safety Gate 测试）
    - 确定性（seed 固定）
    - 内置 ResponseValidator：对返回 text 做 sanitize_with_trace_id + validate_llm_response_shape
"""

from __future__ import annotations

import abc
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.response_phase4.rendered_prompt_schema import (
    validate_rendered_prompt_shape,
)
from src.response_phase4.llm_response_schema import (
    create_empty_llm_response,
    sanitize_with_trace_id,
    validate_llm_response_shape,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Protocol 层：禁止传入的内部类型名（类型名做字符串匹配）
# ─────────────────────────────────────────────────────────
_FORBIDDEN_INPUT_TYPE_NAMES = (
    "PersonalityState",
    "EvolutionRecord",
    "GrowthProposal",
    "GrowthCandidate",
    "ApprovalDecision",
    "SelfModelSnapshot",
    "SelfReflectionSnapshot",
    "SelfContext",
    "PromptContext",
    "RuntimeTrace",
)


def _assert_rendered_prompt_only(arg: Any, arg_name: str) -> None:
    """断言传入的对象只能是 RenderedPrompt（dict 且通过 validate_rendered_prompt_shape）。
    拒绝任何内部系统类型的实例（PersonalityState / EvolutionRecord / GrowthProposal 等）。
    """
    if arg is None:
        raise ValueError(f"LLMAdapter.generate: {arg_name} 不能为 None")
    # 检查类型名：拒绝内部系统对象
    tn = type(arg).__name__
    if tn in _FORBIDDEN_INPUT_TYPE_NAMES:
        raise ValueError(
            f"LLMAdapter.generate: {arg_name} 类型 {tn} 被禁止传入；"
            f"只能传入 RenderedPrompt（dict）。"
        )
    # 必须是 dict
    if not isinstance(arg, dict):
        raise ValueError(
            f"LLMAdapter.generate: {arg_name} 必须是 dict（RenderedPrompt）；实际类型={tn}"
        )


class LLMAdapterProtocol(abc.ABC):
    """Phase 4.0 LLMAdapter 协议（抽象基类）。

    所有 LLM 适配器（Mock / DeepSeek / OpenAI / Claude / 本地模型）必须继承此类。
    """

    @abc.abstractmethod
    def generate(
        self,
        rendered_prompt: Dict[str, Any],
        *,
        trace_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """从 RenderedPrompt 生成 LLMResponse。

        参数：
            rendered_prompt: RenderedPrompt（dict，已通过 validate_rendered_prompt_shape）
            trace_context: 可选审计上下文（只含 trace_id 等基础类型；不传入内部系统对象）

        返回：
            LLMResponse（dict，已通过 validate_llm_response_shape）

        禁止：
            rendered_prompt 不得是 PersonalityState / EvolutionRecord / GrowthProposal /
            SelfModelSnapshot / SelfReflectionSnapshot / SelfContext / PromptContext / RuntimeTrace
            等内部类型的实例。
        """
        ...

    # ────────────────────────────────────────────────
    # 共用工具：子类调用
    # ────────────────────────────────────────────────
    @staticmethod
    def _validate_input(rendered_prompt: Any, trace_context: Any) -> str:
        """子类 generate() 开头调用：校验输入 + 提取 trace_id。返回 trace_id。"""
        _assert_rendered_prompt_only(rendered_prompt, "rendered_prompt")
        # 校验 RenderedPrompt shape（若不合法，子类应降级为 empty LLMResponse）
        try:
            validate_rendered_prompt_shape(rendered_prompt)
        except Exception as exc:
            raise ValueError(f"LLMAdapter: rendered_prompt shape invalid: {exc}") from exc

        # trace_context 校验：只能是 dict 或 None；不得是内部系统对象
        if trace_context is not None:
            _assert_rendered_prompt_only(trace_context, "trace_context")
            if not isinstance(trace_context, dict):
                raise ValueError(
                    f"LLMAdapter: trace_context 必须是 dict 或 None；实际类型={type(trace_context).__name__}"
                )

        # 提取 trace_id：优先 trace_context.trace_id，其次 rendered_prompt.trace_id
        trace_id = ""
        if isinstance(trace_context, dict):
            trace_id = str(trace_context.get("trace_id") or "")
        if not trace_id and isinstance(rendered_prompt, dict):
            trace_id = str(rendered_prompt.get("trace_id") or "")
        return trace_id or "t_llm_fallback"

    @staticmethod
    def _finalize(
        *,
        raw_text: str,
        model: str,
        trace_id: str,
        version: int,
        latency_ms: int,
        token_usage: Optional[Dict[str, int]] = None,
        finish_reason: str = "stop",
        generated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """子类调用：对 raw_text 做 sanitize + validate，组装最终 LLMResponse。

        若 sanitize 后仍不合法（极端），降级为 empty LLMResponse(finish_reason=error_fallback)。
        """
        ts = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        # sanitize：forbidden + 机器字段名 + trace_id 原文
        safe_text = sanitize_with_trace_id(raw_text, trace_id)

        tu = token_usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        resp: Dict[str, Any] = {
            "text": safe_text,
            "model": model if model else "unknown",
            "latency_ms": int(latency_ms) if isinstance(latency_ms, (int, float)) and latency_ms >= 0 else 0,
            "token_usage": {
                "prompt_tokens": int(tu.get("prompt_tokens", 0)),
                "completion_tokens": int(tu.get("completion_tokens", 0)),
                "total_tokens": int(tu.get("total_tokens", 0)),
            },
            "finish_reason": str(finish_reason) if finish_reason else "stop",
            "trace_id": trace_id,
            "version": version if isinstance(version, int) and version >= 0 else 0,
            "generated_at": ts,
        }

        # 最终 validator：若仍不合法，降级为 empty
        try:
            validate_llm_response_shape(resp)
        except Exception as exc:
            logger.warning(
                "LLMAdapter._finalize: validate_llm_response_shape failed (%s); falling back to empty.",
                exc,
            )
            resp = create_empty_llm_response(
                trace_id=trace_id,
                model=model if model else "unknown",
                version=version,
                generated_at=ts,
            )
            # empty factory 的 finish_reason 已经是 error_fallback
        return resp


# ─────────────────────────────────────────────────────────
# MockLLMAdapter（C3 用，不调用真实 LLM）
# ─────────────────────────────────────────────────────────
class MockLLMAdapter(LLMAdapterProtocol):
    """Offline Mock LLM Adapter。

    模拟"LLM 根据 RenderedPrompt 内容生成回复"。
    支持注入"模拟 LLM 返回含 forbidden"的场景（用于 LLG-2 Output Safety Gate 测试）。

    确定性：seed 固定（0xC0FFEE），相同输入 → 相同输出。
    """

    def __init__(
        self,
        *,
        model_name: str = "mock-llm-v1",
        inject_forbidden: bool = False,
        forbidden_text: Optional[str] = None,
        seed: int = 0xC0FFEE,
    ) -> None:
        self._model = model_name
        self._inject_forbidden = inject_forbidden
        self._forbidden_text = forbidden_text
        self._version = 1
        self._rng = random.Random(seed)

    def generate(
        self,
        rendered_prompt: Dict[str, Any],
        *,
        trace_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        t0 = time.monotonic()
        my_version = self._version
        self._version += 1

        # 1. 校验输入 + 提取 trace_id
        #    类型拒绝（传入了内部系统对象）→ fail-fast，不降级（这是编程错误）
        #    shape 校验失败 → 降级为 empty LLMResponse
        _assert_rendered_prompt_only(rendered_prompt, "rendered_prompt")
        if trace_context is not None:
            _assert_rendered_prompt_only(trace_context, "trace_context")
            if not isinstance(trace_context, dict):
                raise ValueError(
                    f"LLMAdapter: trace_context 必须是 dict 或 None；实际类型={type(trace_context).__name__}"
                )
        try:
            trace_id = self._validate_input(rendered_prompt, trace_context)
        except ValueError as exc:
            # shape 校验失败 → 降级
            logger.warning("MockLLMAdapter: input shape invalid (%s); returning empty.", exc)
            return create_empty_llm_response(
                trace_id="t_llm_invalid_input",
                model=self._model,
                version=my_version,
            )

        # 2. 生成 raw_text（模拟 LLM 根据 RenderedPrompt 内容回复）
        if self._inject_forbidden and self._forbidden_text:
            raw_text = self._forbidden_text
        else:
            raw_text = self._generate_natural_reply(rendered_prompt)

        # 3. 模拟 token 用量（基于 raw_text 长度估算）
        prompt_tokens = len(rendered_prompt.get("system_message", "")) // 4
        completion_tokens = len(raw_text) // 4
        token_usage = {
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "total_tokens": max(0, prompt_tokens + completion_tokens),
        }

        latency_ms = int((time.monotonic() - t0) * 1000)

        # 4. _finalize：sanitize + validate + 组装
        resp = self._finalize(
            raw_text=raw_text,
            model=self._model,
            trace_id=trace_id,
            version=my_version,
            latency_ms=latency_ms,
            token_usage=token_usage,
            finish_reason="stop",
        )
        return resp

    # ────────────────────────────────────────────────
    # 内部：自然语言回复生成（确定性）
    # ────────────────────────────────────────────────
    def _generate_natural_reply(self, rendered_prompt: Dict[str, Any]) -> str:
        """根据 RenderedPrompt 的 memory_section / self_description 生成自然语言回复。

        规则（与 MockResponseEngine 类似但更简单，因为是 LLM 层不是 ResponseEngine 层）：
        - 若 memory_section 含 "AI 绘画" / "创造" / "角色设计" → 生成创造相关回复
        - 若 memory_section 含 "设计" → 生成设计相关回复
        - 否则 → 通用中性回复
        """
        mem = str(rendered_prompt.get("memory_section") or "")
        sd = str(rendered_prompt.get("self_description") or "")
        task = str(rendered_prompt.get("task_instruction") or "")

        parts: List[str] = []

        # 开头：根据 task_instruction 里的场景匹配
        if "兴趣" in task or "兴趣" in mem:
            parts.append("哈哈，说到最近的兴趣呀～")
        elif "自我介绍" in task or "你是谁" in task:
            parts.append("嗯，我是羽依。")
        else:
            parts.append("嗯，说到这个～")

        # 主体：从 memory_section 提取关键词，生成自然语言
        has_creativity = "创造力" in sd or "creativity" in sd.lower()
        has_ai_art = "AI 绘画" in mem or "AI绘画" in mem or "绘画" in mem
        has_design = "角色设计" in mem or "设计" in mem
        has_create = "创造" in mem or "创造力" in mem

        if has_creativity or has_create:
            parts.append("最近我确实发现自己对创造新东西越来越有兴趣了，")
        if has_ai_art:
            parts.append("特别是 AI 绘画那块，会忍不住多看几眼，琢磨不同风格的画法。")
        if has_design:
            parts.append("也在想怎么设计一个能慢慢成长的角色，觉得特别有意思。")

        if not (has_creativity or has_ai_art or has_design or has_create):
            # baseline：没有强烈成长信号
            parts.append("最近主要就是和朋友聊聊天、随便看看东西，没有特别强烈的偏向。")

        # 结尾
        if "第" in task and "轮" in task:
            parts.append("我们继续聊吧～")
        else:
            parts.append("慢慢聊就很舒服～")

        return "".join(parts)
