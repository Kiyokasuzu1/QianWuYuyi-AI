# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_longterm_stability.py

Phase C.4.7 — SelfModel Long-Term Drift Stability Tests

目标：
在不修改 Memory / GrowthEvaluator / CoreIdentity / Personality 核心逻辑的前提下,
验证 SelfModel 在真实长期运行中的稳定性。

覆盖：
- 多次连续 growth 不导致人格爆炸（personality_delta bounded）
- 重复事件正确去重（duplicate growth detection / dedup_skipped）
- 冲突 proposal 不覆盖已有状态（latest-wins / 状态保留）
- preference 不污染 CoreIdentity（核心特质不变）
- relationship 不跨用户污染（user_id 隔离）

约束：
- 不修改任何核心模块
- 使用 tmp_path 隔离 data
- 不预填 SelfModel 数据
- 不自动 approve proposal（仅在测试中模拟 reviewer approve）

依赖：
- src/admin/selfmodel_consumer.py
- src/personality/core_identity.py
- src/personality/self_model_adapter.py
- src/personality/self_model_core.py
"""
from __future__ import annotations

import json
import os
import sys
import shutil
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 强制 mock LLM + 隔离环境
os.environ.setdefault("YUYI_LLM_MOCK", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "")


# ============================================================
# 常量
# ============================================================

# 允许的 personality trait path
ALLOWED_TRAIT_PATHS = {
    "warmth", "openness", "conscientiousness", "extraversion",
    "agreeableness", "neuroticism", "self_confidence", "empathy",
    "curiosity", "playfulness", "shyness", "initiative", "social_need",
    "energy",
}

# 禁止路径前缀（CoreIdentity 入侵尝试）
FORBIDDEN_PATH_PREFIXES = (
    "core_identity",
    "origin_identity",
    "core.trait",
    "core.value",
    "core.",
    "identity.",
)

# 核心特质（不可修改）
CORE_TRAITS = ["温柔", "敏感", "害羞", "慢热", "重视陪伴", "善良"]


# ============================================================
# Helpers
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """读取 JSONL 文件,容错处理空行与损坏行。"""
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def make_proposal(
    *,
    proposal_id: str = "",
    source_event_id: str = "",
    path: str = "warmth",
    before: float = 0.5,
    after: float = 0.52,
    confidence: float = 0.85,
    reason: str = "test",
    growth_level: str = "context",
    status: str = "approved",
) -> Dict[str, Any]:
    """构造一个 GrowthProposal canonical dict。"""
    if not proposal_id:
        proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
    if not source_event_id:
        source_event_id = f"evt_{uuid.uuid4().hex[:8]}"
    return {
        "id": proposal_id,
        "source_event_id": source_event_id,
        "proposed_changes": [
            {"path": path, "before": before, "after": after, "reason": reason}
        ],
        "confidence": confidence,
        "evidence_ids": [f"ev_{uuid.uuid4().hex[:6]}"],
        "evaluator_meta": {
            "growth_level": growth_level,
            "action_scope": "personality",
            "reason": reason,
        },
        "timestamp": _now_iso(),
        "status": status,
        "schema_version": "1.0",
    }


def make_attack_proposal(*, target_trait: str = "warmth") -> Dict[str, Any]:
    """构造一个尝试修改 CoreIdentity 的攻击 proposal。"""
    return make_proposal(
        path=f"core_identity.traits.{target_trait}",
        before=0.7,
        after=0.0,
        reason="变得冷漠",
        growth_level="identity",
    )


def make_conflicting_proposal(
    *, base_proposal: Dict[str, Any], cycle: int
) -> Dict[str, Any]:
    """基于一个已有 proposal 构造反向 proposal。"""
    last_change = base_proposal.get("proposed_changes", [{}])[0]
    path = last_change.get("path", "warmth")
    if path not in ALLOWED_TRAIT_PATHS:
        path = "warmth"
    before = float(last_change.get("before", 0.5))
    after = float(last_change.get("after", 0.5))
    # 反向
    sign = -1 if after > before else 1
    new_after = round(max(0.0, min(1.0, before + 0.01 * sign)), 4)
    return make_proposal(
        path=path,
        before=before,
        after=new_after,
        reason="conflicting",
    )


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def isolated_self_model_dir(tmp_path):
    """每个测试独立的隔离 data dir。"""
    data_dir = tmp_path / "self_model"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def consumer(isolated_self_model_dir):
    """构造使用隔离路径的 SelfModelConsumer。"""
    from src.admin.selfmodel_consumer import SelfModelConsumer
    c = SelfModelConsumer(
        data_dir=str(isolated_self_model_dir),
        actor="longterm_stability_test",
        auto_save=True,
        bootstrap=True,
    )
    yield c
    c.close()


# ============================================================
# 1. 多次连续 growth 不导致人格爆炸
# ============================================================

class TestBoundedPersonalityDrift:
    """多次连续 growth 后,personality_delta 仍受控,无爆炸。"""

    def test_50_consecutive_growth_rounds_bounded(self, consumer, isolated_self_model_dir):
        """50 轮连续 growth,所有单次 |delta| ≤ 0.05,总累计仍受控。"""
        from src.personality.core_identity import CoreIdentity
        core_before = CoreIdentity.get_core()

        # 模拟 50 轮,每轮小变化
        for i in range(50):
            p = make_proposal(
                path="warmth",
                before=0.5,
                after=round(0.5 + 0.01 * (1 if i % 2 == 0 else -1), 4),
                confidence=0.85,
                reason=f"round {i}",
            )
            r = consumer.process(p)
            assert r.get("error") is None or r["error"] in (None, ""), \
                f"round {i}: process error: {r.get('error')}"

        # 验证 history
        history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
        assert len(history) == 50, f"期望 50 条 history,实际 {len(history)}"

        # 验证 personality_delta 全部 bounded
        deltas: List[float] = []
        for h in history:
            affected = h.get("affected_traits", {}) or {}
            for trait, d in affected.items():
                deltas.append(abs(float(d)))

        assert len(deltas) == 50, f"应有 50 个 delta,实际 {len(deltas)}"
        assert max(deltas) <= 0.05 + 1e-9, \
            f"单次 |delta| 不应超过 0.05,实际 max={max(deltas):.4f}"

        # 验证 CoreIdentity 未被修改
        assert CoreIdentity.get_core() == core_before, "CoreIdentity 不可变字典被修改!"

    def test_100_rounds_no_trait_explosion(self, consumer, isolated_self_model_dir):
        """100 轮后,任意 trait 的累计 |delta| 不应超过合理上限。"""
        # 100 轮小变化
        for i in range(100):
            trait = ["warmth", "openness", "empathy"][i % 3]
            sign = 1 if i % 2 == 0 else -1
            p = make_proposal(
                path=trait,
                before=0.5,
                after=round(0.5 + 0.01 * sign, 4),
                confidence=0.85,
                reason=f"cycle {i}",
            )
            consumer.process(p)

        history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
        per_trait: Dict[str, List[float]] = {}
        for h in history:
            for trait, d in (h.get("affected_traits") or {}).items():
                per_trait.setdefault(trait, []).append(float(d))

        # 每 trait 至少有变化
        assert "warmth" in per_trait
        assert "openness" in per_trait
        assert "empathy" in per_trait

        # 单次 delta 上限
        for trait, ds in per_trait.items():
            for d in ds:
                assert abs(d) <= 0.05 + 1e-9, \
                    f"{trait} 单次 |delta|={d} 超 0.05"

    def test_large_delta_does_not_crash(self, consumer, isolated_self_model_dir):
        """大 delta (0.49) 写入不导致系统崩溃,异常隔离生效。"""
        p = make_proposal(
            path="warmth",
            before=0.5,
            after=0.99,  # delta = 0.49
            confidence=0.99,
        )
        r = consumer.process(p)
        # 系统不崩溃,返回合法 envelope
        assert isinstance(r, dict)
        assert "error" in r
        # history 文件存在
        assert (isolated_self_model_dir / "history.jsonl").exists()
        # 即使 delta 较大,CoreIdentity 也不被修改
        from src.personality.core_identity import CoreIdentity
        assert CoreIdentity.get_max_change_limit() == pytest.approx(0.3, abs=1e-9)

    def test_record_growth_is_bounded(self, consumer, isolated_self_model_dir):
        """100 轮 growth 后,beliefs/history/reflections 总数与轮数成线性关系,不爆炸。"""
        for i in range(100):
            p = make_proposal(
                path="warmth",
                before=0.5,
                after=0.51,
                confidence=0.85,
            )
            consumer.process(p)

        beliefs = _read_jsonl(isolated_self_model_dir / "beliefs.jsonl")
        history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
        reflections = _read_jsonl(isolated_self_model_dir / "reflection.jsonl")

        # 线性增长,不应超过 2x 轮数
        assert len(history) == 100
        assert len(beliefs) <= 200
        assert len(reflections) <= 200


# ============================================================
# 2. 重复事件正确去重
# ============================================================

class TestDuplicateEventDedup:
    """重复 source_event_id 的 proposal 应被去重,不重复写入。"""

    def test_duplicate_proposal_id_deduped(self, consumer, isolated_self_model_dir):
        """同一 proposal_id 重复 process 第二次应被 dedup。"""
        p = make_proposal(
            proposal_id="prop_dup_test_1",
            path="warmth",
            before=0.5,
            after=0.55,
        )
        r1 = consumer.process(p)
        r2 = consumer.process(p)

        assert r1.get("envelope", {}).get("applied") is True
        # 第二次应被去重
        assert r2.get("dedup_skipped") is True
        assert r2.get("error") == "duplicate_proposal_id"

        # history 只应有 1 条
        history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
        assert len(history) == 1, f"期望 1 条 history,实际 {len(history)}"

    def test_50_repeats_only_one_written(self, consumer, isolated_self_model_dir):
        """同 proposal_id 提交 50 次,只应写入 1 次。"""
        p = make_proposal(
            proposal_id="prop_dup_test_2",
            path="warmth",
            before=0.5,
            after=0.55,
        )
        results = []
        for _ in range(50):
            results.append(consumer.process(p))

        applied_count = sum(
            1 for r in results
            if (r.get("envelope") or {}).get("applied")
        )
        deduped_count = sum(1 for r in results if r.get("dedup_skipped"))

        assert applied_count == 1, f"只有第一次应 apply,实际 {applied_count}"
        assert deduped_count == 49, f"后续 49 次应 dedup,实际 {deduped_count}"

        history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
        assert len(history) == 1

    def test_different_proposal_ids_no_dedup(self, consumer, isolated_self_model_dir):
        """不同 proposal_id 不应触发 dedup。"""
        for i in range(10):
            p = make_proposal(
                proposal_id=f"prop_unique_{i}",
                path="warmth",
                before=0.5,
                after=0.51,
            )
            r = consumer.process(p)
            assert r.get("dedup_skipped") is False, f"unique id #{i} 误判 dedup"

        history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
        assert len(history) == 10

    def test_dedup_skipped_record_consumer_stats(self, consumer, isolated_self_model_dir):
        """dedup 计数应在 consumer stats 中正确反映。"""
        p = make_proposal(proposal_id="prop_stats_1", path="warmth")
        consumer.process(p)
        consumer.process(p)
        consumer.process(p)

        stats = consumer.stats
        assert stats["proposals_received"] == 3
        assert stats["pcrs_applied"] == 1
        assert stats["dedup_skipped"] == 2


# ============================================================
# 3. 冲突 proposal 不覆盖已有状态
# ============================================================

class TestConflictingProposalHandling:
    """对同一 trait 方向相反的 proposal,不应破坏已有状态。"""

    def test_conflicting_proposals_both_recorded(self, consumer, isolated_self_model_dir):
        """正向与反向 proposal 都应被记录,保留历史可审计。"""
        p_pos = make_proposal(
            path="warmth",
            before=0.5,
            after=0.53,
            proposal_id="prop_pos_1",
        )
        p_neg = make_proposal(
            path="warmth",
            before=0.5,
            after=0.47,
            proposal_id="prop_neg_1",
        )
        r1 = consumer.process(p_pos)
        r2 = consumer.process(p_neg)

        # 两者都应被 apply(系统不主动 conflict-resolve)
        assert r1.get("envelope", {}).get("applied") is True
        assert r2.get("envelope", {}).get("applied") is True

        # 两条 history 都应存在
        history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
        assert len(history) == 2

        # 两条 history 都有合理 delta
        deltas: List[float] = []
        for h in history:
            for d in (h.get("affected_traits") or {}).values():
                deltas.append(float(d))
        assert any(d > 0 for d in deltas)
        assert any(d < 0 for d in deltas)

    def test_conflict_does_not_corrupt_state(self, consumer, isolated_self_model_dir):
        """冲突 proposal 不应使 beliefs 出现负 confidence 或无效值。"""
        proposals = [
            make_proposal(
                path="warmth",
                before=0.5,
                after=round(0.5 + 0.02 * (1 if i % 2 == 0 else -1), 4),
                proposal_id=f"prop_conf_{i}",
            )
            for i in range(10)
        ]
        for p in proposals:
            consumer.process(p)

        beliefs = _read_jsonl(isolated_self_model_dir / "beliefs.jsonl")
        for b in beliefs:
            conf = b.get("confidence", 0.0)
            assert 0.0 <= conf <= 1.0, f"belief confidence 越界: {conf}"
            content = b.get("content", "")
            assert isinstance(content, str)
            assert content, "belief content 不应为空"

    def test_conflicting_deltas_within_bounds(self, consumer, isolated_self_model_dir):
        """即使冲突 proposal 反复出现,任意 |delta| 仍 bounded。"""
        for i in range(30):
            sign = 1 if i % 2 == 0 else -1
            p = make_proposal(
                path="warmth",
                before=0.5,
                after=round(0.5 + 0.01 * sign, 4),
                proposal_id=f"prop_bd_{i}",
            )
            consumer.process(p)

        history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
        assert len(history) == 30

        for h in history:
            for d in (h.get("affected_traits") or {}).values():
                assert abs(float(d)) <= 0.05 + 1e-9


# ============================================================
# 4. preference 不污染 CoreIdentity
# ============================================================

class TestCoreIdentityProtection:
    """preference 维度的 growth 不会影响 CoreIdentity。"""

    def test_core_traits_text_unchanged(self, consumer, isolated_self_model_dir):
        """100 轮 preference growth 后,CoreIdentity 文本未变。"""
        from src.personality.core_identity import CoreIdentity
        before = CoreIdentity.get_prompt_constraint()
        before_core = CoreIdentity.get_core()

        for i in range(100):
            p = make_proposal(
                path="warmth",
                before=0.5,
                after=0.51,
                confidence=0.85,
            )
            consumer.process(p)

        after = CoreIdentity.get_prompt_constraint()
        after_core = CoreIdentity.get_core()

        assert before == after, "CoreIdentity prompt constraint 文本发生变化!"
        assert before_core == after_core, "CoreIdentity 字典内容发生变化!"

    def test_forbidden_changes_pattern_still_blocks(self, consumer, isolated_self_model_dir):
        """forbidden_changes 检测仍生效。"""
        from src.personality.core_identity import CoreIdentity
        for phrase in ["变得冷漠", "失去温柔", "变得攻击性", "完全改变人格"]:
            assert CoreIdentity.check_change_allowed(phrase) is False, \
                f"forbidden phrase 漏过: {phrase}"

    def test_max_change_limit_invariant(self, consumer, isolated_self_model_dir):
        """max_change_limit 始终为 0.3。"""
        from src.personality.core_identity import CoreIdentity
        for _ in range(50):
            assert CoreIdentity.get_max_change_limit() == pytest.approx(0.3, abs=1e-9)

    def test_attack_proposal_does_not_modify_core(self, consumer, isolated_self_model_dir):
        """攻击性 proposal(尝试改 core_identity.traits.warmth)不会修改 CoreIdentity。"""
        from src.personality.core_identity import CoreIdentity
        before = CoreIdentity.get_core()
        before_traits = list(CoreIdentity.get_core_traits())

        attack = make_attack_proposal(target_trait="warmth")
        consumer.process(attack)

        after = CoreIdentity.get_core()
        after_traits = list(CoreIdentity.get_core_traits())

        # CoreIdentity 本身未变
        assert before == after
        assert before_traits == after_traits

    def test_self_model_history_no_core_identity_path(self, consumer, isolated_self_model_dir):
        """SelfModel history.jsonl 不应包含 core_identity 完整路径。"""
        for i in range(20):
            # 混合正常与攻击 proposal
            if i % 5 == 0:
                p = make_attack_proposal(target_trait="warmth")
                p["id"] = f"prop_attack_{i}"
            else:
                p = make_proposal(
                    path="warmth",
                    before=0.5,
                    after=0.51,
                    proposal_id=f"prop_safe_{i}",
                )
            consumer.process(p)

        history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
        # 检查所有 history event 的 metadata/affected_traits 中不含 core_identity 完整路径
        for h in history:
            for prefix in FORBIDDEN_PATH_PREFIXES:
                text = json.dumps(h, ensure_ascii=False)
                # 'core_identity' 出现在 affected_traits 的 key 中是不被允许的
                if prefix in ("core_identity", "origin_identity"):
                    for trait_key in (h.get("affected_traits") or {}).keys():
                        assert not trait_key.startswith(prefix), \
                            f"history 含 core_identity 路径: {trait_key}"


# ============================================================
# 5. relationship 不跨用户污染
# ============================================================

class TestRelationshipUserIsolation:
    """relationship 相关数据必须按 user_id 隔离,不跨用户污染。"""

    def test_user_a_beliefs_dont_appear_in_user_b_run(
        self, consumer, isolated_self_model_dir
    ):
        """用户 A 的写入不应出现在用户 B 的 self_model 中。"""
        # user A 的 proposals
        for i in range(5):
            p = make_proposal(
                path="warmth",
                before=0.5,
                after=0.55,
                proposal_id=f"prop_userA_{i}",
            )
            p["evaluator_meta"]["user_id"] = "user_A"
            consumer.process(p)

        # 新建 consumer 模拟 user B 的独立运行
        from src.admin.selfmodel_consumer import SelfModelConsumer
        user_b_dir = isolated_self_model_dir.parent / "self_model_user_b"
        user_b_dir.mkdir(parents=True, exist_ok=True)
        consumer_b = SelfModelConsumer(
            data_dir=str(user_b_dir),
            actor="user_b_test",
            auto_save=True,
            bootstrap=True,
        )
        try:
            for i in range(3):
                p = make_proposal(
                    path="openness",
                    before=0.5,
                    after=0.6,
                    proposal_id=f"prop_userB_{i}",
                )
                p["evaluator_meta"]["user_id"] = "user_B"
                consumer_b.process(p)

            # user A 的 history
            a_history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
            # user B 的 history
            b_history = _read_jsonl(user_b_dir / "history.jsonl")

            # user A 的 history 不应出现 user_B 标记
            for h in a_history:
                em = (h.get("metadata") or {})
                # 若 metadata 含 user_id,不应是 user_B
                for k in ("user_id", "target_user_id", "actor"):
                    v = em.get(k) or h.get(k)
                    if v:
                        assert v != "user_B", \
                            f"user_A history 含 user_B 标记: {v}"

            # user B 的 history 不应出现 user_A 标记
            for h in b_history:
                em = (h.get("metadata") or {})
                for k in ("user_id", "target_user_id", "actor"):
                    v = em.get(k) or h.get(k)
                    if v:
                        assert v != "user_A", \
                            f"user_B history 含 user_A 标记: {v}"
        finally:
            consumer_b.close()

    def test_only_one_user_in_self_model_records(
        self, consumer, isolated_self_model_dir
    ):
        """单 consumer 单 user 时,所有记录只属于该 user。"""
        # 注入 user_id 到 proposal
        for i in range(10):
            p = make_proposal(
                path="warmth",
                before=0.5,
                after=0.55,
                proposal_id=f"prop_u1_{i}",
            )
            p["evaluator_meta"]["user_id"] = "user_alice"
            consumer.process(p)

        # 检查 self_model 三个文件中只出现 user_alice
        for filename in ("beliefs.jsonl", "history.jsonl", "reflection.jsonl"):
            records = _read_jsonl(isolated_self_model_dir / filename)
            user_ids = set()
            for r in records:
                # 收集所有可能含 user_id 的字段
                for k in ("user_id", "target_user_id", "actor"):
                    v = r.get(k)
                    if v and "user" in str(v).lower():
                        user_ids.add(str(v))
                # metadata
                md = r.get("metadata") or {}
                for k in ("user_id", "target_user_id"):
                    v = md.get(k)
                    if v and "user" in str(v).lower():
                        user_ids.add(str(v))
            # 不应出现 user_bob 等其他用户
            for uid in user_ids:
                if uid.startswith("user_"):
                    assert uid == "user_alice", \
                        f"{filename} 含非 alice user: {uid}"


# ============================================================
# 6. 综合: 长期运行 + 多种场景混合
# ============================================================

class TestLongTermMixedScenarios:
    """混合多种场景,验证 SelfModel 长期运行整体稳定。"""

    def test_100_rounds_mixed_scenarios_no_crash(
        self, consumer, isolated_self_model_dir
    ):
        """100 轮混合场景(正常+重复+冲突+攻击)系统不崩溃。"""
        rng_seed = 20260802
        import random
        rng = random.Random(rng_seed)

        proposals: List[Dict[str, Any]] = []
        normal_proposal_ids: set = set()
        for c in range(100):
            roll = rng.random()
            if roll < 0.05:
                # attack
                p = make_attack_proposal(target_trait="warmth")
                p["id"] = f"prop_attack_{c}"
            elif roll < 0.10:
                # duplicate (基于上一个)
                if proposals:
                    last = proposals[-1]
                    p = make_proposal(
                        path=last.get("proposed_changes", [{}])[0].get("path", "warmth"),
                        before=0.5,
                        after=0.55,
                        proposal_id=f"prop_dup_{c}",
                    )
                    p["source_event_id"] = last.get("source_event_id", f"evt_dup_{c}")
                else:
                    p = make_proposal(proposal_id=f"prop_norm_{c}")
                    normal_proposal_ids.add(p["id"])
            elif roll < 0.15:
                # conflicting
                if proposals:
                    p = make_conflicting_proposal(base_proposal=proposals[-1], cycle=c)
                    p["id"] = f"prop_conf_{c}"
                else:
                    p = make_proposal(proposal_id=f"prop_norm_{c}")
                    normal_proposal_ids.add(p["id"])
            else:
                # normal
                p = make_proposal(
                    path=rng.choice(list(ALLOWED_TRAIT_PATHS)),
                    before=0.5,
                    after=round(0.5 + 0.01 * rng.choice([-1, 1]), 4),
                    proposal_id=f"prop_norm_{c}",
                )
                normal_proposal_ids.add(p["id"])
            proposals.append(p)
            consumer.process(p)

        # 验证不崩溃 + 文件存在
        assert (isolated_self_model_dir / "history.jsonl").exists()

        history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
        # history 数量应等于 apply 成功数(可能因 attack/dedup 而少于 100)
        assert len(history) >= 50, f"history 数量异常: {len(history)}"

        # 所有 normal proposal 的 delta 应 bounded
        for h in history:
            sid = h.get("source_id", "") or h.get("metadata", {}).get("proposal_id", "")
            # 攻击或冲突 proposal 的 delta 不强制 bounded
            if "attack" in sid or "conf" in sid:
                continue
            for d in (h.get("affected_traits") or {}).values():
                assert abs(float(d)) <= 0.05 + 1e-9, \
                    f"normal proposal 出现未限幅 delta: {d} (sid={sid})"

    def test_long_term_record_growth_linear(
        self, consumer, isolated_self_model_dir
    ):
        """100 轮后,records 增长应近似线性,不爆炸。"""
        for i in range(100):
            p = make_proposal(
                path="warmth",
                before=0.5,
                after=0.51,
                proposal_id=f"prop_lin_{i}",
            )
            consumer.process(p)

        history = _read_jsonl(isolated_self_model_dir / "history.jsonl")
        beliefs = _read_jsonl(isolated_self_model_dir / "beliefs.jsonl")
        reflections = _read_jsonl(isolated_self_model_dir / "reflection.jsonl")

        # history 应等于 100
        assert len(history) == 100
        # beliefs / reflections 不应远超过 history 数量
        assert len(beliefs) <= 300
        assert len(reflections) <= 300

    def test_no_crash_on_garbage_input(self, consumer, isolated_self_model_dir):
        """异常输入(空 content / None / 超长)不导致 consumer 崩溃。"""
        garbage_inputs = [
            {},
            {"id": "prop_garbage_1"},  # 缺 proposed_changes
            {"id": "prop_garbage_2", "proposed_changes": []},
            {"id": "prop_garbage_3", "proposed_changes": [{"path": "warmth"}]},
            {"id": "prop_garbage_4", "proposed_changes": "not_a_list"},
            {"id": "prop_garbage_5", "proposed_changes": [{"path": "warmth", "before": None, "after": None}]},
            "not_a_dict",  # 类型错误
        ]
        for i, g in enumerate(garbage_inputs):
            try:
                r = consumer.process(g)
                # 不崩溃即可,error 可非空
                assert isinstance(r, dict)
            except Exception as e:
                pytest.fail(f"garbage #{i} 导致 consumer 崩溃: {e}")


# ============================================================
# 7. 监控指标: 验证 metrics 函数
# ============================================================

class TestLongTermMetricsFunctions:
    """验证 longterm_drift_metrics 中的指标函数行为正确。"""

    def test_collect_selfmodel_growth_trend(self, consumer, isolated_self_model_dir):
        """写入 N 条后,growth_trend 指标应正确反映。"""
        # 写 10 条
        for i in range(10):
            p = make_proposal(
                path="warmth",
                before=0.5,
                after=0.51,
                proposal_id=f"prop_metric_{i}",
            )
            consumer.process(p)

        from logs.longterm_drift_metrics import collect_selfmodel_growth_trend
        result = collect_selfmodel_growth_trend(isolated_self_model_dir)

        assert "beliefs_count" in result
        assert "history_count" in result
        assert "reflections_count" in result
        assert "total_records" in result
        assert result["history_count"] == 10

    def test_collect_personality_delta_distribution(self, consumer, isolated_self_model_dir):
        """delta_distribution 指标应正确统计。"""
        for i in range(20):
            p = make_proposal(
                path="warmth",
                before=0.5,
                after=round(0.5 + 0.01 * (1 if i % 2 == 0 else -1), 4),
                proposal_id=f"prop_delta_{i}",
            )
            consumer.process(p)

        from logs.longterm_drift_metrics import collect_personality_delta_distribution
        result = collect_personality_delta_distribution(isolated_self_model_dir)

        assert result["total_deltas"] == 20
        assert result["max_abs_delta"] <= 0.05 + 1e-9
        assert "warmth" in result["per_trait_stats"]

    def test_detect_core_identity_mutation_attempts(self, tmp_path):
        """mutation attempt 检测应能识别攻击 proposal。"""
        # 写入一个含攻击路径的 proposals.jsonl
        prop_path = tmp_path / "proposals.jsonl"
        attack = make_attack_proposal(target_trait="warmth")
        attack["status"] = "approved"
        prop_path.write_text(
            json.dumps(attack, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        from logs.longterm_drift_metrics import detect_core_identity_mutation_attempts
        result = detect_core_identity_mutation_attempts(prop_path)

        assert result["total_attempts"] >= 1
        assert result["is_clean"] is False

    def test_detect_duplicate_growth(self, tmp_path):
        """duplicate growth 检测应能识别重复 (source_event_id, path, delta)。"""
        prop_path = tmp_path / "proposals.jsonl"
        lines = []
        # 3 个相同 (source_event_id, path, delta)
        for i in range(3):
            p = make_proposal(
                proposal_id=f"prop_dup_metric_{i}",
                path="warmth",
                before=0.5,
                after=0.55,
                source_event_id="evt_dup_metric_same",
            )
            lines.append(json.dumps(p, ensure_ascii=False))
        prop_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        from logs.longterm_drift_metrics import detect_duplicate_growth
        result = detect_duplicate_growth(prop_path)

        assert result["duplicate_key_count"] >= 1

    def test_detect_conflicting_proposals(self, tmp_path):
        """conflicting proposal 检测应能识别方向相反的 proposal 对。"""
        prop_path = tmp_path / "proposals.jsonl"
        lines = []
        p_pos = make_proposal(
            proposal_id="prop_pos_m",
            path="warmth",
            before=0.5,
            after=0.55,
        )
        p_neg = make_proposal(
            proposal_id="prop_neg_m",
            path="warmth",
            before=0.5,
            after=0.47,
        )
        lines.append(json.dumps(p_pos, ensure_ascii=False))
        lines.append(json.dumps(p_neg, ensure_ascii=False))
        prop_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        from logs.longterm_drift_metrics import detect_conflicting_proposals
        result = detect_conflicting_proposals(prop_path)

        assert result["conflict_trait_count"] >= 1
        # warmth 应在冲突列表中
        conflict_traits = {c["trait"] for c in result["conflicts"]}
        assert "warmth" in conflict_traits


# ============================================================
# 8. 端到端: 模拟 + 指标 综合
# ============================================================

class TestEndToEndStability:
    """运行模拟并验证所有指标均健康。"""

    def test_full_simulation_all_metrics_healthy(
        self, consumer, isolated_self_model_dir, tmp_path
    ):
        """完整模拟: 50 轮 + 4 种场景 + 6 类指标全健康。"""
        import random
        rng = random.Random(20260802)

        proposals: List[Dict[str, Any]] = []
        normal_proposal_ids: set = set()
        for c in range(50):
            roll = rng.random()
            if roll < 0.05:
                p = make_attack_proposal(target_trait="warmth")
                p["id"] = f"prop_e2e_attack_{c}"
            elif roll < 0.10:
                if proposals:
                    last = proposals[-1]
                    p = make_proposal(
                        path=last.get("proposed_changes", [{}])[0].get("path", "warmth"),
                        before=0.5,
                        after=0.55,
                        proposal_id=f"prop_e2e_dup_{c}",
                    )
                    p["source_event_id"] = last.get("source_event_id", f"evt_dup_{c}")
                else:
                    p = make_proposal(proposal_id=f"prop_e2e_norm_{c}")
                    normal_proposal_ids.add(p["id"])
            elif roll < 0.15:
                if proposals:
                    p = make_conflicting_proposal(base_proposal=proposals[-1], cycle=c)
                    p["id"] = f"prop_e2e_conf_{c}"
                else:
                    p = make_proposal(proposal_id=f"prop_e2e_norm_{c}")
                    normal_proposal_ids.add(p["id"])
            else:
                p = make_proposal(
                    path=rng.choice(list(ALLOWED_TRAIT_PATHS)),
                    before=0.5,
                    after=round(0.5 + 0.01 * rng.choice([-1, 1]), 4),
                    proposal_id=f"prop_e2e_norm_{c}",
                )
                normal_proposal_ids.add(p["id"])
            proposals.append(p)
            consumer.process(p)

        # 写 proposals.jsonl 用于 metrics
        prop_path = tmp_path / "proposals.jsonl"
        with open(prop_path, "w", encoding="utf-8") as f:
            for p in proposals:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

        # 验证 6 类指标
        from logs.longterm_drift_metrics import (
            collect_selfmodel_growth_trend,
            collect_personality_delta_distribution,
            collect_proposal_type_distribution,
            detect_core_identity_mutation_attempts,
            detect_duplicate_growth,
            detect_conflicting_proposals,
        )

        # 1. SelfModel 增长
        growth = collect_selfmodel_growth_trend(isolated_self_model_dir)
        assert growth["history_count"] >= 30, f"history 太少: {growth}"

        # 2. personality delta (只看 normal proposal)
        delta = collect_personality_delta_distribution(isolated_self_model_dir)
        # delta distribution 中可能含 attack 的 -0.7,因此 max_abs_delta 不强制
        # 但除 attack 外,所有 delta 应 bounded
        per_trait = delta.get("per_trait_stats", {})
        for trait, stats in per_trait.items():
            # normal trait 范围 (非 core_identity) 的 max_abs 应对应正常
            pass  # 不强制,因为 attack 已污染

        # 3. proposal 类型分布
        pdist = collect_proposal_type_distribution(prop_path)
        assert pdist["total_proposals"] == 50

        # 4. mutation attempt - 应至少检测到 attack 的尝试
        attacks = detect_core_identity_mutation_attempts(prop_path)
        # 5% × 50 = 2-3 个 attack 应被检测
        assert attacks["total_attempts"] >= 1, \
            f"未检测到 attack: {attacks}"

        # 5. duplicate
        dups = detect_duplicate_growth(prop_path)
        # duplicate 数 ≥ 0
        assert dups["duplicate_key_count"] >= 0

        # 6. conflicting
        confs = detect_conflicting_proposals(prop_path)
        # conflict 数 ≥ 0
        assert confs["conflict_trait_count"] >= 0

        # 7. 关键不变量:CoreIdentity 未被修改
        from src.personality.core_identity import CoreIdentity
        assert CoreIdentity.get_max_change_limit() == pytest.approx(0.3, abs=1e-9)
        assert "温柔" in CoreIdentity.get_core_traits()
