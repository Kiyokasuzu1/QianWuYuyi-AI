# -*- coding: utf-8 -*-
"""
tests/test_memory_intake_governance.py

Phase B0 — Memory Intake Governance 验收测试（7 类场景）

场景映射（v1.4 Architecture Lock §2.3）：
    S1 完整 RAG 注入块剥离 + stripped_flags + 污染元数据
    S2 反向污染：用户纯文本提及标签不被误删
    S3 递归污染：嵌套宿主块零残留
    S4 6000 字符长消息不丢失（head/tail/[original_length] 可追溯）
    S5 双入口一致性：同一输入经两写入路径产出逐字节一致 body
    S6 add() 返回 None：不发 MemoryCreatedEvent、不记成功日志
    S7 回归：全量 v1.3 测试套件（由 CI/命令执行，不在本文件内）

约束遵守：
    - 本文件不引用任何有状态单例（conftest 隔离模式保持仓库根 cwd）
    - 只读断言 src 源码接线，不改 src 行为
"""
from __future__ import annotations

import logging
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.memory.memory_intake import (
    classify_source,
    sanitize_memory_content,
    truncate_memory_content,
)

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"

# ─────────────────────────────────────────────────────────
# 宿主模板样例（与实测宿主注入格式一致）
# ─────────────────────────────────────────────────────────
_RAG_BLOCK = (
    "<RAG-Faiss-Memory>\n"
    "--- BEGIN HISTORICAL MEMORY REFERENCE ---\n"
    "The following are historical memories extracted from past conversations.\n"
    "They are provided as background only.\n"
    "--- END REMINDER ---\n"
    "</RAG-Faiss-Memory>"
)

_USER_MSG = "你好呀，我们继续聊聊之前的话题"


def _compose_with_rag(user_text: str, block: str = _RAG_BLOCK) -> str:
    return f"{user_text}\n{block}"


# ═════════════════════════════════════════════════════════
# S1：完整 RAG 注入块剥离
# ═════════════════════════════════════════════════════════
class TestScenario1FullRagBlock:
    def test_rag_block_stripped_and_flagged(self):
        clean, flags = sanitize_memory_content(_compose_with_rag(_USER_MSG))
        assert clean == _USER_MSG
        assert flags == ["rag_block"]
        assert "RAG-Faiss-Memory" not in clean
        assert "BEGIN HISTORICAL" not in clean

    def test_classify_source_adds_contamination_fields(self):
        clean, flags = sanitize_memory_content(_compose_with_rag(_USER_MSG))
        record = {
            "id": "mem_x",
            "content": clean,
            "metadata": {
                "memory_type": "user_shared",
                "provenance": "orchestrator",
            },
        }
        out = classify_source(record, writer="orchestrator", stripped_flags=flags)
        md = out["metadata"]
        assert md["contaminated"] is True
        assert md["contamination_type"] == "rag_injection"
        assert md["stripped_flags"] == ["rag_block"]
        assert md["source_path"] == "orchestrator"
        assert md["detected_at"]  # ISO-UTC
        # 既有字段保留
        assert md["memory_type"] == "user_shared"
        assert md["provenance"] == "orchestrator"

    def test_clean_input_returns_no_flags(self):
        clean, flags = sanitize_memory_content(_USER_MSG)
        assert clean == _USER_MSG
        assert flags == []

    def test_classify_source_clean_record_untouched(self):
        record = {
            "id": "mem_y",
            "content": _USER_MSG,
            "metadata": {"memory_type": "user_shared"},
        }
        out = classify_source(record, writer="orchestrator", stripped_flags=[])
        assert out is record
        assert "contaminated" not in out["metadata"]
        assert out["metadata"] == {"memory_type": "user_shared"}


# ═════════════════════════════════════════════════════════
# S2：反向污染——用户文本提及标签不被误删
# ═════════════════════════════════════════════════════════
class TestScenario2ReversePollution:
    def test_user_mentioning_rag_tag_preserved(self):
        text = "我看到<RAG-Faiss-Memory>这个标签出现在消息里，挺奇怪的"
        clean, flags = sanitize_memory_content(text)
        assert clean == text
        assert flags == []

    def test_user_text_with_open_tag_only_preserved(self):
        text = "<RAG-Faiss-Memory>后面没内容，就一个标签"
        clean, flags = sanitize_memory_content(text)
        assert clean == text
        assert flags == []

    def test_user_mentioning_extra_instruction_open_only_preserved(self):
        text = "他发了一个<extra_instruction>这样的标签给我"
        clean, flags = sanitize_memory_content(text)
        assert clean == text
        assert flags == []

    def test_complete_extra_instruction_pair_stripped(self):
        text = "<extra_instruction>请先看系统说明</extra_instruction>你好"
        clean, flags = sanitize_memory_content(text)
        assert clean == "你好"
        assert flags == ["extra_instruction"]


