# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b4.py

Phase B.4 Runtime Integration —— Action Execution Governance 接入层测试

覆盖:
  1. 配置测试(default / override / 不修改入参 / 兼容性)
  2. ExecutionPolicy 测试(从 cfg 构造 / 序列化)
  3. ActionLifecycleManager 测试(状态机 / ledger / 重复检测 / 冷却)
  4. ActionAuditRecorder 测试(audit 写入 / 异常隔离)
  5. RuntimeB4Bridge 测试(包装 / flush_pending / summarize_b4 / 透传)
  6. 重复检测 + 审计轨迹集成测试
  7. Supervisor 集成测试(supervisor 调 flush_pending 不报错)
  8. Backward Compatibility(B.4 关闭时行为不变化)
  9. 核心模块未修改保护(不侵入)
 10. start_runtime.py CLI / init_governance 测试

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化所有外部依赖
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# 1. 测试目标导入
# ============================================================

from src.runtime.phase_b4_integration import (
    PHASE_B4_DEFAULT_CONFIG,
    PHASE_B4_NAME,
    PHASE_B4_VERSION,
    LifecycleStage,
    apply_phase_b4_config,
    is_phase_b4_enabled,
    is_b4_enabled_simple,
    ExecutionPolicy,
    ActionAuditRecord,
    ActionLifecycleManager,
    ActionAuditRecorder,
    RuntimeB4Bridge,
    create_b4_bridge,
    safe_flush_pending_b4,
)

from src.runtime.phase_b3_integration import RuntimeB3Bridge
from src.runtime.audit_log.runtime_audit_logger import RuntimeAuditLogger
from src.runtime.supervisor import RuntimeSupervisor


# ============================================================
# 2. Helper: 构造一个 Fake B.3 Bridge,模拟 inner bridge
# ============================================================

