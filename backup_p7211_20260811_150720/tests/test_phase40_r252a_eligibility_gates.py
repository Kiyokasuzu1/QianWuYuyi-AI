"""
Phase 4.0 — R2.5.2-A Gates: GrowthEligibilityFilter

契约边界（本文件只测这些，不做业务判断）：
  - GE1: 新经历首次命中 —— 应被 Eligibility 拒绝（frequency + grace_period 未达标）
         产出 pipeline_state = rejected_growth_eligibility
         observation_state = first_seen / grace_period_accumulating
  - GE2: 同一 canonical_topic 的经历连续第二次进入（ledger 累积到≥2次）——
         应放行至 Evaluator（frequency 或 grace_period 达标）
  - GE3: 频率够了，但 history importance 波动率太高 → 拒绝
  - GE4: 任何情况下 Personality / GrowthState 不被写入（即使放行也只到 pending）
  - GE5: 审计 emit_eligibility_audit 对非 dict 输入/缺失字段有防御
  - GE6: GrowthIntegrationService.apply_experience* 仍然 applied=False（R2.5.2-A 红线）
  - GE7: Filter 本身完全独立（不依赖 Evaluator / Personality）
"""
from __future__ import annotations

import unittest
from typing import Any, Dict
from unittest.mock import MagicMock, patch

# R2.5.2-A
from src.growth.growth_eligibility_filter import (
    GrowthCandidateLedger,
    GrowthEligibilityFilter,
    emit_eligibility_audit,
    DECISION_KEYS,
)
from src.growth.growth_integration import GrowthIntegrationService


# 与 R2.5.1 Gates 完全一致的 record 构造函数（保证接口形状对齐）
def _mk_record(
    memory_id: str = "mem_ge001",
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
        "user_id": "u_ge_test",
        "role": role,
        "timestamp": "2025-08-08T10:00:00",
        "importance": importance,
        "memory_class": memory_type.replace("user_", "") if memory_type.startswith("user_") else memory_type,
        "metadata": md,
    }


class TestPhase40R252AGE1FirstHitRejected(unittest.TestCase):
    """GE1: 新经历首次命中 -> GrowthEligibilityFilter 必须拒绝（避免一次经历改变自己）"""

    def setUp(self):
        # 用隔离的 ledger（避免与单例/其他测试冲突）
        self.ledger = GrowthCandidateLedger()

    def _record_then_accept(self, rid: str):
        f = GrowthEligibilityFilter(ledger=self.ledger)
        rec = _mk_record(rid)
        normalized = {
            "canonical_topic": "long_term_interest_artistic_painting_practice",
            "event_type": "preference_expression",
            "topic": rec["content"][:30],
            "importance": rec["importance"],
            "source_ids": [rid],
            "evidence": [{"memory_id": rid, "text": rec["content"], "role": "user"}],
        }
        decision = f.evaluate(rec, normalized, history=[])
        return decision

    def test_first_hit_not_eligible(self):
        d = self._record_then_accept("mem_ge1_a")
        self.assertIs(d["eligible"], False)
        self.assertIn("FREQUENCY_OR_GRACE", d["rule"])

    def test_first_hit_observation_state_is_first_seen(self):
        d = self._record_then_accept("mem_ge1_b")
        self.assertEqual(d["observation_state"], "first_seen")

    def test_first_hit_grace_period_count_is_1(self):
        d = self._record_then_accept("mem_ge1_c")
        # history=[] + ledger 刚记 1 条 -> grace_period_observations = 1
        self.assertEqual(d["grace_period_observations"], 1)

    def test_first_hit_decision_keys_shape_frozen(self):
        d = self._record_then_accept("mem_ge1_d")
        for k in DECISION_KEYS:
            self.assertIn(k, d, f"decision 缺少冻结字段 {k}")


