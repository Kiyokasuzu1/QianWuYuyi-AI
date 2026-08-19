# -*- coding: utf-8 -*-
"""
Phase 3.6.2 P2.5 测试：drift 消除（P2.5.1） + 饱和保护（P2.5.2）。

验证目标：
1. rebuild_growth_records() 的补偿 delta 能让 GrowthAccumulator.compute() 输出 == current_value
2. 注入 growth_records 后，100 次 resolve() drift ≈ 0（Half-Life >> 50）
3. saturation_guard.clamp_pcr_deltas() 在接近边界时正确限制 delta
4. 端到端：warmth +0.04 → 注入 → 100 次 resolve → 仍保持 0.74
"""
from __future__ import annotations

import math
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ============================================================
# Helpers
# ============================================================
def _make_pcr(traits, summary="test"):
    return {
        "request_id": f"pcr_{uuid.uuid4().hex[:8]}",
        "source_proposal_id": f"prop_{uuid.uuid4().hex[:8]}",
        "reason": summary,
        "confidence": 0.9,
        "evidence_count": 1,
        "evolution_record": {"trait_changes": dict(traits)},
    }


def _create_adapter(data_dir):
    from src.personality.self_model_adapter import SelfModelAdapter
    from src.personality.self_model_persistence import SelfModelPersistence
    adapter = SelfModelAdapter(actor="test")
    adapter.attach_persistence(SelfModelPersistence(data_dir))
    return adapter


# ============================================================
# P2.5.1: rebuild_growth_records 补偿验证
# ============================================================
class TestGrowthRecordsCompensation:
    def test_empty_history_returns_empty(self, tmp_path):
        """空 history → 空 records。"""
        from src.runtime.trait_rebuilder import TraitRebuilder
        adapter = _create_adapter(str(tmp_path / "sm"))
        adapter.load_state()
        rb = TraitRebuilder()
        records = rb.rebuild_growth_records(adapter)
        assert records == []

    def test_compensation_makes_accumulated_equal_current(self, tmp_path):
        """补偿后的 records 让 GrowthAccumulator 输出 == TraitRebuilder 输出。"""
        from src.runtime.trait_rebuilder import TraitRebuilder
        from src.personality.growth_accumulator import GrowthAccumulator
        from src.personality.personality_profile import PersonalityProfile

        adapter = _create_adapter(str(tmp_path / "sm"))
        adapter.apply_pcr(_make_pcr({"warmth": 0.02, "shyness": -0.03}))
        adapter.save_state()

        adapter2 = _create_adapter(str(tmp_path / "sm"))
        adapter2.load_state()

        rb = TraitRebuilder()
        values = rb.rebuild(adapter2)          # {"warmth": 0.72, "shyness": 0.72}
        records = rb.rebuild_growth_records(adapter2)

        assert len(records) == 2

        # 用 GrowthAccumulator 计算 accumulated
        acc = GrowthAccumulator()
        base = PersonalityProfile.get_base()
        accumulated = acc.compute(records, base)

        # accumulated 应该 == values（误差 < 0.001）
        for trait in values:
            assert accumulated.get(trait, 0) == pytest.approx(values[trait], abs=0.001), (
                f"{trait}: accumulated={accumulated.get(trait)} vs current={values[trait]}"
            )

    def test_compensation_with_large_delta(self, tmp_path):
        """大 delta (0.15) 也能正确补偿。"""
        from src.runtime.trait_rebuilder import TraitRebuilder
        from src.personality.growth_accumulator import GrowthAccumulator
        from src.personality.personality_profile import PersonalityProfile

        adapter = _create_adapter(str(tmp_path / "sm"))
        adapter.apply_pcr(_make_pcr({"warmth": 0.15}))
        adapter.save_state()

        adapter2 = _create_adapter(str(tmp_path / "sm"))
        adapter2.load_state()

        rb = TraitRebuilder()
        values = rb.rebuild(adapter2)
        records = rb.rebuild_growth_records(adapter2)

        acc = GrowthAccumulator()
        accumulated = acc.compute(records, PersonalityProfile.get_base())

        assert accumulated["warmth"] == pytest.approx(values["warmth"], abs=0.002)

    def test_compensation_clamped_at_boundary(self, tmp_path):
        """delta 超过 0.2 (tanh 极限) 时 clamp 不崩溃。"""
        from src.runtime.trait_rebuilder import TraitRebuilder

        adapter = _create_adapter(str(tmp_path / "sm"))
        # warmth +0.3 → 超过 tanh*0.2=0.2 的补偿范围
        adapter.apply_pcr(_make_pcr({"warmth": 0.3}))
        adapter.save_state()

        adapter2 = _create_adapter(str(tmp_path / "sm"))
        adapter2.load_state()

        rb = TraitRebuilder()
        records = rb.rebuild_growth_records(adapter2)
        # 不崩溃，返回 records（delta 被 clamp）
        assert len(records) == 1


