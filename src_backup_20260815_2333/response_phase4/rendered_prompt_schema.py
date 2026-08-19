"""
Phase 4.0 — R2.7.1-B1 RenderedPrompt Contract（schema + validator + empty factory）

冻结 RenderedPrompt 的对外结构：
  system_message: str          — 整段 system prompt（含身份 + 成长摘要 + 连续性 + 反思），禁止内部术语
  self_description: str        — "我是谁" 的纯描述文本（来自 SelfModel identity_view + personality_summary）
  memory_section: str          — 记忆/近期话题段（来自 PromptContext memory_block 或 context_memories 渲染）
  relationship_section: str    — 关系段（来自 PromptContext.relationship_block + relationship_snapshot）
  task_instruction: str        — 当前轮任务说明（来自 PromptContext.task_info + task_block）
  trace_id: str                — 本认知循环 trace_id，用于可审计（**不在自然语言正文中出现**，只在 Prompt 顶部以注释或元信息传递，Gates 会强制 system_message 不含 trace_id）

严格信息边界（来自 RL-2）：
  FORBIDDEN_KEYS（出现在 key 名时）：proposal_id / approval_reason / internal_score / evaluator_meta / confidence
  FORBIDDEN_VALUE_TOKEN（出现在值中时）：proposal_id / approval_reason / internal_score / evaluator_meta / confidence / trait_delta（\b 正则）
  RenderedPrompt 本身（6 个字段值）扫描通过；validator 在 validate_rendered_prompt_shape 中抛 ValueError。

设计原则（不跨红线）：
  - 只做 schema 校验；不读取/修改任何 src 内部组件状态
  - 所有字段皆字符串或基础类型，不包含嵌套对象（避免泄漏内部结构化字段）
  - assigned_version：每次渲染严格 +1，与 SelfContext/PromptContext 的版本链语义一致
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Tuple

VERSIONED_FIELDS = (
    "system_message",
    "self_description",
    "memory_section",
    "relationship_section",
    "task_instruction",
    "trace_id",
)


def _forbidden_flat_hits(obj: Any) -> List[Tuple[str, str, str]]:
    """RL-2 forbidden 扫描：返回 (forbidden_word, path, snippet)。

    - 键名 ∈ KEY_FORBIDDEN → 直接命中
    - 字符串值中：KEY_FORBIDDEN 子串（case-insensitive）直接命中
    - "trait_delta"：按独立 token 语义正则 \\btrait_delta\\b（case-insensitive）命中
    - 其他基础类型（int/float/bool）不扫描（避免数字本身意外匹配）
    """
    KEY_FORBIDDEN = frozenset(
        ["proposal_id", "approval_reason", "internal_score", "evaluator_meta", "confidence"]
    )
    TRAIT_DELTA_RE = re.compile(r"(?<![a-zA-Z0-9_])trait_delta(?![a-zA-Z0-9_])", re.IGNORECASE)
    hits: List[Tuple[str, str, str]] = []

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
            for w in KEY_FORBIDDEN:
                if w in s_low:
                    hits.append((w, path, o[:200]))
            if TRAIT_DELTA_RE.search(o):
                hits.append(("trait_delta", path, o[:200]))
            return
        # None / int / float / bool 安全跳过

    walk(obj, "root")
    return hits


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def create_empty_rendered_prompt(
    *,
    trace_id: str = "trace_empty",
    assigned_version: int = 0,
    generated_at: str = "1970-01-01T00:00:00+00:00",
) -> Dict[str, Any]:
    """空工厂：字段全为空串；用于 Renderer build 失败时回退。"""
    return {
        "version": assigned_version if isinstance(assigned_version, int) and assigned_version >= 0 else 0,
        "generated_at": generated_at,
        "trace_id": trace_id if trace_id else "trace_empty",
        "system_message": "",
        "self_description": "",
        "memory_section": "",
        "relationship_section": "",
        "task_instruction": "",
    }


def validate_rendered_prompt_shape(rp: Any) -> None:
    """严格校验 RenderedPrompt 结构 + 信息边界。失败抛 ValueError（带路径）。"""
    if not isinstance(rp, dict):
        raise ValueError("rendered_prompt must be a dict")

    # 1) 顶层必备字段
    for req in ("version", "generated_at", "trace_id", *VERSIONED_FIELDS):
        if req not in rp:
            raise ValueError(f"rendered_prompt missing top-level key: {req}")

    # 2) version int ≥ 0
    if not isinstance(rp["version"], int) or rp["version"] < 0:
        raise ValueError("rendered_prompt.version must be int ≥ 0")

    # 3) generated_at / trace_id 非空串
    if not _is_nonempty_str(rp["generated_at"]):
        raise ValueError("rendered_prompt.generated_at must be non-empty str")
    if not _is_nonempty_str(rp["trace_id"]):
        raise ValueError("rendered_prompt.trace_id must be non-empty str")

    # 4) 6 个业务字段必须全部是字符串类型（允许 ""）
    for f in VERSIONED_FIELDS:
        if not isinstance(rp[f], str):
            raise ValueError(f"rendered_prompt[{f!r}] must be str")

    # 5) 信息边界：forbidden 关键字 0 命中
    forbidden = _forbidden_flat_hits(rp)
    if forbidden:
        details = "; ".join(f"[{w}] {p}::{s}" for (w, p, s) in forbidden)
        raise ValueError(f"rendered_prompt 命中 forbidden keywords: {details}")

    # 6) system_message / self_description 中不得直接包含 trace_id 字符串（禁止自然语言泄漏审计 ID）
    #    若出现，说明 Renderer 把 trace_id 拼进了对外语言 → 失败
    tid = rp["trace_id"]
    if isinstance(tid, str) and len(tid) >= 4:
        for field in ("system_message", "self_description", "task_instruction", "memory_section", "relationship_section"):
            if tid in rp[field]:
                raise ValueError(
                    f"rendered_prompt.{field} 中不应包含 trace_id 原文（避免泄漏审计信息）；"
                    f"trace_id={tid!r}"
                )

    # 7) 白名单字段：禁止额外顶层键（防止扩展字段名泄漏内部结构）
    allowed_top = {"version", "generated_at", "trace_id", *VERSIONED_FIELDS}
    for k in rp.keys():
        if k not in allowed_top:
            raise ValueError(f"rendered_prompt 禁止额外顶层字段 {k!r}（白名单={sorted(allowed_top)}）")