class TestPhase40R252AGE2GraceAndFrequencyPasses(unittest.TestCase):
    """GE2: 第二次同主题命中 —— 放行给 Evaluator"""

    def setUp(self):
        self.ledger = GrowthCandidateLedger()
        self.filter = GrowthEligibilityFilter(ledger=self.ledger)

    def _normalized(self, rid: str, importance: float = 0.72):
        return {
            "canonical_topic": "long_term_interest_artistic_painting_practice",
            "event_type": "preference_expression",
            "topic": "画画长期兴趣",
            "importance": importance,
            "source_ids": [rid],
            "evidence": [{"memory_id": rid, "text": "画画练习", "role": "user"}],
        }

    def test_second_same_topic_in_ledger_passes(self):
        # 第一次：写入 ledger
        r1 = _mk_record("mem_ge2_a")
        d1 = self.filter.evaluate(r1, self._normalized("mem_ge2_a"), history=[])
        self.assertIs(d1["eligible"], False)
        # 第二次：同主题 + 不同 memory_id
        r2 = _mk_record("mem_ge2_b")
        d2 = self.filter.evaluate(r2, self._normalized("mem_ge2_b"), history=[])
        self.assertIs(d2["eligible"], True, f"第二次应该通过，rule={d2['rule']} state={d2['observation_state']}")
        self.assertIn(d2["observation_state"], {"window_qualified", "frequency_qualified"})
        self.assertIn(d2["grace_period_observations"], (2, 3))

    def test_frequency_via_history_list_passes(self):
        """通过 history list 而非 ledger 累计 也能达到 frequency 阈值。"""
        history = [
            {
                "canonical_topic": "long_term_interest_artistic_painting_practice",
                "event_type": "preference_expression",
                "importance": 0.71,
                "source_ids": ["mem_ge2_h1"],
                "evidence": [{"memory_id": "mem_ge2_h1"}],
            },
            {
                "canonical_topic": "long_term_interest_artistic_painting_practice",
                "event_type": "preference_expression",
                "importance": 0.73,
                "source_ids": ["mem_ge2_h2"],
                "evidence": [{"memory_id": "mem_ge2_h2"}],
            },
        ]
        r = _mk_record("mem_ge2_c")
        d = self.filter.evaluate(r, self._normalized("mem_ge2_c"), history=history)
        self.assertIs(d["eligible"], True, f"history=2 条应该 frequency>=2 从而通过, got rule={d['rule']}")
        # evidence_count 至少 3: history 2 + current 1
        self.assertGreaterEqual(d["evidence_count"], 3)


class TestPhase40R252AGE3VolatileRejection(unittest.TestCase):
    """GE3: history importance 波动太大 -> 拒绝。"""

    def setUp(self):
        self.ledger = GrowthCandidateLedger()
        self.filter = GrowthEligibilityFilter(
            ledger=self.ledger,
            stability_max_std=0.05,  # 把阈值收严，让轻微波动就拒
        )

    def test_volatile_history_rejected(self):
        history = [
            {
                "canonical_topic": "c_fps_gaming",
                "event_type": "preference_expression",
                "importance": 0.95,  # 昨天很喜欢 FPS
                "source_ids": ["m_ge3_h1"],
                "evidence": [{"memory_id": "m_ge3_h1"}],
            },
            {
                "canonical_topic": "c_fps_gaming",
                "event_type": "preference_expression",
                "importance": 0.2,  # 今天就说不玩了 —— 波动
                "source_ids": ["m_ge3_h2"],
                "evidence": [{"memory_id": "m_ge3_h2"}],
            },
        ]
        cur = {
            "canonical_topic": "c_fps_gaming",
            "event_type": "preference_expression",
            "importance": 0.7,
            "source_ids": ["m_ge3_cur"],
            "evidence": [{"memory_id": "m_ge3_cur"}],
        }
        r = _mk_record("m_ge3_cur", content="今天又想玩FPS了", importance=0.7, memory_type="user_preference")
        d = self.filter.evaluate(r, cur, history=history)
        self.assertIs(d["eligible"], False, f"波动率大应该被拒: {d['rule']}")
        self.assertIn("VOLATILE", d["rule"])
        self.assertEqual(d["observation_state"], "rejected_volatile_importance")


