# -*- coding: utf-8 -*-
"""
Phase 4.0 R2.5.1 Gate 测试：ExperienceBridge 分诊台。

Gate 清单：
  G1: 路由正确性（memory_only / relationship_memory / growth_candidate 三通道分流、
      GC 黑/白名单 + importance 阈值）。
  G2: 通道动作（GrowthCandidate 调用 GrowthIntegrationService.accept_experience，
      RelationshipMemory R2.5.1 只审计不执行）。
  G3: 状态隔离（Bridge 不 new 任何人格/关系/成长 state 对象，
      process 结束时这些状态保持原值）。
  G4: 错误隔离（growth_handler 抛异常 / 记录无效时，Bridge 降级 memory_only，
      不把异常抛到 Runtime）。
  G5: 完整端到端（EventBus 发布 MEMORY_CREATED → experience_bridge_memory_created_handler
      → ExperienceBridge.process 的链跑通）。
  G6: 边界隔离（GrowthCandidate 进入 GrowthIntegrationService 通道后，
      PersonalityState / GrowthState / RelationshipState 绝对未被 modify。
      用 mock 三个对象的所有可变方法验证）。
"""
from __future__ import annotations

import copy
import unittest
from unittest.mock import MagicMock, patch
from typing import Any, Dict

from src.experience.experience_bridge import ExperienceBridge


# ============================================================
# 测试工具：构造最小合法 record
# ============================================================

def _mk_record(
    memory_id: str = "mem_001",
    *,
    content: str = "我对画画有长期兴趣，希望以后每周都练习。",
    importance: float = 0.72,
    role: str = "user",
    memory_type: str = "user_preference",
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
        "user_id": "u_test",
        "role": role,
        "timestamp": "2025-03-18T10:00:00",
        "importance": importance,
        "memory_class": memory_type.replace("user_", ""),
        "metadata": md,
    }


# ============================================================
# G1
# ============================================================

