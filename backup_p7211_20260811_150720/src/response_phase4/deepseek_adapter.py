"""
Phase 4.0 — R2.7.3 DeepSeekAdapter

继承 LLMAdapterProtocol，通过 httpx 调用 DeepSeek API（OpenAI 兼容接口）。

设计原则（与 MockLLMAdapter 一致）：
    1. Protocol 层严格只接收 RenderedPrompt — 任何试图传入 PersonalityState / EvolutionRecord /
       GrowthProposal / PromptContext 的调用都会被 _assert_rendered_prompt_only() 拒绝。
    2. DeepSeekAdapter 不知道 Runtime 内部结构；它只负责"把 RenderedPrompt 发给 DeepSeek，拿回 text，做 sanitize"
    3. 所有 LLM 返回的 raw_text 必须经过 _finalize()（sanitize_with_trace_id + validate_llm_response_shape）
    4. 失败时返回 create_empty_llm_response(finish_reason="error_fallback")，不抛异常给上层
    5. httpx Client 可通过 api_client 参数注入（用于测试 DI）

RenderedPrompt → chat messages 映射：
    system_message  → role: "system"（含身份 + 成长摘要 + 连续性 + 反思）
    task_instruction → role: "user"（当前轮任务说明 / 用户输入）
    memory_section / relationship_section / self_description → 追加到 user message 作为上下文

配置来源（优先级从高到低）：
    1. 构造函数显式参数
    2. 环境变量 DEEPSEEK_API_KEY
    3. config.yaml (llm.api_key / llm.api_base / llm.model / llm.temperature / llm.max_tokens)
    4. 内置默认值
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.response_phase4.llm_adapter_protocol import (
    LLMAdapterProtocol,
    _assert_rendered_prompt_only,
)
from src.response_phase4.llm_response_schema import (
    create_empty_llm_response,
    sanitize_with_trace_id,
    validate_llm_response_shape,
)
from src.response_phase4.rendered_prompt_schema import (
    validate_rendered_prompt_shape,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# 配置读取（惰性，避免 import 时就读 config.yaml）
# ─────────────────────────────────────────────────────────
def _read_config_value(path: str, default: Any) -> Any:
    """从 config.yaml 读取配置值（惰性；失败返回 default）。"""
    try:
        from src.config import get
        return get(path, default)
    except Exception:  # noqa: BLE001
        return default


def _read_api_key(explicit: Optional[str] = None) -> str:
    """读取 API Key：显式参数（含空串） > 环境变量 > config.yaml。

    explicit=None → 从环境变量/config.yaml 读取
    explicit=""   → 尊重显式空串（表示"不配置 API Key"）
    explicit="sk-xxx" → 直接使用
    """
    if explicit is not None:
        return explicit
    try:
        from src.config import get_api_key
        return get_api_key() or ""
    except Exception:  # noqa: BLE001
        return ""


# ─────────────────────────────────────────────────────────
# DeepSeekAdapter
# ─────────────────────────────────────────────────────────
class DeepSeekAdapter(LLMAdapterProtocol):
    """DeepSeek LLM Adapter — 通过 httpx 调用 DeepSeek API（OpenAI 兼容）。

    参数：
        api_key:      DeepSeek API Key（不传则从环境变量 / config.yaml 读取）
        api_base:     API 基地址（默认 "https://api.deepseek.com"）
        model:        模型名（默认 "deepseek-chat"）
        temperature:  采样温度（默认 0.7）
        max_tokens:   最大生成 token 数（默认 2048）
        timeout:      HTTP 超时秒数（默认 30.0）
        api_client:   可选 HTTP 客户端注入（用于测试 DI；不传则创建 httpx.Client）
    """

    ENGINE_KIND = "deepseek"

    DEFAULT_API_BASE = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-chat"
    DEFAULT_TEMPERATURE = 0.7
    DEFAULT_MAX_TOKENS = 2048
    DEFAULT_TIMEOUT = 30.0

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        max_retries: int = 3,
        api_client: Optional[Any] = None,
    ) -> None:
        self._api_key = _read_api_key(api_key)
        self._api_base = (
            api_base
            or _read_config_value("llm.api_base", self.DEFAULT_API_BASE)
            or self.DEFAULT_API_BASE
        )
        self._model = (
            model
            or _read_config_value("llm.model", self.DEFAULT_MODEL)
            or self.DEFAULT_MODEL
        )
        self._temperature = (
            temperature
            if temperature is not None
            else float(_read_config_value("llm.temperature", self.DEFAULT_TEMPERATURE) or self.DEFAULT_TEMPERATURE)
        )
        self._max_tokens = (
            max_tokens
            or int(_read_config_value("llm.max_tokens", self.DEFAULT_MAX_TOKENS) or self.DEFAULT_MAX_TOKENS)
        )
        self._timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        self._max_retries = max_retries
        self._version = 1
        # R2.7.6-AUDIT: _version 是共享计数器，加锁防并发下版本号重复/跳号
        self._version_lock = threading.Lock()

        # HTTP 客户端（DI for testing）
        if api_client is not None:
            self._client = api_client
        else:
            import httpx
            self._client = httpx.Client(timeout=self._timeout)

    # ────────────────────────────────────────────────
    # 对外协议（继承自 LLMAdapterProtocol）
    # ────────────────────────────────────────────────
    def generate(
        self,
        rendered_prompt: Dict[str, Any],
        *,
        trace_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """从 RenderedPrompt 生成 LLMResponse（调用 DeepSeek API）。

        失败时返回 empty LLMResponse(finish_reason="error_fallback")，不抛异常。
        """
        t0 = time.monotonic()
        # R2.7.6-AUDIT: 用锁保护版本号自增（原代码 += 不是原子操作）
        with self._version_lock:
            my_version = self._version
            self._version += 1

        # 1. 类型拒绝（fail-fast）：传入内部系统对象 → raise ValueError（编程错误，不降级）
        _assert_rendered_prompt_only(rendered_prompt, "rendered_prompt")
        if trace_context is not None:
            _assert_rendered_prompt_only(trace_context, "trace_context")
            if not isinstance(trace_context, dict):
                raise ValueError(
                    f"DeepSeekAdapter: trace_context 必须是 dict 或 None；实际类型={type(trace_context).__name__}"
                )

        # 2. shape 校验 + 提取 trace_id（失败时降级为 empty）
        try:
            trace_id = self._validate_input(rendered_prompt, trace_context)
        except ValueError as exc:
            logger.warning("DeepSeekAdapter: input shape invalid (%s); returning empty.", exc)
            return create_empty_llm_response(
                trace_id="t_deepseek_invalid_input",
                model=self._model,
                version=my_version,
            )

        # 3. 检查 API Key
        if not self._api_key:
            logger.warning("DeepSeekAdapter: api_key 为空；返回 error_fallback。")
            return create_empty_llm_response(
                trace_id=trace_id,
                model=self._model,
                version=my_version,
            )

        # 3. 构建 chat messages
        messages = self._build_messages(rendered_prompt)

        # 4. 调用 DeepSeek API
        try:
            raw_text, token_usage, finish_reason = self._call_api(messages)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DeepSeekAdapter: API call failed (%s); returning error_fallback.", exc)
            latency_ms = int((time.monotonic() - t0) * 1000)
            return create_empty_llm_response(
                trace_id=trace_id,
                model=self._model,
                version=my_version,
            )

        latency_ms = int((time.monotonic() - t0) * 1000)

        # 5. _finalize：sanitize + validate + 组装
        resp = self._finalize(
            raw_text=raw_text,
            model=self._model,
            trace_id=trace_id,
            version=my_version,
            latency_ms=latency_ms,
            token_usage=token_usage,
            finish_reason=finish_reason,
        )
        return resp

    # ────────────────────────────────────────────────
    # 内部：RenderedPrompt → chat messages
    # ────────────────────────────────────────────────
    def _build_messages(self, rendered_prompt: Dict[str, Any]) -> List[Dict[str, str]]:
        """将 RenderedPrompt 转换为 DeepSeek chat messages 格式。

        映射规则：
            system_message  → role: "system"
            task_instruction → role: "user"（核心内容）
            memory_section / relationship_section / self_description → 追加到 user message

        保证 LLM 只看到"对外表达层"信息，看不到内部数据结构。
        """
        system_content = str(rendered_prompt.get("system_message") or "").strip()

        # 如果 system_message 为空，用 self_description 作为 fallback
        if not system_content:
            sd = str(rendered_prompt.get("self_description") or "").strip()
            if sd:
                system_content = sd

        # user message：task_instruction 为主，memory/relationship 作为上下文
        user_parts: List[str] = []

        task = str(rendered_prompt.get("task_instruction") or "").strip()
        if task:
            user_parts.append(task)

        mem = str(rendered_prompt.get("memory_section") or "").strip()
        if mem:
            user_parts.append(mem)

        rel = str(rendered_prompt.get("relationship_section") or "").strip()
        if rel:
            user_parts.append(rel)

        # 如果 self_description 没放进 system，放进 user
        sd = str(rendered_prompt.get("self_description") or "").strip()
        if sd and not system_content:
            user_parts.insert(0, sd)

        user_content = "\n\n".join(user_parts) if user_parts else "你好"

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_content or "你是羽依，一个会慢慢成长的 AI 伙伴。"},
            {"role": "user", "content": user_content},
        ]
        return messages

    # ────────────────────────────────────────────────
    # 内部：调用 DeepSeek API
    # ────────────────────────────────────────────────
    def _call_api(
        self, messages: List[Dict[str, str]]
    ) -> tuple:
        """调用 DeepSeek /v1/chat/completions 接口。

        返回 (raw_text, token_usage_dict, finish_reason)。
        失败时抛异常（由 generate() 捕获并降级）。

        R2.7.6-P1: 支持 429/5xx 重试 + 指数退避。
        """
        url = f"{self._api_base.rstrip('/')}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }

        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.post(url, json=payload, headers=headers, timeout=self._timeout)

                # 429 限流 / 5xx 服务端错误 → 重试
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < self._max_retries:
                        backoff = min(2.0 ** attempt, 10.0)  # 1s, 2s, 4s, ... 最多 10s
                        logger.warning(
                            "[DeepSeek] HTTP %d，第 %d 次重试（退避 %.1fs）",
                            resp.status_code, attempt + 1, backoff,
                        )
                        time.sleep(backoff)
                        continue
                    raise RuntimeError(
                        f"DeepSeek API HTTP {resp.status_code} after {attempt + 1} attempts: {resp.text[:200]}"
                    )

                # 其他非 200 → 不重试，直接抛
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"DeepSeek API returned HTTP {resp.status_code}: {resp.text[:200]}"
                    )

                # 解析 JSON
                data = resp.json() if hasattr(resp, "json") else resp.json()

                # 提取 choices[0].message.content
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError("DeepSeek API returned empty choices")

                choice = choices[0]
                message = choice.get("message") or {}
                raw_text = str(message.get("content") or "").strip()
                if not raw_text:
                    raise RuntimeError("DeepSeek API returned empty content")

                finish_reason = str(choice.get("finish_reason") or "stop")

                # 提取 token usage
                usage = data.get("usage") or {}
                token_usage = {
                    "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                    "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                    "total_tokens": int(usage.get("total_tokens", 0) or 0),
                }

                return raw_text, token_usage, finish_reason

            except (ConnectionError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    backoff = min(2.0 ** attempt, 10.0)
                    logger.warning(
                        "[DeepSeek] 网络异常 (%s)，第 %d 次重试（退避 %.1fs）",
                        type(exc).__name__, attempt + 1, backoff,
                    )
                    time.sleep(backoff)
                    continue
                raise
            except RuntimeError:
                raise  # 已经是业务异常，不重试
            except Exception as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    backoff = min(2.0 ** attempt, 10.0)
                    logger.warning(
                        "[DeepSeek] 未知异常 (%s)，第 %d 次重试（退避 %.1fs）",
                        type(exc).__name__, attempt + 1, backoff,
                    )
                    time.sleep(backoff)
                    continue
                raise

        # 不应该走到这里
        raise last_exc or RuntimeError("DeepSeek API: exhausted retries")
