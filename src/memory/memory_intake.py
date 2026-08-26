# -*- coding: utf-8 -*-
"""
src/memory/memory_intake.py

Phase B0 — Memory Intake Layer（记忆入口治理·唯一写入管线）

统一管线： sanitize → classify → truncate → guard → store

对外接口：
    sanitize_memory_content(content) -> (clean_text, stripped_flags)
        只剥离「完整宿主模板」（宿主注入块：open/close 成对 + BEGIN/END 标记齐全）。
        用户自己写的纯文本提及标签（如 "我看到<RAG-Faiss-Memory>这个标签"）
        不会被删除。

    truncate_memory_content(content, max_len=4000, head_keep=2500, tail_keep=800) -> str
        与 Orchestrator._normalize_memory_content 逐字节一致：
        head + [中间内容省略] + tail + [original_length=N]。

    classify_source(record, writer=..., stripped_flags=...) -> record
        仅经既有 metadata dict 补充污染来源字段（contaminated / contamination_type /
        stripped_flags / detected_at / source_path）。无污染时不添加任何字段。

约束（v1.4 Architecture Lock §2，不可违反）：
    - 不改 Memory schema；不新增第二套 store；不删除历史数据
    - 不触碰 Temporal / Goal / Growth / Initiative 链路
    - 保持 v1.3 已验证行为兼容（system_reminder 剥离语义与 pollution_guard 完全一致）
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 宿主模板正则（单一事实来源；pollution_guard 也从这里引用）
# ============================================================

# system_reminder：与 v1.3 pollution_guard 既有语义完全一致
SYSTEM_REMINDER_BLOCK_RE = re.compile(
    r"<system_reminder[^>]*>.*?</system_reminder>",
    re.IGNORECASE | re.DOTALL,
)
SYSTEM_REMINDER_OPEN_RE = re.compile(r"<system_reminder[^>]*>", re.IGNORECASE)
SYSTEM_REMINDER_CLOSE_RE = re.compile(r"</system_reminder>", re.IGNORECASE)

# RAG 宿主块：必须 open/close 成对 + BEGIN/END 标记齐全才算宿主模板。
# 实测宿主格式（.diag/download/default_memory.json 实证）：
#   <RAG-Faiss-Memory>
#   --- BEGIN HISTORICAL MEMORY REFERENCE ---
#   ...
#   --- END REMINDER ---
#   </RAG-Faiss-Memory>
# 兼容旧变体：END 标记为 --- END HISTORICAL MEMORY REFERENCE ---。
RAG_HOST_BLOCK_RE = re.compile(
    r"<RAG-Faiss-Memory>\s*---\s*BEGIN HISTORICAL MEMORY REFERENCE\s*---"
    r".*?"
    r"---\s*END (?:REMINDER|HISTORICAL MEMORY REFERENCE)\s*---\s*"
    r"</RAG-Faiss-Memory>",
    re.IGNORECASE | re.DOTALL,
)
# 嵌套/配对剥离后的孤儿残片（缺 open 的 END+close、缺 close 的 open+BEGIN）：
# 仅清理完整标记序列，不碰纯标签提及。
RAG_ORPHAN_TAIL_RE = re.compile(
    r"---\s*END (?:REMINDER|HISTORICAL MEMORY REFERENCE)\s*---\s*</RAG-Faiss-Memory>",
    re.IGNORECASE,
)
RAG_ORPHAN_HEAD_RE = re.compile(
    r"<RAG-Faiss-Memory>\s*---\s*BEGIN HISTORICAL MEMORY REFERENCE\s*---",
    re.IGNORECASE,
)

# extra_instruction：完整开闭标签对（单开标签不删，保护用户文本）
EXTRA_INSTRUCTION_BLOCK_RE = re.compile(
    r"<extra_instruction[^>]*>.*?</extra_instruction>",
    re.IGNORECASE | re.DOTALL,
)

# 默认截断参数（与 Orchestrator 类常量一致；max_len 默认 4000 对齐 guard 上限）
DEFAULT_MAX_LENGTH: int = 4000
DEFAULT_HEAD_KEEP: int = 2500
DEFAULT_TAIL_KEEP: int = 800

# ============================================================
# sanitize
# ============================================================


def sanitize_memory_content(content: object) -> Tuple[str, List[str]]:
    """剥离完整宿主模板，返回 (干净文本, stripped_flags)。

    剥离规则（只删「完整宿主模板」，不删用户文本）：
    1. <system_reminder> 完整块 + 残留孤立标签（v1.3 既有语义）
    2. <RAG-Faiss-Memory> 完整宿主块（open/close + BEGIN/END 齐全）
       嵌套时循环剥离 + 孤儿标记残片清理，保证零嵌套残留
    3. <extra_instruction> 完整开闭对

    stripped_flags 取值（去重、固定顺序）：
        "system_reminder" / "rag_block" / "extra_instruction"

    Args:
        content: 原始内容（str / None / 其他类型）

    Returns:
        (clean_text: str, stripped_flags: List[str])；干净输入返回 flags=[]
    """
    if content is None:
        return "", []
    if not isinstance(content, str):
        content = str(content)

    flags: List[str] = []
    text = content

    # 1) system_reminder（与 v1.3 pollution_guard.sanitize_content 完全一致）
    if SYSTEM_REMINDER_OPEN_RE.search(text) or SYSTEM_REMINDER_CLOSE_RE.search(text):
        _before = text
        text = SYSTEM_REMINDER_BLOCK_RE.sub("", text)
        text = SYSTEM_REMINDER_OPEN_RE.sub("", text)
        text = SYSTEM_REMINDER_CLOSE_RE.sub("", text)
        if text != _before:
            flags.append("system_reminder")

    # 2) RAG 宿主块：循环剥离（处理嵌套/兄弟块），再清孤儿残片
    _before = text
    _prev = None
    while _prev != text:
        _prev = text
        text = RAG_HOST_BLOCK_RE.sub("", text)
    text = RAG_ORPHAN_TAIL_RE.sub("", text)
    text = RAG_ORPHAN_HEAD_RE.sub("", text)
    if text != _before:
        flags.append("rag_block")

    # 3) extra_instruction 完整开闭对
    _before = text
    text = EXTRA_INSTRUCTION_BLOCK_RE.sub("", text)
    if text != _before:
        flags.append("extra_instruction")

    return text.strip(), flags


# ============================================================
# truncate
# ============================================================


def truncate_memory_content(
    content: object,
    max_len: int = DEFAULT_MAX_LENGTH,
    head_keep: int = DEFAULT_HEAD_KEEP,
    tail_keep: int = DEFAULT_TAIL_KEEP,
) -> str:
    """规整记忆内容，保证输出长度 <= max_len。

    与 Orchestrator._normalize_memory_content 算法逐字节一致
    （head + 省略标记 + tail + [original_length=N]），仅常量参数化。

    Args:
        content: 原始内容（str / None / 其他类型）
        max_len: 长度上限（默认 4000，对齐 PollutionGuard）
        head_keep: 头部保留字符数
        tail_keep: 尾部保留字符数

    Returns:
        规整后的字符串，长度 <= max_len
    """
    # 1) 空 / None → 空字符串
    if content is None:
        return ""

    # 2) 非字符串 → 安全转字符串
    if not isinstance(content, str):
        try:
            content = str(content)
        except Exception:
            return ""

    # 3) 去掉首尾空白（保留中间换行）
    normalized = content.strip()
    if not normalized:
        return ""

    original_len = len(normalized)

    # 4) 长度达标 → 直接返回
    if original_len <= max_len:
        return normalized

    # 5) 超长 → head + [中间内容省略] + tail + 原始长度标记
    head_end = head_keep
    tail_start = max(head_end, original_len - tail_keep)

    # head 和 tail 有重叠时退化：直接截断 head
    if tail_start <= head_end:
        truncated = normalized[: max_len - 40]
        return truncated + "\n\n[内容已截断]" + f"\n[original_length={original_len}]"

    head = normalized[:head_end]
    tail = normalized[tail_start:]

    marker = (
        "\n\n[中间内容省略]\n\n"
        + tail
        + f"\n[original_length={original_len}]"
    )

    # 再次兜底：确保最终长度 <= max_len
    result = head + marker
    if len(result) > max_len:
        # 再截一次 head 留足空间给 marker
        overflow = len(result) - max_len
        safe_head_len = max(50, head_end - overflow - 50)
        result = (
            normalized[:safe_head_len]
            + "\n\n[中间内容省略]\n\n"
            + tail
            + f"\n[original_length={original_len}]"
        )
        # 最后保险：硬截断到 max_len
        if len(result) > max_len:
            result = result[:max_len]

    return result


# ============================================================
# classify
# ============================================================

_FLAG_TO_TYPE: Dict[str, str] = {
    "rag_block": "rag_injection",
    "system_reminder": "system_reminder_injection",
    "extra_instruction": "extra_instruction_injection",
}


def classify_source(
    record: Dict[str, Any],
    writer: str = "unknown",
    stripped_flags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """按写入入口 + 净化结果补充污染来源元数据（只经既有 metadata dict）。

    - 有 stripped_flags 时添加：
        contaminated=True
        contamination_type（如 "rag_injection"）
        stripped_flags（原样列表）
        detected_at（ISO-UTC）
        source_path=writer（写入入口标识）
    - 无污染时：原样返回，不添加任何字段（干净记录零改动）。
    - 既有 metadata 字段（provenance / memory_type 等）一律保留不覆盖。

    Args:
        record: 待写入的 memory record（dict）
        writer: 写入入口标识（"orchestrator" / "runtime_pipeline" / ...）
        stripped_flags: sanitize_memory_content 返回的 flags

    Returns:
        补充元数据后的 record（同一 dict 对象）
    """
    if not stripped_flags:
        return record

    md = record.get("metadata")
    md = dict(md) if isinstance(md, dict) else {}
    types = [_FLAG_TO_TYPE.get(f, str(f)) for f in stripped_flags]
    md["contaminated"] = True
    md["contamination_type"] = "+".join(types)
    md["stripped_flags"] = list(stripped_flags)
    md["detected_at"] = datetime.now(timezone.utc).isoformat()
    md["source_path"] = str(writer)
    record["metadata"] = md
    return record


# ============ M1-3 纯规则 importance scorer（v1.6.0） ============
# 原则（任务书 §七）：
#   importance = 记忆本身的长期价值（写入时评估一次）；
#   retrieval score = 当前 query 是否应召回（selection 层决定，二者分离）。
# 纯规则、零 LLM、零 IO；不读存储、不写存储；只读 content/metadata。
# 无命中时回落 0.5（与原行为一致），保证零风险向后兼容。

#: 类别 → 基础 importance（可配置覆盖 memory.importance_category_*）
_DEFAULT_CATEGORY_IMPORTANCE: Dict[str, float] = {
    "milestone": 0.85,        # 人生里程碑/重大事件
    "relationship": 0.80,     # 关系相关内容
    "preference": 0.75,       # 长期偏好
    "fact": 0.70,             # 用户事实/约定
    "goal": 0.70,             # 项目/长期目标
    "emotion": 0.65,          # 明显情绪事件
    "info": 0.55,             # 普通信息分享
    "chitchat": 0.45,         # 普通闲聊
    "noise": 0.35,            # 极低信息量
}

#: 规则信号 → 命中则覆盖/叠加类别（按优先级）
_IMPORTANCE_RULES: Tuple[Tuple[Pattern[str], str], ...] = (
    # 具体模式优先（preference 的"喜欢+对象"先于 relationship 的泛"喜欢"；
    # 对象至少 1 个非人称字符，避免"喜欢你"被误判为偏好）
    (re.compile(r"喜欢(?!你|他|她|我|你们|我们).{1,6}|最爱|最讨厌", re.I), "preference"),
    (re.compile(r"爱|喜欢|想念|在一起|永远|约定|承诺|亲亲|抱抱", re.I), "relationship"),
    (re.compile(r"雪原|钻石|下界|附魔|MC|Minecraft|我的世界|捡到|建造|合成", re.I), "milestone"),
    (re.compile(r"生日|结婚|毕业|入职|第一次|我们结婚|我们永远", re.I), "milestone"),
    (re.compile(r"想要|希望|偏好|打算|计划做", re.I), "preference"),
    (re.compile(r"目标|计划|项目|开发|上线|版本|需求|测试|部署|系统|长期|愿景", re.I), "goal"),
    (re.compile(r"开心|难过|生气|伤心|害怕|紧张|期待|委屈|哭|笑|崩溃|压力", re.I), "emotion"),
    (re.compile(r"我是|我叫|我是谁|你是|我们认识|记得我|我叫什么", re.I), "fact"),
)

#: 极低信息量模式（聊天噪音）
_NOISE_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(r"^(好的|好|嗯|哦|啊|哈哈|嘿嘿|懂了|收到|没事|没关系|没事儿|还行|ok|OK|好呀|好嘞|行|可以|对|是|不是|不知道|谢谢|不用谢|再见|晚安|早上好|中午好|晚上好|测试|test|abc|hi|hello)[\s。！？!?.，,]*$", re.I),
    re.compile(r"^.{0,2}$"),  # 超短消息（1-2 字符）
)


def score_importance(
    content: str,
    memory_type: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    emotion_tag: Optional[str] = None,
) -> float:
    """M1-3 纯规则 importance scorer。

    输入：content（必）、memory_type / metadata / emotion_tag（可选，已有则用）。
    输出：0.35 ~ 0.85 的 importance 值；默认 0.5（无任何信号）。

    规则优先级：
      1. 极低信息量（噪音模式）→ 0.35；
      2. 类别信号（里程碑/关系/偏好/事实/目标/情绪）→ 对应基础值；
      3. 无明显信号 → 按 memory_type 提升（milestone/fact/relationship 类）或 0.5。
    不硬编码大量关键词（每类 ≤8 个），不调用 LLM，不读存储。
    """
    text = str(content or "").strip()
    if not text:
        return 0.5

    # 1) 噪音 → 0.35
    for pat in _NOISE_PATTERNS:
        if pat.search(text):
            return _DEFAULT_CATEGORY_IMPORTANCE["noise"]

    # 2) 类别信号（按优先级第一个命中）
    for pat, cat in _IMPORTANCE_RULES:
        if pat.search(text):
            return _DEFAULT_CATEGORY_IMPORTANCE[cat]

    # 3) memory_type 兜底（milestone/fact/relationship 类提升）
    mt = str(memory_type or "").lower()
    if mt in ("user_milestone", "milestone"):
        return _DEFAULT_CATEGORY_IMPORTANCE["milestone"]
    if mt in ("user_fact", "fact"):
        return _DEFAULT_CATEGORY_IMPORTANCE["fact"]
    if mt in ("user_relationship", "relationship"):
        return _DEFAULT_CATEGORY_IMPORTANCE["relationship"]
    if mt in ("user_shared", "shared"):
        return _DEFAULT_CATEGORY_IMPORTANCE["info"]

    # 4) 默认 0.5（与原行为一致）
    return 0.5


__all__ = ["sanitize_memory_content", "truncate_memory_content", "classify_source", "score_importance"]
