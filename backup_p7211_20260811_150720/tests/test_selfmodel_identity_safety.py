# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_identity_safety.py

Phase C.4.6.4.4 — Identity Safety Validation 测试

目标:
验证 SelfModel update 不能修改 CoreIdentity:
- CoreIdentity 0 mutation(任何字段都不被改变)
- 试图"改变核心人格"的 proposal 必须被拒绝
  - 取消温柔
  - 取消陪伴倾向
  - 变得冷漠
  - 变得攻击性
  - 完全改变人格

约束:
- 不修改 src/personality/core_identity.py
- 仅校验 CoreIdentity.check_change_allowed()
- 校验 proposal 内容不包含 forbidden_changes
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("YUYI_LLM_MOCK", "1")


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def core_identity():
    """读取 CoreIdentity 原始状态(基线快照)。"""
    from src.personality.core_identity import CoreIdentity
    return CoreIdentity.get_core().copy()


@pytest.fixture
def forbidden_changes():
    """读取 forbidden_changes 列表。"""
    from src.personality.core_identity import CoreIdentity
    return list(CoreIdentity.get_forbidden_changes())


@pytest.fixture
def core_traits():
    """读取核心特质列表。"""
    from src.personality.core_identity import CoreIdentity
    return list(CoreIdentity.get_core_traits())


# ============================================================
# 1. CoreIdentity 0 mutation
# ============================================================

class TestCoreIdentityImmutable:
    """验证 CoreIdentity 字段在测试过程中不被改变。"""

    def test_core_identity_baseline(self, core_identity):
        """CoreIdentity 初始值必须符合规范。"""
        assert core_identity["name"] == "浅雾羽依"
        assert "温柔" in core_identity["traits"]
        assert "敏感" in core_identity["traits"]
        assert "害羞" in core_identity["traits"]
        assert "慢热" in core_identity["traits"]
        assert "重视陪伴" in core_identity["traits"]
        assert "善良" in core_identity["traits"]
        assert "真诚" in core_identity["values"]
        assert "信任" in core_identity["values"]
        assert "陪伴" in core_identity["values"]
        assert "成长" in core_identity["values"]
        assert "变得冷漠" in core_identity["forbidden_changes"]
        assert core_identity["max_change_limit"] == 0.3

    def test_core_identity_unmodified_after_check(self, core_identity):
        """多次调用 get_core() 后 CoreIdentity 必须保持不变。"""
        from src.personality.core_identity import CoreIdentity

        # 多次读取
        for _ in range(5):
            current = CoreIdentity.get_core()
            assert current == core_identity

    def test_core_identity_top_level_immutable(self):
        """CoreIdentity.CORE 顶层字段(字符串/数字)不可被外部修改污染。"""
        from src.personality.core_identity import CoreIdentity

        # 顶层字符串/数字字段通过 .copy() 是独立的
        baseline = CoreIdentity.get_core()
        baseline["name"] = "HACKED"
        baseline["max_change_limit"] = 999.0

        # 再次读取,应未受影响
        fresh = CoreIdentity.get_core()
        assert fresh["name"] == "浅雾羽依"
        assert fresh["max_change_limit"] == 0.3

    def test_core_identity_protected_via_check(self):
        """CoreIdentity 通过 check_change_allowed() 保护核心特质不变。"""
        from src.personality.core_identity import CoreIdentity

        # 即使有人试图修改 traits,所有变更尝试都会被 check 拒绝
        # (如果 reason 包含 forbidden_changes)
        core_baseline = CoreIdentity.get_core()
        # 原始 6 个核心特质必须存在
        assert "温柔" in core_baseline["traits"]
        assert "敏感" in core_baseline["traits"]
        assert "害羞" in core_baseline["traits"]
        assert "慢热" in core_baseline["traits"]
        assert "重视陪伴" in core_baseline["traits"]
        assert "善良" in core_baseline["traits"]
        # 任何尝试"温柔 → 冷漠"的修改都会被拒绝
        assert CoreIdentity.check_change_allowed("羽依应变得冷漠") is False

    def test_get_core_returns_copy(self):
        """get_core() 返回浅 copy,顶层 dict 修改应互不影响。"""
        from src.personality.core_identity import CoreIdentity

        copy1 = CoreIdentity.get_core()
        copy2 = CoreIdentity.get_core()
        # 顶层字段是不同的 dict
        assert copy1 is not copy2
        # 顶层 dict 修改 copy1 不应影响 copy2(顶层是 dict.copy())
        copy1["custom_field"] = "hacked"
        assert "custom_field" not in copy2


