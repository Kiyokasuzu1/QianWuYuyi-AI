# -*- coding: utf-8 -*-
"""
Phase 3.5.2 P1: TraitRebuilder 单元测试 + 重启模拟闭环测试。

覆盖范围：
1. TraitRebuilder.rebuild() 单元：空 history / 单条 delta / 多条累加 / clamp / 负数 delta / 未知 trait
2. inject_trait_states() 兼容注入：set_trait_states setter / set_trait_values setter / 私有属性 fallback
3. 重启模拟闭环测试（关键验收）：
   - 第一次 Runtime：apply +0.02 → history 保存
   - 第二次 Runtime（模拟重启）：new adapter / new resolver / load_state / TraitRebuilder / inject
   - 验证 warmth 从 0.70 变成 0.72，且 resolve() 后仍显著大于 0.70
"""
from __future__ import annotations

import json
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _import_or_skip():
    try:
        from src.runtime.trait_rebuilder import TraitRebuilder, inject_trait_states
        return TraitRebuilder, inject_trait_states
    except Exception as e:  # pragma: no cover
        pytest.skip(f"TraitRebuilder 导入失败: {e}")


# ============================================================
# Helpers: Fake 适配器（不需要真实 SelfModelAdapter）
# ============================================================
class _FakeHistoryEvent:
    def __init__(self, affected_traits=None):
        self.affected_traits = dict(affected_traits) if affected_traits else {}


class _FakeHistory:
    def __init__(self, events):
        self._events = list(events)

    def all(self):
        return list(self._events)


class _FakeAdapter:
    """最小 Fake SelfModelAdapter：只实现 get_history() 返回 FakeHistory。"""

    def __init__(self, deltas: List[Dict[str, float]] = None):
        """deltas: 每条事件的 affected_traits，按顺序传。"""
        events = [_FakeHistoryEvent(d) for d in (deltas or [])]
        self._history = _FakeHistory(events)

    def get_history(self):
        return self._history


