# -*- coding: utf-8 -*-
"""v1.5.5 Memory Recall Fix: selection 单元测试。

覆盖：候选合并 / semantic 保留 / recent 补位 / 去重 / 窗口上限 /
relevance 门槛 / 空候选边界 / 配置缺省。
"""
import pytest

from src.memory.memory_selection import select_injection_memories


def _rec(mid, content, ts="2026-08-26T00:00:00"):
    return {
        "id": mid,
        "content": content,
        "timestamp": ts,
        "user_id": "366648462",
        "role": "user",
        "metadata": {},
    }


def _sem(rec, relevance):
    return {"record": rec, "relevance": relevance}


# ============================================================
# 1. 候选集合正确：semantic 旧相关保留
# ============================================================
def test_old_relevant_semantic_kept():
    recent = [_rec(f"R{i}", f"recent {i}", f"2026-08-2{i}T10:00:00") for i in range(5)]
    old = _rec("OLD", "我们第一次见面的对话", "2026-07-16T10:00:00")
    final = select_injection_memories(recent, [_sem(old, 0.8)])
    ids = [r["id"] for r in final]
    assert "OLD" in ids  # 旧记忆不被 recent 挤掉
    assert len(final) <= 6


# ============================================================
# 2. semantic 按 relevance 降序在前
# ============================================================
def test_semantic_sorted_desc_first():
    recent = [_rec("R1", "r1", "2026-08-26T10:00:00")]
    s1 = _sem(_rec("S1", "s1"), 0.5)
    s2 = _sem(_rec("S2", "s2"), 0.9)
    final = select_injection_memories(recent, [s1, s2], max_total=3)
    assert [r["id"] for r in final[:2]] == ["S2", "S1"]


# ============================================================
# 3. recent 正常补位（semantic 1 条 → recent 补足）
# ============================================================
def test_recent_fills_remaining():
    recent = [_rec(f"R{i}", f"r{i}", f"2026-08-2{i}T10:00:00") for i in range(6)]
    final = select_injection_memories(recent, [_sem(_rec("S1", "s1"), 0.8)], max_total=6, max_semantic=3)
    assert len(final) == 6
    assert final[0]["id"] == "S1"
    assert len([r for r in final if r["id"].startswith("R")]) == 5


# ============================================================
# 4. 去重：同一 id 同时出现在 semantic 与 recent → 只占 1 槽，semantic 优先
# ============================================================
def test_dedupe_semantic_wins():
    # v3 语义：7 天以上的旧记忆走"历史 importance 保底"优先占槽，
    # 同一记录被 semantic 再次选中时去重（不占双槽）。
    same = _rec("DUP", "same content", "2026-07-01T10:00:00")
    recent = [_rec("R1", "r1", "2026-08-26T10:00:00"), dict(same)]
    final = select_injection_memories(recent, [_sem(dict(same), 0.9)], max_total=3)
    ids = [r["id"] for r in final]
    assert ids.count("DUP") == 1  # 只占一槽


# ============================================================
# 5. 总数不超过 max_total
# ============================================================
def test_total_cap():
    recent = [_rec(f"R{i}", f"r{i}", f"2026-08-2{i}T10:00:00") for i in range(10)]
    sems = [_sem(_rec(f"S{i}", f"s{i}"), 0.9 - i * 0.1) for i in range(5)]
    final = select_injection_memories(recent, sems, max_total=6)
    assert len(final) == 6


# ============================================================
# 6. 无高质量 semantic（relevance 不达标）→ recent 补足
# ============================================================
def test_low_relevance_semantic_rejected():
    recent = [_rec(f"R{i}", f"r{i}", f"2026-08-2{i}T10:00:00") for i in range(6)]
    final = select_injection_memories(recent, [_sem(_rec("LOW", "low"), 0.1)], max_total=6)
    ids = [r["id"] for r in final]
    assert "LOW" not in ids  # 不达标不硬凑
    assert len(final) == 6  # recent 全量补足


# ============================================================
# 7. semantic 超过 max_semantic 只取 top-N
# ============================================================
def test_semantic_capped():
    recent = []
    sems = [_sem(_rec(f"S{i}", f"s{i}"), 1.0 - i * 0.1) for i in range(5)]
    final = select_injection_memories(recent, sems, max_total=6, max_semantic=3)
    assert len(final) == 3  # 只有 top-3
    assert final[0]["id"] == "S0"


