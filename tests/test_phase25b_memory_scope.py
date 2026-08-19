# -*- coding: utf-8 -*-
"""Phase 2.5-B Memory Scope 纯函数测试。

覆盖:scope 推导 / 四层授权矩阵 / 软权重 / Prompt 主体标注。
"""
from src.memory.memory_scope import (
    SCOPE_YUI_CORE,
    SCOPE_RELATIONSHIP_CORE,
    SCOPE_PRIVATE_USER,
    SCOPE_PUBLIC,
    derive_scope,
    resolve_allowed,
    scope_weight,
    label_for_prompt,
)
from src.identity.yui_core_profile import CORE_RELATIONSHIP_MEMORY_IDS

CREATOR = "366648462"
OTHER = "123456"


def _rec(**kw):
    base = {
        "id": "mem_t",
        "user_id": CREATOR,
        "role": "user",
        "content": "x",
        "metadata": {"memory_type": "user_shared"},
    }
    base.update(kw)
    return base


class TestDeriveScope:
    def test_explicit_scope_wins(self):
        rec = _rec(metadata={"memory_type": "user_shared", "memory_scope": "public"})
        assert derive_scope(rec) == SCOPE_PUBLIC

    def test_assistant_role_is_yui_core(self):
        assert derive_scope(_rec(role="assistant")) == SCOPE_YUI_CORE

    def test_user_role_defaults_private(self):
        assert derive_scope(_rec()) == SCOPE_PRIVATE_USER

    def test_whitelist_anchor_is_relationship_core(self):
        CORE_RELATIONSHIP_MEMORY_IDS.add("mem_anchor_test")
        try:
            assert derive_scope(_rec(id="mem_anchor_test")) == SCOPE_RELATIONSHIP_CORE
        finally:
            CORE_RELATIONSHIP_MEMORY_IDS.discard("mem_anchor_test")


class TestResolveAllowed:
    def test_yui_core_and_public_always_allowed(self):
        assert resolve_allowed(_rec(role="assistant"), OTHER)
        assert resolve_allowed(
            _rec(metadata={"memory_type": "user_shared", "memory_scope": "public"}),
            OTHER,
        )

    def test_creator_private_only_creator(self):
        rec = _rec(content="私人经历")
        assert resolve_allowed(rec, CREATOR)
        assert not resolve_allowed(rec, OTHER)

    def test_other_private_only_other(self):
        rec = _rec(user_id=OTHER)
        assert resolve_allowed(rec, OTHER)
        assert not resolve_allowed(rec, CREATOR)

    def test_relationship_core_own_key(self):
        rec = _rec(metadata={
            "memory_type": "user_shared",
            "memory_scope": "relationship_core",
            "relationship_key": f"yuyi:{OTHER}",
        })
        assert resolve_allowed(rec, OTHER)
        assert not resolve_allowed(rec, CREATOR)

    def test_relationship_core_global_visible_everywhere(self):
        # 清清的约定在用户B会话仍作为羽依自身约束存在(场景1)
        rec = _rec(metadata={
            "memory_type": "user_shared",
            "memory_scope": "relationship_core",
            "relationship_key": f"yuyi:{CREATOR}",
            "visibility": "global",
        })
        assert resolve_allowed(rec, CREATOR)
        assert resolve_allowed(rec, OTHER)

    def test_relationship_only_not_visible_to_third_party(self):
        rec = _rec(user_id=OTHER, metadata={
            "memory_type": "user_shared",
            "memory_scope": "relationship_core",
            "relationship_key": f"yuyi:{OTHER}",
            "visibility": "relationship_only",
        })
        assert resolve_allowed(rec, OTHER)
        assert not resolve_allowed(rec, CREATOR)

    def test_anchor_visible_everywhere(self):
        CORE_RELATIONSHIP_MEMORY_IDS.add("mem_anchor_test2")
        try:
            rec = _rec(id="mem_anchor_test2")
            assert resolve_allowed(rec, CREATOR)
            assert resolve_allowed(rec, OTHER)  # 约定跨窗口存在
        finally:
            CORE_RELATIONSHIP_MEMORY_IDS.discard("mem_anchor_test2")

    def test_unknown_source_not_allowed(self):
        rec = {"id": "mem_u", "role": "user", "content": "?", "metadata": {}}
        assert not resolve_allowed(rec, CREATOR)
        assert not resolve_allowed(rec, OTHER)


class TestScopeWeight:
    def test_own_relationship_full_weight(self):
        rec = _rec(metadata={
            "memory_type": "user_shared",
            "memory_scope": "relationship_core",
            "relationship_key": f"yuyi:{CREATOR}",
        })
        assert scope_weight(rec, CREATOR) == 1.0

    def test_foreign_global_relationship_downweighted(self):
        rec = _rec(metadata={
            "memory_type": "user_shared",
            "memory_scope": "relationship_core",
            "relationship_key": f"yuyi:{CREATOR}",
            "visibility": "global",
        })
        assert scope_weight(rec, OTHER) == 0.5

    def test_private_full_weight(self):
        assert scope_weight(_rec(), CREATOR) == 1.0


class TestLabelForPrompt:
    def test_creator_private_label(self):
        assert label_for_prompt(_rec(), CREATOR) == "清夏铃曾说"

    def test_other_user_private_label(self):
        rec = _rec(user_id=OTHER)
        assert label_for_prompt(rec, OTHER) == f"用户{OTHER}曾说"

    def test_unknown_source_label(self):
        rec = {"id": "mem_u", "role": "user", "content": "?", "metadata": {}}
        assert label_for_prompt(rec, CREATOR) == "未知来源记录"

    def test_yui_own_label(self):
        assert label_for_prompt(_rec(role="assistant"), CREATOR) == "羽依自身"

    def test_relationship_core_label_creator(self):
        rec = _rec(metadata={
            "memory_type": "user_shared",
            "memory_scope": "relationship_core",
            "relationship_key": f"yuyi:{CREATOR}",
        })
        label = label_for_prompt(rec, CREATOR)
        assert "清夏铃" in label
        assert "长期约定" in label

    def test_relationship_core_label_foreign_constraint(self):
        # 场景1:用户B会话中,清清约定渲染为「对羽依的约束」而非当前用户的话
        rec = _rec(metadata={
            "memory_type": "user_shared",
            "memory_scope": "relationship_core",
            "relationship_key": f"yuyi:{CREATOR}",
            "visibility": "global",
        })
        label = label_for_prompt(rec, OTHER)
        assert "清夏铃" in label
        assert "对羽依的约束" in label

    def test_other_user_relationship_label(self):
        rec = _rec(metadata={
            "memory_type": "user_shared",
            "memory_scope": "other_user_relationship",
            "subject_user_id": OTHER,
        })
        assert label_for_prompt(rec, CREATOR) == f"关于用户{OTHER}"
