# -*- coding: utf-8 -*-
"""
Phase 3.5.2 P1: TraitRebuilder + inject_trait_states 兼容注入。

职责（严格单一）：
- TraitRebuilder: history events → {trait_name: current_value}  （纯值，不知道 TraitState 结构）
- inject_trait_states(resolver, states): 把值字典安全注入 PersonalityResolver，
  优先走公开 setter，fallback 到私有属性，未来 resolver 改名/换内部结构也不崩。

设计约束：
- 不导入 src/personality/** 任何内部类（除 PersonalityProfile.get_base() 作为默认值）
- 不读 JSONL 文件（通过 SelfModelAdapter.get_history() 间接读取）
- 任何异常返回空 dict，不影响启动
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


class TraitRebuilder:
    """
    从 SelfModel history 回放 trait 变化，计算启动时的当前人格数值。

    输出：Dict[str, float]，例如 {"warmth": 0.72, "gentleness": 0.81}
         不包含 TraitState 结构，由调用方（Runtime）决定如何转换成 resolver 需要的类型。
    """

    def rebuild(
        self,
        adapter: Any,
        base: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """
        从 adapter.get_history().all() 累加 affected_traits delta。

        Args:
            adapter: SelfModelAdapter（已调用过 load_state）
            base: 人格基准，默认 PersonalityProfile.get_base()

        Returns:
            Dict[str, float]: 受影响的 trait 的当前值（BASE + sum(deltas)）。
            如果没有 history 或没有任何 delta，返回 {}（让 resolver 走原有懒加载）。
            任何异常返回 {}。
        """
        try:
            if adapter is None:
                return {}

            # --------- 1. 加载 BASE 默认值（延迟 import，避免循环依赖） ---------
            if base is None:
                try:
                    from src.personality.personality_profile import PersonalityProfile

                    base = dict(PersonalityProfile.get_base() or {})
                except Exception:
                    base = {}

            # --------- 2. 读取 history events ---------
            try:
                history = adapter.get_history() if hasattr(adapter, "get_history") and callable(adapter.get_history) else None
            except Exception:
                history = None

            if history is None:
                return {}

            try:
                events = history.all() if hasattr(history, "all") and callable(history.all) else []
            except Exception:
                events = []

            if not events:
                return {}

            # --------- 3. 累加 delta ---------
            total_deltas: Dict[str, float] = {}
            for event in events:
                if event is None:
                    continue
                # 允许两种访问方式：event.affected_traits 或 event["affected_traits"]
                try:
                    affected = event.affected_traits if hasattr(event, "affected_traits") else None
                except Exception:
                    affected = None
                if affected is None and isinstance(event, dict):
                    affected = event.get("affected_traits")
                if not isinstance(affected, dict):
                    continue
                for trait_name, delta in affected.items():
                    if not isinstance(trait_name, str) or not trait_name:
                        continue
                    try:
                        delta_f = float(delta)
                    except (TypeError, ValueError):
                        continue
                    total_deltas[trait_name] = total_deltas.get(trait_name, 0.0) + delta_f

            if not total_deltas:
                return {}

            # --------- 4. BASE + delta 得到当前值，clamp [0,1] ---------
            result: Dict[str, float] = {}
            for trait_name, total_delta in total_deltas.items():
                base_value = 0.5
                if isinstance(base, dict) and trait_name in base:
                    try:
                        base_value = float(base[trait_name])
                    except (TypeError, ValueError):
                        base_value = 0.5
                current = base_value + total_delta
                if current < 0.0:
                    current = 0.0
                if current > 1.0:
                    current = 1.0
                result[trait_name] = round(current, 4)

            return result

        except Exception:
            return {}

    def rebuild_with_report(
        self,
        adapter: Any,
        base: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        和 rebuild() 一样，但返回完整报告（用于日志/测试）。

        Returns:
            {
                "values": Dict[str, float],     # 同 rebuild() 输出
                "history_events": int,          # 事件数
                "affected_traits": int,         # 受影响 trait 数
                "deltas": Dict[str, float],     # 累加的 delta
                "base_used": Dict[str, float],  # 实际用的 BASE
            }
        """
        report = {
            "values": {},
            "history_events": 0,
            "affected_traits": 0,
            "deltas": {},
            "base_used": {},
        }
        try:
            if adapter is None:
                return report

            if base is None:
                try:
                    from src.personality.personality_profile import PersonalityProfile

                    base = dict(PersonalityProfile.get_base() or {})
                except Exception:
                    base = {}
            report["base_used"] = dict(base) if isinstance(base, dict) else {}

            try:
                history = adapter.get_history() if hasattr(adapter, "get_history") and callable(adapter.get_history) else None
            except Exception:
                history = None
            if history is None:
                return report

            try:
                events = history.all() if hasattr(history, "all") and callable(history.all) else []
            except Exception:
                events = []
            report["history_events"] = len(events)

            total_deltas: Dict[str, float] = {}
            for event in events:
                if event is None:
                    continue
                try:
                    affected = event.affected_traits if hasattr(event, "affected_traits") else None
                except Exception:
                    affected = None
                if affected is None and isinstance(event, dict):
                    affected = event.get("affected_traits")
                if not isinstance(affected, dict):
                    continue
                for trait_name, delta in affected.items():
                    if not isinstance(trait_name, str) or not trait_name:
                        continue
                    try:
                        delta_f = float(delta)
                    except (TypeError, ValueError):
                        continue
                    total_deltas[trait_name] = total_deltas.get(trait_name, 0.0) + delta_f
            report["deltas"] = dict(total_deltas)

            values: Dict[str, float] = {}
            for trait_name, total_delta in total_deltas.items():
                base_value = 0.5
                if isinstance(base, dict) and trait_name in base:
                    try:
                        base_value = float(base[trait_name])
                    except (TypeError, ValueError):
                        base_value = 0.5
                current = base_value + total_delta
                if current < 0.0:
                    current = 0.0
                if current > 1.0:
                    current = 1.0
                values[trait_name] = round(current, 4)
            report["values"] = values
            report["affected_traits"] = len(values)
            return report

        except Exception:
            return report

    # ============================================================
    # P2.5.1: 补偿 growth_records（反演 tanh 压缩，消除 resolve drift）
    # ============================================================
    # 背景：
    #   GrowthAccumulator.compute() 用 tanh(raw*3.0)*0.2 压缩 delta。
    #   直接注入原始 delta 会导致 accumulated < current_value，仍产生 drift。
    #   解决方案：反演 tanh，让 accumulated == current_value，growth_delta ≈ 0。
    #
    # 数学：
    #   目标: BASE + tanh(raw*3.0)*0.2 = BASE + total_delta
    #   => raw = atanh(total_delta / 0.2) / 3.0
    #   约束: |total_delta / 0.2| < 1 (否则 atanh 发散)，clamp 到 ±0.199
    _TANH_SCALE = 3.0
    _GROWTH_SCALE = 0.2
    _COMPENSATION_CLAMP = 0.199  # 略小于 0.2，避免 atanh 边界

    def rebuild_growth_records(
        self,
        adapter: Any,
        base: Optional[Dict[str, float]] = None,
    ) -> List[Dict[str, Any]]:
        """
        构造补偿后的 growth_records，使 GrowthAccumulator.compute() 输出 == TraitRebuilder.rebuild() 输出。

        输出格式（GrowthAccumulator 兼容）：
            [{
                "affected_dimensions": {"warmth": 0.0334},  # 补偿后的 delta
                "confidence": 1.0,
                "source_type": "restored",   # weight=1.0
                "created_at": "",             # activity=1.0
            }]

        注入 resolver.growth_records 后：
            accumulated = BASE + tanh(compensated_raw * 3.0) * 0.2 = BASE + total_delta = current_value
            growth_delta = accumulated - current_value ≈ 0
            => resolve() 不再产生 drift

        异常返回 []。
        """
        try:
            total_deltas = self._collect_total_deltas(adapter)
            if not total_deltas:
                return []

            if base is None:
                try:
                    from src.personality.personality_profile import PersonalityProfile
                    base = dict(PersonalityProfile.get_base() or {})
                except Exception:
                    base = {}

            records: List[Dict[str, Any]] = []
            for trait, total_delta in total_deltas.items():
                # clamp total_delta 到 ±_COMPENSATION_CLAMP，避免 atanh 发散
                clamped_delta = max(-self._COMPENSATION_CLAMP,
                                    min(self._COMPENSATION_CLAMP, total_delta))
                # 反演 tanh: raw = atanh(delta/scale) / tanh_scale
                ratio = clamped_delta / self._GROWTH_SCALE
                # ratio 应在 (-1, 1)，clamp 保护
                ratio = max(-0.999, min(0.999, ratio))
                compensated_raw = math.atanh(ratio) / self._TANH_SCALE

                records.append({
                    "affected_dimensions": {trait: round(compensated_raw, 6)},
                    "confidence": 1.0,
                    "source_type": "restored",
                    "created_at": "",
                })

            return records

        except Exception:
            return []

    def _collect_total_deltas(self, adapter: Any) -> Dict[str, float]:
        """从 adapter history 累加 affected_traits delta（内部复用）。"""
        try:
            if adapter is None:
                return {}
            history = adapter.get_history() if hasattr(adapter, "get_history") and callable(adapter.get_history) else None
            if history is None:
                return {}
            events = history.all() if hasattr(history, "all") and callable(history.all) else []
            if not events:
                return {}

            total_deltas: Dict[str, float] = {}
            for event in events:
                if event is None:
                    continue
                try:
                    affected = event.affected_traits if hasattr(event, "affected_traits") else None
                except Exception:
                    affected = None
                if affected is None and isinstance(event, dict):
                    affected = event.get("affected_traits")
                if not isinstance(affected, dict):
                    continue
                for trait_name, delta in affected.items():
                    if not isinstance(trait_name, str) or not trait_name:
                        continue
                    try:
                        delta_f = float(delta)
                    except (TypeError, ValueError):
                        continue
                    total_deltas[trait_name] = total_deltas.get(trait_name, 0.0) + delta_f
            return total_deltas
        except Exception:
            return {}


