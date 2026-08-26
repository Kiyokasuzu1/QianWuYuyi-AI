# -*- coding: utf-8 -*-
"""M2-2 MemoryRelevanceEvaluator 融合排序接线验收测试。

覆盖任务书 Test 1-10：
1. 高相关+高重要 → 前列
2. 高相关+低重要 → 可进入但不无限压制
3. 高重要+低相关 → 保留长期机会
4. _vector_relevance 进入 semantic 分量
5. relevance_breakdown 可审计（final_score + 各维度）
6. M1-1 弱时间意图三态保持
7. M1-2 洪峰（200 闲聊 + 1 重要）
8. 长期历史保底（>7 天）
9. selection_scorer=false 完整回退 M1
10. 双链一致（orchestrator / runtime_core）
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.memory.memory_relevance_evaluator import MemoryRelevanceEvaluator
from src.memory.memory_selection import select_injection_memories

NOW = datetime.now()


def _rec(mid, hours_ago, importance=0.5, content="", frontend=None, memory_type="user_experience"):
    ts = NOW - timedelta(hours=hours_ago)
    md = {"memory_type": memory_type}
    if frontend:
        md["frontend"] = frontend
    return {
        "id": mid,
        "content": content or f"记录{mid}",
        "timestamp": ts.isoformat(),
        "user_id": "366648462",
        "role": "user",
        "importance": importance,
        "source_event_id": "",
        "emotion_tag": "",
        "relationship_id": "366648462",
        "metadata": md,
    }


def _sem(record, relevance):
    return {"record": record, "relevance": relevance}


# ============ Test 1: 高相关 + 高重要 ============
def test_high_relevance_high_importance_top():
    high = _rec("high", 50, importance=0.9, content="羽依说：爱是让对方被理解（重要定义）")
    low = _rec("low", 2, importance=0.4, content="今天吃了饭")
    final = select_injection_memories(
        [low, high],
        [_sem(high, 0.95), _sem(low, 0.5)],
        query="你对爱的理解是什么",
    )
    assert final[0]["id"] == "high", f"高相关+高重要应在最前，实际 {[r['id'] for r in final]}"


# ============ Test 2: 高相关 + 低重要 ============
def test_high_relevance_low_importance_enterable():
    hi_rel = _rec("rel", 3, importance=0.35, content="雪原上有钻石矿")
    hi_imp = _rec("imp", 600, importance=0.85, content="一个月前的里程碑对话")
    # 语义阶段：rel 相关度高（0.9）但重要性低；imp 不相关（0.1）但重要
    final = select_injection_memories(
        [hi_rel, hi_imp],
        [_sem(hi_rel, 0.9), _sem(hi_imp, 0.1)],
        query="雪原里有什么",
    )
    ids = [r["id"] for r in final]
    # 高相关低重要可进入（semantic 槽），且不无限压制——imp 仍有机会（保底/桶）
    assert "rel" in ids, "高相关低重要应可进入 semantic 槽"
    # importance 权重有界：rel 在语义排序中不应绝对压制所有历史
    assert len(final) <= 6


# ============ Test 3: 高重要 + 低相关 ============
def test_high_importance_low_relevance_kept():
    old_imp = _rec("old_imp", 500, importance=0.9, content="20天前的重大约定")
    today = [_rec(f"t{i}", i / 9, importance=0.5, content=f"今天闲聊{i}") for i in range(50)]
    final = select_injection_memories(
        today + [old_imp],
        [],
        query="今天天气怎么样",
    )
    assert "old_imp" in [r["id"] for r in final], "高重要低相关经保底应保留"


# ============ Test 4: _vector_relevance 进入 semantic 分量 ============
def test_vector_relevance_feeds_semantic_component():
    rec = _rec("vr", 10, importance=0.5, content="测试内容")
    rec["_vector_relevance"] = 0.9
    ev = MemoryRelevanceEvaluator()
    record = ev.evaluate(rec, query="")
    assert record.breakdown.semantic_score == pytest.approx(0.9, abs=0.01), \
        f"semantic 分量应=0.9，实际 {record.breakdown.semantic_score}"
    # 无 _vector_relevance → 0
    rec2 = _rec("vr2", 10, importance=0.5, content="测试内容")
    r2 = ev.evaluate(rec2, query="")
    assert r2.breakdown.semantic_score == 0.0


# ============ Test 5: relevance_breakdown 可审计 ============
def test_relevance_breakdown_auditable():
    rec = _rec("br", 10, importance=0.7, content="我们约定永远在一起")
    ev = MemoryRelevanceEvaluator()
    record = ev.evaluate(rec, query="我们之间有什么约定")
    b = record.breakdown
    assert b.final_score > 0
    for field in ("query_match_score", "importance_score", "semantic_score",
                  "time_decay_score", "relationship_score", "identity_score",
                  "emotional_score"):
        assert getattr(b, field) >= 0.0, f"{field} 缺失"
    assert record.reasons  # 可解释理由
    # 双链 selection 层最终带临时标记（不持久化）
    final = select_injection_memories(
        [rec], [_sem(rec, 0.8)], query="我们之间有什么约定")
    assert final and final[0]["id"] == "br"


# ============ Test 6: M1-1 弱时间意图三态（scorer 开启下） ============
def test_weak_intent_states_preserved_with_scorer():
    old = _rec("old", 600, importance=0.5, content="20天前：爱是让对方被理解")
    new = _rec("new", 2, importance=0.5, content="今天：日常闲聊")
    pool = [old, new] + [_rec(f"p{i}", 100 + i * 5) for i in range(20)]
    sem = [_sem(old, 0.85), _sem(new, 0.9)]

    f1 = select_injection_memories(pool, sem, query="")
    f2 = select_injection_memories(pool, sem, query="你当时是怎么定义爱的？")
    f3 = select_injection_memories(pool, sem, query="最近一次你说什么")

    s1 = [r["id"] for r in f1 if r.get("_semantic_relevance") is not None]
    s2 = [r["id"] for r in f2 if r.get("_semantic_relevance") is not None]
    s3 = [r["id"] for r in f3 if r.get("_semantic_relevance") is not None]
    # 无意图：relevance 降序（scorer 精排后 new 仍在前——new query_match 高）
    assert s1 == ["new", "old"], f"无意图应 relevance 主导，实际 {s1}"
    # "当时"：旧优先（弱意图优先于 scorer）
    assert s2 == ["old", "new"], f"'当时'应旧优先，实际 {s2}"
    # "最近一次"：新优先
    assert s3 == ["new", "old"], f"'最近一次'应新优先，实际 {s3}"


# ============ Test 7: M1-2 洪峰（200 闲聊 + 1 重要） ============
def test_flood_not_crowding_important_with_scorer():
    imp = _rec("imp", 40.5, importance=0.85, content="昨天凌晨：我爱你，讨论什么是爱（重要）")
    chitchat = [_rec(f"c{i}", i / 9, importance=0.5, content=f"闲聊{i}") for i in range(200)]
    final = select_injection_memories(chitchat + [imp], [], query="")
    assert "imp" in [r["id"] for r in final], "重要记忆被 200 条闲聊挤出"


# ============ Test 8: 长期历史保底 ============
def test_long_term_floor_with_scorer():
    recent = [_rec(f"r{i}", 1 + i % 29, importance=0.5) for i in range(200)]
    old = _rec("floor", 20 * 24, importance=0.85, content="20天前：关于诚实的深度对话")
    final = select_injection_memories(recent + [old], [], query="")
    assert "floor" in [r["id"] for r in final], "长期重要历史保底失效"


# ============ Test 9: selection_scorer=false 回退 M1 ============
def test_selection_scorer_false_rolls_back(monkeypatch):
    """scorer=false → semantic 阶段按 relevance 降序（M1 行为）。"""
    recs = [_rec(f"m{i}", 10 + i, importance=0.5, content=f"内容{i}") for i in range(10)]
    a = _rec("a", 5, importance=0.6, content="AAA相关")
    b = _rec("b", 6, importance=0.9, content="BBB相关")
    sem = [_sem(b, 0.95), _sem(a, 0.9)]

    import src.config as _cfgmod
    _orig_get = _cfgmod.get

    def _fake_get(path, default=None):
        if path == "memory.selection_scorer":
            return False
        return _orig_get(path, default)

    monkeypatch.setattr("src.config.get", _fake_get)
    final = select_injection_memories(recs + [a, b], sem, query="AAA相关")
    sem_ids = [r["id"] for r in final if r.get("_semantic_relevance") is not None]
    # M1 行为：relevance 降序 → b(0.95) 在 a(0.9) 前
    assert sem_ids == ["b", "a"], f"scorer=false 应 relevance 降序，实际 {sem_ids}"
    # 且 recent 桶仍受 selection_fusion 控制（正交）
    assert len(final) <= 6


def test_selection_scorer_default_on():
    """默认（无配置）scorer 开启——高 importance 相关记录排序不受影响。"""
    a = _rec("a", 5, importance=0.9, content="爱是持续的行动（重要）")
    b = _rec("b", 6, importance=0.4, content="随便聊聊")
    final = select_injection_memories(
        [b, a], [_sem(a, 0.8), _sem(b, 0.85)], query="爱是什么")
    sem_ids = [r["id"] for r in final if r.get("_semantic_relevance") is not None]
    # a 重要性更高 → 融合分更高 → 排前（而非纯 relevance 的 b）
    assert sem_ids[0] == "a", f"融合分应让高重要 a 在前，实际 {sem_ids}"


# ============ Test 10: 双链一致 ============
def test_dual_chain_consistency(monkeypatch):
    """orchestrator 与 runtime_core 共用 select_injection_memories → 结果一致。"""
    from src.orchestrator import Orchestrator
    from src.runtime.runtime_core import RuntimeCore

    class _Ctx:
        user_message = "我们之前聊过爱吗"
        inputs = {"user_id": "366648462"}
        history = []
        retrieved_memories = []

    class _FakeVector:
        def search(self, query, top_k=5, user_id=None):
            return [{
                "mem_id": "old_love", "relevance": 0.8,
                "content": "20天前：爱是让对方被理解", "timestamp": (NOW - timedelta(hours=500)).isoformat(),
            }]

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

    recs = [
        _rec("old_love", 500, importance=0.85, content="20天前：爱是让对方被理解"),
        _rec("c1", 2, importance=0.5, content="今天闲聊1"),
        _rec("c2", 3, importance=0.5, content="今天闲聊2"),
    ]
    store = _FakeStore(recs)
    vec = _FakeVector()
    monkeypatch.setattr(
        "src.memory.memory_scope.collect_allowed_records",
        lambda store_, uid, owner_limit=None: store_.get_by_user(uid),
    )
    monkeypatch.setattr(
        "src.config.get",
        lambda path, default=None: 20 if path == "memory.retrieval_broad_k" else default,
    )

    # orchestrator 链
    orch = Orchestrator.__new__(Orchestrator)
    orch.memory_store = store
    orch.vector_memory = vec
    orch.target_user_id = "366648462"
    out_orch = [r["id"] for r in orch._collect_chat_memories("366648462", query="我们之前聊过爱吗")]

    # runtime_core 链
    rc = RuntimeCore.__new__(RuntimeCore)
    rc.memory_adapter = None
    rc._lazy_memory_store = store
    monkeypatch.setattr(rc, "get_vector_memory", lambda: vec)
    rc._stage_02_memory_retrieval(None, _Ctx())
    out_rc = [r["id"] for r in _Ctx.retrieved_memories] if not hasattr(rc, "_ctx_last") else out_orch
    # 直接读 ctx
    ctx = _Ctx()
    rc._stage_02_memory_retrieval(None, ctx)
    out_rc = [r["id"] for r in ctx.retrieved_memories]

    assert out_orch == out_rc, f"双链不一致: orchestrator={out_orch} runtime={out_rc}"
    assert "old_love" in out_orch
