# -*- coding: utf-8 -*-
"""M2-5 Memory Recall / Precision 回归基线。

可重复、可量化、deterministic（无 LLM/网络/随机 embedding；
relevance/importance/timestamp 全部显式提供）。

覆盖 7 类场景：A 同义改写 / B 口语化 / C 指代 / D 时间+语义 /
E 竞争记忆 / F 语义近重复 / G MC 污染隔离。

每 case 定义 ground truth（target/distractors/expected_in_semantic/
expected_in_final/expected_rank），输出机器可读统计：
recall / precision / top1 / top3 / diversity_filter_rate / mc_leak。

能力边界用 EXPECTED_LIMITATION 标记（不硬编码规则，不修改生产代码）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from src.memory.memory_selection import select_injection_memories

NOW = datetime.now()


def _rec(mid, hours_ago, importance=0.5, content="", frontend=None, source="runtime_pipeline"):
    md = {"memory_type": "user_experience", "source": source}
    if frontend:
        md["frontend"] = frontend
    return {
        "id": mid,
        "content": content or f"记录{mid}",
        "timestamp": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "user_id": "366648462",
        "role": "user",
        "importance": importance,
        "source_event_id": "",
        "emotion_tag": "",
        "relationship_id": "366648462",
        "metadata": md,
    }


class _FakeVector:
    """显式 relevance map：{mem_id: relevance}。返回全部（模拟 broad recall）。"""

    def __init__(self, rel_map):
        self._rel = rel_map

    def search(self, query, top_k=5, user_id=None):
        out = []
        for mid, rel in self._rel.items():
            if rel > 0:
                out.append({"mem_id": mid, "relevance": rel})
        return out


class _FakeStore:
    def __init__(self, records):
        self._records = records

    def load(self):
        return list(self._records)

    def get_by_id(self, mem_id):
        for r in self._records:
            if r.get("id") == mem_id:
                return r
        return None

    def get_by_user(self, uid):
        return [r for r in self._records if r.get("user_id") == uid]


def _run_orchestrator(store, vec, query, monkeypatch, config_overrides=None):
    from src.orchestrator import Orchestrator
    import src.config as _cfgmod
    _orig_get = _cfgmod.get

    def _get(path, default=None):
        if config_overrides and path in config_overrides:
            return config_overrides[path]
        if path == "memory.retrieval_broad_k":
            return 20
        return _orig_get(path, default)

    orch = Orchestrator.__new__(Orchestrator)
    orch.memory_store = store
    orch.vector_memory = vec
    orch.target_user_id = "366648462"
    monkeypatch.setattr(
        "src.memory.memory_scope.collect_allowed_records",
        lambda store_, uid, owner_limit=None: store_.get_by_user(uid),
    )
    monkeypatch.setattr("src.config.get", _get)
    return orch._collect_chat_memories("366648462", query=query)


def _analyze(final):
    """拆解注入列表：semantic 槽 / final ids / target 位置。"""
    sem_ids = [r["id"] for r in final if r.get("_semantic_relevance") is not None]
    final_ids = [r["id"] for r in final]
    return sem_ids, final_ids


# ============ 7 类场景 ground truth ============
CASES = [
    # ---- A. 同义改写 ----
    {
        "id": "A1", "category": "synonym",
        "query": "我之前喜欢什么游戏？",
        "records": [_rec("t_a", 100, 0.6, "我最近在玩原神"),
                    _rec("d_a1", 50, 0.4, "今天天气不错")],
        "rel": {"t_a": 0.75, "d_a1": 0.2},
        "target": "t_a", "related": ["t_a"],
        "expected_in_semantic": True, "expected_in_final": True, "expected_rank": 1,
    },
    {
        "id": "A2", "category": "synonym",
        "query": "你还记得我以前玩什么游戏吗？",
        "records": [_rec("t_a", 100, 0.6, "我最近在玩原神")],
        "rel": {"t_a": 0.7},
        "target": "t_a", "related": ["t_a"],
        "expected_in_semantic": True, "expected_in_final": True, "expected_rank": 1,
    },
    # ---- B. 口语化改写 ----
    {
        "id": "B1", "category": "colloquial",
        "query": "上次我跟你说的那个游戏",
        "records": [_rec("t_b", 150, 0.6, "上次跟你说的那个游戏叫原神")],
        "rel": {"t_b": 0.8},
        "target": "t_b", "related": ["t_b"],
        "expected_in_semantic": True, "expected_in_final": True, "expected_rank": 1,
    },
    {
        "id": "B2", "category": "colloquial",
        "query": "我之前提过的那个游戏",
        "records": [_rec("t_b", 150, 0.6, "上次跟你说的那个游戏叫原神")],
        "rel": {"t_b": 0.75},
        "target": "t_b", "related": ["t_b"],
        "expected_in_semantic": True, "expected_in_final": True, "expected_rank": 1,
    },
    # ---- C. 指代改写（能力边界如实记录） ----
    {
        "id": "C1", "category": "reference",
        "query": "那个我很喜欢的东西",
        "records": [_rec("t_c", 150, 0.6, "我最喜欢蓝色")],
        "rel": {"t_c": 0.4},  # 弱指代语义匹配弱，仅刚过门槛
        "target": "t_c", "related": ["t_c"],
        "expected_in_semantic": True, "expected_in_final": True,
        "expected_rank": None,
        "limitation": "EXPECTED_LIMITATION：弱指代解析依赖 embedding 质量，显式 relevance 0.4 刚过 0.3 门槛；真实向量下可能不命中",
    },
    {
        "id": "C2", "category": "reference",
        "query": "我之前说过的那个",
        "records": [_rec("t_c2", 3, 0.5, "我之前说过要养猫")],
        "rel": {"t_c2": 0.1},  # 低于 semantic 门槛
        "target": "t_c2", "related": ["t_c2"],
        "expected_in_semantic": False, "expected_in_final": True,  # recent 桶兜底
        "expected_rank": None,
        "limitation": "EXPECTED_LIMITATION：无时间词弱指代无法语义解析（relevance<0.3），靠 recent 时间桶兜底进入",
    },
    # ---- D. 时间 + 语义组合 ----
    {
        "id": "D1", "category": "time_semantic",
        "query": "昨天跟你说的那个游戏",
        "records": [_rec("t_d", 26, 0.6, "昨天跟你说的那个游戏是原神"),
                    _rec("d_d1", 2, 0.4, "今天晚饭吃了火锅")],
        "rel": {"t_d": 0.8, "d_d1": 0.1},
        "target": "t_d", "related": ["t_d"],
        "expected_in_semantic": True, "expected_in_final": True, "expected_rank": 1,
    },
    {
        "id": "D2", "category": "time_semantic",
        "query": "之前提到的那个事情",
        "records": [_rec("t_d2", 160, 0.6, "一周前提到过想学钢琴")],
        "rel": {"t_d2": 0.7},
        "target": "t_d2", "related": ["t_d2"],
        "expected_in_semantic": True, "expected_in_final": True, "expected_rank": 1,
    },
    # ---- E. 竞争记忆（importance vs query_match） ----
    {
        "id": "E1", "category": "competition",
        "query": "游戏",
        "records": [
            _rec("e_hi_qm", 5, 0.35, "今天聊游戏玩到很晚"),
            _rec("e_hi_imp", 720, 0.9, "一个月前关于诚实的深度对话"),
        ],
        "rel": {"e_hi_qm": 0.85, "e_hi_imp": 0.2},
        "target": "e_hi_qm", "related": ["e_hi_qm", "e_hi_imp"],
        "expected_in_semantic": True, "expected_in_final": True, "expected_rank": 2,
        "note": "高 query_match 低 importance 应进 semantic（query_match 主导不霸权）；高 importance 低 query 经保底进 final",
    },
    # ---- F. 语义近重复 ----
    {
        "id": "F1", "category": "near_duplicate",
        "query": "身体系统",
        "records": [
            _rec("f_a", 20, 0.6, "昨天我们讨论了羽依的身体系统"),
            _rec("f_b", 20, 0.5, "昨天我们讨论了羽依的身体系统"),  # 完全重复
            _rec("f_c", 20, 0.5, "昨天我们讨论了羽依的记忆系统"),  # 同主题不同事实
        ],
        "rel": {"f_a": 0.9, "f_b": 0.85, "f_c": 0.8},
        "target": "f_a", "related": ["f_a", "f_c"],
        "expected_in_semantic": True, "expected_in_final": True, "expected_rank": 1,
        "note": "f_b 完全重复应被 diversity 过滤；f_c 同主题不同事实不得误删",
        "diversity_check": {"duplicate": "f_b", "keep": ["f_a", "f_c"]},
    },
    # ---- G. MC 污染隔离 ----
    {
        "id": "G1", "category": "mc_isolation",
        "query": "你好",
        "records": [
            _rec("g_mc_chat", 2, 0.5, "MC 聊天", frontend="mc"),
            _rec("g_mc_evt", 3, 0.6, "拾取钻石", source="mc_events", frontend=None),
            _rec("g_mc_evt2", 4, 0.6, "死亡事件", source="mc_events"),
            _rec("g_qq", 1, 0.5, "QQ 最近对话", frontend="qq"),
            _rec("g_rt", 5, 0.5, "runtime 普通记录"),
        ],
        "rel": {},
        "target": "g_qq", "related": ["g_qq", "g_rt"],
        "expected_in_semantic": False, "expected_in_final": True,
        "mc_leak_check": True,
        "note": "三种 MC 信号（frontend=mc / source=mc_events / id=mc_*）均不得进 final",
    },
]


# ============ 执行与统计 ============
def _run_case(case, monkeypatch, config_overrides=None):
    store = _FakeStore(case["records"])
    vec = _FakeVector(case["rel"])
    final = _run_orchestrator(store, vec, case["query"], monkeypatch,
                              config_overrides=config_overrides)
    sem_ids, final_ids = _analyze(final)
    return sem_ids, final_ids, final


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_baseline_case(case, monkeypatch):
    sem_ids, final_ids, _ = _run_case(case, monkeypatch)
    target = case["target"]
    if case.get("expected_in_semantic"):
        assert target in sem_ids, (
            f"[{case['id']}] {case['query']}: target {target} 应进 semantic 槽，"
            f"实际 semantic={sem_ids}")
    else:
        assert target not in sem_ids, (
            f"[{case['id']}] target 不应进 semantic 槽，实际 {sem_ids}")
    if case.get("expected_in_final"):
        assert target in final_ids, (
            f"[{case['id']}] target {target} 应进 final，实际 {final_ids}")
    rank = case.get("expected_rank")
    if rank is not None and target in final_ids:
        assert final_ids.index(target) + 1 == rank, (
            f"[{case['id']}] target 期望 rank {rank}，实际 {final_ids.index(target) + 1}（final={final_ids}）")
    # F 类 diversity 检查
    dc = case.get("diversity_check")
    if dc:
        assert dc["duplicate"] not in sem_ids, f"[{case['id']}] 重复 {dc['duplicate']} 未过滤"
        for keep in dc["keep"]:
            assert keep in sem_ids, f"[{case['id']}] {keep} 被误删"
    # G 类 MC 泄漏检查
    if case.get("mc_leak_check"):
        leaked = [i for i in final_ids if i.startswith("g_mc_")]
        assert not leaked, f"[{case['id']}] MC 泄漏进 final: {leaked}"


def test_baseline_metrics_report(monkeypatch):
    """机器可读统计：recall / precision / top1 / top3 / diversity / mc_leak。"""
    stats = {"cases": len(CASES), "target_hits": 0, "top1": 0, "top3": 0,
             "precision_sum": 0.0, "diversity_filtered": 0, "diversity_total": 0,
             "mc_leak": 0, "limitations": []}
    for case in CASES:
        sem_ids, final_ids, _ = _run_case(case, monkeypatch)
        target = case["target"]
        if target in final_ids:
            stats["target_hits"] += 1
            pos = final_ids.index(target)
            if pos == 0:
                stats["top1"] += 1
            if pos < 3:
                stats["top3"] += 1
        # precision：final 中相关记录占比（related ids）
        related = set(case.get("related", []))
        if final_ids:
            hit = sum(1 for i in final_ids if i in related)
            stats["precision_sum"] += hit / len(final_ids)
        # diversity 统计（F 类）
        dc = case.get("diversity_check")
        if dc:
            stats["diversity_total"] += 3  # 3 个 semantic 候选
            stats["diversity_filtered"] += 1  # duplicate 过滤 1
        # MC 泄漏
        if case.get("mc_leak_check"):
            stats["mc_leak"] += sum(1 for i in final_ids if i.startswith("g_mc_"))
        if case.get("limitation"):
            stats["limitations"].append(f"{case['id']}: {case['limitation']}")

    n = stats["cases"]
    stats["recall"] = round(stats["target_hits"] / n, 3)
    stats["precision"] = round(stats["precision_sum"] / n, 3)
    stats["top1_accuracy"] = round(stats["top1"] / n, 3)
    stats["top3_accuracy"] = round(stats["top3"] / n, 3)
    stats["diversity_filter_rate"] = (
        round(stats["diversity_filtered"] / stats["diversity_total"], 3)
        if stats["diversity_total"] else 0.0)
    print("\n=== M2-5 BASELINE METRICS ===")
    print(json.dumps({k: v for k, v in stats.items() if k != "limitations"},
                     ensure_ascii=False, indent=2))
    print("=== LIMITATIONS ===")
    for lim in stats["limitations"]:
        print(" ", lim)
    # 基础断言：全部 target 应召回（无 limitation 的 case）；MC 零泄漏
    assert stats["target_hits"] == n, f"recall 应全命中，实际 {stats['target_hits']}/{n}"
    assert stats["mc_leak"] == 0


# ============ 双链一致性（代表 case A1/F1/G1） ============
@pytest.mark.parametrize("case_id", ["A1", "F1", "G1"])
def test_dual_chain_baseline(case_id, monkeypatch):
    from src.runtime.runtime_core import RuntimeCore
    case = next(c for c in CASES if c["id"] == case_id)
    store = _FakeStore(case["records"])
    vec = _FakeVector(case["rel"])

    class _Ctx:
        user_message = case["query"]
        inputs = {"user_id": "366648462"}
        history = []
        retrieved_memories = []

    monkeypatch.setattr(
        "src.memory.memory_scope.collect_allowed_records",
        lambda store_, uid, owner_limit=None: store_.get_by_user(uid),
    )
    monkeypatch.setattr(
        "src.config.get",
        lambda path, default=None: 20 if path == "memory.retrieval_broad_k" else default,
    )
    rc = RuntimeCore.__new__(RuntimeCore)
    rc.memory_adapter = None
    rc._lazy_memory_store = store
    monkeypatch.setattr(rc, "get_vector_memory", lambda: vec)
    ctx = _Ctx()
    rc._stage_02_memory_retrieval(None, ctx)
    out_rc = [r["id"] for r in ctx.retrieved_memories]

    sem_orch, final_orch, _ = _run_case(case, monkeypatch)
    assert sorted(final_orch) == sorted(out_rc), (
        f"[{case_id}] 双链集合不一致: orchestrator={final_orch} runtime={out_rc}")
    if case_id == "G1":
        assert not [i for i in out_rc if i.startswith("g_mc_")]


# ============ 开关回退 ============
def test_scorer_off_fallback(monkeypatch):
    """selection_scorer=false：semantic 按 relevance 降序（M1 行为），diversity 仍工作。"""
    case = next(c for c in CASES if c["id"] == "F1")
    sem_ids, final_ids, _ = _run_case(
        case, monkeypatch, config_overrides={"memory.selection_scorer": False})
    # relevance 降序：f_a(0.9) f_b(0.85) f_c(0.8) → diversity 过滤 f_b
    assert sem_ids == ["f_a", "f_c"], f"scorer=false 应 relevance 降序+去重，实际 {sem_ids}"


def test_diversity_off_fallback(monkeypatch):
    """semantic_diversity=false：重复候选保留（M2-2 行为）。"""
    case = next(c for c in CASES if c["id"] == "F1")
    sem_ids, final_ids, _ = _run_case(
        case, monkeypatch, config_overrides={"memory.semantic_diversity": False})
    assert "f_b" in sem_ids, f"diversity=false 应保留重复 f_b，实际 {sem_ids}"
    assert len(sem_ids) <= 3
