# -*- coding: utf-8 -*-
"""
tests/test_runtime_perception.py

Phase 3.8.x: 验证 Perception 模块基础能力

覆盖：
- FactSource 枚举的 5 个值 + trust_score / is_grounded
- Fact 构造校验（INFERENCE 必须低 confidence + 空 evidence）
- Fact 序列化 / 反序列化
- PerceptionGuard：
    * 全部 grounded → 不需要 hedge
    * 包含 INFERENCE → needs_hedge=True，wrap_reply 加 hedge
    * 全部 INFERENCE + min_grounded_ratio=1.0 → needs_refusal
    * assert_no_speculation 抛异常
    * 默认中英文模板
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.perception import (
    FactSource,
    USER_INPUT,
    MEMORY,
    VISION,
    SYSTEM,
    INFERENCE,
    Fact,
    FACT_SCHEMA_VERSION,
    PerceptionGuard,
    GuardReport,
)


# ============================================================
# FactSource
# ============================================================
class TestFactSource:
    def test_five_sources(self):
        assert FactSource.USER_INPUT.value == "user_input"
        assert FactSource.MEMORY.value == "memory"
        assert FactSource.VISION.value == "vision"
        assert FactSource.SYSTEM.value == "system"
        assert FactSource.INFERENCE.value == "inference"

    def test_aliases(self):
        assert USER_INPUT is FactSource.USER_INPUT
        assert MEMORY is FactSource.MEMORY
        assert VISION is FactSource.VISION
        assert SYSTEM is FactSource.SYSTEM
        assert INFERENCE is FactSource.INFERENCE

    def test_is_grounded(self):
        assert FactSource.USER_INPUT.is_grounded is True
        assert FactSource.MEMORY.is_grounded is True
        assert FactSource.VISION.is_grounded is True
        assert FactSource.SYSTEM.is_grounded is True
        assert FactSource.INFERENCE.is_grounded is False

    def test_trust_score_ordering(self):
        # 推测的信任度应低于 grounded
        assert FactSource.INFERENCE.trust_score < FactSource.MEMORY.trust_score
        assert FactSource.MEMORY.trust_score <= FactSource.USER_INPUT.trust_score

    def test_from_string(self):
        assert FactSource("user_input") is FactSource.USER_INPUT
        with pytest.raises(ValueError):
            FactSource("unknown")


# ============================================================
# Fact
# ============================================================
class TestFact:
    def test_basic_grounded_fact(self):
        f = Fact(content="用户说今天下雨", source=USER_INPUT, confidence=1.0)
        assert f.source is USER_INPUT
        assert f.is_inference is False
        assert f.trust_score == 1.0
        assert f.schema_version == FACT_SCHEMA_VERSION == "1.0"

    def test_inference_fact_low_confidence(self):
        f = Fact(content="他可能也喜欢猫", source=INFERENCE, confidence=0.3)
        assert f.is_inference is True
        assert f.evidence_ids == []

    def test_inference_high_confidence_rejected(self):
        # INFERENCE 假装高 confidence 是不被允许的
        with pytest.raises(ValueError):
            Fact(content="假装是事实", source=INFERENCE, confidence=0.9)

    def test_inference_with_evidence_rejected(self):
        # INFERENCE 不能引用 grounded evidence
        with pytest.raises(ValueError):
            Fact(
                content="基于记忆的推测",
                source=INFERENCE,
                confidence=0.3,
                evidence_ids=["mem_1"],
            )

    def test_confidence_out_of_range(self):
        with pytest.raises(ValueError):
            Fact(content="x", source=USER_INPUT, confidence=1.5)
        with pytest.raises(ValueError):
            Fact(content="x", source=USER_INPUT, confidence=-0.1)

    def test_string_source_accepted(self):
        f = Fact(content="x", source="memory", confidence=0.8)
        assert f.source is FactSource.MEMORY

    def test_invalid_source_rejected(self):
        with pytest.raises(ValueError):
            Fact(content="x", source="nonsense", confidence=0.5)

    def test_to_from_dict(self):
        f = Fact(
            content="记忆片段",
            source=MEMORY,
            confidence=0.8,
            evidence_ids=["evt_1"],
            meta={"k": "v"},
        )
        data = f.to_dict()
        assert data["source"] == "memory"
        f2 = Fact.from_dict(data)
        assert f2.id == f.id
        assert f2.content == f.content
        assert f2.source is FactSource.MEMORY
        assert f2.evidence_ids == ["evt_1"]
        assert f2.meta == {"k": "v"}

    def test_id_unique(self):
        f1 = Fact(content="a", source=USER_INPUT)
        f2 = Fact(content="b", source=USER_INPUT)
        assert f1.id != f2.id
        assert f1.id.startswith("fact_")

    def test_timestamp_iso(self):
        f = Fact(content="x", source=SYSTEM)
        assert f.timestamp.endswith("Z")


# ============================================================
# PerceptionGuard
# ============================================================
class TestPerceptionGuard:
    def test_all_grounded_no_hedge(self):
        g = PerceptionGuard()
        facts = [
            Fact(content="用户输入", source=USER_INPUT),
            Fact(content="记忆", source=MEMORY, evidence_ids=["m1"]),
        ]
        report = g.check(facts)
        assert report.total == 2
        assert report.grounded_count == 2
        assert report.inference_count == 0
        assert report.grounded_only is True
        assert report.needs_hedge is False
        assert report.needs_refusal is False

    def test_with_inference_needs_hedge(self):
        g = PerceptionGuard()
        facts = [
            Fact(content="用户问天气", source=USER_INPUT),
            Fact(content="他可能在上海", source=INFERENCE, confidence=0.3),
        ]
        report = g.check(facts)
        assert report.inference_count == 1
        assert report.grounded_only is False
        assert report.needs_hedge is True
        assert report.needs_refusal is False

    def test_wrap_reply_adds_hedge_zh(self):
        g = PerceptionGuard(language="zh")
        facts = [Fact(content="推测内容", source=INFERENCE, confidence=0.3)]
        out = g.wrap_reply(facts, "他可能喜欢猫")
        assert "推测" in out or "推理" in out
        assert "他可能喜欢猫" in out

    def test_wrap_reply_adds_hedge_en(self):
        g = PerceptionGuard(language="en")
        facts = [Fact(content="inference", source=INFERENCE, confidence=0.3)]
        out = g.wrap_reply(facts, "He may like cats.")
        assert "inference" in out.lower()
        assert "He may like cats." in out

    def test_all_grounded_no_wrap(self):
        g = PerceptionGuard()
        facts = [Fact(content="hi", source=USER_INPUT)]
        assert g.wrap_reply(facts, "你好") == "你好"

    def test_refusal_when_all_inference_with_strict_ratio(self):
        g = PerceptionGuard(min_grounded_ratio=0.5)
        facts = [
            Fact(content="推测1", source=INFERENCE, confidence=0.3),
            Fact(content="推测2", source=INFERENCE, confidence=0.2),
        ]
        report = g.check(facts)
        assert report.grounded_only is False
        assert report.needs_hedge is True
        # ratio=0 < 0.5 → refusal
        assert report.needs_refusal is True
        out = g.wrap_reply(facts, "draft")
        assert out == g.refusal_text

    def test_assert_no_speculation_ok(self):
        g = PerceptionGuard()
        facts = [Fact(content="hi", source=USER_INPUT)]
        g.assert_no_speculation(facts)  # 不应抛

    def test_assert_no_speculation_raises(self):
        g = PerceptionGuard()
        facts = [Fact(content="推测", source=INFERENCE, confidence=0.3)]
        with pytest.raises(ValueError):
            g.assert_no_speculation(facts)

    def test_is_grounded_only_helper(self):
        g = PerceptionGuard()
        facts1 = [Fact(content="a", source=USER_INPUT)]
        facts2 = [
            Fact(content="a", source=USER_INPUT),
            Fact(content="b", source=INFERENCE, confidence=0.3),
        ]
        assert g.is_grounded_only(facts1) is True
        assert g.is_grounded_only(facts2) is False

    def test_has_inference_helper(self):
        g = PerceptionGuard()
        assert g.has_inference([Fact(content="a", source=USER_INPUT)]) is False
        assert (
            g.has_inference([Fact(content="b", source=INFERENCE, confidence=0.3)])
            is True
        )

    def test_grounded_and_inference_filters(self):
        g = PerceptionGuard()
        facts = [
            Fact(content="a", source=USER_INPUT),
            Fact(content="b", source=MEMORY),
            Fact(content="c", source=INFERENCE, confidence=0.3),
        ]
        assert len(g.grounded_facts(facts)) == 2
        assert len(g.inference_facts(facts)) == 1

    def test_empty_facts(self):
        g = PerceptionGuard()
        report = g.check([])
        assert report.total == 0
        assert report.grounded_only is True
        assert report.needs_hedge is False
        assert g.wrap_reply([], "draft") == "draft"

    def test_report_to_dict(self):
        g = PerceptionGuard()
        facts = [Fact(content="a", source=INFERENCE, confidence=0.3)]
        report = g.check(facts)
        d = report.to_dict()
        assert d["total"] == 1
        assert d["inference_count"] == 1
        assert d["needs_hedge"] is True

    def test_custom_hedge_text(self):
        g = PerceptionGuard(
            hedge_prefix="[自定义前缀]",
            hedge_suffix="[自定义后缀]",
            language="zh",
        )
        facts = [Fact(content="x", source=INFERENCE, confidence=0.3)]
        out = g.wrap_reply(facts, "正文")
        assert out.startswith("[自定义前缀]")
        assert out.endswith("[自定义后缀]")

    def test_speculation_packaging_prevention(self):
        """禁止把推测包装成事实：INFERENCE + 高 confidence 必须报错。"""
        # 这是核心防伪：模型不能将推测伪装为事实
        with pytest.raises(ValueError):
            Fact(
                content="我觉得他住在北京",
                source=INFERENCE,
                confidence=0.95,
            )

    def test_mixed_facts_wrap(self):
        g = PerceptionGuard(language="zh")
        facts = [
            Fact(content="用户问问题", source=USER_INPUT),
            Fact(content="推理得到的答案", source=INFERENCE, confidence=0.4),
        ]
        out = g.wrap_reply(facts, "这是答案")
        # 应有 hedge
        assert "这是答案" in out
        # 至少被 prefix / suffix 包了一层
        assert out != "这是答案"