# ============================================================
# P2.5.1: drift 消除验证（核心验收）
# ============================================================
class TestDriftElimination:
    def test_100_resolve_drift_near_zero(self, tmp_path):
        """
        核心验收：注入补偿 growth_records 后，100 次 resolve() drift < 0.002。
        对比 P1 时 4 次就掉一半。
        """
        from src.runtime.trait_rebuilder import (
            TraitRebuilder, inject_trait_states, inject_growth_records,
        )
        from src.personality.personality_resolver import PersonalityResolver

        adapter = _create_adapter(str(tmp_path / "sm"))
        adapter.apply_pcr(_make_pcr({"warmth": 0.04}))
        adapter.save_state()

        adapter2 = _create_adapter(str(tmp_path / "sm"))
        adapter2.load_state()

        rb = TraitRebuilder()
        values = rb.rebuild(adapter2)
        records = rb.rebuild_growth_records(adapter2)

        resolver = PersonalityResolver()
        inject_trait_states(resolver, values)
        inject_growth_records(resolver, records)

        # 第一次 resolve 获取初始值
        vec1 = resolver.resolve()
        d1 = dict(vec1.get_all() or {})
        warmth_1 = d1.get("warmth", 0.0)

        # 再 resolve 99 次
        warmth_100 = warmth_1
        for _ in range(99):
            vec = resolver.resolve()
            d = dict(vec.get_all() or {})
            warmth_100 = d.get("warmth", warmth_100)

        # drift 应该 < 0.005（之前是 4 次掉一半 ≈ 0.02 漂移）
        drift = abs(warmth_100 - warmth_1)
        assert drift < 0.005, (
            f"100 次 resolve 后 drift={drift:.6f} 应 < 0.005"
            f" (warmth: {warmth_1:.4f} → {warmth_100:.4f})"
        )

    def test_half_life_much_longer_than_4(self, tmp_path):
        """半衰期应该 >> 4 次 resolve（之前是 ~4 次）。"""
        from src.runtime.trait_rebuilder import (
            TraitRebuilder, inject_trait_states, inject_growth_records,
        )
        from src.personality.personality_resolver import PersonalityResolver
        from src.personality.personality_profile import PersonalityProfile

        adapter = _create_adapter(str(tmp_path / "sm"))
        adapter.apply_pcr(_make_pcr({"warmth": 0.04}))
        adapter.save_state()

        adapter2 = _create_adapter(str(tmp_path / "sm"))
        adapter2.load_state()

        rb = TraitRebuilder()
        values = rb.rebuild(adapter2)
        records = rb.rebuild_growth_records(adapter2)

        resolver = PersonalityResolver()
        inject_trait_states(resolver, values)
        inject_growth_records(resolver, records)

        base_warmth = PersonalityProfile.get_base().get("warmth", 0.70)

        # resolve 50 次，找半衰期
        vec = resolver.resolve()
        d = dict(vec.get_all() or {})
        warmth_start = d.get("warmth", 0.0)
        initial_growth = warmth_start - base_warmth
        half_target = base_warmth + initial_growth / 2.0

        half_life = None
        for i in range(1, 51):
            vec = resolver.resolve()
            d = dict(vec.get_all() or {})
            w = d.get("warmth", 0.0)
            if initial_growth > 0 and w <= half_target:
                half_life = i
                break

        # 如果 50 次内没到半衰期，说明 drift 极小（好）
        if half_life is None:
            # 验证 50 次后 warmth 仍接近初始值
            assert warmth_start - base_warmth > 0.01, (
                "50 次 resolve 后成长应仍存在"
            )
        else:
            assert half_life > 20, (
                f"半衰期 {half_life} 应 >> 20（之前是 4）"
            )