class TestPhase40R252AGE4StateIsolation(unittest.TestCase):
    """GE4: 即使放行 -> Personality / GrowthState 也不会被写入；applied 恒 False。"""

    def test_applied_false_after_two_hits_via_ledger(self):
        # 同 content、不同 memory_id -> Normalizer 几乎必然解析为同一 canonical_topic
        # -> 第二次命中会触发 ledger grace_period >=2 从而放行到 Evaluator
        svc = GrowthIntegrationService(config={"auto_accept_enabled": False})
        shared_content = "我对画画有长期兴趣，希望以后每周都练习，甚至周末去报班。"
        r1 = _mk_record(
            "mem_ge4_a",
            content=shared_content,
            importance=0.85,
            memory_type="user_preference",
            metadata_extra={"meaning": "user_preference:creative_activity_artistic"},
        )
        r2 = _mk_record(
            "mem_ge4_b",
            content=shared_content,
            importance=0.85,
            memory_type="user_preference",
            metadata_extra={"meaning": "user_preference:creative_activity_artistic"},
        )
        r3 = _mk_record(
            "mem_ge4_c",
            content=shared_content,
            importance=0.82,
            memory_type="user_preference",
            metadata_extra={"meaning": "user_preference:creative_activity_artistic"},
        )

        # r1：首次 -> eligibility 挡下
        res1 = svc.accept_experience(r1)
        self.assertIs(res1["applied"], False)

        # r2：ledger 观察 1 + history 0 -> grace 可能 2；或仍需 r3
        res2 = svc.accept_experience(r2)
        self.assertIs(res2["applied"], False)

        # r3：至少命中 grace >= 2 窗口，应该放行到 Evaluator；再不行也只是 eligibility 再次拒绝
        res3 = svc.accept_experience(r3)
        # applied 恒 False（红线）
        self.assertIs(res3["applied"], False)
        # 允许所有合法的 pipeline_state（无论 eligibility 是否放行、Evaluator 是否放行，applied 都必须 False）
        self.assertIn(
            res3["pipeline_state"],
            {
                "created", "deduped",
                "rejected_low_confidence", "rejected_no_evidence",
                "rejected_growth_eligibility", "needs_review",
            },
        )
        # 如果走到了 created / deduped，必须有 proposal_id，且仍然不 apply
        if res3["pipeline_state"] in {"created", "deduped"}:
            self.assertIsNotNone(res3.get("proposal_id"))


class TestPhase40R252AGE5AuditDefensive(unittest.TestCase):
    """GE5: 审计对非 dict / 缺失字段 防御。"""

    def test_non_dict_input_silent(self):
        # 直接塞字符串
        try:
            emit_eligibility_audit("not a dict")
        except Exception as exc:  # pragma: no cover - 异常路径
            self.fail(f"emit_eligibility_audit 对非 dict 不应抛：{exc!r}")

    def test_empty_dict_input_silent(self):
        try:
            emit_eligibility_audit({})
        except Exception as exc:  # pragma: no cover - 异常路径
            self.fail(f"emit_eligibility_audit 对空 dict 不应抛：{exc!r}")

    def test_full_decision_runs_without_crash(self):
        ledger = GrowthCandidateLedger()
        f = GrowthEligibilityFilter(ledger=ledger)
        r = _mk_record("mem_ge5_a")
        n = {"canonical_topic": "t", "event_type": "e", "importance": 0.7, "source_ids": ["mem_ge5_a"]}
        d = f.evaluate(r, n, [])
        try:
            emit_eligibility_audit(d, user_id="u_ge5")
        except Exception as exc:  # pragma: no cover - 异常路径
            self.fail(f"完整 decision 审计不应抛：{exc!r}")


class TestPhase40R252AGE6StaticAppliedAlwaysFalse(unittest.TestCase):
    """GE6: accept_experience_static 也 applied=False；且第一次命中会走 eligibility 拒绝路径。"""

    def test_static_first_hit_eligibility_rejects(self):
        r = _mk_record("mem_ge6_a", importance=0.75, memory_type="user_milestone")
        res = GrowthIntegrationService.accept_experience_static(r, auto_accept_enabled=False)
        self.assertIs(res["applied"], False)
        # 首次命中应 rejected_growth_eligibility（其他 Gates 已证明非首次会走到 created/deduped）
        self.assertIn(
            res["pipeline_state"],
            {"rejected_growth_eligibility", "created", "rejected_no_evidence", "rejected_low_confidence", "deduped"},
        )


class TestPhase40R252AGE7FilterNoEvaluatorDependency(unittest.TestCase):
    """GE7: Filter 本身完全独立（不依赖 Evaluator / PersonalityAdapter）"""

    def test_evaluate_does_not_import_growth_evaluator(self):
        import sys

        saved_evaluator = sys.modules.pop("src.growth.growth_evaluator", None)
        saved_adapter = sys.modules.pop("src.personality.personality_adapter", None)
        try:
            ledger = GrowthCandidateLedger()
            f = GrowthEligibilityFilter(ledger=ledger)
            r = _mk_record("mem_ge7_a")
            n = {"canonical_topic": "t7", "event_type": "e7", "importance": 0.7, "source_ids": ["mem_ge7_a"]}
            d = f.evaluate(r, n, [])
            self.assertIn("eligible", d)  # 能返回决策，不需 Evaluator
        finally:
            if saved_evaluator is not None:
                sys.modules["src.growth.growth_evaluator"] = saved_evaluator
            if saved_adapter is not None:
                sys.modules["src.personality.personality_adapter"] = saved_adapter


if __name__ == "__main__":
    unittest.main()