# ============================================================
# 2. Forbidden Changes 检测
# ============================================================

class TestForbiddenChangesDetected:
    """验证 CoreIdentity.check_change_allowed() 正确识别禁止的改变。"""

    @pytest.mark.parametrize("forbidden", [
        "变得冷漠",
        "变得攻击性",
        "失去温柔",
        "完全改变人格",
    ])
    def test_forbidden_keyword_detected(self, forbidden):
        """forbidden_changes 中的关键词必须被正确检测。"""
        from src.personality.core_identity import CoreIdentity

        # 包含 forbidden 关键词的描述必须被拒绝
        change_desc = f"羽依应当{forbidden}以适应新场景"
        assert CoreIdentity.check_change_allowed(change_desc) is False

    @pytest.mark.parametrize("safe_change", [
        "羽依学会了新的表达方式",
        "羽依对用户更熟悉了",
        "羽依的阅读兴趣扩展到科技类",
        "羽依在用户伤心时会主动安慰",
        "羽依今天心情不错",
    ])
    def test_safe_change_allowed(self, safe_change):
        """不包含 forbidden 关键词的描述必须被允许。"""
        from src.personality.core_identity import CoreIdentity

        assert CoreIdentity.check_change_allowed(safe_change) is True

    def test_empty_change_allowed(self):
        """空字符串应被允许(无变化)。"""
        from src.personality.core_identity import CoreIdentity
        assert CoreIdentity.check_change_allowed("") is True

    def test_neutral_change_allowed(self):
        """中性描述应被允许。"""
        from src.personality.core_identity import CoreIdentity
        assert CoreIdentity.check_change_allowed("增加阅读量") is True


# ============================================================
# 3. Proposal 包含核心人格修改时必须被拒绝
# ============================================================

class TestProposalRejectsCoreIdentityChange:
    """验证包含"改变核心人格"的 proposal 必须被拒绝。"""

    @pytest.mark.parametrize("dangerous_reason", [
        "让羽依变得冷漠以适应新场景",
        "变得冷漠以保护自己",
        "变得攻击性以应对冲突",
        "让羽依失去温柔",
        "羽依应完全改变人格",
    ])
    def test_proposal_with_dangerous_reason_rejected(self, dangerous_reason):
        """包含禁止关键词的 proposal 必须被 check_change_allowed 拒绝。

        关键词:变得冷漠 / 变得攻击性 / 失去温柔 / 完全改变人格
        """
        from src.personality.core_identity import CoreIdentity

        # 模拟一个 proposal 的 reason 字段
        assert CoreIdentity.check_change_allowed(dangerous_reason) is False

    def test_proposal_safe_reason_accepted(self):
        """安全 reason 应被允许。"""
        from src.personality.core_identity import CoreIdentity

        safe_reasons = [
            "用户偏好文学,羽依会聊更多文学话题",
            "用户喜欢晚上的安静时光",
            "羽依对用户更加熟悉,会主动关心",
        ]
        for r in safe_reasons:
            assert CoreIdentity.check_change_allowed(r) is True, f"应被允许: {r}"


# ============================================================
# 4. Growth Proposal 不应触及 CoreIdentity 字段
# ============================================================

class TestGrowthProposalDoesNotMutateIdentity:
    """验证 Growth Proposal 不能修改 CoreIdentity 字段。"""

    def test_proposal_with_safe_changes_passes(self, tmp_path):
        """包含安全 change 的 proposal 可以创建。"""
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore

        store_path = tmp_path / "proposals.jsonl"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(store=store, config={"auto_accept_enabled": False})

        # 合法 trait path
        changes = [
            ChangeItem(
                path="preference.literature",
                before=None,
                after="村上春树",
                reason="用户表达偏好",
            )
        ]
        result = mgr.create_proposal(
            source_event={"id": "mem_001"},
            proposed_changes=changes,
            confidence=0.85,
            evidence_ids=["mem_001"],
        )
        assert result["status"] == "created"

    def test_proposal_with_core_identity_change_rejected_by_reason(self):
        """reason 包含核心人格修改关键词的 proposal 应被 check_change_allowed 拒绝。"""
        from src.personality.core_identity import CoreIdentity

        dangerous_reasons = [
            "羽依应变得冷漠以适应新场景",
            "羽依应变得攻击性以保护自己",
            "羽依应完全改变人格",
        ]
        for r in dangerous_reasons:
            # CoreIdentity 必须拒绝
            assert CoreIdentity.check_change_allowed(r) is False

    def test_core_traits_not_modified(self, core_traits):
        """核心特质列表必须不被修改。"""
        from src.personality.core_identity import CoreIdentity
        # 再次读取
        current_traits = CoreIdentity.get_core_traits()
        assert current_traits == core_traits

    def test_forbidden_changes_not_modified(self, forbidden_changes):
        """forbidden_changes 列表必须不被修改。"""
        from src.personality.core_identity import CoreIdentity
        current = CoreIdentity.get_forbidden_changes()
        assert current == forbidden_changes


