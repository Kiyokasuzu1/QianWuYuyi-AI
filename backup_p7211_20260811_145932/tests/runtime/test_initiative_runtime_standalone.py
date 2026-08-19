# -*- coding: utf-8 -*-
"""
tests/runtime/test_initiative_runtime_standalone.py

Phase 7.2.1-p1: Runtime InitiativeBridge 独立单元测试。

覆盖:
  1. InitiativeBridge 构造 + cooldown 配置读取（默认 60s / 自定义 / 0 关）
  2. handle_send_message: 同用户 cooldown 内跳过
  3. handle_send_message: 同用户 30s 内相同内容指纹去重
  4. handle_send_message: 发送失败时回滚 cooldown 记录（不占用冷却槽）
  5. handle_send_message: 无 target_user 时直接跳过
  6. handle_send_message: payload 已有 message 不用 generate_initiative
  7. _resolve_target_user 优先级：payload > send_config > orchestrator.target_user_id
  8. 旧版兼容：initiative_sender.py 根文件已删除；新版 src/runtime/initiative_sender.py
     提供 flatten_initiative_config / build_send_config_from_flat / send_private_msg 接口
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def fake_action():
    """构造一个用于测试的 Action 对象。"""
    from src.runtime.action_dispatcher import Action

    def _factory(
        *,
        action_id: str = "test_action",
        message: str | None = None,
        target_user: str | None = None,
        reason: str = "测试触发",
    ):
        payload: Dict[str, Any] = {}
        if message is not None:
            payload["message"] = message
        if target_user is not None:
            payload["target_user"] = target_user
        return Action(
            action_id=action_id,
            action_type="send_message",
            reason=reason,
            payload=payload,
        )
    return _factory


@pytest.fixture
def success_callback():
    """返回一个始终成功(True)的 send_callback。"""
    return lambda msg: True


@pytest.fixture
def failure_callback():
    """返回一个始终失败(False)的 send_callback。"""
    return lambda msg: False


# =====================================================================
# 1. InitiativeBridge 构造 + cooldown 配置
# =====================================================================

class TestInitiativeBridgeConstruction:
    """InitiativeBridge 构造与 cooldown 配置读取。"""

    def test_default_cooldown_is_60(self):
        """未显式配置 cooldown → 默认 60s。"""
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(orchestrator=None, send_config={})
        assert bridge._get_cooldown_seconds() == 60

    def test_custom_cooldown_seconds(self):
        """显式配置 initiative_cooldown_seconds=30 → 返回 30。"""
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=None,
            send_config={"initiative_cooldown_seconds": 30},
        )
        assert bridge._get_cooldown_seconds() == 30

    def test_cooldown_zero_disables_check(self):
        """initiative_cooldown_seconds=0 → _check_cooldown 始终返回 None。"""
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=None,
            send_config={"initiative_cooldown_seconds": 0},
        )
        assert bridge._get_cooldown_seconds() == 0
        assert bridge._check_cooldown("any_user") is None

    def test_invalid_cooldown_falls_back_to_default(self):
        """非法值(None/"abc"/负数) → 回退默认 60 或 0。"""
        from src.runtime.initiative_bridge import InitiativeBridge
        # None -> 默认 60
        b1 = InitiativeBridge(orchestrator=None,
                              send_config={"initiative_cooldown_seconds": None})
        assert b1._get_cooldown_seconds() == 60
        # 字符串 -> 默认 60
        b2 = InitiativeBridge(orchestrator=None,
                              send_config={"initiative_cooldown_seconds": "abc"})
        assert b2._get_cooldown_seconds() == 60
        # 负数 -> 0（至少 0 秒）
        b3 = InitiativeBridge(orchestrator=None,
                              send_config={"initiative_cooldown_seconds": -15})
        assert b3._get_cooldown_seconds() == 0

    def test_is_registered_initially_false(self):
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(orchestrator=None)
        assert bridge.is_registered is False


# =====================================================================
# 2. cooldown 内跳过发送
# =====================================================================

class TestInitiativeBridgeCooldown:
    """测试 cooldown 防重复发送。"""

    def test_second_send_within_cooldown_skipped(self, fake_action, success_callback):
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=None,
            send_config={
                "target_user": "u1",
                "initiative_cooldown_seconds": 60,
                "send_callback": success_callback,
            },
        )
        a1 = fake_action(message="第一次消息")
        r1 = bridge.handle_send_message(a1)
        assert r1.get("sent") is True, f"第一次发送应成功: {r1}"

        # 第二次在 cooldown 内 → 跳过
        a2 = fake_action(message="第二次不同的消息")
        r2 = bridge.handle_send_message(a2)
        assert r2.get("sent") is False
        assert r2.get("skipped_by_cooldown") is True
        assert "cooldown active" in (r2.get("reason") or "")

    def test_cooldown_expired_allows_resend(self, fake_action, success_callback):
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=None,
            send_config={
                "target_user": "u1",
                "initiative_cooldown_seconds": 1,  # 1s 短冷却便于测试
                "send_callback": success_callback,
            },
        )
        a1 = fake_action(message="m1")
        assert bridge.handle_send_message(a1).get("sent") is True
        # 等 1.1 秒让 cooldown 过期
        time.sleep(1.1)
        a2 = fake_action(message="m2")
        r2 = bridge.handle_send_message(a2)
        assert r2.get("sent") is True, f"cooldown 过期后应允许重发: {r2}"
        assert r2.get("skipped_by_cooldown") is not True

    def test_disabled_cooldown_allows_burst(self, fake_action, success_callback):
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=None,
            send_config={
                "target_user": "u1",
                "initiative_cooldown_seconds": 0,  # 关 cooldown
                "send_callback": success_callback,
            },
        )
        # 连续 5 次都应成功
        for i in range(5):
            action = fake_action(message=f"burst_{i}", action_id=f"act_{i}")
            r = bridge.handle_send_message(action)
            assert r.get("sent") is True, f"第 {i} 次 burst 发送失败: {r}"

    def test_different_users_not_cross_cooldown(self, fake_action, success_callback):
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=None,
            send_config={
                # 不在顶层配置 target_user，用 payload 传
                "initiative_cooldown_seconds": 60,
                "send_callback": success_callback,
            },
        )
        # 用户 A 先发
        a_a = fake_action(target_user="userA", message="A 的消息")
        assert bridge.handle_send_message(a_a).get("sent") is True
        # 用户 A 在 cooldown 内应被挡
        a_a2 = fake_action(target_user="userA", message="A 的第二条")
        assert bridge.handle_send_message(a_a2).get("skipped_by_cooldown") is True
        # 用户 B 不受 A 的 cooldown 影响
        a_b = fake_action(target_user="userB", message="B 的消息")
        assert bridge.handle_send_message(a_b).get("sent") is True


# =====================================================================
# 3. 消息指纹去重（同用户 30s 内相同前 100 char 消息去重）
# =====================================================================

class TestInitiativeBridgeDedup:
    """消息指纹去重。"""

    def test_same_message_within_30s_deduped(self, fake_action, success_callback):
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=None,
            send_config={
                "target_user": "u1",
                "initiative_cooldown_seconds": 0,  # 关 cooldown，单独验证 dedup
                "send_callback": success_callback,
            },
        )
        same_msg = "完全相同的主动消息内容" * 3
        a1 = fake_action(message=same_msg)
        assert bridge.handle_send_message(a1).get("sent") is True
        a2 = fake_action(message=same_msg)
        r2 = bridge.handle_send_message(a2)
        assert r2.get("sent") is False
        assert r2.get("skipped_by_duplicate") is True

    def test_different_message_not_deduped(self, fake_action, success_callback):
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=None,
            send_config={
                "target_user": "u1",
                "initiative_cooldown_seconds": 0,
                "send_callback": success_callback,
            },
        )
        a1 = fake_action(message="不同的消息 A")
        assert bridge.handle_send_message(a1).get("sent") is True
        a2 = fake_action(message="不同的消息 B")
        assert bridge.handle_send_message(a2).get("sent") is True

    def test_long_message_first_100_deduped(self, fake_action, success_callback):
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=None,
            send_config={
                "target_user": "u1",
                "initiative_cooldown_seconds": 0,
                "send_callback": success_callback,
            },
        )
        head = "公共前100字符" + "x" * 94  # 6 + 94 = 100 字符，确保前 100 char 完全相同
        a1_msg = head + "后缀A"
        a2_msg = head + "后缀B"  # 前 100 char 完全相同（都是 head），后缀不同仍被去重
        assert bridge.handle_send_message(fake_action(message=a1_msg)).get("sent") is True
        r2 = bridge.handle_send_message(fake_action(message=a2_msg))
        # 指纹基于前 100 char，所以仍被去重
        assert r2.get("sent") is False
        assert r2.get("skipped_by_duplicate") is True


# =====================================================================
# 4. 发送失败 → 回滚 cooldown 记录
# =====================================================================

class TestInitiativeBridgeFailureRollback:
    """发送失败时，cooldown/fingerprint 记录被回滚，不占用下一次发送槽。"""

    def test_send_failure_rolls_back_cooldown_record(self, fake_action, failure_callback,
                                                      success_callback):
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=None,
            send_config={
                "target_user": "u1",
                "initiative_cooldown_seconds": 60,
                "send_callback": failure_callback,
            },
        )
        a1 = fake_action(message="将失败的消息")
        r1 = bridge.handle_send_message(a1)
        assert r1.get("sent") is False, f"callback 返回 False 时 sent 应为 False: {r1}"
        assert r1.get("reason") == "send_message returned False"

        # 立即用 success_callback 替换并重发 → 应成功（因为失败被回滚，没占用 cooldown）
        bridge._send_config["send_callback"] = success_callback
        a2 = fake_action(message="新内容")
        r2 = bridge.handle_send_message(a2)
        assert r2.get("sent") is True, \
            (f"失败记录应回滚，允许立即重发但实际失败。"
             f"r2={r2} r1={r1}")
        # r2 既不应是 cooldown skip，也不应是 dedup（message 不同）
        assert r2.get("skipped_by_cooldown") is not True
        assert r2.get("skipped_by_duplicate") is not True


# =====================================================================
# 5. 无 target_user → 直接跳过
# =====================================================================

class TestInitiativeBridgeNoTarget:
    """缺少 target_user 配置时的行为。"""

    def test_no_target_anywhere_returns_reason(self, fake_action, success_callback):
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=None,  # 无 orchestrator，也没有 target_user_id
            send_config={
                # 顶层不配置 target_user
                "initiative_cooldown_seconds": 0,
                "send_callback": success_callback,
            },
        )
        action = fake_action(message="不会被发送的消息")  # payload 中也不带 target_user
        result = bridge.handle_send_message(action)
        assert result.get("sent") is False
        assert "no target_user" in (result.get("reason") or "")
        assert result.get("message") is None  # 无目标时，甚至不应进入 message 处理


# =====================================================================
# 6. payload 已有 message → 不走 generate_initiative
# =====================================================================

class TestInitiativeBridgeMessageInPayload:
    """payload.message 已存在 → 直接用；不调用 orchestrator.generate_initiative。"""

    def test_existing_message_bypasses_generate(self, fake_action, success_callback):
        from src.runtime.initiative_bridge import InitiativeBridge

        mock_orch = MagicMock()
        mock_orch.target_user_id = "u_mock"
        bridge = InitiativeBridge(
            orchestrator=mock_orch,
            send_config={
                "initiative_cooldown_seconds": 0,
                "send_callback": success_callback,
            },
        )
        action = fake_action(message="payload 中的消息")  # payload 已有 message
        result = bridge.handle_send_message(action)
        assert result.get("sent") is True
        # generate_initiative 不应被调用
        mock_orch.generate_initiative.assert_not_called()
        assert result.get("message") == "payload 中的消息"

    def test_no_message_calls_generate(self, fake_action, success_callback):
        """payload 无 message → 调用 orchestrator.generate_initiative。"""
        from src.runtime.initiative_bridge import InitiativeBridge

        mock_orch = MagicMock()
        mock_orch.target_user_id = "u_mock"
        mock_orch.generate_initiative.return_value = "来自生成器的主动消息"
        bridge = InitiativeBridge(
            orchestrator=mock_orch,
            send_config={
                "initiative_cooldown_seconds": 0,
                "send_callback": success_callback,
            },
        )
        # 不设 message → 走 generate_initiative 分支
        action = fake_action()
        result = bridge.handle_send_message(action)
        assert result.get("sent") is True
        mock_orch.generate_initiative.assert_called_once_with("u_mock")
        assert result.get("message") == "来自生成器的主动消息"

    def test_generate_returns_dict(self, fake_action, success_callback):
        """generate_initiative 返回 dict（老版本行为）→ 取 message/content 字段。"""
        from src.runtime.initiative_bridge import InitiativeBridge

        mock_orch = MagicMock()
        mock_orch.target_user_id = "u_mock"
        mock_orch.generate_initiative.return_value = {"message": "dict 里的消息"}
        bridge = InitiativeBridge(
            orchestrator=mock_orch,
            send_config={
                "initiative_cooldown_seconds": 0,
                "send_callback": success_callback,
            },
        )
        action = fake_action()
        result = bridge.handle_send_message(action)
        assert result.get("sent") is True
        assert result.get("message") == "dict 里的消息"


# =====================================================================
# 7. _resolve_target_user 优先级链
# =====================================================================

class TestInitiativeBridgeTargetResolution:
    """target_user 解析优先级：payload > send_config > orchestrator.target_user_id。"""

    def _make_bridge(self, orch_target=None, cfg_target=None):
        from src.runtime.initiative_bridge import InitiativeBridge
        mock_orch = None
        if orch_target is not None:
            mock_orch = MagicMock()
            mock_orch.target_user_id = orch_target
        send_cfg = {}
        if cfg_target is not None:
            send_cfg["target_user"] = cfg_target
        return InitiativeBridge(orchestrator=mock_orch, send_config=send_cfg)

    def test_payload_takes_highest_priority(self):
        bridge = self._make_bridge(orch_target="orch_user", cfg_target="cfg_user")
        payload = {"target_user": "payload_user"}
        assert bridge._resolve_target_user(payload) == "payload_user"

    def test_payload_user_id_alias(self):
        """payload 里 user_id 也是合法别名。"""
        bridge = self._make_bridge(orch_target="orch_user", cfg_target="cfg_user")
        assert bridge._resolve_target_user({"user_id": "payload_uid"}) == "payload_uid"

    def test_cfg_second_priority(self):
        bridge = self._make_bridge(orch_target="orch_user", cfg_target="cfg_user")
        assert bridge._resolve_target_user({}) == "cfg_user"

    def test_orch_third_priority(self):
        bridge = self._make_bridge(orch_target="orch_user", cfg_target=None)
        assert bridge._resolve_target_user({}) == "orch_user"

    def test_no_source_returns_none(self):
        bridge = self._make_bridge(orch_target=None, cfg_target=None)
        assert bridge._resolve_target_user({}) is None


# =====================================================================
# 8. 新版发送模块接口完整性（src/runtime/initiative_sender.py）
# =====================================================================

class TestNewInitiativeSenderModule:
    """验证新版 initiative_sender 模块提供完整接口 + 旧版根文件已删除。"""

    def test_root_initiative_sender_deleted(self):
        """Phase 7.2.1-p1 迁移后：根目录不应再存在 initiative_sender.py。"""
        root_sender = PROJECT_ROOT / "initiative_sender.py"
        assert not root_sender.exists(), \
            (f"旧版 initiative_sender.py 未删除! 路径: {root_sender}\n"
             f"请确认 Phase 7.2.1-p1 单一路径迁移已完整执行。")

    def test_runtime_initiative_sender_exists(self):
        """新版 src/runtime/initiative_sender.py 应存在。"""
        new_sender = PROJECT_ROOT / "src" / "runtime" / "initiative_sender.py"
        assert new_sender.exists(), f"新版 initiative_sender 模块缺失: {new_sender}"

    def test_module_exports_flatten_and_build(self):
        """导出 flatten_initiative_config / build_send_config_from_flat / send_private_msg。"""
        from src.runtime.initiative_sender import (
            flatten_initiative_config,
            build_send_config_from_flat,
            send_private_msg,
        )
        assert callable(flatten_initiative_config)
        assert callable(build_send_config_from_flat)
        assert callable(send_private_msg)

    def test_flatten_merges_initiative_section(self):
        """flatten_initiative_config：initiative 子段提升到顶层。"""
        from src.runtime.initiative_sender import flatten_initiative_config
        raw = {
            "other_key": "other_value",
            "initiative": {
                "enabled": True,
                "api_type": "astrbot",
                "initiative_cooldown_seconds": 45,
            },
        }
        flat = flatten_initiative_config(raw)
        assert flat.get("enabled") is True
        assert flat.get("api_type") == "astrbot"
        assert flat.get("initiative_cooldown_seconds") == 45
        # 非 initiative 段原值保留
        assert flat.get("other_key") == "other_value"

    def test_flatten_env_overrides_apply(self, monkeypatch):
        """flatten_initiative_config：环境变量覆盖生效。"""
        from src.runtime.initiative_sender import flatten_initiative_config
        monkeypatch.setenv("TARGET_USER_QQ", "999888777")
        monkeypatch.setenv("API_TYPE", "astrbot")
        flat = flatten_initiative_config({})
        assert flat.get("target_user_qq") == "999888777"
        assert flat.get("api_type") == "astrbot"

    def test_build_send_config_from_flat_maps_correctly(self):
        """build_send_config_from_flat：扁平 cfg 映射到 InitiativeBridge send_config。"""
        from src.runtime.initiative_sender import build_send_config_from_flat
        flat = {
            "api_type": "astrbot",
            "onebot_url": "http://onebot:3000",
            "onebot_token": "secret",
            "astrbot_url": "http://astrbot:11451",
            "target_user_qq": "366648462",
            "request_timeout": 15.0,
        }
        send_cfg = build_send_config_from_flat(flat)
        assert send_cfg["api_type"] == "astrbot"
        assert send_cfg["onebot_url"] == "http://onebot:3000"
        assert send_cfg["onebot_token"] == "secret"
        assert send_cfg["astrbot_url"] == "http://astrbot:11451"
        assert send_cfg["target_user"] == "366648462"
        assert send_cfg["request_timeout"] == 15.0


# =====================================================================
# 9. ActionDispatcher 注册（使用 mock bridge 验证 register）
# =====================================================================

class TestInitiativeBridgeRegister:
    """register() 注册到 ActionDispatcher 的行为。"""

    def test_register_hooks_action_handler(self, monkeypatch):
        """register() 应调用 bridge.register_action_handler('send_message', handler)。"""
        from src.runtime.initiative_bridge import InitiativeBridge

        call_log = []

        class _FakeBridge:
            def register_action_handler(self, action_type, handler):
                call_log.append(("register", action_type, handler))

            def mark_action_handlers_ready(self):
                call_log.append(("mark_ready",))

        fake = _FakeBridge()
        # patch get_runtime_bridge() 返回 fake
        import src.runtime.initiative_bridge as ib_mod
        monkeypatch.setattr(ib_mod, "get_runtime_bridge", lambda: fake)

        bridge = InitiativeBridge(orchestrator=None, send_config={})
        assert bridge.is_registered is False
        ok = bridge.register()
        assert ok is True
        assert bridge.is_registered is True
        assert len(call_log) == 2
        assert call_log[0][0] == "register"
        assert call_log[0][1] == "send_message"
        assert callable(call_log[0][2])  # handler 是可调用对象
        assert call_log[1] == ("mark_ready",)

    def test_register_twice_is_idempotent(self, monkeypatch):
        """重复 register() 不会重复注册。"""
        from src.runtime.initiative_bridge import InitiativeBridge

        reg_count = 0

        class _FakeBridge:
            def register_action_handler(self, *a, **kw):
                nonlocal reg_count
                reg_count += 1

            def mark_action_handlers_ready(self):
                pass

        import src.runtime.initiative_bridge as ib_mod
        monkeypatch.setattr(ib_mod, "get_runtime_bridge", lambda: _FakeBridge())

        bridge = InitiativeBridge(orchestrator=None)
        bridge.register()
        bridge.register()  # 第二次
        assert reg_count == 1, f"重复注册不应触发多次 register_action_handler: {reg_count}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
