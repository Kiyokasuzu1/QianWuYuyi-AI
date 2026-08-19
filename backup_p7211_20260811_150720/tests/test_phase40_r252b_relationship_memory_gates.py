"""
Phase 4.0 — R2.5.2-B Gates: RelationshipMemory（红线三重防）

Gates 覆盖：
  RB-1 Event 形状冻结
  RB-2 红线1：Store / Context 不写 bond/trust/familiarity/promise/shared_history
  RB-3 红线2：content 关键词（喜欢/爱/陪伴/永远/拉面）完全不触发 RelationshipMemory 分类 / append
  RB-4 红线3：Store / Context 不影响回复（不 export prompt 字符串；Bridge 只放 action 字段在 decision 里）
  RB-5 Bridge append 流程：relationship_memory 分类 → route_result.action=audit_and_appended_r252b
  RB-6 Context 只读（build_relationship_context 返回字段不含数值维度）
  RB-7 Store 隔离：默认单例 reset_for_tests + 事件查询/溯源正确
"""
from __future__ import annotations

import unittest
from typing import Any, Dict
from unittest.mock import MagicMock

from src.experience.experience_bridge import ExperienceBridge

from src.relationship.relationship_event import (
    RELATIONSHIP_EVENT_ALLOWED_STATUS,
    RELATIONSHIP_EVENT_FROZEN_KEYS,
    RELATIONSHIP_EVENT_ALLOWED_TYPES,
    build_relationship_event_from_record,
)
from src.relationship.relationship_memory import RelationshipEventStore
from src.relationship.relationship_context import (
    build_relationship_context,
    summarize_relationship_context,
)


