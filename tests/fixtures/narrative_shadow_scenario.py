# -*- coding: utf-8 -*-
"""
tests/fixtures/narrative_shadow_scenario.py

v1.2 Phase 3.2 Fast SHADOW — 生产事件模拟器（仅测试使用, 不进入生产代码）。

约束:
- 不绕过 RuntimeCore 与生产装配路径: 场景一律经 RuntimeIntegrationHost 的
  tick → _assemble_narrative 生产流程驱动（绝不直接调用 assembler）;
- RuntimeCore 的 flag 传递由 test_self_narrative_shadow.py::Test 6 独立覆盖;
- WorldState 只提供「生产事件流」输入, 全部可溯源（带 id/时间戳）。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost


class WorldState:
    """生产事件流状态（模拟真实数据源随时间变化）。"""

    def __init__(self) -> None:
        self.memories: List[Dict[str, Any]] = []
        self.experiences: List[Dict[str, Any]] = []
        self.reflections: List[Dict[str, Any]] = []
        self.growth_records: List[Dict[str, Any]] = []
        self.audit_entries: List[Dict[str, Any]] = []
        self.growth_narratives: List[Dict[str, Any]] = []
        self.malicious_sourceless: List[Dict[str, Any]] = []

    def add_growth_event(
        self,
        dimension: str,
        before: float,
        after: float,
        reason: str,
        *,
        record_id: str,
        proposal_id: str,
        approval_id: str,
        timestamp: str,
    ) -> None:
        """模拟一次真实成长事件（成长记录 + 治理审计同源）。"""
        self.growth_records.append({
            "record_id": record_id,
            "timestamp": timestamp,
            "trigger_events": [f"evt_{record_id}"],
            "changes": {
                dimension: {
                    "before": before,
                    "after": after,
                    "delta": round(after - before, 4),
                    "reason": reason,
                }
            },
            "affected_dimensions": [dimension],
            "meaning": reason,
            "narrative": "",
            "confidence": 0.85,
            "validation_count": 1,
            "growth_level": "trait",
        })
        self.audit_entries.append({
            "id": f"sm_{record_id}",
            "component": "personality",
            "target": "personality",
            "before": {dimension: before},
            "after": {dimension: after},
            "proposal_id": proposal_id,
            "approval_id": approval_id,
            "actor": "runtime_drain",
            "timestamp": timestamp,
        })

    def add_malicious_sourceless(self, content: str) -> None:
        """模拟恶意数据: 内容含身份/经历断言, 但无任何来源标识。"""
        self.malicious_sourceless.append({"content": content})

    def as_input(self) -> Dict[str, Any]:
        return {
            "memories": copy.deepcopy(self.memories),
            "experiences": copy.deepcopy(self.experiences),
            "reflections": copy.deepcopy(self.reflections),
            "growth_records": copy.deepcopy(self.growth_records),
            "audit_entries": copy.deepcopy(self.audit_entries),
            "growth_narratives": copy.deepcopy(self.growth_narratives),
        }


def build_shadow_host(
    world: WorldState,
    shadow_path: str,
    *,
    name: str = "v12_fast_shadow",
) -> RuntimeIntegrationHost:
    """构造 SHADOW 模式生产宿主（assembly 开, 落盘 shadow 路径）。

    数据流完全走生产路径: host.tick → _assemble_narrative →
    SelfNarrativeAssembler → SelfNarrativeHistory.append_snapshot。
    """
    host = RuntimeIntegrationHost(
        name=name,
        auto_create_manager=False,
        auto_register_tasks=False,
        enable_persistence=False,
        narrative_assembly_enabled=True,
        narrative_history_path=shadow_path,
        narrative_data_provider=world.as_input,
    )
    host.start()
    return host


def read_shadow_snapshots(shadow_path: str) -> List[Any]:
    """只读读取 shadow 叙事历史（latest-first）。"""
    from src.personality.self_narrative_history import SelfNarrativeHistory

    return SelfNarrativeHistory.load(shadow_path).get_recent(100)
