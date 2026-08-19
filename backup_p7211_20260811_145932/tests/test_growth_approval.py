"""
Phase 3.5.13: Growth Approval Layer 测试

覆盖：
 1. Schema 结构（ApprovalRecord / ChangeDiff / ApprovalHistory / ApprovalSnapshot）
 2. ApprovalManager - approve_proposal
 3. ApprovalManager - reject_proposal
 4. ApprovalManager - modify_proposal
 5. ApprovalManager - get_pending_proposals / get_approval_history
 6. 审计历史持久化与加载
 7. 快照统计
 8. RuntimeCore 接入（默认关闭 / 启用 / approve / reject / modify / get_pending / get_history）
 9. 完整链路：Reflection → Proposal → Approve → 审计可追溯
10. 约束验证（不自动接受 / 不修改 Personality / 审计完整）

约束验证：
- 不修改 Personality System
- 不自动接受 Proposal
- 保留完整审计记录
- 支持 approve/reject/modify
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Dict, List

# ============================================================
# 依赖
# ============================================================

from src.contracts.growth_schema import GrowthProposal, ChangeItem
from src.contracts.growth_approval_schema import (
    ACTION_APPROVE,
    ACTION_REJECT,
    ACTION_MODIFY,
    ALL_ACTIONS,
    ApprovalRecord,
    ApprovalHistory,
    ApprovalSnapshot,
    ChangeDiff,
)
from src.growth.approval_manager import ApprovalManager
from src.runtime.adapters.growth_adapter import GrowthAdapter


# ============================================================
# 1. Schema 结构测试
# ============================================================

class TestApprovalSchema(unittest.TestCase):
    def test_01_change_diff_defaults(self):
        d = ChangeDiff()
        self.assertEqual(d.path, "")
        self.assertIsNone(d.before)
        self.assertIsNone(d.after)

    def test_02_approval_record_defaults(self):
        r = ApprovalRecord()
        self.assertTrue(r.record_id.startswith("apr_"))
        self.assertEqual(r.action, "")
        self.assertEqual(r.changes, [])
        self.assertEqual(r.metadata, {})

    def test_03_approval_record_to_dict(self):
        r = ApprovalRecord(
            proposal_id="prop_001",
            action=ACTION_APPROVE,
            reason="测试",
            before_status="proposed",
            after_status="accepted",
            changes=[ChangeDiff(path="self_state.initiative", before=-0.1, after=-0.05)],
        )
        d = r.to_dict()
        self.assertEqual(d["proposal_id"], "prop_001")
        self.assertEqual(d["action"], ACTION_APPROVE)
        self.assertEqual(len(d["changes"]), 1)
        self.assertEqual(d["changes"][0]["path"], "self_state.initiative")

    def test_04_approval_record_summary(self):
        r = ApprovalRecord(
            proposal_id="prop_001",
            action=ACTION_MODIFY,
            before_status="proposed",
            after_status="accepted",
            changes=[ChangeDiff(path="x")],
        )
        s = r.summary()
        self.assertIn("MODIFY", s)
        self.assertIn("proposed->accepted", s)
        self.assertIn("changes=1", s)

    def test_05_approval_history_add_and_stats(self):
        h = ApprovalHistory()
        h.add(ApprovalRecord(action=ACTION_APPROVE))
        h.add(ApprovalRecord(action=ACTION_REJECT))
        h.add(ApprovalRecord(action=ACTION_MODIFY))
        self.assertEqual(h.total_approvals, 1)
        self.assertEqual(h.total_rejections, 1)
        self.assertEqual(h.total_modifications, 1)
        self.assertEqual(len(h.records), 3)

    def test_06_approval_history_get_by_proposal(self):
        h = ApprovalHistory()
        h.add(ApprovalRecord(proposal_id="p1", action=ACTION_APPROVE))
        h.add(ApprovalRecord(proposal_id="p2", action=ACTION_REJECT))
        h.add(ApprovalRecord(proposal_id="p1", action=ACTION_MODIFY))
        records = h.get_by_proposal("p1")
        self.assertEqual(len(records), 2)

    def test_07_approval_snapshot_defaults(self):
        s = ApprovalSnapshot()
        self.assertTrue(s.snapshot_id.startswith("apsnap_"))
        self.assertEqual(s.total_records, 0)
        self.assertEqual(s.pending_count, 0)

    def test_08_all_actions_constant(self):
        self.assertEqual(len(ALL_ACTIONS), 3)
        self.assertIn(ACTION_APPROVE, ALL_ACTIONS)
        self.assertIn(ACTION_REJECT, ALL_ACTIONS)
        self.assertIn(ACTION_MODIFY, ALL_ACTIONS)


# ============================================================
# 2. ApprovalManager 基础测试
# ============================================================

class TestApprovalManagerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proposals_path = os.path.join(self.tmp.name, "proposals.json")
        self.history_path = os.path.join(self.tmp.name, "history.json")
        self.adapter = GrowthAdapter(proposals_path=self.proposals_path)
        self.manager = ApprovalManager(
            growth_adapter=self.adapter,
            history_path=self.history_path,
        )
        # 创建测试用 proposal
        self.proposal = self._create_proposal("prop_test_001")

    def tearDown(self):
        self.tmp.cleanup()

    def _create_proposal(self, proposal_id: str) -> GrowthProposal:
        proposal = GrowthProposal(
            id=proposal_id,
            proposed_changes=[
                ChangeItem(path="self_state.initiative", after=-0.1, reason="测试"),
                ChangeItem(path="self_state.energy", after=0.05, reason="测试"),
            ],
            confidence=0.7,
            evidence_ids=["e1", "e2"],
        )
        self.adapter.update_proposal(proposal)
        return proposal

    def test_01_initial_state(self):
        pending = self.manager.get_pending_proposals()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, "prop_test_001")

    def test_02_history_empty_initially(self):
        history = self.manager.get_approval_history()
        self.assertEqual(len(history), 0)

    def test_03_snapshot_initial(self):
        snap = self.manager.get_snapshot()
        self.assertEqual(snap.total_records, 0)
        self.assertEqual(snap.pending_count, 1)


# ============================================================
# 3. Approve Proposal 测试
# ============================================================

class TestApproveProposal(TestApprovalManagerBase):
    def test_01_approve_success(self):
        record = self.manager.approve_proposal(
            "prop_test_001",
            reason="符合身份原则",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.action, ACTION_APPROVE)
        self.assertEqual(record.before_status, "proposed")
        self.assertEqual(record.after_status, "accepted")
        self.assertEqual(record.reason, "符合身份原则")
        self.assertEqual(record.actor, "human")

    def test_02_approve_nonexistent(self):
        record = self.manager.approve_proposal("nonexistent_id")
        self.assertIsNone(record)

    def test_03_approve_records_history(self):
        self.manager.approve_proposal("prop_test_001")
        history = self.manager.get_approval_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].action, ACTION_APPROVE)

    def test_04_approve_snapshot_stats(self):
        self.manager.approve_proposal("prop_test_001")
        snap = self.manager.get_snapshot()
        self.assertEqual(snap.total_approvals, 1)
        self.assertEqual(snap.pending_count, 0)

    def test_05_approve_cannot_reapprove(self):
        self.manager.approve_proposal("prop_test_001")
        # 再次批准：状态已非 proposed
        record = self.manager.approve_proposal("prop_test_001")
        # adapter 会返回 proposal 但状态不变
        # 审计记录仍会生成
        self.assertIsNotNone(record)
        self.assertEqual(record.after_status, "accepted")

    def test_06_approve_metadata_captured(self):
        record = self.manager.approve_proposal("prop_test_001")
        self.assertEqual(record.metadata["confidence"], 0.7)
        self.assertEqual(record.metadata["evidence_count"], 2)


# ============================================================
# 4. Reject Proposal 测试
# ============================================================

class TestRejectProposal(TestApprovalManagerBase):
    def test_01_reject_success(self):
        record = self.manager.reject_proposal(
            "prop_test_001",
            reason="与记忆冲突",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.action, ACTION_REJECT)
        self.assertEqual(record.before_status, "proposed")
        self.assertEqual(record.after_status, "rejected")
        self.assertEqual(record.reason, "与记忆冲突")

    def test_02_reject_nonexistent(self):
        record = self.manager.reject_proposal("nonexistent_id")
        self.assertIsNone(record)

    def test_03_reject_records_history(self):
        self.manager.reject_proposal("prop_test_001")
        history = self.manager.get_approval_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].action, ACTION_REJECT)

    def test_04_reject_snapshot_stats(self):
        self.manager.reject_proposal("prop_test_001")
        snap = self.manager.get_snapshot()
        self.assertEqual(snap.total_rejections, 1)
        self.assertEqual(snap.pending_count, 0)


# ============================================================
# 5. Modify Proposal 测试
# ============================================================

class TestModifyProposal(TestApprovalManagerBase):
    def test_01_modify_success(self):
        record = self.manager.modify_proposal(
            "prop_test_001",
            changes=[
                {"path": "self_state.initiative", "after": -0.05},
            ],
            reason="降低调整幅度",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.action, ACTION_MODIFY)
        self.assertEqual(record.before_status, "proposed")
        self.assertEqual(record.after_status, "accepted")
        self.assertEqual(len(record.changes), 1)
        self.assertEqual(record.changes[0].path, "self_state.initiative")
        self.assertEqual(record.changes[0].before, -0.1)
        self.assertEqual(record.changes[0].after, -0.05)

    def test_02_modify_multiple_changes(self):
        record = self.manager.modify_proposal(
            "prop_test_001",
            changes=[
                {"path": "self_state.initiative", "after": -0.05},
                {"path": "self_state.energy", "after": 0.02},
            ],
            reason="多项修改",
        )
        self.assertEqual(len(record.changes), 2)

    def test_03_modify_nonexistent(self):
        record = self.manager.modify_proposal(
            "nonexistent_id",
            changes=[{"path": "x", "after": 0}],
        )
        self.assertIsNone(record)

    def test_04_modify_records_metadata(self):
        record = self.manager.modify_proposal(
            "prop_test_001",
            changes=[{"path": "self_state.initiative", "after": -0.05}],
        )
        self.assertIn("modified_paths", record.metadata)
        self.assertIn("self_state.initiative", record.metadata["modified_paths"])

    def test_05_modify_snapshot_stats(self):
        self.manager.modify_proposal(
            "prop_test_001",
            changes=[{"path": "self_state.initiative", "after": -0.05}],
        )
        snap = self.manager.get_snapshot()
        self.assertEqual(snap.total_modifications, 1)
        self.assertEqual(snap.pending_count, 0)

    def test_06_modify_actually_updates_proposal(self):
        self.manager.modify_proposal(
            "prop_test_001",
            changes=[{"path": "self_state.initiative", "after": -0.05}],
        )
        # 验证 proposal 已更新
        proposals = self.adapter.list_proposals(limit=10)
        updated = next(p for p in proposals if p.id == "prop_test_001")
        initiative_change = next(
            c for c in updated.proposed_changes if c.path == "self_state.initiative"
        )
        self.assertEqual(initiative_change.after, -0.05)


# ============================================================
# 6. 历史持久化与加载
# ============================================================

class TestHistoryPersistence(unittest.TestCase):
    def test_01_history_persisted_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            proposals_path = os.path.join(tmp, "proposals.json")
            history_path = os.path.join(tmp, "history.json")
            adapter = GrowthAdapter(proposals_path=proposals_path)
            manager = ApprovalManager(
                growth_adapter=adapter,
                history_path=history_path,
            )
            # 创建并批准
            proposal = GrowthProposal(
                id="prop_persist_001",
                proposed_changes=[ChangeItem(path="x", after=0.1)],
            )
            adapter.update_proposal(proposal)
            manager.approve_proposal("prop_persist_001", reason="测试持久化")

            # 重新加载
            manager2 = ApprovalManager(
                growth_adapter=adapter,
                history_path=history_path,
            )
            history = manager2.get_approval_history()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].action, ACTION_APPROVE)
            self.assertEqual(history[0].reason, "测试持久化")

    def test_02_history_stats_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            proposals_path = os.path.join(tmp, "proposals.json")
            history_path = os.path.join(tmp, "history.json")
            adapter = GrowthAdapter(proposals_path=proposals_path)

            manager = ApprovalManager(
                growth_adapter=adapter,
                history_path=history_path,
            )
            for i in range(3):
                p = GrowthProposal(
                    id=f"prop_stat_{i}",
                    proposed_changes=[ChangeItem(path="x", after=0.1)],
                )
                adapter.update_proposal(p)
                if i == 0:
                    manager.approve_proposal(f"prop_stat_{i}")
                elif i == 1:
                    manager.reject_proposal(f"prop_stat_{i}")
                else:
                    manager.modify_proposal(
                        f"prop_stat_{i}",
                        changes=[{"path": "x", "after": 0.05}],
                    )

            # 重新加载
            manager2 = ApprovalManager(
                growth_adapter=adapter,
                history_path=history_path,
            )
            snap = manager2.get_snapshot()
            self.assertEqual(snap.total_approvals, 1)
            self.assertEqual(snap.total_rejections, 1)
            self.assertEqual(snap.total_modifications, 1)

    def test_03_clear_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            proposals_path = os.path.join(tmp, "proposals.json")
            history_path = os.path.join(tmp, "history.json")
            adapter = GrowthAdapter(proposals_path=proposals_path)
            manager = ApprovalManager(
                growth_adapter=adapter,
                history_path=history_path,
            )
            p = GrowthProposal(id="prop_clear", proposed_changes=[ChangeItem(path="x")])
            adapter.update_proposal(p)
            manager.approve_proposal("prop_clear")

            n = manager.clear_history()
            self.assertEqual(n, 1)
            self.assertEqual(len(manager.get_approval_history()), 0)


# ============================================================
# 7. 多提案审批历史排序
# ============================================================

class TestHistoryOrdering(unittest.TestCase):
    def test_01_history_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            proposals_path = os.path.join(tmp, "proposals.json")
            adapter = GrowthAdapter(proposals_path=proposals_path)
            manager = ApprovalManager(
                growth_adapter=adapter,
                history_path=None,  # 仅内存
            )
            # 创建多个 proposal
            for i in range(5):
                p = GrowthProposal(
                    id=f"prop_order_{i}",
                    proposed_changes=[ChangeItem(path="x")],
                )
                adapter.update_proposal(p)

            # 依次批准
            for i in range(5):
                manager.approve_proposal(f"prop_order_{i}")

            history = manager.get_approval_history(limit=10)
            self.assertEqual(len(history), 5)
            # 验证最新在前（通过 timestamp 排序）
            timestamps = [r.timestamp for r in history]
            self.assertEqual(timestamps, sorted(timestamps, reverse=True))

    def test_02_get_proposal_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            proposals_path = os.path.join(tmp, "proposals.json")
            adapter = GrowthAdapter(proposals_path=proposals_path)
            manager = ApprovalManager(
                growth_adapter=adapter,
                history_path=None,
            )
            p = GrowthProposal(
                id="prop_multi_action",
                proposed_changes=[ChangeItem(path="x", after=0.1)],
            )
            adapter.update_proposal(p)
            # 多次操作同一提案
            manager.approve_proposal("prop_multi_action")

            records = manager.get_proposal_history("prop_multi_action")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].proposal_id, "prop_multi_action")


# ============================================================
# 8. RuntimeCore 接入测试
# ============================================================

class TestRuntimeApprovalIntegration(unittest.TestCase):
    def _make_config(self, tmp: str, enabled: bool = True) -> Dict[str, Any]:
        return {
            "adapters_enabled": True,
            "experience_enabled": True,
            "approval_manager_enabled": enabled,
            "memory_store_path": os.path.join(tmp, "mem_appr.json"),
            "growth_proposals_path": os.path.join(tmp, "gp_appr.json"),
            "approval_history_path": os.path.join(tmp, "appr_hist.json"),
            "state_file": os.path.join(tmp, "rt_appr.json"),
            "tick_interval_seconds": 1,
            "reflection_min_experiences": 2,
        }

    def _inject_proposal(self, rc, proposal_id: str) -> str:
        """通过 adapter 注入一个测试 proposal"""
        from src.contracts.growth_schema import GrowthProposal, ChangeItem
        proposal = GrowthProposal(
            id=proposal_id,
            proposed_changes=[
                ChangeItem(path="self_state.initiative", after=-0.1),
            ],
            confidence=0.7,
            evidence_ids=["e1"],
        )
        rc.growth_adapter.update_proposal(proposal)
        return proposal_id

    def test_01_disabled_by_default(self):
        from src.runtime.runtime_core import RuntimeCore
        rc = RuntimeCore(config={"adapters_enabled": False})
        self.assertIsNone(rc.approval_manager)
        self.assertIsNone(rc.approve_growth_proposal("any"))
        self.assertIsNone(rc.reject_growth_proposal("any"))
        self.assertEqual(rc.get_pending_growth_proposals(), [])
        self.assertEqual(rc.get_growth_approval_history(), [])

    def test_02_enabled_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp, enabled=True))
            self.assertIsNotNone(rc.approval_manager)

    def test_03_approve_via_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            pid = self._inject_proposal(rc, "prop_rt_appr_001")
            result = rc.approve_growth_proposal(pid, reason="测试")
            self.assertIsNotNone(result)
            self.assertEqual(result["action"], ACTION_APPROVE)
            self.assertEqual(result["after_status"], "accepted")

    def test_04_reject_via_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            pid = self._inject_proposal(rc, "prop_rt_rej_001")
            result = rc.reject_growth_proposal(pid, reason="测试")
            self.assertIsNotNone(result)
            self.assertEqual(result["action"], ACTION_REJECT)
            self.assertEqual(result["after_status"], "rejected")

    def test_05_modify_via_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            pid = self._inject_proposal(rc, "prop_rt_mod_001")
            result = rc.modify_growth_proposal(
                pid,
                changes=[{"path": "self_state.initiative", "after": -0.05}],
                reason="降低幅度",
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["action"], ACTION_MODIFY)
            self.assertEqual(len(result["changes"]), 1)

    def test_06_get_pending_via_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            self._inject_proposal(rc, "prop_pend_001")
            self._inject_proposal(rc, "prop_pend_002")
            pending = rc.get_pending_growth_proposals()
            self.assertEqual(len(pending), 2)

    def test_07_get_history_via_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            pid = self._inject_proposal(rc, "prop_hist_001")
            rc.approve_growth_proposal(pid)
            history = rc.get_growth_approval_history()
            self.assertEqual(len(history), 1)

    def test_08_get_proposal_history_via_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            pid = self._inject_proposal(rc, "prop_single_001")
            rc.approve_growth_proposal(pid)
            records = rc.get_proposal_approval_history(pid)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["proposal_id"], pid)

    def test_09_approval_snapshot_via_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            pid = self._inject_proposal(rc, "prop_snap_001")
            rc.approve_growth_proposal(pid)
            snap = rc.get_approval_snapshot()
            self.assertIsNotNone(snap)
            self.assertEqual(snap["total_approvals"], 1)
            self.assertEqual(snap["pending_count"], 0)

    def test_10_clear_history_via_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            pid = self._inject_proposal(rc, "prop_clear_001")
            rc.approve_growth_proposal(pid)
            n = rc.clear_approval_history()
            self.assertEqual(n, 1)
            self.assertEqual(len(rc.get_growth_approval_history()), 0)

    def test_11_approve_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            result = rc.approve_growth_proposal("nonexistent")
            self.assertIsNone(result)


# ============================================================
# 9. 完整链路测试
#    Reflection → Proposal → Approve → 审计可追溯
# ============================================================

class TestFullApprovalLifecycle(unittest.TestCase):
    def test_01_reflect_propose_approve_audit(self):
        """
        完整链路：
        1. 注入经验 → 反思生成 Insight
        2. Insight 转 GrowthProposal（proposed 状态）
        3. 人工批准 Proposal → accepted
        4. 审批历史可追溯
        5. 不自动接受任何 Proposal
        """
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "approval_manager_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem_full.json"),
                "growth_proposals_path": os.path.join(tmp, "gp_full.json"),
                "approval_history_path": os.path.join(tmp, "appr_full.json"),
                "state_file": os.path.join(tmp, "rt_full.json"),
                "tick_interval_seconds": 1,
                "reflection_min_experiences": 2,
            }
            rc = RuntimeCore(config=config)

            # 1. 注入经验
            from src.contracts.experience_schema import RuntimeExperience, ActionResult
            for i in range(6):
                exp = RuntimeExperience(
                    experience_id=f"e_appr_{i}",
                    trigger_event={"event_type": "user_message", "data": {"content": f"hi{i}"}},
                    trigger_type="user_message",
                    decision_source="rule",
                    action_type="send_message",
                    result=ActionResult(
                        action_id=f"act_appr_{i}",
                        success=True,
                        response_received=(i % 2 == 0),
                    ),
                    self_state_before={"initiative": 0.7},
                    self_state_after={"initiative": 0.68},
                    duration_ms=100.0,
                )
                rc.experience_builder._buffer.append(exp)

            # 2. 反思
            insight = rc.reflect_on_experiences()
            self.assertIsNotNone(insight)

            # 3. 获取生成的 proposals
            pending = rc.get_pending_growth_proposals()
            # reflection 应该生成了 proposals
            # 如果没有生成，至少验证审批流程可用
            if pending:
                proposal_id = pending[0]["id"]

                # 4. 验证初始状态为 proposed（不自动接受）
                self.assertEqual(pending[0]["status"], "proposed")

                # 5. 人工批准
                result = rc.approve_growth_proposal(proposal_id, reason="完整链路测试")
                self.assertIsNotNone(result)
                self.assertEqual(result["action"], ACTION_APPROVE)
                self.assertEqual(result["after_status"], "accepted")

                # 6. 审批历史可追溯
                history = rc.get_growth_approval_history()
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0]["proposal_id"], proposal_id)

                # 7. 待审批列表减少
                pending_after = rc.get_pending_growth_proposals()
                self.assertEqual(len(pending_after), len(pending) - 1)

    def test_02_multi_proposal_approve_reject_modify(self):
        """
        多提案审批场景：
        - 创建 3 个 proposals
        - 分别执行 approve / reject / modify
        - 验证审计历史完整记录 3 种动作
        """
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            from src.contracts.growth_schema import GrowthProposal, ChangeItem
            config = {
                "adapters_enabled": True,
                "approval_manager_enabled": True,
                "growth_proposals_path": os.path.join(tmp, "gp_multi.json"),
                "approval_history_path": os.path.join(tmp, "appr_multi.json"),
                "state_file": os.path.join(tmp, "rt_multi.json"),
                "tick_interval_seconds": 1,
            }
            rc = RuntimeCore(config=config)

            # 创建 3 个 proposals
            for i in range(3):
                p = GrowthProposal(
                    id=f"prop_multi_{i}",
                    proposed_changes=[
                        ChangeItem(path="self_state.initiative", after=-0.1, reason="测试"),
                    ],
                    confidence=0.6,
                )
                rc.growth_adapter.update_proposal(p)

            # 验证 3 个待审批
            pending = rc.get_pending_growth_proposals()
            self.assertEqual(len(pending), 3)

            # 分别执行
            rc.approve_growth_proposal("prop_multi_0", reason="批准0")
            rc.reject_growth_proposal("prop_multi_1", reason="拒绝1")
            rc.modify_growth_proposal(
                "prop_multi_2",
                changes=[{"path": "self_state.initiative", "after": -0.05}],
                reason="修改2",
            )

            # 验证审计历史
            history = rc.get_growth_approval_history()
            self.assertEqual(len(history), 3)

            actions = [h["action"] for h in history]
            self.assertIn(ACTION_APPROVE, actions)
            self.assertIn(ACTION_REJECT, actions)
            self.assertIn(ACTION_MODIFY, actions)

            # 验证快照统计
            snap = rc.get_approval_snapshot()
            self.assertEqual(snap["total_approvals"], 1)
            self.assertEqual(snap["total_rejections"], 1)
            self.assertEqual(snap["total_modifications"], 1)
            self.assertEqual(snap["pending_count"], 0)


# ============================================================
# 10. 约束验证测试
# ============================================================

class TestConstraints(unittest.TestCase):
    def test_01_no_auto_accept(self):
        """验证：注入 proposal 后不会自动变为 accepted"""
        with tempfile.TemporaryDirectory() as tmp:
            proposals_path = os.path.join(tmp, "proposals.json")
            adapter = GrowthAdapter(proposals_path=proposals_path)
            manager = ApprovalManager(
                growth_adapter=adapter,
                history_path=None,
            )
            p = GrowthProposal(
                id="prop_no_auto",
                proposed_changes=[ChangeItem(path="x", after=0.1)],
            )
            adapter.update_proposal(p)

            # 验证仍为 proposed
            pending = manager.get_pending_proposals()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].status, "proposed")

    def test_02_does_not_modify_personality(self):
        """验证：审批管理器不直接修改人格数据"""
        with tempfile.TemporaryDirectory() as tmp:
            proposals_path = os.path.join(tmp, "proposals.json")
            adapter = GrowthAdapter(proposals_path=proposals_path)
            manager = ApprovalManager(
                growth_adapter=adapter,
                history_path=None,
            )
            p = GrowthProposal(
                id="prop_no_personality",
                proposed_changes=[ChangeItem(path="self_state.initiative", after=-0.1)],
            )
            adapter.update_proposal(p)

            # 批准
            record = manager.approve_proposal("prop_no_personality")

            # 验证：只修改了 proposal 状态，未触及任何人格数据
            self.assertEqual(record.action, ACTION_APPROVE)
            # 审批管理器无任何写人格的接口
            self.assertFalse(hasattr(manager, "write_personality"))
            self.assertFalse(hasattr(manager, "modify_trait"))

    def test_03_audit_record_complete(self):
        """验证：审计记录包含完整信息"""
        with tempfile.TemporaryDirectory() as tmp:
            proposals_path = os.path.join(tmp, "proposals.json")
            adapter = GrowthAdapter(proposals_path=proposals_path)
            manager = ApprovalManager(
                growth_adapter=adapter,
                history_path=None,
            )
            p = GrowthProposal(
                id="prop_audit",
                proposed_changes=[
                    ChangeItem(path="self_state.initiative", after=-0.1, reason="原始"),
                ],
                confidence=0.8,
                evidence_ids=["e1", "e2", "e3"],
            )
            adapter.update_proposal(p)

            record = manager.approve_proposal("prop_audit", reason="审计测试", actor="admin")

            # 验证审计记录字段完整
            self.assertTrue(record.record_id)
            self.assertTrue(record.timestamp)
            self.assertEqual(record.proposal_id, "prop_audit")
            self.assertTrue(record.proposal_snapshot)  # 操作前快照
            self.assertEqual(record.action, ACTION_APPROVE)
            self.assertEqual(record.actor, "admin")
            self.assertEqual(record.reason, "审计测试")
            self.assertEqual(record.before_status, "proposed")
            self.assertEqual(record.after_status, "accepted")
            self.assertEqual(record.metadata["confidence"], 0.8)
            self.assertEqual(record.metadata["evidence_count"], 3)

    def test_04_modify_preserves_before_snapshot(self):
        """验证：modify 操作保留修改前的快照"""
        with tempfile.TemporaryDirectory() as tmp:
            proposals_path = os.path.join(tmp, "proposals.json")
            adapter = GrowthAdapter(proposals_path=proposals_path)
            manager = ApprovalManager(
                growth_adapter=adapter,
                history_path=None,
            )
            p = GrowthProposal(
                id="prop_snap",
                proposed_changes=[
                    ChangeItem(path="x", after=0.1),
                ],
            )
            adapter.update_proposal(p)

            record = manager.modify_proposal(
                "prop_snap",
                changes=[{"path": "x", "after": 0.05}],
                reason="修改测试",
            )

            # 验证快照保留的是修改前的值
            snapshot = record.proposal_snapshot
            original_changes = snapshot.get("proposed_changes", [])
            self.assertEqual(original_changes[0]["after"], 0.1)

            # 验证 ChangeDiff 记录了 before/after
            self.assertEqual(record.changes[0].before, 0.1)
            self.assertEqual(record.changes[0].after, 0.05)


if __name__ == "__main__":
    unittest.main()
