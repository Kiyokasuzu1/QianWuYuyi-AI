# -*- coding: utf-8 -*-
"""
src/memory/pollution_guard.py

Phase C.2.3 — Memory 防污染守卫

职责:
- 在 MemoryStore.add() 入口处检查待写入记录
- 拒绝以下类型的污染数据:
  * runtime_experience (runtime 内部经验)
  * system_prompt / system_reminder / system_meta
  * internal_reasoning / ai_thought / ai_reflection
  * debug / tool_call / reflection_process
  * role=system / role=tool / role=function
  * content 含提示注入标签 (<system_reminder> / <extra_instruction> / [RuntimeExperience])
  * 缺 role / 缺 content / 缺 memory_type
  * 长度超限(> 4000 字符)

通过白名单 + 硬黑名单 + 内容启发式 三层防护,确保 memory.json 保持干净。

约束:
- 纯函数,无副作用(可独立测试)
- 不修改现有 MemoryStore 业务逻辑
- 仅作为 add() 的前置过滤层
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

# ============================================================
# 规则配置
# ============================================================

# 硬黑名单:明确禁止保存的 memory_type
FORBIDDEN_TYPES: frozenset = frozenset({
    "runtime_experience",
    "system_prompt",
    "system_reminder",
    "system_meta",
    "system",
    "internal_reasoning",
    "ai_thought",
    "ai_reflection",
    "ai_internal",
    "ai_self_talk",
    "ai_prompt",
    "ai_scratchpad",
    "ai_planning",
    "debug",
    "tool_call",
    "tool_result",
    "reflection_process",
    "reflection",
    "test",
    "init",
})

# 白名单:允许保存的 memory_type(只允许这 11 种)
ALLOWED_TYPES: frozenset = frozenset({
    "user_fact",
    "user_preference",
    "user_event",
    "user_experience",
    "user_milestone",
    "user_emotion",
    "user_goal",
    "user_relationship",
    "user_shared",
    "relationship_event",
    "important_experience",
})

# 允许的 role
ALLOWED_ROLES: frozenset = frozenset({
    "user",
    "human",
})

# 禁止的 role(系统/AI 内部角色)
FORBIDDEN_ROLES: frozenset = frozenset({
    "system",
    "assistant",
    "tool",
    "function",
    "ai",
    "model",
})

# 提示注入 / 系统消息特征
INJECTION_PATTERNS: tuple = (
    re.compile(r"<system_reminder[\s>]", re.IGNORECASE),
    re.compile(r"<system_prompt[\s>]", re.IGNORECASE),
    re.compile(r"<extra_instruction[\s>]", re.IGNORECASE),
    re.compile(r"<\|[a-z_]+\|>", re.IGNORECASE),  # <|function_call|>
    re.compile(r"\[RuntimeExperience\]", re.IGNORECASE),
    re.compile(r"\[InternalReasoning\]", re.IGNORECASE),
    re.compile(r"\[Debug\]", re.IGNORECASE),
    re.compile(r"^SYSTEM:\s", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^ASSISTANT:\s", re.IGNORECASE | re.MULTILINE),
)

# 长度上限
MAX_CONTENT_LENGTH = 4000

# ============================================================
# Phase C.2.3.1: 兼容性白名单
# ============================================================
# 已通过 MemoryVerifier 验证的 memory_class 视为可信
# 保留向后兼容,避免破坏 Phase 4.3 时代编写的测试/调用
VERIFIED_MEMORY_CLASSES: frozenset = frozenset({
    "preference",         # 用户偏好
    "user_statement",     # 用户陈述
    "instruction",        # 用户指令
    "growth_memory",      # 成长记忆
    "fact",               # 用户事实(扩展)
    "emotion",            # 用户情绪
    "experience",         # 用户经历
    "event",              # 事件
    "milestone",          # 里程碑
    "goal",               # 目标
    "relationship",       # 关系
    "shared",             # 分享
    "identity",           # Phase C.2.3: 用户身份（"我叫X"/"我是X"）
    "source_document",    # Phase C.2.3: 长文本档案
})

# ============================================================
# 主入口
# ============================================================


def _extract_type(memory: Dict[str, Any]) -> str:
    """从 memory 中提取 memory_type。"""
    md = memory.get("metadata") or {}
    if not isinstance(md, dict):
        md = {}
    t = (
        md.get("memory_type")
        or memory.get("memory_type")
        or md.get("type")
        or memory.get("type")
        or ""
    )
    return str(t).lower().strip()


def _extract_role(memory: Dict[str, Any]) -> str:
    return str(memory.get("role", "") or "").strip().lower()


def _extract_content(memory: Dict[str, Any]) -> str:
    c = memory.get("content")
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    return str(c)


def check(memory: Dict[str, Any]) -> Tuple[bool, str]:
    """
    检查 memory 是否允许写入。

    Returns:
        (allowed: bool, reason: str)
        - allowed=True  → 允许写入,reason="ok"
        - allowed=False → 拒绝写入,reason 说明拒绝原因

    拒绝优先级(按顺序检查):
    1. 不是 dict
    2. content 为空 / 缺失
    3. content 超长
    4. 提示注入 / 系统消息特征
    5. role 黑名单(system/assistant/tool/function/ai/model)
    6. role 缺失(没明确说"是用户说的")
    7. memory_type 黑名单
    8. memory_type 缺失或不在白名单
    """
    # 1. 类型校验
    if not isinstance(memory, dict):
        return (False, "not_a_dict")

    content = _extract_content(memory).strip()

    # 2. content 不能为空
    if not content:
        return (False, "empty_content")

    # 3. 长度限制
    if len(content) > MAX_CONTENT_LENGTH:
        return (False, f"content_too_long:{len(content)}>{MAX_CONTENT_LENGTH}")

    # 4. 提示注入 / 系统消息特征
    for pat in INJECTION_PATTERNS:
        if pat.search(content):
            return (False, f"injection_detected:{pat.pattern}")

    role = _extract_role(memory)

    # 5. role 黑名单
    if role in FORBIDDEN_ROLES:
        return (False, f"forbidden_role:{role}")

    # 6. role 必须存在(防止无 role 的"无主"记录)
    if not role:
        return (False, "empty_role")

    # role 白名单校验
    if role not in ALLOWED_ROLES:
        return (False, f"role_not_in_whitelist:{role}")

    # 7. memory_type 黑名单
    mtype = _extract_type(memory)
    if mtype in FORBIDDEN_TYPES:
        return (False, f"forbidden_type:{mtype}")

    # 8. memory_type 必须在白名单
    if not mtype:
        # Phase C.2.3.1: 向后兼容 — 若带 memory_class(由 MemoryVerifier 标注),放行
        mclass = memory.get("memory_class", "")
        if mclass and mclass in VERIFIED_MEMORY_CLASSES:
            return (True, "ok_verified_via_memory_class")
        return (False, "empty_memory_type")
    if mtype not in ALLOWED_TYPES:
        return (False, f"type_not_in_whitelist:{mtype}")

    return (True, "ok")


def is_allowed(memory: Dict[str, Any]) -> bool:
    """便捷接口:仅返回布尔值。"""
    allowed, _ = check(memory)
    return allowed


def get_reason(memory: Dict[str, Any]) -> Optional[str]:
    """便捷接口:仅返回拒绝原因(若允许则返回 None)。"""
    allowed, reason = check(memory)
    return None if allowed else reason


# ============================================================
# Phase 1: 写入前清洗接口
# ============================================================
# 职责边界(与 check() 严格分离):
# - 只负责写入前处理,不参与任何检测/拒绝判断
# - 只清理 system_reminder 标签(支持属性/多行/大小写)
# - 不处理 system_prompt / extra_instruction / RuntimeExperience 等
#   其它 INJECTION_PATTERNS——那些仍由 check() 检测拒绝

_SYSTEM_REMINDER_BLOCK_RE = re.compile(
    r"<system_reminder[^>]*>.*?</system_reminder>",
    re.IGNORECASE | re.DOTALL,
)
_SYSTEM_REMINDER_OPEN_RE = re.compile(r"<system_reminder[^>]*>", re.IGNORECASE)
_SYSTEM_REMINDER_CLOSE_RE = re.compile(r"</system_reminder>", re.IGNORECASE)


def sanitize_content(content: object) -> str:
    """写入前清洗:移除 <system_reminder> 注入块,返回干净文本。

    处理步骤:
    1. None -> ""
    2. 非字符串 -> str()
    3. 删除完整注入块 <system_reminder>...</system_reminder>
    4. 删除残留孤立标签 <system_reminder> / </system_reminder>
    5. strip()

    Args:
        content: 原始内容(object)

    Returns:
        清洗后的字符串(可能为空字符串)
    """
    if content is None:
        return ""
    if not isinstance(content, str):
        content = str(content)

    text = _SYSTEM_REMINDER_BLOCK_RE.sub("", content)
    text = _SYSTEM_REMINDER_OPEN_RE.sub("", text)
    text = _SYSTEM_REMINDER_CLOSE_RE.sub("", text)
    return text.strip()


# ============================================================
# 诊断接口
# ============================================================

def stats() -> Dict[str, Any]:
    """返回当前规则的统计信息(用于 Dashboard / 监控)。"""
    return {
        "forbidden_types_count": len(FORBIDDEN_TYPES),
        "allowed_types_count": len(ALLOWED_TYPES),
        "forbidden_roles_count": len(FORBIDDEN_ROLES),
        "allowed_roles_count": len(ALLOWED_ROLES),
        "injection_patterns_count": len(INJECTION_PATTERNS),
        "max_content_length": MAX_CONTENT_LENGTH,
    }
