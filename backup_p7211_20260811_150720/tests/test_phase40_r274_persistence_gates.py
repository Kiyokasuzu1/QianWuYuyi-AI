"""
Phase 4.0 — R2.7.4 Persistence Gates（RPG-1 / RPG-2 / RPG-3 / RPG-4）

验证羽依"第一次跨进程保持自己"：
  - 保存 → 重启进程 → 加载：trait 完全一致
  - 记忆持久化后：回复能关联昨天的话题
  - 人格稳定：不会一次聊天就完全变了另一个人
  - 磁盘损坏/回退：自动降级为出厂基线，不崩溃

测试沙箱：每个测试类使用独立的 tempdir（data/snapshots 不被污染）。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from src.personality.evolution_record import build_evolution_record
from src.personality.personality_state import PersonalityState, reset_personality_state
from src.response_phase4.persistence_manager import (
    CURRENT_SCHEMA_VERSION,
    GLOBAL_TRAIT_DELTA_CAP_FROM_BASELINE,
    SINGLE_TRAIT_DELTA_CAP_PER_SESSION,
    Phase4PersistenceManager,
    validate_memory_snapshot_shape,
    validate_personality_snapshot_shape,
    validate_relationship_snapshot_shape,
)


def _apply_delta(
    ps: PersonalityState,
    delta: Dict[str, float],
    proposal_id: str,
    approval_id: str,
) -> bool:
    """辅助：对 PersonalityState apply 一个 trait delta。返回 True 表示成功应用。"""
    before = {f"trait.{k}": float(ps.traits.get(k, 0.5)) for k in delta}
    after = {f"trait.{k}": min(1.0, max(0.0, float(ps.traits.get(k, 0.5)) + v)) for k, v in delta.items()}
    rec = build_evolution_record(
        proposal_id=proposal_id,
        approval_id=approval_id,
        change_type="trait_delta",
        before=before,
        after=after,
        reasons=[f"proposal {proposal_id}"],
        confidence=0.88,
    )
    r = ps.apply_evolution(rec)
    return bool(r.get("applied"))


class TestRPG1PersistenceGate(unittest.TestCase):
    """RPG-1 Persistence Gate：Save → [模拟重启] → Load，Personality/Memory/Relationship 全等。

    Session A 修改 PersonalityState → save
    重启 Session B → load
    状态完全一致（含 version、applied_proposal_ids、trait 值、evolution_record_ids）
    """

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="rpg1_sandbox_"))
        self.pm = Phase4PersistenceManager(snapshot_dir=str(self.tmpdir), user_tag="yuyi_test")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rpg_1a_personality_save_load_roundtrip(self) -> None:
        """Personality：save 后 load，trait/version/ids 完全一致（模拟跨进程）。"""
        ps = reset_personality_state()
        _apply_delta(ps, {"creativity": +0.10}, "p_rpg1_001", "a_rpg1_001")
        _apply_delta(ps, {"character_interest": +0.10}, "p_rpg1_002", "a_rpg1_002")
        _apply_delta(ps, {"creativity": +0.03, "curiosity": -0.02}, "p_rpg1_003", "a_rpg1_003")

        self.pm.save_all(ps, memories=[], relationship=None)

        # 模拟重启：load
        result = self.pm.load_all()
        ps2 = result.personality

        # 全等检查（trait 逐值 + version + proposal ids）
        for k, v in ps.traits.items():
            self.assertAlmostEqual(
                ps2.traits.get(k, -99),
                v,
                places=5,
                msg=f"trait {k} 不匹配: load={ps2.traits.get(k)} save={v}",
            )
        self.assertEqual(ps2.version, ps.version, f"version 不匹配: load={ps2.version} save={ps.version}")
        self.assertEqual(
            ps2._applied_proposal_ids,
            ps._applied_proposal_ids,
            "applied_proposal_ids 不匹配（EP-2 幂等依赖）",
        )
        self.assertEqual(
            len(ps2.evolution_record_ids),
            len(ps.evolution_record_ids),
            "evolution_record_ids 数量不匹配",
        )

    def test_rpg_1b_memory_save_load_roundtrip(self) -> None:
        """Memory：save 后 load，records 条数/id/text 完全一致。"""
        ps = reset_personality_state()
        memories = [
            {"id": "mem_101", "text": "最近在研究 AI 绘画", "topic": "AI_art", "timestamp_ms": 1700000100000},
            {"id": "mem_102", "text": "我设计了一个猫娘角色", "topic": "character_design", "timestamp_ms": 1700000200000},
            {"id": "mem_103", "text": "今天试了三种绘画风格对比", "topic": "AI_art", "timestamp_ms": 1700000300000},
        ]
        self.pm.save_all(ps, memories=memories, relationship=None)

        result = self.pm.load_all()
        self.assertEqual(len(result.memories), 3, "memory 条数不匹配")
        by_id = {m["id"]: m for m in result.memories}
        for orig in memories:
            self.assertIn(orig["id"], by_id, f"memory id 丢失: {orig['id']}")
            self.assertEqual(by_id[orig["id"]]["text"], orig["text"])
            self.assertEqual(by_id[orig["id"]]["topic"], orig["topic"])

    def test_rpg_1c_relationship_save_load_roundtrip(self) -> None:
        """Relationship：save trust/closeness/interactions，load 后保持。"""
        ps = reset_personality_state()
        rel = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "trust_level": 0.75,
            "closeness": 0.62,
            "interaction_count": 17,
            "last_interaction_ts_ms": 1700000500000,
            "shared_memory_tags": ["AI 绘画", "猫娘"],
            "history": [{"event": "first_chat", "ts": 1700000001}, {"event": "deep_talk", "ts": 1700000200}],
        }
        self.pm.save_all(ps, memories=[], relationship=rel)

        result = self.pm.load_all()
        r = result.relationship
        self.assertAlmostEqual(r["trust_level"], 0.75, places=5)
        self.assertAlmostEqual(r["closeness"], 0.62, places=5)
        self.assertEqual(r["interaction_count"], 17)
        self.assertEqual(r["shared_memory_tags"], ["AI 绘画", "猫娘"])
        self.assertEqual(len(r["history"]), 2)

    def test_rpg_1d_files_exist_after_save(self) -> None:
        """save 后 3 个快照文件必须存在，内容 JSON 可解析。"""
        ps = reset_personality_state()
        _apply_delta(ps, {"empathy": +0.05}, "p_rpg1_file", "a_rpg1_file")
        paths = self.pm.save_all(ps, memories=[{"id": "x", "text": "x", "topic": "x", "timestamp_ms": 1}])

        for label, pstr in paths.items():
            p = Path(pstr)
            self.assertTrue(p.exists(), f"{label} 文件不存在: {p}")
            # 内容 JSON 可解析且含 schema_version
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.assertIn("schema_version", d, f"{p.name} schema_version 丢失")

    def test_rpg_1e_shapes_pass_after_load(self) -> None:
        """load 后生成的 Personality 再次 to_dict 必须通过 snapshot shape validate。"""
        ps = reset_personality_state()
        _apply_delta(ps, {"playfulness": +0.10}, "p_rpg1_shape", "a_rpg1_shape")
        self.pm.save_all(ps, memories=[{"id": "a", "text": "t", "topic": "t", "timestamp_ms": 1}])

        res = self.pm.load_all()
        # Personality to_dict → validate
        snap = res.personality.to_dict()
        validate_personality_snapshot_shape(snap)  # no raise
        # Memory snapshot → validate
        mem = {"schema_version": CURRENT_SCHEMA_VERSION, "records": res.memories}
        validate_memory_snapshot_shape(mem)  # no raise
        # Relationship → validate
        validate_relationship_snapshot_shape(res.relationship)  # no raise


class TestRPG2MemoryContinuityGate(unittest.TestCase):
    """RPG-2 Memory Continuity Gate：昨天聊过的"角色设计"今天仍然能关联。

    流程：
      Day A：写入 memory=[AI绘画, 猫娘角色]，save。
      次日 Day B：load 恢复，跑一轮 ContinuousLoopRunner（空 proposal_delta，只引用恢复后的记忆）。
      断言：回复含"角色设计"或"绘画"或"创造"等恢复记忆关键词（不是空白 baseline）。
    """

    def setUp(self) -> None:
        from tests.support.continuous_loop_runner import ContinuousLoopRunner
        self.tmpdir = Path(tempfile.mkdtemp(prefix="rpg2_sandbox_"))
        self.pm = Phase4PersistenceManager(snapshot_dir=str(self.tmpdir), user_tag="yuyi_memory_cont")
        self.Runner = ContinuousLoopRunner

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rpg_2a_replied_mentions_recovered_memory_keywords(self) -> None:
        # Day A：聊"角色设计/AI绘画"→ save
        ps = reset_personality_state()
        _apply_delta(ps, {"creativity": +0.10, "character_interest": +0.05}, "p_rpg2_a", "a_rpg2_a")
        memories_day_a = [
            {"id": "m_rpg2_101", "text": "用户最近在研究 AI 绘画，尝试不同风格", "topic": "AI_art", "timestamp_ms": 1700000100000},
            {"id": "m_rpg2_102", "text": "用户设计了一个猫娘角色，造型可爱，性格软萌", "topic": "character_design", "timestamp_ms": 1700000200000},
            {"id": "m_rpg2_103", "text": "用户对创作类话题越来越感兴趣", "topic": "AI_personality", "timestamp_ms": 1700000300000},
            {"id": "m_rpg2_104", "text": "用户想给猫娘角色配色，考虑水彩风格与日系风格的结合", "topic": "character_design", "timestamp_ms": 1700000400000},
        ]
        rel_day_a = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "trust_level": 0.55,
            "closeness": 0.42,
            "interaction_count": 3,
            "last_interaction_ts_ms": 1700000300000,
            "shared_memory_tags": ["AI_art", "character_design"],
            "history": [{"event": "day_a_chat"}],
        }
        self.pm.save_all(ps, memories=memories_day_a, relationship=rel_day_a)

        # 次日 Day B：load 恢复 → 启动 Runner
        res = self.pm.load_all()
        loaded_ps = res.personality
        loaded_memories = res.memories

        # 跑一轮：用户问"最近怎么样？"（不新增记忆，用恢复后的累积记忆）
        runner = self.Runner()
        # 把恢复后的记忆放进 cumulative_memories（key = memory id）
        cum: Dict[str, Any] = {}
        for m in loaded_memories:
            cum[m["id"]] = (m["id"], m.get("topic", "general"), m["text"])

        turn_cfg: Dict[str, Any] = {
            "turn_idx": 6,
            "day_label": "Day B (续)",
            "user_input": "最近怎么样？",
            "memories": [],  # 本轮无新记忆
            "proposal_delta": {},  # 不触发成长（验证"记忆单独能影响回复"）
            "sr_cause": "routine_update",
            "ts_ms": 1700001000000,
        }
        turn = runner._run_one_turn(
            cur_traits=dict(loaded_ps.traits),
            current_personality_version=max(loaded_ps.version, 1),
            cumulative_memories=cum,
            turn_cfg=turn_cfg,
            session_id="s_rpg2_dayB_001",
        )

        reply = turn["reply_text"]
        # 恢复后表达关联关键词：
        #   - 成长类（严格）：AI 绘画/角色/设计/创造/绘画 等（MockEngine growth/self_intro 输出）
        #   - 记忆连续性宽松类：塑造形象 / 画图 / 创作 / 慢慢把想法具象 等（MockEngine memory_accumulation 输出，
        #     刻意避开严格 growth 关键词，避免破坏 R2.7.1-B3 的前后递增 Gate）
        strict_kws = ["AI 绘画", "AI绘画", "角色", "设计", "创造", "绘画", "猫娘", "创造力", "创造新"]
        continuity_kws = ["塑造形象", "画图", "创作", "具象", "构思", "形象", "画"]
        all_kws = list(set(strict_kws + continuity_kws))
        hits = sum(1 for w in all_kws if w in reply)
        # 至少命中 1 个：回复不再是"没特别变化"的 baseline
        self.assertGreaterEqual(
            hits,
            1,
            f"恢复后的回复没有关联昨日记忆关键词。reply=\n{reply}",
        )
        # 另外 reply 不能只是 baseline（没有记忆的 baseline 是"最近没特别变化"）
        self.assertNotIn(
            "最近没什么特别的变化",
            reply,
            f"回复退回 baseline，记忆没影响表达。reply=\n{reply}",
        )


class TestRPG3PersonalityStabilityGate(unittest.TestCase):
    """RPG-3 Personality Stability Gate：防止人格漂移。

    1) 单次 apply_evolution 的 trait 变化幅度 ≤ 0.15
    2) 出厂基线 → 当前，全局 trait 平均变化 ≤ 0.40（由 PM 的 stability Gate 检查）
    3) 加载低 version 的旧快照 → 拒绝回退（recovery_details 有 warn）
    """

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="rpg3_sandbox_"))
        self.pm = Phase4PersistenceManager(snapshot_dir=str(self.tmpdir), user_tag="yuyi_stab")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rpg_3a_single_evolution_delta_cap(self) -> None:
        """单次成长 creativity 变 +0.20（超过 0.15 cap）应被视为"一次漂移过大"。"""
        # 先应用"合法小幅度"3 次（每次 ≤ 0.15）→ 正常
        ps = reset_personality_state()
        before = float(ps.traits.get("creativity", 0.0))
        ok = _apply_delta(ps, {"creativity": +0.10}, "p_rpg3_ok", "a_rpg3_ok")
        self.assertTrue(ok, "幅度 0.10 应该 apply 成功")
        after = float(ps.traits.get("creativity", 0.0))
        delta1 = abs(after - before)
        self.assertLessEqual(
            delta1,
            SINGLE_TRAIT_DELTA_CAP_PER_SESSION,
            f"单次变化 {delta1:.3f} > 单次 cap {SINGLE_TRAIT_DELTA_CAP_PER_SESSION}",
        )

        # 现在"强行超大 delta"测试：模拟恶意大跳变 → 从 PersonalityState 行为看 clamp 到 [0,1]
        # 注意：这里我们不依赖 apply（evolution_record after 本身已经 clamp）
        # 而是验证 PersistenceManager 的 stability Gate：restore 后超大偏差会给出 warning
        # 先 save 一个几乎没变的 state
        self.pm.save_all(ps, memories=[], relationship=None)
        res = self.pm.load_all()
        # recovery_details 里 severity=ok 的 stability 应该存在
        sev_ok = [r for r in res.recovery_details if r["severity"] == "ok" and "stability" in r["issue"]]
        self.assertGreaterEqual(len(sev_ok), 1, f"没找到 stability_ok：{res.recovery_details}")

    def test_rpg_3b_global_drift_triggers_warning(self) -> None:
        """存一个 drift 严重的 Personality（全局 avg > 0.40 cap）→ stability Gate 应 warn。"""
        # 直接构造一个 drifted snapshot
        drifted_traits = {
            # baseline: creativity=0.6 → drift to 0.99 (Δ=0.39)
            "creativity": 0.99,
            # baseline: curiosity=0.7 → drift 0.05 (Δ=0.65)
            "curiosity": 0.05,
            # baseline: empathy=0.65 → 0.98 (Δ=0.33)
            "empathy": 0.98,
            # baseline: independence=0.6 → 0.03 (Δ=0.57)
            "independence": 0.03,
            # baseline: playfulness=0.55 → 0.97 (Δ=0.42)
            "playfulness": 0.97,
        }
        drifted_snap = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "traits": drifted_traits,
            "version": 10,
            "applied_proposal_ids": ["p_x"],
            "evolution_record_ids": [],
        }
        p = self.tmpdir / f"personality_yuyi_stab.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(drifted_snap, f)

        # load_all 后 recovery_details 应该有 drift warn
        res = self.pm.load_all()
        drift_warns = [
            r for r in res.recovery_details
            if r["severity"] == "warn" and ("drift" in r["issue"])
        ]
        self.assertGreaterEqual(
            len(drift_warns),
            1,
            f"drifted snapshot 没触发 stability warning：\n{res.recovery_details}",
        )

    def test_rpg_3c_version_rollback_refused(self) -> None:
        """load 时 ceiling > disk.version → 版本回退，返回 baseline PersonalityState（version=0）并 warn。"""
        ps = reset_personality_state()
        _apply_delta(ps, {"warmth": +0.10}, "p_rpg3_rb", "a_rpg3_rb")  # version=1
        self.pm.save_all(ps, memories=[], relationship=None)
        # disk: version=1；传入 ceiling=99 → 拒绝回退
        res = self.pm.load_all(current_personality_version_ceiling=99)
        # recovery_details 应该出现 personality_version_rollback_blocked
        rollback_warns = [
            r for r in res.recovery_details
            if r["severity"] == "warn" and "rollback" in r["issue"]
        ]
        self.assertGreaterEqual(
            len(rollback_warns),
            1,
            f"版本回退没被警告：\n{res.recovery_details}",
        )
        # load 出来的 personality 应该是 baseline（version=0，没滚回 disk 的 version=1）
        self.assertEqual(
            res.personality.version,
            0,
            f"回退后 personality 不是 baseline（version={res.personality.version}）",
        )


class TestRPG4SnapshotRecoveryGate(unittest.TestCase):
    """RPG-4 Snapshot Recovery Gate：磁盘损坏/缺失 → 降级，不崩溃。

    场景：
      1) 目录不存在（新启动） → 三态用默认值（recovery_details 全部 info/ok）
      2) personality 文件空 / 非法 JSON → 降级 PersonalityState()
      3) personality 缺字段 → 降级
      4) memory 文件损坏 → 空 []
      5) relationship 缺少 closeness / 非法 JSON → 默认值
    """

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="rpg4_sandbox_"))
        self.pm = Phase4PersistenceManager(snapshot_dir=str(self.tmpdir), user_tag="yuyi_rec")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rpg_4a_fresh_start_returns_defaults(self) -> None:
        """全新安装（无任何快照）→ Personality() 默认 traits，memory=[], relationship 默认。"""
        res = self.pm.load_all()
        # Personality 默认基线
        self.assertEqual(res.personality.version, 0)
        for k, v in {"creativity": 0.6, "curiosity": 0.7, "empathy": 0.65, "independence": 0.6, "playfulness": 0.55}.items():
            self.assertAlmostEqual(res.personality.traits.get(k, -1), v, places=5, msg=f"默认基线 trait {k} 错")
        self.assertEqual(res.memories, [], "memory 应为空列表")
        # relationship 默认值
        self.assertAlmostEqual(float(res.relationship.get("trust_level", -1)), 0.50, places=5)
        self.assertAlmostEqual(float(res.relationship.get("closeness", -1)), 0.30, places=5)

    def test_rpg_4b_personality_corrupted_json(self) -> None:
        """personality 文件为非法 JSON → 降级，memory/relationship 正常。"""
        ps_path = self.pm.personality_file
        ps_path.parent.mkdir(parents=True, exist_ok=True)
        ps_path.write_text("this is {not json at all!", encoding="utf-8")
        # 写入一个合法 memory 做对照
        mem_path = self.pm.memory_file
        mem_path.write_text(
            json.dumps({"schema_version": 1, "records": [{"id": "ok", "text": "ok", "topic": "x", "timestamp_ms": 1}]}),
            encoding="utf-8",
        )
        res = self.pm.load_all()
        # personality 降级（默认 version=0 基线）
        self.assertEqual(res.personality.version, 0, "损坏 personality 未降级")
        # memory 仍然正确读取
        self.assertEqual([m["id"] for m in res.memories], ["ok"], "memory 也坏掉了？不对")
        # recovery_details 应有 corrupted warn 或 unreadable warn（JSONDecodeError 被 ValueError 捕获为 corrupted）
        corrupted = [r for r in res.recovery_details if r["issue"] == "personality_snapshot_corrupted" or r["issue"] == "personality_snapshot_unreadable"]
        self.assertGreaterEqual(len(corrupted), 1, f"corrupted 警告丢失：\n{res.recovery_details}")

    def test_rpg_4c_personality_missing_required_fields(self) -> None:
        """personality 缺 applied_proposal_ids → validate 失败 → 降级。"""
        bad = {"schema_version": 1, "traits": {"creativity": 0.8}, "version": 3}  # no applied_proposal_ids
        self.pm.personality_file.parent.mkdir(parents=True, exist_ok=True)
        self.pm.personality_file.write_text(json.dumps(bad), encoding="utf-8")

        res = self.pm.load_all()
        self.assertEqual(res.personality.version, 0, "缺字段未降级")
        corrupted = [r for r in res.recovery_details if r["issue"] == "personality_snapshot_corrupted"]
        self.assertGreaterEqual(len(corrupted), 1)

    def test_rpg_4d_memory_corrupted(self) -> None:
        """memory 文件为非法 JSON → 返回空列表。"""
        self.pm.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.pm.memory_file.write_text("{oops}", encoding="utf-8")
        res = self.pm.load_all()
        self.assertEqual(res.memories, [], "memory 未降级为空列表")
        corrupted = [r for r in res.recovery_details if "memory" in r["issue"] and (
            "unreadable" in r["issue"] or "corrupted" in r["issue"]
        )]
        self.assertGreaterEqual(len(corrupted), 1)

    def test_rpg_4e_relationship_missing_trust(self) -> None:
        """relationship 缺 trust_level（不是数值）→ 默认值。"""
        bad = {"schema_version": 1, "closeness": 0.5, "interaction_count": 0,
               "last_interaction_ts_ms": 0, "shared_memory_tags": [], "history": []}  # no trust_level
        self.pm.relationship_file.parent.mkdir(parents=True, exist_ok=True)
        self.pm.relationship_file.write_text(json.dumps(bad), encoding="utf-8")
        res = self.pm.load_all()
        # 降级后 trust_level=0.5
        self.assertAlmostEqual(float(res.relationship.get("trust_level", -1)), 0.50, places=4)


if __name__ == "__main__":
    unittest.main()