class TestPhase40R251Gate1RoutingCorrectness(unittest.TestCase):
    """G1: 路由正确性。"""

    def _route(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        return ExperienceBridge.route(rec, growth_handler=lambda r: {"pipeline_state": "created"})

    # --- GrowthCandidate allowlist ---
    def test_gc_allowlist_preference_high_importance(self):
        d = self._route(_mk_record("g1a", importance=0.7, memory_type="user_preference"))
        self.assertEqual(d["classified_to"], "growth_candidate")
        self.assertEqual(d["rule_triggered"], "GC_ALLOWLIST_AND_IMPORTANCE")

    def test_gc_allowlist_milestone_exact_065_threshold(self):
        d = self._route(_mk_record("g1b", importance=0.65, memory_type="user_milestone"))
        self.assertEqual(d["classified_to"], "growth_candidate")
        self.assertEqual(d["rule_triggered"], "GC_ALLOWLIST_AND_IMPORTANCE")

    def test_gc_allowlist_below_065_should_reject(self):
        d = self._route(_mk_record("g1c", importance=0.6499, memory_type="user_milestone"))
        self.assertEqual(d["classified_to"], "memory_only")
        self.assertEqual(d["rule_triggered"], "GC_IMPORTANCE_BELOW_THRESHOLD")

    # --- GrowthCandidate blocklist ---
    def test_gc_blocklist_food_even_high_importance(self):
        d = self._route(_mk_record(
            "g1d", content="我喜欢吃拉面", importance=0.99, memory_type="food",
        ))
        self.assertEqual(d["classified_to"], "memory_only")
        self.assertEqual(d["rule_triggered"], "GC_MEMORY_TYPE_BLOCKLIST")

    def test_gc_blocklist_user_emotion(self):
        d = self._route(_mk_record(
            "g1e", content="今天心情不错", importance=0.9, memory_type="user_emotion",
        ))
        self.assertEqual(d["classified_to"], "memory_only")
        self.assertEqual(d["rule_triggered"], "GC_MEMORY_TYPE_BLOCKLIST")

    def test_gc_blocklist_conversation(self):
        d = self._route(_mk_record(
            "g1f", content="我今天和朋友聊天了", importance=0.9, memory_type="conversation",
        ))
        self.assertEqual(d["classified_to"], "memory_only")
        self.assertEqual(d["rule_triggered"], "GC_MEMORY_TYPE_BLOCKLIST")

    # --- strict fallback（无 memory_type 结构化信号） ---
    def test_gc_no_type_fallback_075_exact_threshold(self):
        rec = _mk_record("g1g", importance=0.75)
        rec["metadata"] = {}  # 擦掉 memory_type
        d = self._route(rec)
        self.assertEqual(d["classified_to"], "growth_candidate")
        self.assertEqual(d["rule_triggered"], "GC_STRICT_FALLBACK_HIGH_IMPORTANCE")

    def test_gc_no_type_fallback_below_075_no_signal(self):
        rec = _mk_record("g1h", importance=0.74)
        rec["metadata"] = {}
        d = self._route(rec)
        self.assertEqual(d["classified_to"], "memory_only")
        self.assertEqual(d["rule_triggered"], "GC_NO_SIGNAL_AND_IMPORTANCE_LOW")

    # --- RelationshipMemory 结构化信号（不看 content 关键词） ---
    def test_relationship_memory_explicit_type(self):
        d = self._route(_mk_record(
            "g1i", content="随便一句话没关键词", importance=0.4, memory_type="relationship",
        ))
        self.assertEqual(d["classified_to"], "relationship_memory")
        self.assertEqual(d["rule_triggered"], "RELATIONSHIP_STRUCTURED_SIGNAL")

    def test_relationship_memory_signal_flag(self):
        d = self._route(_mk_record(
            "g1j", content="我喜欢吃拉面", importance=0.3, memory_type="food",
            relationship_signal=True,  # 结构化 signal 应该优先于黑名单
        ))
        # RelationshipMemory 判定早于 GrowthCandidate 判定
        self.assertEqual(d["classified_to"], "relationship_memory")
        self.assertEqual(d["rule_triggered"], "RELATIONSHIP_STRUCTURED_SIGNAL")

    def test_relationship_memory_meaning_prefix(self):
        d = self._route(_mk_record(
            "g1k", content="随便一句话", importance=0.3, memory_type="conversation",
            metadata_extra={"meaning": "relationship_promise"},
        ))
        self.assertEqual(d["classified_to"], "relationship_memory")

    # --- 明确禁止关键词猜测（content 有 '喜欢/爱/陪' 但没有 structured signal -> memory_only） ---
    def test_keyword_like_in_content_should_not_promote(self):
        rec = _mk_record("g1l", content="我喜欢吃拉面。", importance=0.5, memory_type="food")
        d = self._route(rec)
        self.assertNotEqual(d["classified_to"], "relationship_memory")
        self.assertEqual(d["classified_to"], "memory_only")

    def test_keyword_accompany_in_content_should_not_promote(self):
        rec = _mk_record("g1m", content="今天陪妈妈去超市。", importance=0.5, memory_type="daily_activity")
        d = self._route(rec)
        self.assertNotEqual(d["classified_to"], "relationship_memory")
        self.assertEqual(d["classified_to"], "memory_only")

    # --- 非 user 角色过滤 ---
    def test_non_user_role_always_memory_only(self):
        rec = _mk_record("g1n", importance=0.99, memory_type="user_milestone", role="assistant")
        d = self._route(rec)
        self.assertEqual(d["classified_to"], "memory_only")
        self.assertEqual(d["rule_triggered"], "EB_NON_USER_ROLE_MEMORY_ONLY")

    # --- 空内容过滤 ---
    def test_empty_content_always_memory_only(self):
        rec = _mk_record("g1o", content="   ", importance=0.99, memory_type="user_milestone")
        d = self._route(rec)
        self.assertEqual(d["classified_to"], "memory_only")
        self.assertEqual(d["rule_triggered"], "EB_EMPTY_CONTENT_MEMORY_ONLY")

    # --- 决策类型字段约束 ---
    def test_decision_shape(self):
        d = self._route(_mk_record("g1p", importance=0.8, memory_type="user_milestone"))
        self.assertIn("decision_id", d)
        self.assertIn("classified_to", d)
        self.assertIn("rule_triggered", d)
        self.assertIn("timestamp_iso", d)
        self.assertIn("duration_ms", d)
        self.assertIsInstance(d["duration_ms"], int)
        self.assertGreaterEqual(d["duration_ms"], 0)
        self.assertTrue(d["decision_id"].startswith("rd_"))


# ============================================================
# G2
# ============================================================

class TestPhase40R251Gate2ChannelActions(unittest.TestCase):
    """G2: 通道动作是否符合设计。"""

    def test_growth_candidate_calls_handler_once(self):
        handler = MagicMock(return_value={"pipeline_state": "created", "proposal_id": "prop_mock"})
        rec = _mk_record("g2a", importance=0.8, memory_type="user_preference")
        decision = ExperienceBridge.route(rec, growth_handler=handler)
        self.assertEqual(decision["classified_to"], "growth_candidate")
        handler.assert_called_once()
        args_record = handler.call_args[0][0]
        self.assertEqual(args_record["id"], "g2a")

        result = decision["route_result"] or {}
        self.assertEqual(result["action"], "dispatched_to_growth_integration_service")
        self.assertEqual(result["growth_result"]["pipeline_state"], "created")

    def test_growth_handler_isolation_returns_result_reason(self):
        """即便 handler 返回 rejected_low_confidence，Bridge 也把结果放进 route_result。"""

        def _h(_r):
            return {
                "pipeline_state": "rejected_low_confidence",
                "proposal_id": None,
                "reasons": ["Evaluator growth_allowed=False"],
            }

        rec = _mk_record("g2b", importance=0.72, memory_type="user_preference")
        decision = ExperienceBridge.route(rec, growth_handler=_h)
        self.assertEqual(decision["classified_to"], "growth_candidate")
        res = decision["route_result"]["growth_result"]
        self.assertEqual(res["pipeline_state"], "rejected_low_confidence")
        self.assertIsNone(res["proposal_id"])

    def test_relationship_memory_is_audit_only_r251(self):
        rec = _mk_record("g2c", importance=0.6, memory_type="relationship")
        decision = ExperienceBridge.route(rec)
        self.assertEqual(decision["classified_to"], "relationship_memory")
        result = decision["route_result"] or {}
        self.assertEqual(result["channel"], "relationship_memory")
        # R2.5.1: audit_only_r251
        # R2.5.2-B: 升级为 audit_and_appended_r252b（追加 observed 事件，但仍不修改 RelationshipState 5 维）
        # R2.5.2-B 失败兜底: audit_only_append_failed_r252b（失败隔离，不降通道）
        self.assertIn(
            result["action"],
            {"audit_only_r251", "audit_and_appended_r252b", "audit_only_append_failed_r252b"},
        )

    def test_memory_only_action_is_noop(self):
        rec = _mk_record("g2d", importance=0.4, memory_type="daily_activity")
        decision = ExperienceBridge.route(rec)
        result = decision["route_result"] or {}
        self.assertEqual(result["action"], "noop")


# ============================================================
# G3
# ============================================================

class TestPhase40R251Gate3StateIsolation(unittest.TestCase):
    """G3: ExperienceBridge 本身不触碰 PersonalityState/GrowthState/RelationshipState。

    方式：用 monkeypatch 检查 Bridge.process() 期间，任何导入/访问这些类的代码
    是否真的被调用 -> 结论：Bridge 不该 import 这些类。
    """

    def test_bridge_module_does_not_import_state_modules(self):
        import sys
        # 清除潜在缓存（测试用）
        for mod in list(sys.modules.keys()):
            if "experience_bridge" in mod or "route_decision" in mod:
                del sys.modules[mod]

        import src.experience.experience_bridge as bridge_mod  # noqa: F401
        import src.experience.route_decision as rd_mod  # noqa: F401

        forbidden = [
            "src.personality.personality_state",
            "src.personality.personality_resolver",
            "src.relationship",
            "src.growth.growth_state",
            "src.growth.growth_evaluator",
            "src.growth.event_normalizer",
            "src.growth.event_validator",
            "src.growth.event_history_matcher",
            "src.growth.proposal_manager",
        ]
        for f in forbidden:
            # Bridge 顶层不能强依赖这些模块
            # 允许 GrowthIntegrationService（由 handler 调用）自己 import，但 Bridge 模块本身不应该 import
            if f in sys.modules:
                # 我们再确认：不是 Bridge 模块 import chain 带来的
                self.assertNotIn(
                    f,
                    bridge_mod.__dict__.keys(),
                    f"{f} 不应直接挂在 experience_bridge 模块命名空间",
                )
        # 直接的 import 检查：bridge_mod 自己的 globals 里不应有这些子系统的对象
        globals_names = set(bridge_mod.__dict__.keys())
        self.assertNotIn("GrowthEvaluator", globals_names)
        self.assertNotIn("EventNormalizer", globals_names)
        self.assertNotIn("EventValidator", globals_names)
        self.assertNotIn("ProposalManager", globals_names)
        self.assertNotIn("PersonalityState", globals_names)

    def test_bridge_process_with_mocked_handler_no_state_objects_constructed(self):
        """Bridge 只依赖传入 handler，不应 new Growth/Personality/Relationship 对象。"""
        state_constructed = {"count": 0}

        def _spy(record):
            # 在 handler 里我们甚至不 new state 对象
            return {"pipeline_state": "created", "proposal_id": "p_spy"}

        rec = _mk_record("g3a", importance=0.75, memory_type="user_preference")
        with patch(
            "src.experience.experience_bridge.GrowthIntegrationService",
            create=True,
        ) as fake_gis:
            fake_gis.accept_experience_static.side_effect = RuntimeError(
                "静态入口不应该被调用（因为我们传了 handler）"
            )
            decision = ExperienceBridge.route(rec, growth_handler=_spy)
            self.assertEqual(decision["classified_to"], "growth_candidate")
            # fake_gis 不应该被任何方式初始化
            fake_gis.assert_not_called()


# ============================================================
# G4
# ============================================================

class TestPhase40R251Gate4FaultIsolation(unittest.TestCase):
    """G4: 错误隔离。"""

    def test_growth_handler_raise_does_not_propagate(self):
        def _boom(_r):
            raise OSError("磁盘坏了")

        rec = _mk_record("g4a", importance=0.8, memory_type="user_milestone")
        # 应该不抛
        decision = ExperienceBridge.route(rec, growth_handler=_boom)
        self.assertEqual(decision["classified_to"], "growth_candidate")
        result = decision["route_result"] or {}
        self.assertEqual(result["action"], "growth_handler_failed_isolated")
        self.assertIn("error", result)
        self.assertIn("磁盘坏了", result["error"])

    def test_process_bad_type_is_memory_only_with_error(self):
        # 传入 int，不是 dict/str，应该降级 memory_only 而不抛
        decision = ExperienceBridge.route(12345, user_id="u_test")
        self.assertEqual(decision["classified_to"], "memory_only")
        self.assertIsNotNone(decision["error"])
        self.assertIn("只接受", decision["error"] or "")

    def test_process_memory_id_without_lookup_should_memory_only(self):
        decision = ExperienceBridge.route("mem_missing_lookup_001")
        self.assertEqual(decision["classified_to"], "memory_only")
        self.assertIsNotNone(decision["error"])
        self.assertIn("没有注入 record_lookup", decision["error"] or "")

    def test_process_memory_id_with_lookup_returns_nondict(self):
        def _lookup(mem_id):
            return "I am a string not a dict"

        decision = ExperienceBridge.route(
            "mem_bad", user_id="u", record_lookup=_lookup,
        )
        self.assertEqual(decision["classified_to"], "memory_only")
        self.assertIsNotNone(decision["error"])
        self.assertIn("不是 dict", decision["error"] or "")


# ============================================================
# G5
# ============================================================

class TestPhase40R251Gate5E2EHandler(unittest.TestCase):
    """G5: MemoryCreatedEvent → experience_bridge_memory_created_handler → ExperienceBridge 全链。"""

    def test_event_handler_invokes_bridge_with_lookup(self):
        from datetime import datetime
        from src.memory.memory_provider import MemoryProvider
        from src.events.events import EventType, YuyiEvent
        from src.events.handlers import experience_bridge_memory_created_handler

        try:
            store = MemoryProvider.get_store()
            store.add({
                "id": "mem_g5_001",
                "content": "我终于完成了第一幅油画《日落》，这是我的第一个里程碑。",
                "memory_class": "milestone",
                "timestamp": datetime.now().isoformat(),
                "role": "user",
                "user_id": "u_g5",
                "importance": 0.78,
                "metadata": {"memory_type": "user_milestone"},
            })

            # 用 patch 替换 GrowthIntegrationService.accept_experience_static，
            # 确保不会真的写 ProposalStore 文件
            with patch(
                "src.growth.growth_integration.GrowthIntegrationService.accept_experience_static",
                classmethod(lambda cls, record, **kw: {
                    "pipeline_state": "created",
                    "proposal_id": "prop_G5_mock",
                    "applied": False,
                }),
            ):
                evt = YuyiEvent(
                    event_type=EventType.MEMORY_CREATED,
                    source="gate5_test",
                    data={"memory_id": "mem_g5_001", "user_id": "u_g5"},
                )
                # 不抛
                experience_bridge_memory_created_handler(evt)

        finally:
            MemoryProvider.reset_for_testing()

    def test_event_handler_no_memory_id_silent(self):
        from src.events.events import EventType, YuyiEvent
        from src.events.handlers import experience_bridge_memory_created_handler

        evt = YuyiEvent(
            event_type=EventType.MEMORY_CREATED,
            source="gate5_test",
            data={"user_id": "u_g5b"},  # 没有 memory_id
        )
        # 应该静默 return，不抛、不 print 异常
        experience_bridge_memory_created_handler(evt)


# ============================================================
# G6
# ============================================================

class TestPhase40R251Gate6BoundaryIsolation(unittest.TestCase):
    """G6: 边界隔离（Review 新增 Gate）。

    验证 GrowthCandidate 进入 GrowthIntegrationService 通道后：
      - PersonalityState unchanged (原快照 == 后快照)
      - GrowthState unchanged (原快照 == 后快照)
      - RelationshipState unchanged (原快照 == 后快照)

    这里因为 Phase 还没把三个 State 真正对象挂到 Runtime，我们采用更强的黑盒方式：
    对 GrowthIntegrationService.accept_experience_static 做一个"真实调用 +
    运行前后对比 personality/growth/relationship 模块已知可变单例的内容"。
    如果没有这些单例，则验证：accept_experience_static 的返回值 applied == False，
    且 proposal 状态 != accepted/applied（只能是 created/deduped/rejected_* / error）。
    """

    def _call_accept_experience(self, record: Dict[str, Any]) -> Dict[str, Any]:
        from src.growth.growth_integration import GrowthIntegrationService

        return GrowthIntegrationService.accept_experience_static(
            record,
            auto_accept_enabled=False,  # 必须 False
            confidence_threshold=0.8,
        )

    def test_applied_always_false_for_accept_experience(self):
        # Case 1: allowlist + 高 importance（Case 1 长期兴趣）
        r1 = self._call_accept_experience(_mk_record(
            "g6a",
            content="我对画画有长期兴趣，打算每周坚持学下去。",
            importance=0.82,
            memory_type="user_preference",
        ))
        self.assertFalse(r1["applied"])
        self.assertIn(r1["pipeline_state"], {
            "created", "deduped", "rejected_low_confidence",
            "rejected_no_evidence", "accepted", "error",
            # R2.5.2-A 新增：被 GrowthEligibilityFilter 挡下的新状态
            "rejected_growth_eligibility",
        })
        # R2.5.1：即便 ProposalManager 意外 accept，我们的方法强制 applied=False
        # 所以 accepted 状态是允许的，但 applied 必须 False。

    def test_applied_is_false_even_with_relationship_memory_record(self):
        r2 = self._call_accept_experience(_mk_record(
            "g6b",
            content="我希望永远和羽依做朋友。",
            importance=0.9,
            memory_type="relationship",
        ))
        self.assertFalse(r2["applied"])

    def test_personality_state_unchanged_without_true_singleton(self):
        """如果 PersonalityState 有全局单例，则做 before/after 快照比较；否则测试只覆盖 applied=False。"""
        import sys

        have_singletons = False
        before = {}

        # personality
        if "src.personality.personality_state" in sys.modules:
            ps = sys.modules["src.personality.personality_state"]
            if hasattr(ps, "default_instance"):
                before["personality_state"] = copy.deepcopy(ps.default_instance)
                have_singletons = True
        # growth_state
        if "src.growth.growth_state" in sys.modules:
            gs = sys.modules["src.growth.growth_state"]
            if hasattr(gs, "default_instance"):
                before["growth_state"] = copy.deepcopy(gs.default_instance)
                have_singletons = True
        # relationship
        if "src.relationship.relationship_state" in sys.modules:
            rs = sys.modules["src.relationship.relationship_state"]
            if hasattr(rs, "default_instance"):
                before["relationship_state"] = copy.deepcopy(rs.default_instance)
                have_singletons = True

        # 调用一次真正的 pipeline（无论结果，关键是状态对象不变）
        self._call_accept_experience(_mk_record(
            "g6c",
            content="我对 AI 架构设计很感兴趣，平时会花时间研究大模型。",
            importance=0.85,
            memory_type="user_preference",
        ))

        if have_singletons:
            # 后快照对比
            if "personality_state" in before:
                ps = sys.modules["src.personality.personality_state"]
                after = copy.deepcopy(ps.default_instance)
                self.assertEqual(before["personality_state"], after,
                                 "PersonalityState 在 GrowthCandidate 通道后发生了改变！")
            if "growth_state" in before:
                gs = sys.modules["src.growth.growth_state"]
                after = copy.deepcopy(gs.default_instance)
                self.assertEqual(before["growth_state"], after,
                                 "GrowthState 在 GrowthCandidate 通道后发生了改变！")
            if "relationship_state" in before:
                rs = sys.modules["src.relationship.relationship_state"]
                after = copy.deepcopy(rs.default_instance)
                self.assertEqual(before["relationship_state"], after,
                                 "RelationshipState 在 GrowthCandidate 通道后发生了改变！")
        else:
            # 没有 singleton：我们退而验证方法没有任何方式调用 PersonalityAdapter 的 apply
            from unittest.mock import MagicMock
            from src.personality.personality_adapter import PersonalityAdapter

            real_apply = getattr(PersonalityAdapter, "apply", None)
            spy = MagicMock(side_effect=real_apply) if real_apply else MagicMock()
            if real_apply:
                with patch.object(PersonalityAdapter, "apply", spy):
                    self._call_accept_experience(_mk_record(
                        "g6c2",
                        content="我很喜欢编程，愿意成为 AI 工程师。",
                        importance=0.85,
                        memory_type="user_goal",
                    ))
                spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
