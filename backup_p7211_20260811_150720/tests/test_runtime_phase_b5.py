# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b5.py

Phase B.5 Runtime Integration —— Action Persistence & Recovery 测试

覆盖:
  1. 配置测试(default / override / 不修改入参)
  2. ActionPersistenceManager 写入测试(JSONL append-only)
  3. 查询接口(get_action / get_history / get_recent_actions)
  4. 恢复(load_state)
  5. 与 B.4 lifecycle 集成(persistence 钩子)
  6. 重复 action 检测(重启后已 terminal 不再 dispatch)
  7. 异常写盘(降级为 memory-only)
  8. B.4 兼容(B.4 没传 persistence 时不破)
  9. 重启恢复模拟(进程 1 写 → 进程 2 读)
 10. start_runtime.py init_persistence 集成
 11. 核心模块未修改保护

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化所有外部依赖
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# 1. 测试目标导入
# ============================================================

from src.runtime.action_persistence import (
    PHASE_B5_DEFAULT_CONFIG,
    PHASE_B5_NAME,
    PHASE_B5_VERSION,
    DEFAULT_PERSISTENCE_PATH,
    SCHEMA_VERSION,
    apply_phase_b5_config,
    is_phase_b5_enabled,
    is_b5_enabled_simple,
    ActionPersistenceRecord,
    ActionPersistenceManager,
    create_persistence_manager,
    safe_persist,
)

from src.runtime.phase_b4_integration import (
    PHASE_B4_DEFAULT_CONFIG,
    LifecycleStage,
    ExecutionPolicy,
    ActionLifecycleManager,
    RuntimeB4Bridge,
    create_b4_bridge,
)

from src.runtime.audit_log.runtime_audit_logger import RuntimeAuditLogger


# ============================================================
# 2. Helper
# ============================================================