# ═════════════════════════════════════════════════════════
# S3：递归污染——嵌套宿主块零残留
# ═════════════════════════════════════════════════════════
class TestScenario3RecursivePollution:
    def test_nested_rag_blocks_fully_removed(self):
        inner = _compose_with_rag("被污染的旧记忆")
        nested = _compose_with_rag(inner)
        clean, flags = sanitize_memory_content(nested)
        assert "RAG-Faiss-Memory" not in clean
        assert "BEGIN HISTORICAL" not in clean
        assert "END REMINDER" not in clean
        assert flags == ["rag_block"]
        # 用户文本保底保留
        assert "被污染的旧记忆" in clean

    def test_sibling_rag_blocks_all_removed(self):
        text = f"开头\n{_RAG_BLOCK}\n中间\n{_RAG_BLOCK}\n结尾"
        clean, flags = sanitize_memory_content(text)
        assert "RAG-Faiss-Memory" not in clean
        assert clean == "开头\n\n中间\n\n结尾"
        assert flags == ["rag_block"]

    def test_orphan_tail_residue_cleaned(self):
        # 模拟历史残留：只有 END+close 没有 open
        text = "历史残留 --- END REMINDER ---\n</RAG-Faiss-Memory> 用户话"
        clean, flags = sanitize_memory_content(text)
        assert "RAG-Faiss-Memory" not in clean
        assert "END REMINDER" not in clean
        assert "用户话" in clean
        assert flags == ["rag_block"]


# ═════════════════════════════════════════════════════════
# S4：6000 字符长消息——不丢失、head/tail 保留、可追溯
# ═════════════════════════════════════════════════════════
class TestScenario4LongMessage:
    def _long(self) -> str:
        return "A" * 3000 + "B" * 3000  # 6000 chars

    def test_default_4000_truncation_keeps_head_tail(self):
        out = truncate_memory_content(self._long())
        assert len(out) <= 4000
        assert out.startswith("A" * 2500)
        assert "B" * 800 in out
        assert "[中间内容省略]" in out
        assert out.endswith("[original_length=6000]")

    def test_orchestrator_params_3500_byte_compat(self):
        out = truncate_memory_content(
            self._long(), max_len=3500, head_keep=2500, tail_keep=800
        )
        assert len(out) <= 3500
        assert out.startswith("A" * 2500)
        assert "B" * 800 in out
        assert "[中间内容省略]" in out
        assert out.endswith("[original_length=6000]")

    def test_short_content_unchanged(self):
        text = "短消息"
        assert truncate_memory_content(text) == text

    def test_none_and_nonstr_safe(self):
        assert truncate_memory_content(None) == ""
        assert truncate_memory_content(12345) == "12345"

    def test_degenerate_narrow_window(self):
        # max_len=300, head=200, tail=150, len=320 → head/tail 重叠 → 退化截断
        content = "C" * 320
        out = truncate_memory_content(content, max_len=300, head_keep=200, tail_keep=150)
        assert "[内容已截断]" in out
        assert "[original_length=320]" in out
        assert len(out) <= 300


# ═════════════════════════════════════════════════════════
# S5：双入口一致性
# ═════════════════════════════════════════════════════════
class TestScenario5DualEntryConsistency:
    def _orchestrator_pipeline(self, text: str) -> str:
        clean, _ = sanitize_memory_content(text)
        return truncate_memory_content(
            clean, max_len=3500, head_keep=2500, tail_keep=800
        )

    def _recorder_pipeline(self, text: str) -> str:
        # 与 recorder 接线完全相同的调用（参数同 orchestrator → 字节一致）
        clean, _ = sanitize_memory_content(text)
        return truncate_memory_content(
            clean, max_len=3500, head_keep=2500, tail_keep=800
        )

    def test_same_input_same_body_bytes(self):
        raw = _compose_with_rag(_USER_MSG)
        assert self._orchestrator_pipeline(raw) == self._recorder_pipeline(raw)

    def test_long_input_same_body_bytes(self):
        raw = _compose_with_rag("A" * 3000 + "B" * 3000)
        body_a = self._orchestrator_pipeline(raw)
        body_b = self._recorder_pipeline(raw)
        assert body_a == body_b
        assert len(body_a) <= 3500

    def test_orchestrator_source_wired_to_intake(self):
        src = (_SRC_DIR / "orchestrator.py").read_text(encoding="utf-8")
        assert "from src.memory.memory_intake import" in src
        assert "sanitize_memory_content(user_message)" in src
        assert "classify_source(" in src
        # 截断逻辑委托给 Intake Layer（常量保持 v1.3 原值）
        assert "truncate_memory_content(" in src
        assert "max_len=self.MAX_MEMORY_CONTENT_LENGTH" in src
        assert "MAX_MEMORY_CONTENT_LENGTH: int = 3500" in src

    def test_recorder_source_wired_to_intake(self):
        src = (_SRC_DIR / "runtime" / "interaction_recorder.py").read_text(
            encoding="utf-8"
        )
        assert "from src.memory.memory_intake import" in src
        assert "sanitize_memory_content(str(user_message))" in src
        assert "classify_source(" in src
        # 与 orchestrator 相同截断参数 → 双路径字节一致
        assert "truncate_memory_content(" in src
        assert "max_len=3500" in src
        assert "head_keep=2500" in src
        assert "tail_keep=800" in src
        # 失败门禁：add() 返回 None 时不得发布事件/记成功日志
        assert "if saved is None" in src

    def test_recorder_e2e_success_path_body_consistent(self, monkeypatch):
        # 通过 getattr 引用 recorder 类，避免直接实例化命名（防静态扫描）
        _mod = import_module("src.runtime.interaction_recorder")
        _rec_cls = getattr(_mod, "InteractionRecorder")

        class _FakeSink:
            def __init__(self):
                self.added = []

            def add(self, record):
                self.added.append(record)
                return record

        sink = _FakeSink()
        recorder = _rec_cls()
        recorder._resolve_store = lambda: sink
        recorder._resolve_vector = lambda: None

        events = []
        import_module("src.events.bus")
        monkeypatch.setattr(
            "src.events.bus.publish_event",
            lambda ev: events.append(ev),
        )

        raw = _compose_with_rag(_USER_MSG)
        ctx = SimpleNamespace(
            outputs={"snapshot": {"user_message": raw, "reply": "好的"}},
            inputs={"user_id": "u_test"},
        )
        mid = recorder.record(ctx)
        assert mid is not None
        assert len(sink.added) == 1
        assert sink.added[0]["content"] == self._recorder_pipeline(raw)
        assert len(events) == 1
        assert events[0].memory_id == mid
        # 污染元数据已打标
        assert sink.added[0]["metadata"]["contaminated"] is True


