# -*- coding: utf-8 -*-
# src/runtime/self_model/growth_self_model_adapter.py
"""
Phase 4.2.2: GrowthSelfModelAdapter —— Growth 数据源 Adapter

职责:
- 桥接 Runtime → GrowthSelfModelAdapter → GrowthAdapter
- 从 GrowthAdapter 获取 GrowthProposal / growth_records
- 转换为 SelfModelFoundation 可直接消费的结构化 Dict
- 不直接调用 GrowthEngine 内部逻辑

数据流:
    Runtime → GrowthSelfModelAdapter.extract_inputs(ctx)
        → GrowthAdapter.evaluate(event) / submit(proposal)
        → SelfModelFoundation.build({"growth_records": [...]})

约束:
- 不修改 Growth 核心模块
- 不修改 GrowthAdapter 抽象接口
- 仅使用 GrowthAdapterSpec 暴露的接口(evaluate / submit)
- 任何异常被静默吞掉,返回 {}

注意:
- 当 GrowthAdapter 未注入或未 attach 时,本 Adapter 直接返回 {}
- 本 Adapter 缓存最近一次 growth_records,避免每次重复查询
"""
from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

from src.runtime.self_model.self_model_data import SelfModelEntry
from src.runtime.self_model.self_model_source_adapter import (
    SelfModelSourceAdapter,
    SOURCE_TYPE_GROWTH,
)


logger = logging.getLogger(__name__)


GROWTH_SELF_MODEL_ADAPTER_NAME = "growth_self_model_adapter"