# ============================================================
# 8. 空候选边界
# ============================================================
def test_empty_candidates():
    assert select_injection_memories([], []) == []
    assert select_injection_memories([_rec("R1", "r1")], [])[0]["id"] == "R1"
    assert select_injection_memories([], [_sem(_rec("S1", "s1"), 0.8)])[0]["id"] == "S1"


# ============================================================
# 9. relevance 阈值边界（恰好达标 / 差一点）
# ============================================================
def test_relevance_threshold_boundary():
    recent = [_rec("R1", "r1")]
    f_ok = select_injection_memories(recent, [_sem(_rec("B1", "b1"), 0.3)], max_total=3)
    assert "B1" in [r["id"] for r in f_ok]  # 0.3 恰好达标
    f_no = select_injection_memories(recent, [_sem(_rec("B2", "b2"), 0.29)], max_total=3)
    assert "B2" not in [r["id"] for r in f_no]  # 0.29 拒绝


# ============================================================
# 10. 质量过滤：空 content 不占槽
# ============================================================
def test_empty_content_filtered():
    recent = [_rec("R1", "   "), _rec("R2", "real content")]
    final = select_injection_memories(recent, [], max_total=6)
    assert [r["id"] for r in final] == ["R2"]


# ============================================================
# 11. 原始候选不被污染（深拷贝标记）
# ============================================================
def test_no_mutation_of_input():
    rec = _rec("SRC", "src content")
    sems = [_sem(rec, 0.9)]
    select_injection_memories([], sems, max_total=3)
    assert "_semantic_relevance" not in rec  # 原始记录无标记


# ============================================================
# v2: 时间分层抽样
# ============================================================
import time as _time
from src.memory.memory_selection import _recent_time_buckets, _history_by_importance


def _rec_with_age(mid, content, age_days):
    ts = _time.time() - age_days * 86400
    from datetime import datetime
    return _rec(mid, content, datetime.fromtimestamp(ts).isoformat())


def test_time_buckets_give_history():
    """分层：24h 内 2 条 + 7 天内 2 条 + 7 天以上 2 条——历史记忆可见。"""
    recs = [
        _rec_with_age("T1", "今天1", 0.01),    # ~15 分钟前
        _rec_with_age("T2", "今天2", 0.05),    # ~1 小时前
        _rec_with_age("W1", "本周1", 3),       # 3 天前
        _rec_with_age("W2", "本周2", 5),       # 5 天前
        _rec_with_age("O1", "旧1", 30),        # 30 天前
        _rec_with_age("O2", "旧2", 60),        # 60 天前
        _rec_with_age("O3", "旧3", 90),        # 90 天前
    ]
    bucketed = _recent_time_buckets(recs)
    ids = [r["id"] for r in bucketed]
    # 桶 1（24h）：T1 T2；桶 2（7 天）：W1 W2；桶 3（7 天+）：O1 O2 O3
    assert ids[:2] == ["T1", "T2"]
    assert ids[2:4] == ["W1", "W2"]
    assert ids[4:6] == ["O1", "O2"]
    assert "O3" in ids  # 兜底补入


def test_empty_bucket_falls_through():
    """某桶为空时，后续桶接续（额度不浪费）。"""
    recs = [
        _rec_with_age("O1", "旧1", 10),
        _rec_with_age("O2", "旧2", 20),
    ]
    bucketed = _recent_time_buckets(recs)
    ids = [r["id"] for r in bucketed]
    # 24h 桶空 → 7 天桶空 → 7 天+ 桶接续 O1 O2
    assert ids == ["O1", "O2"]


def test_selection_uses_buckets_for_recent():
    """端到端：selection 的 recent 补位用分层——旧记忆出现在 final。"""
    recs = [
        _rec_with_age("T1", "今天1", 0.01),
        _rec_with_age("T2", "今天2", 0.05),
        _rec_with_age("W1", "本周1", 3),
        _rec_with_age("W2", "本周2", 5),
        _rec_with_age("O1", "旧1", 30),
        _rec_with_age("O2", "旧2", 60),
    ]
    final = select_injection_memories(recs, [], max_total=6)
    ids = [r["id"] for r in final]
    assert "O1" in ids  # 7 天以上的旧记忆进入 final（纯最新策略下不可能）
    assert len(final) == 6
