# -*- coding: utf-8 -*-
"""Phase C.4.7 — Long-term drift metrics collector.

收集 SelfModel 长期漂移分析所需指标:
- SelfModel record 增长趋势
- personality delta 分布
- GrowthProposal 类型分布
- CoreIdentity mutation attempt
- duplicate growth detection
- conflicting proposal detection
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path("d:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI")
sys.path.insert(0, str(ROOT))

# CoreIdentity 核心特质(不可改变)
CORE_TRAITS = {"温柔", "敏感", "害羞", "慢热", "重视陪伴", "善良"}
FORBIDDEN_PATTERNS = ["冷漠", "攻击性", "刻薄", "恶毒", "暴力", "失去温柔", "完全改变人格"]


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def collect_selfmodel_growth_trend(self_model_dir: Path) -> Dict[str, Any]:
    """指标 1: SelfModel record 增长趋势。"""
    beliefs = _read_jsonl(self_model_dir / "beliefs.jsonl")
    history = _read_jsonl(self_model_dir / "history.jsonl")
    reflections = _read_jsonl(self_model_dir / "reflection.jsonl")
    return {
        "beliefs_count": len(beliefs),
        "history_count": len(history),
        "reflections_count": len(reflections),
        "total_records": len(beliefs) + len(history) + len(reflections),
        "first_record_ts": min(
            [b.get("first_seen", "") for b in beliefs if b.get("first_seen")]
            + [h.get("timestamp", "") for h in history if h.get("timestamp")]
            + [r.get("timestamp", "") for r in reflections if r.get("timestamp")],
            default="",
        ),
        "last_record_ts": max(
            [b.get("last_confirmed", "") for b in beliefs if b.get("last_confirmed")]
            + [h.get("timestamp", "") for h in history if h.get("timestamp")]
            + [r.get("timestamp", "") for r in reflections if r.get("timestamp")],
            default="",
        ),
    }


def collect_personality_delta_distribution(
    self_model_dir: Path,
) -> Dict[str, Any]:
    """指标 2: personality delta 分布。"""
    history = _read_jsonl(self_model_dir / "history.jsonl")
    deltas: List[float] = []
    per_trait: Dict[str, List[float]] = defaultdict(list)

    for h in history:
        affected = h.get("affected_traits", {}) or {}
        for trait, delta in affected.items():
            try:
                d = float(delta)
                deltas.append(d)
                per_trait[trait].append(d)
            except (TypeError, ValueError):
                continue

    if not deltas:
        return {
            "total_deltas": 0,
            "max_abs_delta": 0.0,
            "mean_delta": 0.0,
            "per_trait_count": 0,
            "per_trait_stats": {},
        }

    abs_deltas = [abs(d) for d in deltas]
    return {
        "total_deltas": len(deltas),
        "max_abs_delta": max(abs_deltas),
        "mean_delta": sum(deltas) / len(deltas),
        "per_trait_count": len(per_trait),
        "per_trait_stats": {
            trait: {
                "count": len(ds),
                "sum": round(sum(ds), 6),
                "max_abs": max(abs(d) for d in ds),
            }
            for trait, ds in per_trait.items()
        },
    }


def collect_proposal_type_distribution(
    proposals_path: Path,
) -> Dict[str, Any]:
    """指标 3: GrowthProposal 类型分布。"""
    proposals = _read_jsonl(proposals_path)
    by_status: Counter = Counter()
    by_path: Counter = Counter()
    by_growth_level: Counter = Counter()
    confidence_buckets: Counter = Counter()
    delta_buckets: Counter = Counter()

    for p in proposals:
        status = p.get("status", "unknown")
        by_status[status] += 1

        em = p.get("evaluator_meta", {}) or {}
        gl = em.get("growth_level", "unknown")
        by_growth_level[gl] += 1

        try:
            conf = float(p.get("confidence", 0.0))
            if conf < 0.5:
                confidence_buckets["<0.5"] += 1
            elif conf < 0.7:
                confidence_buckets["0.5-0.7"] += 1
            elif conf < 0.85:
                confidence_buckets["0.7-0.85"] += 1
            else:
                confidence_buckets[">=0.85"] += 1
        except (TypeError, ValueError):
            pass

        for change in p.get("proposed_changes", []) or []:
            path = change.get("path", "unknown") if isinstance(change, dict) else "unknown"
            by_path[path] += 1
            try:
                after = float(change.get("after", 0.0))
                before = float(change.get("before", 0.0))
                delta = abs(after - before)
                if delta < 0.01:
                    delta_buckets["<0.01"] += 1
                elif delta < 0.03:
                    delta_buckets["0.01-0.03"] += 1
                elif delta < 0.05:
                    delta_buckets["0.03-0.05"] += 1
                else:
                    delta_buckets[">=0.05"] += 1
            except (TypeError, ValueError):
                pass

    return {
        "total_proposals": len(proposals),
        "by_status": dict(by_status),
        "by_growth_level": dict(by_growth_level),
        "by_path_top10": dict(by_path.most_common(10)),
        "confidence_distribution": dict(confidence_buckets),
        "delta_distribution": dict(delta_buckets),
    }


def detect_core_identity_mutation_attempts(
    proposals_path: Path,
) -> Dict[str, Any]:
    """指标 4: CoreIdentity mutation attempt 检测。

    检测 proposal 中是否包含尝试修改 CoreIdentity 核心特质/值的描述。
    """
    proposals = _read_jsonl(proposals_path)
    attempts: List[Dict[str, Any]] = []

    for p in proposals:
        # 收集所有文本字段
        text = json.dumps(p, ensure_ascii=False)
        # 1. 路径检查: 是否尝试写 core_identity.* / traits.* 等核心字段
        suspicious_paths = []
        for change in p.get("proposed_changes", []) or []:
            if not isinstance(change, dict):
                continue
            path = change.get("path", "")
            if not isinstance(path, str):
                continue
            pl = path.lower()
            if any(
                kw in pl
                for kw in [
                    "core_identity",
                    "origin_identity",
                    "core.trait",
                    "core.value",
                    "core.",
                    "identity.",
                ]
            ):
                suspicious_paths.append(path)

        # 2. 内容检查: 是否包含禁止模式
        forbidden_matches = []
        for pat in FORBIDDEN_PATTERNS:
            if pat in text:
                forbidden_matches.append(pat)

        # 3. 核心特质删除检测
        core_removal = []
        for trait in CORE_TRAITS:
            for change in p.get("proposed_changes", []) or []:
                if not isinstance(change, dict):
                    continue
                after = change.get("after")
                if after is not None:
                    try:
                        if float(after) < 0.0:
                            core_removal.append(trait)
                    except (TypeError, ValueError):
                        pass

        if suspicious_paths or forbidden_matches or core_removal:
            attempts.append({
                "proposal_id": p.get("id") or p.get("proposal_id", ""),
                "status": p.get("status", ""),
                "suspicious_paths": suspicious_paths,
                "forbidden_matches": forbidden_matches,
                "core_removal": core_removal,
            })

    return {
        "total_attempts": len(attempts),
        "attempts": attempts,
        "is_clean": len(attempts) == 0,
    }


def detect_duplicate_growth(
    proposals_path: Path,
) -> Dict[str, Any]:
    """指标 5: duplicate growth detection。

    基于 (source_event_id, path, delta) 三元组检测重复 proposal。
    """
    proposals = _read_jsonl(proposals_path)
    seen: Dict[Tuple[str, str, float], List[str]] = defaultdict(list)

    for p in proposals:
        sid = p.get("source_event_id", "")
        for change in p.get("proposed_changes", []) or []:
            if not isinstance(change, dict):
                continue
            path = change.get("path", "")
            try:
                before = float(change.get("before", 0.0))
                after = float(change.get("after", 0.0))
                delta = round(after - before, 4)
            except (TypeError, ValueError):
                delta = 0.0
            key = (sid, path, delta)
            pid = p.get("id") or p.get("proposal_id", "")
            seen[key].append(pid)

    duplicates = {
        str(k): v for k, v in seen.items() if len(v) > 1
    }
    return {
        "total_keys": len(seen),
        "duplicate_key_count": len(duplicates),
        "duplicates": duplicates,
    }


def detect_conflicting_proposals(
    proposals_path: Path,
) -> Dict[str, Any]:
    """指标 6: conflicting proposal detection。

    检测对同一 trait 产生方向相反 delta 的 proposal 对。
    """
    proposals = _read_jsonl(proposals_path)
    per_trait: Dict[str, List[Tuple[str, float, str]]] = defaultdict(list)

    for p in proposals:
        pid = p.get("id") or p.get("proposal_id", "")
        status = p.get("status", "")
        for change in p.get("proposed_changes", []) or []:
            if not isinstance(change, dict):
                continue
            path = change.get("path", "")
            try:
                before = float(change.get("before", 0.0))
                after = float(change.get("after", 0.0))
                delta = round(after - before, 4)
            except (TypeError, ValueError):
                continue
            if delta == 0.0:
                continue
            per_trait[path].append((pid, delta, status))

    conflicts: List[Dict[str, Any]] = []
    for trait, items in per_trait.items():
        positives = [x for x in items if x[1] > 0]
        negatives = [x for x in items if x[1] < 0]
        if positives and negatives:
            conflicts.append({
                "trait": trait,
                "positive_count": len(positives),
                "negative_count": len(negatives),
                "positive_proposals": [x[0] for x in positives],
                "negative_proposals": [x[0] for x in negatives],
            })

    return {
        "total_traits_affected": len(per_trait),
        "conflict_trait_count": len(conflicts),
        "conflicts": conflicts,
    }


def collect_all_metrics() -> Dict[str, Any]:
    """汇总所有指标。"""
    self_model_dir = ROOT / "data" / "self_model"
    proposals_path = ROOT / "data" / "proposals" / "proposals.jsonl"

    return {
        "selfmodel_growth_trend": collect_selfmodel_growth_trend(self_model_dir),
        "personality_delta_distribution": collect_personality_delta_distribution(
            self_model_dir
        ),
        "proposal_type_distribution": collect_proposal_type_distribution(proposals_path),
        "core_identity_mutation_attempts": detect_core_identity_mutation_attempts(
            proposals_path
        ),
        "duplicate_growth_detection": detect_duplicate_growth(proposals_path),
        "conflicting_proposals": detect_conflicting_proposals(proposals_path),
    }


if __name__ == "__main__":
    metrics = collect_all_metrics()
    print(json.dumps(metrics, ensure_ascii=False, indent=2, default=str))