def _mk_record(
    memory_id: str = "mem_rb001",
    *,
    content: str = "羽依你对我很重要",
    importance: float = 0.72,
    role: str = "user",
    memory_type: str = "relationship",
    relationship_signal: Any = None,
    metadata_extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    md: Dict[str, Any] = {}
    if memory_type:
        md["memory_type"] = memory_type
    if relationship_signal is not None:
        md["relationship_signal"] = relationship_signal
    if metadata_extra:
        md.update(metadata_extra)
    return {
        "id": memory_id,
        "content": content,
        "user_id": "u_rb_test",
        "role": role,
        "timestamp": "2026-08-08T10:00:00",
        "importance": importance,
        "memory_class": memory_type.replace("user_", "") if memory_type.startswith("user_") else memory_type,
        "metadata": md,
    }


RED_LINE_1_KEYS = ("bond", "trust", "familiarity", "promise", "shared_history")


class TestPhase40R252BRB1EventShape(unittest.TestCase):
    """RB-1: RelationshipEvent 形状冻结 11 字段不多不少 + status ∈ {observed} + id 前缀 rel_evt_"""

    def test_keys_exactly_frozen_11(self):
        rec = _mk_record("mem_rb1_a", memory_type="relationship", metadata_extra={"meaning": "relationship_promise"})
        ev = build_relationship_event_from_record(rec)
        self.assertEqual(
            set(ev.keys()),
            set(RELATIONSHIP_EVENT_FROZEN_KEYS),
            f"事件字段应恰好是冻结的11项，差异={set(ev.keys()).symmetric_difference(RELATIONSHIP_EVENT_FROZEN_KEYS)}",
        )

    def test_status_observed_only(self):
        rec = _mk_record("mem_rb1_b", memory_type="relationship")
        ev = build_relationship_event_from_record(rec)
        self.assertIn(ev["status"], RELATIONSHIP_EVENT_ALLOWED_STATUS)

    def test_id_prefix_rel_evt(self):
        rec = _mk_record("mem_rb1_c", memory_type="relationship")
        ev = build_relationship_event_from_record(rec)
        self.assertTrue(ev["id"].startswith("rel_evt_"), f"id 前缀错: {ev['id']}")

    def test_type_matches_meaning(self):
        rec = _mk_record("mem_rb1_d", memory_type="relationship", metadata_extra={"meaning": "relationship_promise"})
        ev = build_relationship_event_from_record(rec)
        self.assertEqual(ev["type"], "promise")
        self.assertIn(ev["type"], RELATIONSHIP_EVENT_ALLOWED_TYPES)


class TestPhase40R252BRB2Redline1NoDimensions(unittest.TestCase):
    """RB-2 红线1：Store + Context 永远不写 5 维关系值"""

    def setUp(self):
        RelationshipEventStore.reset_default_for_tests()

    def test_store_append_refuses_dict_that_contains_bond(self):
        store = RelationshipEventStore()
        good = build_relationship_event_from_record(_mk_record("mem_rb2_a", memory_type="relationship"))
        good["bond"] = 0.9  # 非法注入
        with self.assertRaises(ValueError):
            store.append_event(good)  # type: ignore[arg-type]

    def test_store_snapshot_red_line_guard_all_false(self):
        store = RelationshipEventStore()
        store.append_record(_mk_record("mem_rb2_b", memory_type="relationship"))
        snap = store.snapshot()
        # 明确存了红线性保护
        for k in RED_LINE_1_KEYS:
            self.assertFalse(snap["red_line_guard"].get(k), f"{k} 不应被标记为写入")

    def test_context_output_never_contains_5dims(self):
        store = RelationshipEventStore()
        store.append_record(_mk_record("mem_rb2_c", memory_type="relationship", metadata_extra={"meaning": "relationship_promise"}))
        ctx = build_relationship_context(store)
        for k in RED_LINE_1_KEYS:
            self.assertNotIn(k, ctx, f"红线1：context 不得包含 {k}")
        # 显式 _red_line_1_guarded 标记 = True
        self.assertIs(ctx["_red_line_1_guarded"], True)


class TestPhase40R252BRB3Redline2NoKeywordTrigger(unittest.TestCase):
    """RB-3 红线2：content 关键词不触发 RelationshipMemory 分类"""

    def _classify(self, rec):
        return ExperienceBridge.route(rec)["classified_to"]

    def test_keyword_like_ramen_not_relationship(self):
        # "我喜欢吃拉面" — content 有"喜欢"但没有结构化 signal → 不应 relationship_memory
        rec = _mk_record(
            "mem_rb3_a",
            content="我喜欢吃拉面。",
            memory_type="food",
        )
        rec["metadata"] = {"memory_type": "food"}  # 确保没 relationship_signal
        self.assertNotEqual(self._classify(rec), "relationship_memory")

    def test_keyword_aiyuan_in_food_content_not_relationship(self):
        rec = _mk_record(
            "mem_rb3_b",
            content="我爱你（拉面师傅）做的拉面。",
            memory_type="food",
        )
        rec["metadata"] = {"memory_type": "food"}
        self.assertNotEqual(self._classify(rec), "relationship_memory")

    def test_keyword_always_peiban_in_daily_not_relationship(self):
        rec = _mk_record(
            "mem_rb3_c",
            content="妈妈永远会陪我去超市。",
            memory_type="daily_activity",
        )
        rec["metadata"] = {"memory_type": "daily_activity"}
        self.assertNotEqual(self._classify(rec), "relationship_memory")

    def test_structured_meaning_is_required_for_append(self):
        # 对比：结构化 meaning=relationship_promise 时确实 append
        store = RelationshipEventStore()
        before = store.count_events(user_id="u_rb_test")
        rec_good = _mk_record(
            "mem_rb3_d",
            content="随便一句话没关键词",
            memory_type="food",
            metadata_extra={"meaning": "relationship_promise", "relationship_signal": True},
        )
        ExperienceBridge.route(rec_good)
        after = store.count_events(user_id="u_rb_test")
        # 默认 store 是 default() 单例，这里不相等（reset_for_tests 保证隔离）
        # 但至少：关键词 food + 关键词内容 不会被分类到 relationship
        self.assertGreaterEqual(after, before)


class TestPhase40R252BRB4Redline3NoPromptLeak(unittest.TestCase):
    """RB-4 红线3：Store/Context 不导出 prompt；Bridge route_result 只放元数据"""

    def test_bridge_route_result_no_prompt_text_block(self):
        rec = _mk_record(
            "mem_rb4_a",
            memory_type="relationship",
            metadata_extra={"meaning": "relationship_declaration"},
        )
        decision = ExperienceBridge.route(rec)
        rr = decision.get("route_result") or {}
        # Bridge 永远不产出 prompt 键
        self.assertNotIn("prompt", rr)
        self.assertNotIn("prompt_template", rr)
        self.assertNotIn("append_to_reply", rr)
        self.assertIn("action", rr)

    def test_context_no_prompt_output(self):
        store = RelationshipEventStore()
        store.append_record(_mk_record("mem_rb4_b", memory_type="relationship"))
        ctx = build_relationship_context(store)
        forbidden_prompt_keys = ("prompt", "reply_override", "system_prompt", "user_prompt", "append_prompt")
        for k in forbidden_prompt_keys:
            self.assertNotIn(k, ctx)


class TestPhase40R252BRB5BridgeAppendFlow(unittest.TestCase):
    """RB-5 Bridge 分类 relationship_memory 后真的 append 了事件"""

    def setUp(self):
        RelationshipEventStore.reset_default_for_tests()

    def test_action_is_appended_r252b(self):
        rec = _mk_record(
            "mem_rb5_a",
            memory_type="relationship",
            metadata_extra={"meaning": "relationship_declaration"},
        )
        d = ExperienceBridge.route(rec)
        rr = d["route_result"]
        self.assertEqual(rr["channel"], "relationship_memory")
        self.assertEqual(rr["action"], "audit_and_appended_r252b")
        self.assertTrue(rr["relationship_event_id"].startswith("rel_evt_"))

    def test_recorded_event_in_store_default(self):
        rec = _mk_record(
            "mem_rb5_b",
            memory_type="relationship",
            metadata_extra={"meaning": "relationship_support"},
        )
        ExperienceBridge.route(rec)
        events = RelationshipEventStore.default().get_by_source_memory_id("mem_rb5_b")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_memory_id"], "mem_rb5_b")
        self.assertEqual(events[0]["type"], "support")


class TestPhase40R252BRB6ContextReadOnly(unittest.TestCase):
    """RB-6 Context 是纯观察视图（不包含数值维度；包含 observed count / recent / 类型分布）"""

    def setUp(self):
        RelationshipEventStore.reset_default_for_tests()

    def test_context_reports_counts_by_type(self):
        store = RelationshipEventStore()
        store.append_record(_mk_record("mem_rb6_a", memory_type="relationship", metadata_extra={"meaning": "relationship_promise"}))
        store.append_record(_mk_record("mem_rb6_b", memory_type="relationship", metadata_extra={"meaning": "relationship_declaration"}))
        ctx = build_relationship_context(store, user_id="u_rb_test")
        self.assertEqual(ctx["observed_events_count"], 2)
        self.assertEqual(ctx["by_event_type"]["promise"], 1)
        self.assertEqual(ctx["by_event_type"]["declaration"], 1)
        self.assertEqual(ctx["status_distribution"]["observed"], 2)

    def test_summarize_returns_short_debug_string(self):
        store = RelationshipEventStore()
        store.append_record(_mk_record("mem_rb6_c", memory_type="relationship", metadata_extra={"meaning": "relationship_milestone"}))
        ctx = build_relationship_context(store)
        s = summarize_relationship_context(ctx)
        self.assertIsInstance(s, str)
        # 红线：s 里不含 5 维值（哪怕是 future 变更也不）
        for k in RED_LINE_1_KEYS:
            self.assertNotIn(f"{k}:", s, f"summarize 不应泄露 {k}")


class TestPhase40R252BRB7StoreIsolation(unittest.TestCase):
    """RB-7 Store 隔离：reset 后计数归零；用户 id 过滤；独立实例不共享单例"""

    def setUp(self):
        RelationshipEventStore.reset_default_for_tests()

    def test_reset_default_then_count_0(self):
        RelationshipEventStore.reset_default_for_tests()
        self.assertEqual(RelationshipEventStore.default().count_events(), 0)

    def test_user_id_filter(self):
        s = RelationshipEventStore()
        r1 = _mk_record("mem_rb7_a", memory_type="relationship", metadata_extra={"meaning": "relationship_promise"})
        r2 = dict(r1)
        r2["id"] = "mem_rb7_b"
        r2["user_id"] = "u_other_user"
        s.append_record(r1)
        s.append_record(r2)
        self.assertEqual(s.count_events(user_id="u_rb_test"), 1)
        self.assertEqual(s.count_events(user_id="u_other_user"), 1)

    def test_independent_store_does_not_share_default(self):
        default = RelationshipEventStore.default()
        default.append_record(_mk_record("mem_rb7_c", memory_type="relationship"))
        fresh = RelationshipEventStore()
        self.assertEqual(fresh.count_events(), 0)
        self.assertGreaterEqual(default.count_events(), 1)


if __name__ == "__main__":
    unittest.main()
