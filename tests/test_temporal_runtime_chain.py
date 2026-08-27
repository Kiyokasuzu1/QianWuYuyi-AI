# -*- coding: utf-8 -*-
"""
tests/test_temporal_runtime_chain.py

Phase B1b 接入点修正 — Runtime 链测试。

覆盖（v1.4 B1b 修正方案）：
    1. 完整链路（ResponseAdapter → ResponseEngine → PromptBuilder）收到 temporal_context
    2. shadow：日志存在（mode/chars/last_source/last_interaction/budget）+ Prompt 无 temporal block
    3. off：Prompt 与修改前一致（旧裸时间行、无块）
    4. active：Prompt 出现 temporal block
    5. 无 chat_memories：不 crash（last_source=none）
    6. 未来时间：不出现负离线时间
    7. temporal 异常：全部隔离，降级旧时间行，回复不受影响
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.runtime.adapters.response_adapter import ResponseRequest

UTC = timezone.utc
ADAPTER_LOGGER = "src.runtime.adapters.impl.response_adapter_impl"


def _past_ts(hours: float = 3.0) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _set_mode(monkeypatch, mode: str) -> None:
    import src.config as cfg

    monkeypatch.setattr(
        cfg,
        "_config",
        {"temporal": {"temporal_context_mode": mode, "temporal_context_budget": 400}},
    )


class _FakeLLM:
    def __init__(self):
        self.messages = None

    def generate(self, messages):
        self.messages = messages
        return "ok"


class _CapEngine:
    """捕获 adapter → engine.generate 的 kwargs（验证透传参数）。"""

    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return "ok"


def _real_engine():
    from src.response.engine import ResponseEngine

    engine = ResponseEngine()
    fake_llm = _FakeLLM()
    engine._llm = fake_llm  # 无网络
    return engine, fake_llm


def _adapter(engine):
    from src.runtime.adapters.impl.response_adapter_impl import ResponseAdapterImpl

    adapter = ResponseAdapterImpl(response_engine=engine)
    adapter.attach()
    return adapter


def _request(chat_memories=None):
    return ResponseRequest(
        user_input="你好", chat_memories=list(chat_memories or [])
    )


# ═════════════════════════════════════════════════════════
# 1) 完整链路：active 注入 → PromptBuilder 收到块
# ═════════════════════════════════════════════════════════
class TestFullChain:
    def test_active_chain_injects_block(self, monkeypatch):
        _set_mode(monkeypatch, "active")
        engine, fake_llm = _real_engine()
        adapter = _adapter(engine)
        reply = adapter.generate(_request([{"timestamp": _past_ts()}]))
        assert reply == "ok"
        system = fake_llm.messages[0]["content"]
        assert "当前时间：" in system
        assert "用户最近一次消息" in system
        assert "离线间隔" in system
        assert "(CST)" in system

    def test_off_chain_keeps_old_behavior(self, monkeypatch):
        _set_mode(monkeypatch, "off")
        engine, fake_llm = _real_engine()
        adapter = _adapter(engine)
        reply = adapter.generate(_request([{"timestamp": _past_ts()}]))
        assert reply == "ok"
        system = fake_llm.messages[0]["content"]
        assert "当前时间：" in system  # 旧裸时间行
        assert "用户最近一次消息" not in system
        assert "离线间隔" not in system

    def test_shadow_chain_prompt_has_no_block(self, monkeypatch, caplog):
        _set_mode(monkeypatch, "shadow")
        caplog.set_level(logging.INFO, logger=ADAPTER_LOGGER)
        engine, fake_llm = _real_engine()
        adapter = _adapter(engine)
        reply = adapter.generate(_request([{"timestamp": _past_ts()}]))
        assert reply == "ok"
        system = fake_llm.messages[0]["content"]
        assert "当前时间：" in system
        assert "用户最近一次消息" not in system
        assert "离线间隔" not in system
        # shadow 日志五要素
        log_text = caplog.text
        assert "[TemporalContext][shadow]" in log_text
        assert "mode=shadow" in log_text
        assert "chars=" in log_text
        assert "last_source=memory" in log_text
        assert "last_interaction=" in log_text
        assert "budget=400" in log_text


# ═════════════════════════════════════════════════════════
# 2) 适配器透传参数（fake engine 捕获）
# ═════════════════════════════════════════════════════════
class TestAdapterPassThrough:
    def test_off_passes_none(self, monkeypatch):
        _set_mode(monkeypatch, "off")
        engine = _CapEngine()
        adapter = _adapter(engine)
        adapter.generate(_request([{"timestamp": _past_ts()}]))
        assert engine.calls[-1]["temporal_context"] is None

    def test_shadow_passes_none(self, monkeypatch):
        _set_mode(monkeypatch, "shadow")
        engine = _CapEngine()
        adapter = _adapter(engine)
        adapter.generate(_request([{"timestamp": _past_ts()}]))
        assert engine.calls[-1]["temporal_context"] is None

    def test_active_passes_block(self, monkeypatch):
        _set_mode(monkeypatch, "active")
        engine = _CapEngine()
        adapter = _adapter(engine)
        adapter.generate(_request([{"timestamp": _past_ts()}]))
        tc = engine.calls[-1]["temporal_context"]
        assert tc and tc.startswith("当前时间：")


# ═════════════════════════════════════════════════════════
# 3) 无 chat_memories / 未来时间 / 异常隔离
# ═════════════════════════════════════════════════════════
class TestEdgeCases:
    def test_no_chat_memories_no_crash(self, monkeypatch, caplog):
        _set_mode(monkeypatch, "shadow")
        caplog.set_level(logging.INFO, logger=ADAPTER_LOGGER)
        engine = _CapEngine()
        adapter = _adapter(engine)
        reply = adapter.generate(_request(None))
        assert reply == "ok"
        assert "last_source=none" in caplog.text

    def test_active_no_memories_block_has_only_current_time(self, monkeypatch):
        _set_mode(monkeypatch, "active")
        engine, fake_llm = _real_engine()
        adapter = _adapter(engine)
        adapter.generate(_request([]))
        system = fake_llm.messages[0]["content"]
        assert "当前时间：" in system
        assert "用户最近一次消息" not in system
        assert "离线间隔" not in system

    def test_future_timestamp_no_negative_offline(self, monkeypatch):
        _set_mode(monkeypatch, "active")
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        engine, fake_llm = _real_engine()
        adapter = _adapter(engine)
        adapter.generate(_request([{"timestamp": future}]))
        system = fake_llm.messages[0]["content"]
        assert "当前时间：" in system
        assert "用户最近一次消息" not in system  # 未来时间 → 省略两行，无负离线时间
        assert "离线间隔" not in system

    def test_temporal_exception_isolated(self, monkeypatch, caplog):
        _set_mode(monkeypatch, "active")
        import src.temporal.temporal_context as tc_module

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(tc_module, "resolve_temporal_context", _boom)
        caplog.set_level(logging.WARNING, logger=ADAPTER_LOGGER)
        engine, fake_llm = _real_engine()
        adapter = _adapter(engine)
        reply = adapter.generate(_request([{"timestamp": _past_ts()}]))
        assert reply == "ok"  # 回复不受影响
        system = fake_llm.messages[0]["content"]
        assert "当前时间：" in system  # 降级旧时间行
        assert "用户最近一次消息" not in system
        assert "[TemporalContext] 计算失败" in caplog.text


# ═════════════════════════════════════════════════════════
# 4) 接线静态断言
# ═════════════════════════════════════════════════════════
class TestWiringStatic:
    def test_adapter_and_engine_wired(self):
        root = Path(__file__).resolve().parents[1]
        adapter_src = (
            root / "src" / "runtime" / "adapters" / "impl" / "response_adapter_impl.py"
        ).read_text(encoding="utf-8")
        engine_src = (root / "src" / "response" / "engine.py").read_text(encoding="utf-8")
        assert "resolve_temporal_context" in adapter_src
        assert "[TemporalContext][shadow]" in adapter_src
        assert "temporal_context=_temporal_inject" in adapter_src
        assert "temporal_context=temporal_context" in engine_src
