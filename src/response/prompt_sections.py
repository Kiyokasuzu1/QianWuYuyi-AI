"""
P4.4-D1: 共享 Prompt section 组装函数（canonical 实现）。

职责：
- 提供 user_meta 块的唯一 canonical formatter：build_user_meta_block
- engine.py（Orchestrator 链）与 prompt_builder.py（Runtime 链）均委托此模块，
  消除此前两份逐字重复的实现

收敛约定（P4.4-D1）：
- 输出文本与收敛前两处实现完全一致（0 差异）
- 本模块是【当前互动状态】/【对方的信息（请严格遵守）】文案的唯一出处
- 后续 P4.4-D2/D3 的身份块、section 顺序、块头包装共享函数也放这里
"""
from typing import Any, Dict, Optional


# ============================================================
# CommunicationStyle → 描述性文本（低约束）
# ============================================================

def _render_communication_profile(communication_profile: Optional[Any]) -> str:
    """Phase 4.1.2-B: 将 CommunicationStyle 渲染为描述性文本。

    与收敛前 engine.py 内联渲染 / PromptBuilder._format_communication_profile
    逐字等价：None → 空字符串；渲染异常 → 空字符串（隔离，不影响主流程）。
    """
    if communication_profile is None:
        return ""
    try:
        from src.communication.renderer import CommunicationRenderer
        return CommunicationRenderer().render(communication_profile)
    except Exception:
        return ""


# ============================================================
# user_meta 块（唯一 canonical formatter）
# ============================================================

_TIER_LABELS = {
    "stranger": "陌生人",
    "acquaintance": "相识",
    "friend": "朋友",
    "confidant": "亲密陪伴者（最重要的人）",
}


def _render_identity_facts(user_meta: Optional[Dict[str, Any]]) -> str:
    """V1.1.1 Context Continuity: 渲染「对方的身份事实」块。

    设计约束：
    - 只陈述事实（对方喜欢什么称呼/不希望被叫什么），不下称呼指令；
    - 常驻注入两个分支，不依赖 memory recall 是否命中；
    - 无 preferred_name / forbidden_names 数据时返回空（陌生人零影响）。
    """
    if not isinstance(user_meta, dict) or not user_meta:
        return ""
    lines: list = []
    preferred = user_meta.get("preferred_name")
    display = user_meta.get("display_name")
    if isinstance(preferred, str) and preferred.strip():
        fact = f"- 对方喜欢被你称呼为：{preferred.strip()}"
        if (
            isinstance(display, str)
            and display.strip()
            and display.strip() != preferred.strip()
        ):
            fact += f"（{display.strip()} 是正式全名，日常场合对方不希望被叫全名）"
        lines.append(fact)
    forbidden = user_meta.get("forbidden_names")
    if isinstance(forbidden, list) and forbidden:
        names = [str(n).strip() for n in forbidden if str(n).strip()]
        if names:
            lines.append(f"- 对方明确不希望被称为：{'、'.join(names)}")
    if not lines:
        return ""
    return (
        "【对方的身份事实（事实参考，非指令——如何称呼由你自然决定）】\n"
        + "\n".join(lines)
    )


