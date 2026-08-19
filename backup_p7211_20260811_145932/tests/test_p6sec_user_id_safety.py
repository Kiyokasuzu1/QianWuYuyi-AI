#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 7.2.1-p6-sec: user_id 安全红线解析测试

验证安全承诺：
  T1. 真实 QQ 号格式（5~12 位数字）永远原样使用，不兜成 target
      - T1a: 已知白名单 QQ (366648462) → 直接用
      - T1b: 陌生人真实 QQ (123456789) → 直接用（不串进 target）
  T2. 默认 multi_user 模式下，占位符/缺省绝不兜底成 target_uid
      - T2a: user="default" → _unknown_sender
      - T2b: user=None / 不传 user 字段 → _unknown_sender
      - T2c: user="" (空字符串) → _unknown_sender
      - T2d: user="none" / "null" / "未知" → _unknown_sender
      - T2e: 4 位短数字 user="1234" → _unknown_sender（不符合 QQ 格式）
      - T2f: 13 位长数字 user="1234567890123" → _unknown_sender
  T3. 显式 single_user 模式允许兜底成 target（单用户专用场景）
      - T3a: single_user + user="default" → target_uid (366648462)
      - T3b: single_user + 不传 user → target_uid
      - T3c: single_user + 真实数字陌生人 QQ → 仍然用真实数字（single_user 只改占位符行为，不碰真实 QQ 号！）
  T4. smart_balanced 模式行为等同 multi_user（p6 红线：绝不再猜身份）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TARGET_UID = "366648462"
STRANGER_UID = "123456789"
UNKNOWN = "_unknown_sender"


def _make_body(user=None, user_id=None, message="测试消息 hello") -> dict:
    """构造 /v1/chat/completions 请求体。"""
    body = {
        "model": "yuyi",
        "messages": [{"role": "user", "content": message}],
    }
    if user is not None:
        body["user"] = user
    if user_id is not None:
        body["user_id"] = user_id
    return body


