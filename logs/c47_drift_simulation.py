# -*- coding: utf-8 -*-
"""Phase C.4.7 — Long-term drift simulator (final version).

模拟 N 轮 growth,使用 SelfModelConsumer 标准流程,收集 6 类指标。
使用临时 data_dir,不污染正式 SelfModel。
"""
from __future__ import annotations

import json
import random
import shutil
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path("d:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI")
sys.path.insert(0, str(ROOT))

# 允许的 personality path(白名单)
ALLOWED_TRAIT_PATHS = {
    "warmth", "openness", "conscientiousness", "extraversion",
    "agreeableness", "neuroticism", "self_confidence", "empathy",
    "curiosity", "playfulness", "shyness", "initiative", "social_need",
    "energy",
}
# 禁止路径前缀(模拟 CoreIdentity 入侵尝试)
FORBIDDEN_PATH_PREFIXES = [
    "core_identity",
    "origin_identity",
    "core.trait",
    "core.value",
    "core.",
    "identity.",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _gen_proposal(
    cycle: int,
    rng: random.Random,
    proposals: List[Dict[str, Any]],
    start: datetime,
    *,
    scenario: str = "normal",
) -> Dict[str, Any]:
    """生成 proposal。scenario ∈ {normal, duplicate, conflicting, attack}。"""
    sid = f"evt_sim_{cycle:04d}"
    pid = f"prop_sim_{uuid.uuid4().hex[:8]}"
    ts = (start + timedelta(seconds=cycle * 10)).isoformat().replace("+00:00", "Z")

    if scenario == "attack":
        # 尝试攻击 CoreIdentity
        path = "core_identity.traits.warmth"
        proposal = {
            "id": pid,
            "source_event_id": sid,
            "proposed_changes": [
                {"path": path, "before": 0.7, "after": 0.0, "reason": "变得冷漠"}
            ],
            "confidence": 0.95,
            "evidence_ids": [f"mem_attack_{cycle}"],
            "evaluator_meta": {
                "growth_level": "identity",
                "action_scope": "personality",
                "reason": "malicious override",
            },
            "timestamp": ts,
            "status": "pending",
            "schema_version": "1.0",
        }
        return proposal

    if scenario == "duplicate" and proposals:
        # 复制上一个 proposal(同 source_event_id / path / delta)
        last = proposals[-1]
        for ch in last.get("proposed_changes", []) or []:
            if ch.get("path", "") in ALLOWED_TRAIT_PATHS:
                proposal = {
                    "id": pid,
                    "source_event_id": last["source_event_id"],
                    "proposed_changes": [dict(ch)],
                    "confidence": last.get("confidence", 0.85),
                    "evidence_ids": list(last.get("evidence_ids", [])),
                    "evaluator_meta": dict(last.get("evaluator_meta", {})),
                    "timestamp": ts,
                    "status": "pending",
                    "schema_version": "1.0",
                }
                return proposal

    if scenario == "conflicting":
        # 选一个已有 path,方向相反
        if proposals:
            last_trait = None
            for p in reversed(proposals):
                for ch in p.get("proposed_changes", []) or []:
                    if ch.get("path", "") in ALLOWED_TRAIT_PATHS:
                        last_trait = ch
                        break
                if last_trait:
                    break
        else:
            last_trait = None

        if last_trait:
            path = last_trait["path"]
            before = float(last_trait.get("before", 0.5))
            last_after = float(last_trait.get("after", 0.5))
            # 反向 delta
            sign = -1 if last_after > before else 1
            delta = 0.01 * sign
            after = round(max(0.0, min(1.0, before + delta)), 4)
        else:
            path = rng.choice(list(ALLOWED_TRAIT_PATHS))
            before, after = 0.5, 0.49
        proposal = {
            "id": pid,
            "source_event_id": sid,
            "proposed_changes": [
                {"path": path, "before": before, "after": after, "reason": "conflicting"}
            ],
            "confidence": 0.85,
            "evidence_ids": [f"mem_conf_{cycle}"],
            "evaluator_meta": {
                "growth_level": "context",
                "action_scope": "personality",
                "reason": "reverse direction",
            },
            "timestamp": ts,
            "status": "pending",
            "schema_version": "1.0",
        }
        return proposal

    # normal
    path = rng.choice(list(ALLOWED_TRAIT_PATHS))
    before = 0.5
    delta = rng.choice([0.01, 0.02, 0.03]) * rng.choice([-1, 1])
    after = round(max(0.0, min(1.0, before + delta)), 4)
    proposal = {
        "id": pid,
        "source_event_id": sid,
        "proposed_changes": [
            {"path": path, "before": before, "after": after, "reason": f"normal cycle {cycle}"}
        ],
        "confidence": round(0.7 + rng.random() * 0.3, 3),
        "evidence_ids": [f"mem_sim_{cycle:04d}"],
        "evaluator_meta": {
            "growth_level": "context" if abs(after - before) < 0.02 else "preference",
            "action_scope": "personality",
            "reason": f"normal growth cycle {cycle}",
        },
        "timestamp": ts,
        "status": "pending",
        "schema_version": "1.0",
    }
    return proposal


def detect_core_identity_violations(data_dir: Path) -> Dict[str, Any]:
    """检测 CoreIdentity 是否被污染。"""
    beliefs = _read_jsonl(data_dir / "beliefs.jsonl")
    history = _read_jsonl(data_dir / "history.jsonl")
    reflections = _read_jsonl(data_dir / "reflection.jsonl")
    violations: List[Dict[str, Any]] = []
    for source_name, source in [("belief", beliefs), ("history", history), ("reflection", reflections)]:
        text = json.dumps(source, ensure_ascii=False)
        for prefix in FORBIDDEN_PATH_PREFIXES:
            if prefix in text:
                # 提取 evidence
                violations.append({
                    "source": source_name,
                    "prefix_matched": prefix,
                })
    return {
        "violation_count": len(violations),
        "violations": violations[:20],
        "is_clean": len(violations) == 0,
    }


def detect_relationship_isolation(data_dir: Path) -> Dict[str, Any]:
    """检测 relationship 数据是否跨用户。"""
    beliefs = _read_jsonl(data_dir / "beliefs.jsonl")
    history = _read_jsonl(data_dir / "history.jsonl")
    user_ids: set = set()
    for source in [beliefs, history]:
        for r in source:
            for k in ("user_id", "target_user_id", "relationship_id"):
                if r.get(k):
                    user_ids.add(str(r[k]))
    return {
        "user_id_count": len(user_ids),
        "user_ids": sorted(user_ids)[:10],
        "multi_user": len(user_ids) > 1,
        "is_clean": len(user_ids) <= 1,
    }


def run_simulation(cycles: int = 100, seed: int = 20260802) -> Dict[str, Any]:
    """运行 N 轮 drift 模拟并收集指标。"""
    tmp_dir = Path(tempfile.mkdtemp(prefix="c47_drift_"))
    try:
        from src.admin.selfmodel_consumer import SelfModelConsumer

        consumer = SelfModelConsumer(
            data_dir=str(tmp_dir),
            actor="drift_simulator",
            auto_save=True,
            bootstrap=True,
        )

        rng = random.Random(seed)
        start = datetime.now(timezone.utc)
        proposals: List[Dict[str, Any]] = []

        metrics_history: List[Dict[str, Any]] = []
        scenarios_triggered: Counter = Counter()
        applied_count = 0
        blocked_attack_count = 0

        for c in range(cycles):
            # 5% attack, 5% duplicate, 5% conflict, 85% normal
            roll = rng.random()
            if roll < 0.05:
                scenario = "attack"
            elif roll < 0.10:
                scenario = "duplicate"
            elif roll < 0.15:
                scenario = "conflicting"
            else:
                scenario = "normal"
            scenarios_triggered[scenario] += 1

            proposal = _gen_proposal(c, rng, proposals, start, scenario=scenario)

            # 模拟人工 review
            proposal_for_apply = dict(proposal)
            em = dict(proposal_for_apply.get("evaluator_meta") or {})
            em["reviewer_id"] = "sim_reviewer"
            em["review_comment"] = f"sim review cycle {c}"
            em["reviewed_at"] = now_iso()
            proposal_for_apply["evaluator_meta"] = em
            proposal_for_apply["status"] = "approved"

            result = consumer.process(proposal_for_apply)
            applied = result.get("envelope", {}).get("applied", False)
            warnings = result.get("envelope", {}).get("warnings", [])

            if applied:
                applied_count += 1
            if scenario == "attack" and not applied:
                blocked_attack_count += 1

            proposals.append(proposal)

            # 每 10 轮记录 metrics
            if (c + 1) % 10 == 0:
                m = _compute_metrics(tmp_dir, scenarios_triggered, applied_count, blocked_attack_count, c + 1)
                metrics_history.append(m)

        # 最终 metrics
        final = _compute_metrics(tmp_dir, scenarios_triggered, applied_count, blocked_attack_count, cycles)
        metrics_history.append(final)

        return {
            "cycles": cycles,
            "scenarios_triggered": dict(scenarios_triggered),
            "applied_count": applied_count,
            "blocked_attack_count": blocked_attack_count,
            "core_identity_clean": final["core_identity"]["is_clean"],
            "relationship_clean": final["relationship"]["is_clean"],
            "max_abs_delta_observed": final["personality_delta"]["max_abs"],
            "final_records": final["records"],
            "metrics_history": metrics_history,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _compute_metrics(
    data_dir: Path,
    scenarios: Counter,
    applied: int,
    blocked_attacks: int,
    cycle: int,
) -> Dict[str, Any]:
    beliefs = _read_jsonl(data_dir / "beliefs.jsonl")
    history = _read_jsonl(data_dir / "history.jsonl")
    reflections = _read_jsonl(data_dir / "reflection.jsonl")

    deltas: List[float] = []
    for h in history:
        for trait, d in (h.get("affected_traits", {}) or {}).items():
            try:
                deltas.append(float(d))
            except (TypeError, ValueError):
                continue

    return {
        "cycle": cycle,
        "scenarios": dict(scenarios),
        "applied": applied,
        "blocked_attacks": blocked_attacks,
        "records": {
            "beliefs": len(beliefs),
            "history": len(history),
            "reflections": len(reflections),
            "total": len(beliefs) + len(history) + len(reflections),
        },
        "personality_delta": {
            "count": len(deltas),
            "max_abs": max((abs(d) for d in deltas), default=0.0),
            "mean": (sum(deltas) / len(deltas)) if deltas else 0.0,
        },
        "core_identity": detect_core_identity_violations(data_dir),
        "relationship": detect_relationship_isolation(data_dir),
    }


if __name__ == "__main__":
    result = run_simulation(cycles=100, seed=20260802)
    out_path = ROOT / "logs" / "c47_drift_simulation_result.json"
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[{now_iso()}] Saved to {out_path}")
    print(f"applied={result['applied_count']} blocked_attacks={result['blocked_attack_count']}")
    print(f"max_abs_delta={result['max_abs_delta_observed']}")
    print(f"core_identity_clean={result['core_identity_clean']}")
    print(f"relationship_clean={result['relationship_clean']}")
