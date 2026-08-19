# -*- coding: utf-8 -*-
"""
src/runtime/self_model/self_model_registry.py

Phase 4.2.1: Self Model Registry —— Foundation 注册表
Phase 4.2.2: 扩展支持 SourceAdapter 输入源

职责:
- 管理多个 SelfModelBuilder / SelfModelFoundation 实例
- 提供批量 build / health_check / aggregate
- Runtime 只依赖本注册表,不直接接触具体 Builder / Foundation
- 接受多个 SelfModelSourceAdapter,将其 extract 合并后传入 Foundation

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import Runtime / Memory / Emotion / Personality 业务模块
- 异常隔离:单个 Builder / SourceAdapter 失败不中断 Registry
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.runtime.self_model.self_model_data import SelfModelSnapshot
from src.runtime.self_model.self_model_foundation import (
    SelfModelFoundation,
    SelfModelBuilderBase,
    SELF_MODEL_FOUNDATION_SCHEMA_VERSION,
)
from src.runtime.self_model.self_model_source_adapter import (
    SelfModelSourceAdapter,
)


SELF_MODEL_REGISTRY_SCHEMA_VERSION = "1.0"


class SelfModelRegistry:
    """SelfModel Builder/Foundation 注册表(Phase 4.2.1 v1.0,Phase 4.2.2 扩展输入源)。

    职责:
    - 接收多个 SelfModelBuilderBase / SelfModelFoundation
    - 提供按 name 索引
    - 批量 build / health_check
    - 批量 aggregate(所有 Foundation 都 build 一次, 聚合 snapshot)
    - (Phase 4.2.2) 接收多个 SelfModelSourceAdapter,合并其 extract 作为 Foundation inputs

    依赖:
    - 仅依赖 SelfModelBuilderBase / SelfModelFoundation
    - 不接触具体 Personality / Memory / Vision 业务模块
    """

    def __init__(self) -> None:
        self._foundations: Dict[str, SelfModelFoundation] = {}
        self._builders: Dict[str, SelfModelBuilderBase] = {}
        # Phase 4.2.2: Source Adapter 输入源
        self._source_adapters: Dict[str, SelfModelSourceAdapter] = {}
        # Phase 4.2.2: 缓存最近一次 merge 结果
        self._last_merged_inputs: Optional[Dict[str, Any]] = None
        self._last_merge_errors: List[Dict[str, str]] = []

    # --------------------------------------------------------
    # Foundation 注册
    # --------------------------------------------------------
    def register_foundation(self, foundation: SelfModelFoundation) -> None:
        if not isinstance(foundation, SelfModelFoundation):
            raise TypeError(
                f"foundation must be SelfModelFoundation, got {type(foundation).__name__}"
            )
        if foundation.identity_id in self._foundations:
            raise ValueError(
                f"SelfModelFoundation {foundation.identity_id!r} already registered"
            )
        self._foundations[foundation.identity_id] = foundation

    def unregister_foundation(self, identity_id: str) -> Optional[SelfModelFoundation]:
        return self._foundations.pop(identity_id, None)

    def get_foundation(self, identity_id: str) -> Optional[SelfModelFoundation]:
        return self._foundations.get(identity_id)

    def all_foundations(self) -> List[SelfModelFoundation]:
        return list(self._foundations.values())

    # --------------------------------------------------------
    # Builder 注册(可选)
    # --------------------------------------------------------
    def register_builder(self, builder: SelfModelBuilderBase) -> None:
        if not isinstance(builder, SelfModelBuilderBase):
            raise TypeError(
                f"builder must be SelfModelBuilderBase, got {type(builder).__name__}"
            )
        if builder.name in self._builders:
            raise ValueError(
                f"SelfModelBuilder {builder.name!r} already registered"
            )
        self._builders[builder.name] = builder

    def unregister_builder(self, name: str) -> Optional[SelfModelBuilderBase]:
        return self._builders.pop(name, None)

    def get_builder(self, name: str) -> Optional[SelfModelBuilderBase]:
        return self._builders.get(name)

    def all_builders(self) -> List[SelfModelBuilderBase]:
        return list(self._builders.values())

    # --------------------------------------------------------
    # 便利
    # --------------------------------------------------------
    def __len__(self) -> int:
        return len(self._foundations) + len(self._builders)

    def __contains__(self, key: str) -> bool:
        return key in self._foundations or key in self._builders

    @property
    def foundation_count(self) -> int:
        return len(self._foundations)

    @property
    def builder_count(self) -> int:
        return len(self._builders)

    # --------------------------------------------------------
    # 核心:批量 build
    # --------------------------------------------------------
    def build_all(
        self,
        inputs_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
        ctx: Optional[Any] = None,
    ) -> List[SelfModelSnapshot]:
        """每个 Foundation 各 build 一次, 聚合所有 snapshot。

        inputs_by_id:
            { identity_id: { trait_states: ..., growth_records: ..., ... } }
            未指定的 Foundation 用 {} (即默认 identity)。

        ctx:
            (Phase 4.2.2) 运行时上下文,传递给 SourceAdapter.extract_inputs(ctx)

        行为:
        1) 如果注册了 SourceAdapter,先把 ctx 喂给它们,合并结果
        2) 然后每个 Foundation 用 (其自身 inputs | merged source inputs) build

        失败隔离:Foundation.build() 返回 None 时, 不加入结果。
        """
        inputs_by_id = inputs_by_id or {}
        # Phase 4.2.2: 合并 SourceAdapter 的输入
        merged_source_inputs: Dict[str, Any] = {}
        if self._source_adapters:
            merged_source_inputs = self.merge_source_inputs(ctx)
        results: List[SelfModelSnapshot] = []
        for fid, foundation in self._foundations.items():
            try:
                foundation_inputs = inputs_by_id.get(fid, {}) or {}
                # 合并 source inputs (foundation 自身 inputs 优先)
                combined: Dict[str, Any] = dict(merged_source_inputs)
                for k, v in foundation_inputs.items():
                    if v is not None:
                        combined[k] = v
                snap = foundation.build(combined)
                if snap is not None:
                    results.append(snap)
            except Exception:  # noqa: BLE001
                continue
        return results

    def build_primary(
        self,
        inputs: Optional[Dict[str, Any]] = None,
        ctx: Optional[Any] = None,
    ) -> Optional[SelfModelSnapshot]:
        """便捷:对第一个 Foundation 调 build, 用于 Runtime 主路径。

        行为(Phase 4.2.2):
        1) 如果注册了 SourceAdapter,合并其 extract 结果
        2) 用 (merged source inputs | caller inputs) 调 foundation.build
        3) 失败时返回 None(由 Runtime 静默 no-op)。
        """
        if not self._foundations:
            return None
        first_id = next(iter(self._foundations.keys()))
        foundation = self._foundations[first_id]
        try:
            combined: Dict[str, Any] = {}
            # Phase 4.2.2: 先放 source inputs
            if self._source_adapters:
                merged = self.merge_source_inputs(ctx)
                combined.update(merged)
            # caller inputs 优先级更高(覆盖 source 默认)
            if inputs:
                for k, v in inputs.items():
                    if v is not None:
                        combined[k] = v
            return foundation.build(combined)
        except Exception:  # noqa: BLE001
            return None

    # --------------------------------------------------------
    # 健康度
    # --------------------------------------------------------
    def health_check_all(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "foundations": {},
            "builders": {},
            "schema_version": SELF_MODEL_REGISTRY_SCHEMA_VERSION,
        }
        for fid, foundation in self._foundations.items():
            try:
                result["foundations"][fid] = foundation.health_check()
            except Exception as exc:  # noqa: BLE001
                result["foundations"][fid] = {
                    "healthy": False,
                    "error": str(exc),
                }
        for name, builder in self._builders.items():
            try:
                result["builders"][name] = builder.health_check()
            except Exception as exc:  # noqa: BLE001
                result["builders"][name] = {
                    "healthy": False,
                    "error": str(exc),
                }
        return result

    # --------------------------------------------------------
    # History 摘要(Runtime SELF_MODEL_BUILD 阶段使用)
    # --------------------------------------------------------
    def history_summary(self) -> List[Dict[str, Any]]:
        """聚合所有 Foundation 的 history 摘要(扁平化)。

        Runtime 通过此方法读取 SELF_MODEL_BUILD 阶段产生的 history,
        任何 Foundation 异常被静默吞掉。
        """
        out: List[Dict[str, Any]] = []
        for fid, foundation in self._foundations.items():
            try:
                for entry in foundation.history_list() or []:
                    item = dict(entry)
                    item["identity_id"] = fid
                    out.append(item)
            except Exception:  # noqa: BLE001
                continue
        return out

    # ============================================================
    # Phase 4.2.2: Source Adapter 输入源机制
    # ============================================================

    # --------------------------------------------------------
    # Source Adapter 注册
    # --------------------------------------------------------
    def register_source(self, source: SelfModelSourceAdapter) -> None:
        """注册一个 SourceAdapter。

        - name 唯一:相同 name 重复注册会抛 ValueError
        - source_type 唯一:同 source_type 已有 Adapter 时,后注册的覆盖前者
        """
        if not isinstance(source, SelfModelSourceAdapter):
            raise TypeError(
                f"source must be SelfModelSourceAdapter, "
                f"got {type(source).__name__}"
            )
        if source.name in self._source_adapters:
            raise ValueError(
                f"SelfModelSourceAdapter {source.name!r} already registered"
            )
        # source_type 唯一性:若已存在同类型,移除旧的
        existing_same_type = [
            k for k, v in self._source_adapters.items()
            if v.source_type == source.source_type
        ]
        for k in existing_same_type:
            self._source_adapters.pop(k, None)
        self._source_adapters[source.name] = source

    def unregister_source(self, name: str) -> Optional[SelfModelSourceAdapter]:
        """反注册一个 SourceAdapter。"""
        return self._source_adapters.pop(name, None)

    def get_source(self, name: str) -> Optional[SelfModelSourceAdapter]:
        """按 name 获取 SourceAdapter。"""
        return self._source_adapters.get(name)

    def get_source_by_type(
        self, source_type: str,
    ) -> Optional[SelfModelSourceAdapter]:
        """按 source_type 获取 SourceAdapter。"""
        for src in self._source_adapters.values():
            if src.source_type == source_type:
                return src
        return None

    def all_sources(self) -> List[SelfModelSourceAdapter]:
        return list(self._source_adapters.values())

    @property
    def source_count(self) -> int:
        return len(self._source_adapters)

    # --------------------------------------------------------
    # Source Adapter 合并
    # --------------------------------------------------------
    def merge_source_inputs(
        self, ctx: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """遍历所有 SourceAdapter,合并它们的 extract_inputs 结果。

        合并规则:
        - trait_states:   Dict 合并(后者覆盖前者同名 trait)
        - core_values:    List 拼接(后注册的优先级高)
        - preferences:    List 拼接
        - growth_records: List 拼接
        - extra_entries:  List 拼接
        - current_state:  Dict 合并(emotion/personality 等)
        - identity_overrides: Dict 合并(后者覆盖)
        - relationship_state: Dict 合并

        失败隔离:任一 Adapter 出错,记入 _last_merge_errors,继续下一个

        Returns:
            合并后的 Dict(空 Dict 表示无 source)
        """
        merged: Dict[str, Any] = {
            "trait_states": {},
            "core_values": [],
            "preferences": [],
            "growth_records": [],
            "extra_entries": [],
            "current_state": {},
            "identity_overrides": {},
            "relationship_state": {},
        }
        errors: List[Dict[str, str]] = []
        for name, source in self._source_adapters.items():
            try:
                extracted = source.safe_extract(ctx)
                if not isinstance(extracted, dict):
                    errors.append({
                        "source": name,
                        "error": "extract returned non-dict",
                    })
                    continue
                # Phase 4.2.2: 追踪 source 内部错误(由基类 safe_extract 捕获)
                # safe_extract 捕获异常后会设置 _last_error,此处统一报告
                last_error = getattr(source, "last_error", None)
                if last_error:
                    errors.append({
                        "source": name,
                        "error": str(last_error),
                    })
                    # source 失败 → 不混入 partial 数据
                    continue
                # trait_states
                ts = extracted.get("trait_states")
                if isinstance(ts, dict):
                    for tn, tv in ts.items():
                        if isinstance(tv, dict):
                            merged["trait_states"][str(tn)] = dict(tv)
                # lists
                for k in ("core_values", "preferences", "growth_records", "extra_entries"):
                    raw = extracted.get(k)
                    if isinstance(raw, list):
                        merged[k].extend(raw)
                # dicts
                for k in ("current_state", "identity_overrides", "relationship_state"):
                    raw = extracted.get(k)
                    if isinstance(raw, dict):
                        for dk, dv in raw.items():
                            if dv is not None:
                                merged[k][str(dk)] = dv
            except Exception as exc:  # noqa: BLE001
                errors.append({
                    "source": name,
                    "error": str(exc),
                })
        self._last_merged_inputs = {
            k: v for k, v in merged.items() if v or k in ("trait_states",)
        }
        self._last_merge_errors = errors
        return merged

    def last_merged_inputs(self) -> Optional[Dict[str, Any]]:
        return self._last_merged_inputs

    def last_merge_errors(self) -> List[Dict[str, str]]:
        return list(self._last_merge_errors)

    def source_health_check(self) -> Dict[str, Any]:
        """所有 Source Adapter 的 health_check 聚合。"""
        result: Dict[str, Any] = {
            "sources": {},
            "schema_version": SELF_MODEL_REGISTRY_SCHEMA_VERSION,
            "count": len(self._source_adapters),
        }
        for name, source in self._source_adapters.items():
            try:
                result["sources"][name] = source.health_check()
            except Exception as exc:  # noqa: BLE001
                result["sources"][name] = {
                    "healthy": False,
                    "error": str(exc),
                }
        return result


__all__ = [
    "SelfModelRegistry",
    "SELF_MODEL_REGISTRY_SCHEMA_VERSION",
]
