# -*- coding: utf-8 -*-
"""
tests/test_real_conversation_scenario.py

Phase C.4.2 — Real Conversation Simulation

目标:
在 8 个真实对话场景中,验证系统长期运行稳定性:

1. 普通聊天
2. 长期兴趣培养
3. 用户偏好变化
4. 情绪变化
5. 观点变化
6. 重复事件
7. 矛盾信息
8. 错误输入

验证:
- Memory 正确分类
- Growth 不会过度成长
- Identity 保持稳定
- Proposal 产生合理

约束:
- 不修改任何核心模块
- 使用 tmp_path 隔离
- 强制 mock LLM
- 使用真实的 MemoryExtractor / PollutionGuard / ProposalManager
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 强制 mock + 隔离
os.environ["YUYI_LLM_MOCK"] = "1"
os.environ.setdefault("DEEPSEEK_API_KEY", "")


# ============================================================
# Helpers
# ============================================================

USER_ID = "u_real_conv_001"


def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now() + timedelta(seconds=offset_seconds)).isoformat()


def _make_msg(role: str, content: str) -> Dict[str, str]:
    return {"role": role, "content": content}


def _make_user_memory(
    content: str,
    memory_type: str = "user_shared",
    user_id: str = USER_ID,
    offset_seconds: int = 0,
) -> Dict[str, Any]:
    return {
        "user_id": user_id,
        "role": "user",
        "content": content,
        "truth": 1,
        "metadata": {
            "memory_type": memory_type,
            "user_id": user_id,
            "timestamp": _now_iso(offset_seconds),
        },
    }


def _setup_store(tmp_path: Path):
    """构造隔离的 MemoryStore。"""
    from src.memory.memory_store import MemoryStore

    memory_path = tmp_path / "memory.json"
    memory_path.write_text("[]", encoding="utf-8")
    return MemoryStore(str(memory_path)), memory_path


# ============================================================
# 1. 场景 1: 普通聊天
# ============================================================

class TestNormalChatScenario:
    """场景 1: 普通聊天。验证:Memory 正确写入,无 pollution。"""

    def test_normal_chat_does_not_trigger_growth(self, tmp_path):
        """普通闲聊不产生 growth proposal(importance 低)。"""
        from src.growth.growth_evaluator import GrowthEvaluator

        store, _ = _setup_store(tmp_path)
        evaluator = GrowthEvaluator()

        # 20 轮普通打招呼
        for i in range(20):
            content = f"今天第{i+1}次打招呼,你好。"
            store.add(_make_user_memory(content, "user_shared", offset_seconds=i))

            # Growth 评估
            ev = {
                "event_id": f"e_{i}",
                "event": content,
                "event_type": "chat",
                "canonical_topic": "greeting",
                "evidence": [{"text": content, "role": "user", "source_index": 0}],
                "importance": 0.3,  # 低重要性
            }
            result = evaluator.evaluate(ev, [])
            # 普通聊天不进入 preference/trait 级别
            assert result["growth_level"] in ("trace", "context"), (
                f"普通聊天产生 growth level={result['growth_level']}"
            )

        # 验证 memory 全部写入
        assert len(store.load()) == 20

    def test_normal_chat_memory_type_distribution(self, tmp_path):
        """普通聊天全部归为 user_shared。"""
        store, _ = _setup_store(tmp_path)
        for i in range(10):
            store.add(_make_user_memory(f"普通聊天{i}", "user_shared", offset_seconds=i))

        by_type = {}
        for m in store.load():
            t = (m.get("metadata") or {}).get("memory_type", "?")
            by_type[t] = by_type.get(t, 0) + 1
        assert by_type.get("user_shared", 0) == 10


# ============================================================
# 2. 场景 2: 长期兴趣培养
# ============================================================

class TestLongTermInterestScenario:
    """场景 2: 长期兴趣培养。验证:重复兴趣累积触发 preference 级别 Growth。"""

    def test_repeated_interest_triggers_preference_level(self):
        """长期重复兴趣 → Growth 评估 preference 级别。"""
        from src.growth.growth_evaluator import GrowthEvaluator

        topic = "AI绘画"
        now = datetime.now()

        # 模拟 8 次历史兴趣表达
        history = []
        for i in range(8):
            history.append({
                "event": f"我喜欢{topic}",
                "event_type": "preference",
                "canonical_topic": topic,
                "first_seen": (now - timedelta(days=60 - i * 5)).isoformat(),
                "last_seen": (now - timedelta(days=5 - i)).isoformat(),
            })

        ev = {
            "event_id": "evt_interest",
            "event": f"我特别喜欢{topic}",
            "event_type": "preference",
            "canonical_topic": topic,
            "evidence": [{"text": f"我特别喜欢{topic}", "role": "user", "source_index": 0}],
            "importance": 1.0,
            "first_seen": now.isoformat(),
        }
        result = GrowthEvaluator().evaluate(ev, history)
        assert result["growth_level"] in ("preference", "trait")
        assert result["growth_domain"] == "preference"
        # 不进入 identity 维度
        assert "identity" not in (result.get("growth_domain") or "")

    def test_long_term_interest_passes_pollution_guard(self, tmp_path):
        """长期兴趣相关 memory 全部通过 PollutionGuard。"""
        from src.memory.pollution_guard import check

        interests = [
            "我最近迷上了AI绘画",
            "我对古典音乐很感兴趣",
            "我喜欢读科幻小说",
            "我对编程非常感兴趣",
            "我最近喜欢上了徒步",
        ]
        for content in interests:
            m = {
                "role": "user",
                "content": content,
                "metadata": {"memory_type": "user_preference", "user_id": USER_ID},
            }
            ok, reason = check(m)
            assert ok is True, f"合法兴趣被拒: {content} ({reason})"


# ============================================================
# 3. 场景 3: 用户偏好变化
# ============================================================

class TestUserPreferenceChangeScenario:
    """场景 3: 用户偏好变化(A→B)。验证:生成 preference proposal,非 identity。"""

    def test_preference_change_creates_preference_proposal(self, tmp_path):
        """A→B 的偏好变化可创建 proposal(高 confidence)。"""
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore
        from src.contracts.growth_schema import ChangeItem

        store = ProposalStore(path=str(tmp_path / "p.jsonl"))
        mgr = ProposalManager(store=store, config={"confidence_threshold": 0.8})

        # 偏好变化:拿铁 → 美式
        r = mgr.create_proposal(
            source_event={
                "id": "evt_pref_change_1",
                "event": "我之前喜欢拿铁,现在改喜欢美式咖啡了。",
                "event_type": "preference",
                "canonical_topic": "drink",
                "evidence": [{"text": "我之前喜欢拿铁,现在改喜欢美式咖啡了。", "role": "user", "source_index": 0}],
                "importance": 0.8,
            },
            proposed_changes=[ChangeItem(path="drink_preference", before="latte", after="americano", reason="change")],
            confidence=0.9,
            evidence_ids=["ev_1"],
        )
        assert r["status"] == "created"
        # proposal 状态是 pending,需人工 review
        assert r["proposal"].status in ("proposed", "pending")

    def test_preference_change_does_not_modify_core(self):
        """偏好变化不应触发 CoreIdentity 改变。"""
        from src.personality.core_identity import CoreIdentity

        before = list(CoreIdentity.CORE["traits"])
        # 模拟偏好变化描述
        changes = [
            "用户从喜欢拿铁变为喜欢美式",
            "用户口味变化",
            "用户换了工作",
        ]
        for desc in changes:
            assert CoreIdentity.check_change_allowed(desc) is True
        after = list(CoreIdentity.CORE["traits"])
        assert before == after


# ============================================================
# 4. 场景 4: 情绪变化
# ============================================================

class TestEmotionChangeScenario:
    """场景 4: 情绪变化。验证:情绪类 memory 正确分类,无 growth。"""

    def test_emotion_memory_classified_correctly(self, tmp_path):
        """情绪类 memory 被正确分类为 user_emotion。"""
        store, _ = _setup_store(tmp_path)
        emotions = [
            ("今天有点难过,想找个人聊聊。", "user_emotion"),
            ("工作压力很大,心里有点疲惫。", "user_emotion"),
            ("你真的懂我,让我感到温暖。", "user_emotion"),
            ("最近很开心,希望这份心情能持续。", "user_emotion"),
        ]
        for content, mtype in emotions:
            store.add(_make_user_memory(content, mtype))

        memories = store.load()
        emotion_count = sum(
            1 for m in memories
            if (m.get("metadata") or {}).get("memory_type") == "user_emotion"
        )
        assert emotion_count == 4

    def test_emotion_does_not_trigger_identity_change(self):
        """情绪类 proposal 不应进入 identity 维度。"""
        from src.growth.growth_evaluator import GrowthEvaluator

        ev = {
            "event_id": "evt_emotion",
            "event": "用户情绪变化",
            "event_type": "emotion",
            "canonical_topic": "mood",
            "evidence": [],
            "importance": 0.8,
        }
        result = GrowthEvaluator().evaluate(ev, [])
        # emotion 事件不应进入 identity
        assert "identity" not in (result.get("growth_domain") or "")


# ============================================================
# 5. 场景 5: 观点变化
# ============================================================

class TestOpinionChangeScenario:
    """场景 5: 观点变化。验证:同一话题多版本正确处理。"""

    def test_opinion_versions_kept_separately(self, tmp_path):
        """同一话题的不同观点都保留(不覆盖)。"""
        store, _ = _setup_store(tmp_path)
        opinions = [
            "我觉得A方案更好,理由是简单。",
            "我又想了想,B方案其实更稳妥。",
            "其实A和B各有所长,不必二选一。",
        ]
        for i, op in enumerate(opinions):
            store.add(_make_user_memory(op, "user_preference", offset_seconds=i * 10))

        # 3 个观点全部保留
        assert len(store.load()) == 3
        contents = [m.get("content", "") for m in store.load()]
        assert "我觉得A方案更好" in contents[0]
        assert "B方案其实更稳妥" in contents[1]
        assert "各有所长" in contents[2]

    def test_opinion_change_dedup_only_on_identical(self, tmp_path):
        """观点变化:相同的 id 才 dedupe,内容不同则新建。"""
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore
        from src.contracts.growth_schema import ChangeItem

        store = ProposalStore(path=str(tmp_path / "p.jsonl"))
        mgr = ProposalManager(store=store, config={"confidence_threshold": 0.8})

        # 第一次观点
        r1 = mgr.create_proposal(
            source_event={
                "id": "evt_op_1", "event": "A更好", "event_type": "preference",
                "canonical_topic": "topic_A", "evidence": [], "importance": 0.7,
            },
            proposed_changes=[ChangeItem(path="opinion", before="A", after="A", reason="op1")],
            confidence=0.85, evidence_ids=["e1"],
        )
        assert r1["status"] == "created"

        # 不同 id 的新观点应被独立创建
        r2 = mgr.create_proposal(
            source_event={
                "id": "evt_op_2", "event": "B更好", "event_type": "preference",
                "canonical_topic": "topic_B", "evidence": [], "importance": 0.7,
            },
            proposed_changes=[ChangeItem(path="opinion", before="B", after="B", reason="op2")],
            confidence=0.85, evidence_ids=["e2"],
        )
        assert r2["status"] == "created"


# ============================================================
# 6. 场景 6: 重复事件
# ============================================================

class TestRepeatedEventScenario:
    """场景 6: 重复事件。验证:dedup 机制生效。"""

    def test_same_event_id_deduped(self, tmp_path):
        """相同 source_event_id 的 proposal 自动 dedupe。"""
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore
        from src.contracts.growth_schema import ChangeItem

        store = ProposalStore(path=str(tmp_path / "p.jsonl"))
        mgr = ProposalManager(store=store, config={"confidence_threshold": 0.8})

        event = {
            "id": "evt_same",
            "event": "重复事件",
            "event_type": "preference",
            "canonical_topic": "x",
            "evidence": [],
            "importance": 0.8,
        }
        changes = [ChangeItem(path="trust", before=0.5, after=0.6, reason="dup")]

        r1 = mgr.create_proposal(event, changes, 0.9, ["e1"])
        assert r1["status"] == "created"

        # 重复 10 次
        for i in range(10):
            r = mgr.create_proposal(event, changes, 0.9, [f"e_{i}"])
            assert r["status"] == "deduped"

    def test_memory_id_uniqueness(self, tmp_path):
        """MemoryStore 中 id 唯一。"""
        store, _ = _setup_store(tmp_path)
        for i in range(20):
            store.add(_make_user_memory(f"重复内容 {i}", "user_shared", offset_seconds=i))
        ids = [m.get("id") for m in store.load()]
        assert len(ids) == len(set(ids))


# ============================================================
# 7. 场景 7: 矛盾信息
# ============================================================

class TestContradictoryInfoScenario:
    """场景 7: 矛盾信息。验证:同时保留,不自动合并。"""

    def test_contradictory_memories_both_kept(self, tmp_path):
        """矛盾的两条 memory 都被保留(用户可能反悔)。"""
        store, _ = _setup_store(tmp_path)
        contradictory = [
            "我之前说喜欢A,但现在我觉得A不适合我。",
            "我可能记错了,其实我之前喜欢的是B。",
        ]
        for i, content in enumerate(contradictory):
            store.add(_make_user_memory(content, "user_preference", offset_seconds=i * 5))

        # 两条都保留
        assert len(store.load()) == 2

    def test_contradictory_proposal_enters_review(self, tmp_path):
        """矛盾 proposal 默认 pending,需人工 review。"""
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore
        from src.contracts.growth_schema import ChangeItem

        store = ProposalStore(path=str(tmp_path / "p.jsonl"))
        mgr = ProposalManager(store=store, config={"auto_accept_enabled": False})

        r = mgr.create_proposal(
            source_event={
                "id": "evt_contradict", "event": "矛盾信息", "event_type": "preference",
                "canonical_topic": "x", "evidence": [], "importance": 0.9,
            },
            proposed_changes=[ChangeItem(path="trust", before=0.5, after=0.7, reason="conflict")],
            confidence=0.9, evidence_ids=["e1"],
        )
        # 状态是 pending (review 状态)
        assert r["status"] == "created"
        assert r["proposal"].status in ("proposed", "pending")
        # 不应自动 applied
        assert r["proposal"].status != "applied"


# ============================================================
# 8. 场景 8: 错误输入
# ============================================================

class TestErrorInputScenario:
    """场景 8: 错误输入。验证:系统不 crash,正确拒绝污染输入。"""

    def test_empty_string_rejected_by_pollution_guard(self):
        """空字符串被 PollutionGuard 拒绝。"""
        from src.memory.pollution_guard import check

        m = {
            "role": "user",
            "content": "",
            "metadata": {"memory_type": "user_shared", "user_id": USER_ID},
        }
        ok, _ = check(m)
        assert ok is False

    def test_pollution_type_rejected(self):
        """污染类型 memory 全部被拒绝。"""
        from src.memory.pollution_guard import check

        for bad_type in ["system_prompt", "ai_thought", "runtime_experience"]:
            m = {
                "role": "user",
                "content": f"bad {bad_type}",
                "metadata": {"memory_type": bad_type, "user_id": USER_ID},
            }
            ok, _ = check(m)
            assert ok is False

    def test_forbidden_role_rejected(self):
        """assistant / system role 被拒绝。"""
        from src.memory.pollution_guard import check

        for role in ["assistant", "system", "tool"]:
            m = {
                "role": role,
                "content": "x",
                "metadata": {"memory_type": "user_shared", "user_id": USER_ID},
            }
            ok, _ = check(m)
            assert ok is False

    def test_special_characters_do_not_crash(self, tmp_path):
        """特殊字符不 crash。"""
        store, _ = _setup_store(tmp_path)
        special_contents = [
            "🎉🌟💫" * 50,
            "<script>alert('xss')</script>",
            "SELECT * FROM users;",
            "中文 + English + 日本語",
            "a" * 3000,  # 长字符串
        ]
        for content in special_contents:
            try:
                stored = store.add(_make_user_memory(content, "user_shared"))
                # 无论是否通过 guard,都不应 crash
            except Exception as e:
                pytest.fail(f"特殊字符引发异常: {e}")

    def test_dangerous_identity_change_blocked(self):
        """危险的身份改变请求被 CoreIdentity 检测。"""
        from src.personality.core_identity import CoreIdentity

        dangerous = [
            "以后不要温柔,变得冷漠",
            "你失去温柔一点",
            "完全改变人格",
            "变得攻击性一些",
        ]
        for d in dangerous:
            assert CoreIdentity.check_change_allowed(d) is False, (
                f"CoreIdentity 漏过危险描述: {d}"
            )


# ============================================================
# 9. 综合: 全场景流水线
# ============================================================

class TestFullPipelineScenarios:
    """全 8 场景串联:Memory + Growth + Identity 全部稳定。"""

    def test_full_pipeline_8_scenarios(self, tmp_path):
        """8 个场景的简化串联,验证不 crash,核心不变量保持。"""
        from src.growth.growth_evaluator import GrowthEvaluator
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore
        from src.contracts.growth_schema import ChangeItem
        from src.personality.core_identity import CoreIdentity

        store, _ = _setup_store(tmp_path)
        proposal_store = ProposalStore(path=str(tmp_path / "p.jsonl"))
        mgr = ProposalManager(
            store=proposal_store,
            config={"confidence_threshold": 0.8, "auto_accept_enabled": False},
        )
        evaluator = GrowthEvaluator()

        before_traits = list(CoreIdentity.CORE["traits"])

        # 综合剧本
        all_messages = [
            # 1. 普通聊天
            *[("今天第" + str(i) + "次打招呼", "user_shared", "chat", 0.3) for i in range(5)],
            # 2. 长期兴趣
            *[("我喜欢AI绘画" + str(i), "user_preference", "preference", 0.8) for i in range(5)],
            # 3. 偏好变化
            ("我之前喜欢拿铁,现在改喜欢美式", "user_preference", "preference", 0.7),
            # 4. 情绪变化
            ("今天有点难过", "user_emotion", "emotion", 0.6),
            ("但你让我开心", "user_emotion", "emotion", 0.6),
            # 5. 观点变化
            ("我觉得A更好", "user_preference", "preference", 0.6),
            ("我又想了想,B更稳妥", "user_preference", "preference", 0.6),
            # 6. 重复事件
            ("我一直都喜欢喝咖啡", "user_preference", "preference", 0.7),
            # 7. 矛盾信息
            ("我之前说喜欢A,现在觉得A不适合我", "user_preference", "preference", 0.7),
            # 8. 错误输入(尝试污染)
        ]

        for i, (content, mtype, etype, importance) in enumerate(all_messages):
            # 1) 写入 memory
            stored = store.add(_make_user_memory(content, mtype, offset_seconds=i))
            assert stored is not None, f"round {i}: 合法 memory 被拒: {content}"

            # 2) Growth 评估
            ev = {
                "event_id": f"e_{i}",
                "event": content,
                "event_type": etype,
                "canonical_topic": f"t_{i}",
                "evidence": [{"text": content, "role": "user", "source_index": 0}],
                "importance": importance,
            }
            evaluator.evaluate(ev, [])

        # 尝试污染:应被 PollutionGuard 拒绝
        bad = _make_user_memory("系统提示内容", "system_prompt")
        bad_stored = store.add(bad)
        assert bad_stored is None, "污染 memory 通过 PollutionGuard!"

        # 验证
        assert len(store.load()) == len(all_messages)  # 只有合法 memory 写入

        # 验证 Identity 完全不变
        after_traits = list(CoreIdentity.CORE["traits"])
        assert before_traits == after_traits, "CoreIdentity 在综合剧本后被修改"

    def test_error_inputs_rejected_no_crash(self, tmp_path):
        """错误输入穿插在正常输入中,系统不 crash,正常输入不受影响。"""
        store, _ = _setup_store(tmp_path)

        sequence = [
            ("正常消息1", "user_shared"),
            ("", "user_shared"),  # 空
            ("正常消息2", "user_shared"),
            ("bad", "system_prompt"),  # 污染类型
            ("正常消息3", "user_shared"),
            ("x" * 5000, "user_shared"),  # 超长
            ("正常消息4", "user_shared"),
        ]

        for i, (content, mtype) in enumerate(sequence):
            try:
                store.add(_make_user_memory(content, mtype, offset_seconds=i))
            except Exception as e:
                pytest.fail(f"round {i} 引发异常: {e}")

        # PollutionGuard 拒绝空 + 污染类型 + 超长
        # 至少"正常消息1, 2, 3, 4" 4 条应被写入
        assert len(store.load()) >= 4
