"""
Phase 3 单元测试：Autonomous Intelligence Layer

测试覆盖：
1. Autonomous API 路由（goals / reflections / proactive / dream / growth-pending）
2. ProactiveEngine 主动行为引擎
3. ActionConfidenceGate 克制层
4. DreamLayer 梦境/模拟层
5. 数据类序列化（ProposedAction / SimulationResult）
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_PROJECT_ROOT = Path(__file__).parent.parent


# ==========================================
# API 测试（Flask Test Client）
# ==========================================

class TestAutonomousGoalsAPI(unittest.TestCase):
    """自主目标系统 API 测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_goals_returns_ok(self):
        """GET /admin/api/autonomous/goals → 200"""
        resp = self.client.get("/admin/api/autonomous/goals?mock=true")
        self.assertEqual(resp.status_code, 200)

    def test_goals_structure(self):
        """目标返回结构完整"""
        resp = self.client.get("/admin/api/autonomous/goals?mock=true")
        data = resp.get_json()
        self.assertIn("goals", data)
        self.assertIn("stats", data)
        self.assertIn("mock", data)

    def test_goals_stats_fields(self):
        """目标统计字段完整"""
        resp = self.client.get("/admin/api/autonomous/goals?mock=true")
        stats = resp.get_json()["stats"]
        self.assertIn("total_goals", stats)
        self.assertIn("active_goals", stats)
        self.assertIn("completed_goals", stats)

    def test_goals_item_structure(self):
        """单个目标字段完整"""
        resp = self.client.get("/admin/api/autonomous/goals?mock=true")
        goals = resp.get_json()["goals"]
        self.assertGreater(len(goals), 0)
        g = goals[0]
        for key in ("goal_id", "description", "goal_type", "status",
                    "priority", "progress", "created_at", "updated_at"):
            self.assertIn(key, g, f"目标缺少字段: {key}")


class TestAutonomousReflectionsAPI(unittest.TestCase):
    """自我反思系统 API 测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_reflections_returns_ok(self):
        """GET /admin/api/autonomous/reflections → 200"""
        resp = self.client.get("/admin/api/autonomous/reflections?mock=true")
        self.assertEqual(resp.status_code, 200)

    def test_reflections_structure(self):
        """反思返回结构完整"""
        resp = self.client.get("/admin/api/autonomous/reflections?mock=true")
        data = resp.get_json()
        self.assertIn("reflections", data)
        self.assertIn("stats", data)

    def test_reflections_item_structure(self):
        """单个反思记录字段完整"""
        resp = self.client.get("/admin/api/autonomous/reflections?mock=true")
        reflections = resp.get_json()["reflections"]
        self.assertGreater(len(reflections), 0)
        r = reflections[0]
        for key in ("reflection_id", "reflection_type", "started_at",
                    "completed_at", "insights", "improvements"):
            self.assertIn(key, r, f"反思缺少字段: {key}")
        self.assertIsInstance(r["insights"], list)
        self.assertIsInstance(r["improvements"], list)


class TestAutonomousProactiveAPI(unittest.TestCase):
    """主动行为引擎 API 测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_proactive_returns_ok(self):
        """GET /admin/api/autonomous/proactive → 200"""
        resp = self.client.get("/admin/api/autonomous/proactive?mock=true")
        self.assertEqual(resp.status_code, 200)

    def test_proactive_structure(self):
        """主动行为返回结构完整"""
        resp = self.client.get("/admin/api/autonomous/proactive?mock=true")
        data = resp.get_json()
        self.assertIn("actions", data)
        self.assertIn("stats", data)

    def test_proactive_stats_fields(self):
        """主动行为统计字段完整"""
        resp = self.client.get("/admin/api/autonomous/proactive?mock=true")
        stats = resp.get_json()["stats"]
        for key in ("total", "executed", "rejected", "deferred"):
            self.assertIn(key, stats)

    def test_proactive_action_structure(self):
        """单个主动行为字段完整"""
        resp = self.client.get("/admin/api/autonomous/proactive?mock=true")
        actions = resp.get_json()["actions"]
        self.assertGreater(len(actions), 0)
        a = actions[0]
        for key in ("action_id", "action_type", "content", "trigger_reason",
                    "necessity_score", "disturbance_risk", "confidence",
                    "outcome", "outcome_reason"):
            self.assertIn(key, a, f"行为缺少字段: {key}")

    def test_proactive_action_outcome_values(self):
        """行为结果值为合法枚举"""
        resp = self.client.get("/admin/api/autonomous/proactive?mock=true")
        actions = resp.get_json()["actions"]
        valid_outcomes = {"executed", "rejected", "deferred", "skipped", None}
        for a in actions:
            self.assertIn(a.get("outcome"), valid_outcomes)


