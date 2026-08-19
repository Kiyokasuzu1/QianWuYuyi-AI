# -*- coding: utf-8 -*-
"""
tests/test_runtime_health_contract.py

Phase C.7.1 Runtime Health Contract Compatibility Fix —— 验证测试

背景:
  Phase C.7.0 Audit 发现 Runtime Health Contract 存在 schema 不一致问题:
    - MemoryRuntimeAdapter.health_check() → {"healthy": bool, ...}
    - Emotion / Personality / Relationship / Growth RuntimeAdapter
      → {"status": "healthy" | "degraded", ...}

目标:
  验证 RuntimeCycleOrchestrator 在不修改 Adapter 本身的前提下,通过内部
  _normalize_adapter_health() 归一化层:
    1) 兼容两种格式(healthy / status)
    2) 修复 healthy_adapters 聚合数量
    3) 修复 is_degraded 综合判断
    4) 修复 snapshot schema(新增 healthy 字段)
    5) 保持向后兼容(原字段全部保留)

约束:
  - 不修改任何 Adapter / 核心模块
  - 所有外部依赖 mock 化
  - 不启动真实 LLM / 业务
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

# ============================================================
# 0. 路径 + 导入
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.runtime.phase_c1_integration import RuntimeCycleOrchestrator


# ============================================================
# 1. Mock Adapter(对应 5 个真实 Adapter 的 health 格式)
# ============================================================


class _MockMemoryAdapter:
    """模拟 MemoryRuntimeAdapter:使用 {"healthy": bool, ...} 格式。"""

    name = "memory"
    schema_version = "1.0"

    def __init__(self, healthy: bool = True) -> None:
        self._healthy = bool(healthy)
        self._process_count = 0

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def is_attached(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": self._healthy,
            "name": "memory",
            "schema_version": "1.0",
            "attached": True,
            "process_count": 0,
        }

    def process_cycle(self, ctx: Any) -> Any:
        self._process_count += 1
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": "memory", "attached": True}


class _MockEmotionAdapter:
    """模拟 EmotionRuntimeAdapter:使用 {"status": ...} 格式。"""

    name = "emotion"
    schema_version = "1.0"

    def __init__(self, status: str = "healthy") -> None:
        self._status = str(status or "healthy")
        self._process_count = 0

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def is_attached(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        return {
            "adapter": "emotion",
            "status": self._status,
            "schema_version": "1.0",
            "attached": True,
            "process_count": 0,
        }

    def process_cycle(self, ctx: Any) -> Any:
        self._process_count += 1
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": "emotion", "attached": True}


class _MockPersonalityAdapter:
    """模拟 PersonalityRuntimeAdapter:使用 {"status": ...} 格式。"""

    name = "personality"
    schema_version = "1.0"

    def __init__(self, status: str = "healthy") -> None:
        self._status = str(status or "healthy")

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def is_attached(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        return {
            "adapter": "personality",
            "status": self._status,
            "schema_version": "1.0",
            "attached": True,
        }

    def process_cycle(self, ctx: Any) -> Any:
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": "personality", "attached": True}


class _MockRelationshipAdapter:
    """模拟 RelationshipRuntimeAdapter:使用 {"status": ...} 格式。"""

    name = "relationship"
    schema_version = "1.0"

    def __init__(self, status: str = "healthy") -> None:
        self._status = str(status or "healthy")

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def is_attached(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        return {
            "adapter": "relationship",
            "status": self._status,
            "schema_version": "1.0",
            "attached": True,
        }

    def process_cycle(self, ctx: Any) -> Any:
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": "relationship", "attached": True}


class _MockGrowthAdapter:
    """模拟 GrowthRuntimeAdapter:使用 {"status": ...} 格式。"""

    name = "growth"
    schema_version = "1.0"

    def __init__(self, status: str = "healthy") -> None:
        self._status = str(status or "healthy")

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def is_attached(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        return {
            "status": self._status,
            "adapter": "growth",
            "schema_version": "1.0",
            "attached": True,
        }

    def process_cycle(self, ctx: Any) -> Any:
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": "growth", "attached": True}


# ============================================================
# 2. Helper
# ============================================================


def _build_orchestrator_with_adapters(adapters: list) -> RuntimeCycleOrchestrator:
    """构造一个 orchestrator 并注册传入的 adapters(无 persistence)。"""
    orch = RuntimeCycleOrchestrator(cfg={"phase_c1": {"enabled": True}}, persistence=None)
    for adp in adapters:
        ok = orch.register_adapter(adp, name=adp.name)
        assert ok, f"register_adapter 失败: {adp.name}"
    return orch


# ============================================================
# 3. _normalize_adapter_health 单元测试
# ============================================================


class TestNormalizeAdapterHealth:
    """直接验证 _normalize_adapter_health 归一化逻辑。"""

    def test_memory_healthy_format(self) -> None:
        """Memory 风格:{"healthy": True} → 归一化为 healthy。"""
        orch = RuntimeCycleOrchestrator()
        out = orch._normalize_adapter_health(
            {"healthy": True, "name": "memory"}, "memory"
        )
        assert out["name"] == "memory"
        assert out["healthy"] is True
        assert out["degraded"] is False
        assert isinstance(out["raw"], dict)
        assert out["raw"]["healthy"] is True

    def test_memory_unhealthy_format(self) -> None:
        """Memory 风格:{"healthy": False} → 归一化为 degraded。"""
        orch = RuntimeCycleOrchestrator()
        out = orch._normalize_adapter_health(
            {"healthy": False, "name": "memory"}, "memory"
        )
        assert out["healthy"] is False
        assert out["degraded"] is True

    def test_emotion_status_healthy(self) -> None:
        """Emotion 风格:{"status": "healthy"} → 归一化为 healthy。"""
        orch = RuntimeCycleOrchestrator()
        out = orch._normalize_adapter_health(
            {"status": "healthy", "adapter": "emotion"}, "emotion"
        )
        assert out["name"] == "emotion"
        assert out["healthy"] is True
        assert out["degraded"] is False

    def test_emotion_status_degraded(self) -> None:
        """Emotion 风格:{"status": "degraded"} → 归一化为 degraded。"""
        orch = RuntimeCycleOrchestrator()
        out = orch._normalize_adapter_health(
            {"status": "degraded", "adapter": "emotion"}, "emotion"
        )
        assert out["healthy"] is False
        assert out["degraded"] is True

    def test_unknown_status_treated_as_unhealthy(self) -> None:
        """未知 / 错误状态 → 视为 unhealthy。"""
        orch = RuntimeCycleOrchestrator()
        for bad_status in ["error", "unhealthy", "", "UNKNOWN", None]:
            out = orch._normalize_adapter_health(
                {"status": bad_status, "adapter": "x"}, "x"
            )
            assert out["healthy"] is False, f"status={bad_status!r} 应视为 unhealthy"
            assert out["degraded"] is True

    def test_non_dict_health_returns_unhealthy(self) -> None:
        """非 dict 输入(异常 / 损坏)→ 返回 unhealthy 默认结构。"""
        orch = RuntimeCycleOrchestrator()
        for bad in [None, "healthy", 123, ["healthy"], True]:
            out = orch._normalize_adapter_health(bad, "x")
            assert out["healthy"] is False
            assert out["degraded"] is True
            assert out["name"] == "x"

    def test_healthy_key_takes_priority_over_status(self) -> None:
        """当同时存在 healthy 和 status 时,healthy 优先(Memory 风格优先)。"""
        orch = RuntimeCycleOrchestrator()
        out = orch._normalize_adapter_health(
            {"healthy": True, "status": "degraded"}, "x"
        )
        assert out["healthy"] is True
        assert out["degraded"] is False


# ============================================================
# 4. health_check 聚合测试
# ============================================================


class TestHealthCheckAggregation:
    """验证 health_check() 聚合逻辑对 5 类 Adapter 的兼容性。"""

    def test_memory_adapter_healthy(self) -> None:
        """Memory 风格 healthy → healthy_adapters=1, healthy=True。"""
        orch = _build_orchestrator_with_adapters([_MockMemoryAdapter(healthy=True)])
        h = orch.health_check()
        assert h["healthy_adapters"] == 1
        assert h["healthy"] is True
        # adapters 列表里归一化结构
        assert len(h["adapters"]) == 1
        a = h["adapters"][0]
        assert a["name"] == "memory"
        assert a["healthy"] is True
        assert a["degraded"] is False

    def test_emotion_adapter_status_healthy(self) -> None:
        """Emotion 风格 status=healthy → healthy_adapters=1, healthy=True。"""
        orch = _build_orchestrator_with_adapters([_MockEmotionAdapter(status="healthy")])
        h = orch.health_check()
        assert h["healthy_adapters"] == 1
        assert h["healthy"] is True
        a = h["adapters"][0]
        assert a["name"] == "emotion"
        assert a["healthy"] is True

    def test_growth_adapter_status_degraded(self) -> None:
        """Growth 风格 status=degraded → healthy_adapters=0, healthy=False。"""
        orch = _build_orchestrator_with_adapters([_MockGrowthAdapter(status="degraded")])
        h = orch.health_check()
        assert h["healthy_adapters"] == 0
        assert h["healthy"] is False
        a = h["adapters"][0]
        assert a["name"] == "growth"
        assert a["healthy"] is False
        assert a["degraded"] is True

    def test_all_five_adapters_healthy(self) -> None:
        """5 个 adapter 全部 healthy → healthy_adapters=5, healthy=True。"""
        adapters = [
            _MockMemoryAdapter(healthy=True),
            _MockEmotionAdapter(status="healthy"),
            _MockPersonalityAdapter(status="healthy"),
            _MockRelationshipAdapter(status="healthy"),
            _MockGrowthAdapter(status="healthy"),
        ]
        orch = _build_orchestrator_with_adapters(adapters)
        h = orch.health_check()
        assert h["adapter_count"] == 5
        assert h["healthy_adapters"] == 5
        assert h["healthy"] is True

    def test_mixed_adapters_count_correctly(self) -> None:
        """混合 healthy/degraded 场景:3 healthy + 2 degraded → healthy_adapters=3。"""
        adapters = [
            _MockMemoryAdapter(healthy=True),
            _MockEmotionAdapter(status="healthy"),
            _MockPersonalityAdapter(status="healthy"),
            _MockRelationshipAdapter(status="degraded"),
            _MockGrowthAdapter(status="degraded"),
        ]
        orch = _build_orchestrator_with_adapters(adapters)
        h = orch.health_check()
        assert h["adapter_count"] == 5
        assert h["healthy_adapters"] == 3
        # 至少 1 个 healthy → overall healthy 仍可能为 True
        # (只要 healthy_adapters > 0 且无连续失败)
        assert h["healthy"] is True


# ============================================================
# 5. is_degraded 综合判断
# ============================================================


class TestIsDegraded:
    """验证 is_degraded 综合考虑 persistence + adapter 健康状态。"""

    def test_any_adapter_degraded_makes_runtime_degraded(self) -> None:
        """任一 adapter degraded → is_degraded=True。"""
        adapters = [
            _MockMemoryAdapter(healthy=True),
            _MockEmotionAdapter(status="degraded"),  # 一个 degraded
            _MockPersonalityAdapter(status="healthy"),
            _MockRelationshipAdapter(status="healthy"),
            _MockGrowthAdapter(status="healthy"),
        ]
        orch = _build_orchestrator_with_adapters(adapters)
        assert orch.is_degraded is True
        h = orch.health_check()
        assert h["degraded"] is True

    def test_all_adapters_healthy_not_degraded(self) -> None:
        """全部 adapter healthy + 无 persistence → 不应因 persistence 误判 degraded。

        修复后:persistence=None 不再直接返回 True。
        所有 adapter healthy → is_degraded=False。
        """
        adapters = [
            _MockMemoryAdapter(healthy=True),
            _MockEmotionAdapter(status="healthy"),
            _MockPersonalityAdapter(status="healthy"),
            _MockRelationshipAdapter(status="healthy"),
            _MockGrowthAdapter(status="healthy"),
        ]
        orch = _build_orchestrator_with_adapters(adapters)
        assert orch.is_degraded is False
        h = orch.health_check()
        assert h["degraded"] is False
        assert h["healthy"] is True

    def test_no_adapters_orchestrator_state(self) -> None:
        """0 adapter + 无 persistence → 不应因缺 adapter 自动 degraded。
        (具体策略:adapter_count=0 时 is_degraded=False, healthy=False)
        """
        orch = RuntimeCycleOrchestrator(cfg={"phase_c1": {"enabled": True}}, persistence=None)
        # 0 adapter:既无 persistence degraded,也无 adapter degraded
        assert orch.is_degraded is False


# ============================================================
# 6. snapshot schema 验证
# ============================================================


class TestSnapshotSchema:
    """验证 snapshot 输出符合 C.7.1 规范:新增 healthy 字段,保留旧字段。"""

    def test_snapshot_contains_healthy_field(self) -> None:
        """snapshot 必须包含 healthy 字段。"""
        orch = _build_orchestrator_with_adapters([_MockMemoryAdapter(healthy=True)])
        snap = orch.snapshot()
        assert "healthy" in snap
        assert snap["healthy"] is True

    def test_snapshot_healthy_false_when_adapter_degraded(self) -> None:
        """adapter degraded → snapshot.healthy=False。"""
        orch = _build_orchestrator_with_adapters([_MockEmotionAdapter(status="degraded")])
        snap = orch.snapshot()
        assert "healthy" in snap
        assert snap["healthy"] is False

    def test_old_fields_preserved_in_snapshot(self) -> None:
        """向后兼容:snapshot 原字段全部保留。"""
        orch = _build_orchestrator_with_adapters([_MockMemoryAdapter(healthy=True)])
        snap = orch.snapshot()
        # 旧字段必须保留
        assert "name" in snap
        assert "version" in snap
        assert "schema_version" in snap
        assert "enabled" in snap
        assert "closed" in snap
        assert "cycle_count" in snap
        assert "completed_count" in snap
        assert "failed_count" in snap
        assert "consecutive_failures" in snap
        assert "adapter_count" in snap
        assert "adapters" in snap
        assert "history_stats" in snap
        assert "last_error" in snap
        assert "last_event_at" in snap
        assert "created_at" in snap
        assert "degraded" in snap
        assert "ts" in snap
        # 新增字段
        assert "healthy" in snap

    def test_health_check_and_snapshot_healthy_consistent(self) -> None:
        """health_check().healthy 与 snapshot().healthy 一致。

        注意:`healthy` 与 `degraded` 不互斥。
          - healthy=True 表示"至少 1 个 adapter 健康,且无连续失败"(正向)
          - degraded=True 表示"任一 adapter 不健康 OR persistence degraded"(负向)
        所以混合场景下两者可同时为 True(部分健康、部分降级)。
        """
        adapters = [
            _MockMemoryAdapter(healthy=True),
            _MockEmotionAdapter(status="degraded"),
            _MockPersonalityAdapter(status="healthy"),
            _MockRelationshipAdapter(status="healthy"),
            _MockGrowthAdapter(status="healthy"),
        ]
        orch = _build_orchestrator_with_adapters(adapters)
        h = orch.health_check()
        s = orch.snapshot()
        # 4 个 healthy adapter → overall healthy=True(正向条件满足)
        assert h["healthy"] is True
        assert s["healthy"] is True
        # 1 个 degraded adapter → overall degraded=True(负向条件触发)
        assert h["degraded"] is True
        assert s["degraded"] is True
        # healthy_adapters 数量准确
        assert h["healthy_adapters"] == 4
        # health_check 与 snapshot 的 healthy 字段一致
        assert h["healthy"] == s["healthy"]
        assert h["degraded"] == s["degraded"]


# ============================================================
# 7. Backward Compatibility(确保不破坏既有 schema 字段)
# ============================================================


class TestBackwardCompatibility:
    """回归测试:确保 C.7.1 修复不破坏既有 health_check 字段。"""

    def test_health_check_keeps_all_old_fields(self) -> None:
        """health_check 必须保留所有既有字段。"""
        orch = _build_orchestrator_with_adapters(
            [_MockMemoryAdapter(healthy=True), _MockEmotionAdapter(status="healthy")]
        )
        h = orch.health_check()
        # 旧字段
        for key in [
            "name", "version", "schema_version", "enabled", "closed",
            "cycle_count", "completed_count", "failed_count",
            "consecutive_failures", "adapter_count", "history_in_memory",
            "last_event_at", "last_error", "created_at", "degraded",
        ]:
            assert key in h, f"health_check 缺少旧字段: {key}"
        # 新增 / 修复字段
        assert "adapters" in h
        assert "healthy_adapters" in h
        assert "healthy" in h

    def test_adapters_list_uses_normalized_format(self) -> None:
        """adapters 列表的每项必须使用归一化结构(含 name/healthy/degraded/raw)。"""
        orch = _build_orchestrator_with_adapters(
            [_MockMemoryAdapter(healthy=True), _MockEmotionAdapter(status="healthy")]
        )
        h = orch.health_check()
        for a in h["adapters"]:
            assert "name" in a
            assert "healthy" in a
            assert "degraded" in a
            assert "raw" in a  # 保留原始 health 供排查

    def test_schema_version_unchanged(self) -> None:
        """schema_version 必须保持 CYCLE_EVENT_SCHEMA_VERSION,不升级。"""
        from src.runtime.cycle_event import CYCLE_EVENT_SCHEMA_VERSION
        orch = RuntimeCycleOrchestrator()
        h = orch.health_check()
        s = orch.snapshot()
        assert h["schema_version"] == CYCLE_EVENT_SCHEMA_VERSION
        assert s["schema_version"] == CYCLE_EVENT_SCHEMA_VERSION
