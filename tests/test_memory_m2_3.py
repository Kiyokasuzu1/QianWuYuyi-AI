# -*- coding: utf-8 -*-
"""M2-3 Semantic Diversity 去重验收测试。

覆盖任务书 Test 1-10。Test 1 按实现审计结论处理：
- 任务书原始 A/B/C 输入在 0.85 token Jaccard 下 J(A,B)=0.316、J(A,C)=0.571——
  任何字面度量下 A/C 相似度必然 ≥ A/B（A 与 C 仅差 2 字符），
  因此"B 过滤 C 保留"需要语义 embedding（M3 范围）；M2-3 按任务书
  保守原则（0.85，宁可漏过滤不误删）实现，Test 1 验证保守性
  （任务书输入零误删）+ 机制（真重复对 J≥0.85 被 greedy 过滤）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.memory.memory_selection import (
    _apply_semantic_diversity, _jaccard_similarity, select_injection_memories,
)

NOW = datetime.now()


def _rec(mid, hours_ago, importance=0.5, content="", memory_type="user_experience"):
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
        "metadata": {"memory_type": memory_type},
    }


def _sem(record, relevance):
    return {"record": record, "relevance": relevance}


# ============ Test 1a: 任务书输入保守性（0.85 下零误删） ============
def test_taskbook_inputs_no_false_delete():
    """任务书 A/B/C：0.85 保守阈值下全部保留（含 C——相似度 J(A,C)=0.571
    反而高于 J(A,B)=0.316，任何字面度量无法区分；保守优先不误删）。"""
    a = _rec("A", 20, content="今天讨论了羽依的身体系统")
    b = _rec("B", 20, content="今天我们聊了羽依身体系统的设计")
    c = _rec("C", 20, content="今天讨论了羽依的记忆系统")
    # 直接测 diversity 纯函数：按分数降序输入
    out = _apply_semantic_diversity(
        [_sem(a, 0.9), _sem(b, 0.85), _sem(c, 0.8)], threshold=0.85)
    ids = [x["record"]["id"] for x in out]
    assert set(ids) == {"A", "B", "C"}, f"0.85 保守阈值不应误删任何一条，实际 {ids}"


# ============ Test 1b: 真重复被过滤（机制验证） ============
def test_true_duplicate_filtered_high_score_kept():
    """完全重复（J=1.0）：高分保留、低分过滤。"""
    a = _rec("A", 20, importance=0.8, content="我们昨天去雪原了")
    b = _rec("B", 20, importance=0.4, content="我们昨天去雪原了")  # 完全重复
    out = _apply_semantic_diversity([_sem(a, 0.95), _sem(b, 0.9)], threshold=0.85)
    ids = [x["record"]["id"] for x in out]
    assert ids == ["A"], f"真重复应只留高分 A，实际 {ids}"
    assert _jaccard_similarity("我们昨天去雪原了", "我们昨天去雪原了") == 1.0


# ============ Test 2: 高相似但不同事实不误删 ============
def test_same_topic_different_fact_kept():
    a = _rec("A", 10, content="羽依未来可以进入玩偶")
    b = _rec("B", 10, content="羽依进入玩偶后需要摄像头")
    out = _apply_semantic_diversity([_sem(a, 0.9), _sem(b, 0.85)], threshold=0.85)
    ids = [x["record"]["id"] for x in out]
    assert set(ids) == {"A", "B"}, f"不同事实不应被误删，实际 {ids}"
    assert _jaccard_similarity("羽依未来可以进入玩偶", "羽依进入玩偶后需要摄像头") < 0.85


# ============ Test 3: 高分优先 ============
def test_high_score_kept_greedy():
    a = _rec("A", 10, content="今天讨论了羽依的身体系统")   # final 分高
    b = _rec("B", 10, content="今天讨论了羽依的身体系统")   # 完全重复，分低
    out = _apply_semantic_diversity([_sem(a, 0.92), _sem(b, 0.71)], threshold=0.85)
    assert [x["record"]["id"] for x in out] == ["A"], "greedy 应保留高分 A"


# ============ Test 4: 三槽限制 + diversity 生效 ============
def test_slot_limit_with_diversity():
    recs = [_rec(f"m{i}", 10 + i, content=f"重复内容主题{i % 3}") for i in range(20)]
    # 前 3 条完全相同（重复），其余不同
    recs[0] = _rec("dup0", 10, content="我们讨论了羽依的身体系统")
    recs[1] = _rec("dup1", 10, content="我们讨论了羽依的身体系统")
    recs[2] = _rec("dup2", 10, content="我们讨论了羽依的身体系统")
    sem = [_sem(r, 0.9 - i * 0.01) for i, r in enumerate(recs)]
    final = select_injection_memories(recs, sem, query="")
    sem_ids = [r["id"] for r in final if r.get("_semantic_relevance") is not None]
    assert len(sem_ids) <= 3, f"semantic 槽应 ≤3，实际 {sem_ids}"
    assert len(set(sem_ids)) == len(sem_ids)
    assert "dup0" in sem_ids and "dup1" not in sem_ids, "重复应只留高分一条"


# ============ Test 5: 弱时间意图保持 ============
def test_weak_intent_preserved_with_diversity():
    old1 = _rec("o1", 600, content="20天前：爱是让对方被理解")
    old2 = _rec("o2", 500, content="20天前：爱是让对方被理解")  # 与 o1 重复
    new = _rec("new", 2, content="今天：日常闲聊")
    pool = [old1, old2, new] + [_rec(f"p{i}", 100 + i * 5) for i in range(15)]
    sem = [_sem(old1, 0.85), _sem(old2, 0.84), _sem(new, 0.9)]
    f = select_injection_memories(pool, sem, query="你当时是怎么定义爱的？")
    sem_ids = [r["id"] for r in f if r.get("_semantic_relevance") is not None]
    # "当时" → 旧优先（o1 在语义槽第一）；重复 o2 被 diversity 过滤；
    # new 不重复且 relevance 达标 → 正常进入第二语义槽（行为正确）
    assert sem_ids == ["o1", "new"], f"'当时'应旧优先且去重，实际 {sem_ids}"
    f2 = select_injection_memories(pool, sem, query="最近一次你说什么")
    sem_ids2 = [r["id"] for r in f2 if r.get("_semantic_relevance") is not None]
    assert sem_ids2 == ["new"], f"'最近一次'应新优先，实际 {sem_ids2}"


# ============ Test 6: selection_scorer=false 时 diversity 仍工作 ============
def test_diversity_works_when_scorer_disabled(monkeypatch):
    import src.config as _cfgmod
    _orig_get = _cfgmod.get

    def _fake_get(path, default=None):
        if path == "memory.selection_scorer":
            return False
        return _orig_get(path, default)

    monkeypatch.setattr("src.config.get", _fake_get)
    a = _rec("A", 10, content="我们讨论了羽依的身体系统")
    b = _rec("B", 10, content="我们讨论了羽依的身体系统")  # 完全重复
    c = _rec("C", 10, content="今天聊了羽依的记忆系统")
    final = select_injection_memories(
        [a, b, c], [_sem(a, 0.95), _sem(b, 0.9), _sem(c, 0.8)], query="")
    sem_ids = [r["id"] for r in final if r.get("_semantic_relevance") is not None]
    # relevance 排序（scorer 关）+ diversity 仍生效：重复 B 被过滤
    assert "A" in sem_ids and "B" not in sem_ids, f"scorer=false 时 diversity 应仍生效，实际 {sem_ids}"
    assert len(sem_ids) <= 3


# ============ Test 7: 空文本/缺字段 fail-soft ============
def test_empty_text_fail_soft():
    a = _rec("A", 10, content="")
    b = _rec("B", 10, content=None)  # type: ignore[arg-type]
    out = _apply_semantic_diversity([_sem(a, 0.9), _sem(b, 0.85)], threshold=0.85)
    assert len(out) == 2, "空文本候选不参与比较、不异常"
    out2 = _apply_semantic_diversity([], threshold=0.85)
    assert out2 == []


# ============ Test 8: 中文/Unicode 正常 ============
def test_chinese_unicode_handled():
    a = _rec("A", 10, content="我们昨天去了雪原，捡到了钻石")
    b = _rec("B", 10, content="我们昨天去了雪原，捡到了钻石")  # 完全重复（中文）
    out = _apply_semantic_diversity([_sem(a, 0.9), _sem(b, 0.8)], threshold=0.85)
    assert len(out) == 1, "中文重复应被过滤"
    # 中文不同内容不误删
    c = _rec("C", 10, content="我们昨天去了下界，打败了凋灵")
    out2 = _apply_semantic_diversity([_sem(a, 0.9), _sem(c, 0.8)], threshold=0.85)
    assert len(out2) == 2


# ============ Test 9: 双链一致 ============
def test_dual_chain_consistency(monkeypatch):
    from src.orchestrator import Orchestrator
    from src.runtime.runtime_core import RuntimeCore

    class _Ctx:
        user_message = "身体系统"
        inputs = {"user_id": "366648462"}
        history = []
        retrieved_memories = []

    class _FakeVector:
        def search(self, query, top_k=5, user_id=None):
            return [
                {"mem_id": "A", "relevance": 0.9, "content": "今天讨论了羽依的身体系统",
                 "timestamp": (NOW - timedelta(hours=10)).isoformat()},
                {"mem_id": "B", "relevance": 0.85, "content": "今天讨论了羽依的身体系统",
                 "timestamp": (NOW - timedelta(hours=10)).isoformat()},
            ]

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
        _rec("A", 10, content="今天讨论了羽依的身体系统"),
        _rec("B", 10, content="今天讨论了羽依的身体系统"),
        _rec("C", 2, content="最近闲聊"),
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
    # orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.memory_store = store
    orch.vector_memory = vec
    orch.target_user_id = "366648462"
    out_orch = [r["id"] for r in orch._collect_chat_memories("366648462", query="身体系统")]
    # runtime_core
    rc = RuntimeCore.__new__(RuntimeCore)
    rc.memory_adapter = None
    rc._lazy_memory_store = store
    monkeypatch.setattr(rc, "get_vector_memory", lambda: vec)
    ctx = _Ctx()
    rc._stage_02_memory_retrieval(None, ctx)
    out_rc = [r["id"] for r in ctx.retrieved_memories]
    # 双链内容一致（共享 select_injection_memories 层；顺序差异源于 M1-2
    # fusion sort 对同分记录的不稳定排序，非 M2-3 引入）
    assert sorted(out_orch) == sorted(out_rc), f"双链集合不一致: {out_orch} vs {out_rc}"
    # diversity 只作用于 semantic 槽：重复 B 不得出现在语义槽（recent 桶
    # 通道允许补入——任务书规定 diversity 不触碰 recent buckets）
    assert "A" in out_orch
    # 语义槽只含 A（B 被 diversity 过滤）；recent 通道的 B 不携带语义标记
    sem_orch = [r["id"] for r in orch._collect_chat_memories("366648462", query="身体系统")
                if r.get("_semantic_relevance") is not None]
    assert sem_orch == ["A"], f"语义槽应只含 A（B 被过滤），实际 {sem_orch}"


# ============ Test 10: 历史保底不受影响 ============
def test_history_fallback_unaffected_by_diversity():
    """重要历史记忆即使与 semantic 候选高度相似，保底仍保留。"""
    old_imp = _rec("floor", 20 * 24, importance=0.85, content="20天前：我们讨论了羽依的身体系统")
    sem_dup = _rec("sdup", 10, importance=0.5, content="20天前：我们讨论了羽依的身体系统")
    recent = [_rec(f"r{i}", 1 + i % 29, importance=0.5) for i in range(100)] + [old_imp]
    sem = [_sem(sem_dup, 0.9)]
    final = select_injection_memories(recent, sem, query="身体系统")
    ids = [r["id"] for r in final]
    assert "floor" in ids, "历史保底不得被 diversity 删除"
    # diversity 只作用于 semantic 候选内部，不与保底通道交叉（保底独立保留）
