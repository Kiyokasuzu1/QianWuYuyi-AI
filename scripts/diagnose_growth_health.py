# -*- coding: utf-8 -*-
"""
Phase 3.6: diagnose_growth_health.py
成长健康度诊断脚本 — 用于 Phase 3.6 Growth Reality Test。

读入一个 data_dir（默认为 data/self_model），输出：
    1. 基础信息：历史条数、时间跨度、各维度变化次数
    2. 稳定性诊断：单次最大 delta、连续同向变化、是否趋向饱和（接近 0 或 1）
    3. 合理性诊断：平均 delta、异常事件（单次 delta > 阈值）、source_type 分布
    4. 可解释性诊断：summary 覆盖率、空意义记录占比
    5. 恢复性验证：TraitRebuilder 重建值 vs BASE 偏差

用法：
    python scripts/diagnose_growth_health.py
    python scripts/diagnose_growth_health.py --data-dir /path/to/self_model
    python scripts/diagnose_growth_health.py --warn-delta 0.05   # 自定义单次大变化阈值
    python scripts/diagnose_growth_health.py --show-events 5     # 打印前 N 条原始事件
    python scripts/diagnose_growth_health.py --format json       # 机器可读输出（用于未来定时巡检）
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ============================================================
# Core
# ============================================================
def _load_adapter(data_dir: str):
    from src.personality.self_model_adapter import SelfModelAdapter
    from src.personality.self_model_persistence import SelfModelPersistence

    adapter = SelfModelAdapter(actor="diagnose")
    persistence = SelfModelPersistence(data_dir)
    adapter.attach_persistence(persistence)
    counts = adapter.load_state()
    return adapter, counts


def _collect_events(adapter) -> List[Tuple[str, Dict[str, Any]]]:
    """返回 [(event_id, event)] 列表，按 timestamp 排序。"""
    events_raw = []
    try:
        history = adapter.get_history()
        for event in history.all():
            event_id = getattr(event, "event_id", str(uuid.uuid4()))
            if not event_id and isinstance(event, dict):
                event_id = event.get("event_id", "")
            events_raw.append((event_id, event))
    except Exception as e:
        print(f"[WARN] 读取 history 失败: {e}")
        return []

    def _ts(e):
        t = getattr(e, "timestamp", None)
        if not t and isinstance(e, dict):
            t = e.get("timestamp")
        return t or ""

    events_raw.sort(key=lambda kv: _ts(kv[1]))
    return events_raw


def _field(event, key, default=None):
    v = getattr(event, key, None)
    if v is None and isinstance(event, dict):
        v = event.get(key)
    return default if v is None else v


def _estimate_drift(adapter, n_resolve: int = 6) -> Dict[str, Any]:
    """
    用 n_resolve 次仿真估算漂移速度和 Personality Half-Life。

    返回：
    {
      "warmth": {"drift_per_resolve": -0.002, "remaining_after_6": 0.75, "half_life_resolves": 14, ...},
      "mean_abs_drift_per_resolve": 0.0015,
      "has_rapid_decay": bool,
      "half_life_warmth_resolves": int or null,
      "sample_series": {...},
    }
    """
    from src.personality.personality_resolver import PersonalityResolver
    from src.runtime.trait_rebuilder import TraitRebuilder, inject_trait_states
    from src.personality.personality_profile import PersonalityProfile

    try:
        resolver = PersonalityResolver()
        values = TraitRebuilder().rebuild(adapter)
        if values:
            inject_trait_states(resolver, values)
    except Exception as e:
        return {"error": str(e)}

    base = PersonalityProfile.get_base()
    series: Dict[str, List[float]] = {}

    for step in range(n_resolve):
        try:
            vec = resolver.resolve()
            data = dict(vec.get_all() or {}) if hasattr(vec, "get_all") else {}
        except Exception:
            data = {}
        for k in base.keys():
            series.setdefault(k, [])
            try:
                v = float(data.get(k, series[k][-1])) if series[k] else float(data.get(k, base[k]))
            except (TypeError, ValueError):
                v = series[k][-1] if series[k] else base[k]
            series[k].append(v)

    per_trait: Dict[str, Any] = {}
    abs_drifts = []
    for k in base.keys():
        s = series[k]
        if len(s) < 2:
            continue
        start = s[0]
        end = s[-1]
        delta_total = end - start
        drift = delta_total / (len(s) - 1) if len(s) > 1 else 0.0
        base_delta = start - base[k]
        if abs(base_delta) < 1e-5:
            remaining_pct = 1.0 if abs(delta_total) < 1e-5 else 0.0
        else:
            end_base_delta = end - base[k]
            remaining_pct = end_base_delta / base_delta

        # half-life: steps until remaining_pct crosses 0.5
        half_life_steps: Optional[int] = None
        for i in range(1, len(s)):
            cur_delta = s[i] - base[k]
            if abs(base_delta) > 1e-5:
                cur_pct = cur_delta / base_delta
                # crossing
                if (base_delta > 0 and cur_pct <= 0.5) or (base_delta < 0 and cur_pct >= 0.5):
                    half_life_steps = i
                    break
        per_trait[k] = {
            "start": round(start, 5),
            "end": round(end, 5),
            "drift_per_resolve": round(drift, 7),
            "remaining_pct_after_n": round(remaining_pct, 4),
            "half_life_resolves": half_life_steps,
        }
        abs_drifts.append(abs(drift))

    warmth_hl = per_trait.get("warmth", {}).get("half_life_resolves")

    # rapid_decay: any trait with remaining_pct < 0.5 after n_resolve when base_delta != 0
    rapid = any(
        v["remaining_pct_after_n"] < 0.5
        for v in per_trait.values()
        if not (series[list(per_trait.keys())[0]][0] - base[list(per_trait.keys())[0]]) < 1e-5  # 有成长的才检查
        if False
    )
    # 更准确：检查每个 trait 是否 start != base AND end-base / start-base < 0.5
    for k, info in per_trait.items():
        start_delta = info["start"] - base[k]
        if abs(start_delta) > 0.005 and info["remaining_pct_after_n"] < 0.5:
            rapid = True
            break

    return {
        "per_trait": per_trait,
        "n_resolve_simulated": n_resolve,
        "mean_abs_drift_per_resolve": round(sum(abs_drifts) / len(abs_drifts), 7) if abs_drifts else 0.0,
        "half_life_warmth_resolves": warmth_hl,
        "has_rapid_decay": rapid,
        "sample_series": {
            k: [round(x, 4) for x in v] for k, v in series.items() if k in ("warmth", "shyness")
        },
    }


def _detect_saturation(adapter) -> Dict[str, Any]:
    """检测当前重建值是否已接近 0 或 1（饱和）。"""
    from src.runtime.trait_rebuilder import TraitRebuilder
    from src.personality.personality_profile import PersonalityProfile

    try:
        values = TraitRebuilder().rebuild(adapter)
    except Exception as e:
        return {"error": str(e)}
    base = PersonalityProfile.get_base()

    saturated: List[Dict[str, Any]] = []
    near_saturated: List[Dict[str, Any]] = []
    for k in set(base.keys()) | set(values.keys()):
        v = values.get(k, base.get(k, 0.5))
        bv = base.get(k, v)
        if v >= 0.95 or v <= 0.05:
            saturated.append({"trait": k, "value": round(v, 4), "base": round(bv, 4)})
        elif v >= 0.9 or v <= 0.1:
            near_saturated.append({"trait": k, "value": round(v, 4), "base": round(bv, 4)})

    return {
        "has_saturation": len(saturated) > 0,
        "has_near_saturation": len(near_saturated) > 0,
        "saturated": saturated,
        "near_saturated": near_saturated,
    }


def diagnose(data_dir: str, warn_delta: float = 0.05) -> Dict[str, Any]:
    report: Dict[str, Any] = {"data_dir": data_dir}

    # ---------------- Load ----------------
    adapter, load_counts = _load_adapter(data_dir)
    report["loaded_counts"] = load_counts
    events = _collect_events(adapter)
    report["event_count"] = len(events)

    if not events:
        report["status"] = "empty"
        report["summary"] = "无成长记录（新用户或数据目录为空）"
        return report

    # ---------------- 1. 基础信息 ----------------
    timestamps = [_field(e, "timestamp", "") or "" for _, e in events]
    valid_ts = [t for t in timestamps if t]
    time_span = None
    if len(valid_ts) >= 2:
        try:
            fmt = "%Y-%m-%dT%H:%M:%S"
            t0 = datetime.fromisoformat(valid_ts[0][:19])
            t1 = datetime.fromisoformat(valid_ts[-1][:19])
            days = (t1 - t0).total_seconds() / 86400.0
            time_span = {"days": round(days, 2), "start": valid_ts[0], "end": valid_ts[-1]}
        except Exception:
            time_span = None
    report["time_span"] = time_span

    # 维度统计
    per_trait_total_delta: Dict[str, float] = defaultdict(float)
    per_trait_count: Dict[str, int] = defaultdict(int)
    per_trait_changes: Dict[str, List[float]] = defaultdict(list)
    single_max_delta: Dict[str, float] = {}
    source_type_count: Dict[str, int] = defaultdict(int)
    event_type_count: Dict[str, int] = defaultdict(int)
    summary_empty_count = 0
    anomaly_events: List[Dict[str, Any]] = []

    for idx, (eid, event) in enumerate(events):
        source_type = _field(event, "source_type", "") or ""
        event_type = _field(event, "event_type", "") or ""
        source_type_count[source_type] += 1
        event_type_count[event_type] += 1

        summary = _field(event, "summary", "") or ""
        if not summary.strip():
            summary_empty_count += 1

        affected = _field(event, "affected_traits", None)
        if not isinstance(affected, dict):
            continue
        for trait, delta in affected.items():
            try:
                delta_f = float(delta)
            except (TypeError, ValueError):
                continue
            per_trait_total_delta[trait] += delta_f
            per_trait_count[trait] += 1
            per_trait_changes[trait].append(delta_f)
            abs_d = abs(delta_f)
            if trait not in single_max_delta or abs_d > abs(single_max_delta[trait]):
                single_max_delta[trait] = delta_f
            if abs_d > warn_delta:
                anomaly_events.append({
                    "index": idx,
                    "event_id": eid,
                    "timestamp": _field(event, "timestamp", ""),
                    "trait": trait,
                    "delta": delta_f,
                    "summary": summary,
                    "source_type": source_type,
                    "note": f"单次变化 |{delta_f}| > 阈值 {warn_delta}",
                })

    report["per_trait"] = {
        trait: {
            "count": per_trait_count[trait],
            "total_delta": round(per_trait_total_delta[trait], 4),
            "avg_delta": round(per_trait_total_delta[trait] / per_trait_count[trait], 4)
                if per_trait_count[trait] else 0.0,
            "max_abs_delta_single": round(max([abs(d) for d in per_trait_changes[trait]], default=0.0), 4),
            "single_max_delta": round(single_max_delta.get(trait, 0.0), 4),
        }
        for trait in per_trait_count
    }
    report["source_type_distribution"] = dict(source_type_count)
    report["event_type_distribution"] = dict(event_type_count)

    # ---------------- 2. 稳定性 ----------------
    # 连续同向变化检测（同 trait 连续 >=3 次 same sign）
    consecutive_same: List[Dict[str, Any]] = []
    for trait, deltas in per_trait_changes.items():
        if len(deltas) < 3:
            continue
        streak = 1
        prev_sign = 1 if deltas[0] >= 0 else -1
        for i in range(1, len(deltas)):
            s = 1 if deltas[i] >= 0 else -1
            if s == prev_sign:
                streak += 1
            else:
                if streak >= 3:
                    consecutive_same.append({
                        "trait": trait, "streak_length": streak,
                        "sign": "positive" if prev_sign > 0 else "negative",
                    })
                streak = 1
                prev_sign = s
        if streak >= 3:
            consecutive_same.append({
                "trait": trait, "streak_length": streak,
                "sign": "positive" if prev_sign > 0 else "negative",
            })
    report["stability"] = {
        "consecutive_same_sign": consecutive_same,
        "consecutive_same_sign_count": len(consecutive_same),
    }

    # ---------------- 2b. 漂移估算（EvolutionEngine 的自然衰减动力学）
    # 用 resolve 3 次仿真，近似估计每 resolve 一次的漂移幅度，以及 Personality Half-Life。
    drift_report = _estimate_drift(adapter)
    report["stability"]["drift"] = drift_report

    # ---------------- 2c. 饱和检测
    saturation = _detect_saturation(adapter)
    report["stability"]["saturation"] = saturation

    # ---------------- 3. 合理性 ----------------
    report["reasonableness"] = {
        "anomaly_events": anomaly_events,
        "anomaly_count": len(anomaly_events),
        "warn_delta_threshold": warn_delta,
    }

    # ---------------- 4. 可解释性 ----------------
    empty_pct = round(summary_empty_count / len(events) * 100, 2) if events else 0.0
    report["explainability"] = {
        "summary_empty_count": summary_empty_count,
        "summary_empty_pct": empty_pct,
        "events_total": len(events),
    }

    # ---------------- 5. 恢复性验证 ----------------
    from src.runtime.trait_rebuilder import TraitRebuilder
    from src.personality.personality_profile import PersonalityProfile

    rebuilder = TraitRebuilder()
    rebuilt = rebuilder.rebuild(adapter)
    base = PersonalityProfile.get_base()
    recovery_report: Dict[str, Any] = {
        "rebuilt_traits": {},
        "per_trait_deviation_from_base": {},
    }
    for trait, value in rebuilt.items():
        bv = base.get(trait, 0.5)
        recovery_report["rebuilt_traits"][trait] = round(value, 4)
        recovery_report["per_trait_deviation_from_base"][trait] = round(value - bv, 4)
    report["recovery"] = recovery_report

    # ---------------- 最终状态 ----------------
    flags = []
    if anomaly_events:
        flags.append("anomaly")
    if consecutive_same:
        flags.append("drift_risk")
    if empty_pct > 20:
        flags.append("poor_explainability")
    if saturation and saturation.get("has_saturation"):
        flags.append("saturation")
    if drift_report and drift_report.get("has_rapid_decay"):
        flags.append("rapid_decay")
    report["flags"] = flags
    report["status"] = "ok" if not flags else f"warning_{'_'.join(flags)}"

    # 保存原始事件供排障（截取）
    report["_events_sample"] = [
        {
            "event_id": eid,
            "timestamp": _field(e, "timestamp", ""),
            "source_type": _field(e, "source_type", ""),
            "affected_traits": _field(e, "affected_traits", {}),
            "summary": _field(e, "summary", ""),
        }
        for eid, e in events[:10]
    ]
    return report


# ============================================================
# Display
# ============================================================
def _render_human(report: Dict[str, Any], show_events: int = 0) -> str:
    lines: List[str] = []
    a = lines.append

    a("=" * 72)
    a("羽依成长健康度诊断报告  [Phase 3.6 Growth Reality Test]")
    a("=" * 72)
    a(f"数据目录    : {report['data_dir']}")
    a(f"状态        : {report['status']}")
    if report.get("flags"):
        a(f"⚠️  标记       : {', '.join(report['flags'])}")
    a("")

    # ---- 基础 ----
    counts = report.get("loaded_counts", {})
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    a("1. 基础信息")
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    a(f"  事件总数          : {report.get('event_count', 0)}")
    a(f"  beliefs / history / reflections : {counts.get('beliefs', 0)} / {counts.get('history', 0)} / {counts.get('reflections', 0)}")
    ts = report.get("time_span")
    if ts:
        a(f"  时间跨度          : {ts['days']} 天  ({ts['start'][:19]} → {ts['end'][:19]})")
    else:
        a("  时间跨度          : 不足 2 条带时间的记录")
    src = report.get("source_type_distribution", {})
    if src:
        a("  来源分布          : " + ", ".join(f"{k}={v}" for k, v in src.items()))
    et = report.get("event_type_distribution", {})
    if et:
        a("  事件类型分布      : " + ", ".join(f"{k}={v}" for k, v in et.items()))
    a("")

    per_trait = report.get("per_trait", {})
    if per_trait:
        a("  各维度统计:")
        a(f"    {'维度':<22} {'次数':>4} {'总变化':>8} {'平均':>7} {'单次最大Δ':>10}")
        for trait, info in sorted(per_trait.items()):
            a(f"    {trait:<22} {info['count']:>4} {info['total_delta']:>+8.4f} "
              f"{info['avg_delta']:>+7.4f} {info['max_abs_delta_single']:>+10.4f}")
    a("")

    # ---- 稳定性 ----
    stab = report.get("stability", {})
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    a("2. 稳定性诊断")
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    consec = stab.get("consecutive_same_sign", [])
    if consec:
        a(f"  ⚠️  连续同向变化次数  : {len(consec)}")
        for c in consec:
            a(f"     - {c['trait']}: 连续 {c['streak_length']} 次 {c['sign']}")
    else:
        a("  ✅ 连续同向变化      : 无（好）")
    a("")

    # ---- 合理性 ----
    reason = report.get("reasonableness", {})
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    a(f"3. 合理性诊断（单次 |Δ| 阈值 = {reason.get('warn_delta_threshold', 0.05)}）")
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    anomalies = reason.get("anomaly_events", [])
    if anomalies:
        a(f"  ⚠️  异常大变化事件    : {len(anomalies)} 条")
        for a2 in anomalies[:5]:
            a(f"     - {a2['trait']:12s} Δ={a2['delta']:+.4f}  ts={str(a2['timestamp'])[:16]} "
              f"src={a2['source_type']}  summary={a2['summary'][:40]}")
        if len(anomalies) > 5:
            a(f"     ... 以及 {len(anomalies)-5} 条")
    else:
        a("  ✅ 异常大变化        : 无（好）")
    a("")

    # ---- 可解释性 ----
    expl = report.get("explainability", {})
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    a("4. 可解释性诊断")
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    a(f"  空 summary 记录     : {expl.get('summary_empty_count', 0)}/{expl.get('events_total', 0)} "
      f"({expl.get('summary_empty_pct', 0)}%)")
    if expl.get("summary_empty_pct", 0) > 20:
        a("  ⚠️  空 summary 占比高，部分成长事件缺乏人类可读的解释")
    else:
        a("  ✅ 可解释性          : 良好")
    a("")

    # ---- 恢复性 ----
    rec = report.get("recovery", {})
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    a("5. 恢复性验证（TraitRebuilder.rebuild() 输出）")
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    rebuilt = rec.get("rebuilt_traits", {})
    dev = rec.get("per_trait_deviation_from_base", {})
    if rebuilt:
        a(f"    {'维度':<22} {'当前值':>8} {'相对BASE':>10}")
        for trait in sorted(rebuilt.keys()):
            a(f"    {trait:<22} {rebuilt[trait]:>8.4f} {dev.get(trait, 0):>+10.4f}")
    else:
        a("  (无 trait 变化)")
    a("")

    # ---- 样本事件 ----
    if show_events > 0:
        sample = report.get("_events_sample", [])[:show_events]
        a(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        a(f"事件样本（前 {len(sample)} 条）")
        a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for i, s in enumerate(sample, 1):
            a(f"  [{i}] {s['event_id']}")
            a(f"       ts      : {s['timestamp']}")
            a(f"       source  : {s['source_type']}")
            a(f"       traits  : {json.dumps(s['affected_traits'], ensure_ascii=False)}")
            a(f"       summary : {s['summary'][:80]}")
        a("")

    a("=" * 72)
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--data-dir", default="data/self_model", help="SelfModel 数据目录")
    p.add_argument("--warn-delta", type=float, default=0.05,
                   help="单次变化告警阈值，默认 0.05")
    p.add_argument("--show-events", type=int, default=0,
                   help="打印前 N 条原始事件，默认 0（不打印）")
    p.add_argument("--format", choices=["text", "json"], default="text",
                   help="输出格式，默认 text")
    # Phase 3.6.3: 每日记录
    p.add_argument("--daily", action="store_true",
                   help="将 JSON 报告保存为 data/growth_health/day{N}_growth_report.json（自动递增）")
    p.add_argument("--day", type=int, default=None,
                   help="与 --daily 配合，强制指定 dayN（默认自动递增，按现有最大 N+1）")
    p.add_argument("--compare", type=str, default=None,
                   help="与指定的旧 JSON 报告对比（文件路径或 dayN 号），输出相对变化")
    args = p.parse_args(argv)

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"[WARN] 目录不存在: {data_dir}，将按空目录处理")
        data_dir.mkdir(parents=True, exist_ok=True)

    report = diagnose(str(data_dir), warn_delta=args.warn_delta)

    # --compare: 读取旧报告做对比
    compare_report = None
    if args.compare:
        cmp_path = _resolve_compare_path(args.compare)
        if cmp_path and cmp_path.exists():
            try:
                with open(cmp_path, "r", encoding="utf-8") as f:
                    compare_report = json.load(f)
            except Exception as e:
                print(f"[WARN] 对比报告读取失败: {cmp_path} ({e})")

    if args.format == "json":
        out = dict(report)
        if compare_report:
            out["diff_vs"] = {
                "file": str(cmp_path),
                "trait_delta": _compare_traits(report, compare_report),
            }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(_render_human(report, show_events=args.show_events))
        if compare_report:
            print(_render_compare(report, compare_report, str(cmp_path)))

    # --daily: 保存
    if args.daily:
        save_dir = _REPO / "data" / "growth_health"
        save_dir.mkdir(parents=True, exist_ok=True)
        day_no = args.day
        if day_no is None:
            existing = list(save_dir.glob("day*_growth_report.json"))
            nums = []
            for p in existing:
                stem = p.stem.replace("_growth_report", "")
                try:
                    nums.append(int(stem.replace("day", "")))
                except ValueError:
                    pass
            day_no = (max(nums) + 1) if nums else 1
        out_path = save_dir / f"day{day_no}_growth_report.json"
        out_data = dict(report)
        out_data["_meta"] = {
            "day": day_no,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "compare_with": str(cmp_path) if compare_report else None,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out_data, f, ensure_ascii=False, indent=2)
        print(f"\n[Phase 3.6.3] 已保存每日报告: {out_path}")

    return 0


def _resolve_compare_path(compare_arg: str) -> Path:
    # 形如 "day3" → data/growth_health/day3_growth_report.json
    s = str(compare_arg).strip()
    if s.lower().startswith("day"):
        rest = s[3:]
        try:
            n = int(rest)
            return _REPO / "data" / "growth_health" / f"day{n}_growth_report.json"
        except ValueError:
            pass
    # 否则按文件路径
    return Path(s)


def _compare_traits(new_r: Dict, old_r: Dict) -> Dict[str, Any]:
    """对比两个 report 的 trait 当前值和总 delta。"""
    new_rebuilt = new_r.get("recovery", {}).get("rebuilt_traits", {})
    old_rebuilt = old_r.get("recovery", {}).get("rebuilt_traits", {})
    diff: Dict[str, Any] = {}
    all_traits = set(new_rebuilt.keys()) | set(old_rebuilt.keys())
    for t in all_traits:
        nv = new_rebuilt.get(t, None)
        ov = old_rebuilt.get(t, None)
        if nv is None or ov is None:
            diff[t] = {"new": nv, "old": ov}
            continue
        delta = round(nv - ov, 5)
        diff[t] = {"new": round(nv, 4), "old": round(ov, 4), "delta": delta}
    return diff


def _render_compare(new_r: Dict, old_r: Dict, cmp_path: str) -> str:
    lines: List[str] = []
    a = lines.append
    a("")
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    a(f"📈 相对变化（vs {Path(cmp_path).name}）")
    a("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    old_events = old_r.get("event_count", 0)
    new_events = new_r.get("event_count", 0)
    a(f"  事件数  : {old_events} → {new_events}  (Δ +{new_events - old_events})")
    diff = _compare_traits(new_r, old_r)
    if diff:
        a(f"  {'维度':<22} {'旧值':>8} {'新值':>8} {'Δ':>10}")
        for t in sorted(diff.keys()):
            d = diff[t]
            old_v = d.get("old")
            new_v = d.get("new")
            delta = d.get("delta")
            a(f"  {t:<22} "
              f"{(old_v if old_v is not None else 'n/a'):>8} "
              f"{(new_v if new_v is not None else 'n/a'):>8} "
              f"{(f'{delta:+.4f}' if delta is not None else 'n/a'):>10}")
    else:
        a("  (无 trait 变化)")
    old_flags = old_r.get("flags", [])
    new_flags = new_r.get("flags", [])
    if old_flags != new_flags:
        a(f"  flags: {old_flags} → {new_flags}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
