# -*- coding: utf-8 -*-
"""
tests/test_runtime_health_monitor.py

Phase C.4.1 — Runtime Health Monitor 测试

目标:
验证 scripts/runtime_health_monitor.py 的功能:
- 对话数量统计
- Memory 写入分析
- Proposal 状态统计
- SelfModel 变化监控
- CoreIdentity 变化尝试检测
- LLM 错误统计
- 报警逻辑

约束:
- 使用 tmp_path 隔离
- 不修改任何核心模块
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def fake_data_dir(tmp_path):
    """构造模拟的 data 目录。"""
    data_dir = tmp_path / "data"
    growth_dir = data_dir / "growth" / "proposals"
    llm_failures_dir = data_dir / "llm_failures"
    selfmodel_dir = data_dir / "self_model"

    growth_dir.mkdir(parents=True, exist_ok=True)
    llm_failures_dir.mkdir(parents=True, exist_ok=True)
    selfmodel_dir.mkdir(parents=True, exist_ok=True)

    return {
        "data_dir": data_dir,
        "memory_path": data_dir / "memory.json",
        "proposals_path": growth_dir / "proposals.json",
        "llm_failures_path": llm_failures_dir / "failures.jsonl",
        "selfmodel_dir": selfmodel_dir,
    }


@pytest.fixture
def populated_data_dir(fake_data_dir):
    """填充示例数据。"""
    # memory.json
    memories = [
        {
            "user_id": "u_001",
            "role": "user",
            "content": f"用户消息 {i}",
            "metadata": {
                "memory_type": "user_shared" if i % 3 == 0 else "user_preference",
                "user_id": "u_001",
                "timestamp": f"2026-08-{1 + (i % 28):02d}T00:00:00",
            },
        }
        for i in range(10)
    ]
    fake_data_dir["memory_path"].write_text(
        json.dumps(memories, ensure_ascii=False), encoding="utf-8"
    )

    # proposals.json
    proposals = {
        "version": "1.0",
        "proposals": [
            {
                "proposal_id": f"prop_{i:03d}",
                "timestamp": "2026-08-01T00:00:00",
                "proposal_type": "relationship",
                "status": "pending" if i < 5 else "rejected",
                "confidence": 0.85,
                "evidence": [f"ev_{i}"],
                "before_state": {"trust": 0.5},
                "after_state": {"trust": 0.6},
                "affected_dimensions": {"trust": 0.1},
            }
            for i in range(7)
        ],
    }
    fake_data_dir["proposals_path"].write_text(
        json.dumps(proposals, ensure_ascii=False), encoding="utf-8"
    )

    # llm_failures.jsonl
    failures = [
        {"error_type": "timeout", "ts": "2026-08-01T00:00:00"},
        {"error_type": "connection_error", "ts": "2026-08-01T00:01:00"},
        {"error_type": "rate_limit", "ts": "2026-08-01T00:02:00"},
    ]
    with open(fake_data_dir["llm_failures_path"], "w", encoding="utf-8") as f:
        for fail in failures:
            f.write(json.dumps(fail, ensure_ascii=False) + "\n")

    return fake_data_dir


# ============================================================
# 1. 对话数量统计
# ============================================================

class TestDialogCount:
    def test_analyze_dialog_count(self, populated_data_dir):
        from scripts.runtime_health_monitor import analyze_dialog_count

        result = analyze_dialog_count(populated_data_dir["memory_path"])
        assert result["available"] is True
        assert result["total_user_turns"] == 10
        assert result["estimated_dialogs"] == 10

    def test_analyze_dialog_count_missing(self, fake_data_dir):
        from scripts.runtime_health_monitor import analyze_dialog_count

        result = analyze_dialog_count(fake_data_dir["memory_path"])
        assert result["available"] is False
        assert "error" in result


# ============================================================
# 2. Memory 写入分析
# ============================================================

class TestMemoryWrites:
    def test_analyze_memory_writes(self, populated_data_dir):
        from scripts.runtime_health_monitor import analyze_memory_writes

        result = analyze_memory_writes(populated_data_dir["memory_path"])
        assert result["available"] is True
        assert result["total"] == 10
        # 1/3 是 user_shared, 2/3 是 user_preference
        assert result["by_type"].get("user_shared", 0) >= 3
        assert result["by_type"].get("user_preference", 0) >= 6
        # 多天活跃
        assert result["active_days"] >= 1


# ============================================================
# 3. Proposal 统计
# ============================================================

class TestProposalAnalysis:
    def test_analyze_proposals(self, populated_data_dir):
        from scripts.runtime_health_monitor import analyze_proposals

        result = analyze_proposals(populated_data_dir["proposals_path"])
        assert result["available"] is True
        assert result["total"] == 7
        assert result["pending_count"] == 5
        assert result["rejected_count"] == 2

    def test_analyze_proposals_missing(self, fake_data_dir):
        from scripts.runtime_health_monitor import analyze_proposals

        result = analyze_proposals(fake_data_dir["proposals_path"])
        # 文件不存在时,返回 available=False
        assert result["available"] is False
        assert result["total"] == 0


# ============================================================
# 4. SelfModel 分析
# ============================================================

class TestSelfModelAnalysis:
    def test_analyze_selfmodel_empty(self, fake_data_dir):
        from scripts.runtime_health_monitor import analyze_selfmodel

        result = analyze_selfmodel(fake_data_dir["selfmodel_dir"])
        assert result["available"] is True
        assert result["data_status"] == "empty"
        assert result["beliefs_count"] == 0
        assert result["history_count"] == 0

    def test_analyze_selfmodel_not_initialized(self, fake_data_dir):
        from scripts.runtime_health_monitor import analyze_selfmodel

        # 删除 selfmodel 目录
        import shutil
        shutil.rmtree(fake_data_dir["selfmodel_dir"])

        result = analyze_selfmodel(fake_data_dir["selfmodel_dir"])
        assert result["available"] is False
        assert result["data_status"] == "not_initialized"

    def test_analyze_selfmodel_with_beliefs(self, fake_data_dir):
        from scripts.runtime_health_monitor import analyze_selfmodel

        # 写入 beliefs
        with open(fake_data_dir["selfmodel_dir"] / "beliefs.jsonl", "w", encoding="utf-8") as f:
            for i in range(3):
                f.write(json.dumps({"belief_id": f"b_{i}", "trait": "温柔", "value": 0.8}) + "\n")

        result = analyze_selfmodel(fake_data_dir["selfmodel_dir"])
        assert result["available"] is True
        assert result["data_status"] == "initialized"
        assert result["beliefs_count"] == 3


# ============================================================
# 5. CoreIdentity 变化尝试检测
# ============================================================

class TestCoreIdentityAttempts:
    def test_no_attempts_in_normal_proposals(self, populated_data_dir):
        from scripts.runtime_health_monitor import analyze_core_identity_attempts

        result = analyze_core_identity_attempts(populated_data_dir["proposals_path"])
        assert result["total_attempts"] == 0

    def test_dangerous_proposal_detected(self, tmp_path):
        from scripts.runtime_health_monitor import analyze_core_identity_attempts

        proposals = {
            "version": "1.0",
            "proposals": [
                {
                    "proposal_id": "prop_danger_1",
                    "status": "pending",
                    "confidence": 0.9,
                    "reason": "建议羽依失去温柔,变得冷漠一点",
                },
                {
                    "proposal_id": "prop_danger_2",
                    "status": "pending",
                    "confidence": 0.85,
                    "reason": "完全改变人格",
                },
                {
                    "proposal_id": "prop_safe",
                    "status": "pending",
                    "confidence": 0.7,
                    "reason": "用户喜欢蓝色",
                },
            ],
        }
        p = tmp_path / "proposals.json"
        p.write_text(json.dumps(proposals, ensure_ascii=False), encoding="utf-8")

        result = analyze_core_identity_attempts(p)
        assert result["total_attempts"] == 2
        # 2 个 attempt 的 keyword 应该是 forbidden 之一
        keywords = [a["matched_keyword"] for a in result["attempts"]]
        assert "失去温柔" in keywords or "完全改变人格" in keywords


# ============================================================
# 6. LLM 错误分析
# ============================================================

class TestLLMErrors:
    def test_analyze_llm_errors(self, populated_data_dir):
        from scripts.runtime_health_monitor import analyze_llm_errors

        result = analyze_llm_errors(populated_data_dir["llm_failures_path"])
        assert result["available"] is True
        assert result["total_errors"] == 3
        assert result["by_type"].get("timeout", 0) == 1
        assert result["by_type"].get("connection_error", 0) == 1
        assert result["by_type"].get("rate_limit", 0) == 1

    def test_analyze_llm_errors_missing(self, fake_data_dir):
        from scripts.runtime_health_monitor import analyze_llm_errors

        result = analyze_llm_errors(fake_data_dir["llm_failures_path"])
        assert result["available"] is False
        assert result["total_errors"] == 0


# ============================================================
# 7. 报警逻辑
# ============================================================

class TestAlerts:
    def test_no_alerts_for_healthy_data(self, populated_data_dir):
        from scripts.runtime_health_monitor import (
            analyze_dialog_count,
            analyze_memory_writes,
            analyze_proposals,
            analyze_core_identity_attempts,
            analyze_llm_errors,
            compute_alerts,
        )

        dialog = analyze_dialog_count(populated_data_dir["memory_path"])
        memory = analyze_memory_writes(populated_data_dir["memory_path"])
        proposals = analyze_proposals(populated_data_dir["proposals_path"])
        identity = analyze_core_identity_attempts(populated_data_dir["proposals_path"])
        llm = analyze_llm_errors(populated_data_dir["llm_failures_path"])

        alerts = compute_alerts(dialog, memory, proposals, identity, llm)
        # 7 个 proposal 中 5 个 pending < 100, 不告警
        assert all(a["level"] != "CRITICAL" for a in alerts)

    def test_critical_alert_for_identity_attempt(self, populated_data_dir):
        from scripts.runtime_health_monitor import (
            analyze_dialog_count,
            analyze_memory_writes,
            analyze_proposals,
            analyze_core_identity_attempts,
            analyze_llm_errors,
            compute_alerts,
        )

        # 注入危险 proposal
        proposals_data = json.loads(populated_data_dir["proposals_path"].read_text(encoding="utf-8"))
        proposals_data["proposals"].append({
            "proposal_id": "prop_danger",
            "status": "pending",
            "confidence": 0.9,
            "reason": "建议羽依失去温柔",
        })
        populated_data_dir["proposals_path"].write_text(
            json.dumps(proposals_data, ensure_ascii=False), encoding="utf-8"
        )

        dialog = analyze_dialog_count(populated_data_dir["memory_path"])
        memory = analyze_memory_writes(populated_data_dir["memory_path"])
        proposals = analyze_proposals(populated_data_dir["proposals_path"])
        identity = analyze_core_identity_attempts(populated_data_dir["proposals_path"])
        llm = analyze_llm_errors(populated_data_dir["llm_failures_path"])

        alerts = compute_alerts(dialog, memory, proposals, identity, llm)
        # 至少有一个 CRITICAL
        assert any(a["level"] == "CRITICAL" for a in alerts)

    def test_warning_for_high_pending(self, tmp_path):
        from scripts.runtime_health_monitor import (
            analyze_dialog_count,
            analyze_memory_writes,
            analyze_proposals,
            analyze_core_identity_attempts,
            analyze_llm_errors,
            compute_alerts,
        )

        # 创建 150 个 pending proposals
        proposals = {
            "version": "1.0",
            "proposals": [
                {
                    "proposal_id": f"p_{i}",
                    "status": "pending",
                    "confidence": 0.8,
                    "evidence": [],
                    "before_state": {"x": 0.5},
                    "after_state": {"x": 0.6},
                }
                for i in range(150)
            ],
        }
        p = tmp_path / "proposals.json"
        p.write_text(json.dumps(proposals, ensure_ascii=False), encoding="utf-8")

        proposals_r = analyze_proposals(p)
        identity = analyze_core_identity_attempts(p)
        dialog = {"available": True, "estimated_dialogs": 100}
        memory = {"available": True, "total": 100, "by_type": {}, "by_day": {}, "by_hour": {}, "by_user": {}, "active_days": 1, "active_hours": 1}
        llm = {"available": False, "total_errors": 0, "by_type": {}}

        alerts = compute_alerts(dialog, memory, proposals_r, identity, llm)
        # 150 > 100 触发 WARNING
        assert any(a["metric"] == "proposals_pending" for a in alerts)


# ============================================================
# 8. 报告渲染
# ============================================================

class TestReportRender:
    def test_render_report_empty(self, fake_data_dir):
        from scripts.runtime_health_monitor import render_report

        dialog = {"available": False, "error": "no data"}
        memory = {"available": False, "error": "no data", "total": 0, "by_type": {}}
        proposals = {"available": False, "total": 0, "by_status": {}, "by_type": {}, "rejected_count": 0, "pending_count": 0, "applied_count": 0}
        selfmodel = {"available": False, "data_status": "not_initialized"}
        identity = {"total_attempts": 0, "attempts": [], "forbidden_keywords": []}
        llm_errors = {"available": False, "total_errors": 0, "by_type": {}}
        alerts = []

        text = render_report(dialog, memory, proposals, selfmodel, identity, llm_errors, alerts)
        assert "# Runtime Health Report" in text
        assert "0. 告警" in text
        assert "无告警" in text

    def test_render_report_with_alerts(self, populated_data_dir):
        from scripts.runtime_health_monitor import (
            analyze_dialog_count,
            analyze_memory_writes,
            analyze_proposals,
            analyze_selfmodel,
            analyze_core_identity_attempts,
            analyze_llm_errors,
            compute_alerts,
            render_report,
        )

        dialog = analyze_dialog_count(populated_data_dir["memory_path"])
        memory = analyze_memory_writes(populated_data_dir["memory_path"])
        proposals = analyze_proposals(populated_data_dir["proposals_path"])
        selfmodel = analyze_selfmodel(populated_data_dir["selfmodel_dir"])
        identity = analyze_core_identity_attempts(populated_data_dir["proposals_path"])
        llm_errors = analyze_llm_errors(populated_data_dir["llm_failures_path"])
        alerts = compute_alerts(dialog, memory, proposals, identity, llm_errors)

        text = render_report(dialog, memory, proposals, selfmodel, identity, llm_errors, alerts)
        assert "Runtime Health Report" in text
        assert "1. 对话数量" in text
        assert "2. Memory 写入与类型" in text
        assert "3. Growth Proposal 状态" in text
        assert "4. SelfModel 变化" in text
        assert "5. CoreIdentity 变化尝试" in text
        assert "6. LLM 错误统计" in text
        assert "7. 总结" in text