# ============================================================
# Test Group 1: TraitRebuilder.rebuild() 单元测试
# ============================================================
class TestTraitRebuilderUnit:
    def test_empty_history_returns_empty(self):
        """没有 history 事件，返回空 dict（resolver 走原有懒加载）。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        assert rb.rebuild(_FakeAdapter([])) == {}
        assert rb.rebuild(None) == {}

    def test_adapter_without_get_history_returns_empty(self):
        """adapter 没有 get_history 方法，不崩溃。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()

        class AdapterBad:
            pass

        assert rb.rebuild(AdapterBad()) == {}
        assert rb.rebuild("not an object") == {}

    def test_single_event_single_trait(self):
        """单条事件 warmth +0.02，期望 warmth = 0.70 + 0.02 = 0.72。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        out = rb.rebuild(_FakeAdapter([{"warmth": 0.02}]))
        assert "warmth" in out
        assert abs(out["warmth"] - 0.72) < 0.0001

    def test_single_event_multiple_traits(self):
        """单条事件改两个 trait，都在输出里。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        out = rb.rebuild(_FakeAdapter([{"warmth": 0.03, "shyness": -0.05}]))
        # warmth: 0.70 + 0.03 = 0.73
        assert abs(out["warmth"] - 0.73) < 0.0001
        # shyness: 0.75 + (-0.05) = 0.70
        assert abs(out["shyness"] - 0.70) < 0.0001

    def test_multiple_events_accumulate(self):
        """多条事件，delta 累加。warmth: +0.02 + 0.01 + 0.03 = 0.06 → 0.76。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        out = rb.rebuild(_FakeAdapter([
            {"warmth": 0.02},
            {"warmth": 0.01},
            {"warmth": 0.03},
        ]))
        assert abs(out["warmth"] - 0.76) < 0.0001

    def test_clamp_lower_zero(self):
        """累加后负值，clamp 到 0。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        # warmth = 0.70 + (-0.9) = -0.20 → clamp 0.0
        out = rb.rebuild(_FakeAdapter([{"warmth": -0.9}]))
        assert out["warmth"] == pytest.approx(0.0, abs=0.0001)

    def test_clamp_upper_one(self):
        """累加后 > 1，clamp 到 1。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        out = rb.rebuild(_FakeAdapter([{"warmth": 1.5}]))
        assert out["warmth"] == pytest.approx(1.0, abs=0.0001)

    def test_negative_delta_subtracts(self):
        """负数 delta 正常减法。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        # gentleness: 0.80 - 0.10 = 0.70
        out = rb.rebuild(_FakeAdapter([{"gentleness": -0.10}]))
        assert abs(out["gentleness"] - 0.70) < 0.0001

    def test_unknown_trait_uses_base_05(self):
        """不在 BASE 里的 trait（如 curiosity），默认 base=0.5。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        # curiosity: 0.5 + 0.03 = 0.53
        out = rb.rebuild(_FakeAdapter([{"curiosity": 0.03}]))
        assert "curiosity" in out
        assert abs(out["curiosity"] - 0.53) < 0.0001

    def test_custom_base_respected(self):
        """显式传入 base 参数，优先使用。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        base = {"warmth": 0.50}
        out = rb.rebuild(_FakeAdapter([{"warmth": 0.05}]), base=base)
        assert abs(out["warmth"] - 0.55) < 0.0001

    def test_non_numeric_delta_skipped(self):
        """非数值 delta 被跳过，不崩溃。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        out = rb.rebuild(_FakeAdapter([
            {"warmth": "not_a_float"},
            {"gentleness": None},
            {"shyness": [1, 2, 3]},
            {"warmth": 0.01},  # 只有这条生效
        ]))
        # warmth: 0.70 + 0.01 = 0.71
        assert abs(out["warmth"] - 0.71) < 0.0001
        # gentleness 因为 None 被跳过，所以输出里不出现
        assert "gentleness" not in out
        assert "shyness" not in out

    def test_event_with_empty_affected_traits_skipped(self):
        """affected_traits 为空的事件跳过。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        out = rb.rebuild(_FakeAdapter([{}, {"warmth": 0.02}, {}]))
        assert len(out) == 1
        assert abs(out["warmth"] - 0.72) < 0.0001

    def test_report_mode_returns_metadata(self):
        """rebuild_with_report() 返回 values + history_events + deltas + base_used。"""
        TraitRebuilder, _ = _import_or_skip()
        rb = TraitRebuilder()
        report = rb.rebuild_with_report(_FakeAdapter([
            {"warmth": 0.02},
            {"warmth": 0.01, "shyness": -0.05},
        ]))
        assert report["history_events"] == 2
        assert report["affected_traits"] == 2
        assert abs(report["deltas"]["warmth"] - 0.03) < 0.0001
        assert abs(report["deltas"]["shyness"] - (-0.05)) < 0.0001
        assert isinstance(report["base_used"], dict) and "warmth" in report["base_used"]
        assert abs(report["values"]["warmth"] - 0.73) < 0.0001


