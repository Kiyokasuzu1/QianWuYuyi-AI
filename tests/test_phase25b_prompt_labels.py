# -*- coding: utf-8 -*-
"""Phase 2.5-B Prompt 主体标注 + YUI_CORE 常驻块测试。

覆盖:
- 记忆渲染必须区分「清夏铃曾说 / 其他用户曾说 / 羽依自身 / 未知来源记录」
- 禁止出现无主体的「用户:」
- 清清的约定在白名单后渲染为「长期约定(对羽依的约束)」
- YUI_CORE 核心身份事实在任何会话(含陌生人会话)都存在于 system prompt
"""
from src.response.prompt_builder import PromptBuilder
from src.identity.yui_core_profile import (
    CORE_RELATIONSHIP_MEMORY_IDS,
    build_yui_core_block,
)

CREATOR = "366648462"
OTHER = "123456"


def test_format_chat_memories_labels():
    builder = PromptBuilder()
    memories = [
        {"id": "m1", "user_id": CREATOR, "role": "user", "content": "我今天去了XX餐厅",
         "metadata": {"memory_type": "user_shared"}},
        {"id": "m2", "user_id": OTHER, "role": "user", "content": "我喜欢XX游戏",
         "metadata": {"memory_type": "user_shared"}},
        {"id": "m3", "role": "assistant", "content": "羽依自己的记录",
         "metadata": {"memory_type": "user_shared"}},
        {"id": "m4", "role": "user", "content": "没有归属的记录", "metadata": {}},
    ]
    text = builder._format_chat_memories(memories)
    assert "清夏铃曾说" in text
    assert f"用户{OTHER}曾说" in text
    assert "羽依自身" in text
    assert "未知来源记录" in text
    # 禁止无主体标注(现状「用户: xxx」)
    for line in text.splitlines():
        assert not line.strip().startswith("用户:")


def test_anchor_agreement_labeled_as_constraint():
    builder = PromptBuilder()
    CORE_RELATIONSHIP_MEMORY_IDS.add("mem_agr")
    try:
        memories = [{"id": "mem_agr", "user_id": CREATOR, "role": "user",
                     "content": "不要随便和别人抱抱",
                     "metadata": {"memory_type": "user_shared"}}]
        text = builder._format_chat_memories(memories)
        assert "清夏铃" in text
        assert "长期约定" in text
        assert "对羽依的约束" in text
    finally:
        CORE_RELATIONSHIP_MEMORY_IDS.discard("mem_agr")


def test_yui_core_block_present_in_any_session():
    """场景4/6:任何窗口都知道清清是谁(不依赖记忆召回)。"""
    builder = PromptBuilder()
    messages = builder.build_messages(user_message="羽依是谁?", chat_memories=[])
    system = messages[0]["content"]
    assert "核心身份事实" in system
    assert "清夏铃" in system


def test_yui_core_block_present_even_without_user_meta():
    """陌生人会话(无 user_meta/identity_context)也必须包含 YUI_CORE。"""
    builder = PromptBuilder()
    messages = builder.build_messages(user_message="hi", chat_memories=[])
    system = messages[0]["content"]
    assert "清夏铃" in system
    assert "浅雾羽依" in system


def test_yui_core_block_builds_from_facts():
    block = build_yui_core_block()
    assert block.startswith("【核心身份事实】")
    assert "清夏铃" in block
    assert "366648462" in block