class TestAutonomousDreamAPI(unittest.TestCase):
    """梦境/模拟层 API 测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_dream_returns_ok(self):
        """GET /admin/api/autonomous/dream → 200"""
        resp = self.client.get("/admin/api/autonomous/dream?mock=true")
        self.assertEqual(resp.status_code, 200)

    def test_dream_structure(self):
        """模拟返回结构完整"""
        resp = self.client.get("/admin/api/autonomous/dream?mock=true")
        data = resp.get_json()
        self.assertIn("simulations", data)
        self.assertIn("stats", data)

    def test_dream_simulation_structure(self):
        """单个模拟记录字段完整"""
        resp = self.client.get("/admin/api/autonomous/dream?mock=true")
        simulations = resp.get_json()["simulations"]
        self.assertGreater(len(simulations), 0)
        s = simulations[0]
        for key in ("simulation_id", "scenario_type", "description",
                    "risk_level", "outcomes", "recommendation"):
            self.assertIn(key, s, f"模拟缺少字段: {key}")

    def test_dream_outcome_structure(self):
        """模拟结果项字段完整"""
        resp = self.client.get("/admin/api/autonomous/dream?mock=true")
        simulations = resp.get_json()["simulations"]
        for s in simulations:
            for outcome in s.get("outcomes", []):
                self.assertIn("metric", outcome)
                self.assertIn("before", outcome)
                self.assertIn("after", outcome)

    def test_dream_risk_level_valid(self):
        """风险等级为合法值"""
        resp = self.client.get("/admin/api/autonomous/dream?mock=true")
        simulations = resp.get_json()["simulations"]
        valid_levels = {"low", "medium", "high"}
        for s in simulations:
            self.assertIn(s.get("risk_level"), valid_levels)


class TestAutonomousGrowthPendingAPI(unittest.TestCase):
    """成长待审批 API 测试"""

    @classmethod
    def setUpClass(cls):
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        cls.app = Flask(__name__)
        cls.app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        cls.client = cls.app.test_client()

    def test_growth_pending_returns_ok(self):
        """GET /admin/api/autonomous/growth-pending → 200"""
        resp = self.client.get("/admin/api/autonomous/growth-pending?mock=true")
        self.assertEqual(resp.status_code, 200)

    def test_growth_pending_structure(self):
        """待审批返回结构完整"""
        resp = self.client.get("/admin/api/autonomous/growth-pending?mock=true")
        data = resp.get_json()
        self.assertIn("proposals", data)
        self.assertIn("stats", data)

    def test_growth_pending_stats_fields(self):
        """待审批统计字段完整"""
        resp = self.client.get("/admin/api/autonomous/growth-pending?mock=true")
        stats = resp.get_json()["stats"]
        for key in ("pending", "approved", "rejected"):
            self.assertIn(key, stats)

    def test_growth_pending_proposal_structure(self):
        """单个提案字段完整"""
        resp = self.client.get("/admin/api/autonomous/growth-pending?mock=true")
        proposals = resp.get_json()["proposals"]
        self.assertGreater(len(proposals), 0)
        p = proposals[0]
        for key in ("proposal_id", "title", "description", "type",
                    "impact", "status", "created_at"):
            self.assertIn(key, p, f"提案缺少字段: {key}")


# ==========================================
# 后端模块测试
# ==========================================

import pytest


class TestProactiveEngine:
    """主动行为引擎单元测试"""

    def test_propose_action_creates_action(self):
        """提议行为应创建 ProposedAction"""
        from src.proactive.proactive_engine import ProactiveEngine, ActionType
        engine = ProactiveEngine()
        action = engine.propose_action(
            action_type=ActionType.GREETING,
            content="你好",
            trigger_reason="测试",
            user_id="user_001",
            necessity_score=0.8,
            disturbance_risk=0.2,
        )
        assert action.action_id.startswith("act_")
        assert action.action_type == ActionType.GREETING
        assert action.content == "你好"
        assert action.confidence == 0.8 * (1 - 0.2)
        assert action.outcome is None

    def test_propose_action_stores_action(self):
        """提议行为应存储在引擎中"""
        from src.proactive.proactive_engine import ProactiveEngine, ActionType
        engine = ProactiveEngine()
        action = engine.propose_action(
            action_type=ActionType.CARE,
            content="注意休息",
            trigger_reason="长时间工作",
        )
        stored = engine.get_action(action.action_id)
        assert stored is not None
        assert stored.action_id == action.action_id

    def test_execute_action_not_found(self):
        """执行不存在的行为应返回 False"""
        from src.proactive.proactive_engine import ProactiveEngine
        engine = ProactiveEngine()
        result = engine.execute_action("act_nonexistent")
        assert result is False

    def test_get_recent_actions_default_limit(self):
        """获取最近行为应限制数量"""
        from src.proactive.proactive_engine import ProactiveEngine, ActionType
        engine = ProactiveEngine()
        for i in range(25):
            engine.propose_action(
                action_type=ActionType.GREETING,
                content=f"消息{i}",
                trigger_reason="测试",
            )
        recent = engine.get_recent_actions(limit=10)
        assert len(recent) == 10

    def test_get_pending_actions(self):
        """获取待执行行为应只返回无结果的行为"""
        from src.proactive.proactive_engine import (
            ProactiveEngine, ActionType, ActionOutcome,
        )
        engine = ProactiveEngine()
        a1 = engine.propose_action(
            action_type=ActionType.GREETING,
            content="你好",
            trigger_reason="测试",
        )
        a2 = engine.propose_action(
            action_type=ActionType.CARE,
            content="注意休息",
            trigger_reason="测试",
        )
        # 手动设置一个 outcome
        engine._actions[a1.action_id].outcome = ActionOutcome.EXECUTED
        pending = engine.get_pending_actions()
        assert len(pending) == 1
        assert pending[0].action_id == a2.action_id

    def test_action_to_dict(self):
        """ProposedAction 应正确序列化"""
        from src.proactive.proactive_engine import (
            ProposedAction, ActionType, ActionOutcome,
        )
        action = ProposedAction(
            action_id="act_test",
            action_type=ActionType.SHARE,
            content="分享内容",
            trigger_reason="测试",
            user_id="user_001",
            necessity_score=0.7,
            disturbance_risk=0.3,
            confidence=0.5,
        )
        d = action.to_dict()
        assert d["action_id"] == "act_test"
        assert d["action_type"] == "share"
        assert d["outcome"] is None
        assert d["necessity_score"] == 0.7


class TestActionConfidenceGate:
    """行为信心克制层单元测试"""

    def test_evaluate_do_not_disturb(self):
        """免打扰模式应拒绝"""
        from src.proactive.proactive_engine import (
            ActionConfidenceGate, ProposedAction, ActionType, ActionOutcome,
        )
        gate = ActionConfidenceGate()
        gate.set_user_preference("user_001", {"do_not_disturb": True})
        action = ProposedAction(
            action_id="act_001",
            action_type=ActionType.GREETING,
            content="你好",
            trigger_reason="测试",
            user_id="user_001",
            necessity_score=0.9,
            disturbance_risk=0.1,
            confidence=0.8,
        )
        outcome, reason = gate.evaluate(action)
        assert outcome == ActionOutcome.REJECTED
        assert "免打扰" in reason

    def test_evaluate_low_necessity(self):
        """必要性不足应跳过"""
        from src.proactive.proactive_engine import (
            ActionConfidenceGate, ProposedAction, ActionType, ActionOutcome,
        )
        gate = ActionConfidenceGate()
        action = ProposedAction(
            action_id="act_001",
            action_type=ActionType.GREETING,
            content="你好",
            trigger_reason="测试",
            necessity_score=0.1,
            disturbance_risk=0.2,
            confidence=0.1,
        )
        outcome, reason = gate.evaluate(action)
        assert outcome == ActionOutcome.SKIPPED
        assert "必要性" in reason

    def test_evaluate_high_disturbance(self):
        """打扰风险过高应拒绝"""
        from src.proactive.proactive_engine import (
            ActionConfidenceGate, ProposedAction, ActionType, ActionOutcome,
        )
        gate = ActionConfidenceGate()
        action = ProposedAction(
            action_id="act_001",
            action_type=ActionType.SHARE,
            content="分享",
            trigger_reason="测试",
            necessity_score=0.8,
            disturbance_risk=0.9,
            confidence=0.08,
        )
        outcome, reason = gate.evaluate(action)
        assert outcome == ActionOutcome.REJECTED
        assert "打扰风险" in reason

    def test_evaluate_executed(self):
        """通过所有检查应执行"""
        from src.proactive.proactive_engine import (
            ActionConfidenceGate, ProposedAction, ActionType, ActionOutcome,
        )
        gate = ActionConfidenceGate()
        action = ProposedAction(
            action_id="act_001",
            action_type=ActionType.GREETING,
            content="你好",
            trigger_reason="测试",
            necessity_score=0.9,
            disturbance_risk=0.1,
            confidence=0.81,
        )
        outcome, reason = gate.evaluate(action)
        assert outcome == ActionOutcome.EXECUTED
        assert "通过" in reason

    def test_user_stats(self):
        """用户统计应返回今日行为数"""
        from src.proactive.proactive_engine import ActionConfidenceGate
        gate = ActionConfidenceGate()
        stats = gate.get_user_stats("user_001")
        assert "today_actions" in stats
        assert "total_recent" in stats
        assert "preference" in stats


class TestDreamLayer:
    """梦境/模拟层单元测试"""

    def test_simulate_behavior_returns_result(self):
        """行为模拟应返回结果"""
        from src.dream.dream_layer import DreamLayer, ScenarioType
        layer = DreamLayer()
        result = layer.simulate_scenario(
            scenario_type=ScenarioType.BEHAVIOR,
            description="测试行为模拟",
            variables={"trust": 50},
        )
        assert result.result_id.startswith("res_")
        assert result.scenario_id.startswith("sim_")
        assert len(result.outcomes) > 0
        assert result.risk_level in ("low", "medium", "high")

    def test_simulate_growth_with_variables(self):
        """成长模拟应处理变量变化"""
        from src.dream.dream_layer import DreamLayer, ScenarioType
        layer = DreamLayer()
        result = layer.simulate_scenario(
            scenario_type=ScenarioType.GROWTH,
            description="测试成长模拟",
            variables={"creativity": {"before": 60, "after": 80}},
        )
        assert len(result.outcomes) > 0
        # 检查是否包含 creativity 变化
        metrics = [o.metric for o in result.outcomes]
        assert "creativity" in metrics

    def test_simulate_relationship(self):
        """关系模拟应返回结果"""
        from src.dream.dream_layer import DreamLayer, ScenarioType
        layer = DreamLayer()
        result = layer.simulate_scenario(
            scenario_type=ScenarioType.RELATIONSHIP,
            description="测试关系模拟",
        )
        assert len(result.outcomes) > 0
        assert result.risk_level == "low"

    def test_simulate_creative(self):
        """创意模拟应返回结果"""
        from src.dream.dream_layer import DreamLayer, ScenarioType
        layer = DreamLayer()
        result = layer.simulate_scenario(
            scenario_type=ScenarioType.CREATIVE,
            description="测试创意模拟",
        )
        assert len(result.outcomes) > 0

    def test_simulate_future(self):
        """未来预测应返回结果"""
        from src.dream.dream_layer import DreamLayer, ScenarioType
        layer = DreamLayer()
        result = layer.simulate_scenario(
            scenario_type=ScenarioType.FUTURE,
            description="测试未来预测",
        )
        assert len(result.outcomes) > 0

    def test_simulate_behavior_impact_shortcut(self):
        """快捷方法 simulate_behavior_impact 应工作"""
        from src.dream.dream_layer import DreamLayer
        layer = DreamLayer()
        result = layer.simulate_behavior_impact(
            behavior="发送问候",
            user_id="user_001",
            context={"mood": "happy"},
        )
        assert result.result_id.startswith("res_")

    def test_predict_future_state_shortcut(self):
        """快捷方法 predict_future_state 应工作"""
        from src.dream.dream_layer import DreamLayer
        layer = DreamLayer()
        result = layer.predict_future_state(days=7, focus_areas=["trust"])
        assert result.result_id.startswith("res_")

    def test_scenario_storage(self):
        """模拟场景应被存储"""
        from src.dream.dream_layer import DreamLayer, ScenarioType
        layer = DreamLayer()
        result = layer.simulate_scenario(
            scenario_type=ScenarioType.BEHAVIOR,
            description="存储测试",
        )
        stored_scenario = layer.get_scenario(result.scenario_id)
        assert stored_scenario is not None
        assert stored_scenario.scenario_id == result.scenario_id

    def test_result_storage(self):
        """模拟结果应被存储"""
        from src.dream.dream_layer import DreamLayer, ScenarioType
        layer = DreamLayer()
        result = layer.simulate_scenario(
            scenario_type=ScenarioType.BEHAVIOR,
            description="结果存储测试",
        )
        stored_result = layer.get_result(result.result_id)
        assert stored_result is not None
        assert stored_result.result_id == result.result_id

    def test_simulation_result_to_dict(self):
        """SimulationResult 应正确序列化"""
        from src.dream.dream_layer import (
            SimulationResult, SimulationOutcome,
        )
        result = SimulationResult(
            result_id="res_test",
            scenario_id="sim_test",
            outcomes=[
                SimulationOutcome(
                    metric="trust",
                    before=50,
                    after=55,
                    confidence=0.8,
                    description="信任度提升",
                ),
            ],
            risk_level="low",
            recommendation="建议执行",
        )
        d = result.to_dict()
        assert d["result_id"] == "res_test"
        assert d["risk_level"] == "low"
        assert len(d["outcomes"]) == 1
        assert d["outcomes"][0]["metric"] == "trust"

    def test_scenario_to_dict(self):
        """SimulationScenario 应正确序列化"""
        from src.dream.dream_layer import (
            SimulationScenario, ScenarioType, SimulationVariable,
        )
        scenario = SimulationScenario(
            scenario_id="sim_test",
            scenario_type=ScenarioType.BEHAVIOR,
            description="测试",
            variables=[
                SimulationVariable(
                    name="mood",
                    current_value="tired",
                    simulated_value="happy",
                ),
            ],
            assumptions=["用户在线"],
        )
        d = scenario.to_dict()
        assert d["scenario_id"] == "sim_test"
        assert d["scenario_type"] == "behavior"
        assert len(d["variables"]) == 1
        assert d["variables"][0]["name"] == "mood"
        assert d["assumptions"] == ["用户在线"]


class TestDreamLayerLifecycle:
    """梦境层生命周期测试"""

    def test_start_stop(self):
        """启动和停止应正常工作"""
        from src.dream.dream_layer import DreamLayer
        layer = DreamLayer()
        assert layer._running is False
        # start 需要 cognitive core，可能失败但不抛异常
        started = layer.start()
        # 停止应总是成功
        stopped = layer.stop()
        assert stopped is True


class TestProactiveEngineLifecycle:
    """主动行为引擎生命周期测试"""

    def test_start_stop(self):
        """启动和停止应正常工作"""
        from src.proactive.proactive_engine import ProactiveEngine
        engine = ProactiveEngine()
        assert engine._running is False
        started = engine.start()
        assert engine._running is True
        stopped = engine.stop()
        assert stopped is True
        assert engine._running is False

    def test_record_user_activity(self):
        """记录用户活跃时间应存储"""
        from src.proactive.proactive_engine import ProactiveEngine
        engine = ProactiveEngine()
        engine.record_user_activity("user_001")
        assert "user_001" in engine._user_last_active
        assert engine._user_last_active["user_001"] > 0


class TestAutonomousIntegration:
    """自主智能层集成测试"""

    def test_all_mock_endpoints_exist(self):
        """所有 mock 端点都应存在并返回 200"""
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        app = Flask(__name__)
        app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        client = app.test_client()

        endpoints = [
            "/admin/api/autonomous/goals?mock=true",
            "/admin/api/autonomous/reflections?mock=true",
            "/admin/api/autonomous/proactive?mock=true",
            "/admin/api/autonomous/dream?mock=true",
            "/admin/api/autonomous/growth-pending?mock=true",
        ]
        for endpoint in endpoints:
            resp = client.get(endpoint)
            assert resp.status_code == 200, f"端点 {endpoint} 返回 {resp.status_code}"

    def test_mock_flag_present(self):
        """mock 响应应包含 mock=True 标记"""
        os.chdir(str(_PROJECT_ROOT))
        from flask import Flask
        from src.admin.api.routes import admin_bp, init_admin
        app = Flask(__name__)
        app.register_blueprint(admin_bp, url_prefix="/admin")
        init_admin(orchestrator=None)
        client = app.test_client()

        resp = client.get("/admin/api/autonomous/goals?mock=true")
        data = resp.get_json()
        assert data.get("mock") is True


if __name__ == "__main__":
    unittest.main()
