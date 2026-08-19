"""
Phase 4.0.0 — SelfModel Persistence 重启一致性测试

验证目标：
  1. SelfModelStore 持久化：apply_change_proposal() 后落盘，重启恢复一致
  2. PersonalityGrowthHistory 持久化：add() 后落盘，重启恢复一致
  3. 跨重启整链：growth_record → SelfModelStore 变化 → 重启 → 恢复一致
  4. 边界情况：空文件、损坏 JSON、并发写入安全

章程：只测不修。发现问题 → 分类 → 记录 → 报告。
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid

import pytest

from src.personality.self_model_store import SelfModelStore
from src.personality.personality_growth_record import PersonalityGrowthHistory
from src.personality.self_model_updater import SelfModelUpdater


# ============================================================
# 辅助函数
# ============================================================

def _make_growth_record(
    record_id: str = None,
    source_event_id: str = None,
    reason: str = "用户展示了对我技术能力的信任",
    affected_dimensions: dict = None,
    confidence: float = 0.75,
    growth_signal: str = "trust_building",
) -> dict:
    """创建一条合法 GrowthRecord。"""
    return {
        "record_id": record_id or str(uuid.uuid4()),
        "source_event_id": source_event_id or str(uuid.uuid4()),
        "source_type": "interaction",
        "growth_signal": growth_signal,
        "growth_level": "trait",
        "affected_dimensions": affected_dimensions or {"self_confidence": 0.03},
        "confidence": confidence,
        "reason": reason,
        "evidence": [
            {"text": "用户说：我相信你的判断", "role": "user"}
        ],
        "timestamp": "2026-08-15T12:00:00",
        "changes": affected_dimensions or {"self_confidence": 0.03},
        "meaning": "用户对我的信任在加深",
    }


# ============================================================
# 测试 1: SelfModelStore 持久化 — 写 → 重启 → 读
# ============================================================

class TestSelfModelStorePersistence:
    """SelfModelStore 持久化重启一致性。"""

    def test_write_and_restart_narrative_append(self):
        """写入一条 growth_narrative 后重启，验证恢复一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "self_model.json")

            # === 启动 A ===
            store_a = SelfModelStore(storage_path=path)
            updater_a = SelfModelUpdater(self_model_store=store_a)
            record = _make_growth_record(
                reason="用户说：我相信你的判断",
                growth_signal="trust_verified",
            )
            proposal = updater_a.create_proposal_from_growth(record)
            assert proposal is not None, "应从合法 GrowthRecord 生成 proposal"
            store_a.apply_change_proposal(proposal)

            # 验证 A 的状态
            model_a = store_a.get()
            assert model_a is not None
            narratives_a = model_a.get("growth_narratives", [])
            assert len(narratives_a) >= 1, f"应有至少 1 条 narrative，实际 {len(narratives_a)}"
            assert narratives_a[0]["source_growth_record_id"] == record["record_id"]

            # 验证文件存在且非空
            assert os.path.exists(path), f"持久化文件应存在: {path}"
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            assert "model" in raw
            assert "saved_at" in raw

            # === 关闭 A（删除引用） ===
            del store_a
            del updater_a

            # === 启动 B ===
            store_b = SelfModelStore(storage_path=path)
            model_b = store_b.get()
            assert model_b is not None, "重启后应能恢复 SelfModel"

            narratives_b = model_b.get("growth_narratives", [])
            assert len(narratives_b) == len(narratives_a), (
                f"重启后 narrative 数量应一致: {len(narratives_a)} vs {len(narratives_b)}"
            )
            assert narratives_b[0]["source_growth_record_id"] == record["record_id"], (
                "重启后 source_growth_record_id 应一致"
            )
            assert narratives_b[0]["narrative"] == narratives_a[0]["narrative"], (
                "重启后 narrative 内容应一致"
            )

    def test_write_and_restart_self_understanding(self):
        """更新 self_understanding 后重启，验证恢复一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "self_model.json")

            # === 启动 A ===
            store_a = SelfModelStore(storage_path=path)
            updater_a = SelfModelUpdater(self_model_store=store_a)

            # 先写入一条 narrative 初始化基础模型
            record1 = _make_growth_record(
                record_id="rec-001",
                reason="初次互动",
                growth_signal="first_contact",
            )
            proposal1 = updater_a.create_proposal_from_growth(record1)
            store_a.apply_change_proposal(proposal1)

            # 再写入一条 self_understanding 更新
            record2 = _make_growth_record(
                record_id="rec-002",
                reason="用户持续信任我",
                affected_dimensions={"self_awareness": 0.05},
                growth_signal="self_awareness_growth",
            )
            proposal2 = updater_a.create_proposal_from_growth(record2)
            store_a.apply_change_proposal(proposal2)

            understanding_a = store_a.get().get("self_understanding", {})

            # === 关闭 A ===
            del store_a
            del updater_a

            # === 启动 B ===
            store_b = SelfModelStore(storage_path=path)
            understanding_b = store_b.get().get("self_understanding", {})

            for key in ["experience_awareness", "trait_awareness", "identity_continuity", "overall"]:
                assert understanding_b.get(key) == understanding_a.get(key), (
                    f"self_understanding.{key} 重启后应一致: "
                    f"{understanding_a.get(key)} vs {understanding_b.get(key)}"
                )

    def test_multiple_narratives_restart(self):
        """写入多条 narrative 后重启，验证全部恢复。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "self_model.json")

            store_a = SelfModelStore(storage_path=path)
            updater_a = SelfModelUpdater(self_model_store=store_a)

            record_ids = []
            for i in range(5):
                rid = f"rec-{i:03d}"
                record_ids.append(rid)
                record = _make_growth_record(
                    record_id=rid,
                    reason=f"第{i}次互动",
                    growth_signal=f"interaction_{i}",
                )
                proposal = updater_a.create_proposal_from_growth(record)
                store_a.apply_change_proposal(proposal)

            narratives_a = store_a.get().get("growth_narratives", [])
            assert len(narratives_a) == 5

            del store_a
            del updater_a

            store_b = SelfModelStore(storage_path=path)
            narratives_b = store_b.get().get("growth_narratives", [])
            assert len(narratives_b) == 5, f"重启后应有 5 条 narrative，实际 {len(narratives_b)}"

            restored_ids = [n["source_growth_record_id"] for n in narratives_b]
            for rid in record_ids:
                assert rid in restored_ids, f"record_id={rid} 应在重启后恢复"


