# -*- coding: utf-8 -*-
"""P2.1.3-A Permission Gate Foundation 单元测试。

覆盖最小策略契约：
    - permission == "user"  → memory/emotion/personality/growth 全部允许
    - permission == "sandbox" / "unknown" / 其他任何值 → 全部拒绝
    - None / 字段缺失对象 / 任意异常对象 → fail-closed，且永不抛异常
    - get_permissions 键与 PERMISSION_TARGETS 严格一致，值与四个判定函数一致
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.security.identity import Identity
from src.security.permission import (
    PERMISSION_TARGETS,
    can_modify_emotion,
    can_modify_memory,
    can_modify_personality,
    can_modify_relationship,
    can_trigger_growth,
    get_permissions,
)

_ALL_CHECK_FNS = (
    can_modify_memory,
    can_modify_emotion,
    can_modify_personality,
    can_trigger_growth,
    can_modify_relationship,
)


class TestUserIdentityAllowed:
    """已验证 user 身份：四个能力全部放行，get_permissions 全 True。"""

    def test_user_identity_all_allowed(self):
        identity = Identity(id="366648462", source="qq", verified=True, permission="user")
        assert can_modify_memory(identity) is True
        assert can_modify_emotion(identity) is True
        assert can_modify_personality(identity) is True
        assert can_trigger_growth(identity) is True
        assert can_modify_relationship(identity) is True

    def test_user_get_permissions_all_true(self):
        identity = Identity(id="366648462", source="qq", verified=True, permission="user")
        perms = get_permissions(identity)
        assert set(perms.keys()) == set(PERMISSION_TARGETS)
        assert all(perms.values())


class TestSandboxIdentityDenied:
    """沙盒身份：四个能力全部拒绝（不修改任何全局状态）。"""

    def test_sandbox_identity_all_denied(self):
        identity = Identity(id="_unknown_sender", source="placeholder", verified=False, permission="sandbox")
        for fn in _ALL_CHECK_FNS:
            assert fn(identity) is False, f"{fn.__name__} 应拒绝 sandbox"


class TestUnknownPermissionDenied:
    """permission 为 unknown（或任何非 user 值）：fail-closed 全部拒绝。"""

    def test_unknown_permission_all_denied(self):
        identity = Identity(id="abc123", source="qq", verified=False, permission="unknown")
        for fn in _ALL_CHECK_FNS:
            assert fn(identity) is False, f"{fn.__name__} 应拒绝 unknown"

    def test_unexpected_permission_value_denied(self):
        identity = Identity(id="abc123", source="qq", verified=False, permission="admin")
        for fn in _ALL_CHECK_FNS:
            assert fn(identity) is False, f"{fn.__name__} 应拒绝非 user 值"


class TestRobustnessFailClosed:
    """异常输入永不抛异常，且一律拒绝。"""

    def test_none_identity_safe(self):
        for fn in _ALL_CHECK_FNS:
            assert fn(None) is False
        assert all(v is False for v in get_permissions(None).values())

    def test_object_missing_permission_field_safe(self):
        class WeirdObject:
            id = "x"

        obj = WeirdObject()
        for fn in _ALL_CHECK_FNS:
            assert fn(obj) is False
        assert all(v is False for v in get_permissions(obj).values())


class TestGetPermissionsContract:
    """get_permissions 契约：键完整、值类型与四个判定函数一致。"""

    def test_sandbox_full_dict_contract(self):
        identity = Identity(id="_unknown_sender", source="placeholder", verified=False, permission="sandbox")
        perms = get_permissions(identity)
        assert perms == {
            "memory": False,
            "emotion": False,
            "personality": False,
            "growth": False,
            "relationship": False,
        }

    def test_permissions_consistent_with_check_fns(self):
        identity = Identity(id="366648462", source="qq", verified=True, permission="user")
        perms = get_permissions(identity)
        mapping = {
            "memory": can_modify_memory(identity),
            "emotion": can_modify_emotion(identity),
            "personality": can_modify_personality(identity),
            "growth": can_trigger_growth(identity),
            "relationship": can_modify_relationship(identity),
        }
        assert perms == mapping
        assert all(isinstance(v, bool) for v in perms.values())