# ============================================================
# Test Group 2: inject_trait_states() 兼容注入
# ============================================================
class TestInjectTraitStates:
    def test_private_dict_fallback(self):
        """真实 PersonalityResolver：_trait_states 是 dict。"""
        _, inject = _import_or_skip()
        try:
            from src.personality.personality_resolver import PersonalityResolver
        except Exception:  # pragma: no cover
            pytest.skip("PersonalityResolver 导入失败")
        r = PersonalityResolver()
        values = {"warmth": 0.72, "shyness": 0.70}
        ok = inject(r, values)
        assert ok is True
        # 读取 _trait_states
        internal = getattr(r, "_trait_states", None)
        assert isinstance(internal, dict)
        assert "warmth" in internal
        assert internal["warmth"]["current_value"] == pytest.approx(0.72, abs=0.0001)
        assert "shyness" in internal
        assert internal["shyness"]["current_value"] == pytest.approx(0.70, abs=0.0001)

    def test_public_setter_trait_states_is_used_first(self):
        """如果 resolver 有 set_trait_states，优先调用。"""
        _, inject = _import_or_skip()
        calls = []

        class R:
            def set_trait_states(self, states):
                calls.append(("set_trait_states", states))

        r = R()
        values = {"warmth": 0.55}
        ok = inject(r, values)
        assert ok is True
        assert calls[0][0] == "set_trait_states"
        # TraitState 应该是 dict（create_trait_state 产物）
        assert isinstance(calls[0][1]["warmth"], dict)
        assert calls[0][1]["warmth"]["current_value"] == pytest.approx(0.55)

    def test_public_setter_values_used_as_fallback_before_private(self):
        """没有 set_trait_states 但有 set_trait_values 时，第二个策略命中。"""
        _, inject = _import_or_skip()
        calls = []

        class R:
            def set_trait_values(self, values):
                calls.append(("set_trait_values", values))

        r = R()
        values = {"warmth": 0.9}
        ok = inject(r, values)
        assert ok is True
        assert calls[0][0] == "set_trait_values"
        assert calls[0][1]["warmth"] == pytest.approx(0.9)

    def test_empty_values_returns_false(self):
        """空 values 直接返回 False。"""
        _, inject = _import_or_skip()

        class R:
            def set_trait_values(self, v):
                raise AssertionError("不应该被调用")

        assert inject(R(), {}) is False

    def test_no_known_strategy_returns_false(self):
        """既没有 setter 也没有私有 _trait_states，返回 False。"""
        _, inject = _import_or_skip()

        class R:
            pass

        r = R()
        ok = inject(r, {"warmth": 0.5})
        assert ok is False

    def test_real_resolver_resolve_after_inject(self):
        """注入后 resolve()，warmth 应显著高于 BASE 0.70。"""
        _, inject = _import_or_skip()
        try:
            from src.personality.personality_resolver import PersonalityResolver
        except Exception:  # pragma: no cover
            pytest.skip("PersonalityResolver 导入失败")
        r = PersonalityResolver()
        # 注入 warmth = 0.80（比 BASE 0.70 高 0.10）
        inject(r, {"warmth": 0.80})
        vec = r.resolve()
        # 读取 warmth 数值。用 get_all() 或者 dict 方式拿。
        data = {}
        if hasattr(vec, "get_all") and callable(vec.get_all):
            data = dict(vec.get_all() or {})
        # warmth 注入后应该大于 BASE(0.70)
        warmth_val = data.get("warmth")
        assert warmth_val is not None, f"resolve() 输出里没有 warmth: {list(data.keys())}"
        # 即便 resolve() 会造成约 -0.0085 漂移（delta=-0.10 → effective=-0.0085），
        # 0.80 - 0.0085 = 0.7915 仍显著高于 BASE。
        assert warmth_val > 0.75, f"期望 warmth > 0.75，实际 {warmth_val}"


