"""
Phase 4.0 — R2.7.1-C2 LLMResponse Contract（schema + validator + sanitize/fallback）

冻结 LLM 返回的对外结构：
  text: str                — 自然语言回复（用户直接看到的唯一字段）
  model: str               — 模型名（如 "deepseek-v4-pro" / "mock-v1"）
  latency_ms: int          — 调用耗时毫秒（≥0）
  token_usage: Dict        — {prompt_tokens, completion_tokens, total_tokens}（均 int ≥0）
  finish_reason: str       — "stop" / "length" / "content_filter" / "error_fallback"
  trace_id: str            — 本认知循环 trace_id（与 RenderedPrompt.trace_id 一致）
  version: int             — LLMResponse 版本（每次 generate +1）
  generated_at: str        — ISO 时间戳

严格信息边界（最后一道防线，在 RenderedPrompt 之后）：
  FORBIDDEN_KEYS：proposal_id / approval_reason / internal_score / evaluator_meta / confidence
  FORBIDDEN_VALUE_TOKEN：上述 5 词（case-insensitive 子串）+ trait_delta（\\b 正则）
  → 若 LLM 返回的 text 中出现，由 sanitize_llm_response_text() 替换为 "……"
  → 若 sanitize 后仍有残留（极端情况），由 validate_llm_response_shape() 抛 ValueError

机器字段名隔离（防止 LLM 把元数据"说"出来）：
  text 中不得出现以下机器字段名（作为独立 word）：
    model / latency_ms / token_usage / finish_reason / trace_id / version / generated_at
    prompt_tokens / completion_tokens / total_tokens
  → 用正则 \\b{name}\\b 检测

trace_id 正文隔离：
  text 中不得包含 trace_id 原文（len(trace_id) ≥ 4 时检测）

设计原则（不跨红线）：
  - 只做 schema 校验 + sanitize；不调用任何 LLM
  - 所有字段皆基础类型（str/int/dict）
  - 白名单顶层字段，禁止额外键
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Tuple

# ─────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────
LLM_RESPONSE_FIELDS = (
    "text",
    "model",
    "latency_ms",
    "token_usage",
    "finish_reason",
    "trace_id",
    "version",
    "generated_at",
)

FINISH_REASON_WHITELIST = frozenset(
    {"stop", "length", "content_filter", "tool_calls", "error_fallback"}
)

# forbidden 关键字（与 RenderedPrompt 保持一致）
_FORBIDDEN_KEY_WORDS = (
    "proposal_id",
    "approval_reason",
    "internal_score",
    "evaluator_meta",
    "confidence",
)

_TRAIT_DELTA_RE = re.compile(r"(?<![a-zA-Z0-9_])trait_delta(?![a-zA-Z0-9_])", re.IGNORECASE)

# 机器字段名（text 中不得出现这些独立 token，防止 LLM 把元数据"说"出来）
_MACHINE_FIELD_NAMES = (
    "model",
    "latency_ms",
    "token_usage",
    "finish_reason",
    "trace_id",
    "version",
    "generated_at",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "engine_version",
    "self_model_version",
    "self_reflection_version",
    "rendered_prompt_version",
    "prompt_context_version",
    "self_context_version",
    "continuity_status",
    "growth_happened",
)

# 预编译正则：每个机器字段名做 ASCII 边界匹配（case-insensitive）
# 用 (?<![a-zA-Z0-9_]) 和 (?![a-zA-Z0-9_]) 替代 \b，因为 Python Unicode 模式下中文也算 \w
_MACHINE_FIELD_RES = [
    (name, re.compile(r"(?<![a-zA-Z0-9_])" + re.escape(name) + r"(?![a-zA-Z0-9_])", re.IGNORECASE))
    for name in _MACHINE_FIELD_NAMES
]


# ─────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────
def _forbidden_flat_hits(obj: Any) -> List[Tuple[str, str, str]]:
    """递归扫描 forbidden（键名 + 字符串值子串 + trait_delta 正则）。"""
    hits: List[Tuple[str, str, str]] = []
    KEY_FORBIDDEN = frozenset(_FORBIDDEN_KEY_WORDS)

    def walk(o: Any, path: str) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                ks = str(k).lower()
                if ks in KEY_FORBIDDEN:
                    hits.append((ks, path + f".KEY:{k}", f"forbidden_key={k}"))
                walk(v, path + "." + str(k))
            return
        if isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
            return
        if isinstance(o, str):
            s_low = o.lower()
            for w in _FORBIDDEN_KEY_WORDS:
                if w in s_low:
                    hits.append((w, path, o[:200]))
            if _TRAIT_DELTA_RE.search(o):
                hits.append(("trait_delta", path, o[:200]))
            return

    walk(obj, "root")
    return hits


def _machine_field_hits(text: str) -> List[Tuple[str, str]]:
    """检测 text 中是否出现机器字段名（独立 token）。返回 [(field_name, snippet), ...]。"""
    if not isinstance(text, str):
        return []
    hits: List[Tuple[str, str]] = []
    for name, regex in _MACHINE_FIELD_RES:
        m = regex.search(text)
        if m:
            start = max(0, m.start() - 10)
            end = min(len(text), m.end() + 10)
            hits.append((name, text[start:end]))
    return hits


def sanitize_llm_response_text(text: str) -> str:
    """对 LLM 返回的 text 做 forbidden 清洗：
    - proposal_id / approval_reason / internal_score / evaluator_meta / confidence
      → 用正则 case-insensitive 替换为 "……"
    - trait_delta（独立 token）→ 替换为 "……"
    - trace_id 原文（若调用方提供 trace_id）→ 替换为 "……"（由 _sanitize_with_trace_id 调用）

    注意：此函数不删 trace_id（因为 trace_id 是动态的，需要调用方传入）。
    """
    if not isinstance(text, str):
        return ""
    out = text
    for w in _FORBIDDEN_KEY_WORDS:
        out = re.sub(re.escape(w), "……", out, flags=re.IGNORECASE)
    out = _TRAIT_DELTA_RE.sub("……", out)
    # 机器字段名：用 "……" 替换（但不删太多，只替换独立 token）
    for _name, regex in _MACHINE_FIELD_RES:
        out = regex.sub("……", out)
    return out


def sanitize_with_trace_id(text: str, trace_id: str) -> str:
    """先做 forbidden + 机器字段名清洗，再删 trace_id 原文。"""
    out = sanitize_llm_response_text(text)
    if isinstance(trace_id, str) and len(trace_id) >= 4 and trace_id in out:
        out = out.replace(trace_id, "……")
    return out


def create_empty_llm_response(
    *,
    trace_id: str = "trace_empty",
    model: str = "unknown",
    version: int = 0,
    generated_at: str = "1970-01-01T00:00:00+00:00",
) -> Dict[str, Any]:
    """空工厂：用于 LLM 调用失败 / sanitize 失败时的安全回退。"""
    return {
        "text": "",
        "model": model if model else "unknown",
        "latency_ms": 0,
        "token_usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "finish_reason": "error_fallback",
        "trace_id": trace_id if trace_id else "trace_empty",
        "version": version if isinstance(version, int) and version >= 0 else 0,
        "generated_at": generated_at,
    }


def validate_llm_response_shape(resp: Any) -> None:
    """严格校验 LLMResponse 结构 + 信息边界。失败抛 ValueError（带路径）。"""
    if not isinstance(resp, dict):
        raise ValueError("llm_response must be a dict")

    # 1) 顶层必备字段
    for req in LLM_RESPONSE_FIELDS:
        if req not in resp:
            raise ValueError(f"llm_response missing top-level key: {req}")

    # 2) 白名单：禁止额外顶层键
    allowed_top = set(LLM_RESPONSE_FIELDS)
    for k in resp.keys():
        if k not in allowed_top:
            raise ValueError(f"llm_response 禁止额外顶层字段 {k!r}（白名单={sorted(allowed_top)}）")

    # 3) text: str
    if not isinstance(resp["text"], str):
        raise ValueError("llm_response.text must be str")

    # 4) model: str
    if not isinstance(resp["model"], str) or not resp["model"].strip():
        raise ValueError("llm_response.model must be non-empty str")

    # 5) latency_ms: int ≥ 0
    if not isinstance(resp["latency_ms"], int) or resp["latency_ms"] < 0:
        raise ValueError("llm_response.latency_ms must be int ≥ 0")

    # 6) token_usage: dict with 3 int fields
    tu = resp["token_usage"]
    if not isinstance(tu, dict):
        raise ValueError("llm_response.token_usage must be dict")
    for tk in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if tk not in tu:
            raise ValueError(f"llm_response.token_usage missing key: {tk}")
        if not isinstance(tu[tk], int) or tu[tk] < 0:
            raise ValueError(f"llm_response.token_usage.{tk} must be int ≥ 0")

    # 7) finish_reason: enum
    if str(resp["finish_reason"]) not in FINISH_REASON_WHITELIST:
        raise ValueError(
            f"llm_response.finish_reason must be in {sorted(FINISH_REASON_WHITELIST)}; got {resp['finish_reason']!r}"
        )

    # 8) trace_id: non-empty str
    if not isinstance(resp["trace_id"], str) or not resp["trace_id"].strip():
        raise ValueError("llm_response.trace_id must be non-empty str")

    # 9) version: int ≥ 0
    if not isinstance(resp["version"], int) or resp["version"] < 0:
        raise ValueError("llm_response.version must be int ≥ 0")

    # 10) generated_at: non-empty str
    if not isinstance(resp["generated_at"], str) or not resp["generated_at"].strip():
        raise ValueError("llm_response.generated_at must be non-empty str")

    # 11) 信息边界：forbidden 关键字 0 命中（递归扫描整个 resp）
    forbidden = _forbidden_flat_hits(resp)
    if forbidden:
        details = "; ".join(f"[{w}] {p}::{s}" for (w, p, s) in forbidden)
        raise ValueError(f"llm_response 命中 forbidden keywords: {details}")

    # 12) trace_id 正文隔离：text 中不得包含 trace_id 原文
    tid = resp["trace_id"]
    if isinstance(tid, str) and len(tid) >= 4 and tid in resp["text"]:
        raise ValueError(
            f"llm_response.text 中不应包含 trace_id 原文（避免泄漏审计信息）；trace_id={tid!r}"
        )

    # 13) 机器字段名隔离：text 中不得出现机器字段名（独立 token）
    machine_hits = _machine_field_hits(resp["text"])
    if machine_hits:
        details = "; ".join(f"[{n}] near: {s}" for (n, s) in machine_hits)
        raise ValueError(f"llm_response.text 中出现机器字段名: {details}")
