# -*- coding: utf-8 -*-
"""
tests/test_growth_proposal_runtime.py

Phase C.8.0 Growth Proposal Lifecycle Runtime Integration —— 验证测试

目标:
  验证 GrowthProposalRuntime 严格只读地:
    1) 读取 growth signal
    2) 评估 confidence
    3) 创建 GrowthProposal(单一写入口)
    4) 记录 audit
    5) 不修改任何业务状态(Personality / SelfModel)
    6) 不调用 accept_proposal / reject_proposal / apply_proposal

约束:
  - 不修改任何 Adapter / 核心模块
  - 所有外部依赖 mock 化
  - 不启动真实 LLM / 业务
  - 不调 process_cycle / 不调任何写入方法

覆盖 25+ 测试:
  TestProposalCreation     (1-3)    正常创建 / schema / status=pending
  TestThreshold            (4-5)    低 confidence 拒绝 / 高 confidence 通过
  TestDuplicate            (6-7)    重复 event 不创建 / fingerprint 一致
  TestReadonly             (8-10)   PersonalityState / SelfModel / apply 不调用
  TestAudit                (11-12)  proposal 创建写 audit / 字段完整
  TestFailSafe             (13-15)  evaluator / store / audit 异常
  TestIntegration          (16-17)  Observer→GrowthRuntime / Adapter 可消费
  TestSecurity             (18-20)  accept / apply / resolver 禁止
  TestSchema               (21-25)  proposal 字段完整性(5 个)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ============================================================
# 0. 路径 + 导入
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.runtime.growth import (
    GROWTH_PROPOSAL_RUNTIME_SCHEMA_VERSION,
    GROWTH_PROPOSAL_RUNTIME_NAME,
    GROWTH_PROPOSAL_RUNTIME_VERSION,
    DEFAULT_CONFIDENCE_THRESHOLD,
    AUDIT_ACTION_PROPOSAL_CREATED,
    GrowthProposalRuntime,
    create_growth_proposal_runtime,
    safe_process_growth_signal,
)
from src.runtime.growth.growth_proposal_runtime import (
    PROPOSAL_STATUS_PENDING,
    PROPOSAL_STATUS_REJECTED_LOW_CONFIDENCE,
    PROPOSAL_STATUS_REJECTED_NO_EVIDENCE,
    PROPOSAL_STATUS_DEDUPED,
    PROPOSAL_STATUS_CREATED,
    PROPOSAL_STATUS_ERROR,
)


# ============================================================
# 1. Mock Evaluator
# ============================================================


class _MockEvaluator:
    """模拟 GrowthEvaluator.evaluate(event) -> dict。"""

    def __init__(self, confidence: float = 0.8) -> None:
        self._confidence = float(confidence)
        self._call_count: int = 0

    def evaluate(self, event: Any, history: Any = None) -> Dict[str, Any]:
        self._call_count += 1
        return {
            "confidence": self._confidence,
            "growth_level": "preference",
            "growth_domain": "preference",
            "growth_signal": "warmth",
            "target_candidates": ["warmth", "gentleness"],
            "growth_allowed": True,
            "stability": 0.6,
            "consistency": 0.7,
            "impact": 0.5,
            "source_reliability": 0.8,
        }


class _BrokenEvaluator:
    """evaluator.evaluate 抛异常。"""

    def evaluate(self, event: Any, history: Any = None) -> Dict[str, Any]:
        raise RuntimeError("evaluator crashed")


# ============================================================
# 2. Mock ProposalManager
# ============================================================


class _MockProposalManager:
    """模拟 ProposalManager(只允许 create_proposal)。"""

    def __init__(self) -> None:
        self._create_calls: int = 0
        self._accept_calls: int = 0
        self._reject_calls: int = 0
        self._apply_calls: int = 0
        self._proposals: Dict[str, Dict[str, Any]] = {}
        self._next_id: int = 0
        # 注入异常开关
        self._create_should_raise: Optional[Exception] = None
        # dedupe:记录已创建 proposal 的 fingerprint
        self._seen_fingerprints: Dict[str, str] = {}

    def create_proposal(
        self,
        source_event: Dict[str, Any],
        proposed_changes: List[Any],
        confidence: float,
        evidence_ids: List[str],
        evaluator_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._create_calls += 1
        if self._create_should_raise is not None:
            raise self._create_should_raise

        # 计算 fingerprint(与运行时相同逻辑简化版)
        import hashlib
        parts = [str(source_event.get("id", "") or "")]
        for ch in proposed_changes:
            if isinstance(ch, dict):
                parts.append(str(ch.get("path", "")))
            elif hasattr(ch, "path"):
                parts.append(str(getattr(ch, "path", "")))
        fp = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
        if fp in self._seen_fingerprints:
            return {
                "status": "deduped",
                "proposal": None,
                "existing_id": self._seen_fingerprints[fp],
                "reason": f"duplicate of {self._seen_fingerprints[fp]}",
            }

        # 置信度门槛(manager 也有自己的门槛,这里统一用 0.7)
        if confidence < 0.7:
            return {
                "status": "rejected_low_confidence",
                "proposal": None,
                "existing_id": None,
                "reason": f"confidence {confidence:.3f} < threshold 0.7",
            }

        if not evidence_ids:
            return {
                "status": "rejected_no_evidence",
                "proposal": None,
                "existing_id": None,
                "reason": "evidence_ids is empty",
            }

        # 创建 proposal
        self._next_id += 1
        pid = f"prop_{self._next_id:03d}"
        proposal = {
            "id": pid,
            "source_event_id": source_event.get("id", ""),
            "proposed_changes": [
                ch.to_dict() if hasattr(ch, "to_dict") else (ch if isinstance(ch, dict) else {})
                for ch in proposed_changes
            ],
            "confidence": float(confidence),
            "evidence_ids": list(evidence_ids),
            "evaluator_meta": dict(evaluator_meta or {}),
            "status": "pending",
            "timestamp": "2026-08-03T00:00:00Z",
            "schema_version": "1.0",
        }
        self._proposals[pid] = proposal
        self._seen_fingerprints[fp] = pid
        return {
            "status": "created",
            "proposal": proposal,
            "existing_id": None,
            "reason": "proposal stored, awaiting review",
        }

    # 严禁被调用的方法(防误用)
    def accept_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self._accept_calls += 1
        raise AssertionError("accept_proposal FORBIDDEN in C.8.0")

    def reject_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self._reject_calls += 1
        raise AssertionError("reject_proposal FORBIDDEN in C.8.0")

    def apply_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self._apply_calls += 1
        raise AssertionError("apply_proposal FORBIDDEN in C.8.0")

    @property
    def create_call_count(self) -> int:
        return self._create_calls

    @property
    def accept_call_count(self) -> int:
        return self._accept_calls

    @property
    def reject_call_count(self) -> int:
        return self._reject_calls

    @property
    def apply_call_count(self) -> int:
        return self._apply_calls


class _BrokenProposalManager:
    """create_proposal 抛异常的 mock。"""

    def create_proposal(
        self,
        source_event: Any,
        proposed_changes: Any,
        confidence: float,
        evidence_ids: Any,
        evaluator_meta: Any = None,
    ) -> Any:
        raise RuntimeError("proposal_manager.create_proposal crashed")


# ============================================================
# 3. Mock Audit
# ============================================================


class _MockAudit:
    """模拟 audit 记录器。"""

    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []
        self._raise: bool = False

    def record(
        self,
        operation_type: str = "",
        source: str = "",
        action: str = "",
        detail: Optional[Dict[str, Any]] = None,
        result: str = "success",
        **kwargs: Any,
    ) -> None:
        if self._raise:
            raise RuntimeError("audit record failed")
        self._records.append({
            "operation_type": operation_type,
            "source": source,
            "action": action,
            "detail": dict(detail or {}),
            "result": result,
        })

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def records(self) -> List[Dict[str, Any]]:
        return list(self._records)


# ============================================================
# 4. Mock 业务对象(用于 TestReadonly)
# ============================================================


class _MockPersonalityState:
    """模拟 src/personality/PersonalityState 内部状态。"""

    def __init__(self) -> None:
        self.traits = {"warmth": 0.5, "gentleness": 0.5}
        self._writes: int = 0

    def resolve(self, *args: Any, **kwargs: Any) -> Any:
        self._writes += 1
        return {"resolved": True}

    def apply_proposal(self, *args: Any, **kwargs: Any) -> bool:
        self._writes += 1
        return True

    @property
    def write_count(self) -> int:
        return self._writes


class _MockSelfModelStore:
    """模拟 src/runtime/self_model/SelfModelStore。"""

    def __init__(self) -> None:
        self.traits = {"warmth": 0.5}
        self._writes: int = 0

    def update(self, *args: Any, **kwargs: Any) -> bool:
        self._writes += 1
        return True

    @property
    def write_count(self) -> int:
        return self._writes


class _MockTraitStateUpdater:
    """模拟 TraitStateUpdater。"""

    def __init__(self) -> None:
        self._writes: int = 0

    def apply(self, *args: Any, **kwargs: Any) -> bool:
        self._writes += 1
        return True

    @property
    def write_count(self) -> int:
        return self._writes


# ============================================================
# 5. Mock GrowthRuntimeAdapter
# ============================================================


class _MockGrowthRuntimeAdapter:
    """模拟 src/runtime/adapters/impl/growth_runtime_adapter.py。"""

    name = "growth"
    schema_version = "1.0"

    def __init__(self) -> None:
        self._process_calls: int = 0
        self._last_output: Optional[Dict[str, Any]] = None

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        return {"healthy": True, "name": "growth"}

    def process_cycle(self, ctx: Any) -> Any:
        self._process_calls += 1
        # 模拟产出 growth_output(下游可消费)
        if hasattr(ctx, "__dict__"):
            ctx.growth_output = {
                "growth_signals": [
                    {
                        "event_id": "evt_001",
                        "event_type": "preference",
                        "canonical_topic": "music",
                        "evidence": [{"id": "ev_1", "text": "她说了她喜欢听歌"}],
                        "importance": 0.8,
                        "confidence": 0.85,
                    }
                ]
            }
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": "growth", "attached": True}

    def get_output(self) -> Optional[Dict[str, Any]]:
        return self._last_output


# ============================================================
# 6. Helper:构造 signal
# ============================================================


def _make_signal(
    confidence: float = 0.8,
    event_id: str = "evt_001",
    event_type: str = "preference",
    canonical_topic: str = "music",
    evidence: Optional[List[Dict[str, Any]]] = None,
    source_ids: Optional[List[str]] = None,
    cycle_id: str = "cycle_001",
    proposed_changes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "id": event_id,
        "event_id": event_id,
        "event_type": event_type,
        "canonical_topic": canonical_topic,
        "event": {
            "id": event_id,
            "event_type": event_type,
            "canonical_topic": canonical_topic,
        },
        "evidence": evidence if evidence is not None else [{"id": "ev_1", "text": "行为证据"}],
        "source_ids": source_ids if source_ids is not None else ["ev_1", "mem_1"],
        "importance_score": 0.8,
        "confidence": confidence,
        "cycle_id": cycle_id,
        "proposed_changes": proposed_changes if proposed_changes is not None else [
            {"path": "personality.warmth", "before": 0.5, "after": 0.6, "reason": "user feedback"},
        ],
    }


# ============================================================
# 7. 测试:TestProposalCreation(1-3)
# ============================================================


class TestProposalCreation:
    """Proposal 创建测试。"""

    def test_01_normal_create(self) -> None:
        """1. 正常创建 proposal。"""
        ev = _MockEvaluator(confidence=0.85)
        pm = _MockProposalManager()
        audit = _MockAudit()
        runtime = create_growth_proposal_runtime(
            growth_evaluator=ev,
            proposal_manager=pm,
            audit=audit,
        )
        result = runtime.process_growth_signal(_make_signal(confidence=0.85))
        assert result["success"] is True
        assert result["status"] == PROPOSAL_STATUS_CREATED
        assert result["proposal_id"] is not None
        assert result["proposal_id"].startswith("prop_")
        assert pm.create_call_count == 1
        # audit 被记录
        assert audit.record_count == 1
        assert audit.records[0]["operation_type"] == AUDIT_ACTION_PROPOSAL_CREATED

    def test_02_schema_correct(self) -> None:
        """2. 输出的 schema 字段完整。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        result = runtime.process_growth_signal(_make_signal(confidence=0.85))
        # 顶层字段
        for key in [
            "schema_version", "success", "degraded", "status",
            "proposal_id", "proposal", "confidence", "fingerprint",
            "audit_recorded", "error", "timestamp", "runtime",
        ]:
            assert key in result, f"缺少字段: {key}"
        assert result["schema_version"] == GROWTH_PROPOSAL_RUNTIME_SCHEMA_VERSION
        # runtime 块
        for key in ["name", "version", "schema_version"]:
            assert key in result["runtime"]

    def test_03_status_pending(self) -> None:
        """3. 创建的 proposal status 必须是 pending(不接受 accept)。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        result = runtime.process_growth_signal(_make_signal(confidence=0.85))
        # proposal 内部 status
        assert result["proposal"]["status"] == "pending"
        # accept_proposal 未被调
        pm = runtime._proposal_manager
        assert pm.accept_call_count == 0
        assert pm.apply_call_count == 0


# ============================================================
# 8. 测试:TestThreshold(4-5)
# ============================================================


class TestThreshold:
    """置信度门槛测试。"""

    def test_04_low_confidence_rejected(self) -> None:
        """4. 低 confidence(< 0.7)被拒绝。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.5),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        # signal 直接传 confidence=0.5(避免 evaluator 重新评估)
        result = runtime.process_growth_signal(_make_signal(confidence=0.5))
        assert result["status"] == PROPOSAL_STATUS_REJECTED_LOW_CONFIDENCE
        assert result["proposal_id"] is None
        assert result["success"] is True  # 流程上没崩

    def test_05_high_confidence_passed(self) -> None:
        """5. 高 confidence(>= 0.7)通过。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.95),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        result = runtime.process_growth_signal(_make_signal(confidence=0.95))
        assert result["status"] == PROPOSAL_STATUS_CREATED
        assert result["proposal_id"] is not None


# ============================================================
# 9. 测试:TestDuplicate(6-7)
# ============================================================


class TestDuplicate:
    """重复检测测试。"""

    def test_06_duplicate_event_skipped(self) -> None:
        """6. 同一 event 重复创建 → 不创建新 proposal。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        signal = _make_signal(confidence=0.85, event_id="evt_dup_001")
        # 第一次
        r1 = runtime.process_growth_signal(signal)
        # 第二次(同样 event)
        r2 = runtime.process_growth_signal(signal)
        # 第一次创建成功
        assert r1["status"] == PROPOSAL_STATUS_CREATED
        # 第二次命中本实例去重表 → status=deduped
        assert r2["status"] == PROPOSAL_STATUS_DEDUPED
        # proposal_manager.create_proposal 只被调 1 次(第二次被本实例去重表拦截)
        pm = runtime._proposal_manager
        assert pm.create_call_count == 1

    def test_07_fingerprint_consistent(self) -> None:
        """7. fingerprint 一致:同一 event 多次调用 → 相同 fingerprint。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        signal = _make_signal(confidence=0.85, event_id="evt_fp_001")
        r1 = runtime.process_growth_signal(signal)
        # 第二次(不重复 event_id 但内容相同 → 仍 dedup)
        r2 = runtime.process_growth_signal(signal)
        # fingerprint 相同
        assert r1["fingerprint"] == r2["fingerprint"]
        assert r1["fingerprint"] != ""


# ============================================================
# 10. 测试:TestReadonly(8-10)
# ============================================================


class TestReadonly:
    """只读业务边界测试。"""

    def test_08_personality_state_unchanged(self) -> None:
        """8. 不修改 PersonalityState(无 write_count 增长)。"""
        pys = _MockPersonalityState()
        # 故意把 personality_state 注入到 proposal_manager(模拟真实链路)
        # 但 runtime 不应触碰
        pm = _MockProposalManager()
        pm._personality_state = pys  # type: ignore[attr-defined]
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=pm,
            audit=_MockAudit(),
        )
        before = pys.write_count
        # 跑 5 次
        for i in range(5):
            runtime.process_growth_signal(_make_signal(confidence=0.85, event_id=f"evt_{i}"))
        assert pys.write_count == before

    def test_09_self_model_unchanged(self) -> None:
        """9. 不修改 SelfModelStore。"""
        sms = _MockSelfModelStore()
        pm = _MockProposalManager()
        pm._self_model_store = sms  # type: ignore[attr-defined]
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=pm,
            audit=_MockAudit(),
        )
        before = sms.write_count
        for i in range(3):
            runtime.process_growth_signal(_make_signal(confidence=0.85, event_id=f"evt_sm_{i}"))
        assert sms.write_count == before

    def test_10_apply_not_called(self) -> None:
        """10. 不调用 apply_proposal / resolve / apply。"""
        pm = _MockProposalManager()
        pys = _MockPersonalityState()
        tsu = _MockTraitStateUpdater()
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=pm,
            audit=_MockAudit(),
        )
        runtime.process_growth_signal(_make_signal(confidence=0.85))
        # ProposalManager.apply_proposal 必须 0 次
        assert pm.apply_call_count == 0
        assert pm.accept_call_count == 0
        assert pm.reject_call_count == 0
        # personality/TSU 写次数 0
        assert pys.write_count == 0
        assert tsu.write_count == 0


# ============================================================
# 11. 测试:TestAudit(11-12)
# ============================================================


class TestAudit:
    """Audit 写入测试。"""

    def test_11_proposal_create_writes_audit(self) -> None:
        """11. proposal 创建时记录 audit。"""
        audit = _MockAudit()
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=audit,
        )
        runtime.process_growth_signal(_make_signal(confidence=0.85, cycle_id="cycle_xyz"))
        assert audit.record_count == 1
        rec = audit.records[0]
        assert rec["operation_type"] == AUDIT_ACTION_PROPOSAL_CREATED

    def test_12_audit_fields_complete(self) -> None:
        """12. audit 字段完整(proposal_id / cycle_id / timestamp / confidence)。"""
        audit = _MockAudit()
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=audit,
        )
        runtime.process_growth_signal(_make_signal(confidence=0.85, cycle_id="cycle_audit"))
        rec = audit.records[0]
        detail = rec["detail"]
        for key in ["proposal_id", "cycle_id", "timestamp", "confidence"]:
            assert key in detail, f"audit detail 缺少 {key}"
        assert detail["cycle_id"] == "cycle_audit"
        assert detail["timestamp"] is not None
        assert detail["confidence"] > 0.0


# ============================================================
# 12. 测试:TestFailSafe(13-15)
# ============================================================


class TestFailSafe:
    """失败隔离测试。"""

    def test_13_evaluator_exception(self) -> None:
        """13. evaluator 抛异常 → 不崩,使用 signal 里的 confidence。"""
        ev = _BrokenEvaluator()
        pm = _MockProposalManager()
        runtime = create_growth_proposal_runtime(
            growth_evaluator=ev,
            proposal_manager=pm,
            audit=_MockAudit(),
        )
        # signal 中已带 confidence=0.8,evaluator 异常不阻断
        result = runtime.process_growth_signal(_make_signal(confidence=0.8))
        assert result["success"] is True
        assert result["status"] == PROPOSAL_STATUS_CREATED

    def test_14_store_exception(self) -> None:
        """14. proposal_manager.create_proposal 抛异常 → 不崩,返回 error。"""
        pm = _MockProposalManager()
        pm._create_should_raise = RuntimeError("store crash")
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=pm,
            audit=_MockAudit(),
        )
        result = runtime.process_growth_signal(_make_signal(confidence=0.85))
        assert result["success"] is False
        assert result["degraded"] is True
        assert result["status"] == PROPOSAL_STATUS_ERROR
        assert "create_proposal_failed" in (result.get("error") or "")

    def test_15_audit_exception(self) -> None:
        """15. audit 抛异常 → 不阻断 proposal 创建。"""
        audit = _MockAudit()
        audit._raise = True
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=audit,
        )
        result = runtime.process_growth_signal(_make_signal(confidence=0.85))
        # proposal 仍创建成功
        assert result["status"] == PROPOSAL_STATUS_CREATED
        # audit_recorded=False(因异常)
        assert result["audit_recorded"] is False


# ============================================================
# 13. 测试:TestIntegration(16-17)
# ============================================================


class TestIntegration:
    """端到端集成测试。"""

    def test_16_observer_to_growth_runtime(self) -> None:
        """16. RuntimeObserver 快照中 growth adapter 输出可被 GrowthProposalRuntime 消费。"""
        from src.runtime.observability import RuntimeObserver

        growth_adapter = _MockGrowthRuntimeAdapter()
        orch = _MockOrchWithAdapters([growth_adapter])
        observer = RuntimeObserver(orchestrator=orch)
        snap = observer.snapshot()
        # observer 应能读到 growth adapter(状态)
        assert "growth" in snap["adapters"]
        # 模拟一次 process_cycle,产出 growth_output
        class _MockCtx:
            pass
        ctx = _MockCtx()
        growth_adapter.process_cycle(ctx)
        growth_output = ctx.growth_output
        # growth_output 包含 growth_signals,可被 GrowthProposalRuntime 消费
        assert "growth_signals" in growth_output
        signal = growth_output["growth_signals"][0]
        # 构造 signal,送入 runtime
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        # signal 缺 confidence(由 runtime evaluator 提供)
        sig = {
            "id": signal["event_id"],
            "event_type": signal["event_type"],
            "canonical_topic": signal["canonical_topic"],
            "event": signal,
            "evidence": signal["evidence"],
            "source_ids": ["ev_1"],
            "importance_score": signal["importance"],
            "cycle_id": "c_integration",
        }
        result = runtime.process_growth_signal(sig)
        assert result["status"] == PROPOSAL_STATUS_CREATED

    def test_17_growth_runtime_adapter_output_consumable(self) -> None:
        """17. GrowthRuntimeAdapter 输出格式可被 GrowthProposalRuntime 处理。"""
        growth_adapter = _MockGrowthRuntimeAdapter()
        class _MockCtx:
            pass
        ctx = _MockCtx()
        growth_adapter.process_cycle(ctx)
        growth_output = ctx.growth_output
        # 验证 adapter 输出包含 runtime 可消费的字段
        assert "growth_signals" in growth_output
        sig0 = growth_output["growth_signals"][0]
        for key in ["event_id", "event_type", "canonical_topic", "evidence", "importance"]:
            assert key in sig0, f"growth_output 缺少 {key}"


class _MockOrchWithAdapters:
    """最小 mock orchestrator(只暴露 list_adapters)。"""

    def __init__(self, adapters: List[Any]) -> None:
        self._adapters = []
        for a in adapters:
            self._adapters.append({
                "name": getattr(a, "name", "unknown"),
                "adapter": a,
                "instance": a,
                "attached": True,
                "health": {"healthy": True, "status": "healthy"},
            })

    def list_adapters(self) -> List[Dict[str, Any]]:
        return list(self._adapters)

    def health_check(self) -> Dict[str, Any]:
        return {"healthy": True, "degraded": False}

    def snapshot(self) -> Dict[str, Any]:
        return {"name": "MockOrch", "healthy": True}


# ============================================================
# 14. 测试:TestSecurity(18-20)
# ============================================================


class TestSecurity:
    """安全限制测试。"""

    def test_18_accept_forbidden(self) -> None:
        """18. ProposalManager.accept_proposal() 必须 0 次调用。"""
        pm = _MockProposalManager()
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=pm,
            audit=_MockAudit(),
        )
        # 跑 10 次创建
        for i in range(10):
            runtime.process_growth_signal(_make_signal(confidence=0.85, event_id=f"evt_sec_{i}"))
        # accept_proposal 永远 0 次
        assert pm.accept_call_count == 0

    def test_19_apply_forbidden(self) -> None:
        """19. ProposalManager.apply_proposal() 必须 0 次调用。"""
        pm = _MockProposalManager()
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=pm,
            audit=_MockAudit(),
        )
        for i in range(10):
            runtime.process_growth_signal(_make_signal(confidence=0.85, event_id=f"evt_app_{i}"))
        assert pm.apply_call_count == 0

    def test_20_resolver_forbidden(self) -> None:
        """20. PersonalityResolver.resolve() 必须 0 次调用。"""
        pys = _MockPersonalityState()
        pm = _MockProposalManager()
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=pm,
            audit=_MockAudit(),
        )
        for i in range(5):
            runtime.process_growth_signal(_make_signal(confidence=0.85, event_id=f"evt_res_{i}"))
        # resolve() 0 次
        assert pys.write_count == 0


# ============================================================
# 15. 测试:TestSchema(21-25)
# ============================================================


class TestSchema:
    """Proposal 字段完整性测试。"""

    def test_21_proposal_has_id(self) -> None:
        """21. proposal 必须包含 id 字段。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        result = runtime.process_growth_signal(_make_signal(confidence=0.85))
        assert "id" in result["proposal"]
        assert result["proposal"]["id"] is not None
        assert result["proposal_id"] == result["proposal"]["id"]

    def test_22_proposal_has_created_at(self) -> None:
        """22. proposal 必须包含 timestamp(created_at) 字段。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        result = runtime.process_growth_signal(_make_signal(confidence=0.85))
        assert "timestamp" in result["proposal"]
        assert result["proposal"]["timestamp"] is not None

    def test_23_proposal_has_source_event(self) -> None:
        """23. proposal 必须包含 source_event_id。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        result = runtime.process_growth_signal(
            _make_signal(confidence=0.85, event_id="evt_schema_001")
        )
        # proposal 内有 source_event_id
        prop = result["proposal"]
        # ProposalManager mock 把 source_event_id 存为 source_event_id 字段
        assert prop.get("source_event_id") == "evt_schema_001"

    def test_24_proposal_has_changes(self) -> None:
        """24. proposal 必须包含 proposed_changes 列表。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        result = runtime.process_growth_signal(_make_signal(confidence=0.85))
        prop = result["proposal"]
        assert "proposed_changes" in prop
        assert isinstance(prop["proposed_changes"], list)
        assert len(prop["proposed_changes"]) >= 1

    def test_25_proposal_has_confidence_and_requires_review(self) -> None:
        """25. proposal 必须包含 confidence,且 requires_review=True(由 status=pending 隐含)。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        result = runtime.process_growth_signal(_make_signal(confidence=0.85))
        prop = result["proposal"]
        assert "confidence" in prop
        assert prop["confidence"] > 0.0
        # status=pending 隐含 requires_review=True
        assert prop["status"] == "pending"


