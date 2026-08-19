# -*- coding: utf-8 -*-
"""
Phase 3.6.2 P2.5.2: SaturationGuard — PCR delta 边界保护。

职责：
    在 PCR 应用前，根据当前 trait 值和距离边界的距离，限制单次 delta 幅度。
    越接近 0 或 1，允许的变化越小（渐进饱和保护）。

设计原则：
    - 不修改 SelfModelAdapter.apply_pcr（不碰 self_model 核心）
    - 不修改 EvolutionEngine 参数（不污染人格动力学）
    - 纯函数，可由 GrowthPipeline / 压力测试 / 任何调用方在 apply_pcr 前调用

公式：
    max_delta = distance_to_boundary * ratio (默认 ratio=0.1)

    例如 warmth=0.95, delta=+0.04:
        distance = 1.0 - 0.95 = 0.05
        max_delta = 0.05 * 0.1 = 0.005
        clamped_delta = min(0.04, 0.005) = 0.005

    例如 warmth=0.70, delta=+0.03:
        distance = 1.0 - 0.70 = 0.30
        max_delta = 0.30 * 0.1 = 0.030
        clamped_delta = min(0.03, 0.030) = 0.030  (不受影响)

效果：
    trait 越接近边界，变化越慢，自然趋稳，不会硬撞 0 或 1。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def clamp_pcr_deltas(
    current_values: Dict[str, float],
    trait_changes: Dict[str, float],
    ratio: float = 0.1,
    low: float = 0.0,
    high: float = 1.0,
) -> Dict[str, float]:
    """
    根据 current_values 限制 trait_changes 中的 delta。

    Args:
        current_values: 当前 trait 值，例如 {"warmth": 0.95, "shyness": 0.30}
        trait_changes: PCR 中的 trait delta，例如 {"warmth": 0.04, "shyness": -0.02}
        ratio: 距离边界的比例系数，默认 0.1（每次最多改变剩余空间的 10%）
        low: trait 下界，默认 0.0
        high: trait 上界，默认 1.0

    Returns:
        clamped_changes: 限制后的 delta 字典（不修改输入）
    """
    if not isinstance(trait_changes, dict):
        return {}
    if not isinstance(current_values, dict):
        current_values = {}
    if ratio <= 0:
        return dict(trait_changes)

    clamped: Dict[str, float] = {}
    for trait, delta in trait_changes.items():
        try:
            delta_f = float(delta)
        except (TypeError, ValueError):
            continue

        current = current_values.get(trait, 0.5)
        try:
            current_f = float(current)
        except (TypeError, ValueError):
            current_f = 0.5

        if delta_f > 0:
            distance = high - current_f
            max_delta = distance * ratio
            clamped_delta = min(delta_f, max(max_delta, 0.0))
        elif delta_f < 0:
            distance = current_f - low
            max_delta = distance * ratio
            clamped_delta = max(delta_f, -max(max_delta, 0.0))
        else:
            clamped_delta = 0.0

        clamped[trait] = round(clamped_delta, 6)

    return clamped


def clamp_pcr(
    current_values: Dict[str, float],
    pcr: Dict[str, Any],
    ratio: float = 0.1,
) -> Dict[str, Any]:
    """
    对一个完整 PCR dict 做饱和保护。

    修改 pcr["evolution_record"]["trait_changes"]（返回新 dict，不修改输入）。

    Args:
        current_values: 当前 trait 值
        pcr: PersonalityChangeRequest dict
        ratio: 距离边界比例

    Returns:
        new_pcr: 限制后的 PCR（深拷贝）
    """
    import copy
    new_pcr = copy.deepcopy(pcr)
    try:
        evo = new_pcr.get("evolution_record", {})
        if not isinstance(evo, dict):
            return new_pcr
        trait_changes = evo.get("trait_changes", {})
        if not isinstance(trait_changes, dict):
            return new_pcr
        clamped = clamp_pcr_deltas(current_values, trait_changes, ratio=ratio)
        new_pcr["evolution_record"]["trait_changes"] = clamped
        new_pcr.setdefault("metadata", {})
        new_pcr["metadata"]["saturation_guard_applied"] = True
        new_pcr["metadata"]["saturation_ratio"] = ratio
    except Exception:
        pass
    return new_pcr


def get_current_values_from_adapter(adapter: Any) -> Dict[str, float]:
    """
    从 SelfModelAdapter 获取当前 trait 值（通过 TraitRebuilder）。
    便利函数，供调用方使用。
    """
    try:
        from src.runtime.trait_rebuilder import TraitRebuilder
        rb = TraitRebuilder()
        return rb.rebuild(adapter)
    except Exception:
        return {}
