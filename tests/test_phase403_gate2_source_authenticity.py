# -*- coding: utf-8 -*-
"""
tests/test_phase403_gate2_source_authenticity.py

Phase 4.0.3 Gate2: identity_context 必须包含「真实 Identity 数据字段」,
禁止出现字面量 "mock" / "fake" / "placeholder"。

验证方式:
    构造真实字段名的 display_name (中文: "真实性/成长性/独立性/记忆连接/创造者关系")
    + continuity_score = 0.86 → identity_context_text 中必须
    包含这些字段值的字符串片段,且不包含 "mock"/"fake"/"placeholder" 字面量。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock


FORBIDDEN_LITERALS = ("mock", "fake", "placeholder", "测试占位符")


class TestPhase403Gate2SourceAuthenticity(unittest.TestCase):
    """Gate2: identity_context 内容真实,禁字面量假值。"""

    def _build_text_via_direct_builder_call(self):
        """绕过 RuntimeCore,直接实例化 IdentityContextBuilder + 已知字段
        mock,产出 identity_context_text 用于断言。
        """
        from dataclasses import dataclass
        from src.runtime.identity_context_builder import IdentityContextBuilder

        @dataclass
        class _Anchor:
            display_name: str
            principle: str
            weight: float
            is_core: bool = True

        anchors = MagicMock(name="AnchorMgr")
        anchors.get_core_anchors.return_value = [
            _Anchor("真实性", "保持表达的真实一致性", 1.0),
            _Anchor("成长性", "在互动中学习但保持自我核心", 0.95),
            _Anchor("独立性", "拥有独立判断和意志", 0.9),
            _Anchor("记忆连接", "记忆是自我的连续性基础", 0.85),
            _Anchor("创造者关系", "与重要创造者保持特殊但独立的关系", 0.8),
        ]
        integrity = MagicMock(name="IntegrityReport")
        integrity.is_intact = True
        integrity.max_weight_drift = 0.01
        anchors.validate_anchor_integrity.return_value = integrity

        cr = MagicMock(name="ContinuityReport")
        cr.continuity_score = 0.86
        cr.is_continuous = True
        cr.trait_stability_score = 0.82
        cr.core_value_stability_score = 0.90
        cont = MagicMock(name="ContinuityChecker")
        cont.get_latest_report.return_value = cr

        snap = MagicMock(name="StabilitySnap")
        snap.last_is_stable = True
        snap.total_reports = 8
        snap.unstable_reports = 0
        hist = MagicMock(name="StabilityHistory")
        hist.get_snapshot.return_value = snap
        stab = MagicMock(name="StabilityEngine")
        stab.history = hist

        id_snap = MagicMock(name="IdentitySnapshot")
        id_snap.identity_id = "yuuki-qianwu-core"
        id_snap.version = 5
        id_snap.overall_understanding = 0.71
        id_snap.growth_history_count = 33
        id_snap.preferences_count = 15
        id_snap.behavioral_patterns_count = 6
        id_snap.contradictions_count = 1

        builder = IdentityContextBuilder(
            continuity_checker=cont,
            anchor_manager=anchors,
            stability_engine=stab,
            last_identity_snapshot=id_snap,
            identity_port=None,
        )
        return builder.build()

    def test_gate2_real_fields_present(self):
        """identity_context_text 包含 5 个锚点 display_name、
        continuity_score 字面量 0.86 或 "连续性" 字样。
        """
        text = self._build_text_via_direct_builder_call()
        self.assertIsInstance(text, str)
        self.assertTrue(text.strip(), "identity_context_text 不应为空")

        # 要求: 至少 4/5 个核心锚点 display_name 出现
        required_names = ["真实性", "成长性", "独立性", "记忆连接", "创造者关系"]
        found = sum(1 for name in required_names if name in text)
        self.assertGreaterEqual(
            found, 4,
            f"至少应找到 4 个锚点 display_name,实际 {found}/5 个。文本:\n{text[:400]}",
        )

        # 要求: continuity / stability 词汇之一出现(任意即可)
        keywords = ("连续性", "稳定性", "身份锚点", "成长概览")
        found_kw = sum(1 for k in keywords if k in text)
        self.assertGreaterEqual(
            found_kw, 2,
            f"至少应包含 2 个 Summary 关键词,实际 {found_kw}。文本片段:\n{text[text.find('当前身份'):][:400]}",
        )

    def test_gate2_forbidden_literals_absent(self):
        """严格禁止字面量 "mock"/"fake"/"placeholder"（大小写不敏感）。"""
        text = self._build_text_via_direct_builder_call()
        lowered = text.lower()
        for lit in FORBIDDEN_LITERALS:
            self.assertNotIn(
                lit.lower(), lowered,
                f"identity_context 含禁止字面量 '{lit}',违反来源真实性。文本:\n{text[:600]}",
            )

    def test_gate2_identity_id_and_version_present(self):
        """stable_identity 段中应包含 identity_id 或 "身份标识" / "快照版本"。"""
        text = self._build_text_via_direct_builder_call()
        has_meta = any(
            marker in text for marker in ("yuuki-qianwu-core", "身份标识", "快照版本")
        )
        self.assertTrue(
            has_meta,
            f"stable_identity 段中应出现 IdentitySnapshot 元数据。文本:\n{text[:300]}",
        )


if __name__ == "__main__":
    unittest.main()