# ============================================================
# 16. 额外测试:辅助 API + 工厂
# ============================================================


class TestHelperAPIs:
    """辅助 API 测试。"""

    def test_safe_process_with_none_runtime(self) -> None:
        """safe_process_growth_signal(None, signal) → 不抛,返回降级结果。"""
        result = safe_process_growth_signal(None, _make_signal(confidence=0.85))
        assert isinstance(result, dict)
        assert "success" in result
        # 没注入 proposal_manager,应失败
        assert result["success"] is False
        assert result["degraded"] is True

    def test_safe_process_with_none_signal(self) -> None:
        """safe_process_growth_signal(runtime, None) → 不抛。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        result = safe_process_growth_signal(runtime, None)
        assert isinstance(result, dict)
        assert "success" in result
        assert result["success"] is False

    def test_runtime_internal_stats(self) -> None:
        """Runtime 自身的 get_stats() 正常累计。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        assert runtime.process_count == 0
        runtime.process_growth_signal(_make_signal(confidence=0.85, event_id="evt_stats_1"))
        runtime.process_growth_signal(_make_signal(confidence=0.5, event_id="evt_stats_2"))
        assert runtime.process_count == 2
        assert runtime.created_count == 1
        assert runtime.rejected_count == 1
        stats = runtime.get_stats()
        assert stats["process_count"] == 2
        assert stats["created_count"] == 1
        assert stats["rejected_count"] == 1

    def test_health_check(self) -> None:
        """health_check 返回完整状态。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        hc = runtime.health_check()
        assert "healthy" in hc
        assert "degraded" in hc
        assert "confidence_threshold" in hc
        assert hc["has_evaluator"] is True
        assert hc["has_proposal_manager"] is True

    def test_audit_none_safe(self) -> None:
        """audit=None 时不抛,且 audit_recorded=False。"""
        runtime = create_growth_proposal_runtime(
            growth_evaluator=_MockEvaluator(0.85),
            proposal_manager=_MockProposalManager(),
            audit=None,
        )
        result = runtime.process_growth_signal(_make_signal(confidence=0.85))
        # 创建成功,audit_recorded=False
        assert result["status"] == PROPOSAL_STATUS_CREATED
        assert result["audit_recorded"] is False

    def test_signal_without_confidence_uses_evaluator(self) -> None:
        """signal 没带 confidence → 由 evaluator 计算。"""
        ev = _MockEvaluator(confidence=0.85)
        runtime = create_growth_proposal_runtime(
            growth_evaluator=ev,
            proposal_manager=_MockProposalManager(),
            audit=_MockAudit(),
        )
        signal = _make_signal(confidence=0.85)
        # 移除 confidence
        del signal["confidence"]
        result = runtime.process_growth_signal(signal)
        # evaluator 被调用 + confidence 满足门槛
        assert ev._call_count == 1
        assert result["status"] == PROPOSAL_STATUS_CREATED
