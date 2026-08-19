# -*- coding: utf-8 -*-
"""
src/runtime/perception/adapter.py

Phase 4.0.0: Perception Adapter Layer —— 抽象接口

职责:
定义所有感知 Adapter 的统一抽象接口,供未来 Screen / Vision / Audio 等
具体 Adapter 实现。

设计原则:
- 不实现任何真实感知能力 (无 cv2 / PIL / mss / pyautogui 等)
- Runtime 只依赖本抽象类,不依赖任何具体实现
- 所有 Adapter 必须实现 attach / detach / health_check / observe 四接口
- observe() 返回 Observation 或 None,禁止直接产出 Fact

依赖:
- 仅 stdlib + abc + typing
- 依赖同包内的 Observation / ObservationKind / FactSource
- 禁止 import 任何 vision / screen / audio 库

后续 Phase 接入:
- Phase 4.1+: ScreenObservationAdapter (mss / Pillow / OCR)
- Phase 4.2+: VisionAdapter (LLM Vision / YOLO)
- Phase 4.3+: AudioAdapter (sounddevice / whisper)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from src.runtime.perception.observation import (
    Observation,
    ObservationKind,
)
from src.runtime.perception.fact_source import FactSource


# Phase 4.0.0: Perception Adapter Schema 版本
PERCEPTION_ADAPTER_SCHEMA_VERSION = "1.0"


# ============================================================
# 允许 Observation → Fact 的来源白名单
# ============================================================
# INFERENCE 不允许从 Observation 转换,只能由 LLM/模型生成
ALLOWED_OBS_TO_FACT_SOURCES = frozenset({
    FactSource.VISION,
    FactSource.SYSTEM,
    FactSource.USER_INPUT,
    FactSource.MEMORY,
})


# ============================================================
# 观察-事实转换函数 (防御性)
# ============================================================
def observation_to_fact(
    obs: Observation,
    allowed_sources: Optional[List[FactSource]] = None,
) -> Optional[Any]:
    """将 Observation 转换为 Fact (Phase 4.0.0 强制规则)。

    规则:
    - observation.available=False → 返回 None
    - observation.kind == NONE → 返回 None
    - observation.content 为空 → 返回 None
    - observation.source 不在白名单 → 抛 ValueError
    - confidence 越界 → 返回 None

    Returns:
        Fact 或 None
    """
    if obs is None:
        return None
    if not obs.available:
        return None
    if obs.kind == ObservationKind.NONE:
        return None
    if not obs.content or not str(obs.content).strip():
        return None
    if not (0.0 <= obs.confidence <= 1.0):
        return None

    allowed = set(allowed_sources) if allowed_sources else set(ALLOWED_OBS_TO_FACT_SOURCES)
    if obs.source not in allowed:
        raise ValueError(
            f"Cannot convert observation to fact with source {obs.source!r}; "
            f"allowed: {sorted(allowed, key=str)}"
        )

    # 延迟 import 避免循环依赖
    from src.runtime.perception.fact import Fact

    return Fact(
        content=obs.content,
        source=obs.source,
        confidence=obs.confidence,
        evidence_ids=[obs.observation_id],
        meta={
            "observation_id": obs.observation_id,
            "observation_kind": obs.kind.value,
            "observation_timestamp": obs.timestamp,
        },
    )


def observations_to_facts(
    observations: List[Observation],
    allowed_sources: Optional[List[FactSource]] = None,
) -> List[Any]:
    """批量转换 Observation → Fact,过滤掉 None 与异常。"""
    facts = []
    for obs in observations:
        try:
            fact = observation_to_fact(obs, allowed_sources)
            if fact is not None:
                facts.append(fact)
        except ValueError:
            # 来源不合法的 Observation 跳过 (不允许转换 INFERENCE)
            continue
    return facts


# ============================================================
# 抽象基类
# ============================================================
class PerceptionAdapter(ABC):
    """所有感知 Adapter 的抽象基类 (Phase 4.0.0 v1.0)。

    生命周期:
        attach()  →  active  →  health_check()  →  observe()  →  detach()

    字段:
    - name:               str                # Adapter 名
    - observation_kind:   ObservationKind    # 该 Adapter 产出的观察类型
    - schema_version:     str = "1.0"
    - _attached:          bool               # attach 状态
    - _last_health:       Optional[Dict]     # 最近一次健康检查结果

    子类必须实现:
    - attach()           - 接入 Runtime
    - detach()           - 解除接入
    - health_check()     - 健康检查
    - observe()          - 执行一次观察,返回 Observation 或 None

    Phase 4.0.0 约束:
    - observe() 禁止直接返回 Fact,必须返回 Observation
    - observe() 禁止调用任何 cv2 / PIL / pyautogui / mss 等库
    - 真实实现须在子类中显式编写,本抽象类不提供默认实现
    """

    name: str = "perception_adapter"
    observation_kind: ObservationKind = ObservationKind.NONE
    schema_version: str = PERCEPTION_ADAPTER_SCHEMA_VERSION

    def __init__(self) -> None:
        self._attached: bool = False
        self._last_health: Optional[Dict[str, Any]] = None
        self._observation_count: int = 0

    # --------------------------------------------------------
    # 生命周期接口
    # --------------------------------------------------------
    @abstractmethod
    def attach(self) -> None:
        """接入 Runtime。

        Phase 4.0.0: 仅设置内部状态,不做任何真实设备连接。
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.attach() 未实现"
        )

    @abstractmethod
    def detach(self) -> None:
        """解除接入。

        Phase 4.0.0: 清理内部状态,关闭任何资源句柄。
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.detach() 未实现"
        )

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """健康检查。

        Returns:
            {
                "healthy": bool,
                "name": str,
                "schema_version": str,
                "observation_kind": str,
                "details": Dict[str, Any],   # 可选
            }
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.health_check() 未实现"
        )

    @abstractmethod
    def observe(self) -> Optional[Observation]:
        """执行一次观察,返回 Observation 或 None。

        Phase 4.0.0 约束:
        - 禁止返回 Fact
        - 禁止 import 任何 cv2 / PIL / mss / pyautogui / openai
        - 当设备不可用或无可读数据时,返回 None
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.observe() 未实现"
        )

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def is_attached(self) -> bool:
        return self._attached

    @property
    def observation_count(self) -> int:
        """累计成功产出 Observation 的次数。"""
        return self._observation_count

    # --------------------------------------------------------
    # 便利方法
    # --------------------------------------------------------
    def collect_observations(self) -> List[Observation]:
        """调用 observe() 一次,返回包含 0/1 个 Observation 的列表。"""
        obs = self.observe()
        if obs is None:
            return []
        self._observation_count += 1
        return [obs]

    def collect_facts(self) -> List[Any]:
        """observe() → observation_to_fact() → Fact 列表。"""
        obs_list = self.collect_observations()
        return observations_to_facts(obs_list)

    def to_fact(self, observation: Observation) -> Optional[Any]:
        """将本 Adapter 产出的 Observation 转为 Fact (便利封装)。"""
        return observation_to_fact(observation)

    # --------------------------------------------------------
    # 内部:状态标记
    # --------------------------------------------------------
    def _mark_attached(self) -> None:
        self._attached = True

    def _mark_detached(self) -> None:
        self._attached = False

    def _cache_health(self, result: Dict[str, Any]) -> None:
        self._last_health = result


# ============================================================
# AdapterRegistry (Phase 4.0.0 接口骨架)
# ============================================================
class PerceptionAdapterRegistry:
    """Perception Adapter 注册表 (Phase 4.0.0 v1.0)。

    职责:
    - 接收多个 PerceptionAdapter
    - 提供按 name 索引 / 按 observation_kind 索引
    - 批量 attach / detach / health_check
    - 批量 observe (产出 Observation 列表)

    不依赖任何具体实现,只持有 PerceptionAdapter 抽象类。
    """

    def __init__(self) -> None:
        self._adapters: Dict[str, PerceptionAdapter] = {}

    def register(self, adapter: PerceptionAdapter) -> None:
        """注册一个 Adapter。"""
        if not isinstance(adapter, PerceptionAdapter):
            raise TypeError(
                f"Adapter must be PerceptionAdapter, got {type(adapter).__name__}"
            )
        if adapter.name in self._adapters:
            raise ValueError(
                f"Adapter {adapter.name!r} already registered"
            )
        self._adapters[adapter.name] = adapter

    def unregister(self, name: str) -> Optional[PerceptionAdapter]:
        return self._adapters.pop(name, None)

    def get(self, name: str) -> Optional[PerceptionAdapter]:
        return self._adapters.get(name)

    def by_kind(
        self, kind: ObservationKind
    ) -> List[PerceptionAdapter]:
        """按 observation_kind 索引。"""
        return [
            a for a in self._adapters.values()
            if a.observation_kind == kind
        ]

    def all(self) -> List[PerceptionAdapter]:
        return list(self._adapters.values())

    def __len__(self) -> int:
        return len(self._adapters)

    def __contains__(self, name: str) -> bool:
        return name in self._adapters

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def attach_all(self) -> None:
        for a in self._adapters.values():
            a.attach()

    def detach_all(self) -> None:
        for a in self._adapters.values():
            try:
                a.detach()
            except Exception:
                # 隔离单个 Adapter 失败
                pass

    def health_check_all(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for name, a in self._adapters.items():
            try:
                results[name] = a.health_check()
            except Exception as exc:
                results[name] = {
                    "healthy": False,
                    "name": name,
                    "error": str(exc),
                }
        return results

    def observe_all(self) -> List[Observation]:
        """所有已 attached Adapter 各 observe 一次,聚合 Observation 列表。"""
        observations: List[Observation] = []
        for a in self._adapters.values():
            if not a.is_attached:
                continue
            obs_list = a.collect_observations()
            observations.extend(obs_list)
        return observations


__all__ = [
    "PerceptionAdapter",
    "PerceptionAdapterRegistry",
    "PERCEPTION_ADAPTER_SCHEMA_VERSION",
    "ALLOWED_OBS_TO_FACT_SOURCES",
    "observation_to_fact",
    "observations_to_facts",
]