class FakeB3Bridge:
    """模拟 B.3 bridge,提供 flush_pending / get_recent_results"""

    def __init__(self, recent_results: Optional[List[Dict[str, Any]]] = None,
                 dispatched_ids: Optional[List[str]] = None,
                 next_result: Optional[Dict[str, int]] = None) -> None:
        self._recent = list(recent_results or [])
        self._dispatched_ids = set(dispatched_ids or [])
        self._next_result = next_result or {"processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0}
        self.flush_call_count = 0

    def flush_pending(self) -> Dict[str, int]:
        self.flush_call_count += 1
        return dict(self._next_result)

    def get_recent_results(self, limit: int = 20) -> List[Dict[str, Any]]:
        return list(self._recent[-limit:])

    def add_recent(self, item: Dict[str, Any]) -> None:
        self._recent.append(item)


# ============================================================
# 3. 配置测试
# ============================================================

class TestConfigInjection:
    """PHASE_B5_DEFAULT_CONFIG / apply_phase_b5_config / is_phase_b5_enabled"""

    def test_default_keys_present(self):
        """默认配置必须包含 action_persistence.enabled=True"""
        cfg = PHASE_B5_DEFAULT_CONFIG
        assert "action_persistence" in cfg
        assert cfg["action_persistence"]["enabled"] is True
        assert cfg["action_persistence"]["path"] == "data/action_lifecycle.jsonl"
        assert cfg["action_persistence"]["auto_recover"] is True

    def test_default_on(self):
        """B.5 默认开启(但不阻塞)"""
        assert PHASE_B5_DEFAULT_CONFIG["action_persistence"]["enabled"] is True

    def test_apply_with_none_returns_defaults(self):
        """None 输入 → 返回默认"""
        result = apply_phase_b5_config(None)
        assert result["action_persistence"]["enabled"] is True

    def test_user_can_disable(self):
        """用户可显式关闭"""
        result = apply_phase_b5_config({
            "action_persistence": {"enabled": False}
        })
        assert result["action_persistence"]["enabled"] is False

    def test_apply_does_not_mutate_input(self):
        """不修改入参 dict"""
        user_cfg = {"action_persistence": {"enabled": False, "path": "/tmp/x.jsonl"}}
        snapshot = json.dumps(user_cfg, sort_keys=True)
        result = apply_phase_b5_config(user_cfg)
        assert json.dumps(user_cfg, sort_keys=True) == snapshot
        assert result is not user_cfg

    def test_apply_handles_non_dict(self):
        """非 dict 输入应被安全处理"""
        assert apply_phase_b5_config("not a dict")["action_persistence"]["enabled"] is True
        assert apply_phase_b5_config(42)["action_persistence"]["enabled"] is True
        assert apply_phase_b5_config([])["action_persistence"]["enabled"] is True

    def test_is_phase_b5_enabled(self):
        """is_phase_b5_enabled 判定正确"""
        assert is_phase_b5_enabled({"action_persistence": {"enabled": True}}) is True
        assert is_phase_b5_enabled({"action_persistence": {"enabled": False}}) is False
        assert is_phase_b5_enabled({}) is True  # 默认 True
        assert is_phase_b5_enabled(None) is False

    def test_is_b5_enabled_simple_variants(self):
        """is_b5_enabled_simple 兼容 cfg['persistence_enabled']"""
        assert is_b5_enabled_simple({"persistence_enabled": True}) is True
        assert is_b5_enabled_simple({"persistence_enabled": False}) is False
        assert is_b5_enabled_simple({"action_persistence": {"enabled": True}}) is True
        assert is_b5_enabled_simple({}) is True  # 默认 True
        assert is_b5_enabled_simple(None) is False


# ============================================================
# 4. ActionPersistenceManager 写入测试
# ============================================================

class TestPersistenceWrite:
    """ActionPersistenceManager 写盘行为"""

    def test_persist_event_creates_file(self, tmp_path):
        """persist_event 创建文件"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ok = mgr.persist_event("a1", "lc1", LifecycleStage.COMPLETED, decision="dispatched")
        assert ok is True
        assert os.path.exists(log_path)

    def test_persist_event_writes_jsonl(self, tmp_path):
        """写入的 JSONL 行可解析"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        mgr.persist_event("a1", "lc1", LifecycleStage.COMPLETED, decision="dispatched")
        mgr.persist_event("a2", "lc2", LifecycleStage.REJECTED, decision="gate_rejected")

        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 2
        rec1 = json.loads(lines[0])
        rec2 = json.loads(lines[1])
        assert rec1["action_id"] == "a1"
        assert rec1["stage"] == "completed"
        assert rec1["decision"] == "dispatched"
        assert rec1["schema_version"] == SCHEMA_VERSION
        assert rec2["action_id"] == "a2"
        assert rec2["stage"] == "rejected"

    def test_persist_event_appends(self, tmp_path):
        """多次写入 append-only"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        for i in range(10):
            mgr.persist_event(f"a{i}", f"lc{i}", LifecycleStage.CREATED, decision="created")
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 10
        for i, line in enumerate(lines):
            assert json.loads(line)["action_id"] == f"a{i}"

    def test_persist_event_with_result_and_error(self, tmp_path):
        """result / error 字段正确写入"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        mgr.persist_event(
            "a1", "lc1", LifecycleStage.FAILED,
            decision="execute_exception",
            result={"code": 500},
            error="boom",
        )
        with open(log_path, "r", encoding="utf-8") as f:
            rec = json.loads(f.readline())
        assert rec["result"] == {"code": 500}
        assert rec["error"] == "boom"

    def test_disabled_persistence_no_write(self, tmp_path):
        """enabled=False 时不写盘(但内存仍记)"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path, enabled=False)
        ok = mgr.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        # 写盘被禁 → 返回 False
        assert ok is False
        # 文件不应存在
        assert not os.path.exists(log_path)
        # 但内存仍记
        assert mgr.get_action("a1") is not None

    def test_write_count_increments(self, tmp_path):
        """write_count 正确累加"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        mgr.persist_event("a1", "lc1", LifecycleStage.CREATED)
        mgr.persist_event("a2", "lc2", LifecycleStage.COMPLETED)
        mgr.persist_event("a3", "lc3", LifecycleStage.FAILED)
        assert mgr.write_count == 3

    def test_empty_action_id_skipped(self, tmp_path):
        """action_id 为空时直接返回 False,不写盘"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ok = mgr.persist_event("", "lc1", LifecycleStage.CREATED)
        assert ok is False
        assert mgr.write_count == 0


# ============================================================
# 5. 异常写盘(降级为 memory-only)
# ============================================================

class TestPersistenceFailureIsolation:
    """写盘失败自动降级"""

    def test_degraded_after_write_failure(self, tmp_path):
        """写盘失败 → degraded=True"""
        log_path = str(tmp_path / "readonly" / "test.jsonl")
        # 让 os.makedirs 失败:用文件占位
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file, not a directory")
        bad_path = str(blocker / "test.jsonl")  # 父路径是文件,无法 makedirs

        mgr = ActionPersistenceManager(path=bad_path)
        ok = mgr.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        assert ok is False
        assert mgr.is_degraded is True
        # 内存索引仍有(降级到 memory)
        assert mgr.get_action("a1") is not None
        assert mgr.write_error_count >= 1

    def test_subsequent_writes_no_op_after_degraded(self, tmp_path):
        """降级后,后续 persist 直接 no-op(不尝试写盘)"""
        bad_path = str(tmp_path / "blocker" / "test.jsonl")
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file")

        mgr = ActionPersistenceManager(path=bad_path)
        mgr.persist_event("a1", "lc1", LifecycleStage.COMPLETED)  # 触发降级
        assert mgr.is_degraded is True

        before = mgr.write_count
        mgr.persist_event("a2", "lc2", LifecycleStage.COMPLETED)
        # 降级后,write_count 不再增加(不尝试写盘)
        assert mgr.write_count == before
        # 但内存仍记
        assert mgr.get_action("a2") is not None

    def test_health_check_includes_degraded_flag(self, tmp_path):
        """health_check 反映 degraded 状态"""
        bad_path = str(tmp_path / "blocker" / "test.jsonl")
        blocker = tmp_path / "blocker"
        blocker.write_text("x")

        mgr = ActionPersistenceManager(path=bad_path)
        mgr.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        h = mgr.health_check()
        assert h["degraded"] is True
        assert h["write_error_count"] >= 1

    def test_safe_persist_handles_none_manager(self):
        """safe_persist 处理 manager=None"""
        ok = safe_persist(None, "a1", "lc1", LifecycleStage.COMPLETED)
        assert ok is False

    def test_safe_persist_handles_exception(self):
        """safe_persist 处理 manager.persist_event 抛错"""
        mgr = MagicMock()
        mgr.persist_event.side_effect = RuntimeError("boom")
        ok = safe_persist(mgr, "a1", "lc1", LifecycleStage.COMPLETED)
        assert ok is False


# ============================================================
# 6. 查询接口
# ============================================================

class TestPersistenceQuery:
    """get_action / get_history / get_recent_actions"""

    def test_get_action_returns_latest(self, tmp_path):
        """get_action 返回最新状态"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        mgr.persist_event("a1", "lc1", LifecycleStage.CREATED)
        mgr.persist_event("a1", "lc1", LifecycleStage.APPROVED)
        mgr.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        latest = mgr.get_action("a1")
        assert latest["stage"] == "completed"
        assert latest["lifecycle_id"] == "lc1"

    def test_get_action_missing_returns_none(self, tmp_path):
        """不存在的 action_id 返回 None"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        assert mgr.get_action("nope") is None

    def test_get_history_returns_all_events(self, tmp_path):
        """get_history 返回全部历史"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        mgr.persist_event("a1", "lc1", LifecycleStage.CREATED)
        mgr.persist_event("a1", "lc1", LifecycleStage.APPROVED)
        mgr.persist_event("a1", "lc1", LifecycleStage.EXECUTING)
        mgr.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        hist = mgr.get_history("a1")
        assert len(hist) == 4
        stages = [r["stage"] for r in hist]
        assert stages == ["created", "approved", "executing", "completed"]

    def test_get_recent_actions(self, tmp_path):
        """get_recent_actions 返回最近 N 条"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        for i in range(20):
            mgr.persist_event(f"a{i}", f"lc{i}", LifecycleStage.COMPLETED)
        recent = mgr.get_recent_actions(limit=5)
        assert len(recent) == 5
        # 应该是最后 5 个
        aids = [r["action_id"] for r in recent]
        assert aids == ["a15", "a16", "a17", "a18", "a19"]

    def test_get_terminal_actions(self, tmp_path):
        """get_terminal_actions 只返回终态"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        mgr.persist_event("a1", "lc1", LifecycleStage.CREATED)
        mgr.persist_event("a2", "lc2", LifecycleStage.COMPLETED)
        mgr.persist_event("a3", "lc3", LifecycleStage.REJECTED)
        mgr.persist_event("a4", "lc4", LifecycleStage.FAILED)
        terminals = mgr.get_terminal_actions()
        assert "a1" not in terminals
        assert "a2" in terminals
        assert "a3" in terminals
        assert "a4" in terminals
        # 检查 (stage, ts) 格式
        assert terminals["a2"][0] == "completed"
        assert isinstance(terminals["a2"][1], float)

    def test_get_dispatched_ids(self, tmp_path):
        """get_dispatched_ids 只返回 completed"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        mgr.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        mgr.persist_event("a2", "lc2", LifecycleStage.COMPLETED)
        mgr.persist_event("a3", "lc3", LifecycleStage.FAILED)
        mgr.persist_event("a4", "lc4", LifecycleStage.REJECTED)
        ids = mgr.get_dispatched_ids()
        assert ids == {"a1", "a2"}


# ============================================================
# 7. load_state 恢复
# ============================================================

class TestPersistenceRecovery:
    """load_state 从 JSONL 恢复"""

    def test_load_empty_file(self, tmp_path):
        """空目录 → load_state 返回 {}"""
        log_path = str(tmp_path / "empty.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        result = mgr.load_state()
        assert result == {}

    def test_load_existing_file(self, tmp_path):
        """已存在的 JSONL 文件 → load_state 还原"""
        log_path = str(tmp_path / "test.jsonl")
        # 预写一些数据
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "schema_version": SCHEMA_VERSION,
                "ts": 100.0,
                "action_id": "a1", "lifecycle_id": "lc1",
                "stage": "completed", "decision": "dispatched",
            }) + "\n")
            f.write(json.dumps({
                "schema_version": SCHEMA_VERSION,
                "ts": 200.0,
                "action_id": "a2", "lifecycle_id": "lc2",
                "stage": "rejected", "decision": "gate_rejected",
            }) + "\n")

        mgr = ActionPersistenceManager(path=log_path)
        result = mgr.load_state()
        assert "a1" in result
        assert "a2" in result
        assert result["a1"] == ("completed", 100.0)
        assert result["a2"] == ("rejected", 200.0)
        # 内存索引也更新
        assert mgr.recover_count == 2
        assert mgr.get_action("a1")["stage"] == "completed"

    def test_load_corrupted_line_skipped(self, tmp_path):
        """损坏行被跳过,不抛"""
        log_path = str(tmp_path / "test.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "action_id": "a1", "lifecycle_id": "lc1",
                "stage": "completed", "ts": 100.0,
            }) + "\n")
            f.write("not valid json\n")  # 损坏
            f.write(json.dumps({
                "action_id": "a2", "lifecycle_id": "lc2",
                "stage": "rejected", "ts": 200.0,
            }) + "\n")

        mgr = ActionPersistenceManager(path=log_path)
        result = mgr.load_state()
        # 损坏行被跳过,有效行被恢复
        assert "a1" in result
        assert "a2" in result
        assert "a3" not in result

    def test_load_skips_lines_with_missing_fields(self, tmp_path):
        """缺少关键字段的行被跳过"""
        log_path = str(tmp_path / "test.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"action_id": "a1"}) + "\n")  # 缺 stage
            f.write(json.dumps({"stage": "completed"}) + "\n")  # 缺 action_id
            f.write(json.dumps({
                "action_id": "a2", "stage": "rejected", "ts": 200.0,
            }) + "\n")
        mgr = ActionPersistenceManager(path=log_path)
        result = mgr.load_state()
        assert "a2" in result
        assert "a1" not in result

    def test_latest_wins_on_overwrite(self, tmp_path):
        """同 action_id 多次出现时,最新 stage 覆盖旧的"""
        log_path = str(tmp_path / "test.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "action_id": "a1", "stage": "created", "ts": 100.0,
            }) + "\n")
            f.write(json.dumps({
                "action_id": "a1", "stage": "completed", "ts": 200.0,
            }) + "\n")
        mgr = ActionPersistenceManager(path=log_path)
        result = mgr.load_state()
        assert result["a1"] == ("completed", 200.0)


# ============================================================
# 8. 与 B.4 lifecycle 集成
# ============================================================

class TestB4LifecycleIntegration:
    """B.4 lifecycle 通过 persistence 钩子写盘"""

    def test_mark_terminal_persists(self, tmp_path):
        """mark_terminal 自动写盘"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        policy = ExecutionPolicy()
        lm = ActionLifecycleManager(policy, persistence=mgr)

        lm.mark_terminal("a1", LifecycleStage.COMPLETED)
        # 写盘 + 内存都更新
        assert mgr.write_count >= 1
        assert mgr.get_action("a1")["stage"] == "completed"

    def test_record_trail_persists(self, tmp_path):
        """record_trail 自动写盘"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        lm = ActionLifecycleManager(ExecutionPolicy(), persistence=mgr)

        rec = ActionAuditRecord_dummy("a1", "lc1", LifecycleStage.APPROVED, decision="gate_passed")
        lm.record_trail(rec)
        assert mgr.get_action("a1")["stage"] == "approved"

    def test_no_persistence_works(self):
        """无 persistence 时 B.4 仍正常工作"""
        lm = ActionLifecycleManager(ExecutionPolicy(), persistence=None)
        lm.mark_terminal("a1", LifecycleStage.COMPLETED)
        assert lm.is_duplicate("a1") is True

    def test_b4_bridge_passes_persistence(self, tmp_path):
        """B.4 bridge 接收 persistence 并启用"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        # 验证:bridge 持有 persistence 引用
        assert b.persistence is mgr
        # 验证:lifecycle manager 也持有
        assert b.lifecycle.persistence is mgr

    def test_b4_bridge_persistence_hooks_audit(self, tmp_path):
        """B.4 bridge governance 路径下,audit 产生的事件会写盘"""
        log_path = str(tmp_path / "test.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        # 至少 3 条 lifecycle 事件应被持久化(approved/executing/completed)
        assert mgr.write_count >= 3
        # 验证 history 中包含 completed(注:B.6 引入后,outcome_recorded 会作为最新 stage 出现,
        # 但 lifecycle 事件仍完整保留)
        hist = mgr.get_history("a1")
        stages = [r.get("stage") for r in hist]
        assert "completed" in stages
        assert mgr.get_action("a1") is not None


class ActionAuditRecord_dummy:
    """轻量级 ActionAuditRecord(避免 import 循环)"""
    def __init__(self, action_id, lifecycle_id, stage, decision="", ts=None, **kw):
        self.action_id = action_id
        self.lifecycle_id = lifecycle_id
        self.stage = stage
        self.decision = decision
        self.ts = float(ts if ts is not None else time.time())
        self.result = None
        self.error = None
        self.extra = {}

    def to_dict(self):
        return {
            "action_id": self.action_id,
            "lifecycle_id": self.lifecycle_id,
            "stage": self.stage,
            "decision": self.decision,
            "ts": self.ts,
        }


# ============================================================
# 9. 重复 action 检测(重启后)
# ============================================================

class TestDuplicatePrevention:
    """持久化后,重启检测重复"""

    def test_restore_repopulates_terminal_set(self, tmp_path):
        """重启后,持久化的 terminal actions 进入本地 ledger"""
        log_path = str(tmp_path / "test.jsonl")
        # 进程 1:写一些 completed actions
        mgr1 = ActionPersistenceManager(path=log_path)
        mgr1.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        mgr1.persist_event("a2", "lc2", LifecycleStage.REJECTED)

        # 进程 2:构造 B.4 lifecycle + persistence
        mgr2 = ActionPersistenceManager(path=log_path)
        lm = ActionLifecycleManager(ExecutionPolicy(), persistence=mgr2)
        n = lm.restore_from_persistence()
        # 恢复 2 个终态
        assert n == 2
        # ledger 应包含
        assert lm.is_duplicate("a1") is True
        assert lm.is_duplicate("a2") is True
        # stage 也正确
        assert lm.get_terminal_stage("a1") == "completed"
        assert lm.get_terminal_stage("a2") == "rejected"

    def test_b4_bridge_recovers_on_init(self, tmp_path):
        """B.4 bridge 构造时自动恢复"""
        log_path = str(tmp_path / "test.jsonl")
        # 进程 1:写
        mgr1 = ActionPersistenceManager(path=log_path)
        mgr1.persist_event("a1", "lc1", LifecycleStage.COMPLETED)

        # 进程 2:B.4 bridge 自动恢复
        mgr2 = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr2,
        )
        # ledger 应已包含 a1
        assert b.lifecycle.is_duplicate("a1") is True
        assert b.lifecycle.get_terminal_stage("a1") == "completed"

    def test_duplicate_dispatch_blocked_after_recovery(self, tmp_path):
        """恢复后,同 action_id 走 governance 时被屏蔽"""
        log_path = str(tmp_path / "test.jsonl")
        # 进程 1:写
        mgr1 = ActionPersistenceManager(path=log_path)
        mgr1.persist_event("a1", "lc1", LifecycleStage.COMPLETED)

        # 进程 2:B.4 bridge
        mgr2 = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr2,
        )
        b.flush_pending()
        # a1 已 terminal → 不应被 audit 为新 dispatched
        # ledger 仍是 completed(没被新覆盖成新状态)
        assert b.lifecycle.get_terminal_stage("a1") == "completed"


