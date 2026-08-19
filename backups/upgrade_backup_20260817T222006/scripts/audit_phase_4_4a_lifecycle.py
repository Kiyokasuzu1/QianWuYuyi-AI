# -*- coding: utf-8 -*-
"""
Phase 4.4-A — 100 轮 Runtime Lifecycle 连续运行审计（只观察，不修改）

任务卡：
- 100 轮 User Input → Runtime 全生命周期（生产入口 rc.process）
- 每 10 轮 snapshot：Memory / Relationship / Growth / SelfModel / Personality
- 验收：Runtime 可继续运行 / 无异常增长 / 无人格自动变化 / 无 proposal 爆炸
- 规则：发现问题先分类（架构缺陷/数据问题/测试问题/历史遗留），不立即修

隔离说明（如实）：
- 数据目录全部落在工作区 data/audit_4_4a/（运行前清空）
- GrowthIntegrationService 注入隔离 ProposalStore（同目录），
  避免污染生产 data/proposals/ —— 除存储路径外行为与生产默认完全一致
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

# 项目根加入 sys.path（脚本从 workspace 根运行）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AUDIT_DIR = ROOT / "data" / "audit_4_4a"
OUT_JSON = ROOT / "docs" / "audit" / "phase4_4a_lifecycle_snapshots.json"

ROUNDS = 100
SNAPSHOT_EVERY = 10


# ------------------------------------------------------------
# 消息计划（确定性，贴近真实使用：普通聊天为主，关系/偏好穿插重复）
# ------------------------------------------------------------
def message_for_round(i: int) -> str:
    ordinary = [
        "今天继续聊羽依的架构设计",
        "Runtime 的生命周期这块我还想再讨论一下",
        "帮我整理一下最近的开发思路",
        "今天有点累，随便聊聊",
        "记忆系统现在的状态怎么样",
        "我们继续推进项目吧",
        "这个模块的职责边界再理一理",
        "今天想讨论一下成长系统的设计",
        "最近代码改动有点多，梳理一下",
        "聊聊下一步的计划",
    ]
    if i % 10 == 0:
        # 创造类真实表达（含行为标记）——本产品语境下的真实用户场景，
        # 用于在 100 轮尺度验证 journal → Stage 4 → proposal 的食物链（E3）
        return "我已经创建了一个新角色，设计了她的形象和性格"
    if i % 5 == 0:
        # 重复偏好主题（触发 Growth 证据积累 —— 真实用户会重复表达偏好）
        return "请给我详细的技术解释，我喜欢深入了解架构细节"
    if i % 3 == 0:
        # 关系模式（collaboration 关键词命中 extractor）
        return "我们一起继续开发羽依这个项目，长期合作下去"
    return ordinary[(i // 1) % len(ordinary)]


def make_event(text: str):
    from src.runtime.events import Event

    return Event(type="user_input", source="user", payload={"text": text, "content": text})


# ------------------------------------------------------------
# 快照采集（全部 duck-typed，任何读取失败记 error 不中断）
# ------------------------------------------------------------
def count_memory_store(rc) -> dict:
    out = {"count": None, "file_bytes": None, "error": None}
    try:
        ms = rc.memory_adapter.get_memory_store()
        # MemoryStore 的权威读取是 load()（runtime_core.py:1298 同款）
        fn = getattr(ms, "load", None)
        if callable(fn):
            out["count"] = len(fn() or [])
        else:
            for attr in ("get_all", "all", "list_all", "get_recent"):
                fn = getattr(ms, attr, None)
                if callable(fn):
                    try:
                        out["count"] = len(fn())
                    except TypeError:
                        out["count"] = len(fn(limit=1000))
                    break
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)
    try:
        p = getattr(rc.memory_adapter.get_memory_store(), "path", None)
        if p and os.path.exists(p):
            out["file_bytes"] = os.path.getsize(p)
    except Exception:  # noqa: BLE001
        pass
    return out


def builder_pending(rc) -> int:
    """ExperienceBuilder 悬挂（永不 finish）的构建中经历数 —— 泄漏观测点。"""
    try:
        return len(getattr(rc.experience_builder, "_building", {}) or {})
    except Exception:  # noqa: BLE001
        return -1


def count_journal(rc) -> int:
    try:
        return len(rc.memory_adapter._journal.load())
    except Exception:  # noqa: BLE001
        return -1


def proposals_by_status(svc) -> dict:
    counts = {"total": 0}
    try:
        for p in svc.list_proposals(status=None, limit=10000):
            status = getattr(p, "status", None) or (p.get("status") if isinstance(p, dict) else "unknown")
            counts[status] = counts.get(status, 0) + 1
            counts["total"] += 1
    except Exception as exc:  # noqa: BLE001
        counts["error"] = repr(exc)
    return counts


def selfmodel_brief(rc) -> dict:
    out = {"traits_count": None, "snapshot_bytes": None, "error": None}
    try:
        snap = rc.get_self_model_snapshot()
        if snap is None:
            out["error"] = "snapshot None"
            return out
        blob = json.dumps(snap, ensure_ascii=False, default=str)
        out["snapshot_bytes"] = len(blob)
        traits = snap.get("traits") or snap.get("self_model", {}).get("traits") or {}
        out["traits_count"] = len(traits) if isinstance(traits, dict) else None
        # 供漂移对比
        out["_traits"] = traits if isinstance(traits, dict) else {}
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)
    return out


def personality_brief(rc) -> dict:
    out = {"traits": {}, "error": None}
    try:
        pr = rc.get_personality_resolver()
        if pr is not None and callable(getattr(pr, "resolve", None)):
            snap = pr.resolve()
            d = snap.to_dict() if hasattr(snap, "to_dict") else (snap if isinstance(snap, dict) else {})
            out["traits"] = d.get("traits", {}) or {}
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)
    return out


def traits_drift(a: dict, b: dict) -> float:
    """两个人格 traits 字典的最大单维漂移。"""
    keys = set(a) | set(b)
    drift = 0.0
    for k in keys:
        try:
            drift = max(drift, abs(float(b.get(k, 0.0)) - float(a.get(k, 0.0))))
        except (TypeError, ValueError):
            continue
    return round(drift, 4)


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
def main() -> int:
    from src.runtime.runtime_core import RuntimeCore
    from src.growth.growth_integration import GrowthIntegrationService
    from src.growth.proposal_store import ProposalStore
    from src.growth.proposal_manager import ProposalManager
    from src.personality.personality_growth_record import PersonalityGrowthHistory

    if AUDIT_DIR.exists():
        shutil.rmtree(AUDIT_DIR)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    rc = RuntimeCore(config={
        "adapters_enabled": True,
        "relationship_enabled": True,
        "experience_enabled": True,
        "event_driven_enabled": True,
        "memory_store_path": str(AUDIT_DIR / "mem.json"),
        "experience_journal_path": str(AUDIT_DIR / "exp_journal.jsonl"),
        "growth_proposals_path": str(AUDIT_DIR / "gp.json"),
        "state_file": str(AUDIT_DIR / "runtime.json"),
    })

    # 隔离的 GrowthIntegrationService（防污染生产 proposals 存储）
    growth_history = PersonalityGrowthHistory()
    store = ProposalStore(path=str(AUDIT_DIR / "proposals.jsonl"))
    manager = ProposalManager(
        store=store, growth_history=growth_history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )
    svc = GrowthIntegrationService(
        proposal_manager=manager, growth_history=growth_history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )
    rc._growth_integration_service = svc

    snapshots = []
    trace_histogram = {}
    phase_error_rounds = 0
    process_exceptions = []
    first_personality = None
    t0 = time.time()

    for i in range(1, ROUNDS + 1):
        try:
            ctx = rc.process(make_event(message_for_round(i)))
        except Exception as exc:  # noqa: BLE001 — 任务卡：Runtime 必须可继续运行
            process_exceptions.append({"round": i, "error": repr(exc)})
            continue

        # 可观测性累计
        trace = (getattr(ctx, "lifecycle_trace", None) or {}).get("growth_activation", {})
        status = trace.get("status", "missing")
        trace_histogram[status] = trace_histogram.get(status, 0) + 1
        if getattr(ctx, "_phase_errors", None):
            phase_error_rounds += 1

        if i % SNAPSHOT_EVERY == 0 or i == ROUNDS:
            rel_state = rc.get_relationship_state_snapshot() or {}
            rel_model = rc.get_relationship_model_snapshot() or {}
            pers = personality_brief(rc)
            if first_personality is None:
                first_personality = pers["traits"]
            sm = selfmodel_brief(rc)
            snap = {
                "round": i,
                "memory": count_memory_store(rc),
                "journal_records": count_journal(rc),
                "builder_pending": builder_pending(rc),
                "relationship": {
                    "total_interactions": rel_model.get("total_interactions"),
                    "stage": rel_state.get("relationship_stage"),
                    "shared_experiences": rel_model.get("total_shared_experiences"),
                    "trust": rel_state.get("trust"),
                    "familiarity": rel_state.get("familiarity"),
                    "collaboration": rel_state.get("collaboration"),
                },
                "growth": {
                    "proposals": proposals_by_status(svc),
                    "trace_histogram": dict(trace_histogram),
                },
                "self_model": {k: v for k, v in sm.items() if not k.startswith("_")},
                "personality": {
                    "traits_count": len(pers["traits"]),
                    "drift_vs_round10": traits_drift(first_personality, pers["traits"]),
                    "error": pers["error"],
                },
                "_selfmodel_traits": sm.get("_traits", {}),
            }
            snapshots.append(snap)
            print(f"[round {i:3d}] mem={snap['memory']['count']} "
                  f"journal={snap['journal_records']} "
                  f"pending={snap['builder_pending']} "
                  f"rel={snap['relationship']['total_interactions']}/"
                  f"{snap['relationship']['stage']} "
                  f"props={snap['growth']['proposals']} "
                  f"drift={snap['personality']['drift_vs_round10']}")

    elapsed = time.time() - t0

    # SelfModel 漂移：首快照 vs 末快照
    sm_drift = traits_drift(
        snapshots[0].get("_selfmodel_traits", {}) if snapshots else {},
        snapshots[-1].get("_selfmodel_traits", {}) if snapshots else {},
    )
    for s in snapshots:
        s.pop("_selfmodel_traits", None)

    result = {
        "meta": {
            "rounds": ROUNDS,
            "elapsed_sec": round(elapsed, 2),
            "audit_dir": str(AUDIT_DIR),
            "message_plan": "ordinary x~53% / relationship(collaboration) x20% / preference x20%",
        },
        "verdict_inputs": {
            "process_exceptions": process_exceptions,
            "phase_error_rounds": phase_error_rounds,
            "trace_histogram": trace_histogram,
            "selfmodel_traits_drift_first_vs_last": sm_drift,
            "personality_drift_final": snapshots[-1]["personality"]["drift_vs_round10"] if snapshots else None,
            "proposals_final": snapshots[-1]["growth"]["proposals"] if snapshots else None,
        },
        "snapshots": snapshots,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n已写入 {OUT_JSON}")
    print(f"总耗时 {elapsed:.1f}s / {ROUNDS} 轮；异常 {len(process_exceptions)}；"
          f"phase_error 轮次 {phase_error_rounds}")
    print(f"trace 分布: {trace_histogram}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