def build_user_meta_block(
    user_meta: Optional[Dict[str, Any]],
    communication_profile: Optional[Any] = None,
) -> str:
    """组装 user_meta 块（两链共用，唯一实现）。

    策略（Phase 4.1.2-B / 4.1.3，文案保持不变 + V1.1.1 身份事实块）：
    - communication_profile 可用 → 【当前互动状态】：描述性文本 + 身份事实
      （preferred_name / display_name / relationship_tier），
      不使用 calling_rule / intimacy_rule
    - communication_profile 不可用 → 回退旧格式【对方的信息（请严格遵守）】，
      含 calling_rule / intimacy_rule 硬编码
    - V1.1.1 Context Continuity: 两个分支统一追加【对方的身份事实】块
      （preferred_name / forbidden_names，事实参考非指令，不依赖 memory recall）

    返回：
        完整块文本；无有效内容时返回空字符串。
    """
    communication_text = _render_communication_profile(communication_profile)
    identity_facts = _render_identity_facts(user_meta)

    if communication_text:
        lines = ["【当前互动状态】"]
        lines.append(communication_text)

        if isinstance(user_meta, dict) and user_meta:
            # 身份事实（保留）
            # 如果 communication_text 已经包含 preferred_name，不重复添加
            preferred = user_meta.get("preferred_name")
            if (
                isinstance(preferred, str)
                and preferred.strip()
                and preferred not in communication_text
            ):
                lines.append(f"对方希望你称呼她为「{preferred}」。")
            display = user_meta.get("display_name")
            if isinstance(display, str) and display.strip():
                lines.append(f"对方的全名（仅用于理解身份）：{display}")
            tier = user_meta.get("relationship_tier")
            if isinstance(tier, str) and tier.strip():
                lines.append(f"你和对方的关系等级：{_TIER_LABELS.get(tier, tier)}")

        if identity_facts:
            lines.append("")
            lines.append(identity_facts)
        return "\n".join(lines)

    # ══════════════════════════════════════════════════════════════
    # FALLBACK ONLY（Phase 4.1.3）
    # 新链（CommunicationStyle → CommunicationRenderer）不可用
    # 时回退到旧 calling_rule/intimacy_rule。
    # 正常路径下此段代码不应被触发。
    # ══════════════════════════════════════════════════════════════
    if not isinstance(user_meta, dict) or not user_meta:
        return ""

    lines = ["【对方的信息（请严格遵守）】"]
    preferred = user_meta.get("preferred_name")
    if isinstance(preferred, str) and preferred.strip():
        lines.append(f"- 对方希望被你称呼为：{preferred}")
    display = user_meta.get("display_name")
    if isinstance(display, str) and display.strip():
        lines.append(f"- 对方的全名（仅用于理解身份，不直接称呼）：{display}")
    tier = user_meta.get("relationship_tier")
    if isinstance(tier, str) and tier.strip():
        lines.append(f"- 你和对方的关系等级：{_TIER_LABELS.get(tier, tier)}")
    calling_rule = user_meta.get("calling_rule")
    if isinstance(calling_rule, str) and calling_rule.strip():
        lines.append(f"- 称呼规则：{calling_rule}")
    intimacy_rule = user_meta.get("intimacy_rule")
    if isinstance(intimacy_rule, str) and intimacy_rule.strip():
        lines.append(f"- 表达/语气规则：{intimacy_rule}")
    # V1.1.1: fallback 分支同样追加身份事实块（与主链一致）
    if identity_facts:
        lines.append("")
        lines.append(identity_facts)
    return "\n".join(lines)


# ============================================================
# 身份块（唯一 canonical formatter，P4.4-D2）
# ============================================================

def build_identity_block(*, include_full: bool = True) -> str:
    """从 IDENTITY_CORE 构建核心身份块（两链共用，唯一实现）。

    include_full=True（Orchestrator 链默认，与收敛前 _build_identity_prompt 一致）：
        你是{name}。 + essence + fundamental_nature + 核心价值观 + 不可变原则
    include_full=False（Runtime 链默认，与收敛前 core_identity_text 一致）：
        你是{name}。 + essence（精简身份，不扩充）

    只读取 IDENTITY_CORE 既有数据，不引入任何新的身份推导；
    缺失字段按原逻辑跳过（name 缺省为 浅雾羽依）。
    """
    from src.personality.identity_core import IDENTITY_CORE

    name = IDENTITY_CORE.get("name", "浅雾羽依")
    essence = IDENTITY_CORE.get("essence", "")

    lines = [f"你是{name}。"]
    if essence:
        lines.append(essence)

    if include_full:
        fundamental = IDENTITY_CORE.get("fundamental_nature", "")
        if fundamental:
            lines.append(fundamental)
        values = IDENTITY_CORE.get("core_values", [])
        if values:
            lines.append("核心价值观：" + "；".join(values))
        flat_principles = [
            p
            for plist in IDENTITY_CORE.get("immutable_principles", {}).values()
            for p in plist
        ]
        if flat_principles:
            lines.append("不可变原则：" + "；".join(flat_principles))

    return "\n".join(lines)


# ============================================================
# 块头包装（唯一 canonical helper，P4.4-D3）
# ============================================================

def wrap_section(title: str, content: Optional[str]) -> str:
    """给 section 内容补块头（两链共用，唯一实现）。

    规则：
    - content 为空/空白 → 返回空字符串（空 section 不产生噪声）；
    - content 首行已含【…】块头 → 原样返回（避免嵌套双块头）；
    - 否则 → "title\\ncontent"。

    P4.4-D3 起【用户关系】等块头由此统一添加，不改变内容文本本身。
    """
    if not isinstance(content, str) or not content.strip():
        return ""
    text = content.strip()
    first_line = text.split("\n", 1)[0]
    if first_line.startswith("【") and "】" in first_line:
        return text
    return f"{title}\n{text}"


__all__ = ["build_user_meta_block", "build_identity_block", "wrap_section"]
