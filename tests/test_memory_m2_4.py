# -*- coding: utf-8 -*-
"""M2-4 MC 记忆隔离兜底验收测试。

覆盖任务书 D 组 10 项：
1. metadata.frontend=mc → 排除
2. metadata.source=mc_events → 排除
3. id=mc_xxx → 排除
4. 三字段缺失 → 普通记录保留
5. source=runtime_pipeline + mem_ id → 保留
6. 普通 QQ 记录 → 保留
7. MC 与普通混合池 → 只排除 MC
8. 双链一致
9. M1/M2 回归（另跑）
10. 边界 fail-soft（metadata=None/非 dict/record 非 dict/id=None）
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.memory.memory_selection import _is_mc_frontend, select_injection_memories

NOW = datetime.now()


def _rec(mid, hours_ago=5, importance=0.5, content="", frontend=None, source="runtime_pipeline"):
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


# ============ 1-3: 三种可信信号 → 排除 ============
def test_frontend_mc_excluded():
    r = _rec("mem_0001", frontend="mc", content="MC 聊天")
    assert _is_mc_frontend(r) is True


def test_source_mc_events_excluded():
    r = _rec("mem_0002", source="mc_events", content="游戏事件")
    assert _is_mc_frontend(r) is True


def test_id_mc_prefix_excluded():
    r = _rec("mc_20260826120000_abcd", content="MC 事件（id 前缀）")
    assert _is_mc_frontend(r) is True


# ============ 4-6: 普通记录保留 ============
def test_missing_fields_kept():
    r = {"id": "mem_0003", "content": "普通记录", "timestamp": NOW.isoformat(),
         "user_id": "366648462", "role": "user", "importance": 0.5, "metadata": {}}
    assert _is_mc_frontend(r) is False


def test_runtime_pipeline_kept():
    r = _rec("mem_0004", source="runtime_pipeline", content="普通 runtime 记录")
    assert _is_mc_frontend(r) is False


def test_qq_frontend_kept():
    r = _rec("mem_0005", frontend="qq", content="QQ 记录")
    assert _is_mc_frontend(r) is False


# ============ 7: 混合池只排除 MC ============
def test_mixed_pool_excludes_only_mc():
    mc_chat = _rec("mem_0010", frontend="mc", content="MC 聊天")
    mc_evt = _rec("mc_20260826120000_xx", source="mc_events", content="MC 事件")
    qq = _rec("mem_0011", frontend="qq", content="QQ 普通")
    pool = [mc_chat, mc_evt, qq]
    kept = [r for r in pool if not _is_mc_frontend(r)]
    assert [r["id"] for r in kept] == ["mem_0011"], "混合池应只排除 MC"


# ============ 8: 双链一致（经 select_injection_memories 共享层） ============
def test_dual_chain_excludes_mc(monkeypatch):
    from src.orchestrator import Orchestrator
    from src.runtime.runtime_core import RuntimeCore

    class _Ctx:
        user_message = "你好"
        inputs = {"user_id": "366648462"}
        history = []
        retrieved_memories = []

    class _FakeVector:
        def search(self, query, top_k=5, user_id=None):
            return []

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
        _rec("mem_0020", 3, frontend="mc", content="MC 聊天"),
        _rec("mc_20260826120000_ab", 4, source="mc_events", content="MC 事件"),
        _rec("mem_0021", 1, frontend="qq", content="QQ 最近"),
        _rec("mem_0022", 50, importance=0.7, content="几天前的普通记录"),
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
    out_orch = [r["id"] for r in orch._collect_chat_memories("366648462", query="你好")]
    # runtime_core
    rc = RuntimeCore.__new__(RuntimeCore)
    rc.memory_adapter = None
    rc._lazy_memory_store = store
    monkeypatch.setattr(rc, "get_vector_memory", lambda: vec)
    ctx = _Ctx()
    rc._stage_02_memory_retrieval(None, ctx)
    out_rc = [r["id"] for r in ctx.retrieved_memories]
    assert sorted(out_orch) == sorted(out_rc), f"双链不一致: {out_orch} vs {out_rc}"
    # MC 记录（frontend=mc / source=mc_events / id=mc_）均不得进入注入
    for rid in out_orch:
        assert not rid.startswith("mc_"), f"MC 记录 {rid} 泄漏进注入"
    assert "mem_0021" in out_orch


# ============ 10: 边界 fail-soft ============
def test_boundary_fail_soft():
    # metadata=None
    r1 = {"id": "mem_0030", "content": "x", "metadata": None}
    assert _is_mc_frontend(r1) is False
    # metadata 非 dict
    r2 = {"id": "mem_0031", "content": "x", "metadata": "oops"}
    assert _is_mc_frontend(r2) is False
    # record 非 dict（对象形态）
    class _Obj:
        metadata = {"source": "mc_events"}
        id = "mem_0032"
    assert _is_mc_frontend(_Obj()) is True
    class _Obj2:
        metadata = {}
        id = "mem_0033"
    assert _is_mc_frontend(_Obj2()) is False
    # id=None
    r3 = {"id": None, "content": "x", "metadata": {}}
    assert _is_mc_frontend(r3) is False
    # 空 record
    assert _is_mc_frontend({}) is False
    assert _is_mc_frontend(None) is False


# ============ 集成：MC 事件记录不再进 recent 池 ============
def test_mc_events_not_in_recent_pool():
    """source=mc_events 的记录（此前漏网）现在被 recent 池排除。"""
    mc_evt = _rec("mc_20260826120000_cd", 2, source="mc_events", content="拾取钻石")
    qq = _rec("mem_0040", 1, content="QQ 最近对话")
    final = select_injection_memories([mc_evt, qq], [], query="")
    ids = [r["id"] for r in final]
    assert "mc_20260826120000_cd" not in ids, "MC 事件不应进 recent 池"
    assert "mem_0040" in ids
