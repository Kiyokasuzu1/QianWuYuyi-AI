# -*- coding: utf-8 -*-
"""
tests/test_phase402_gate1_logging.py

Phase 4.0.2 Gate1: 17 阶段日志必须出现。

验收标准:
    RuntimeCore.process() 必须依次输出:
        "RuntimeCore.process start"
        "Stage 0 CONTROL_CHECK"
        "Stage 1 RECEIVE_EVENT"
        ...
        "Stage 14 RESPONSE_GENERATION"
        "Stage 15 GUARD_CHAIN"
        "Stage 16 RESPONSE"
        "RuntimeCore.process end"
    且当 Stage14 成功生成 reply 时出现 "Runtime reply generated"。

设计: 用 logging Handler 捕获本进程所有 info 日志,断言 17 阶段按顺序出现。
"""
from __future__ import annotations

import logging
import threading
import unittest
from unittest.mock import MagicMock

from src.runtime.events import Event


class _MemoryHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[str] = []
        self._lock = threading.RLock()

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock:
            self.records.append(record.getMessage())

    def clear(self) -> None:
        with self._lock:
            self.records.clear()


class TestPhase402Gate1StageLogging(unittest.TestCase):
    """Gate1: 17 阶段日志按顺序出现。"""

    @classmethod
    def setUpClass(cls) -> None:
        # 提升 lifecycle_executor + runtime_core 的 logger level 到 INFO
        for name in ("src.runtime.lifecycle_executor", "src.runtime.runtime_core"):
            logging.getLogger(name).setLevel(logging.INFO)

    def setUp(self) -> None:
        self.handler = _MemoryHandler()
        # 挂到 root 捕获所有日志
        root_logger = logging.getLogger()
        root_logger.addHandler(self.handler)
        self._old_level = root_logger.level
        root_logger.setLevel(logging.INFO)

    def tearDown(self) -> None:
        root_logger = logging.getLogger()
        root_logger.removeHandler(self.handler)
        root_logger.setLevel(self._old_level)

    # -----------------------------------------------------------------
    # Gate1-A: process() 开始/结束日志
    # -----------------------------------------------------------------
    def test_gate1_process_start_end_logs_present(self):
        """RuntimeCore.process start/end 必须出现在日志里。"""
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        # 让 Stage14 返回固定回复(保证 Runtime reply generated 出现)
        mock_engine = MagicMock()
        mock_engine.generate.return_value = "你好,这是从 Runtime 生成的回复"
        core._orchestrator_engine_ref = mock_engine  # type: ignore[attr-defined]

        try:
            core.start()
            ev = Event(type="user_input", source="user", payload={"text": "嗨"})
            ctx = core.process(ev)
        finally:
            try:
                core.stop()
            except Exception:
                pass

        logs = self.handler.records
        self.assertTrue(
            any("RuntimeCore.process start" in line for line in logs),
            f"未找到 RuntimeCore.process start 日志 | 当前日志样本: {logs[:8]}",
        )
        self.assertTrue(
            any("RuntimeCore.process end" in line for line in logs),
            f"未找到 RuntimeCore.process end 日志 | 当前日志样本: {logs[-8:]}",
        )
        final = getattr(ctx, "_final_reply", None)
        if final and str(final).strip():
            self.assertTrue(
                any("Runtime reply generated" in line for line in logs),
                "有 final reply,但未出现 Runtime reply generated 日志",
            )

    # -----------------------------------------------------------------
    # Gate1-B: 17 阶段日志按顺序出现
    # -----------------------------------------------------------------
    def test_gate1_17_stages_appear_in_order(self):
        """Stage 0..16 日志必须按顺序出现。"""
        from src.runtime.stages import RUNTIME_LIFECYCLE_ORDER
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        # 让 Stage14 快速返回(避免外部依赖)
        mock_engine = MagicMock()
        mock_engine.generate.return_value = "ok"
        core._orchestrator_engine_ref = mock_engine  # type: ignore[attr-defined]

        try:
            core.start()
            ev = Event(type="user_input", source="user", payload={"text": "hi"})
            core.process(ev)
        finally:
            try:
                core.stop()
            except Exception:
                pass

        logs = self.handler.records

        # 期望出现的顺序: Stage 0 CONTROL_CHECK → Stage 1 RECEIVE_EVENT → ...
        expected_sequence: list[str] = []
        for idx, stage in enumerate(RUNTIME_LIFECYCLE_ORDER):
            expected_sequence.append(f"Stage {idx} {stage.name}")

        # 贪心匹配: 按顺序逐个出现
        pos = 0
        matched_stages: list[str] = []
        for line in logs:
            if pos >= len(expected_sequence):
                break
            target = expected_sequence[pos]
            # 格式: "Stage N NAME" 精确包含
            if target in line:
                matched_stages.append(target)
                pos += 1

        self.assertEqual(
            pos, len(expected_sequence),
            (
                f"未按顺序匹配到全部 17 阶段日志。"
                f" 已匹配={matched_stages};"
                f" 未匹配起始于={expected_sequence[pos] if pos < len(expected_sequence) else None};"
                f" 日志样本={[l for l in logs if l.startswith('Stage ')][:25]}"
            ),
        )

    # -----------------------------------------------------------------
    # Gate1-C: Stage14 RESPONSE_GENERATION 必须存在于日志中
    # -----------------------------------------------------------------
    def test_gate1_stage14_response_generation_logged(self):
        """显式检查关键阶段 Stage 14 RESPONSE_GENERATION 出现在日志里。"""
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        mock_engine = MagicMock()
        mock_engine.generate.return_value = "hello"
        core._orchestrator_engine_ref = mock_engine  # type: ignore[attr-defined]

        try:
            core.start()
            ev = Event(type="user_input", source="user", payload={"text": "x"})
            core.process(ev)
        finally:
            try:
                core.stop()
            except Exception:
                pass

        logs = self.handler.records
        stage14_lines = [l for l in logs if "Stage 14" in l and "RESPONSE_GENERATION" in l]
        self.assertGreaterEqual(
            len(stage14_lines), 1,
            f"未出现 Stage 14 RESPONSE_GENERATION 日志。所有 Stage 行: {[l for l in logs if l.startswith('Stage ')]}",
        )


if __name__ == "__main__":
    unittest.main()