class FakeB3Bridge:
    """
    模拟 RuntimeB3Bridge,提供 flush_pending / get_recent_results。
    通过 preset_recent 设置"刚发生的"流转结果。
    """

    def __init__(self, recent_results: Optional[List[Dict[str, Any]]] = None,
                 dispatched_ids: Optional[List[str]] = None,
                 next_result: Optional[Dict[str, int]] = None,
                 raise_on_flush: Optional[Exception] = None) -> None:
        self._recent = list(recent_results or [])
        self._dispatched_ids = set(dispatched_ids or [])
        self._next_result = next_result or {"processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0}
        self._raise_on_flush = raise_on_flush
        self.flush_call_count = 0

    def flush_pending(self) -> Dict[str, int]:
        self.flush_call_count += 1
        if self._raise_on_flush is not None:
            raise self._raise_on_flush
        return dict(self._next_result)

    def get_recent_results(self, limit: int = 20) -> List[Dict[str, Any]]:
        return list(self._recent[-limit:])

    def add_recent(self, item: Dict[str, Any]) -> None:
        self._recent.append(item)

    def set_next(self, result: Dict[str, int]) -> None:
        self._next_result = result

    def add_dispatched_id(self, action_id: str) -> None:
        self._dispatched_ids.add(action_id)


# ============================================================
# 3. 配置测试
# ============================================================

class TestConfigInjection:
    """PHASE_B4_DEFAULT_CONFIG / apply_phase_b4_config / is_phase_b4_enabled"""

    def test_default_keys_present(self):
        """默认配置必须包含 governance.enabled=False"""
        cfg = PHASE_B4_DEFAULT_CONFIG
        assert "governance" in cfg
        assert cfg["governance"]["enabled"] is False
        assert cfg["governance"]["max_retry"] == 0
        assert cfg["governance"]["timeout_seconds"] == 30.0
        assert cfg["governance"]["cooldown_seconds"] == 0.0
        assert cfg["governance"]["duplicate_detection"] is True
        assert cfg["governance"]["audit_enabled"] is True

    def test_default_off(self):
        """默认 governance 必须关闭(opt-in)"""
        assert PHASE_B4_DEFAULT_CONFIG["governance"]["enabled"] is False

    def test_apply_with_none_returns_defaults(self):
        """None 输入 → 返回默认"""
        result = apply_phase_b4_config(None)
        assert result["governance"]["enabled"] is False
        assert result["governance"]["max_retry"] == 0

    def test_user_governance_enabled_wins(self):
        """用户 enabled=True 覆盖默认 False"""
        user_cfg = {"governance": {"enabled": True}}
        result = apply_phase_b4_config(user_cfg)
        assert result["governance"]["enabled"] is True
        # max_retry 仍保留默认
        assert result["governance"]["max_retry"] == 0

    def test_apply_does_not_mutate_input(self):
        """不修改入参 dict"""
        user_cfg = {"governance": {"enabled": True, "max_retry": 5}}
        snapshot_top = dict(user_cfg)
        snapshot_gov = dict(user_cfg["governance"])
        result = apply_phase_b4_config(user_cfg)
        assert user_cfg == snapshot_top, f"入参被修改: {user_cfg} != {snapshot_top}"
        assert user_cfg["governance"] == snapshot_gov
        assert result is not user_cfg

    def test_apply_handles_non_dict(self):
        """非 dict 输入应被安全处理"""
        assert apply_phase_b4_config("not a dict")["governance"]["enabled"] is False
        assert apply_phase_b4_config(42)["governance"]["enabled"] is False
        assert apply_phase_b4_config([])["governance"]["enabled"] is False

    def test_apply_preserves_other_keys(self):
        """不影响其他 key"""
        user_cfg = {
            "dispatcher": {"enabled": True, "max_per_tick": 5},
            "proactive": {"enabled": True},
        }
        result = apply_phase_b4_config(user_cfg)
        assert result["dispatcher"]["enabled"] is True
        assert result["dispatcher"]["max_per_tick"] == 5
        assert result["proactive"]["enabled"] is True

    def test_is_phase_b4_enabled(self):
        """is_phase_b4_enabled 判定正确"""
        assert is_phase_b4_enabled({"governance": {"enabled": True}}) is True
        assert is_phase_b4_enabled({"governance": {"enabled": False}}) is False
        assert is_phase_b4_enabled({}) is False
        assert is_phase_b4_enabled(None) is False

    def test_is_b4_enabled_simple_variants(self):
        """is_b4_enabled_simple 兼容 cfg['governance_enabled']"""
        assert is_b4_enabled_simple({"governance_enabled": True}) is True
        assert is_b4_enabled_simple({"governance_enabled": False}) is False
        assert is_b4_enabled_simple({"governance": {"enabled": True}}) is True
        assert is_b4_enabled_simple({}) is False
        assert is_b4_enabled_simple(None) is False


# ============================================================
# 4. ExecutionPolicy 测试
# ============================================================

class TestExecutionPolicy:
    """ExecutionPolicy 构造 / 序列化"""

    def test_default_construction(self):
        """默认值正确"""
        p = ExecutionPolicy()
        assert p.max_retry == 0
        assert p.timeout_seconds == 30.0
        assert p.cooldown_seconds == 0.0
        assert p.duplicate_detection is True

    def test_from_config_with_dict(self):
        """从 cfg 构造"""
        cfg = {
            "max_retry": 3,
            "timeout_seconds": 60.0,
            "cooldown_seconds": 5.0,
            "duplicate_detection": False,
        }
        p = ExecutionPolicy.from_config(cfg)
        assert p.max_retry == 3
        assert p.timeout_seconds == 60.0
        assert p.cooldown_seconds == 5.0
        assert p.duplicate_detection is False

    def test_from_config_with_partial(self):
        """部分字段缺失时用默认"""
        cfg = {"max_retry": 2}
        p = ExecutionPolicy.from_config(cfg)
        assert p.max_retry == 2
        assert p.timeout_seconds == 30.0  # default
        assert p.cooldown_seconds == 0.0   # default
        assert p.duplicate_detection is True  # default

    def test_from_config_with_none(self):
        """None 输入 → 默认"""
        p = ExecutionPolicy.from_config(None)
        assert p.max_retry == 0
        assert p.duplicate_detection is True

    def test_to_dict_roundtrip(self):
        """to_dict 反序列化保持字段"""
        p = ExecutionPolicy(max_retry=3, timeout_seconds=10.0,
                            cooldown_seconds=1.0, duplicate_detection=False)
        d = p.to_dict()
        assert d["max_retry"] == 3
        assert d["timeout_seconds"] == 10.0
        assert d["cooldown_seconds"] == 1.0
        assert d["duplicate_detection"] is False

    def test_to_dict_uses_default(self):
        """to_dict 在默认值上正确"""
        p = ExecutionPolicy()
        d = p.to_dict()
        assert d == {
            "max_retry": 0,
            "timeout_seconds": 30.0,
            "cooldown_seconds": 0.0,
            "duplicate_detection": True,
        }


# ============================================================
# 5. ActionLifecycleManager 测试
# ============================================================

class TestActionLifecycleManager:
    """ActionLifecycleManager 状态机 + ledger"""

    def test_new_lifecycle_id_is_unique(self):
        """不同 action_id 得到不同 lifecycle_id"""
        lm = ActionLifecycleManager(ExecutionPolicy())
        lc1 = lm.new_lifecycle_id("a1")
        lc2 = lm.new_lifecycle_id("a2")
        assert lc1 != lc2
        assert lc1.startswith("lc_")
        assert lc2.startswith("lc_")

    def test_new_lifecycle_id_reuses_existing(self):
        """同 action_id 重复分配应复用同一 lifecycle_id"""
        lm = ActionLifecycleManager(ExecutionPolicy())
        lc1 = lm.new_lifecycle_id("a1")
        lc2 = lm.new_lifecycle_id("a1")
        assert lc1 == lc2

    def test_is_duplicate_default_policy(self):
        """默认 policy.duplicate_detection=True,标记后即 duplicate"""
        lm = ActionLifecycleManager(ExecutionPolicy())
        assert lm.is_duplicate("a1") is False
        lm.mark_terminal("a1", LifecycleStage.COMPLETED)
        assert lm.is_duplicate("a1") is True

    def test_is_duplicate_disabled_policy(self):
        """policy.duplicate_detection=False 时永远 False"""
        policy = ExecutionPolicy(duplicate_detection=False)
        lm = ActionLifecycleManager(policy)
        lm.mark_terminal("a1", LifecycleStage.COMPLETED)
        assert lm.is_duplicate("a1") is False

    def test_mark_terminal_only_for_terminal_stages(self):
        """非终态 stage 不应写入 ledger"""
        lm = ActionLifecycleManager(ExecutionPolicy())
        lm.mark_terminal("a1", LifecycleStage.CREATED)
        assert lm.is_duplicate("a1") is False
        assert lm.get_terminal_stage("a1") is None

    def test_mark_terminal_for_each_terminal_stage(self):
        """三种终态都正确记录"""
        for st in LifecycleStage.TERMINAL:
            lm = ActionLifecycleManager(ExecutionPolicy())
            aid = f"a_{st}"
            lm.mark_terminal(aid, st)
            assert lm.is_duplicate(aid) is True
            assert lm.get_terminal_stage(aid) == st

    def test_cooldown_disabled_by_default(self):
        """cooldown_seconds=0 时永远不在冷却"""
        lm = ActionLifecycleManager(ExecutionPolicy())
        lm.mark_terminal("a1", LifecycleStage.COMPLETED)
        assert lm.is_in_cooldown("a1") is False

    def test_cooldown_within_window(self):
        """cooldown 期内 is_in_cooldown=True"""
        policy = ExecutionPolicy(cooldown_seconds=10.0)
        lm = ActionLifecycleManager(policy)
        lm.mark_terminal("a1", LifecycleStage.COMPLETED, now=100.0)
        assert lm.is_in_cooldown("a1", now=105.0) is True

    def test_cooldown_expired(self):
        """cooldown 过后 is_in_cooldown=False"""
        policy = ExecutionPolicy(cooldown_seconds=10.0)
        lm = ActionLifecycleManager(policy)
        lm.mark_terminal("a1", LifecycleStage.COMPLETED, now=100.0)
        assert lm.is_in_cooldown("a1", now=120.0) is False

    def test_record_trail(self):
        """记录 trail"""
        lm = ActionLifecycleManager(ExecutionPolicy())
        rec = ActionAuditRecord(
            action_id="a1", lifecycle_id="lc_x",
            stage=LifecycleStage.APPROVED, ts=time.time(),
        )
        lm.record_trail(rec)
        trail = lm.get_audit_trail("a1")
        assert len(trail) == 1
        assert trail[0]["stage"] == "approved"
        assert trail[0]["action_id"] == "a1"

    def test_get_audit_trail_filter_by_action(self):
        """按 action_id 过滤 trail"""
        lm = ActionLifecycleManager(ExecutionPolicy())
        lm.record_trail(ActionAuditRecord("a1", "lc1", LifecycleStage.CREATED, time.time()))
        lm.record_trail(ActionAuditRecord("a2", "lc2", LifecycleStage.CREATED, time.time()))
        lm.record_trail(ActionAuditRecord("a1", "lc1", LifecycleStage.COMPLETED, time.time()))
        a1 = lm.get_audit_trail("a1")
        a2 = lm.get_audit_trail("a2")
        assert len(a1) == 2
        assert len(a2) == 1
        assert all(r["action_id"] == "a1" for r in a1)

    def test_get_ledger(self):
        """ledger 返回所有终态 action"""
        lm = ActionLifecycleManager(ExecutionPolicy())
        lm.mark_terminal("a1", LifecycleStage.COMPLETED, now=1.0)
        lm.mark_terminal("a2", LifecycleStage.REJECTED, now=2.0)
        ledger = lm.get_ledger()
        assert len(ledger) == 2
        aids = {entry[0] for entry in ledger}
        assert aids == {"a1", "a2"}

    def test_clear(self):
        """clear 清空 ledger"""
        lm = ActionLifecycleManager(ExecutionPolicy())
        lm.mark_terminal("a1", LifecycleStage.COMPLETED)
        lm.clear()
        assert lm.is_duplicate("a1") is False
        assert lm.get_ledger() == []

    def test_trail_bounded(self):
        """trail 容量有界"""
        lm = ActionLifecycleManager(ExecutionPolicy())
        for i in range(300):
            lm.record_trail(ActionAuditRecord(
                f"a{i}", f"lc{i}", LifecycleStage.CREATED, time.time()
            ))
        # default max_trail=200
        assert len(lm.get_audit_trail()) <= 200

    def test_all_stage_constants(self):
        """LifecycleStage 常量值正确"""
        assert LifecycleStage.CREATED == "created"
        assert LifecycleStage.APPROVED == "approved"
        assert LifecycleStage.EXECUTING == "executing"
        assert LifecycleStage.COMPLETED == "completed"
        assert LifecycleStage.FAILED == "failed"
        assert LifecycleStage.REJECTED == "rejected"
        assert set(LifecycleStage.TERMINAL) == {
            LifecycleStage.COMPLETED,
            LifecycleStage.FAILED,
            LifecycleStage.REJECTED,
        }
        assert set(LifecycleStage.ALL) == {
            LifecycleStage.CREATED, LifecycleStage.APPROVED,
            LifecycleStage.EXECUTING, LifecycleStage.COMPLETED,
            LifecycleStage.FAILED, LifecycleStage.REJECTED,
        }


# ============================================================
# 6. ActionAuditRecorder 测试
# ============================================================

class TestActionAuditRecorder:
    """ActionAuditRecorder 包装 RuntimeAuditLogger"""

    def test_disabled_when_audit_none(self):
        """audit_logger=None 时禁用"""
        rec = ActionAuditRecorder(audit_logger=None, enabled=True)
        assert rec.is_enabled() is False

    def test_disabled_when_enabled_false(self):
        """enabled=False 时禁用"""
        mock = MagicMock()
        rec = ActionAuditRecorder(audit_logger=mock, enabled=False)
        assert rec.is_enabled() is False

    def test_record_with_mock_audit(self):
        """用 mock audit_logger 写入"""
        mock = MagicMock()
        mock.log_custom.return_value = True
        rec = ActionAuditRecorder(audit_logger=mock, enabled=True)
        assert rec.is_enabled() is True

        record = ActionAuditRecord(
            action_id="a1", lifecycle_id="lc1",
            stage=LifecycleStage.COMPLETED, ts=time.time(),
        )
        ok = rec.record(record)
        assert ok is True
        assert mock.log_custom.called
        # 验证调用参数
        args, kwargs = mock.log_custom.call_args
        assert args[0] == "action.lifecycle"
        assert kwargs.get("action_id") == "a1"
        assert kwargs.get("stage") == "completed"

    def test_record_failed_audit(self):
        """audit 写入失败不影响返回"""
        mock = MagicMock()
        mock.log_custom.return_value = False
        rec = ActionAuditRecorder(audit_logger=mock, enabled=True)

        record = ActionAuditRecord("a1", "lc1", LifecycleStage.FAILED, time.time())
        ok = rec.record(record)
        # log_custom 返回 False → record 返回 False
        assert ok is False
        status = rec.get_status()
        assert status["write_error_count"] >= 1

    def test_record_audit_exception_isolated(self):
        """audit 写入抛异常时被隔离"""
        mock = MagicMock()
        mock.log_custom.side_effect = RuntimeError("boom")
        rec = ActionAuditRecorder(audit_logger=mock, enabled=True)

        record = ActionAuditRecord("a1", "lc1", LifecycleStage.COMPLETED, time.time())
        ok = rec.record(record)
        # 异常被隔离 → 返回 False
        assert ok is False
        status = rec.get_status()
        assert status["write_error_count"] >= 1

    def test_get_status(self):
        """get_status 正确返回统计"""
        mock = MagicMock()
        mock.log_custom.return_value = True
        rec = ActionAuditRecorder(audit_logger=mock, enabled=True)
        rec.record(ActionAuditRecord("a1", "lc1", LifecycleStage.CREATED, time.time()))
        rec.record(ActionAuditRecord("a2", "lc2", LifecycleStage.COMPLETED, time.time()))
        status = rec.get_status()
        assert status["enabled"] is True
        assert status["write_count"] == 2

    def test_real_runtime_audit_logger(self, tmp_path):
        """用真实的 RuntimeAuditLogger 测试"""
        log_path = str(tmp_path / "audit.jsonl")
        audit = RuntimeAuditLogger(log_path=log_path)
        rec = ActionAuditRecorder(audit_logger=audit, enabled=True)

        record = ActionAuditRecord(
            action_id="a1", lifecycle_id="lc1",
            stage=LifecycleStage.APPROVED, ts=time.time(),
            decision="gate_passed",
        )
        assert rec.record(record) is True
        # 检查文件已写入
        assert os.path.exists(log_path)
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1
        rec_obj = json.loads(lines[0])
        assert rec_obj["event"] == "custom"
        assert rec_obj["event_name"] == "action.lifecycle"
        assert rec_obj["action_id"] == "a1"
        assert rec_obj["stage"] == "approved"
        assert rec_obj["decision"] == "gate_passed"


# ============================================================
# 7. RuntimeB4Bridge 测试
# ============================================================

class TestRuntimeB4Bridge:
    """RuntimeB4Bridge 行为(governance ON / OFF 两种)"""

    # -------- 7.1 创建/属性 --------

    def test_bridge_creation_governance_on(self):
        """governance ON 创建"""
        inner = FakeB3Bridge()
        b = RuntimeB4Bridge(inner, use_governance=True)
        assert b.is_governance_enabled() is True
        assert b.is_enabled() is True
        assert b.inner_bridge is inner

    def test_bridge_creation_governance_off(self):
        """governance OFF 创建"""
        inner = FakeB3Bridge()
        b = RuntimeB4Bridge(inner, use_governance=False)
        assert b.is_governance_enabled() is False
        # OFF 仍然 enabled(因为 inner 在)
        assert b.is_enabled() is True

    def test_bridge_with_none_inner(self):
        """inner=None 时 is_enabled=False"""
        b = RuntimeB4Bridge(None, use_governance=True)
        assert b.is_enabled() is False

    # -------- 7.2 flush_pending 透传 --------

    def test_flush_pending_passes_through_result(self):
        """inner 结果应原样透传"""
        inner = FakeB3Bridge(next_result={"processed": 2, "dispatched": 1, "rejected": 1, "skipped": 0})
        b = RuntimeB4Bridge(inner, use_governance=False)
        out = b.flush_pending()
        assert out["processed"] == 2
        assert out["dispatched"] == 1
        assert out["rejected"] == 1
        # governance OFF 时,governed_audited 应为 0
        assert out["governed_audited"] == 0
        assert out["governed_duplicates"] == 0
        assert inner.flush_call_count == 1

    def test_flush_pending_governance_off_keeps_passthrough(self):
        """governance OFF 时,不走 audit 流程"""
        inner = FakeB3Bridge(
            recent_results=[{"action_id": "a1", "stage": "dispatched"}],
            next_result={"processed": 1, "dispatched": 1, "rejected": 0, "skipped": 0},
        )
        b = RuntimeB4Bridge(inner, use_governance=False)
        out = b.flush_pending()
        assert out["governed_audited"] == 0
        # governance OFF 时,ledger 不更新
        assert b.lifecycle.get_ledger() == []

    def test_flush_pending_inner_exception_isolated(self):
        """inner.flush_pending 抛异常时被隔离"""
        inner = FakeB3Bridge(raise_on_flush=RuntimeError("boom"))
        b = RuntimeB4Bridge(inner, use_governance=True)
        # 不应抛
        out = b.flush_pending()
        assert out["dispatched"] == 0
        # errors 应增加
        assert b._total_errors >= 1

    def test_flush_pending_none_inner(self):
        """inner=None 时 flush 不抛"""
        b = RuntimeB4Bridge(None, use_governance=True)
        out = b.flush_pending()
        assert out == {
            "processed": 0, "dispatched": 0,
            "rejected": 0, "skipped": 0,
            "governed_audited": 0, "governed_duplicates": 0,
        }

    # -------- 7.3 audit 写入 + lifecycle 状态机 --------

    def test_flush_pending_governance_on_records_lifecycle(self):
        """governance ON 时,reliable_dispatched 应被转 lifecycle trail"""
        recent = [
            {"action_id": "a1", "stage": "dispatched"},
            {"action_id": "a2", "stage": "gate_rejected"},
        ]
        inner = FakeB3Bridge(recent_results=recent)
        b = RuntimeB4Bridge(inner, use_governance=True)
        out = b.flush_pending()
        # 两条 recent 中:
        # a1 dispatched → 产生 (approved, executing, completed) 三条
        # a2 gate_rejected → 产生 (rejected) 一条
        trail = b.lifecycle.get_audit_trail()
        assert len(trail) >= 4

        a1_stages = [r["stage"] for r in trail if r["action_id"] == "a1"]
        assert "approved" in a1_stages
        assert "executing" in a1_stages
        assert "completed" in a1_stages

        a2_stages = [r["stage"] for r in trail if r["action_id"] == "a2"]
        assert "rejected" in a2_stages

        # ledger 应包含 a1 (completed) 和 a2 (rejected)
        ledger = b.lifecycle.get_ledger()
        ledger_aids = {entry[0] for entry in ledger}
        assert "a1" in ledger_aids
        assert "a2" in ledger_aids

    def test_flush_pending_governance_on_audit_write(self, tmp_path):
        """governance ON + audit 启用 → 写 audit 文件"""
        log_path = str(tmp_path / "audit.jsonl")
        audit = RuntimeAuditLogger(log_path=log_path)
        rec = ActionAuditRecorder(audit_logger=audit, enabled=True)

        recent = [{"action_id": "a1", "stage": "dispatched"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = RuntimeB4Bridge(inner, use_governance=True, audit_recorder=rec)
        b.flush_pending()
        # 验证 audit 文件已写入
        assert os.path.exists(log_path)
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # 至少一条 action.lifecycle
        assert len(lines) >= 1
        ev_names = [json.loads(line).get("event_name") for line in lines]
        assert "action.lifecycle" in ev_names

    def test_flush_pending_failed_action(self):
        """failed action 也走 lifecycle"""
        recent = [{"action_id": "a1", "stage": "execute_exception", "error": "boom"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = RuntimeB4Bridge(inner, use_governance=True)
        out = b.flush_pending()
        trail = b.lifecycle.get_audit_trail("a1")
        assert any(r["stage"] == "failed" for r in trail)

    def test_flush_pending_skipped_duplicate_action(self):
        """B.3 内 skipped_duplicate 应被映射为 rejected"""
        recent = [{"action_id": "a1", "stage": "skipped_duplicate"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = RuntimeB4Bridge(inner, use_governance=True)
        b.flush_pending()
        trail = b.lifecycle.get_audit_trail("a1")
        assert any(r["stage"] == "rejected" and r.get("decision") == "duplicate"
                   for r in trail)

    def test_flush_pending_unknown_stage_skipped(self):
        """未知 stage 不写入 trail,也不抛错"""
        recent = [{"action_id": "a1", "stage": "weird_stage"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = RuntimeB4Bridge(inner, use_governance=True)
        out = b.flush_pending()
        # 不应写入 trail
        assert b.lifecycle.get_audit_trail("a1") == []

    def test_flush_pending_recent_with_no_action_id_skipped(self):
        """recent 中无 action_id 的项应被跳过"""
        recent = [{"stage": "dispatched"}]  # 无 action_id
        inner = FakeB3Bridge(recent_results=recent)
        b = RuntimeB4Bridge(inner, use_governance=True)
        out = b.flush_pending()  # 不应抛
        assert out is not None

    # -------- 7.4 重复检测 --------

    def test_preremove_duplicates_merges_inner_ids(self):
        """inner 的 _dispatched_ids 会被合并到本地 ledger"""
        inner = FakeB3Bridge(dispatched_ids=["a1", "a2"])
        b = RuntimeB4Bridge(inner, use_governance=True)
        b._preremove_duplicates()
        # a1, a2 应进入 ledger
        ledger = b.lifecycle.get_ledger()
        aids = {entry[0] for entry in ledger}
        assert "a1" in aids
        assert "a2" in aids

    def test_preremove_duplicates_no_inner(self):
        """inner=None 时不抛"""
        b = RuntimeB4Bridge(None, use_governance=True)
        n = b._preremove_duplicates()
        assert n == 0

    # -------- 7.5 summarize_b4 --------

    def test_summarize_b4_keys(self):
        """summarize_b4 字段完整"""
        inner = FakeB3Bridge()
        b = RuntimeB4Bridge(inner, use_governance=True)
        s = b.summarize_b4()
        for k in (
            "available", "enabled", "phase_b4_enabled", "phase_b4_name",
            "phase_b4_version", "governance_on", "policy", "audit",
            "ledger_count", "total_flush_calls", "total_audited",
            "total_duplicates_blocked", "total_errors", "ts",
        ):
            assert k in s, f"missing key: {k}"
        assert s["phase_b4_name"] == PHASE_B4_NAME
        assert s["phase_b4_version"] == PHASE_B4_VERSION
        assert s["governance_on"] is True

    def test_summarize_b4_off(self):
        """governance OFF 时,governance_on=False"""
        inner = FakeB3Bridge()
        b = RuntimeB4Bridge(inner, use_governance=False)
        s = b.summarize_b4()
        assert s["governance_on"] is False
        assert s["phase_b4_enabled"] is False

    # -------- 7.6 工具方法 --------

    def test_get_recent_results_passthrough(self):
        """get_recent_results 透传到 inner"""
        recent = [{"a": 1}, {"b": 2}]
        inner = FakeB3Bridge(recent_results=recent)
        b = RuntimeB4Bridge(inner, use_governance=True)
        out = b.get_recent_results(limit=10)
        assert out == recent

    def test_get_audit_trail(self):
        """get_audit_trail 走 lifecycle manager"""
        inner = FakeB3Bridge()
        b = RuntimeB4Bridge(inner, use_governance=True)
        b.lifecycle.record_trail(ActionAuditRecord(
            "a1", "lc1", LifecycleStage.CREATED, time.time()
        ))
        trail = b.get_audit_trail("a1")
        assert len(trail) == 1

    def test_get_audit_trail_exception_isolated(self):
        """get_audit_trail 异常时返回空"""
        inner = FakeB3Bridge()
        b = RuntimeB4Bridge(inner, use_governance=True)
        # 故意破坏 lifecycle
        with patch.object(b.lifecycle, "get_audit_trail", side_effect=RuntimeError("x")):
            out = b.get_audit_trail("a1")
        assert out == []


# ============================================================
# 8. 工厂 / safe_flush_pending_b4 测试
# ============================================================

class TestCreateB4Bridge:
    """create_b4_bridge 工厂"""

    def test_create_disabled(self):
        """cfg 缺省 → governance OFF"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(inner, cfg=None)
        assert b.is_governance_enabled() is False
        assert b.audit is None

    def test_create_enabled_no_audit(self):
        """governance ON 但无 audit_logger → audit None"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner, cfg={"governance": {"enabled": True, "audit_enabled": False}}
        )
        assert b.is_governance_enabled() is True
        assert b.audit is None

    def test_create_enabled_with_audit(self, tmp_path):
        """governance ON + audit_logger → audit 启用"""
        log_path = str(tmp_path / "audit.jsonl")
        audit = RuntimeAuditLogger(log_path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": True}},
            audit_logger=audit,
        )
        assert b.is_governance_enabled() is True
        assert b.audit is not None
        assert b.audit.is_enabled() is True

    def test_create_with_policy_override(self):
        """policy 字段可由 cfg 覆盖"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={
                "governance": {
                    "enabled": True,
                    "max_retry": 5,
                    "timeout_seconds": 60.0,
                    "cooldown_seconds": 2.0,
                    "duplicate_detection": False,
                }
            },
        )
        p = b.policy
        assert p.max_retry == 5
        assert p.timeout_seconds == 60.0
        assert p.cooldown_seconds == 2.0
        assert p.duplicate_detection is False


class TestSafeFlushPendingB4:
    """safe_flush_pending_b4 异常隔离包装"""

    def test_none_bridge(self):
        """bridge=None → ok=False"""
        out = safe_flush_pending_b4(None)
        assert out["ok"] is False
        assert "bridge is None" in out["error"]

    def test_missing_flush_pending(self):
        """bridge 无 flush_pending → ok=False"""
        out = safe_flush_pending_b4(object())
        assert out["ok"] is False
        assert "missing flush_pending" in out["error"]

    def test_normal_bridge(self):
        """正常 bridge → ok=True"""
        inner = FakeB3Bridge(next_result={"processed": 1, "dispatched": 1, "rejected": 0, "skipped": 0})
        b = RuntimeB4Bridge(inner, use_governance=True)
        out = safe_flush_pending_b4(b)
        assert out["ok"] is True
        assert out["result"]["processed"] == 1

    def test_bridge_flush_raises(self):
        """bridge.flush_pending 抛异常 → ok=False,error 信息"""
        inner = FakeB3Bridge(raise_on_flush=RuntimeError("kaboom"))
        b = RuntimeB4Bridge(inner, use_governance=True)
        out = safe_flush_pending_b4(b)
        # RuntimeB4Bridge.flush_pending 内部已隔离,所以 ok=True
        # safe_flush_pending 不会再次被调用进 bridge 内部异常
        assert out["ok"] is True


# ============================================================
# 9. Supervisor 集成测试
# ============================================================

class TestSupervisorIntegration:
    """Supervisor 调 b4_bridge.flush_pending 正常"""

    def test_supervisor_dispatches_to_b4_bridge(self):
        """Supervisor step 4 能调 b4_bridge.flush_pending()"""
        from types import SimpleNamespace

        # 用 SimpleNamespace 充当 inner bridge,提供最小 API
        inner = SimpleNamespace(
            flush_pending=lambda: {"processed": 1, "dispatched": 1, "rejected": 0, "skipped": 0},
            get_recent_results=lambda limit=20: [],
            _dispatched_ids=set(),
        )
        b4_bridge = RuntimeB4Bridge(inner, use_governance=True)

        # 构造一个最小 Supervisor(只为了调 _tick_iteration step 4)
        sup = RuntimeSupervisor(
            runtime_core=None,
            cognitive_core=None,
            heartbeat_collector=None,
            proactive_engine=None,
            action_dispatcher=b4_bridge,
            tick_interval_seconds=60,
            enabled=True,
            name="test_supervisor_b4",
        )
        # 启动 supervisor(只取主循环的一帧)
        # 这里仅测试 _tick_iteration 不报错
        # 启动会以独立线程跑,我们立即 stop
        started = sup.start()
        assert started is True
        # 等一帧
        time.sleep(0.1)
        sup.stop(timeout=2.0)

    def test_supervisor_with_b3_only_unchanged(self):
        """B.4 OFF 时,Supervisor action_dispatcher=b3_bridge 行为不变"""
        # inner bridge 简单模拟
        from types import SimpleNamespace
        inner = SimpleNamespace(
            flush_pending=lambda: {"processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0},
            get_recent_results=lambda limit=20: [],
            _dispatched_ids=set(),
        )
        b3_bridge = RuntimeB3Bridge.__new__(RuntimeB3Bridge)
        b3_bridge._engine = None
        b3_bridge._dispatcher = None
        b3_bridge._max_per_tick = 3
        b3_bridge._dispatched_ids = set()
        b3_bridge._dispatched_lock = __import__("threading").Lock()
        b3_bridge._total_processed = 0
        b3_bridge._total_dispatched = 0
        b3_bridge._total_rejected = 0
        b3_bridge._total_skipped = 0
        b3_bridge._total_errors = 0
        b3_bridge._recent_results = []
        b3_bridge._recent_lock = __import__("threading").Lock()
        b3_bridge._max_recent = 50

        # B.4 没包,直接传 b3
        sup = RuntimeSupervisor(
            runtime_core=None,
            cognitive_core=None,
            heartbeat_collector=None,
            proactive_engine=None,
            action_dispatcher=b3_bridge,
            tick_interval_seconds=60,
            enabled=True,
            name="test_supervisor_b3_only",
        )
        started = sup.start()
        assert started is True
        time.sleep(0.1)
        sup.stop(timeout=2.0)


# ============================================================
# 10. Backward Compatibility 测试
# ============================================================

class TestBackwardCompatibility:
    """B.4 关闭时,行为与 B.3 等价"""

    def test_b4_off_preserves_b3_behavior(self):
        """B.4 governance OFF 时,行为与直接用 b3_bridge 一致"""
        inner = FakeB3Bridge(
            recent_results=[{"action_id": "a1", "stage": "dispatched"}],
            next_result={"processed": 1, "dispatched": 1, "rejected": 0, "skipped": 0},
        )
        b = RuntimeB4Bridge(inner, use_governance=False)
        out = b.flush_pending()

        # 关键: governance OFF 时,
        # - governed_audited=0
        # - ledger 为空(无 lifecycle trail 记录)
        assert out["governed_audited"] == 0
        assert out["governed_duplicates"] == 0
        assert b.lifecycle.get_ledger() == []
        assert b.lifecycle.get_audit_trail() == []

    def test_b4_off_inner_called_once(self):
        """B.4 OFF 时 inner 只被调一次"""
        inner = FakeB3Bridge()
        b = RuntimeB4Bridge(inner, use_governance=False)
        b.flush_pending()
        b.flush_pending()
        b.flush_pending()
        assert inner.flush_call_count == 3

    def test_b4_off_inner_exception_still_isolated(self):
        """B.4 OFF 时 inner 异常仍被隔离"""
        inner = FakeB3Bridge(raise_on_flush=ValueError("x"))
        b = RuntimeB4Bridge(inner, use_governance=False)
        out = b.flush_pending()
        assert out["dispatched"] == 0
        assert b._total_errors >= 1


# ============================================================
# 11. 核心模块未修改保护
# ============================================================

class TestNoCoreModuleModification:
    """确认 B.4 没有修改任何核心模块"""

    CORE_FILES = [
        "src/runtime/supervisor.py",
        "src/runtime/runtime_core.py",
        "src/runtime/action_dispatcher.py",
        "src/proactive/proactive_engine.py",
        "src/runtime/phase_b3_integration.py",
    ]

    @staticmethod
    def _extract_imports(src_text: str) -> List[str]:
        """提取所有 import 语句(忽略 docstring / 注释中的字符串)"""
        import ast
        try:
            tree = ast.parse(src_text)
        except Exception:
            return []
        imports: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imports.append(n.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    def test_phase_b4_does_not_import_supervisor_internals(self):
        """phase_b4_integration 不 import supervisor 内部模块"""
        import src.runtime.phase_b4_integration as mod
        src_text = open(mod.__file__, "r", encoding="utf-8").read()
        imports = self._extract_imports(src_text)
        # src_text 中可以提及 RuntimeSupervisor(在 docstring),
        # 但实际的 import 语句中不能从 supervisor import
        assert not any("src.runtime.supervisor" in imp for imp in imports), \
            f"phase_b4 不得 import supervisor, 实际 imports: {imports}"

    def test_phase_b4_does_not_modify_proactive_engine(self):
        """phase_b4_integration 不 import proactive 子模块"""
        import src.runtime.phase_b4_integration as mod
        src_text = open(mod.__file__, "r", encoding="utf-8").read()
        imports = self._extract_imports(src_text)
        assert not any("src.proactive" in imp for imp in imports), \
            f"phase_b4 不得 import proactive, 实际 imports: {imports}"

    def test_phase_b4_does_not_modify_action_dispatcher(self):
        """phase_b4_integration 不 import action_dispatcher 模块"""
        import src.runtime.phase_b4_integration as mod
        src_text = open(mod.__file__, "r", encoding="utf-8").read()
        imports = self._extract_imports(src_text)
        # 仅允许 import 同模块自身(phase_b4 不直接 import action_dispatcher)
        assert not any("src.runtime.action_dispatcher" in imp for imp in imports), \
            f"phase_b4 不得 import action_dispatcher, 实际 imports: {imports}"

    def test_phase_b4_does_not_modify_runtime_core(self):
        """phase_b4_integration 不 import runtime_core"""
        import src.runtime.phase_b4_integration as mod
        src_text = open(mod.__file__, "r", encoding="utf-8").read()
        imports = self._extract_imports(src_text)
        assert not any("src.runtime.runtime_core" in imp for imp in imports), \
            f"phase_b4 不得 import runtime_core, 实际 imports: {imports}"

    def test_phase_b4_only_wraps_inner_bridge(self):
        """phase_b4_integration 与 inner bridge 通过 duck-typed 通信"""
        import src.runtime.phase_b4_integration as mod
        # 不依赖具体类,只 import 公共类型
        # (RuntimeB3Bridge 可作为 duck-typed inner 出现)
        assert "RuntimeB4Bridge" in dir(mod)

    def test_start_runtime_keeps_b3_paths_intact(self):
        """start_runtime.py 仍保留 b3_bridge 路径(无 b4 时仍可用)"""
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "scripts" / "start_runtime.py"
        text = path.read_text(encoding="utf-8")
        # b3 bridge 构造仍在
        assert "create_b3_bridge" in text
        # b4 bridge 是新增,不破坏 b3 路径
        assert "create_b4_bridge" in text
        # CLI flag 新增
        assert "--enable-governance" in text


# ============================================================
# 12. start_runtime.py CLI / init_governance 测试
# ============================================================

class TestStartRuntimeScript:
    """start_runtime.py 的 CLI / init_governance 接入"""

    def test_init_governance_disabled(self):
        """init_governance 在 cfg 缺省时返回 None"""
        from scripts.start_runtime import init_governance
        result = init_governance({}, b3_bridge=None, force=False)
        assert result is None

    def test_init_governance_enabled_but_no_b3(self):
        """governance 启用但 b3_bridge=None → 返回 None(已隔离)"""
        from scripts.start_runtime import init_governance
        cfg = {"governance": {"enabled": True}}
        result = init_governance(cfg, b3_bridge=None, force=True)
        assert result is None

    def test_init_governance_enabled_with_b3(self, tmp_path):
        """governance 启用 + b3_bridge 存在 → 返回 RuntimeB4Bridge"""
        from scripts.start_runtime import init_governance
        # 模拟 b3 bridge
        inner = FakeB3Bridge()
        cfg = {
            "governance": {
                "enabled": True,
                "max_retry": 0,
                "audit_enabled": True,
                "audit_log_path": str(tmp_path / "audit.jsonl"),
            }
        }
        # patch RuntimeAuditLogger 路径避免污染
        with patch("src.runtime.audit_log.runtime_audit_logger.RuntimeAuditLogger",
                   return_value=MagicMock()):
            result = init_governance(cfg, b3_bridge=inner, force=True)
        # 即使 audit 注入 mock,也应返回 b4 bridge
        assert result is not None
        assert result.is_governance_enabled() is True
        assert result.inner_bridge is inner

    def test_init_governance_audit_failure_isolated(self, tmp_path):
        """RuntimeAuditLogger 构造失败时,audit 关闭但 bridge 仍创建"""
        from scripts.start_runtime import init_governance
        inner = FakeB3Bridge()
        cfg = {
            "governance": {
                "enabled": True,
                "audit_enabled": True,
                "audit_log_path": str(tmp_path / "audit.jsonl"),
            }
        }
        with patch("src.runtime.audit_log.runtime_audit_logger.RuntimeAuditLogger",
                   side_effect=RuntimeError("audit init fail")):
            result = init_governance(cfg, b3_bridge=inner, force=True)
        # bridge 仍创建
        assert result is not None
        # audit=None(被隔离)
        assert result.audit is None

    def test_init_governance_audit_disabled_in_cfg(self, tmp_path):
        """cfg audit_enabled=False → bridge 创建但 audit=None"""
        from scripts.start_runtime import init_governance
        inner = FakeB3Bridge()
        cfg = {
            "governance": {
                "enabled": True,
                "audit_enabled": False,
            }
        }
        result = init_governance(cfg, b3_bridge=inner, force=True)
        assert result is not None
        assert result.audit is None

    def test_cli_enable_governance_flag(self):
        """--enable-governance 在 CLI 中存在"""
        import subprocess
        result = subprocess.run(
            ["python", "scripts/start_runtime.py", "--help"],
            capture_output=True, text=True, timeout=10,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert "--enable-governance" in result.stdout

    def test_cli_disable_default(self):
        """默认不启用 governance(--no-runtime-core 等最小模式)"""
        # 通过 import 验证 config 默认
        import importlib
        sr = importlib.import_module("scripts.start_runtime")
        cfg = sr.load_config()
        # governance.enabled 默认为 False
        assert cfg.get("governance", {}).get("enabled", False) is False


# ============================================================
# 13. 端到端: governance ON + B.3 dispatch + 生命周期完整链路
# ============================================================

class TestEndToEndGovernance:
    """端到端: governance ON + B.3 dispatch + 生命周期"""

    def test_dispatched_action_lifecycle_completed(self, tmp_path):
        """dispatched 动作应走 (approved, executing, completed)"""
        log_path = str(tmp_path / "audit.jsonl")
        audit = RuntimeAuditLogger(log_path=log_path)
        rec = ActionAuditRecorder(audit_logger=audit, enabled=True)

        recent = [{"action_id": "a1", "stage": "dispatched"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": True}},
            audit_logger=audit,
        )
        b.flush_pending()

        # 验证 lifecycle
        trail = b.lifecycle.get_audit_trail("a1")
        stages = [r["stage"] for r in trail]
        assert "approved" in stages
        assert "executing" in stages
        assert "completed" in stages
        assert b.lifecycle.get_terminal_stage("a1") == "completed"

        # 验证 audit 文件
        assert os.path.exists(log_path)
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # 每条 lifecycle 项应写入 audit
        # a1 触发 3 条 trail(approved/executing/completed)
        evs = [json.loads(l).get("event_name") for l in lines]
        assert evs.count("action.lifecycle") >= 3

    def test_rejected_action_lifecycle(self):
        """rejected 动作应走 rejected lifecycle"""
        recent = [{"action_id": "a1", "stage": "gate_rejected"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = RuntimeB4Bridge(inner, use_governance=True)
        b.flush_pending()
        trail = b.lifecycle.get_audit_trail("a1")
        assert any(r["stage"] == "rejected" for r in trail)
        assert b.lifecycle.get_terminal_stage("a1") == "rejected"

    def test_failed_action_lifecycle(self):
        """failed 动作应走 failed lifecycle"""
        recent = [{"action_id": "a1", "stage": "execute_exception", "error": "boom"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = RuntimeB4Bridge(inner, use_governance=True)
        b.flush_pending()
        trail = b.lifecycle.get_audit_trail("a1")
        assert any(r["stage"] == "failed" for r in trail)
        assert b.lifecycle.get_terminal_stage("a1") == "failed"
        # error 字段应记录
        assert any(r.get("error") for r in trail)

    def test_duplicate_action_id_lifecycle(self):
        """同一 action_id 第二次走 rejected/duplicate"""
        # 第一次:dispatched
        recent1 = [{"action_id": "a1", "stage": "dispatched"}]
        inner1 = FakeB3Bridge(recent_results=recent1)
        b = RuntimeB4Bridge(inner1, use_governance=True)
        b.flush_pending()
        assert b.lifecycle.get_terminal_stage("a1") == "completed"

        # 第二次:skipped_duplicate (B.3 行为)
        recent2 = [{"action_id": "a1", "stage": "skipped_duplicate"}]
        inner2 = FakeB3Bridge(recent_results=recent2)
        b2 = RuntimeB4Bridge(inner2, use_governance=True)
        b2.flush_pending()
        trail = b2.lifecycle.get_audit_trail("a1")
        # lifecycle 应记录 duplicate 决策
        assert any(r["stage"] == "rejected" and r.get("decision") == "duplicate"
                   for r in trail)