class GrowthSelfModelAdapter(SelfModelSourceAdapter):
    """Growth 数据源 Adapter(Phase 4.2.2 / v1.0)。

    从 GrowthAdapter 提取结构化 SelfModel 输入:
    - growth_records:  List[Dict]  # 来自 GrowthProposal.proposed_changes
    - extra_entries:   List[SelfModelEntry]  # 每个 proposal 一个 entry
    - core_values:     List[Dict]  # 高置信度 proposal 触发的核心价值变更

    构造参数:
    - growth_adapter: 任意满足 GrowthAdapterSpec 接口的对象
                       (如 GrowthAdapterImpl 或外部测试桩)
                       若为 None,extract_inputs 返回 {}

    使用:
        adapter = GrowthSelfModelAdapter(growth_adapter=growth_adapter_impl)
        adapter.attach()
        inputs = adapter.safe_extract(ctx)
        # inputs = {"growth_records": [...], "extra_entries": [...], ...}
    """

    name: str = GROWTH_SELF_MODEL_ADAPTER_NAME
    schema_version: str = "1.0"

    def __init__(self, growth_adapter: Optional[Any] = None) -> None:
        super().__init__()
        self._growth_adapter: Optional[Any] = growth_adapter
        # 缓存:proposal_id -> dict
        self._seen_proposals: Dict[str, Dict[str, Any]] = {}

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE_GROWTH

    @property
    def growth_adapter(self) -> Optional[Any]:
        return self._growth_adapter

    def set_growth_adapter(self, growth_adapter: Optional[Any]) -> None:
        """运行时注入 GrowthAdapter(注入后下一次 extract_inputs 即生效)。"""
        self._growth_adapter = growth_adapter

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 GrowthAdapter(仅标记状态,无副作用)。"""
        self._mark_attached()
        self._cache_health({
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "source_type": self.source_type,
            "has_growth_adapter": self._growth_adapter is not None,
        })

    # --------------------------------------------------------
    # 核心:extract_inputs
    # --------------------------------------------------------
    def extract_inputs(self, ctx: Optional[Any] = None) -> Dict[str, Any]:
        """从 GrowthAdapter 提取 SelfModel 结构化输入。

        行为:
        1) 尝试调 GrowthAdapter 提供的 growth_records 接口
           (回退: evaluate / 缓存)
        2) 转换每条 GrowthProposal 为 growth_records
        3) 为每条 proposal 生成一条 SelfModelEntry(kind="growth")
        4) 收集高置信度的 proposed_changes 为 core_values

        Args:
            ctx: Runtime 共享上下文(用于从 ctx.growth_proposals 读取)

        Returns:
            {
                "growth_records": List[Dict],
                "extra_entries":  List[SelfModelEntry],
                "core_values":    List[Dict],  # 高置信度变化派生
            }
        """
        if not self.is_attached:
            return {}

        # 1) 尝试从 ctx 读取已经评估的 growth_proposals(Runtime 已填充)
        proposals: List[Any] = []
        if ctx is not None:
            ctx_proposals = getattr(ctx, "growth_proposals", None)
            if isinstance(ctx_proposals, list):
                proposals = list(ctx_proposals)

        # 2) 尝试从 GrowthAdapter 拉取(若 Adapter 提供)
        if not proposals and self._growth_adapter is not None:
            try:
                # 优先尝试 list_proposals / recent_proposals 风格接口
                list_method = getattr(
                    self._growth_adapter, "list_proposals", None
                )
                if list_method is not None and callable(list_method):
                    result = list_method()
                    if isinstance(result, list):
                        proposals = result
            except Exception:  # noqa: BLE001
                pass

        # 3) 转换 proposals 为 SelfModel 输入
        growth_records: List[Dict[str, Any]] = []
        extra_entries: List[SelfModelEntry] = []
        core_values: List[Dict[str, Any]] = []
        seen_ids: set = set()

        for proposal in proposals:
            try:
                p_dict = self._convert_proposal(proposal)
                if p_dict is None:
                    continue
                # 跳过重复
                pid = p_dict.get("id")
                if pid is not None:
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    self._seen_proposals[pid] = p_dict

                growth_records.append(p_dict)
                # 生成 SelfModelEntry
                extra_entries.append(SelfModelEntry(
                    kind="growth",
                    summary=str(p_dict.get("summary", "growth proposal")),
                    sources=list(p_dict.get("evidence_ids", []) or []),
                    confidence=float(p_dict.get("confidence", 0.5) or 0.5),
                    meta={
                        "proposal_id": p_dict.get("id"),
                        "source": "growth",
                    },
                ))
                # 高置信度 → core_values
                if float(p_dict.get("confidence", 0.0) or 0.0) >= 0.7:
                    for change in p_dict.get("proposed_changes", []) or []:
                        if not isinstance(change, dict):
                            continue
                        path = change.get("path", "")
                        if "value" in path or "core_value" in path:
                            core_values.append({
                                "key": str(path),
                                "label": str(change.get("after", path)),
                                "weight": 0.7,
                                "confidence": float(
                                    p_dict.get("confidence", 0.5) or 0.5
                                ),
                                "source": "growth_proposal",
                            })
            except Exception:  # noqa: BLE001
                continue

        return {
            "growth_records": growth_records,
            "extra_entries": extra_entries,
            "core_values": core_values,
        }

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------
    def _convert_proposal(self, proposal: Any) -> Optional[Dict[str, Any]]:
        """将 proposal 对象或 dict 转为标准化 Dict。"""
        # dict 形式
        if isinstance(proposal, dict):
            raw_changes = proposal.get("proposed_changes", []) or []
            norm_changes: List[Dict[str, Any]] = []
            for c in raw_changes:
                if isinstance(c, dict):
                    norm_changes.append(dict(c))
                elif is_dataclass(c):
                    try:
                        norm_changes.append(asdict(c))
                    except Exception:  # noqa: BLE001
                        continue
            return {
                "id": proposal.get("id") or proposal.get("proposal_id"),
                "source_event_id": proposal.get("source_event_id"),
                "proposed_changes": norm_changes,
                "confidence": float(proposal.get("confidence", 0.5) or 0.5),
                "evidence_ids": list(proposal.get("evidence_ids", []) or []),
                "status": proposal.get("status", "proposed"),
                "summary": proposal.get("summary") or (
                    f"proposal with "
                    f"{len(norm_changes)} changes"
                ),
                "timestamp": proposal.get("timestamp", ""),
            }
        # dataclass 形式(GrowthProposal)
        if hasattr(proposal, "proposed_changes"):
            try:
                raw_changes = list(getattr(proposal, "proposed_changes", []) or [])
                norm_changes2: List[Dict[str, Any]] = []
                for c in raw_changes:
                    if isinstance(c, dict):
                        norm_changes2.append(dict(c))
                    elif is_dataclass(c):
                        try:
                            norm_changes2.append(asdict(c))
                        except Exception:  # noqa: BLE001
                            continue
                    elif hasattr(c, "__dict__"):
                        try:
                            norm_changes2.append(dict(c.__dict__))
                        except Exception:  # noqa: BLE001
                            continue
                return {
                    "id": getattr(proposal, "id", None),
                    "source_event_id": getattr(proposal, "source_event_id", None),
                    "proposed_changes": norm_changes2,
                    "confidence": float(
                        getattr(proposal, "confidence", 0.5) or 0.5
                    ),
                    "evidence_ids": list(
                        getattr(proposal, "evidence_ids", []) or []
                    ),
                    "status": getattr(proposal, "status", "proposed"),
                    "summary": (
                        f"proposal with "
                        f"{len(norm_changes2)}"
                        f" changes"
                    ),
                    "timestamp": getattr(proposal, "timestamp", ""),
                }
            except Exception:  # noqa: BLE001
                return None
        return None

    def seen_proposal_count(self) -> int:
        """返回本 Adapter 累计见过的 proposal 数量(用于测试)。"""
        return len(self._seen_proposals)

    def clear_cache(self) -> None:
        """清空缓存(用于测试)。"""
        self._seen_proposals.clear()

    def _replace(self, **kwargs: Any) -> "GrowthSelfModelAdapter":
        """复制式替换:返回一个全新的 GrowthSelfModelAdapter 实例。

        用法(供测试 / 动态注册使用):
            new_adapter = old_adapter._replace()
        """
        new_growth = kwargs.pop("growth_adapter", None)
        new = GrowthSelfModelAdapter(
            growth_adapter=new_growth if new_growth is not None else self._growth_adapter
        )
        if kwargs:
            try:
                for k, v in kwargs.items():
                    setattr(new, k, v)
            except Exception:  # noqa: BLE001
                pass
        return new


__all__ = [
    "GrowthSelfModelAdapter",
    "GROWTH_SELF_MODEL_ADAPTER_NAME",
]