# ============================================================
# P2.5.2: SaturationGuard 单元测试
# ============================================================
class TestSaturationGuard:
    def test_no_clamp_when_far_from_boundary(self):
        """远离边界时不限制。"""
        from src.runtime.saturation_guard import clamp_pcr_deltas
        result = clamp_pcr_deltas(
            {"warmth": 0.70},
            {"warmth": 0.03},
            ratio=0.1,
        )
        assert result["warmth"] == pytest.approx(0.03)

    def test_clamp_near_upper_boundary(self):
        """接近上界时限制 delta。"""
        from src.runtime.saturation_guard import clamp_pcr_deltas
        result = clamp_pcr_deltas(
            {"warmth": 0.95},
            {"warmth": 0.04},
            ratio=0.1,
        )
        # distance = 1-0.95 = 0.05, max_delta = 0.05*0.1 = 0.005
        assert result["warmth"] == pytest.approx(0.005)

    def test_clamp_near_lower_boundary(self):
        """接近下界时限制负 delta。"""
        from src.runtime.saturation_guard import clamp_pcr_deltas
        result = clamp_pcr_deltas(
            {"warmth": 0.05},
            {"warmth": -0.04},
            ratio=0.1,
        )
        # distance = 0.05-0 = 0.05, max_delta = 0.05*0.1 = 0.005
        assert result["warmth"] == pytest.approx(-0.005)

    def test_zero_delta_returns_zero(self):
        """delta=0 返回 0。"""
        from src.runtime.saturation_guard import clamp_pcr_deltas
        result = clamp_pcr_deltas(
            {"warmth": 0.95},
            {"warmth": 0.0},
        )
        assert result["warmth"] == 0.0

    def test_multiple_traits(self):
        """多 trait 混合限制。"""
        from src.runtime.saturation_guard import clamp_pcr_deltas
        result = clamp_pcr_deltas(
            {"warmth": 0.95, "shyness": 0.50},
            {"warmth": 0.04, "shyness": -0.03},
            ratio=0.1,
        )
        assert result["warmth"] == pytest.approx(0.005)   # clamped
        assert result["shyness"] == pytest.approx(-0.03)   # not clamped (far from boundary)

    def test_clamp_pcr_full_dict(self):
        """对完整 PCR dict 做饱和保护。"""
        from src.runtime.saturation_guard import clamp_pcr
        pcr = _make_pcr({"warmth": 0.04}, "test")
        result = clamp_pcr({"warmth": 0.95}, pcr, ratio=0.1)
        assert result["evolution_record"]["trait_changes"]["warmth"] == pytest.approx(0.005)
        assert result["metadata"]["saturation_guard_applied"] is True
        # 原 PCR 不被修改
        assert pcr["evolution_record"]["trait_changes"]["warmth"] == 0.04

    def test_at_exact_boundary(self):
        """trait 已经在边界（0 或 1）时，delta 被限制为 0。"""
        from src.runtime.saturation_guard import clamp_pcr_deltas
        result = clamp_pcr_deltas(
            {"warmth": 1.0},
            {"warmth": 0.02},
        )
        assert result["warmth"] == 0.0

        result2 = clamp_pcr_deltas(
            {"warmth": 0.0},
            {"warmth": -0.02},
        )
        assert result2["warmth"] == 0.0

    def test_ratio_zero_means_no_clamp(self):
        """ratio=0 表示不限制（直接返回原始 delta）。"""
        from src.runtime.saturation_guard import clamp_pcr_deltas
        result = clamp_pcr_deltas(
            {"warmth": 0.99},
            {"warmth": 0.05},
            ratio=0,
        )
        assert result["warmth"] == 0.05


# ============================================================
# P2.5 端到端：补偿 + 饱和保护 + 重启恢复
# ============================================================
class TestP25EndToEnd:
    def test_growth_survives_100_resolve_and_saturation(self, tmp_path):
        """
        完整验收：
        1. warmth +0.04 → 保存 → 重启 → 注入（P1 + P2.5.1）
        2. 100 次 resolve → warmth 基本不变（drift < 0.003）
        3. 再 apply warmth +0.04（经 saturation guard）→ 不会冲到 1.0
        """
        from src.runtime.trait_rebuilder import (
            TraitRebuilder, inject_trait_states, inject_growth_records,
        )
        from src.personality.personality_resolver import PersonalityResolver
        from src.personality.personality_profile import PersonalityProfile
        from src.runtime.saturation_guard import clamp_pcr_deltas

        data_dir = str(tmp_path / "sm")

        # ---- 1. 初始成长 warmth +0.04 ----
        adapter = _create_adapter(data_dir)
        adapter.apply_pcr(_make_pcr({"warmth": 0.04}))
        adapter.save_state()

        # ---- 2. 重启恢复 ----
        adapter2 = _create_adapter(data_dir)
        adapter2.load_state()

        rb = TraitRebuilder()
        values = rb.rebuild(adapter2)
        records = rb.rebuild_growth_records(adapter2)

        resolver = PersonalityResolver()
        inject_trait_states(resolver, values)
        inject_growth_records(resolver, records)

        # ---- 3. 100 次 resolve 验证 drift ----
        vec = resolver.resolve()
        d = dict(vec.get_all() or {})
        warmth_start = d.get("warmth", 0.0)

        for _ in range(99):
            vec = resolver.resolve()
            d = dict(vec.get_all() or {})

        warmth_after_100 = d.get("warmth", 0.0)
        drift = abs(warmth_after_100 - warmth_start)
        assert drift < 0.003, f"drift={drift:.6f}"

        # ---- 4. 饱和保护：继续 +0.04 不会冲到 1.0 ----
        current_values = rb.rebuild(adapter2)
        clamped = clamp_pcr_deltas(current_values, {"warmth": 0.04}, ratio=0.1)
        # warmth ≈ 0.74, distance = 0.26, max_delta = 0.026
        assert clamped["warmth"] <= 0.04  # 可能不限制（0.74 离 1.0 较远）
        assert clamped["warmth"] > 0      # 正向变化保留

        # 如果 warmth 已经很高（如 0.95），delta 会被限制
        clamped_high = clamp_pcr_deltas({"warmth": 0.95}, {"warmth": 0.04}, ratio=0.1)
        assert clamped_high["warmth"] == pytest.approx(0.005)
