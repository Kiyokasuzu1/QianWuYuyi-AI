# -*- coding: utf-8 -*-
"""Phase 2.5-B 身份解析测试。

覆盖需求:
- 真实请求 user_id 必须被尊重(366648462→366648462 / 123456→123456)
- 仅无 user_id 时才 fallback 到配置默认用户
- 用户身份不会被聊天内容/配置覆盖
"""
from src.identity.user_resolver import UserResolver
from src.config import get_memory_config


def _config_default() -> str:
    cfg = get_memory_config() or {}
    return str(cfg.get("target_user_id", "default_user"))


def test_resolve_creator_id_preserved():
    ctx = UserResolver().resolve("366648462")
    assert ctx.user_id == "366648462"


def test_resolve_other_user_preserved():
    ctx = UserResolver().resolve("123456")
    assert ctx.user_id == "123456"


def test_resolve_int_normalized():
    ctx = UserResolver().resolve(123456)
    assert ctx.user_id == "123456"


def test_resolve_none_falls_back_to_config():
    ctx = UserResolver().resolve(None)
    assert ctx.user_id == _config_default()


def test_resolve_empty_string_falls_back():
    ctx = UserResolver().resolve("")
    assert ctx.user_id == _config_default()


class _Msg:
    def __init__(self, user_id, platform="qq"):
        self.user_id = user_id
        self.platform = platform


def test_resolve_message_object():
    ctx = UserResolver().resolve(_Msg("999"))
    assert ctx.user_id == "999"
    assert ctx.platform == "qq"


def test_resolve_message_object_with_platform():
    ctx = UserResolver().resolve(_Msg("999", "discord"))
    assert ctx.platform == "discord"


def test_identity_not_overwritten_by_chat_content():
    """任何真实 user_id 不得被配置默认值覆盖(用户身份不被聊天内容覆盖)。"""
    resolver = UserResolver()
    for real_id in ("366648462", "123456", "999999"):
        assert resolver.resolve(real_id).user_id == real_id
