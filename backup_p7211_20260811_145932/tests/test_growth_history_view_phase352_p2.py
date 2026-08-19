# -*- coding: utf-8 -*-
"""
Phase 3.5.2 P2: GrowthHistoryView 单元测试 + 完整生命周期验收测试。

覆盖范围：
1. GrowthHistoryView 单元：空 adapter / 单条事件 / 多条事件 / get_by_dimension / get_high_confidence
2. inject_growth_history_view() 兼容注入
3. 完整生命周期验收（P2.3 关键验收）：
   T1 成长前 BASE → T2 输入经历 PCR → T3 保存 → T4 重启恢复 → T5 resolve + Prompt 包含成长信息
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _import_or_skip():
    try:
        from src.runtime.growth_history_view import (
            GrowthHistoryView,
            inject_growth_history_view,
            _event_to_growth_record,
        )
        return GrowthHistoryView, inject_growth_history_view, _event_to_growth_record
    except Exception as e:  # pragma: no cover
        pytest.skip(f"GrowthHistoryView 导入失败: {e}")


# ============================================================
# Helpers
# ============================================================
class _FakeHistoryEvent:
    def __init__(self, affected_traits=None, event_id="", timestamp="",
                 source_type="pcr", source_id="", summary="", event_type="pcr_applied",
                 metadata=None):
        self.affected_traits = dict(affected_traits) if affected_traits else {}
        self.event_id = event_id or f"hevt_{uuid.uuid4().hex[:10]}"
        self.timestamp = timestamp or "2026-08-06T12:00:00Z"
        self.source_type = source_type
        self.source_id = source_id
        self.summary = summary
        self.event_type = event_type
        self.metadata = metadata or {}


class _FakeHistory:
    def __init__(self, events):
        self._events = list(events)

    def all(self):
        return list(self._events)


class _FakeAdapter:
    def __init__(self, events=None):
        self._history = _FakeHistory(events or [])

    def get_history(self):
        return self._history


# ============================================================
# Test Group 1: GrowthHistoryView 单元测试
# ============================================================
class TestGrowthHistoryViewUnit:
    def test_empty_adapter_returns_empty(self):
        """空 adapter，所有方法返回空。"""
        GrowthHistoryView, _, _ = _import_or_skip()
        view = GrowthHistoryView(None)
        assert view.all() == []
        assert view.count() == 0
        assert view.latest() is None

    def test_adapter_without_history_returns_empty(self):
        """adapter 没有 get_history 方法，不崩溃。"""
        GrowthHistoryView, _, _ = _import_or_skip()

        class BadAdapter:
            pass

        view = GrowthHistoryView(BadAdapter())
        assert view.all() == []
        assert view.count() == 0

    def test_single_event(self):
        """单条事件 warmth +0.02，all() 返回 1 条记录。"""
        GrowthHistoryView, _, _ = _import_or_skip()
        ev = _FakeHistoryEvent(
            affected_traits={"warmth": 0.02},
            summary="test growth",
            source_id="prop_001",
        )
        view = GrowthHistoryView(_FakeAdapter([ev]))
        records = view.all()
        assert len(records) == 1
        r = records[0]
        assert "warmth" in r["affected_dimensions"]
        assert r["changes"]["warmth"]["delta"] == pytest.approx(0.02)
        assert r["meaning"] == "test growth"
        assert r["confidence"] == pytest.approx(0.7)  # default
        assert r["growth_level"] == "trait"

    def test_multiple_events(self):
        """多条事件，all() 返回全部。"""
        GrowthHistoryView, _, _ = _import_or_skip()
        events = [
            _FakeHistoryEvent(affected_traits={"warmth": 0.02}, summary="r1"),
            _FakeHistoryEvent(affected_traits={"curiosity": 0.03}, summary="r2"),
        ]
        view = GrowthHistoryView(_FakeAdapter(events))
        assert view.count() == 2
        assert view.latest()["meaning"] == "r2"

    def test_get_by_dimension(self):
        """按维度过滤。"""
        GrowthHistoryView, _, _ = _import_or_skip()
        events = [
            _FakeHistoryEvent(affected_traits={"warmth": 0.02}, summary="warmth_change"),
            _FakeHistoryEvent(affected_traits={"curiosity": 0.03}, summary="curiosity_change"),
            _FakeHistoryEvent(affected_traits={"warmth": 0.01, "curiosity": 0.01}, summary="both"),
        ]
        view = GrowthHistoryView(_FakeAdapter(events))
        warmth_records = view.get_by_dimension("warmth")
        assert len(warmth_records) == 2
        curiosity_records = view.get_by_dimension("curiosity")
        assert len(curiosity_records) == 2

    def test_get_high_confidence(self):
        """按置信度过滤。"""
        GrowthHistoryView, _, _ = _import_or_skip()
        events = [
            _FakeHistoryEvent(affected_traits={"warmth": 0.02}, metadata={"confidence": 0.9}),
            _FakeHistoryEvent(affected_traits={"curiosity": 0.03}, metadata={"confidence": 0.5}),
        ]
        view = GrowthHistoryView(_FakeAdapter(events))
        high = view.get_high_confidence(0.7)
        assert len(high) == 1
        assert high[0]["confidence"] == pytest.approx(0.9)

    def test_add_not_supported(self):
        """GrowthHistoryView 是只读视图，add() 返回 False。"""
        GrowthHistoryView, _, _ = _import_or_skip()
        view = GrowthHistoryView(_FakeAdapter([]))
        assert view.add({"any": "record"}) is False

    def test_dict_event_supported(self):
        """事件也可以是 dict（不只 dataclass）。"""
        GrowthHistoryView, _, _ = _import_or_skip()
        ev = {
            "event_id": "hevt_001",
            "timestamp": "2026-08-06T12:00:00Z",
            "source_type": "pcr",
            "source_id": "prop_001",
            "summary": "dict event",
            "event_type": "pcr_applied",
            "affected_traits": {"warmth": 0.05},
            "metadata": {"confidence": 0.85},
        }
        view = GrowthHistoryView(_FakeAdapter([ev]))
        records = view.all()
        assert len(records) == 1
        assert records[0]["changes"]["warmth"]["delta"] == pytest.approx(0.05)
        assert records[0]["confidence"] == pytest.approx(0.85)

    def test_empty_affected_traits(self):
        """事件没有 affected_traits，growth_level=context。"""
        GrowthHistoryView, _, _ = _import_or_skip()
        ev = _FakeHistoryEvent(affected_traits={}, summary="no traits")
        view = GrowthHistoryView(_FakeAdapter([ev]))
        r = view.all()[0]
        assert r["growth_level"] == "context"
        assert r["affected_dimensions"] == []
        assert r["changes"] == {}


# ============================================================
# Test Group 2: inject_growth_history_view() 兼容注入
# ============================================================
class TestInjectGrowthHistoryView:
    def test_inject_into_real_resolver(self):
        """注入真实 PersonalityResolver.growth_history。"""
        _, inject, _ = _import_or_skip()
        try:
            from src.personality.personality_resolver import PersonalityResolver
        except Exception:  # pragma: no cover
            pytest.skip("PersonalityResolver 导入失败")

        adapter = _FakeAdapter([
            _FakeHistoryEvent(affected_traits={"warmth": 0.02}, summary="test"),
        ])
        resolver = PersonalityResolver()
        ok = inject(resolver, adapter)
        assert ok is True

        # resolver.growth_history 应该是 GrowthHistoryView
        gh = resolver.growth_history
        assert hasattr(gh, "all")
        assert hasattr(gh, "count")
        assert gh.count() == 1
        records = gh.all()
        assert records[0]["changes"]["warmth"]["delta"] == pytest.approx(0.02)

    def test_inject_none_returns_false(self):
        """None 参数返回 False。"""
        _, inject, _ = _import_or_skip()
        assert inject(None, _FakeAdapter([])) is False
        assert inject("not_resolver", None) is False

    def test_inject_with_setter(self):
        """如果 resolver 有 set_growth_history，优先调用。"""
        _, inject, _ = _import_or_skip()
        calls = []

        class R:
            def set_growth_history(self, view):
                calls.append(view)

        r = R()
        ok = inject(r, _FakeAdapter([]))
        assert ok is True
        assert len(calls) == 1


# ============================================================
# Test Group 3: 完整生命周期验收（P2.3 关键测试）
# ============================================================
class TestFullLifecycleAcceptance:
    """
    P2.3 完整生命周期验收：
    T1 成长前 → T2 输入经历 → T3 保存 → T4 重启恢复 → T5 resolve + Prompt

    这是"羽依诞生测试"：
    - 证明成长→保存→重启→恢复→表达全链路成立
    - 证明 GrowthHistoryView 提供叙事上下文
    """

    @pytest.fixture
    def temp_data_dir(self, tmp_path):
        d = tmp_path / "self_model"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def _make_pcr_dict(self, affected_traits, summary="lifecycle_test"):
        return {
            "request_id": f"pcr_{uuid.uuid4().hex[:10]}",
            "source_proposal_id": f"prop_{uuid.uuid4().hex[:10]}",
            "reason": summary,
            "confidence": 0.9,
            "evidence_count": 1,
            "evolution_record": {
                "trait_changes": dict(affected_traits),
            },
        }

    def _create_adapter(self, data_dir):
        from src.personality.self_model_adapter import SelfModelAdapter
        from src.personality.self_model_persistence import SelfModelPersistence
        adapter = SelfModelAdapter(actor="test")
        persistence = SelfModelPersistence(data_dir)
        adapter.attach_persistence(persistence)
        return adapter

    def test_full_lifecycle(self, temp_data_dir):
        """
        T1 → T2 → T3 → T4 → T5 完整流程。
        """
        GrowthHistoryView, inject_view, _ = _import_or_skip()
        from src.runtime.trait_rebuilder import TraitRebuilder, inject_trait_states
        from src.personality.personality_resolver import PersonalityResolver
        from src.personality.personality_profile import PersonalityProfile

        # =============================
        # T1: 成长前 — BASE 值
        # =============================
        base = PersonalityProfile.get_base()
        warmth_base = base["warmth"]  # 0.70
        assert warmth_base == pytest.approx(0.70, abs=0.001)

        # =============================
        # T2: 输入经历 — apply PCR (warmth +0.02)
        # =============================
        adapter1 = self._create_adapter(temp_data_dir)
        pcr = self._make_pcr_dict(
            {"warmth": 0.02, "gentleness": 0.01},
            summary="用户希望表达更自然，减少机械提醒",
        )
        result = adapter1.apply_pcr(pcr)
        assert result.get("applied") is True, f"PCR 未应用: {result}"

        # =============================
        # T3: 保存 — JSONL 落盘
        # =============================
        save_result = adapter1.save_state()
        assert save_result.get("history") is True, f"save_state 失败: {save_result}"

        history_file = Path(temp_data_dir) / "history.jsonl"
        assert history_file.exists(), "history.jsonl 未生成"
        assert history_file.stat().st_size > 0, "history.jsonl 为空"

        # 验证 JSONL 内容
        import json
        lines = history_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        record = json.loads(lines[0])
        assert "warmth" in record.get("affected_traits", {})
        assert record["affected_traits"]["warmth"] == pytest.approx(0.02)

        # =============================
        # T4: 重启恢复 — 新 adapter + new resolver + TraitRebuilder + GrowthHistoryView
        # =============================
        adapter2 = self._create_adapter(temp_data_dir)
        load_counts = adapter2.load_state()
        assert load_counts.get("history", 0) >= 1, f"重启后应读出 history: {load_counts}"

        resolver2 = PersonalityResolver()

        # 启动前：resolver 是空的
        assert len(resolver2.get_trait_states()) == 0
        assert resolver2.growth_history.count() == 0  # 默认空 PersonalityGrowthHistory

        # P1: TraitRebuilder 回放
        rb = TraitRebuilder()
        values = rb.rebuild(adapter2)
        assert "warmth" in values
        assert values["warmth"] == pytest.approx(0.72, abs=0.0001)  # 0.70 + 0.02
        assert "gentleness" in values
        assert values["gentleness"] == pytest.approx(0.81, abs=0.0001)  # 0.80 + 0.01

        inject_trait_states(resolver2, values)
        assert len(resolver2.get_trait_states()) >= 2

        # P2: GrowthHistoryView 注入
        inject_view(resolver2, adapter2)
        assert resolver2.growth_history.count() == 1, (
            f"GrowthHistoryView 应该有 1 条记录，实际: {resolver2.growth_history.count()}"
        )

        # 验证 view 内容
        records = resolver2.growth_history.all()
        assert len(records) == 1
        r = records[0]
        assert "warmth" in r["affected_dimensions"]
        assert r["changes"]["warmth"]["delta"] == pytest.approx(0.02)
        assert "自然" in r["meaning"] or "机械" in r["meaning"], (
            f"meaning 应该包含原始 summary 内容: {r['meaning']}"
        )

        # =============================
        # T5: resolve + Prompt 包含成长信息
        # =============================
        vec = resolver2.resolve()
        data = dict(vec.get_all() or {})

        # 5a: trait 值恢复并显著高于 BASE
        warmth_after = data.get("warmth")
        assert warmth_after is not None
        assert warmth_after > 0.705, (
            f"重启后 warmth={warmth_after} 应该显著高于 BASE(0.70)"
        )

        # 5b: GrowthHistoryView 提供叙事上下文
        latest = resolver2.growth_history.latest()
        assert latest is not None
        assert latest["changes"]["warmth"]["delta"] == pytest.approx(0.02)

        # 5c: P0 Prompt 注入 — 模拟 orchestrator._format_personality_vector
        # 验证 PersonalityVector 可以被格式化成 prompt 内容
        from src.orchestrator import Orchestrator
        orch = Orchestrator.__new__(Orchestrator)  # 不调 __init__，只用方法
        prompt_fragment = orch._format_personality_vector(vec)

        assert len(prompt_fragment) > 0, "Prompt fragment 不应为空"
        # 应该包含 warmth 数值
        assert "0.7" in prompt_fragment or "0.72" in prompt_fragment, (
            f"Prompt 应该包含 warmth 数值: {prompt_fragment[:200]}"
        )

    def test_lifecycle_multiple_growth_events(self, temp_data_dir):
        """多次成长事件累加。"""
        GrowthHistoryView, inject_view, _ = _import_or_skip()
        from src.runtime.trait_rebuilder import TraitRebuilder, inject_trait_states
        from src.personality.personality_resolver import PersonalityResolver

        # 第一次成长：warmth +0.02
        a1 = self._create_adapter(temp_data_dir)
        a1.apply_pcr(self._make_pcr_dict(
            {"warmth": 0.02}, "第一次成长：学会温柔表达"
        ))
        a1.save_state()

        # 第二次成长（重启后）：warmth +0.03
        a2 = self._create_adapter(temp_data_dir)
        a2.load_state()
        a2.apply_pcr(self._make_pcr_dict(
            {"warmth": 0.03, "shyness": -0.05}, "第二次成长：减少害羞"
        ))
        a2.save_state()

        # 第三次重启
        a3 = self._create_adapter(temp_data_dir)
        a3.load_state()

        rb = TraitRebuilder()
        values = rb.rebuild(a3)
        # warmth: 0.70 + 0.02 + 0.03 = 0.75
        assert values["warmth"] == pytest.approx(0.75, abs=0.0001)
        # shyness: 0.75 + (-0.05) = 0.70
        assert values["shyness"] == pytest.approx(0.70, abs=0.0001)

        # GrowthHistoryView 应该有 2 条记录
        view = GrowthHistoryView(a3)
        assert view.count() == 2

        records = view.all()
        assert "第一次成长" in records[0]["meaning"]
        assert "第二次成长" in records[1]["meaning"]

        # 按维度过滤
        warmth_records = view.get_by_dimension("warmth")
        assert len(warmth_records) == 2
        shyness_records = view.get_by_dimension("shyness")
        assert len(shyness_records) == 1