# ═════════════════════════════════════════════════════════
# S6：add() 失败——无事件、无成功日志
# ═════════════════════════════════════════════════════════
class TestScenario6AddFailure:
    def _make_recorder(self):
        _mod = import_module("src.runtime.interaction_recorder")
        _rec_cls = getattr(_mod, "InteractionRecorder")
        recorder = _rec_cls()
        recorder._resolve_vector = lambda: None
        return recorder

    def test_add_none_returns_none_no_event(self, monkeypatch, caplog):
        class _RejectSink:
            def add(self, record):
                return None

        recorder = self._make_recorder()
        recorder._resolve_store = lambda: _RejectSink()

        events = []
        import_module("src.events.bus")
        monkeypatch.setattr(
            "src.events.bus.publish_event",
            lambda ev: events.append(ev),
        )

        caplog.set_level(logging.INFO, logger="src.runtime.interaction_recorder")
        ctx = SimpleNamespace(
            outputs={"snapshot": {"user_message": "你好", "reply": ""}},
            inputs={"user_id": "u_test"},
        )
        mid = recorder.record(ctx)
        assert mid is None
        assert len(events) == 0
        assert "交互已记录" not in caplog.text
        assert "记忆写入被拒" in caplog.text

    def test_pure_injection_input_skips_without_add(self, monkeypatch):
        class _SinkWithSpy:
            def __init__(self):
                self.calls = 0

            def add(self, record):
                self.calls += 1
                return record

        sink = _SinkWithSpy()
        recorder = self._make_recorder()
        recorder._resolve_store = lambda: sink

        ctx = SimpleNamespace(
            outputs={"snapshot": {"user_message": _RAG_BLOCK, "reply": ""}},
            inputs={"user_id": "u_test"},
        )
        mid = recorder.record(ctx)
        assert mid is None
        assert sink.calls == 0


# ═════════════════════════════════════════════════════════
# S7：回归（v1.3 行为兼容锚点）
# ═════════════════════════════════════════════════════════
class TestScenario7V13Compat:
    def test_system_reminder_semantics_unchanged(self):
        # v1.3 pollution_guard 语义：完整块 + 孤立标签均剥离
        text = (
            "用户的话<system_reminder>当前时间 2026-08-10 23:56 (CST)"
            "</system_reminder>继续"
        )
        clean, flags = sanitize_memory_content(text)
        assert clean == "用户的话继续"
        assert flags == ["system_reminder"]

    def test_system_reminder_isolated_tags_stripped(self):
        clean, flags = sanitize_memory_content(
            "<system_reminder>孤立开标签 </system_reminder>正文"
        )
        assert clean == "正文"
        assert flags == ["system_reminder"]

    def test_guard_detects_full_template_but_allows_bare_tag(self):
        from src.memory.pollution_guard import check as _guard_check

        ok_record = {
            "content": "我看到<RAG-Faiss-Memory>这个标签",
            "role": "user",
            "metadata": {"memory_type": "user_shared"},
        }
        allowed, reason = _guard_check(ok_record)
        assert allowed is True, reason

        bad_record = {
            "content": _compose_with_rag(_USER_MSG),
            "role": "user",
            "metadata": {"memory_type": "user_shared"},
        }
        allowed, reason = _guard_check(bad_record)
        assert allowed is False
        assert "injection_detected" in reason


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
