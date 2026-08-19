# -*- coding: utf-8 -*-
"""
src/admin/runtime_selfmodel_bridge_test.py

Phase 3.5.3 Step 3: Runtime → SelfModel 桥接方案（Dry Run 仿真）

目标：
- 模拟未来 Runtime → SelfModel 的真实链路
- 不接真实 Runtime（不 import src.runtime.runtime_core / runtime_bridge / orchestrator）
- 通过 MockRuntimeContext + ApprovalGate 仿真 Runtime 端
- 验证 AuditedSelfModelConsumer 作为桥接落地点的可行性
- 为后续 RuntimeCore 接入提供契约参考

仿真链路：

    MockRuntimeContext
        ↓
    GrowthProposal (dict)
        ↓
    ApprovalGate.check()
        ↓ (accepted)
    AuditedSelfModelConsumer.process()
        ↓
    SelfModel (beliefs/history/reflection JSONL)
        ↓
    Audit (consumer_audit.jsonl)

约束（与之前两步一致）：
- 不修改 src/runtime/* / src/orchestrator.py / src/growth/* / src/personality/*
- 不修改 selfmodel_consumer.py / selfmodel_consumer_audit.py
- 仅本文件为新增，且只做仿真 / 测试

使用方式：
- 独立运行：python src/admin/runtime_selfmodel_bridge_test.py
- pytest：python -m pytest src/admin/runtime_selfmodel_bridge_test.py -v
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 允许在 src/admin/ 内直接执行
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 反依赖基线（与审计测试一致）
# ============================================================

FORBIDDEN_MODULES = {
    "src.runtime.runtime_core",
    "src.runtime.runtime_bridge",
    "src.orchestrator",
}


# ============================================================
# 桥接仿真组件
# ============================================================

@dataclass
class MockRuntimeContext:
    """
    仿真 RuntimeContext（不接真实 Runtime）。

    字段：
    - runtime_id: 仿真 Runtime 实例 ID
    - proposal: GrowthProposal dict（可空）
    - proposal_id: 显式 proposal_id（可由 proposal 推导）
    - approval_status: 审批状态，必须是 "accepted" / "rejected" / None / 其他
    - approval_meta: 审批元信息（审批人/理由/时间）
    - session_id: 会话 ID
    """
    runtime_id: str = "mock_runtime_001"
    proposal: Optional[Dict[str, Any]] = None
    proposal_id: Optional[str] = None
    approval_status: Optional[str] = None
    approval_meta: Dict[str, Any] = field(default_factory=dict)
    session_id: str = "session_001"

    def __post_init__(self) -> None:
        # 推导 proposal_id
        if self.proposal_id is None and isinstance(self.proposal, dict):
            if "proposal_id" in self.proposal:
                self.proposal_id = str(self.proposal.get("proposal_id") or "")
            elif "id" in self.proposal:
                self.proposal_id = str(self.proposal.get("id") or "")


# ============================================================
# ApprovalGate
# ============================================================

class ApprovalDecision:
    """审批决策结果。"""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MISSING_STATUS = "missing_status"
    UNKNOWN_STATUS = "unknown_status"
    NO_PROPOSAL = "no_proposal"

    def __init__(self, allowed: bool, decision: str, reason: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
        self.allowed = bool(allowed)
        self.decision = str(decision)
        self.reason = str(reason)
        self.meta = dict(meta or {})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "decision": self.decision,
            "reason": self.reason,
            "meta": self.meta,
        }


class ApprovalGate:
    """
    仿真 Approval Check。

    规则：
    - 缺失 proposal → no_proposal（不安全 → 拒绝）
    - 缺失 approval_status → missing_status（安全失败 → 拒绝）
    - approval_status == "accepted" → 接受
    - approval_status == "rejected" → 拒绝
    - 其他 → unknown_status（拒绝）
    """

    ACCEPTED = ApprovalDecision.ACCEPTED
    REJECTED = ApprovalDecision.REJECTED

    def check(self, ctx: MockRuntimeContext) -> ApprovalDecision:
        # 1) proposal 必须存在
        if ctx.proposal is None or not isinstance(ctx.proposal, dict):
            return ApprovalDecision(False, ApprovalDecision.NO_PROPOSAL, "proposal is None or not dict")

        # 2) approval_status 必须存在
        if ctx.approval_status is None:
            return ApprovalDecision(False, ApprovalDecision.MISSING_STATUS, "approval_status is None")

        # 3) status 合法性
        if ctx.approval_status == "accepted":
            return ApprovalDecision(True, ApprovalDecision.ACCEPTED, "approved", dict(ctx.approval_meta))
        if ctx.approval_status == "rejected":
            return ApprovalDecision(False, ApprovalDecision.REJECTED, "rejected by approval", dict(ctx.approval_meta))
        return ApprovalDecision(False, ApprovalDecision.UNKNOWN_STATUS, f"unknown status: {ctx.approval_status}")


# ============================================================
# RuntimeSelfModelBridge（仿真桥接）
# ============================================================

class RuntimeSelfModelBridge:
    """
    仿真 Runtime → SelfModel 桥接（Dry Run）。

    行为：
    1. 接收 MockRuntimeContext
    2. 通过 ApprovalGate 决策
    3. accepted → 调用 AuditedSelfModelConsumer.process()
    4. rejected / missing → 直接拒绝，不触发 SelfModel
    5. 不论结果如何，返回结构化 BridgeResult
    """

    def __init__(self, audited_consumer: Any, approval_gate: Optional[ApprovalGate] = None) -> None:
        self._consumer = audited_consumer
        self._gate = approval_gate or ApprovalGate()
        # 桥接自身不直接落 audit；audit 由 wrapped consumer 负责
        # 但 bridge 自身维护一个会话级 log（仅供测试观察）
        self._log: List[Dict[str, Any]] = []

    @property
    def log(self) -> List[Dict[str, Any]]:
        return list(self._log)

    def forward(self, ctx: MockRuntimeContext) -> Dict[str, Any]:
        """
        主入口：把 ctx 推给 SelfModel（如果审批通过）。

        Returns:
            {
                "runtime_id": str,
                "session_id": str,
                "proposal_id": str|None,
                "allowed": bool,
                "decision": str,
                "reason": str,
                "result": dict|None,   # audited_consumer.process() 的结果
                "error": str|None,
            }
        """
        bridge_result: Dict[str, Any] = {
            "runtime_id": ctx.runtime_id,
            "session_id": ctx.session_id,
            "proposal_id": ctx.proposal_id,
            "allowed": False,
            "decision": "",
            "reason": "",
            "result": None,
            "error": None,
        }

        # 1) Approval
        decision = self._gate.check(ctx)
        bridge_result["decision"] = decision.decision
        bridge_result["reason"] = decision.reason

        if not decision.allowed:
            self._log.append(bridge_result)
            return bridge_result

        bridge_result["allowed"] = True

        # 2) Forward to audited consumer
        if self._consumer is None:
            bridge_result["error"] = "consumer_unavailable"
            self._log.append(bridge_result)
            return bridge_result

        try:
            res = self._consumer.process(ctx.proposal)
            bridge_result["result"] = res
        except Exception as e:
            bridge_result["error"] = f"consumer_exception: {e}"

        self._log.append(bridge_result)
        return bridge_result

    def forward_batch(self, contexts: List[MockRuntimeContext]) -> List[Dict[str, Any]]:
        return [self.forward(c) for c in contexts]


# ============================================================
# 桥接工厂（测试 / 仿真用）
# ============================================================

def create_simulated_bridge(
    *,
    data_dir: Optional[str] = None,
    audit_dir: Optional[str] = None,
    approval_gate: Optional[ApprovalGate] = None,
) -> Tuple[RuntimeSelfModelBridge, Any, Any]:
    """
    一步创建仿真桥接：
    - Real SelfModelConsumer
    - Real AuditedSelfModelConsumer（wrapper）
    - RuntimeSelfModelBridge（仿真）
    """
    from src.admin.selfmodel_consumer import SelfModelConsumer
    from src.admin.selfmodel_consumer_audit import (
        AuditedSelfModelConsumer,
        SelfModelConsumerAudit,
    )

    consumer = SelfModelConsumer(data_dir=data_dir)
    audit = SelfModelConsumerAudit(audit_dir=audit_dir)
    audited = AuditedSelfModelConsumer(consumer, audit)
    bridge = RuntimeSelfModelBridge(audited, approval_gate=approval_gate)
    return bridge, consumer, audit


# ============================================================
# T1: accepted proposal 可以进入 SelfModel
# ============================================================

class TestAcceptedProposal:
    """accepted proposal 应正常进入 SelfModel，audit 落 1 条 success 记录。"""

    def test_accepted_proposal_enters_selfmodel(self):
        tmp_data = Path(tempfile.mkdtemp(prefix="bridge_accepted_data_"))
        tmp_audit = Path(tempfile.mkdtemp(prefix="bridge_accepted_audit_"))
        try:
            bridge, consumer, audit = create_simulated_bridge(
                data_dir=str(tmp_data),
                audit_dir=str(tmp_audit),
            )
            try:
                proposal = {
                    "id": "bridge_accepted_001",
                    "proposed_changes": [
                        {"path": "personality.traits.curiosity", "before": 0.5, "after": 0.55}
                    ],
                    "confidence": 0.85,
                    "evidence_ids": ["e1"],
                    "evaluator_meta": {"reason_summary": "bridge_test_accepted"},
                }
                ctx = MockRuntimeContext(
                    runtime_id="mock_001",
                    proposal=proposal,
                    approval_status="accepted",
                    approval_meta={"approver": "self", "confidence": 0.85},
                )
                br = bridge.forward(ctx)

                # Bridge result
                assert br["allowed"] is True
                assert br["decision"] == "accepted"
                assert br["proposal_id"] == "bridge_accepted_001"
                assert br["result"] is not None

                # Underlying consumer result
                result = br["result"]
                assert result["pcr_generated"] is True
                assert result["selfmodel_updated"] is True
                assert result["dedup_skipped"] is False

                # Audit 落 1 条 success
                stats = audit.get_stats()
                assert stats["total_processed"] == 1
                assert stats["success_count"] == 1
                assert stats["failed_count"] == 0
                assert audit.has_processed("bridge_accepted_001") is True
                assert audit.has_succeeded("bridge_accepted_001") is True
            finally:
                try:
                    bridge._consumer.consumer.close()
                except Exception:
                    pass
                try:
                    bridge._consumer.audit.close()
                except Exception:
                    pass
        finally:
            shutil.rmtree(tmp_data, ignore_errors=True)
            shutil.rmtree(tmp_audit, ignore_errors=True)


# ============================================================
# T2: rejected proposal 被阻止
# ============================================================

class TestRejectedProposal:
    """rejected proposal 应被阻止，不进入 SelfModel，不写入 audit。"""

    def test_rejected_proposal_blocked(self):
        tmp_data = Path(tempfile.mkdtemp(prefix="bridge_rejected_data_"))
        tmp_audit = Path(tempfile.mkdtemp(prefix="bridge_rejected_audit_"))
        try:
            bridge, consumer, audit = create_simulated_bridge(
                data_dir=str(tmp_data),
                audit_dir=str(tmp_audit),
            )
            try:
                proposal = {
                    "id": "bridge_rejected_001",
                    "proposed_changes": [
                        {"path": "personality.traits.warmth", "before": 0.6, "after": 0.9}
                    ],
                    "confidence": 0.4,
                    "evidence_ids": [],
                    "evaluator_meta": {"reason_summary": "should_be_blocked"},
                }
                ctx = MockRuntimeContext(
                    runtime_id="mock_001",
                    proposal=proposal,
                    approval_status="rejected",
                    approval_meta={"approver": "safety", "reason": "low_confidence"},
                )
                br = bridge.forward(ctx)

                # Bridge 拒绝
                assert br["allowed"] is False
                assert br["decision"] == "rejected"
                assert br["result"] is None
                assert "rejected" in br["reason"].lower() or "approval" in br["reason"].lower()

                # 不进入 SelfModel：审计应无记录
                stats = audit.get_stats()
                assert stats["total_processed"] == 0
                assert audit.has_processed("bridge_rejected_001") is False
            finally:
                try:
                    bridge._consumer.consumer.close()
                except Exception:
                    pass
                try:
                    bridge._consumer.audit.close()
                except Exception:
                    pass
        finally:
            shutil.rmtree(tmp_data, ignore_errors=True)
            shutil.rmtree(tmp_audit, ignore_errors=True)


# ============================================================
# T3: missing status 安全失败
# ============================================================

class TestMissingStatus:
    """approval_status 缺失 → 安全失败（拒绝 + 记录决策但不进 SelfModel）。"""

    def test_missing_status_safe_fail(self):
        tmp_data = Path(tempfile.mkdtemp(prefix="bridge_missing_data_"))
        tmp_audit = Path(tempfile.mkdtemp(prefix="bridge_missing_audit_"))
        try:
            bridge, consumer, audit = create_simulated_bridge(
                data_dir=str(tmp_data),
                audit_dir=str(tmp_audit),
            )
            try:
                proposal = {
                    "id": "bridge_missing_001",
                    "proposed_changes": [
                        {"path": "personality.traits.curiosity", "before": 0.5, "after": 0.55}
                    ],
                    "confidence": 0.85,
                    "evidence_ids": ["e1"],
                }
                ctx = MockRuntimeContext(
                    runtime_id="mock_001",
                    proposal=proposal,
                    approval_status=None,  # 关键：缺失
                )
                br = bridge.forward(ctx)

                # 安全失败
                assert br["allowed"] is False
                assert br["decision"] == "missing_status"
                assert br["result"] is None
                assert "missing" in br["reason"].lower() or "approval_status" in br["reason"].lower()

                # 不进入 SelfModel
                stats = audit.get_stats()
                assert stats["total_processed"] == 0
            finally:
                try:
                    bridge._consumer.consumer.close()
                except Exception:
                    pass
                try:
                    bridge._consumer.audit.close()
                except Exception:
                    pass
        finally:
            shutil.rmtree(tmp_data, ignore_errors=True)
            shutil.rmtree(tmp_audit, ignore_errors=True)

    def test_unknown_status_also_safe_fails(self):
        """非 accepted/rejected 的 status 同样被拒绝（安全优先）。"""
        tmp_data = Path(tempfile.mkdtemp(prefix="bridge_unknown_data_"))
        tmp_audit = Path(tempfile.mkdtemp(prefix="bridge_unknown_audit_"))
        try:
            bridge, consumer, audit = create_simulated_bridge(
                data_dir=str(tmp_data),
                audit_dir=str(tmp_audit),
            )
            try:
                proposal = {"id": "bridge_unknown_001", "proposed_changes": []}
                ctx = MockRuntimeContext(
                    proposal=proposal,
                    approval_status="maybe",
                )
                br = bridge.forward(ctx)
                assert br["allowed"] is False
                assert br["decision"] == "unknown_status"
                stats = audit.get_stats()
                assert stats["total_processed"] == 0
            finally:
                try:
                    bridge._consumer.consumer.close()
                except Exception:
                    pass
                try:
                    bridge._consumer.audit.close()
                except Exception:
                    pass
        finally:
            shutil.rmtree(tmp_data, ignore_errors=True)
            shutil.rmtree(tmp_audit, ignore_errors=True)

    def test_no_proposal_safe_fails(self):
        """proposal 为 None 时也应安全失败。"""
        tmp_data = Path(tempfile.mkdtemp(prefix="bridge_noprop_data_"))
        tmp_audit = Path(tempfile.mkdtemp(prefix="bridge_noprop_audit_"))
        try:
            bridge, consumer, audit = create_simulated_bridge(
                data_dir=str(tmp_data),
                audit_dir=str(tmp_audit),
            )
            try:
                ctx = MockRuntimeContext(
                    proposal=None,
                    approval_status="accepted",
                )
                br = bridge.forward(ctx)
                assert br["allowed"] is False
                assert br["decision"] == "no_proposal"
                stats = audit.get_stats()
                assert stats["total_processed"] == 0
            finally:
                try:
                    bridge._consumer.consumer.close()
                except Exception:
                    pass
                try:
                    bridge._consumer.audit.close()
                except Exception:
                    pass
        finally:
            shutil.rmtree(tmp_data, ignore_errors=True)
            shutil.rmtree(tmp_audit, ignore_errors=True)


# ============================================================
# T4: audit 正确记录
# ============================================================

class TestAuditRecording:
    """桥接流程应正确把每次 accepted proposal 写入 audit。"""

    def test_audit_records_each_accepted_proposal(self):
        tmp_data = Path(tempfile.mkdtemp(prefix="bridge_audit_data_"))
        tmp_audit = Path(tempfile.mkdtemp(prefix="bridge_audit_audit_"))
        try:
            bridge, consumer, audit = create_simulated_bridge(
                data_dir=str(tmp_data),
                audit_dir=str(tmp_audit),
            )
            try:
                proposals = [
                    {
                        "id": f"bridge_audit_{i:03d}",
                        "proposed_changes": [
                            {"path": f"personality.traits.trait_{i}", "before": 0.5, "after": 0.55 + i * 0.01}
                        ],
                        "confidence": 0.8,
                        "evidence_ids": [f"e_{i}"],
                    }
                    for i in range(5)
                ]

                for i, p in enumerate(proposals):
                    ctx = MockRuntimeContext(
                        runtime_id="mock_001",
                        proposal=p,
                        approval_status="accepted",
                    )
                    br = bridge.forward(ctx)
                    assert br["allowed"] is True

                # audit 统计
                stats = audit.get_stats()
                assert stats["total_processed"] == 5
                assert stats["success_count"] == 5
                assert stats["failed_count"] == 0
                assert stats["unique_proposal_ids"] == 5
                for i in range(5):
                    assert audit.has_processed(f"bridge_audit_{i:03d}") is True
            finally:
                try:
                    bridge._consumer.consumer.close()
                except Exception:
                    pass
                try:
                    bridge._consumer.audit.close()
                except Exception:
                    pass
        finally:
            shutil.rmtree(tmp_data, ignore_errors=True)
            shutil.rmtree(tmp_audit, ignore_errors=True)

    def test_audit_does_not_record_rejected(self):
        """被拒绝的 proposal 不应污染 audit。"""
        tmp_data = Path(tempfile.mkdtemp(prefix="bridge_audit_rej_data_"))
        tmp_audit = Path(tempfile.mkdtemp(prefix="bridge_audit_rej_audit_"))
        try:
            bridge, consumer, audit = create_simulated_bridge(
                data_dir=str(tmp_data),
                audit_dir=str(tmp_audit),
            )
            try:
                for i, status in enumerate(["accepted", "rejected", "accepted", None]):
                    proposal = {
                        "id": f"bridge_mix_{i}",
                        "proposed_changes": [
                            {"path": "personality.traits.x", "before": 0.5, "after": 0.55}
                        ],
                    }
                    ctx = MockRuntimeContext(
                        proposal=proposal,
                        approval_status=status,
                    )
                    bridge.forward(ctx)

                stats = audit.get_stats()
                # 仅 2 个 accepted 进 audit
                assert stats["total_processed"] == 2
                assert stats["success_count"] == 2
                assert audit.has_processed("bridge_mix_0") is True
                assert audit.has_processed("bridge_mix_1") is False
                assert audit.has_processed("bridge_mix_2") is True
                assert audit.has_processed("bridge_mix_3") is False
            finally:
                try:
                    bridge._consumer.consumer.close()
                except Exception:
                    pass
                try:
                    bridge._consumer.audit.close()
                except Exception:
                    pass
        finally:
            shutil.rmtree(tmp_data, ignore_errors=True)
            shutil.rmtree(tmp_audit, ignore_errors=True)


# ============================================================
# T5: restart simulation
# ============================================================

class TestRestartSimulation:
    """仿真 Runtime 重启：重新实例化 consumer/audit/bridge 后，旧 proposal 应被识别。"""

    def test_restart_recognizes_already_processed_proposal(self):
        tmp_data = Path(tempfile.mkdtemp(prefix="bridge_restart_data_"))
        tmp_audit = Path(tempfile.mkdtemp(prefix="bridge_restart_audit_"))
        try:
            # Phase 1: 实例 A 处理 proposal
            bridge_a, consumer_a, audit_a = create_simulated_bridge(
                data_dir=str(tmp_data),
                audit_dir=str(tmp_audit),
            )
            try:
                proposal = {
                    "id": "bridge_restart_001",
                    "proposed_changes": [
                        {"path": "personality.traits.curiosity", "before": 0.5, "after": 0.6}
                    ],
                    "confidence": 0.9,
                    "evidence_ids": ["e1"],
                }
                ctx = MockRuntimeContext(
                    proposal=proposal,
                    approval_status="accepted",
                )
                br1 = bridge_a.forward(ctx)
                assert br1["allowed"] is True
                assert br1["result"]["selfmodel_updated"] is True
                stats_a = audit_a.get_stats()
                assert stats_a["total_processed"] == 1
            finally:
                try:
                    bridge_a._consumer.consumer.close()
                except Exception:
                    pass
                try:
                    bridge_a._consumer.audit.close()
                except Exception:
                    pass

            # Phase 2: 实例 B（仿真 Runtime 重启）
            bridge_b, consumer_b, audit_b = create_simulated_bridge(
                data_dir=str(tmp_data),
                audit_dir=str(tmp_audit),
            )
            try:
                # 同一 proposal 再次进入
                proposal2 = {
                    "id": "bridge_restart_001",
                    "proposed_changes": [
                        {"path": "personality.traits.curiosity", "before": 0.5, "after": 0.6}
                    ],
                    "confidence": 0.9,
                    "evidence_ids": ["e1"],
                }
                ctx2 = MockRuntimeContext(
                    proposal=proposal2,
                    approval_status="accepted",
                )
                br2 = bridge_b.forward(ctx2)

                # 1) audit 层跨重启可识别（append-only 持久）
                assert audit_b.has_processed("bridge_restart_001") is True

                # 2) 新 consumer 进程内 _processed_ids 为空（重启后状态丢失），
                #    因此 process-level dedup 不触发，consumer 实际重新 apply
                #    这是当前 SelfModelConsumer 的设计：进程内 dedup + audit 层跨进程识别
                assert br2["result"]["dedup_skipped"] is False
                assert br2["result"]["selfmodel_updated"] is True

                # 3) audit 累加 1 条新记录（total_processed = 2）
                stats_b = audit_b.get_stats()
                assert stats_b["total_processed"] == 2
                assert stats_b["dedup_skipped_count"] == 0
            finally:
                try:
                    bridge_b._consumer.consumer.close()
                except Exception:
                    pass
                try:
                    bridge_b._consumer.audit.close()
                except Exception:
                    pass
        finally:
            shutil.rmtree(tmp_data, ignore_errors=True)
            shutil.rmtree(tmp_audit, ignore_errors=True)


# ============================================================
# T6: 无 RuntimeCore 修改
# ============================================================

class TestNoRuntimeCoreModification:
    """本文件 / 桥接组件不应 import / 依赖 RuntimeCore。"""

    def test_module_source_no_runtime_imports(self):
        text = Path(__file__).read_text(encoding="utf-8")
        # 扫描所有 import / from import
        imports: List[str] = []
        for m in re.finditer(r"^\s*from\s+([\w.]+)\s+import\s+", text, re.MULTILINE):
            imports.append(m.group(1))
        for m in re.finditer(r"^\s*import\s+([\w.]+)", text, re.MULTILINE):
            imports.append(m.group(1))

        for mod_name in imports:
            for forbidden in FORBIDDEN_MODULES:
                assert not mod_name.startswith(forbidden), (
                    f"runtime_selfmodel_bridge_test.py 不应 import {forbidden}（发现 {mod_name!r}）"
                )

    def test_simulation_does_not_load_runtime(self):
        """桥接仿真创建后，sys.modules 不应包含 RuntimeCore。"""
        for m in list(sys.modules.keys()):
            if m in FORBIDDEN_MODULES or m.startswith("src.growth") or m.startswith("src.personality"):
                del sys.modules[m]

        tmp_data = Path(tempfile.mkdtemp(prefix="bridge_norun_data_"))
        tmp_audit = Path(tempfile.mkdtemp(prefix="bridge_norun_audit_"))
        try:
            bridge, consumer, audit = create_simulated_bridge(
                data_dir=str(tmp_data),
                audit_dir=str(tmp_audit),
            )
            try:
                # 跑一次完整 forward
                proposal = {
                    "id": "norun_001",
                    "proposed_changes": [
                        {"path": "personality.traits.x", "before": 0.5, "after": 0.55}
                    ],
                }
                ctx = MockRuntimeContext(
                    proposal=proposal,
                    approval_status="accepted",
                )
                br = bridge.forward(ctx)
                assert br["allowed"] is True
            finally:
                try:
                    bridge._consumer.consumer.close()
                except Exception:
                    pass
                try:
                    bridge._consumer.audit.close()
                except Exception:
                    pass

            loaded = set(sys.modules.keys())
            for forbidden in FORBIDDEN_MODULES:
                assert forbidden not in loaded, (
                    f"运行桥接仿真不应触发 {forbidden} 加载"
                )
        finally:
            shutil.rmtree(tmp_data, ignore_errors=True)
            shutil.rmtree(tmp_audit, ignore_errors=True)


# ============================================================
# 独立运行入口（Dry Run 演示）
# ============================================================

def _dry_run() -> int:
    """非 pytest 入口：演示整条桥接链路。"""
    tmp_data = Path(tempfile.mkdtemp(prefix="bridge_dryrun_data_"))
    tmp_audit = Path(tempfile.mkdtemp(prefix="bridge_dryrun_audit_"))
    try:
        bridge, consumer, audit = create_simulated_bridge(
            data_dir=str(tmp_data),
            audit_dir=str(tmp_audit),
        )
        try:
            cases = [
                ("accepted", "dr_001", True),
                ("rejected", "dr_002", False),
                (None, "dr_003", False),
                ("accepted", "dr_004", True),
            ]
            for status, pid, expected_allowed in cases:
                proposal = {
                    "id": pid,
                    "proposed_changes": [
                        {"path": f"personality.traits.t_{pid}", "before": 0.5, "after": 0.55}
                    ],
                }
                ctx = MockRuntimeContext(
                    runtime_id="dryrun",
                    proposal=proposal,
                    approval_status=status,
                )
                br = bridge.forward(ctx)
                print(f"[dry-run] {pid} status={status!r} → allowed={br['allowed']} decision={br['decision']!r}")
                assert br["allowed"] == expected_allowed, f"{pid}: expected {expected_allowed}, got {br['allowed']}"

            stats = audit.get_stats()
            print(f"[dry-run] final audit stats: {json.dumps(stats, ensure_ascii=False, indent=2)}")
        finally:
            try:
                bridge._consumer.consumer.close()
            except Exception:
                pass
            try:
                bridge._consumer.audit.close()
            except Exception:
                pass
    finally:
        shutil.rmtree(tmp_data, ignore_errors=True)
        shutil.rmtree(tmp_audit, ignore_errors=True)
    print("[dry-run] OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_dry_run())
    except AssertionError as e:
        print(f"[dry-run] FAILED: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"[dry-run] ERROR: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(2)