# ============================================================
# 兼容注入函数
# ============================================================
def inject_trait_states(resolver: Any, values: Dict[str, float]) -> bool:
    """
    把 {"warmth": 0.72, ...} 安全注入 PersonalityResolver。

    注入策略（按优先级，任何一步成功返回 True）：
    1) public API: resolver.set_trait_states({"warmth": TraitState(...), ...})
    2) fallback 公开 API: resolver.set_trait_values({"warmth": 0.72, ...})
    3) 私有属性: resolver._trait_states.update({"warmth": create_trait_state(...), ...})

    全部失败返回 False，不影响启动。

    Args:
        resolver: PersonalityResolver 实例
        values: Dict[str, float]（TraitRebuilder.rebuild() 输出）

    Returns:
        bool: 注入是否成功（至少注入了 1 个值）
    """
    if not isinstance(values, dict) or not values:
        return False

    # ---------- Strategy 1: set_trait_states 公开 API ----------
    setter = getattr(resolver, "set_trait_states", None)
    if callable(setter):
        try:
            states = _values_to_trait_states(values)
            setter(states)
            return True
        except Exception:
            pass

    # ---------- Strategy 2: set_trait_values 公开 API（更匹配我们的输入） ----------
    setter_v = getattr(resolver, "set_trait_values", None)
    if callable(setter_v):
        try:
            setter_v(dict(values))
            return True
        except Exception:
            pass

    # ---------- Strategy 3: 私有属性 fallback ----------
    internal = getattr(resolver, "_trait_states", None)
    if isinstance(internal, dict):
        try:
            states = _values_to_trait_states(values)
            internal.update(states)
            return bool(states)
        except Exception:
            pass

    return False


