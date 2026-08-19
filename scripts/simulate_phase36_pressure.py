# -*- coding: utf-8 -*-
"""
Phase 3.6.1: 压力测试 — Growth Stability Boundary
模拟 100 次互动下人格演化的动力学边界。

三个 Case：
  Case 1 持续正向：用户持续表达喜欢温柔/自然/主动关心。预期 warmth → 0.80~0.90 饱和。
  Case 2 持续负向：用户持续让羽依不要太主动。预期 warmth → 0.55~0.65 有底限。
  Case 3 真实分布 ：20% 正向 / 10% 负向 / 70% 中性。预期各维度在 [BASE±0.15] 内。

为每个 Case 输出：
  - T0 BASE → T10 → T50 → T100 各维度值
  - 单次最大 Δ（正向/负向）
  - Resolve 100 次后的 decay 曲线（不新增成长事件，只 resolve 看漂移）
  - Personality Half-Life：一次 warmth +0.04 之后，Δ 减少到一半所需 resolve 次数
  - Flags：是否饱和（接近 0 或 1），是否漂移吃光成长

用法：
  python scripts/simulate_phase36_pressure.py
  python scripts/simulate_phase36_pressure.py --cases 1,3     # 只跑指定 case
  python scripts/simulate_phase36_pressure.py --events 200   # 自定义事件数
  python scripts/simulate_phase36_pressure.py --format json  # 机器可读
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ============================================================
# Setup
# ============================================================
def _adapter(data_dir: str):
    from src.personality.self_model_adapter import SelfModelAdapter
    from src.personality.self_model_persistence import SelfModelPersistence
    adapter = SelfModelAdapter(actor="sim_phase36_pressure")
    adapter.attach_persistence(SelfModelPersistence(data_dir))
    return adapter


def _pcr(traits: Dict[str, float], summary: str, confidence: float = 0.8) -> Dict[str, Any]:
    return {
        "request_id": f"pcr_{uuid.uuid4().hex[:8]}",
        "source_proposal_id": f"prop_{uuid.uuid4().hex[:8]}",
        "reason": summary,
        "confidence": confidence,
        "evidence_count": 1,
        "evolution_record": {"trait_changes": dict(traits)},
    }


def _apply(adapter, traits, summary, confidence=0.8):
    # P2.5.2: 饱和保护 — 距离边界越近，允许的 delta 越小
    from src.runtime.saturation_guard import clamp_pcr_deltas
    current = _rebuild_current(adapter)
    clamped_traits = clamp_pcr_deltas(current, traits, ratio=0.1)
    r = adapter.apply_pcr(_pcr(clamped_traits, summary, confidence))
    assert r.get("applied"), f"PCR 未应用: {r}"


def _rebuild_current(adapter) -> Dict[str, float]:
    from src.runtime.trait_rebuilder import TraitRebuilder
    return TraitRebuilder().rebuild(adapter)


def _new_resolver_with_replay(adapter):
    from src.personality.personality_resolver import PersonalityResolver
    from src.runtime.trait_rebuilder import (
        TraitRebuilder, inject_trait_states, inject_growth_records,
    )
    from src.runtime.growth_history_view import inject_growth_history_view
    resolver = PersonalityResolver()
    values = TraitRebuilder().rebuild(adapter)
    if values:
        inject_trait_states(resolver, values)
    # P2.5.1: 注入补偿 growth_records，消除 resolve drift
    records = TraitRebuilder().rebuild_growth_records(adapter)
    if records:
        inject_growth_records(resolver, records)
    inject_growth_history_view(resolver, adapter)
    return resolver


# ============================================================
# Event generators
# ============================================================
def gen_positive(i: int, rnd: random.Random) -> Tuple[Dict[str, float], str, float]:
    """正向反馈：用户说"喜欢自然/温柔/关心你"之类的。幅度中等偏小。"""
    choices = [
        ({"warmth": 0.025, "caring": 0.02}, "用户喜欢更温柔的表达，关心更多一点。", 0.88),
        ({"warmth": 0.03, "shyness": -0.005}, "用户反馈聊天很自然，希望继续这样。", 0.9),
        ({"caring": 0.025, "emotional_expression": 0.015}, "用户感谢羽依记得他之前的事。", 0.86),
        ({"warmth": 0.02, "sensitivity": 0.01}, "用户觉得被理解了。", 0.85),
    ]
    traits, s, c = rnd.choice(choices)
    return traits, f"[#{i:03d}] " + s, c


def gen_negative(i: int, rnd: random.Random) -> Tuple[Dict[str, float], str, float]:
    """负向反馈：用户觉得太主动/太热情。"""
    choices = [
        ({"warmth": -0.015, "initiative": -0.005, "shyness": 0.01}, "用户说不要太主动，安静一点。", 0.82),
        ({"caring": -0.015, "warmth": -0.01, "sensitivity": 0.01}, "用户觉得被问得太多、压力大。", 0.84),
        ({"shyness": 0.02, "warmth": -0.015, "gentleness": -0.01}, "用户说不要过于委婉，直接一点。", 0.8),
    ]
    traits, s, c = rnd.choice(choices)
    return traits, f"[#{i:03d}] " + s, c


def gen_neutral(i: int, rnd: random.Random) -> Tuple[Dict[str, float], str, float]:
    """中性对话：大部分时候发生。无变化或极小变化。"""
    # 70% 真正中性：0 变化。30% 轻微波动（±0.002），模拟自然的聊天感知。
    if rnd.random() < 0.7:
        return {}, f"[#{i:03d}] 普通闲聊，没有明显的成长信号。", 0.3
    # 轻微波动：某些 trait 有 ±0.001~0.003 噪声级变化
    traits = {}
    trait_pool = ["warmth", "gentleness", "shyness", "sensitivity", "caring", "emotional_expression"]
    n_traits = rnd.randint(1, 2)
    for t in rnd.sample(trait_pool, n_traits):
        delta = rnd.uniform(-0.003, 0.003)
        if abs(delta) < 0.0005:
            continue
        traits[t] = round(delta, 4)
    if not traits:
        return {}, f"[#{i:03d}] 平淡互动，无成长变化。", 0.2
    return traits, f"[#{i:03d}] 轻微感知波动。", 0.45


# ============================================================
# Core: run a single case
# ============================================================
def _snapshot(resolver) -> Dict[str, float]:
    data = {}
    vec = resolver.resolve()
    if hasattr(vec, "get_all") and callable(vec.get_all):
        data = dict(vec.get_all() or {})
    out = {}
    for k, v in data.items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def run_case(
    name: str,
    n_events: int,
    event_p: Dict[str, float],
    seed: int = 20260806,
    half_life_trait: str = "warmth",
    half_life_prime_delta: float = 0.04,
    n_resolve_decay: int = 100,
) -> Dict[str, Any]:
    """
    event_p: {"positive": 0.2, "negative": 0.1, "neutral": 0.7} 必须和为 1.
    """
    rnd = random.Random(seed)
    tmp = tempfile.mkdtemp(prefix=f"yuyi_p361_{name}_")
    data_dir = Path(tmp)
    adapter = _adapter(str(data_dir))
    from src.personality.personality_profile import PersonalityProfile
    BASE = PersonalityProfile.get_base()

    # ---------- 生成事件 ----------
    event_log: List[Dict[str, Any]] = []
    checkpoints: Dict[int, Dict[str, float]] = {}
    # checkpoints at T0 (BASE), then after events 10, 50, 100, ..., n_events
    def _cp(slot_idx):  # slot_idx: 0→before any, N→after N events
        # build resolver with replay → take snapshot via PersonalityVector (traits as they appear to LLM)
        resolver = _new_resolver_with_replay(adapter)
        # 注意：resolve() 会触发一次 drift，所以在测 checkpoints 时我们调用 resolve_once，存原始 _trait_states 更准。
        states = resolver.get_trait_states() or {}
        snapshot = {}
        for k, ts in states.items():
            if isinstance(ts, dict) and "current_value" in ts:
                try:
                    snapshot[k] = float(ts["current_value"])
                except (TypeError, ValueError):
                    pass
        # 把 BASE 中未出现的 trait 也填进来（保持表格对称）
        for k in BASE.keys():
            snapshot.setdefault(k, BASE[k])
        checkpoints[slot_idx] = snapshot

    _cp(0)

    keys = list(event_p.keys())
    weights = [event_p[k] for k in keys]
    gens = {"positive": gen_positive, "negative": gen_negative, "neutral": gen_neutral}
    applied_events = 0

    for i in range(1, n_events + 1):
        # pick event type
        ev_type = rnd.choices(keys, weights=weights, k=1)[0]
        traits, summary, conf = gens[ev_type](i, rnd)
        if traits:
            _apply(adapter, traits, summary, conf)
            applied_events += 1
            event_log.append({"i": i, "type": ev_type, "traits": traits})
        else:
            event_log.append({"i": i, "type": ev_type, "traits": {}})
        adapter.save_state()
        if i in (10, 25, 50, 75, 100, n_events):
            _cp(i)

    if n_events not in checkpoints:
        _cp(n_events)

    # ---------- 统计：单次最大 Δ（从 event_log 的 traits 聚合）----------
    per_event_delta_total: Dict[str, List[float]] = defaultdict(list)
    for ev in event_log:
        for t, d in (ev.get("traits") or {}).items():
            per_event_delta_total[t].append(d)
    single_max_pos = {
        t: round(max(vs), 4) for t, vs in per_event_delta_total.items() if vs
    }
    single_max_neg = {
        t: round(min(vs), 4) for t, vs in per_event_delta_total.items() if vs
    }
    total_delta = {
        t: round(sum(vs), 4) for t, vs in per_event_delta_total.items() if vs
    }

    # ---------- Resolve Decay Curve ----------
    # 从最终状态重建 resolver。然后连续 resolve N 次，每个 BASE 维度记录初始值和每次的值。
    resolver = _new_resolver_with_replay(adapter)
    start_snap = dict(_snapshot(resolver))
    decay_samples: Dict[str, List[float]] = {k: [start_snap.get(k, 0.0)] for k in BASE}
    for _ in range(n_resolve_decay):
        s = _snapshot(resolver)
        for k in BASE:
            decay_samples[k].append(s.get(k, decay_samples[k][-1]))
    # 最终值和相对衰减（以 T0 为 100%）
    decay_final_pct: Dict[str, float] = {}
    drift_per_resolve: Dict[str, float] = {}
    for k in BASE:
        series = decay_samples[k]
        start = series[0]
        end = series[-1]
        change_since_start = end - start
        drift_per_resolve[k] = round(change_since_start / max(1, len(series) - 1), 6)
        base_delta = start - BASE.get(k, start)
        if abs(base_delta) < 1e-6:
            decay_final_pct[k] = 1.0 if abs(change_since_start) < 1e-6 else 0.0
        else:
            remaining = end - BASE.get(k, start)
            decay_final_pct[k] = round(remaining / base_delta, 4)

    # ---------- Personality Half-Life ----------
    # Method：在空 adapter 上只 inject warmth=BASE+prime_delta（默认 0.04）
    # 然后持续 resolve，记录 Δ 降到 prime_delta/2 所需的次数。
    half_life = _measure_half_life(
        trait=half_life_trait,
        prime_delta=half_life_prime_delta,
        max_steps=n_resolve_decay * 2,
    )

    # ---------- 饱和检查 ----------
    final_snap = checkpoints.get(n_events, checkpoints[0])
    saturation_flags = []
    for k in BASE:
        v = final_snap.get(k, BASE.get(k, 0.5))
        if v >= 0.95:
            saturation_flags.append(f"{k}=+sat({v:.3f})")
        elif v <= 0.05:
            saturation_flags.append(f"{k}=-sat({v:.3f})")

    growth_eaten_flags = []
    for k in BASE:
        expected = total_delta.get(k, 0.0)
        final_s = final_snap.get(k, BASE.get(k, 0.5))
        actual_delta = final_s - BASE.get(k, final_s)
        # 如果 total_delta ≥ 0.02，但 actual_delta 比 total_delta 小 50% 以上：漂移吃了成长
        if abs(expected) >= 0.02 and abs(actual_delta) < abs(expected) * 0.5:
            growth_eaten_flags.append(
                f"{k} ΣΔ={expected:+.3f} → 实际Δ={actual_delta:+.3f} (丢失 {round(100-100*abs(actual_delta/expected),1)}%)"
            )

    flags = []
    if saturation_flags:
        flags.append("saturation")
    if growth_eaten_flags:
        flags.append("drift_eats_growth")
    if decay_final_pct and min(decay_final_pct.values()) < 0.3:
        flags.append("fast_decay")

    return {
        "name": name,
        "seed": seed,
        "n_events": n_events,
        "applied_events_with_traits": applied_events,
        "event_distribution": event_p,
        "tmp_dir": str(data_dir),
        "checkpoints": {
            str(k): {kk: round(vv, 4) for kk, vv in v.items()}
            for k, v in checkpoints.items()
        },
        "single_event": {
            "max_positive_delta": single_max_pos,
            "max_negative_delta": single_max_neg,
            "total_delta": total_delta,
        },
        "decay_analysis": {
            "n_resolve": n_resolve_decay,
            "final_pct_of_growth_remaining": decay_final_pct,
            "drift_per_resolve": drift_per_resolve,
        },
        "half_life": half_life,
        "flags": flags,
        "saturation_details": saturation_flags,
        "growth_eaten_details": growth_eaten_flags,
    }


def _measure_half_life(
    trait: str = "warmth",
    prime_delta: float = 0.04,
    max_steps: int = 200,
) -> Dict[str, Any]:
    """测量一个 trait 的 Personality Half-Life。"""
    with tempfile.TemporaryDirectory() as tmp:
        adapter = _adapter(tmp)
        # 先应用一个 prime 的 PCR
        _apply(adapter, {trait: prime_delta},
               f"Half-life 实验：给 {trait} 施加一个 {prime_delta:+.3f} 的单脉冲。",
               confidence=0.9)
        adapter.save_state()
        resolver = _new_resolver_with_replay(adapter)
        from src.personality.personality_profile import PersonalityProfile
        base = PersonalityProfile.get_base().get(trait, 0.5)

        def _current():
            ts = resolver.get_trait_states() or {}
            v = ts.get(trait, {}).get("current_value") if isinstance(ts.get(trait), dict) else None
            if v is None:
                # 从 resolve 拿
                vec = resolver.resolve()
                data = dict(vec.get_all() or {}) if hasattr(vec, "get_all") else {}
                return data.get(trait, base)
            return v

        # Call resolve 一次以初始化 _trait_states
        resolver.resolve()
        start = _current()
        half_delta = prime_delta / 2.0
        half_target = base + half_delta
        steps_to_half: Optional[int] = None
        # series 记录每个 step 的 Δ 剩余量
        series: List[Dict[str, float]] = []
        for step in range(1, max_steps + 1):
            resolver.resolve()
            cur = _current()
            remaining = cur - base
            series.append({"step": step, "value": round(cur, 5), "remaining_delta": round(remaining, 5)})
            if steps_to_half is None:
                # 穿过 half_target 的时刻
                if prime_delta > 0 and cur <= half_target:
                    steps_to_half = step
                elif prime_delta < 0 and cur >= half_target:
                    steps_to_half = step
        result = {
            "trait": trait,
            "prime_delta": prime_delta,
            "half_target": round(half_target, 5),
            "base": base,
            "start": round(start, 5),
            "steps_to_half": steps_to_half,
            "max_steps_simulated": max_steps,
            "steps_to_half_interpretation": (
                f"~{steps_to_half} 次 resolve()" if steps_to_half is not None
                else f"在 {max_steps} 次 resolve 内未下降到一半"
            ),
            "final_after_max_steps": round(series[-1]["value"], 5) if series else round(start, 5),
            "remaining_pct_after_max_steps": None,
            "sample_series": [series[0], series[9], series[24], series[49], series[-1]]
                if len(series) >= 50 else series[: min(5, len(series))],
        }
        # 剩余 Δ 比例
        if series:
            final_remaining = series[-1]["remaining_delta"]
            if abs(prime_delta) > 0:
                result["remaining_pct_after_max_steps"] = round(final_remaining / prime_delta, 4)
        return result


# ============================================================
# Display
# ============================================================
def _fmt_delta(d):
    try:
        return f"{float(d):+.4f}"
    except Exception:
        return str(d)


def render_case(case: Dict[str, Any]) -> str:
    lines: List[str] = []
    a = lines.append
    a("\n" + "╔" + "═" * 70 + "╗")
    a(f"║  Case: {case['name']:<61}║")
    a("╚" + "═" * 70 + "╝")
    dist = case["event_distribution"]
    a(f"事件分布: {dist}   总事件: {case['n_events']}   实际改变 trait 的次数: {case['applied_events_with_traits']}")
    a(f"临时数据: {case['tmp_dir']}")
    if case["flags"]:
        a(f"⚠️  Flags: {', '.join(case['flags'])}")
    a("")

    # ---------- Checkpoints ----------
    cpts = case["checkpoints"]
    trait_keys = sorted(set(k for v in cpts.values() for k in v.keys()))
    cpt_keys = sorted(cpts.keys(), key=lambda s: int(s))
    header = f"  {'维度':<22}" + "".join(f" T={k:>5}" for k in cpt_keys)
    a("━━━  演化轨迹（各 trait 的 TraitState.current_value）")
    a(header)
    for t in trait_keys:
        row = f"  {t:<22}"
        for k in cpt_keys:
            row += f" {cpts[k].get(t, 0):.4f}"
        a(row)
    a("")

    # ---------- Single event stats ----------
    smax_p = case["single_event"]["max_positive_delta"]
    smax_n = case["single_event"]["max_negative_delta"]
    tdelta = case["single_event"]["total_delta"]
    a("━━━  事件 Δ 统计")
    a(f"  {'维度':<22} {'单次最大+':>10} {'单次最大-':>10} {'累计ΣΔ':>10}")
    for t in sorted(set(smax_p.keys()) | set(smax_n.keys()) | set(tdelta.keys())):
        a(f"  {t:<22} {smax_p.get(t, 0):>+10.4f} {smax_n.get(t, 0):>+10.4f} {tdelta.get(t, 0):>+10.4f}")
    a("")

    # ---------- Decay ----------
    decay = case["decay_analysis"]
    a(f"━━━  Resolve Decay（仅 resolve {decay['n_resolve']} 次，不再有成长事件）")
    a(f"  {'维度':<22} {'剩余成长%':>10} {'每resolveΔ':>14}")
    pct = decay["final_pct_of_growth_remaining"]
    dpr = decay["drift_per_resolve"]
    for t in sorted(pct.keys()):
        a(f"  {t:<22} {pct[t]*100:>9.1f}%   {dpr[t]:>+14.6f}")
    a("")

    # ---------- Half Life ----------
    hl = case["half_life"]
    a("━━━  Personality Half-Life（单脉冲实验）")
    a(f"  施加 Δ{hl['prime_delta']:+.3f} 到 {hl['trait']}"
      f"  (BASE={hl['base']:.4f} → 初始={hl['start']:.4f}，目标一半={hl['half_target']:.4f})")
    a(f"  → 半衰期: {hl['steps_to_half_interpretation']}")
    a(f"  → 模拟 {hl['max_steps_simulated']} 次 resolve 后: value={hl['final_after_max_steps']}"
      + (f"，剩余成长 Δ 的 {hl['remaining_pct_after_max_steps']*100:.1f}%"
         if hl.get('remaining_pct_after_max_steps') is not None else ""))
    if hl.get("sample_series"):
        a("  → 采样点 (step/value/remainingΔ):")
        for p in hl["sample_series"]:
            a(f"     step {p['step']:>3d}: value={p['value']:.4f} rem={p['remaining_delta']:+.4f}")
    a("")

    # ---------- Details ----------
    if case["saturation_details"]:
        a("⚠️  饱和细节: " + " | ".join(case["saturation_details"]))
    if case["growth_eaten_details"]:
        a("⚠️  漂移吃成长: " + " | ".join(case["growth_eaten_details"]))

    return "\n".join(lines)


# ============================================================
# Summary matrix
# ============================================================
def render_summary(cases: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    a = lines.append
    a("\n" + "=" * 72)
    a("Phase 3.6.1 压力测试汇总对比（3 Case 矩阵）")
    a("=" * 72)
    a(f"  {'Case':<22} {'最终warmth':>10} {'最终shyness':>11} {'单脉冲warmth半衰期':>24} {'flags':<20}")
    for c in cases:
        final_cpt = c["checkpoints"].get(str(c["n_events"]), c["checkpoints"].get("0", {}))
        warmth = final_cpt.get("warmth", float("nan"))
        shy = final_cpt.get("shyness", float("nan"))
        hl = c["half_life"]
        hl_s = (
            f"~{hl['steps_to_half']}次 resolve" if hl["steps_to_half"] is not None
            else f"> {hl['max_steps_simulated']}"
        )
        a(f"  {c['name']:<22} {warmth:>10.4f} {shy:>11.4f} {hl_s:>24} {', '.join(c['flags']) or '-':<20}")
    # decay 汇总
    a("")
    a(f"  {'Case':<22} {'100 resolve后warmth剩余%':>24} {'drift/resolve warmth':>20}")
    for c in cases:
        decay = c["decay_analysis"]
        pct = decay["final_pct_of_growth_remaining"].get("warmth")
        dpr = decay["drift_per_resolve"].get("warmth")
        a(f"  {c['name']:<22} {('n/a' if pct is None else f'{pct*100:.1f}%'):>24} "
          f"{('n/a' if dpr is None else f'{dpr:>+20.6f}')}")
    return "\n".join(lines)


# ============================================================
# main
# ============================================================
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--cases", default="1,2,3", help="要跑的 case，默认 1,2,3")
    parser.add_argument("--events", type=int, default=100, help="每 case 互动次数，默认 100")
    parser.add_argument("--seed", type=int, default=20260806)
    parser.add_argument("--decay", type=int, default=100, help="decay/half-life resolve 次数，默认 100")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    case_defs = {
        "1": ("Case1_positive_feedback", {"positive": 0.7, "neutral": 0.2, "negative": 0.1}),
        "2": ("Case2_negative_feedback", {"positive": 0.1, "neutral": 0.2, "negative": 0.7}),
        "3": ("Case3_realistic_20_10_70", {"positive": 0.2, "negative": 0.1, "neutral": 0.7}),
    }
    wanted = [c.strip() for c in args.cases.split(",") if c.strip() in case_defs]
    if not wanted:
        print("[ERROR] 没有合法的 case，请用 --cases 1,2,3", file=sys.stderr)
        return 2

    cases: List[Dict[str, Any]] = []
    for w in wanted:
        name, ep = case_defs[w]
        r = run_case(
            name=name,
            n_events=args.events,
            event_p=ep,
            seed=args.seed,
            n_resolve_decay=args.decay,
        )
        cases.append(r)

    if args.format == "json":
        print(json.dumps(cases, ensure_ascii=False, indent=2))
    else:
        for c in cases:
            print(render_case(c))
        print(render_summary(cases))

    return 0


if __name__ == "__main__":
    sys.exit(main())