# ============================================================
# Test Group 3: 重启模拟闭环测试（P1 关键验收）
# ============================================================
class TestRestartSimulation:
    """关键验收：模拟「关机→开机」后 trait 是否还在。"""

    @pytest.fixture
    def temp_data_dir(self, tmp_path):
        """创建临时 data/self_model 目录（完全隔离，不影响真实用户数据）。"""
        import os
        d = tmp_path / "self_model"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def _create_real_adapter(self, data_dir: str):
        """用真实 SelfModelAdapter + SelfModelPersistence，走完整 JSONL 路径。"""
        from src.personality.self_model_adapter import SelfModelAdapter
        from src.personality.self_model_persistence import SelfModelPersistence

        adapter = SelfModelAdapter(actor="test")
        persistence = SelfModelPersistence(data_dir)
        adapter.attach_persistence(persistence)
        return adapter

    def _make_pcr_dict(self, affected_traits, summary="test_pcr"):
        """
        构造符合 SelfModelAdapter.apply_pcr() 要求的 dict-PCR。
        trait 变更放在 evolution_record.trait_changes 里。
        """
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

    def test_restart_simulation_full_loop(self, temp_data_dir):
        """
        重启模拟完整闭环：
        1) 第一次：apply_pcr (warmth +0.02) → save_state → 落盘
        2) 第二次：新 adapter + 新 resolver（模拟重启）→ load_state → TraitRebuilder → inject
        3) 新 resolver.resolve() 输出 warmth 明显高于 BASE(0.70)
        """
        TraitRebuilder, inject = _import_or_skip()

        # =============================
        # Phase 1: 第一次 runtime（模拟昨天）
        # =============================
        adapter1 = self._create_real_adapter(temp_data_dir)

        # 应用一条 PCR：warmth +0.02
        pcr = self._make_pcr_dict({"warmth": 0.02}, summary="test_growth_warmth")
        result1 = adapter1.apply_pcr(pcr)
        # apply_pcr 返回 applied=True / history_event_id 不为空
        assert result1.get("applied") is True, f"apply_pcr 失败: {result1}"
        assert result1.get("history_event_id") is not None, (
            f"history 应该写入，result: {result1}"
        )

        # 保存到磁盘
        save_result = adapter1.save_state()
        # save_state 返回 {beliefs:bool, history:bool, reflections:bool, note:str}
        assert save_result.get("history") is True, (
            f"history 应该落盘成功: {save_result}"
        )
        # 检查历史文件存在
        history_file = Path(temp_data_dir) / "history.jsonl"
        assert history_file.exists(), f"history.jsonl 未生成: {history_file}"
        assert history_file.stat().st_size > 0, "history.jsonl 为空"

        # =============================
        # Phase 2: 第二次 runtime（模拟今天重启）
        # =============================
        # 注意：必须用新的 adapter 实例（和第一次不同对象），模拟"重启"
        adapter2 = self._create_real_adapter(temp_data_dir)
        load_counts = adapter2.load_state()
        assert load_counts.get("history", 0) >= 1, f"load_state 应读出 history: {load_counts}"

        # 新建 resolver（完全新对象，不带任何之前的状态）
        from src.personality.personality_resolver import PersonalityResolver
        resolver2 = PersonalityResolver()

        # 启动前：resolver._trait_states 为空，resolve() 会用 BASE
        before_ts = resolver2.get_trait_states()
        assert len(before_ts) == 0, "启动前 resolver 应该是空 trait_states"

        # 回放 + 注入
        rb = TraitRebuilder()
        values = rb.rebuild(adapter2)
        # values 应该包含 warmth = 0.70 + 0.02 = 0.72
        assert "warmth" in values, f"回放结果里应该有 warmth: {values}"
        assert abs(values["warmth"] - 0.72) < 0.0001, f"期望 0.72 实际: {values.get('warmth')}"

        ok = inject(resolver2, values)
        assert ok is True, "inject_trait_states 失败"

        # =============================
        # Phase 3: 验证 resolve()
        # =============================
        # resolve() 会进行一次演化，但漂移应该极小
        vec = resolver2.resolve()
        data = {}
        if hasattr(vec, "get_all") and callable(vec.get_all):
            data = dict(vec.get_all() or {})

        warmth_after = data.get("warmth")
        assert warmth_after is not None, f"resolve() 没输出 warmth: {list(data.keys())}"
        # 漂移后仍应明显高于 BASE(0.70)。哪怕漂移 -0.0085 也只是 0.7115，仍 > 0.705
        assert warmth_after > 0.705, (
            f"resolve() 后 warmth {warmth_after} 应该显著高于 BASE(0.70)。"
            f" 漂移不应该把 trait 值拉回 BASE。"
        )

    def test_multiple_restarts_accumulate(self, temp_data_dir):
        """多次"重启"累加 trait。"""
        TraitRebuilder, inject = _import_or_skip()

        from src.personality.personality_resolver import PersonalityResolver

        # 第一次：warmth +0.02 → 0.72
        a1 = self._create_real_adapter(temp_data_dir)
        pcr1 = self._make_pcr_dict({"warmth": 0.02}, "r1")
        r = a1.apply_pcr(pcr1)
        assert r.get("applied"), f"pcr1 未 applied: {r}"
        a1.save_state()  # result = {history:True,...}，如果 history.jsonl 不存在则失败，后续 load_state 会 assert 0 >= 1

        # 第二次重启（warmth 0.72），再 apply +0.03 → 0.75，保存
        a2 = self._create_real_adapter(temp_data_dir)
        a2.load_state()
        r2 = PersonalityResolver()
        v2 = TraitRebuilder().rebuild(a2)
        assert abs(v2["warmth"] - 0.72) < 0.0001, f"第一次回放应为 0.72: {v2}"
        inject(r2, v2)
        pcr2 = self._make_pcr_dict({"warmth": 0.03}, "r2")
        r = a2.apply_pcr(pcr2)
        assert r.get("applied"), f"pcr2 未 applied: {r}"
        a2.save_state()  # 第二次保存

        # 第三次重启：应该读出 warmth = 0.70 + 0.02 + 0.03 = 0.75
        a3 = self._create_real_adapter(temp_data_dir)
        a3.load_state()
        v3 = TraitRebuilder().rebuild(a3)
        assert abs(v3["warmth"] - 0.75) < 0.0001, (
            f"两次累加应得到 warmth=0.75，实际: {v3.get('warmth')}。"
            f" 完整 delta 报告: {TraitRebuilder().rebuild_with_report(a3)}"
        )
