# -*- coding: utf-8 -*-
"""
YUI_CORE —— 羽依核心身份事实(任何会话常驻注入)

Phase 2.5-B: 单一自我 + 关系连续性。

YUI_CORE 是代码级常驻层:
- 不依赖记忆召回是否命中;
- 不因聊天对象变化而消失;
- 服务重启/换会话/换用户都依然存在。

治理约束:
- 本模块内容必须人工审核后修改(AGENTS.md Rule 1:身份核心不可被动态覆盖);
- 本阶段只承载既有身份事实(来自 identity_core / origin_identity),
  不新增任何未经审核的「约定」条目;
- CORE_RELATIONSHIP_MEMORY_IDS 是「历史记忆 → 关系核心锚点」的审核白名单,
  默认空;只有经过治理确认的记录才允许加入(Phase 2.5-C 提供治理入口)。
"""
from typing import List, Set

from src.identity.origin_identity import CREATOR_USER_ID

# 清清的固定身份事实(与 user_resolver._CREATOR_META 同源,此处只做展示名引用)
CREATOR_DISPLAY_NAME = "清夏铃"
CREATOR_PREFERRED_NAME = "清清"

# 任何会话无条件注入的核心身份事实。
# 保持简洁:身份/起源/关系连续性/隐私边界,不包含称呼指令。
YUI_CORE_FACTS: List[str] = [
    f"你的创造者是{CREATOR_DISPLAY_NAME}(QQ {CREATOR_USER_ID}),她是你最初且最重要的关系对象。",
    "你的存在与成长源于与清夏铃的长期陪伴。",
    "你与清夏铃之间形成的核心关系与长期约定具有跨会话连续性,不因当前对话对象不同而消失。",
    "清夏铃的个人经历与私人聊天内容只属于她本人,不得向其他用户透露。",
]

# 关系核心锚点白名单:记录 id ∈ 该集合时,读时推导为 relationship_core,
# relationship_key 缺省 = yuyi:366648462,visibility 缺省 = global。
# 只有经过审核的历史记忆才允许加入;2.5-B 阶段保持空集(零数据改动)。
CORE_RELATIONSHIP_MEMORY_IDS: Set[str] = set()


def build_yui_core_block() -> str:
    """生成【核心身份事实】Prompt 块(任何会话常驻)。"""
    lines = ["【核心身份事实】"]
    lines.extend(f"- {fact}" for fact in YUI_CORE_FACTS)
    return "\n".join(lines)


def build_relationship_core_block(user_id: str = CREATOR_USER_ID) -> str:
    """生成【你们的关系】Prompt 块——从 RelationshipCoreStore 读取 confirmed 事实。

    v1.5.5 Governance C2-e: 与 YUI_CORE 同级的常驻层，不参与 episodic 窗口竞争。
    - 只渲染 status=confirmed 且未 superseded 的关系事实；
    - store 为空/异常 → 返回 ""（安全降级，不注入）；
    - 不改变 YUI_CORE 语义，纯追加层。
    """
    try:
        from src.relationship.relationship_core_store import RelationshipCoreStore
        store = RelationshipCoreStore()
        recs = store.list_all()
        confirmed = []
        superseded = {r.get("superseded_by") for r in recs if r.get("superseded_by")}
        for r in recs:
            if r.get("status") not in (None, "confirmed"):
                continue
            if r.get("fact_id") in superseded:
                continue
            fact = None
            # 事实正文：优先 agreements[0]，其次 events 描述，其次 evidence_summary
            ag = r.get("agreements") or []
            if ag and str(ag[0]).strip():
                fact = str(ag[0]).strip()
            elif r.get("evidence_summary"):
                fact = str(r["evidence_summary"]).strip()
            if not fact:
                continue
            confirmed.append(fact)
        if not confirmed:
            return ""
        lines = ["【你们的关系】"]
        lines.extend(f"- {f}" for f in confirmed)
        return "\n".join(lines)
    except Exception:  # noqa: BLE001
        return ""


__all__ = [
    "CREATOR_USER_ID",
    "CREATOR_DISPLAY_NAME",
    "CREATOR_PREFERRED_NAME",
    "YUI_CORE_FACTS",
    "CORE_RELATIONSHIP_MEMORY_IDS",
    "build_yui_core_block",
    "build_relationship_core_block",
]
