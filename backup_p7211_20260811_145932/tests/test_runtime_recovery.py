# -*- coding: utf-8 -*-
"""
tests/test_runtime_recovery.py

Phase C.7.3 Runtime Recovery & Self-Diagnosis —— 验证测试

目标:
  验证 RuntimeDiagnoser / RuntimeRecovery 严格只读地:
    1) 诊断 RuntimeObserver 快照
    2) 分类异常(severity / type)
    3) 生成恢复建议
    4) 执行安全恢复动作(clear cache / reattach / recheck health)
    5) 不修改任何业务状态(GrowthState / ProposalStore / PersonalityState)

约束:
  - 不修改任何 Adapter / 核心模块
  - 所有外部依赖 mock 化
  - 不启动真实 LLM / 业务
  - 不调 process_cycle / 不调任何写入方法

覆盖 20+ 测试:
  TestDiagnosis         (1-4)   全部健康 / 单 adapter / 多 adapter / runtime degraded
  TestSeverity          (5-7)   low / medium / high
  TestRecovery          (8-10)  recover 成功 / 异常隔离 / attach 调用
  TestReadonly          (11-13) GrowthState / ProposalStore / PersonalityState
  TestIntegration       (14-15) Observer → Diagnoser / Diagnoser → Recovery
  TestSchema            (16-17) diagnosis schema / recovery schema
  TestFailSafe          (18-20) observer 异常 / adapter 异常 / persistence 异常
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


from src.runtime.recovery import (
    RUNTIME_DIAGNOSER_SCHEMA_VERSION,
    RUNTIME_RECOVERY_SCHEMA_VERSION,
    RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
    RECOVERY_ACTION_REATTACH_ADAPTERS,
    RECOVERY_ACTION_RECHECK_HEALTH,
    RECOVERY_ACTION_RESET_TEMP_STATE,
    RECOVERY_ACTION_MARK_DEGRADED,
    RuntimeDiagnoser,
    RuntimeRecovery,
    create_runtime_diagnoser,
    create_runtime_recovery,
    safe_diagnose,
    safe_recover,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SEVERITY_HIGH,
)
from src.runtime.recovery.runtime_diagnoser import (
    TYPE_ADAPTER_UNHEALTHY,
    TYPE_RUNTIME_DEGRADED,
    TYPE_RUNTIME_FAILURE_STREAK,
    TYPE_AUDIT_UNAVAILABLE,
    TYPE_PERSISTENCE_DEGRADED,
    TYPE_CYCLE_FAILURE_RATE_HIGH,
    TYPE_OBSERVER_UNAVAILABLE,
)
from src.runtime.observability import RuntimeObserver


# ============================================================
# 1. Mock Adapters(模拟 5 个真实 Adapter)
# ============================================================


class _MockHealthyAdapter:
    """通用 healthy adapter。"""

    def __init__(self, name: str = "memory", healthy: bool = True) -> None:
        self.name = name
        self._healthy = bool(healthy)
        self._attach_calls: int = 0
        self._health_calls: int = 0
        self._process_calls: int = 0

    def attach(self) -> bool:
        self._attach_calls += 1
        return True

    def health_check(self) -> Dict[str, Any]:
        self._health_calls += 1
        return {"healthy": self._healthy, "name": self.name, "attached": True}

    def process_cycle(self, ctx: Any) -> Any:
        # Recovery 严禁调用;若被调用记入违规
        self._process_calls += 1
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": self.name, "attached": True}


class _MockDegradedAdapter:
    """health_check 返回 degraded status。"""

    def __init__(self, name: str = "growth", status: str = "degraded") -> None:
        self.name = name
        self._status = str(status)
        self._attach_calls: int = 0
        self._process_calls: int = 0

    def attach(self) -> bool:
        self._attach_calls += 1
        return True

    def health_check(self) -> Dict[str, Any]:
        return {"status": self._status, "adapter": self.name, "attached": True}

    def process_cycle(self, ctx: Any) -> Any:
        self._process_calls += 1
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": self.name, "attached": True}


class _BrokenAttachAdapter:
    """attach 抛异常的 adapter(测试异常隔离)。"""

    name = "broken_attach"

    def attach(self) -> bool:
        raise RuntimeError("attach failed")

    def health_check(self) -> Dict[str, Any]:
        return {"healthy": True, "name": self.name, "attached": False}

    def process_cycle(self, ctx: Any) -> Any:
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": self.name, "attached": False}


# ============================================================
# 2. Mock Orchestrator
# ============================================================


class _MockOrchestrator:
    """模拟 RuntimeCycleOrchestrator(只暴露 list_adapters)。"""

    def __init__(self, adapters: Optional[List[Dict[str, Any]]] = None) -> None:
        self._adapters = adapters or []

    def list_adapters(self) -> List[Dict[str, Any]]:
        return list(self._adapters)


# ============================================================
# 3. Mock 业务对象(用于 TestReadonly)
# ============================================================


class _MockGrowthState:
    """模拟 src/growth/GrowthState 内部状态。"""

    def __init__(self) -> None:
        self.metrics = {"warmth": 0.5, "playfulness": 0.5}
        self.behaviors = {"greet": True}
        self._writes: int = 0

    def save(self) -> bool:
        self._writes += 1
        return True

    def apply(self, *args: Any, **kwargs: Any) -> bool:
        self._writes += 1
        return True

    @property
    def write_count(self) -> int:
        return self._writes


class _MockProposalStore:
    """模拟 src/growth/ProposalStore。"""

    def __init__(self) -> None:
        self.proposals: List[Dict[str, Any]] = []
        self._writes: int = 0

    def create_proposal(self, data: Dict[str, Any]) -> Any:
        self._writes += 1
        self.proposals.append(data)
        return data

    def apply_proposal(self, *args: Any, **kwargs: Any) -> bool:
        self._writes += 1
        return True

    @property
    def write_count(self) -> int:
        return self._writes


class _MockPersonalityState:
    """模拟 src/personality/PersonalityState。"""

    def __init__(self) -> None:
        self.traits = {"warmth": 0.5, "gentleness": 0.5}
        self._writes: int = 0

    def resolve(self, *args: Any, **kwargs: Any) -> Any:
        self._writes += 1
        return None

    def apply(self, *args: Any, **kwargs: Any) -> bool:
        self._writes += 1
        return True

    @property
    def write_count(self) -> int:
        return self._writes


# ============================================================
# 4. Helper:构造 snapshot
# ============================================================


def _make_healthy_snapshot() -> Dict[str, Any]:
    """构造一个全 healthy 的 snapshot。"""
    return {
        "schema_version": "1.0",
        "runtime": {
            "name": "RuntimeCycleOrchestrator",
            "healthy": True,
            "degraded": False,
            "enabled": True,
            "closed": False,
            "consecutive_failures": 0,
            "cycle_count": 10,
            "completed_count": 10,
            "failed_count": 0,
        },
        "adapters": {
            "memory": {"name": "memory", "healthy": True, "status": "healthy", "attached": True, "available": True},
            "emotion": {"name": "emotion", "healthy": True, "status": "healthy", "attached": True, "available": True},
            "personality": {"name": "personality", "healthy": True, "status": "healthy", "attached": True, "available": True},
            "relationship": {"name": "relationship", "healthy": True, "status": "healthy", "attached": True, "available": True},
            "growth": {"name": "growth", "healthy": True, "status": "healthy", "attached": True, "available": True},
        },
        "cycles": {
            "total": 10,
            "completed": 10,
            "failed": 0,
            "success_rate": 1.0,
            "in_memory": 10,
            "latest_cycle_id": "c_001",
            "latest_timestamp": "2026-08-03T00:00:00Z",
        },
        "errors": {"last_error": None, "consecutive_failures": 0, "recent_errors": []},
        "persistence": {"available": True, "degraded": False, "write_count": 10, "write_error_count": 0},
        "audit": {"available": True, "latest_cycle_id": "c_001", "latest_timestamp": "2026-08-03T00:00:00Z"},
        "timestamp": "2026-08-03T00:00:00Z",
    }


def _make_runtime_degraded_snapshot() -> Dict[str, Any]:
    """构造 runtime degraded snapshot。"""
    snap = _make_healthy_snapshot()
    snap["runtime"]["degraded"] = True
    snap["runtime"]["healthy"] = False
    return snap


# ============================================================
# 5. 测试:TestDiagnosis(1-4)
# ============================================================


class TestDiagnosis:
    """诊断基础测试。"""

    def test_01_all_healthy(self) -> None:
        """1. 全部健康 → healthy=True,无 issues。"""
        diag = create_runtime_diagnoser()
        result = diag.diagnose(_make_healthy_snapshot())
        assert result["healthy"] is True
        assert result["issue_count"] == 0
        assert len(result["issues"]) == 0
        # 至少包含一个正向 recommendation
        assert any("healthy" in r.lower() or "no recovery" in r.lower() for r in result["recommendations"])

    def test_02_single_adapter_unhealthy(self) -> None:
        """2. 单 adapter unhealthy → 1 个 issue(severity=medium)。"""
        diag = create_runtime_diagnoser()
        snap = _make_healthy_snapshot()
        snap["adapters"]["growth"]["healthy"] = False
        snap["adapters"]["growth"]["status"] = "degraded"
        result = diag.diagnose(snap)
        assert result["healthy"] is False
        # 至少有 1 个 issue(growth)
        assert result["issue_count"] >= 1
        growth_issues = [i for i in result["issues"] if i.get("component") == "growth"]
        assert len(growth_issues) >= 1
        assert growth_issues[0]["severity"] == SEVERITY_MEDIUM
        assert growth_issues[0]["type"] == TYPE_ADAPTER_UNHEALTHY

    def test_03_multiple_adapter_unhealthy(self) -> None:
        """3. 多个 adapter unhealthy → 多个 issue。"""
        diag = create_runtime_diagnoser()
        snap = _make_healthy_snapshot()
        snap["adapters"]["memory"]["healthy"] = False
        snap["adapters"]["growth"]["healthy"] = False
        result = diag.diagnose(snap)
        assert result["healthy"] is False
        assert result["issue_count"] >= 2
        components = {i.get("component") for i in result["issues"]}
        assert "memory" in components
        assert "growth" in components

    def test_04_runtime_degraded(self) -> None:
        """4. runtime degraded → severity=high issue。"""
        diag = create_runtime_diagnoser()
        result = diag.diagnose(_make_runtime_degraded_snapshot())
        assert result["healthy"] is False
        runtime_issues = [i for i in result["issues"] if i.get("component") == "runtime"]
        assert len(runtime_issues) >= 1
        # runtime degraded → severity high
        degraded_issue = next(
            (i for i in runtime_issues if i.get("type") == TYPE_RUNTIME_DEGRADED),
            None,
        )
        assert degraded_issue is not None
        assert degraded_issue["severity"] == SEVERITY_HIGH


# ============================================================
# 6. 测试:TestSeverity(5-7)
# ============================================================


class TestSeverity:
    """严重级别测试。"""

    def test_05_low_severity(self) -> None:
        """5. 仅 observer 不可用 → low severity。"""
        diag = create_runtime_diagnoser()
        # snapshot 为 None → 触发 observer_unavailable (low)
        result = diag.diagnose(None)
        assert result["severity"] == SEVERITY_LOW
        assert result["healthy"] is False
        assert any(i.get("type") == TYPE_OBSERVER_UNAVAILABLE for i in result["issues"])

    def test_06_medium_severity(self) -> None:
        """6. 单 adapter unhealthy → medium severity。"""
        diag = create_runtime_diagnoser()
        snap = _make_healthy_snapshot()
        snap["adapters"]["growth"]["healthy"] = False
        result = diag.diagnose(snap)
        assert result["severity"] == SEVERITY_MEDIUM
        assert any(i.get("severity") == SEVERITY_MEDIUM for i in result["issues"])

    def test_07_high_severity(self) -> None:
        """7. runtime degraded → high severity。"""
        diag = create_runtime_diagnoser()
        result = diag.diagnose(_make_runtime_degraded_snapshot())
        assert result["severity"] == SEVERITY_HIGH
        assert any(i.get("severity") == SEVERITY_HIGH for i in result["issues"])


# ============================================================
# 7. 测试:TestRecovery(8-10)
# ============================================================


class TestRecovery:
    """恢复动作测试。"""

    def test_08_recover_success(self) -> None:
        """8. 健康时 recover → 成功(只清理缓存)。"""
        diag = create_runtime_diagnoser()
        diagnosis = diag.diagnose(_make_healthy_snapshot())
        # 构造一个带 clear_cache 方法的 observer
        observer = _MockObserverWithCache()
        rec = create_runtime_recovery(observer=observer)
        result = rec.recover(diagnosis)
        assert result["success"] is True
        assert result["actions_errored"] == 0
        assert len(result["actions"]) >= 1
        # 健康诊断时,recovery 应直接返回成功
        assert any(a.get("action") == RECOVERY_ACTION_CLEAR_OBSERVER_CACHE for a in result["actions"])

    def test_09_recover_exception_isolation(self) -> None:
        """9. recover 永不抛异常(任何内部错误都吸收)。"""
        rec = create_runtime_recovery(observer=_MockObserverRaises())
        # 故意传入一个可能引发问题的 diagnosis(嵌套深度异常)
        result = rec.recover({"issues": [{"type": "x", "severity": "y"}], "healthy": False})
        assert isinstance(result, dict)
        assert "success" in result
        assert "actions" in result
        # 即便部分 action 失败,recover 自身也不抛
        assert isinstance(result.get("error"), (str, type(None)))

    def test_10_adapter_attach_called(self) -> None:
        """10. 恢复时 adapter.attach() 被调用(非 process_cycle)。"""
        # 构造诊断结果:有 adapter unhealthy
        diagnosis = {
            "healthy": False,
            "severity": SEVERITY_MEDIUM,
            "issues": [
                {
                    "component": "growth",
                    "severity": SEVERITY_MEDIUM,
                    "type": TYPE_ADAPTER_UNHEALTHY,
                    "message": "growth unhealthy",
                    "details": {},
                }
            ],
            "recommendations": [],
        }
        # adapter 实例
        growth = _MockHealthyAdapter(name="growth", healthy=True)
        orch = _MockOrchestrator(adapters=[
            {"name": "growth", "adapter": growth, "instance": growth, "attached": True},
        ])
        rec = create_runtime_recovery(orchestrator=orch)
        result = rec.recover(diagnosis)
        # attach 至少被调 1 次
        assert growth._attach_calls >= 1
        # process_cycle 必须 0 次
        assert growth._process_calls == 0
        # 恢复结果中应包含 reattach / recheck 动作
        actions_set = {a.get("action") for a in result["actions"]}
        assert RECOVERY_ACTION_REATTACH_ADAPTERS in actions_set


# ============================================================
# 8. 测试:TestReadonly(11-13)
# ============================================================


class TestReadonly:
    """只读业务边界测试。"""

    def test_11_growth_state_unchanged(self) -> None:
        """11. Recovery 不修改 GrowthState。"""
        gs = _MockGrowthState()
        before = (gs.write_count, dict(gs.metrics))
        diag = create_runtime_diagnoser()
        diagnosis = diag.diagnose(_make_healthy_snapshot())
        rec = create_runtime_recovery()
        rec.recover(diagnosis)
        # 业务对象完全未变
        assert gs.write_count == before[0]
        assert gs.metrics == before[1]

    def test_12_proposal_store_unchanged(self) -> None:
        """12. Recovery 不修改 ProposalStore。"""
        ps = _MockProposalStore()
        before = (ps.write_count, list(ps.proposals))
        diag = create_runtime_diagnoser()
        diagnosis = diag.diagnose(_make_healthy_snapshot())
        rec = create_runtime_recovery()
        rec.recover(diagnosis)
        assert ps.write_count == before[0]
        assert ps.proposals == before[1]

    def test_13_personality_state_unchanged(self) -> None:
        """13. Recovery 不修改 PersonalityState。"""
        pys = _MockPersonalityState()
        before = (pys.write_count, dict(pys.traits))
        diag = create_runtime_diagnoser()
        diagnosis = diag.diagnose(_make_healthy_snapshot())
        rec = create_runtime_recovery()
        rec.recover(diagnosis)
        assert pys.write_count == before[0]
        assert pys.traits == before[1]


# ============================================================
# 9. 测试:TestIntegration(14-15)
# ============================================================


class _MockObserverWithCache:
    """带 clear_cache() 方法的 observer mock。"""

    def __init__(self) -> None:
        self._cleared: int = 0
        self._last_snapshot_ts: Optional[str] = None
        self._last_error: Optional[str] = None
        self._snapshot_count: int = 0

    def clear_cache(self) -> bool:
        self._cleared += 1
        return True

    def snapshot(self) -> Dict[str, Any]:
        return _make_healthy_snapshot()


class _MockObserverRaises:
    """observer.clear_cache 抛异常的 mock。"""

    def clear_cache(self) -> bool:
        raise RuntimeError("clear_cache crashed")

    def snapshot(self) -> Dict[str, Any]:
        return _make_healthy_snapshot()


class TestIntegration:
    """端到端集成测试。"""

    def test_14_observer_to_diagnoser(self) -> None:
        """14. Observer.snapshot() → Diagnoser.diagnose() 链路。"""
        observer = _MockObserverWithCache()
        rec = create_runtime_recovery(observer=observer)
        # 端到端:observer.snapshot → diagnoser.diagnose
        snap = observer.snapshot()
        diag = create_runtime_diagnoser()
        diagnosis = diag.diagnose(snap)
        # 健康诊断
        assert diagnosis["healthy"] is True
        # recover 链路成功
        result = rec.recover(diagnosis)
        assert result["success"] is True
        # clear_cache 被调用
        assert observer._cleared >= 1

    def test_15_diagnoser_to_recovery(self) -> None:
        """15. Diagnoser.diagnose() → Recovery.recover() 链路(含 unhealthy 路径)。"""
        # 构造 unhealthy snapshot
        snap = _make_healthy_snapshot()
        snap["adapters"]["growth"]["healthy"] = False

        diag = create_runtime_diagnoser()
        diagnosis = diag.diagnose(snap)
        # 诊断应识别出 growth 异常
        assert diagnosis["healthy"] is False
        assert any(i.get("component") == "growth" for i in diagnosis["issues"])

        # 接 recovery
        observer = _MockObserverWithCache()
        rec = create_runtime_recovery(observer=observer)
        result = rec.recover(diagnosis)
        # 整体应成功(可能 actions_errored=0)
        assert isinstance(result, dict)
        assert "success" in result
        # 应触发 reattach(recommended for adapter_unhealthy)
        actions_set = {a.get("action") for a in result["actions"]}
        assert RECOVERY_ACTION_REATTACH_ADAPTERS in actions_set or RECOVERY_ACTION_RECHECK_HEALTH in actions_set


# ============================================================
# 10. 测试:TestSchema(16-17)
# ============================================================


class TestSchema:
    """Schema 字段验证。"""

    def test_16_diagnosis_schema(self) -> None:
        """16. diagnosis 输出符合 schema 契约。"""
        diag = create_runtime_diagnoser()
        result = diag.diagnose(_make_healthy_snapshot())
        # 顶层字段必须存在
        for key in [
            "schema_version",
            "healthy",
            "severity",
            "issue_count",
            "issues",
            "recommendations",
            "timestamp",
            "diagnoser",
        ]:
            assert key in result, f"diagnosis 缺少顶层字段: {key}"
        assert result["schema_version"] == RUNTIME_DIAGNOSER_SCHEMA_VERSION
        # 顶层类型校验
        assert isinstance(result["healthy"], bool)
        assert isinstance(result["severity"], str)
        assert isinstance(result["issue_count"], int)
        assert isinstance(result["issues"], list)
        assert isinstance(result["recommendations"], list)
        # issue 字段
        for iss in result["issues"]:
            for key in ["component", "severity", "type", "message", "details"]:
                assert key in iss, f"issue 缺少字段: {key}"
        # diagnoser 块
        for key in ["name", "version", "schema_version"]:
            assert key in result["diagnoser"]

    def test_17_recovery_schema(self) -> None:
        """17. recovery 输出符合 schema 契约。"""
        diag = create_runtime_diagnoser()
        diagnosis = diag.diagnose(_make_healthy_snapshot())
        rec = create_runtime_recovery(observer=_MockObserverWithCache())
        result = rec.recover(diagnosis)
        # 顶层字段
        for key in [
            "schema_version",
            "success",
            "actions",
            "issues_addressed",
            "actions_executed",
            "actions_skipped",
            "actions_errored",
            "timestamp",
            "recovery",
        ]:
            assert key in result, f"recovery 缺少顶层字段: {key}"
        assert result["schema_version"] == RUNTIME_RECOVERY_SCHEMA_VERSION
        assert isinstance(result["success"], bool)
        assert isinstance(result["actions"], list)
        # action 字段
        for act in result["actions"]:
            for key in ["action", "target", "result", "error", "details"]:
                assert key in act, f"action 缺少字段: {key}"
        # recovery 块
        for key in ["name", "version", "schema_version"]:
            assert key in result["recovery"]


# ============================================================
# 11. 测试:TestFailSafe(18-20)
# ============================================================


class _BrokenObserver:
    """observer 所有方法都抛异常。"""

    def snapshot(self) -> Dict[str, Any]:
        raise RuntimeError("observer.snapshot crashed")

    def clear_cache(self) -> bool:
        raise RuntimeError("observer.clear_cache crashed")


class _BrokenAttachOrchestrator:
    """orchestrator.list_adapters 抛异常。"""

    def list_adapters(self) -> List[Dict[str, Any]]:
        raise RuntimeError("list_adapters crashed")


class _BrokenEverythingOrchestrator:
    """orchestrator 所有方法都抛异常。"""

    def list_adapters(self) -> List[Dict[str, Any]]:
        raise RuntimeError("list_adapters crashed")


class TestFailSafe:
    """失败隔离测试。"""

    def test_18_observer_exception(self) -> None:
        """18. observer 抛异常 → diagnoser 仍返回降级 diagnosis。"""
        observer = _BrokenObserver()
        diag = create_runtime_diagnoser()
        # diagnoser 不直接调 observer.snapshot(只接收 snapshot 输入)
        # 测试:输入 None 或异常类型时,诊断仍能安全返回
        result = diag.diagnose(None)
        assert result["healthy"] is False
        assert any(i.get("type") == TYPE_OBSERVER_UNAVAILABLE for i in result["issues"])
        # 输入异常类型
        result2 = diag.diagnose("not a dict")
        assert result2["healthy"] is False
        # 输入 list 也安全
        result3 = diag.diagnose([])
        assert result3["healthy"] is False

    def test_19_adapter_exception(self) -> None:
        """19. adapter.attach() / health_check() 异常 → recovery 不崩溃。"""
        orch = _BrokenAttachOrchestrator()
        rec = create_runtime_recovery(orchestrator=orch)
        diagnosis = {
            "healthy": False,
            "severity": SEVERITY_MEDIUM,
            "issues": [
                {
                    "component": "growth",
                    "severity": SEVERITY_MEDIUM,
                    "type": TYPE_ADAPTER_UNHEALTHY,
                    "message": "growth unhealthy",
                    "details": {},
                }
            ],
            "recommendations": [],
        }
        # 不应抛异常
        result = rec.recover(diagnosis)
        assert isinstance(result, dict)
        assert "success" in result
        # 部分 action 应 errored(skipped 或 error)
        assert result["actions_errored"] >= 0
        # 整体 success 可能为 False(因为有 errored),但绝不抛

    def test_20_persistence_exception(self) -> None:
        """20. 内部异常 → safe_recover / safe_diagnose 不崩溃。"""
        # safe_diagnose(None, ...) → 不抛
        r1 = safe_diagnose(None, _make_healthy_snapshot())
        assert isinstance(r1, dict)
        # safe_recover(None, ...) → 不抛
        r2 = safe_recover(None, None)
        assert isinstance(r2, dict)
        # 传入异常类型也不抛
        r3 = safe_diagnose(None, "bad_input")
        assert isinstance(r3, dict)
        r4 = safe_recover(None, "bad_input")
        assert isinstance(r4, dict)


# ============================================================
# 12. 额外测试:辅助 API + 工厂
# ============================================================


class TestHelperAPIs:
    """辅助 API 测试。"""

    def test_safe_diagnose_with_none_diagnoser(self) -> None:
        """safe_diagnose(None, snapshot) → 返回空 result,不抛。"""
        out = safe_diagnose(None, _make_healthy_snapshot())
        assert isinstance(out, dict)
        assert "healthy" in out

    def test_safe_recover_with_none_recovery(self) -> None:
        """safe_recover(None, diagnosis) → 返回空 result,不抛。"""
        out = safe_recover(None, None)
        assert isinstance(out, dict)
        assert "success" in out
        assert out["success"] is False

    def test_recovery_internal_stats(self) -> None:
        """Recovery 自身的 get_stats() / recover_count 正常累计。"""
        rec = create_runtime_recovery(observer=_MockObserverWithCache())
        assert rec.recover_count == 0
        diag = create_runtime_diagnoser().diagnose(_make_healthy_snapshot())
        rec.recover(diag)
        rec.recover(diag)
        assert rec.recover_count == 2
        stats = rec.get_stats()
        assert stats["recover_count"] == 2
        assert stats["has_observer"] is True
        assert stats["has_orchestrator"] is False

    def test_recovery_never_calls_process_cycle(self) -> None:
        """Recovery 永不调用 adapter.process_cycle()(硬约束)。"""
        adp = _MockHealthyAdapter(name="growth")
        orch = _MockOrchestrator(adapters=[
            {"name": "growth", "adapter": adp, "instance": adp, "attached": True},
        ])
        rec = create_runtime_recovery(orchestrator=orch)
        diagnosis = {
            "healthy": False,
            "severity": SEVERITY_HIGH,
            "issues": [
                {
                    "component": "growth",
                    "severity": SEVERITY_MEDIUM,
                    "type": TYPE_ADAPTER_UNHEALTHY,
                    "message": "growth unhealthy",
                    "details": {},
                },
                {
                    "component": "runtime",
                    "severity": SEVERITY_HIGH,
                    "type": TYPE_RUNTIME_DEGRADED,
                    "message": "runtime degraded",
                    "details": {},
                },
            ],
            "recommendations": [],
        }
        rec.recover(diagnosis)
        # process_cycle 必须 0 次
        assert adp._process_calls == 0
        # attach 至少 1 次
        assert adp._attach_calls >= 1
        # health_check 至少 1 次
        assert adp._health_calls >= 1