def inject_growth_records(resolver: Any, records: List[Dict[str, Any]]) -> bool:
    """
    P2.5.1: 把补偿后的 growth_records 注入 PersonalityResolver。

    注入策略（按优先级）：
    1) public API: resolver.set_growth_records(records)
    2) 直接赋值: resolver.growth_records = records

    Args:
        resolver: PersonalityResolver 实例
        records: rebuild_growth_records() 输出的 list

    Returns:
        bool: 注入是否成功
    """
    if resolver is None or not isinstance(records, list):
        return False
    try:
        setter = getattr(resolver, "set_growth_records", None)
        if callable(setter):
            try:
                setter(records)
                return True
            except Exception:
                pass
        # 直接赋值
        setattr(resolver, "growth_records", records)
        return True
    except Exception:
        return False


def _values_to_trait_states(values: Dict[str, float]) -> Dict[str, Any]:
    """把原始值字典转成 TraitState dict。延迟 import，只在注入时使用。"""
    try:
        from src.personality.trait_state import create_trait_state
    except Exception:
        def create_trait_state(trait, v):  # pragma: no cover - 兜底
            return {
                "trait": trait,
                "current_value": v,
                "momentum": 0.1,
                "direction": "stable",
                "stability": 0.3,
                "confidence": 0.1,
                "last_growth_direction": "stable",
                "last_updated": "",
                "consecutive_same_direction": 0,
            }

    result: Dict[str, Any] = {}
    for trait, value in values.items():
        if not isinstance(trait, str) or not trait:
            continue
        try:
            vf = float(value)
        except (TypeError, ValueError):
            continue
        if vf < 0.0:
            vf = 0.0
        if vf > 1.0:
            vf = 1.0
        result[trait] = create_trait_state(trait, round(vf, 4))
    return result
