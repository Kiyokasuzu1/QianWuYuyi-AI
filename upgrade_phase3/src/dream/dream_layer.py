"""
Dream / Simulation Layer

羽依梦境/模拟层

设计目标：
- 情景推演：如果...会发生什么？
- 未来预测：基于当前趋势预测未来状态
- 创造方案：生成新的行为/成长方案
- 行为模拟：模拟不同行为的结果

用途：
- 创意生成
- 情景模拟
- 未来规划
- 行为影响预测

使用方式：
    from src.dream.dream_layer import DreamLayer

    dream = DreamLayer()
    dream.start()

    # 模拟一个场景
    result = dream.simulate_scenario(
        scenario_type="behavior",
        description="如果羽依主动分享一首用户喜欢的音乐",
        variables={"user_mood": "tired", "trust_level": 0.6},
    )
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.core.yuyi_cognitive_core import (
    CognitiveLayerBase,
    get_cognitive_core,
)
from src.contracts.cognitive_event_types import CognitiveEvent

logger = logging.getLogger(__name__)


class ScenarioType(Enum):
    """场景类型"""
    BEHAVIOR = "behavior"       # 行为模拟
    GROWTH = "growth"           # 成长影响模拟
    RELATIONSHIP = "relationship"  # 关系发展模拟
    CREATIVE = "creative"       # 创意生成
    FUTURE = "future"           # 未来预测


@dataclass
class SimulationVariable:
    """模拟变量"""
    name: str
    current_value: Any
    simulated_value: Any
    impact_weight: float = 1.0  # 影响权重


@dataclass
class SimulationOutcome:
    """模拟结果项"""
    metric: str
    before: Any
    after: Any
    confidence: float  # 0-1
    description: str


@dataclass
class SimulationScenario:
    """模拟场景"""
    scenario_id: str
    scenario_type: ScenarioType
    description: str

    # 变量
    variables: List[SimulationVariable] = field(default_factory=list)

    # 时间
    created_at: float = 0.0
    simulated_at: float = 0.0

    # 假设条件
    assumptions: List[str] = field(default_factory=list)

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "scenario_type": self.scenario_type.value,
            "description": self.description,
            "variables": [
                {
                    "name": v.name,
                    "current_value": v.current_value,
                    "simulated_value": v.simulated_value,
                    "impact_weight": v.impact_weight,
                }
                for v in self.variables
            ],
            "created_at": self.created_at,
            "simulated_at": self.simulated_at,
            "assumptions": self.assumptions,
        }


@dataclass
class SimulationResult:
    """模拟结果"""
    result_id: str
    scenario_id: str

    # 结果
    outcomes: List[SimulationOutcome] = field(default_factory=list)

    # 总体评估
    overall_confidence: float = 0.0
    risk_level: str = "low"  # low / medium / high
    recommendation: str = ""

    # 时间
    created_at: float = 0.0

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result_id": self.result_id,
            "scenario_id": self.scenario_id,
            "outcomes": [
                {
                    "metric": o.metric,
                    "before": o.before,
                    "after": o.after,
                    "confidence": o.confidence,
                    "description": o.description,
                }
                for o in self.outcomes
            ],
            "overall_confidence": round(self.overall_confidence, 2),
            "risk_level": self.risk_level,
            "recommendation": self.recommendation,
            "created_at": self.created_at,
        }


class DreamCognitiveLayer(CognitiveLayerBase):
    """
    梦境认知层

    集成到 YuyiCognitiveCore
    """

    def __init__(self, dream_layer: "DreamLayer"):
        super().__init__("dream")
        self._dream_layer = dream_layer

    def on_growth_applied(self, event: CognitiveEvent):
        """成长应用后，模拟未来影响"""
        proposal_id = event.data.get("proposal_id", "")
        changes = event.data.get("changes_applied", [])

        # 为每个变化创建未来预测模拟
        for change in changes:
            category = change.get("category", "")
            before = change.get("before", 0)
            after = change.get("after", 0)

            self._dream_layer.simulate_scenario(
                scenario_type=ScenarioType.GROWTH,
                description=f"预测成长应用后的未来状态: {category}",
                variables={category: {"before": before, "after": after}},
            )


class DreamLayer:
    """
    梦境/模拟层

    管理羽依的情景模拟和创意生成
    """

    def __init__(self):
        # 场景存储
        self._scenarios: Dict[str, SimulationScenario] = {}
        self._results: Dict[str, SimulationResult] = {}

        # 认知核心
        self._cognitive_core = None

        # 梦境层
        self._dream_layer: Optional[DreamCognitiveLayer] = None

        # 状态
        self._running = False
        self._lock = threading.Lock()

        logger.info("DreamLayer initialized")

    def start(self) -> bool:
        """启动梦境层"""
        if self._running:
            return True

        logger.info("Starting DreamLayer")

        try:
            self._cognitive_core = get_cognitive_core()
            if not self._cognitive_core.is_running():
                self._cognitive_core.start()

            self._dream_layer = DreamCognitiveLayer(self)
            self._cognitive_core.register_layer("dream", self._dream_layer)

            self._dream_layer._status = "running"
            self._dream_layer.update_digest("simulation_count", 0)

            self._running = True
            logger.info("DreamLayer started successfully")
            return True

        except Exception as e:
            logger.error(f"DreamLayer start failed: {e}")
            return False

    def stop(self) -> bool:
        """停止梦境层"""
        if not self._running:
            return True

        self._running = False
        logger.info("DreamLayer stopped")
        return True

    # ==========================================
    # 场景模拟
    # ==========================================

    def simulate_scenario(
        self,
        scenario_type: ScenarioType,
        description: str,
        variables: Optional[Dict[str, Any]] = None,
        assumptions: Optional[List[str]] = None,
    ) -> SimulationResult:
        """
        模拟一个场景

        Args:
            scenario_type: 场景类型
            description: 场景描述
            variables: 变量字典
            assumptions: 假设条件

        Returns:
            模拟结果
        """
        scenario = SimulationScenario(
            scenario_id=f"sim_{uuid.uuid4().hex[:12]}",
            scenario_type=scenario_type,
            description=description,
            assumptions=assumptions or [],
        )

        # 转换变量
        if variables:
            for name, val in variables.items():
                if isinstance(val, dict) and "before" in val and "after" in val:
                    scenario.variables.append(
                        SimulationVariable(
                            name=name,
                            current_value=val["before"],
                            simulated_value=val["after"],
                        )
                    )
                else:
                    scenario.variables.append(
                        SimulationVariable(
                            name=name,
                            current_value=val,
                            simulated_value=val,
                        )
                    )

        with self._lock:
            self._scenarios[scenario.scenario_id] = scenario

        # 执行模拟
        result = self._run_simulation(scenario)

        with self._lock:
            self._results[result.result_id] = result

        scenario.simulated_at = time.time()

        # 更新层摘要
        if self._dream_layer:
            self._dream_layer.update_digest("simulation_count", len(self._results))

        logger.info(f"Simulation completed: {result.result_id} (scenario={scenario.scenario_id})")
        return result

    def _run_simulation(self, scenario: SimulationScenario) -> SimulationResult:
        """执行模拟的核心逻辑"""
        result = SimulationResult(
            result_id=f"res_{uuid.uuid4().hex[:12]}",
            scenario_id=scenario.scenario_id,
        )

        # 根据场景类型执行不同模拟
        if scenario.scenario_type == ScenarioType.BEHAVIOR:
            self._simulate_behavior(scenario, result)
        elif scenario.scenario_type == ScenarioType.GROWTH:
            self._simulate_growth(scenario, result)
        elif scenario.scenario_type == ScenarioType.RELATIONSHIP:
            self._simulate_relationship(scenario, result)
        elif scenario.scenario_type == ScenarioType.CREATIVE:
            self._simulate_creative(scenario, result)
        elif scenario.scenario_type == ScenarioType.FUTURE:
            self._simulate_future(scenario, result)

        # 计算总体信心
        if result.outcomes:
            result.overall_confidence = sum(o.confidence for o in result.outcomes) / len(result.outcomes)

        return result

    def _simulate_behavior(self, scenario: SimulationScenario, result: SimulationResult):
        """模拟行为影响"""
        # 模拟对用户关系的影响
        result.outcomes.append(
            SimulationOutcome(
                metric="trust_delta",
                before=0,
                after=2,
                confidence=0.7,
                description="主动行为通常会轻微提升信任度",
            )
        )
        result.outcomes.append(
            SimulationOutcome(
                metric="user_satisfaction",
                before="neutral",
                after="positive",
                confidence=0.6,
                description="如果行为时机恰当，用户满意度会提升",
            )
        )
        result.risk_level = "low"
        result.recommendation = "建议在用户活跃时段执行，避免打扰"

    def _simulate_growth(self, scenario: SimulationScenario, result: SimulationResult):
        """模拟成长影响"""
        # 从变量中提取变化
        for var in scenario.variables:
            before = var.current_value
            after = var.simulated_value
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                delta = after - before
                result.outcomes.append(
                    SimulationOutcome(
                        metric=var.name,
                        before=before,
                        after=after,
                        confidence=0.75,
                        description=f"{var.name} 变化 {delta:+.2f}",
                    )
                )

        result.risk_level = "medium"
        result.recommendation = "建议先小范围测试，观察效果后再扩大"

    def _simulate_relationship(self, scenario: SimulationScenario, result: SimulationResult):
        """模拟关系发展"""
        result.outcomes.append(
            SimulationOutcome(
                metric="relationship_level",
                before="acquaintance",
                after="friend",
                confidence=0.5,
                description="持续积极互动可提升关系等级",
            )
        )
        result.risk_level = "low"
        result.recommendation = "保持稳定的互动频率，不要急于求成"

    def _simulate_creative(self, scenario: SimulationScenario, result: SimulationResult):
        """模拟创意生成"""
        result.outcomes.append(
            SimulationOutcome(
                metric="novelty",
                before="baseline",
                after="enhanced",
                confidence=0.6,
                description="模拟环境可以生成新颖的创意方案",
            )
        )
        result.risk_level = "low"
        result.recommendation = "创意方案需要经过评估后再实施"

    def _simulate_future(self, scenario: SimulationScenario, result: SimulationResult):
        """模拟未来预测"""
        result.outcomes.append(
            SimulationOutcome(
                metric="predicted_state",
                before="current",
                after="improved",
                confidence=0.5,
                description="基于当前趋势，未来状态可能改善",
            )
        )
        result.risk_level = "medium"
        result.recommendation = "未来预测存在不确定性，需要持续观察"

    # ==========================================
    # 快捷模拟方法
    # ==========================================

    def simulate_behavior_impact(
        self,
        behavior: str,
        user_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> SimulationResult:
        """
        快捷方法：模拟行为影响

        Args:
            behavior: 行为描述
            user_id: 用户 ID
            context: 上下文

        Returns:
            模拟结果
        """
        return self.simulate_scenario(
            scenario_type=ScenarioType.BEHAVIOR,
            description=f"模拟行为: {behavior}",
            variables=context or {},
        )

    def predict_future_state(
        self,
        days: int = 7,
        focus_areas: Optional[List[str]] = None,
    ) -> SimulationResult:
        """
        快捷方法：预测未来状态

        Args:
            days: 预测天数
            focus_areas: 关注领域

        Returns:
            模拟结果
        """
        return self.simulate_scenario(
            scenario_type=ScenarioType.FUTURE,
            description=f"预测未来 {days} 天状态",
            variables={"days": days, "focus_areas": focus_areas or []},
        )

    # ==========================================
    # 查询
    # ==========================================

    def get_scenario(self, scenario_id: str) -> Optional[SimulationScenario]:
        """获取场景"""
        return self._scenarios.get(scenario_id)

    def get_result(self, result_id: str) -> Optional[SimulationResult]:
        """获取结果"""
        return self._results.get(result_id)

    def get_recent_simulations(self, limit: int = 10) -> List[SimulationResult]:
        """获取最近模拟"""
        results = list(self._results.values())
        results.sort(key=lambda r: r.created_at, reverse=True)
        return results[:limit]

    def get_simulations_by_type(self, scenario_type: ScenarioType) -> List[SimulationResult]:
        """按类型获取模拟"""
        scenario_ids = {
            s.scenario_id for s in self._scenarios.values()
            if s.scenario_type == scenario_type
        }
        return [r for r in self._results.values() if r.scenario_id in scenario_ids]

    def get_stats(self) -> Dict[str, Any]:
        """获取统计"""
        total_scenarios = len(self._scenarios)
        total_results = len(self._results)

        by_type: Dict[str, int] = {}
        for s in self._scenarios.values():
            t = s.scenario_type.value
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "total_scenarios": total_scenarios,
            "total_results": total_results,
            "by_type": by_type,
        }


# ==========================================
# 便捷函数
# ==========================================

_dream_layer_instance: Optional[DreamLayer] = None


def get_dream_layer() -> DreamLayer:
    """获取梦境层单例"""
    global _dream_layer_instance
    if _dream_layer_instance is None:
        _dream_layer_instance = DreamLayer()
    return _dream_layer_instance