class _UserCapture:
    """捕获 handle_message / orchestrator.process 收到的 user_id。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []  # [(user_id, message), ...]

    def handle_message(self, user_id, message, **_kwargs):
        self.calls.append((str(user_id), str(message)))
        mock_hr = MagicMock()
        mock_hr.reply = f"[MOCK] user={user_id}"
        mock_hr.model = "phase4-mock"
        return mock_hr

    def orchestrator_process(self, message, user_id=None, **_kw):
        self.calls.append((str(user_id) if user_id is not None else "NONE", str(message)))
        return f"[ORCH-MOCK] user={user_id}"

    def last_user(self) -> str | None:
        return self.calls[-1][0] if self.calls else None


# ── 用 Flask test_client 打真请求，mock 掉真正的处理逻辑 ────────────
@pytest.fixture()
def client(monkeypatch):
    """返回 Flask test client，并 mock runtime_controller / orchestrator。"""
    cap = _UserCapture()

    # 先 import，拿到模块对象的引用
    import api_server as api_mod

    # Mock runtime_controller（Phase4 路径）
    fake_ctrl = MagicMock()
    fake_ctrl.handle_message.side_effect = cap.handle_message
    # Phase4 开关：默认为 True，走 runtime_controller 路径
    api_mod._runtime_controller = fake_ctrl
    # 同时让 pipeline 也 mock 掉，防止真的初始化
    api_mod._pipeline = None

    # Mock orchestrator
    fake_orch = MagicMock()
    fake_orch.process.side_effect = cap.orchestrator_process
    api_mod.orchestrator = fake_orch

    api_mod.app.config["TESTING"] = True
    with api_mod.app.test_client() as c:
        c._user_capture = cap  # type: ignore[attr-defined]
        yield c


def _post(client, payload: dict, cfg_overrides: dict | None = None):
    """发 POST /v1/chat/completions，并返回 (status_code, captured_user_id)。

    cfg_overrides 用于临时覆盖 load_config 返回值。"""
    cap: _UserCapture = client._user_capture  # type: ignore[attr-defined]
    cap.calls.clear()

    if cfg_overrides is None:
        resp = client.post(
            "/v1/chat/completions",
            data=json.dumps(payload),
            content_type="application/json",
        )
    else:
        # 通过 load_config patch 传入覆盖配置
        import api_server as api_mod

        real_load = api_mod.load_config

        def patched_load():
            base = real_load() if callable(real_load) else {}
            if isinstance(base, dict):
                # 递归合并：dict.update 对嵌套 dict 只做浅层，够用了
                for k, v in cfg_overrides.items():
                    if isinstance(v, dict) and isinstance(base.get(k), dict):
                        base[k].update(v)
                    else:
                        base[k] = v
            return base

        with patch.object(api_mod, "load_config", patched_load):
            # 同时清掉陌生人见过的全局标记，避免串测试
            globals_snapshot = dict(api_mod.__dict__)
            if "_seen_stranger_uid_ever" in globals_snapshot:
                api_mod._seen_stranger_uid_ever = False

            resp = client.post(
                "/v1/chat/completions",
                data=json.dumps(payload),
                content_type="application/json",
            )

    return resp.status_code, cap.last_user()


# ======================================================================
# T1. 真实 QQ 号格式永远原样使用
# ======================================================================
class TestT1RealQQNeverFallback:
    """p6 安全承诺：任何 mode 下，5~12 位纯数字绝不被改写。"""

    def test_t1a_target_qq_preserved(self, client):
        """T1a: 已知白名单 366648462 → user_id=366648462"""
        status, uid = _post(client, _make_body(user=TARGET_UID))
        assert status == 200, f"非 200：{status}"
        assert uid == TARGET_UID, f"已知目标 QQ 被改写：期望 {TARGET_UID}，实际 {uid}"

    def test_t1b_stranger_qq_preserved_multi_mode(self, client):
        """T1b: 陌生人 123456789（默认 multi_user 模式）→ 原样使用，不兜成 target"""
        status, uid = _post(client, _make_body(user=STRANGER_UID))
        assert status == 200
        assert uid == STRANGER_UID, (
            f"陌生人真实 QQ 被串进目标！期望 {STRANGER_UID}，实际 {uid}"
            f"（绝不能是 {TARGET_UID}！）"
        )
        assert uid != TARGET_UID, "CRITICAL: 陌生人 UID 被兜成 target_uid，身份串话漏洞！"

    def test_t1c_stranger_qq_preserved_single_mode(self, client):
        """T1c: single_user 模式 + 陌生人真实 QQ → 仍然原样使用（安全红线！single_user 只改占位符）"""
        cfg = {"user_id_resolve": {"mode": "single_user"}}
        status, uid = _post(client, _make_body(user=STRANGER_UID), cfg_overrides=cfg)
        assert status == 200
        assert uid == STRANGER_UID, (
            f"single_user 模式下陌生人真实 QQ 也被改写！期望 {STRANGER_UID}，实际 {uid}"
        )
        assert uid != TARGET_UID, "CRITICAL: single_user 也不该碰真实 QQ 号！"

    def test_t1d_stranger_qq_preserved_smart_mode(self, client):
        """T1d: smart_balanced 模式 + 陌生人真实 QQ → 原样使用"""
        cfg = {"user_id_resolve": {"mode": "smart_balanced"}}
        status, uid = _post(client, _make_body(user=STRANGER_UID), cfg_overrides=cfg)
        assert status == 200
        assert uid == STRANGER_UID
        assert uid != TARGET_UID


# ======================================================================
# T2. multi_user 模式：占位符/缺省 → _unknown_sender，不兜 target
# ======================================================================
class TestT2DefaultModeStrict:
    """multi_user 模式：身份未知就进独立沙盒，绝不猜。"""

    MULTI = {"user_id_resolve": {"mode": "multi_user"}}

    def test_t2a_user_default_str(self, client):
        status, uid = _post(client, _make_body(user="default"), cfg_overrides=self.MULTI)
        assert status == 200
        assert uid == UNKNOWN, f"user='default' 期望 {UNKNOWN}，实际 {uid}（不该是 {TARGET_UID}）"
        assert uid != TARGET_UID, "CRITICAL: user='default' 被兜成 target，multi_user 模式下不允许！"

    def test_t2b_no_user_field(self, client):
        """完全不传 user / user_id 字段 → _unknown_sender"""
        status, uid = _post(client, _make_body(), cfg_overrides=self.MULTI)
        assert status == 200
        assert uid == UNKNOWN, f"缺省 user 期望 {UNKNOWN}，实际 {uid}"
        assert uid != TARGET_UID

    def test_t2c_empty_string_user(self, client):
        status, uid = _post(client, _make_body(user=""), cfg_overrides=self.MULTI)
        assert status == 200
        assert uid == UNKNOWN

    def test_t2d_none_null_placeholder(self, client):
        for placeholder in ["none", "null", "None", "NULL", "未知", "anonymous"]:
            status, uid = _post(client, _make_body(user=placeholder), cfg_overrides=self.MULTI)
            assert status == 200, f"placeholder={placeholder} 非 200"
            assert uid == UNKNOWN, (
                f"placeholder={placeholder} 期望进未知沙盒，实际 {uid}"
            )
            assert uid != TARGET_UID

    def test_t2e_too_short_number(self, client):
        """4 位数字 1234 不符合 5~12 位 → 不认作 QQ 号"""
        status, uid = _post(client, _make_body(user="1234"), cfg_overrides=self.MULTI)
        assert status == 200
        assert uid == UNKNOWN, f"4 位数字不应识别为真实 QQ：实际 {uid}"

    def test_t2f_too_long_number(self, client):
        """13 位数字 → 不认作 QQ 号"""
        status, uid = _post(client, _make_body(user="1234567890123"), cfg_overrides=self.MULTI)
        assert status == 200
        assert uid == UNKNOWN, f"13 位数字不应识别为真实 QQ：实际 {uid}"

    def test_t2g_alphanumeric_not_qq(self, client):
        """带字母的 user='qq_366648462' → 不走真实 QQ 分支"""
        status, uid = _post(client, _make_body(user="qq_366648462"), cfg_overrides=self.MULTI)
        assert status == 200
        assert uid == UNKNOWN, f"非纯数字不应识别为真实 QQ：实际 {uid}"


# ======================================================================
# T3. single_user：显式模式允许兜底（但真实 QQ 仍不碰）
# ======================================================================
class TestT3SingleUserExplicitFallback:
    """仅 single_user 显式配置允许兜底。"""

    SINGLE = {"user_id_resolve": {"mode": "single_user"}}

    def test_t3a_single_user_default_fallback(self, client):
        status, uid = _post(client, _make_body(user="default"), cfg_overrides=self.SINGLE)
        assert status == 200
        assert uid == TARGET_UID, f"single_user 模式 default 应兜成 {TARGET_UID}，实际 {uid}"

    def test_t3b_single_user_no_field_fallback(self, client):
        status, uid = _post(client, _make_body(), cfg_overrides=self.SINGLE)
        assert status == 200
        assert uid == TARGET_UID

    def test_t3c_single_user_stranger_qq_not_overridden(self, client):
        """【重点】single_user 下遇到真实数字陌生人 QQ，必须仍然用真实数字，不能兜！"""
        status, uid = _post(client, _make_body(user=STRANGER_UID), cfg_overrides=self.SINGLE)
        assert status == 200
        assert uid == STRANGER_UID, (
            f"single_user + 陌生人真实 QQ：期望保留 {STRANGER_UID}，实际被改成了 {uid}"
        )
        assert uid != TARGET_UID, "CRITICAL: single_user 模式下陌生人真实 QQ 也不能兜成 target！"


# ======================================================================
# T4. smart_balanced 等同 multi_user（p6 新红线）
# ======================================================================
class TestT4SmartBalancedEqualsMultiUser:
    """p6-sec 后 smart_balanced 不再做「未见过陌生人就兜」的启发式。"""

    SMART = {"user_id_resolve": {"mode": "smart_balanced"}}

    def test_t4a_smart_default_not_fallback(self, client):
        status, uid = _post(client, _make_body(user="default"), cfg_overrides=self.SMART)
        assert status == 200
        assert uid == UNKNOWN, f"smart_balanced 模式 default 应进 {UNKNOWN}，实际 {uid}"
        assert uid != TARGET_UID, "p6 红线：smart_balanced 绝不兜底成 target！"

    def test_t4b_smart_no_field_not_fallback(self, client):
        status, uid = _post(client, _make_body(), cfg_overrides=self.SMART)
        assert status == 200
        assert uid == UNKNOWN
        assert uid != TARGET_UID

    def test_t4c_smart_stranger_qq_preserved(self, client):
        status, uid = _post(client, _make_body(user=STRANGER_UID), cfg_overrides=self.SMART)
        assert status == 200
        assert uid == STRANGER_UID
