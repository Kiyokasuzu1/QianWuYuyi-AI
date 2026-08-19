# -*- coding: utf-8 -*-
"""P2.1.1 Identity Resolver 基础层测试。

命名说明：tests/test_identity_resolver.py 已被人格侧解析器
（src/personality/identity_resolver.py）占用，本文件测用户身份解析
（src/security/identity.py），故命名 test_security_identity_resolver.py。

覆盖（任务书 Phase 4 十项）：
    1. 合法QQ解析        2. None输入         3. 空字符串
    4. default           5. anonymous        6. guest
    7. 非法字符          8. 数字范围错误     9. 输出字段完整性
    10. sandbox权限正确
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.security.identity import (  # noqa: E402
    DEFAULT_RESOLVER,
    SANDBOX_ID,
    Identity,
    IdentityResolver,
    resolve_identity,
)


class TestValidQQ:
    """1. 合法 QQ 解析。"""

    def test_creator_qq(self):
        identity = IdentityResolver().resolve("366648462")
        assert identity.id == "366648462"
        assert identity.source == "qq"
        assert identity.verified is True
        assert identity.permission == "user"

    def test_stranger_real_qq_also_user(self):
        """陌生人的真实 QQ 同样原样使用（p6sec T1b：不串进他人桶）。"""
        identity = IdentityResolver().resolve("123456789")
        assert identity.id == "123456789"
        assert identity.permission == "user"

    def test_boundary_5_and_12_digits(self):
        assert IdentityResolver().resolve("10000").permission == "user"
        assert IdentityResolver().resolve("123456789012").permission == "user"

    def test_surrounding_whitespace_stripped(self):
        """首尾空白属于传输噪音，strip 后仍为纯数字 → 放行。"""
        identity = IdentityResolver().resolve("  366648462\n")
        assert identity.id == "366648462"
        assert identity.permission == "user"


class TestInvalidInputs:
    """2~8. 非法输入统一落沙盒。"""

    def test_none_input(self):
        identity = IdentityResolver().resolve(None)
        assert identity.id == SANDBOX_ID
        assert identity.source == "unknown"
        assert identity.verified is False
        assert identity.permission == "sandbox"

    def test_empty_string(self):
        identity = IdentityResolver().resolve("")
        assert identity.permission == "sandbox"

    def test_placeholder_default(self):
        for variant in ("default", "Default", "DEFAULT"):
            assert IdentityResolver().resolve(variant).permission == "sandbox"

    def test_placeholder_anonymous(self):
        assert IdentityResolver().resolve("anonymous").id == SANDBOX_ID

    def test_placeholder_guest(self):
        assert IdentityResolver().resolve("guest").id == SANDBOX_ID

    def test_placeholder_unknown_none_null(self):
        for variant in ("unknown", "none", "null", "None", "NULL"):
            assert IdentityResolver().resolve(variant).permission == "sandbox"

    def test_int_qq_number_accepted(self):
        """P2.1.2 修订：JSON number 型 user（int，非 bool）按 QQ 使用（API 兼容）。"""
        identity = IdentityResolver().resolve(366648462)
        assert identity.id == "366648462"
        assert identity.source == "qq"
        assert identity.permission == "user"

    def test_other_non_string_types_rejected(self):
        """float/dict/bool/bytes 等其余非字符串一律落沙盒。"""
        for raw in (3.14, True, False, {}, [], b"366648462"):
            assert IdentityResolver().resolve(raw).permission == "sandbox", repr(raw)

    def test_int_out_of_qq_range_rejected(self):
        assert IdentityResolver().resolve(1234).permission == "sandbox"
        assert IdentityResolver().resolve(1234567890123).permission == "sandbox"

    def test_illegal_characters(self):
        for raw in ("qq_366648462", "366648462abc", "user@test", "清清", "-12345"):
            assert IdentityResolver().resolve(raw).permission == "sandbox", raw

    def test_digit_length_out_of_range(self):
        assert IdentityResolver().resolve("1234").permission == "sandbox"        # 4 位
        assert IdentityResolver().resolve("1234567890123").permission == "sandbox"  # 13 位

    def test_no_auto_correction(self):
        """禁止猜测/修正：带修饰的数字不会被拆解出 QQ。"""
        assert IdentityResolver().resolve("qq:366648462").id == SANDBOX_ID
        assert IdentityResolver().resolve("+8613664846212").id == SANDBOX_ID


class TestIdentityContract:
    """9~10. 输出契约。"""

    def test_field_completeness_and_types(self):
        identity = IdentityResolver().resolve("366648462")
        field_names = {f.name for f in dataclasses.fields(identity)}
        assert field_names == {"id", "source", "verified", "permission"}
        assert isinstance(identity.id, str)
        assert isinstance(identity.source, str)
        assert isinstance(identity.verified, bool)
        assert isinstance(identity.permission, str)

    def test_sandbox_permission_correct(self):
        identity = IdentityResolver().resolve(None)
        assert identity.id == "_unknown_sender"
        assert identity.source == "unknown"
        assert identity.verified is False
        assert identity.permission == "sandbox"
        assert identity.is_sandbox is True

    def test_identity_immutable(self):
        identity = IdentityResolver().resolve("366648462")
        try:
            identity.id = "hacked"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            pass
        else:
            raise AssertionError("Identity 必须不可变（frozen）")

    def test_resolve_never_raises(self):
        """异常输入也绝不抛异常（fail-closed）。"""
        resolver = IdentityResolver()
        for raw in (None, "", {}, [], 0, 3.14, object(), b"366648462", "x" * 5000):
            assert isinstance(resolver.resolve(raw), Identity)


class TestReservedInterface:
    """预留扩展接口（仅存储，不启用行为）。"""

    def test_default_resolver_shared(self):
        assert isinstance(DEFAULT_RESOLVER, IdentityResolver)
        assert resolve_identity("366648462").permission == "user"

    def test_single_user_mode_reserved_no_behavior_change(self):
        """single_user_mode=True 本阶段不改变任何解析结果（接口预留）。"""
        strict = IdentityResolver()
        single = IdentityResolver(single_user_mode=True)
        for raw in ("default", None, "", "366648462"):
            assert strict.resolve(raw) == single.resolve(raw)

    def test_allowed_sources_stored(self):
        resolver = IdentityResolver(allowed_sources=("qq", "api"))
        assert "api" in resolver.allowed_sources
