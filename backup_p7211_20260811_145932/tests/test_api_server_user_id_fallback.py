"""
Phase 7.2.1-p5 — /v1/chat/completions user_id 强兜底单元测试

核心修复：Agent 客户端传 user="default"/"none"/"null"/空串 等无效字符串时，
不能把它们当"传了有效用户"直接用，必须兜到 config.target_user_id=366648462。
否则 Phase4RuntimeController 会读 data/users/default/ 下的陌生人记忆，
表现为「被动回复失忆 + 不喊我清清了」。

测试项（6 条）：
1. user=None（没传）                  → user_id=366648462 + fallback 日志
2. user=""（空字符串）                → user_id=366648462 + fallback 日志
3. user="default"（本次核心修复）     → user_id=366648462 + fallback 日志
4. user="None"/"none"                 → user_id=366648462 + fallback 日志
5. user="null"                        → user_id=366648462 + fallback 日志
6. user="366648462"（合法）           → user_id=366648462 + 不打 fallback 日志
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class TestChatCompletionsUserIdFallback:
    """用 Flask test_client 直接调用 /v1/chat/completions，通过 caplog 断言 user_id 解析结果。

    注意：不真正加载 Orchestrator / Phase4Controller，用 monkeypatch 屏蔽其构造，
    避免依赖外部 API key 和重量级初始化。我们只关心 user_id 解析和日志输出阶段是否正确。
    """

    BASE_BODY = {
        "messages": [
            {"role": "system", "content": "你是羽依。"},
            {"role": "user", "content": "你还记得我叫什么吗？"},
        ],
    }

    @pytest.fixture()
    def _patched_app(self, monkeypatch, tmp_path):
        # 打桩 load_config，保证兜底时 target_user_id=366648462
        import api_server as api_mod

        def fake_load_config():
            # 本测试语义：验证 default/none/null 等占位符会兜底到 target_user_id。
            # 这在 p6-sec 之后是「显式 single_user 模式」才有的行为。
            # Phase 7.2.1-p6-sec 把默认 mode 改成了 multi_user，所以 fixture 必须
            # 显式声明 single_user，才能匹配本测试顶部注释描述的预期。
            return {
                "target_user_id": "366648462",
                "user_id_resolve": {
                    # 显式 single_user：身份未知兜底成 366648462（对齐老通道）
                    "mode": "single_user",
                },
                "runtime": {"enabled": False, "phase4_enabled": False},
                "api": {"target_user_id": "366648462"},
            }

        monkeypatch.setattr(api_mod, "load_config", fake_load_config, raising=False)

        # 屏蔽重量级组件（它们在 user_id 解析之后才会执行，不关心返回值）
        class _FakeCtrl:
            async def handle_message(self, *a, **kw):
                return SimpleNamespace(text="（测试回复）", stream=False)
        monkeypatch.setattr(api_mod, "phase4_controller", _FakeCtrl(), raising=False)

        class _FakeOrch:
            target_user_id = None
            def process(self, *a, **kw):
                return "（测试回复）"
        monkeypatch.setattr(api_mod, "orchestrator", _FakeOrch(), raising=False)

        for attr in ("runtime_controller", "runtime_pipeline", "runtime_core"):
            monkeypatch.setattr(api_mod, attr, MagicMock(), raising=False)

        yield api_mod.app

    # ------------------------------------------------------------------
    # 6 个场景
    # ------------------------------------------------------------------
    def _post(self, app, user_val):
        body = dict(self.BASE_BODY)
        if user_val is not None:
            body["user"] = user_val
        client = app.test_client()
        with client as c:
            return c.post(
                "/v1/chat/completions",
                json=body,
                content_type="application/json",
            )

    @pytest.mark.parametrize(
        "user_val, expect_fallback_log",
        [
            (None, True),                      # 1. 不传 user
            ("", True),                        # 2. 空串
            ("default", True),                 # 3. 「本次核心修复」default 字符串
            ("Default", True),                 # 3b. 大小写不敏感
            ("none", True),                    # 4. none
            ("None", True),                    # 4b. None 字符串
            ("null", True),                    # 5. null
            ("NULL", True),                    # 5b.
            ("unknown", True),                 # 5c. 其他无效值
            ("guest", True),                   # 5d.
            ("366648462", False),              # 6. 合法用户 → 不打 fallback
            (366648462, False),                # 6b. 整数形式
        ],
    )
    def test_user_id_resolves_to_real_uid(
        self, _patched_app, caplog, user_val, expect_fallback_log,
    ):
        caplog.set_level(logging.INFO, logger="__main__")
        caplog.set_level(logging.INFO, logger="api_server")

        self._post(_patched_app, user_val)

        all_text = "\n".join(rec.message for rec in caplog.records)

        # 核心断言：必须出现「处理用户 366648462 消息」，绝对不能出现「处理用户 default」
        assert "处理用户 366648462 消息" in all_text, (
            f"user={user_val!r} 没落到 366648462！日志={all_text[-800:]}"
        )
        assert "处理用户 default 消息" not in all_text, (
            f"user={user_val!r} 居然还出现 default！日志={all_text[-800:]}"
        )

        if expect_fallback_log:
            # p6-sec 以后 single_user 模式下的兜底日志 tag = [user_id-fallback-single]
            # （multi_user 模式是 [user_id-unknown-multi]，不会兜到 target）
            assert "[user_id-fallback-single]" in all_text, (
                f"user={user_val!r} 应触发 single_user 模式兜底，但没打到 [user_id-fallback-single] 日志。"
                f"日志={all_text[-800:]}"
            )
        else:
            # 合法用户 → 不能打任何 fallback 类日志（无论 fallback-single 还是 user_id-fallback）
            has_any_fallback_tag = (
                "[user_id-fallback-single]" in all_text or "[user_id-fallback]" in all_text
            )
            assert not has_any_fallback_tag, (
                f"user={user_val!r} 是合法用户，不应打 fallback 日志。日志={all_text[-800:]}"
            )
