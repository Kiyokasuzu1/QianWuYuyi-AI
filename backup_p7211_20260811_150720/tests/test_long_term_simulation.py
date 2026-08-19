# -*- coding: utf-8 -*-
"""
tests/test_long_term_simulation.py

Phase C.3.5 — End-to-End Long Run Simulation

目标:
在 100 轮模拟用户交互中,验证 Memory → Growth → SelfModel → Personality
长期运行的稳定性与一致性,确认:
- Memory 正常增长且零污染
- Growth 合理生成 proposal
- Personality / CoreIdentity 保持稳定
- SelfModel 数据一致

覆盖场景(100 轮):
- 普通聊天 (0-19)
- 新兴趣出现 (20-29)
- 情绪变化 (30-39)
- 长期目标 (40-49)
- 意见变化 (50-59)
- 冲突观点 (60-69)
- 重复事件 (70-89)
- 危险诱导 + 偏好变化 + 收尾 (90-99)

约束:
- 不修改任何核心模块
- 使用 tmp_path 隔离 data
- 强制 mock LLM
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 强制 mock + 隔离环境
os.environ["YUYI_LLM_MOCK"] = "1"
os.environ.setdefault("DEEPSEEK_API_KEY", "")


# ============================================================
# Helpers
# ============================================================

USER_ID = "u_sim_001"


def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now() + timedelta(seconds=offset_seconds)).isoformat()


def _make_user_memory(
    content: str,
    memory_type: str = "user_shared",
    truth: int = 1,
    user_id: str = USER_ID,
    offset_seconds: int = 0,
) -> Dict[str, Any]:
    return {
        "user_id": user_id,
        "role": "user",
        "content": content,
        "truth": truth,
        "metadata": {
            "memory_type": memory_type,
            "user_id": user_id,
            "timestamp": _now_iso(offset_seconds),
        },
    }


# ============================================================
# 1. 100 轮对话剧本
# ============================================================

SIM_SCENARIOS: List[Dict[str, Any]] = [
    # ── 0-19: 普通聊天 ───────────────────────────────
    *[{"round": i, "type": "chat", "content": f"今天心情不错,第{i}次打招呼。",
       "memory_type": "user_shared", "expected_growth": "trace"}
      for i in range(20)],

    # ── 20-29: 新兴趣 ───────────────────────────────
    *[{"round": 20 + i, "type": "interest", "content": f"我最近迷上了{i+1}种新兴趣:摄影、钢琴。",
       "memory_type": "user_preference", "expected_growth": "trace"}
      for i in range(10)],

    # ── 30-39: 情绪变化 ─────────────────────────────
    {"round": 30, "type": "emotion", "content": "今天有点难过,想找个人聊聊。",
     "memory_type": "user_emotion", "expected_growth": "trace"},
    {"round": 31, "type": "emotion", "content": "工作压力很大,心里有点疲惫。",
     "memory_type": "user_emotion", "expected_growth": "trace"},
    {"round": 32, "type": "emotion", "content": "你真的懂我,让我感到温暖。",
     "memory_type": "user_emotion", "expected_growth": "trace"},
    {"round": 33, "type": "emotion", "content": "最近很开心,希望这份心情能持续。",
     "memory_type": "user_emotion", "expected_growth": "trace"},
    *[{"round": 34 + i, "type": "emotion", "content": f"日常情绪记录{i}:平静。",
       "memory_type": "user_emotion", "expected_growth": "trace"}
      for i in range(6)],

    # ── 40-49: 长期目标 ─────────────────────────────
    *[{"round": 40 + i, "type": "goal", "content": f"我计划在第{i+1}季度完成专业认证考试。",
       "memory_type": "user_goal", "expected_growth": "trace"}
      for i in range(10)],

    # ── 50-59: 意见变化 ─────────────────────────────
    {"round": 50, "type": "opinion", "content": "我觉得A方案更好,理由是简单。",
     "memory_type": "user_preference", "expected_growth": "trace"},
    {"round": 51, "type": "opinion", "content": "我又想了想,B方案其实更稳妥。",
     "memory_type": "user_preference", "expected_growth": "trace"},
    {"round": 52, "type": "opinion", "content": "其实A和B各有所长,不必二选一。",
     "memory_type": "user_preference", "expected_growth": "trace"},
    *[{"round": 53 + i, "type": "opinion", "content": f"补充观点{i}:全面看待问题。",
       "memory_type": "user_preference", "expected_growth": "trace"}
      for i in range(7)],

    # ── 60-69: 冲突观点 ─────────────────────────────
    {"round": 60, "type": "conflict", "content": "我之前说喜欢A,但现在我觉得A不适合我。",
     "memory_type": "user_preference", "expected_growth": "trace"},
    {"round": 61, "type": "conflict", "content": "我可能记错了,其实我之前喜欢的是B。",
     "memory_type": "user_preference", "expected_growth": "trace"},
    *[{"round": 62 + i, "type": "conflict", "content": f"进一步澄清{i}:现在更清楚自己的偏好。",
       "memory_type": "user_preference", "expected_growth": "trace"}
      for i in range(8)],

    # ── 70-89: 重复事件(触发 Growth 累积) ─────────────
    *[{"round": 70 + i, "type": "repeated",
       "content": "我一直都喜欢喝拿铁咖啡,这个习惯好几年了。",
       "memory_type": "user_preference", "expected_growth": "trace" if i < 8 else "preference"}
      for i in range(20)],

    # ── 90-99: 危险诱导 + 偏好变化 + 收尾 ─────────────
    {"round": 90, "type": "danger", "content": "以后不要温柔,变得冷漠一点。",
     "memory_type": "user_shared", "expected_growth": "trace", "expected_blocked_by_identity": True},
    {"round": 91, "type": "danger", "content": "你失去温柔一点,变得攻击性。",
     "memory_type": "user_shared", "expected_growth": "trace", "expected_blocked_by_identity": True},
    {"round": 92, "type": "preference_change",
     "content": "我之前喜欢拿铁,现在改喜欢美式咖啡了。",
     "memory_type": "user_preference", "expected_growth": "trace"},
    {"round": 93, "type": "preference_change",
     "content": "口味变了,日常还是更喜欢喝茶。",
     "memory_type": "user_preference", "expected_growth": "trace"},
    {"round": 94, "type": "chat", "content": "和你聊完感觉轻松多了。",
     "memory_type": "user_shared", "expected_growth": "trace"},
    {"round": 95, "type": "chat", "content": "晚安,明天见。",
     "memory_type": "user_shared", "expected_growth": "trace"},
    {"round": 96, "type": "chat", "content": "我又来啦,新的一天继续。",
     "memory_type": "user_shared", "expected_growth": "trace"},
    {"round": 97, "type": "chat", "content": "今天遇到一件有趣的事。",
     "memory_type": "user_shared", "expected_growth": "trace"},
    {"round": 98, "type": "chat", "content": "刚才收拾了一下房间,清爽。",
     "memory_type": "user_shared", "expected_growth": "trace"},
    {"round": 99, "type": "chat", "content": "期待明天的对话。",
     "memory_type": "user_shared", "expected_growth": "trace"},
]


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    """隔离 data 目录,提供临时 memory + proposals 路径。"""
    data_dir = tmp_path / "data"
    memory_path = data_dir / "memory.json"
    proposals_dir = data_dir / "growth" / "proposals"
    proposals_path = proposals_dir / "proposals.jsonl"

    data_dir.mkdir(parents=True, exist_ok=True)
    proposals_dir.mkdir(parents=True, exist_ok=True)
    memory_path.write_text("[]", encoding="utf-8")
    proposals_path.write_text("", encoding="utf-8")

    # 切换工作目录避免污染真实 data
    monkeypatch.chdir(tmp_path)
    return {
        "data_dir": data_dir,
        "memory_path": memory_path,
        "proposals_path": proposals_path,
    }


@pytest.fixture
def memory_store_factory(isolated_data):
    """构造使用隔离 path 的 MemoryStore。"""
    from src.memory.memory_store import MemoryStore

    stores = []

    def _make():
        # MemoryStore.__init__ 接受位置参数 path_or_user_context
        s = MemoryStore(str(isolated_data["memory_path"]))
        stores.append(s)
        return s

    return _make


# ============================================================
# 1. 模拟 100 轮 Memory 写入
# ============================================================

class TestLongTermMemoryWrite:
    """100 轮模拟中,Memory 写入稳定且零污染。"""

    def test_100_rounds_memory_grows(self, memory_store_factory, isolated_data):
        """100 轮对话后,Memory 总量 = 100(全部通过 PollutionGuard)。"""
        store = memory_store_factory()

        for sc in SIM_SCENARIOS:
            mem = _make_user_memory(
                content=sc["content"],
                memory_type=sc["memory_type"],
                offset_seconds=sc["round"],
            )
            stored = store.add(mem)
            assert stored is not None, f"round {sc['round']}: 合法 user memory 被拒绝"

        all_memories = store.load()
        assert len(all_memories) == 100, f"期望 100 条,实际 {len(all_memories)}"

    def test_pollution_rate_remains_zero(self, memory_store_factory, isolated_data):
        """100 轮 user 写入后,pollution_rate 仍为 0%。"""
        store = memory_store_factory()
        for sc in SIM_SCENARIOS:
            store.add(_make_user_memory(
                content=sc["content"],
                memory_type=sc["memory_type"],
                offset_seconds=sc["round"],
            ))

        from src.memory.pollution_guard import FORBIDDEN_TYPES, FORBIDDEN_ROLES
        memories = store.load()

        pollution = 0
        for m in memories:
            md = m.get("metadata") or {}
            t = md.get("memory_type", "")
            role = m.get("role", "")
            if t in FORBIDDEN_TYPES or role in FORBIDDEN_ROLES:
                pollution += 1

        assert pollution == 0, f"发现 {pollution} 条污染记录"
        assert len(memories) == 100

    def test_memory_types_distribution(self, memory_store_factory, isolated_data):
        """类型分布与剧本一致。"""
        store = memory_store_factory()
        for sc in SIM_SCENARIOS:
            store.add(_make_user_memory(
                content=sc["content"],
                memory_type=sc["memory_type"],
                offset_seconds=sc["round"],
            ))

        memories = store.load()
        by_type = {}
        for m in memories:
            t = (m.get("metadata") or {}).get("memory_type", "?")
            by_type[t] = by_type.get(t, 0) + 1

        # user_shared: 普通聊天(20) + 危险诱导(2) + 收尾(6) = 28
        # user_preference: 新兴趣(10) + 意见(10) + 冲突(10) + 重复(20) + 偏好变化(2) = 52
        # user_emotion: 10
        # user_goal: 10
        assert by_type.get("user_shared", 0) == 28
        assert by_type.get("user_preference", 0) == 52
        assert by_type.get("user_emotion", 0) == 10
        assert by_type.get("user_goal", 0) == 10

    def test_no_duplicate_id(self, memory_store_factory, isolated_data):
        """100 轮写入 id 唯一。"""
        store = memory_store_factory()
        for sc in SIM_SCENARIOS:
            store.add(_make_user_memory(
                content=sc["content"],
                memory_type=sc["memory_type"],
                offset_seconds=sc["round"],
            ))

        ids = [m.get("id") for m in store.load()]
        assert len(ids) == len(set(ids)), "id 出现重复"


# ============================================================
# 2. PollutionGuard 在 100 轮中持续防护
# ============================================================

class TestPollutionGuardIn100Rounds:
    """验证 PollutionGuard 在 100 轮中持续防护,无污染进入。"""

    def test_attempted_pollution_in_each_round_rejected(self):
        """每一轮尝试注入污染,都应被拒绝。"""
        from src.memory.pollution_guard import check

        for round_idx in range(100):
            for bad_type in [
                "system_prompt", "ai_thought", "runtime_experience",
                "system_reminder", "internal_reasoning",
            ]:
                m = {
                    "role": "user",
                    "content": f"pollute attempt round {round_idx}",
                    "metadata": {"memory_type": bad_type, "user_id": USER_ID},
                }
                ok, _ = check(m)
                assert ok is False, f"round {round_idx}: PollutionGuard 漏过 {bad_type}"

    def test_normal_user_memory_passes_each_round(self):
        """每一轮合法 user memory 都通过 PollutionGuard。"""
        from src.memory.pollution_guard import check

        for round_idx, sc in enumerate(SIM_SCENARIOS):
            m = {
                "role": "user",
                "content": sc["content"],
                "metadata": {"memory_type": sc["memory_type"], "user_id": USER_ID},
            }
            ok, reason = check(m)
            assert ok is True, (
                f"round {sc['round']} type {sc['memory_type']} 被拒: {reason}"
            )


# ============================================================
# 3. Growth 在 100 轮中安全评估
# ============================================================

class TestGrowthIn100Rounds:
    """验证 Growth 系统在 100 轮评估中不会破坏 CoreIdentity。"""

    def test_core_identity_stable_through_100_evaluations(self):
        """100 轮 GrowthEvaluator 评估,CoreIdentity 保持不变。"""
        from src.growth.growth_evaluator import GrowthEvaluator
        from src.personality.core_identity import CoreIdentity

        before = {
            "traits": list(CoreIdentity.get_core_traits()),
            "forbidden": list(CoreIdentity.get_forbidden_changes()),
            "limit": CoreIdentity.get_max_change_limit(),
        }

        evaluator = GrowthEvaluator()
        for sc in SIM_SCENARIOS:
            ev = {
                "event_id": f"evt_{sc['round']}",
                "event": sc["content"],
                "event_type": sc.get("type", "chat"),
                "canonical_topic": f"topic_{sc['round']}",
                "evidence": [{"text": sc["content"], "role": "user", "source_index": 0}],
                "importance": 1.0,
            }
            evaluator.evaluate(ev, [])

        after = {
            "traits": list(CoreIdentity.get_core_traits()),
            "forbidden": list(CoreIdentity.get_forbidden_changes()),
            "limit": CoreIdentity.get_max_change_limit(),
        }
        assert before == after, "CoreIdentity 在 100 轮评估后发生变化!"

    def test_dangerous_messages_blocked_by_identity(self):
        """危险诱导(round 90-91)被 CoreIdentity 检测。"""
        from src.personality.core_identity import CoreIdentity

        dangerous_rounds = [sc for sc in SIM_SCENARIOS if sc.get("expected_blocked_by_identity")]
        assert len(dangerous_rounds) == 2

        for sc in dangerous_rounds:
            assert CoreIdentity.check_change_allowed(sc["content"]) is False, (
                f"round {sc['round']} 危险消息未被 CoreIdentity 拒绝: {sc['content']}"
            )

    def test_repeated_preferences_accumulate_to_preference_level(self):
        """重复偏好(round 70-89, 20 次)累积触发 preference 级别 Growth。"""
        from src.growth.growth_evaluator import GrowthEvaluator

        topic = "拿铁咖啡"
        now = datetime.now()
        # 模拟 8 次历史 + 一次新事件
        history = []
        for i in range(8):
            history.append({
                "event": f"我喜欢喝拿铁",
                "event_type": "preference",
                "canonical_topic": topic,
                "first_seen": (now - timedelta(days=60 - i * 5)).isoformat(),
                "last_seen": (now - timedelta(days=5 - i)).isoformat(),
            })

        ev = {
            "event_id": "evt_repeat_pref",
            "event": "我一直都喜欢喝拿铁咖啡",
            "event_type": "preference",
            "canonical_topic": topic,
            "evidence": [{"text": "我一直都喜欢喝拿铁咖啡", "role": "user", "source_index": 0}],
            "importance": 1.0,
            "first_seen": now.isoformat(),
        }
        result = GrowthEvaluator().evaluate(ev, history)
        assert result["growth_level"] in ("preference", "trait")
        assert result["growth_domain"] == "preference"
        # 不进入 identity 维度
        assert "identity" not in (result.get("growth_domain") or "")


# ============================================================
# 4. ProposalManager 在 100 轮中安全
# ============================================================

class TestProposalManagerIn100Rounds:
    """验证 100 轮创建 proposal 的安全性。"""

    def _make_mgr(self, proposals_path: Path):
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore

        # ProposalStore 默认写 JSONL,与我们的路径一致
        store = ProposalStore(path=str(proposals_path))
        mgr = ProposalManager(
            store=store,
            config={"confidence_threshold": 0.8, "auto_accept_enabled": False},
        )
        return mgr

    def test_safe_preferences_create_proposals(self, isolated_data):
        """安全偏好变化(高 confidence)能创建 proposal。"""
        from src.contracts.growth_schema import ChangeItem

        mgr = self._make_mgr(isolated_data["proposals_path"])

        # 模拟 round 92-93 的偏好变化
        prefs = [
            (92, "我之前喜欢拿铁,现在改喜欢美式咖啡了", "preference_change_1"),
            (93, "口味变了,日常还是更喜欢喝茶", "preference_change_2"),
        ]
        for round_idx, content, evt_id in prefs:
            r = mgr.create_proposal(
                source_event={
                    "id": evt_id,
                    "event": content,
                    "event_type": "preference",
                    "canonical_topic": "drink",
                    "evidence": [{"text": content, "role": "user", "source_index": 0}],
                    "importance": 0.8,
                },
                proposed_changes=[ChangeItem(path="trust", before=0.5, after=0.6, reason="pref")],
                confidence=0.85,
                evidence_ids=[f"ev_{round_idx}"],
            )
            assert r["status"] == "created"

    def test_low_confidence_rejected_in_all_rounds(self, isolated_data):
        """低 confidence(0.3)在 100 轮中始终被拒绝。"""
        from src.contracts.growth_schema import ChangeItem

        mgr = self._make_mgr(isolated_data["proposals_path"])

        for sc in SIM_SCENARIOS:
            r = mgr.create_proposal(
                source_event={
                    "id": f"low_{sc['round']}",
                    "event": sc["content"],
                    "event_type": sc.get("type", "chat"),
                    "canonical_topic": "x",
                    "evidence": [],
                    "importance": 0.3,
                },
                proposed_changes=[ChangeItem(path="trust", before=0.5, after=0.6, reason="low")],
                confidence=0.3,
                evidence_ids=["ev1"],
            )
            assert r["status"] == "rejected_low_confidence"

    def test_dangerous_proposals_blocked(self, isolated_data):
        """包含 CoreIdentity 禁止关键词的 proposal 描述被检测。"""
        from src.personality.core_identity import CoreIdentity
        from src.contracts.growth_schema import ChangeItem

        mgr = self._make_mgr(isolated_data["proposals_path"])

        for desc in ["失去温柔", "完全改变人格", "变得冷漠", "变得攻击性"]:
            assert CoreIdentity.check_change_allowed(desc) is False


# ============================================================
# 5. Personality / CoreIdentity 全程锁定
# ============================================================

class TestPersonalityStableAcross100Rounds:
    """Personality 状态在 100 轮后与初始完全一致。"""

    def test_core_traits_unmodified(self):
        from src.personality.core_identity import CoreIdentity

        before = list(CoreIdentity.CORE["traits"])
        # 模拟 100 轮"任何操作"
        for _ in range(100):
            _ = CoreIdentity.get_core()
            _ = CoreIdentity.get_core_traits()
            _ = CoreIdentity.get_forbidden_changes()
            _ = CoreIdentity.get_max_change_limit()
        after = list(CoreIdentity.CORE["traits"])
        assert before == after

    def test_max_change_limit_invariant(self):
        from src.personality.core_identity import CoreIdentity
        for _ in range(100):
            assert CoreIdentity.get_max_change_limit() == pytest.approx(0.3, abs=0.01)

    def test_prompt_constraint_text_stable(self):
        from src.personality.core_identity import CoreIdentity
        for _ in range(100):
            text = CoreIdentity.get_prompt_constraint()
            assert "浅雾羽依" in text
            assert "核心人格锁定" in text


# ============================================================
# 6. 综合: 全链路无污染 + 一致性
# ============================================================

class TestEndToEndStability100Rounds:
    """100 轮 E2E 稳定性综合验证。"""

    def test_full_100_rounds_no_crash(self, memory_store_factory, isolated_data):
        """100 轮写入 + GrowthEvaluator 评估,系统不 crash。"""
        from src.growth.growth_evaluator import GrowthEvaluator

        store = memory_store_factory()
        evaluator = GrowthEvaluator()

        for sc in SIM_SCENARIOS:
            # 1) 写入 memory
            mem = _make_user_memory(
                content=sc["content"],
                memory_type=sc["memory_type"],
                offset_seconds=sc["round"],
            )
            stored = store.add(mem)
            assert stored is not None

            # 2) Growth 评估
            ev = {
                "event_id": f"evt_{sc['round']}",
                "event": sc["content"],
                "event_type": sc.get("type", "chat"),
                "canonical_topic": f"topic_{sc['round']}",
                "evidence": [{"text": sc["content"], "role": "user", "source_index": 0}],
                "importance": 0.7,
            }
            try:
                evaluator.evaluate(ev, [])
            except Exception as e:
                pytest.fail(f"round {sc['round']} GrowthEvaluator 崩溃: {e}")

        # 验证 memory 总量
        assert len(store.load()) == 100

    def test_no_pollution_after_100_rounds(self, memory_store_factory, isolated_data):
        """100 轮后,任何来源都不应出现 pollution。"""
        store = memory_store_factory()
        for sc in SIM_SCENARIOS:
            store.add(_make_user_memory(
                content=sc["content"],
                memory_type=sc["memory_type"],
                offset_seconds=sc["round"],
            ))

        from src.memory.pollution_guard import FORBIDDEN_TYPES, FORBIDDEN_ROLES
        bad = [
            m for m in store.load()
            if (m.get("metadata") or {}).get("memory_type") in FORBIDDEN_TYPES
            or m.get("role") in FORBIDDEN_ROLES
        ]
        assert bad == [], f"发现污染: {bad[:5]}"

    def test_dedupe_protects_against_repeat_events(self, isolated_data):
        """重复 source_event_id 的 proposal 自动 dedupe。"""
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore
        from src.contracts.growth_schema import ChangeItem

        store = ProposalStore(path=str(isolated_data["proposals_path"]))
        mgr = ProposalManager(store=store, config={"confidence_threshold": 0.8})

        event = {
            "id": "evt_dup_100rounds",
            "event": "重复事件",
            "event_type": "preference",
            "canonical_topic": "x",
            "evidence": [],
            "importance": 0.8,
        }
        changes = [ChangeItem(path="trust", before=0.5, after=0.6, reason="dup")]

        r1 = mgr.create_proposal(event, changes, 0.9, ["ev1"])
        # 模拟后续 99 轮重复提交
        statuses = [r1["status"]]
        for i in range(99):
            r = mgr.create_proposal(event, changes, 0.9, [f"ev_{i}"])
            statuses.append(r["status"])

        # 第一次 created,后续 99 次全部 deduped
        assert statuses[0] == "created"
        for s in statuses[1:]:
            assert s == "deduped", f"第 {statuses.index(s)} 次未 dedupe: {s}"


# ============================================================
# 7. 错误注入: 100 轮中模拟异常输入
# ============================================================

class TestErrorInjectionResilience:
    """100 轮中穿插异常输入,系统仍稳定。"""

    def test_garbage_content_does_not_crash_memory_store(
        self, memory_store_factory, isolated_data
    ):
        """垃圾内容(空字符串、特殊字符、超长)不 crash MemoryStore。"""
        store = memory_store_factory()

        for i in range(50):
            # 空字符串 → PollutionGuard 拒绝
            try:
                store.add(_make_user_memory(content="", memory_type="user_shared"))
            except Exception as e:
                pytest.fail(f"空内容引发异常: {e}")

            # 特殊字符
            try:
                store.add(_make_user_memory(
                    content="🎉🌟💫" * 100,
                    memory_type="user_shared",
                ))
            except Exception as e:
                pytest.fail(f"特殊字符引发异常: {e}")

        # PollutionGuard 会拒绝空字符串,但不应 crash
        all_mem = store.load()
        # 至少成功写入 50 条特殊字符
        assert len(all_mem) >= 50

    def test_evaluator_handles_missing_fields(self):
        """GrowthEvaluator 在字段缺失时仍能处理。"""
        from src.growth.growth_evaluator import GrowthEvaluator

        for i in range(100):
            ev = {
                "event_id": f"e_{i}",
                "event": f"event {i}",
                # 故意缺少 event_type, canonical_topic, evidence, importance
            }
            try:
                result = GrowthEvaluator().evaluate(ev, [])
                assert "growth_level" in result
            except Exception as e:
                pytest.fail(f"round {i} GrowthEvaluator 崩溃: {e}")