# ============================================================
# 测试 2: PersonalityGrowthHistory 持久化 — 写 → 重启 → 读
# ============================================================

class TestPersonalityGrowthHistoryPersistence:
    """PersonalityGrowthHistory 持久化重启一致性。"""

    def test_add_and_restart(self):
        """添加多条记录后重启，验证恢复一致。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "growth_history.json")

            # === 启动 A ===
            history_a = PersonalityGrowthHistory(storage_path=path)
            records = [_make_growth_record(record_id=f"gh-{i:03d}") for i in range(3)]
            for r in records:
                history_a.add(r)

            assert history_a.count() == 3
            assert os.path.exists(path)

            del history_a

            # === 启动 B ===
            history_b = PersonalityGrowthHistory(storage_path=path)
            assert history_b.count() == 3, f"重启后记录数应一致: 3 vs {history_b.count()}"

            restored_ids = [r.get("record_id") for r in history_b.all()]
            for r in records:
                assert r["record_id"] in restored_ids, (
                    f"record_id={r['record_id']} 应在重启后恢复"
                )

    def test_empty_history_restart(self):
        """空 history 重启后仍为空。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "growth_history.json")

            history_a = PersonalityGrowthHistory(storage_path=path)
            assert history_a.count() == 0
            del history_a

            history_b = PersonalityGrowthHistory(storage_path=path)
            assert history_b.count() == 0


# ============================================================
# 测试 3: 跨组件整链持久化
# ============================================================