# ============================================================
# 10. B.4 兼容
# ============================================================

class TestB4BackwardCompatibility:
    """B.5 不破坏 B.4 现有行为"""

    def test_b4_without_persistence_unchanged(self, tmp_path):
        """B.4 不传 persistence 时,行为与之前一致"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        assert b.persistence is None
        # flush 仍正常
        out = b.flush_pending()
        assert "dispatched" in out

    def test_b4_audit_still_works(self, tmp_path):
        """B.5 引入后,B.4 audit 仍正常"""
        log_path = str(tmp_path / "audit.jsonl")
        audit = RuntimeAuditLogger(log_path=log_path)
        mgr_path = str(tmp_path / "persist.jsonl")
        mgr = ActionPersistenceManager(path=mgr_path)
        recent = [{"action_id": "a1", "stage": "dispatched"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": True}},
            audit_logger=audit,
            persistence=mgr,
        )
        b.flush_pending()
        # audit 文件应被写入
        assert os.path.exists(log_path)
        # persistence 文件应被写入
        assert os.path.exists(mgr_path)

    def test_b4_lifecycle_no_persistence_no_persist_file(self, tmp_path):
        """B.4 lifecycle 无 persistence 时不创建任何持久化文件"""
        log_path = str(tmp_path / "should_not_exist.jsonl")
        lm = ActionLifecycleManager(ExecutionPolicy(), persistence=None)
        lm.mark_terminal("a1", LifecycleStage.COMPLETED)
        # 不应创建任何文件
        assert not os.path.exists(log_path)


# ============================================================
# 11. 重启恢复模拟
# ============================================================

class TestRestartRecovery:
    """完整模拟:进程 1 写 → 进程 2 读"""

    def test_full_lifecycle_persists_and_restores(self, tmp_path):
        """完整生命周期事件持久化 + 恢复"""
        log_path = str(tmp_path / "test.jsonl")
        # 进程 1:完整生命周期
        mgr1 = ActionPersistenceManager(path=log_path)
        mgr1.persist_event("a1", "lc1", LifecycleStage.CREATED)
        mgr1.persist_event("a1", "lc1", LifecycleStage.APPROVED)
        mgr1.persist_event("a1", "lc1", LifecycleStage.EXECUTING)
        mgr1.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        mgr1.persist_event("a2", "lc2", LifecycleStage.CREATED)
        mgr1.persist_event("a2", "lc2", LifecycleStage.REJECTED)

        # 进程 2:启动恢复
        mgr2 = ActionPersistenceManager(path=log_path)
        # 自动 load_state(在 __init__ 中)
        # 验证内存中能查到
        assert mgr2.get_action("a1")["stage"] == "completed"
        assert mgr2.get_action("a2")["stage"] == "rejected"
        # 验证 history
        h1 = mgr2.get_history("a1")
        stages1 = [r["stage"] for r in h1]
        assert "created" in stages1
        assert "completed" in stages1

    def test_dispatched_id_recovery_works(self, tmp_path):
        """恢复后 dispatched_ids 用于 B.3 幂等"""
        log_path = str(tmp_path / "test.jsonl")
        # 进程 1:写 3 个 completed
        mgr1 = ActionPersistenceManager(path=log_path)
        mgr1.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        mgr1.persist_event("a2", "lc2", LifecycleStage.COMPLETED)
        mgr1.persist_event("a3", "lc3", LifecycleStage.COMPLETED)

        # 进程 2:启动,get_dispatched_ids 应返回这 3 个
        mgr2 = ActionPersistenceManager(path=log_path)
        dispatched = mgr2.get_dispatched_ids()
        assert dispatched == {"a1", "a2", "a3"}


# ============================================================
# 12. start_runtime.py 集成
# ============================================================

class TestStartRuntimeScript:
    """start_runtime.py init_persistence + init_governance 集成"""

    def test_init_persistence_default(self):
        """init_persistence 默认行为"""
        from scripts.start_runtime import init_persistence
        cfg = {"action_persistence": {"enabled": True}}
        mgr = init_persistence(cfg)
        assert mgr is not None
        h = mgr.health_check()
        assert h["enabled"] is True
        assert h["path"] == "data/action_lifecycle.jsonl"

    def test_init_persistence_disabled(self):
        """init_persistence enabled=False → None"""
        from scripts.start_runtime import init_persistence
        cfg = {"action_persistence": {"enabled": False}}
        mgr = init_persistence(cfg)
        assert mgr is None

    def test_init_persistence_custom_path(self, tmp_path):
        """init_persistence 自定义 path"""
        from scripts.start_runtime import init_persistence
        custom_path = str(tmp_path / "custom.jsonl")
        cfg = {
            "action_persistence": {
                "enabled": True,
                "path": custom_path,
            }
        }
        mgr = init_persistence(cfg)
        assert mgr is not None
        # 写入触发文件创建
        mgr.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        assert os.path.exists(custom_path)

    def test_init_governance_with_persistence(self, tmp_path):
        """init_governance 接收 persistence 并注入"""
        from scripts.start_runtime import init_governance
        from src.runtime.phase_b3_integration import create_b3_bridge

        # 构造最小 b3_bridge
        pe = MagicMock()
        pe.get_pending_actions = MagicMock(return_value=[])
        pe.execute_action = MagicMock(return_value=True)
        ad = MagicMock()
        ad.dispatch = MagicMock()
        b3 = create_b3_bridge(pe, ad, max_per_tick=3)

        # 构造 persistence
        mgr = ActionPersistenceManager(path=str(tmp_path / "test.jsonl"))

        cfg = {"governance": {"enabled": True, "audit_enabled": False}}
        # patch RuntimeAuditLogger 注入路径(src.runtime.audit_log.runtime_audit_logger)
        with patch("src.runtime.audit_log.runtime_audit_logger.RuntimeAuditLogger", MagicMock()):
            b4 = init_governance(cfg, b3_bridge=b3, force=True, persistence=mgr)
        assert b4 is not None
        assert b4.persistence is mgr


# ============================================================
# 13. 核心模块未修改保护
# ============================================================

class TestNoCoreModuleModification:
    """B.5 不修改任何核心模块"""

    @staticmethod
    def _extract_imports(src_text: str) -> List[str]:
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

    def test_action_persistence_no_core_imports(self):
        """action_persistence 不 import 任何核心模块"""
        path = Path(__file__).resolve().parent.parent / "src" / "runtime" / "action_persistence.py"
        text = path.read_text(encoding="utf-8")
        imports = self._extract_imports(text)
        forbidden = [
            "src.runtime.supervisor",
            "src.runtime.runtime_core",
            "src.runtime.action_dispatcher",
            "src.proactive",
        ]
        for f in forbidden:
            assert not any(f in imp for imp in imports), \
                f"action_persistence 不得 import {f}, 实际 imports: {imports}"

    def test_b4_integration_still_keeps_no_core_imports(self):
        """phase_b4_integration 仍不 import 核心模块(B.5 接入后)"""
        path = Path(__file__).resolve().parent.parent / "src" / "runtime" / "phase_b4_integration.py"
        text = path.read_text(encoding="utf-8")
        imports = self._extract_imports(text)
        forbidden = [
            "src.runtime.supervisor",
            "src.runtime.runtime_core",
            "src.runtime.action_dispatcher",
            "src.proactive",
        ]
        for f in forbidden:
            assert not any(f in imp for imp in imports), \
                f"phase_b4 不得 import {f}, 实际 imports: {imports}"

    def test_start_runtime_keeps_b3_b4_paths(self):
        """start_runtime.py 保留 B.1/B.2/B.3/B.4 路径,新增 B.5 不破坏"""
        path = Path(__file__).resolve().parent.parent / "scripts" / "start_runtime.py"
        text = path.read_text(encoding="utf-8")
        # B.1
        assert "apply_phase_b1_config" in text or "phase_b_integration" in text
        # B.2
        assert "apply_phase_b2_config" in text
        # B.3
        assert "create_b3_bridge" in text
        # B.4
        assert "create_b4_bridge" in text
        assert "init_governance" in text
        # B.5
        assert "init_persistence" in text
        assert "apply_phase_b5_config" in text


# ============================================================
# 14. 工厂 + 健康度
# ============================================================

class TestCreatePersistenceManager:
    """create_persistence_manager 工厂"""

    def test_create_from_default_config(self):
        """无 cfg → 用默认"""
        mgr = create_persistence_manager()
        h = mgr.health_check()
        assert h["path"] == DEFAULT_PERSISTENCE_PATH
        assert h["enabled"] is True

    def test_create_from_user_config(self, tmp_path):
        """从 cfg 构造"""
        cfg = {
            "action_persistence": {
                "enabled": True,
                "path": str(tmp_path / "x.jsonl"),
                "max_records": 1000,
            }
        }
        mgr = create_persistence_manager(cfg=cfg)
        h = mgr.health_check()
        assert h["path"] == str(tmp_path / "x.jsonl")
        assert h["max_records"] == 1000

    def test_create_auto_recover_false(self, tmp_path):
        """auto_recover=False → 不在构造时 load_state"""
        log_path = str(tmp_path / "test.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "action_id": "a1", "stage": "completed", "ts": 100.0,
            }) + "\n")
        mgr = create_persistence_manager(
            cfg={"action_persistence": {"path": log_path}},
            auto_recover=False,
        )
        # auto_recover=False → clear() 已执行
        assert mgr.get_action("a1") is None


class TestHealthCheck:
    """health_check / reset_stats"""

    def test_health_check_keys(self, tmp_path):
        """health_check 字段完整"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        h = mgr.health_check()
        for k in (
            "name", "schema_version", "version", "enabled", "degraded",
            "closed", "path", "file_exists", "file_size", "max_bytes",
            "max_records", "in_memory_actions", "write_count",
            "write_error_count", "rotation_count", "recover_count",
            "last_write_at", "last_error", "created_at",
        ):
            assert k in h, f"missing key: {k}"

    def test_reset_stats(self, tmp_path):
        """reset_stats 重置统计"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        mgr.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        assert mgr.write_count == 1
        mgr.reset_stats()
        assert mgr.write_count == 0