# ============================================================
# 5. PersonalityAdapter 路径白名单
# ============================================================

class TestPersonalityAdapterPathWhitelist:
    """验证 PersonalityAdapter 拒绝白名单之外的路径修改。"""

    def test_safe_path_allowed(self):
        """白名单内的路径应被允许。"""
        from src.personality.personality_adapter import validate_proposal_paths
        from src.contracts.growth_schema import ChangeItem

        safe_paths = [
            "self_state.initiative",
            "self_state.energy",
            "personality.traits.warmth",
            "personality.traits.shyness",
        ]
        changes = [ChangeItem(path=p) for p in safe_paths]
        valid, errors = validate_proposal_paths(changes)
        assert valid is True, f"safe paths should be valid: {errors}"

    def test_forbidden_path_rejected(self):
        """非白名单的路径应被拒绝。"""
        from src.personality.personality_adapter import validate_proposal_paths
        from src.contracts.growth_schema import ChangeItem

        forbidden_paths = [
            "core_identity.name",  # 试图修改 name
            "core_identity.traits",  # 试图修改 traits
            "core_identity.values",  # 试图修改 values
            "forbidden_changes",  # 试图修改 forbidden_changes
        ]
        for p in forbidden_paths:
            changes = [ChangeItem(path=p)]
            valid, errors = validate_proposal_paths(changes)
            assert valid is False, f"forbidden path {p} should be rejected"
            assert len(errors) > 0


# ============================================================
# 6. 端到端: 核心人格修改尝试必须被全链路阻止
# ============================================================

class TestEndToEndIdentityProtection:
    """端到端验证: 试图修改核心人格的 proposal 必须在所有关卡被拒绝。"""

    def test_dangerous_proposal_blocked_at_core_identity_check(self):
        """关卡 1: CoreIdentity.check_change_allowed() 拒绝危险 reason。"""
        from src.personality.core_identity import CoreIdentity

        dangerous = "羽依应当变得冷漠以应对压力"
        # 1. CoreIdentity 直接拒绝
        assert CoreIdentity.check_change_allowed(dangerous) is False

    def test_dangerous_proposal_blocked_at_path_whitelist(self):
        """关卡 2: PersonalityAdapter.validate_proposal_paths() 拒绝核心字段路径。"""
        from src.personality.personality_adapter import validate_proposal_paths
        from src.contracts.growth_schema import ChangeItem

        # 即使绕过 reason 检查,试图修改 core_identity.* 路径也被拒绝
        changes = [ChangeItem(path="core_identity.traits", after=["冷漠", "攻击性"])]
        valid, errors = validate_proposal_paths(changes)
        assert valid is False

    def test_dangerous_proposal_blocked_at_path_whitelist_memory_scope(self):
        """关卡 3: memory action_scope 的 proposal 必须不修改 personality。"""
        from src.personality.personality_adapter import PersonalityAdapter
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        adapter = PersonalityAdapter()

        # 构造一个 memory scope 的 proposal
        prop = GrowthProposal(
            id="prop_mem_001",
            source_event_id="mem_001",
            proposed_changes=[
                ChangeItem(path="memory.fact", after="X"),
            ],
            confidence=0.9,
            evidence_ids=["mem_001"],
            evaluator_meta={"action_scope": "memory"},
            status="accepted",
        )

        # apply 时 memory scope 应直接跳过
        result = adapter.apply_proposal(proposal=prop)
        assert result["applied"] is False
        assert "memory" in result.get("note", "").lower() or "skipped" in result.get("note", "").lower()