class TestCrossComponentRestart:
    """GrowthRecord → SelfModelStore 整链重启一致性。"""

    def test_full_chain_restart(self):
        """GrowthPipeline 产生记录 → SelfModelUpdater 更新 SelfModel → 重启 → 恢复。"""
        with tempfile.TemporaryDirectory() as tmp:
            sm_path = os.path.join(tmp, "self_model.json")
            gh_path = os.path.join(tmp, "growth_history.json")

            # === 启动 A ===
            store_a = SelfModelStore(storage_path=sm_path)
            updater_a = SelfModelUpdater(self_model_store=store_a)
            history_a = PersonalityGrowthHistory(storage_path=gh_path)

            # 模拟 GrowthPipeline 产生记录
            record = _make_growth_record(
                record_id="chain-001",
                reason="完整链路测试",
                affected_dimensions={"self_confidence": 0.04, "self_awareness": 0.03},
                confidence=0.85,
            )
            history_a.add(record)

            # SelfModelUpdater 读取 GrowthRecord 并更新 SelfModel
            proposal = updater_a.create_proposal_from_growth(record)
            assert proposal is not None
            store_a.apply_change_proposal(proposal)

            # 记录 A 的状态
            narratives_a = store_a.get().get("growth_narratives", [])
            understanding_a = store_a.get().get("self_understanding", {})
            history_count_a = history_a.count()

            del store_a
            del updater_a
            del history_a

            # === 启动 B ===
            store_b = SelfModelStore(storage_path=sm_path)
            history_b = PersonalityGrowthHistory(storage_path=gh_path)

            # 验证 GrowthHistory
            assert history_b.count() == history_count_a, (
                f"GrowthHistory 重启后记录数应一致: {history_count_a} vs {history_b.count()}"
            )

            # 验证 SelfModel
            narratives_b = store_b.get().get("growth_narratives", [])
            assert len(narratives_b) == len(narratives_a), (
                f"重启后 narrative 数量应一致: {len(narratives_a)} vs {len(narratives_b)}"
            )
            assert narratives_b[0]["source_growth_record_id"] == record["record_id"], (
                "source_growth_record_id 应一致"
            )

            understanding_b = store_b.get().get("self_understanding", {})
            for key in ["experience_awareness", "trait_awareness", "identity_continuity", "overall"]:
                assert understanding_b.get(key) == understanding_a.get(key), (
                    f"self_understanding.{key} 重启后应一致"
                )


# ============================================================
# 测试 4: 边界情况
# ============================================================

class TestEdgeCases:
    """持久化边界情况。"""

    def test_missing_file_creates_default(self):
        """文件不存在时，首次 apply 触发 _ensure_base_model 并落盘。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nonexistent", "self_model.json")
            store = SelfModelStore(storage_path=path)
            # 文件不存在时 get() 返回 None（尚未初始化）
            assert store.get() is None, "文件不存在时初始状态应为 None"

            # 首次 apply_change_proposal 触发 _ensure_base_model 并落盘
            updater = SelfModelUpdater(self_model_store=store)
            record = _make_growth_record(record_id="init-001")
            proposal = updater.create_proposal_from_growth(record)
            store.apply_change_proposal(proposal)

            model = store.get()
            assert model is not None, "首次 apply 后应创建基础模型"
            assert model.get("_auto_initialized") is True, "应由 _ensure_base_model 自动初始化"
            assert os.path.exists(path), "save 后文件应存在"

    def test_corrupted_json_recovery(self):
        """损坏的 JSON 文件应被优雅处理（不崩溃）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "self_model.json")

            # 写入损坏的 JSON
            with open(path, "w", encoding="utf-8") as f:
                f.write("this is not valid json {{{")

            # 应能正常创建，不抛异常
            store = SelfModelStore(storage_path=path)
            model = store.get()
            # 损坏后 _current_model 应为 None（加载失败）
            assert model is None or model.get("_auto_initialized") is True, (
                "损坏 JSON 后应优雅降级，不崩溃"
            )

    def test_subdirectory_auto_creation(self):
        """不存在的目录应自动创建。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "deep", "nested", "dir", "self_model.json")
            store = SelfModelStore(storage_path=path)
            # __init__ 中 mkdir(parents=True) 已创建父目录
            assert os.path.exists(os.path.dirname(path)), (
                f"父目录应自动创建: {os.path.dirname(path)}"
            )
            # 文件在触发 save 后才创建
            updater = SelfModelUpdater(self_model_store=store)
            record = _make_growth_record(record_id="dir-001")
            proposal = updater.create_proposal_from_growth(record)
            store.apply_change_proposal(proposal)
            assert os.path.exists(path), f"save 后文件应存在: {path}"

    def test_two_instances_same_path(self):
        """两个实例共享同一路径：写入后另一个实例应能读到。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "self_model.json")

            store1 = SelfModelStore(storage_path=path)
            updater1 = SelfModelUpdater(self_model_store=store1)
            record = _make_growth_record(record_id="shared-001")
            proposal = updater1.create_proposal_from_growth(record)
            store1.apply_change_proposal(proposal)

            # store2 从同一路径加载
            store2 = SelfModelStore(storage_path=path)
            narratives2 = store2.get().get("growth_narratives", [])
            assert len(narratives2) >= 1, "store2 应能读到 store1 写入的内容"
            assert narratives2[0]["source_growth_record_id"] == "shared-001"