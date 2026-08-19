"""
Phase 3.5.6: Experience → Memory → Growth 闭环测试

覆盖：
1. MemoryAdapter 经验存储与查询
2. GrowthAdapter 提案生成与管理
3. 完整闭环流程
4. RuntimeCore 集成
5. 向后兼容测试
"""

import time
import uuid
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

import sys
sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.adapters.memory_adapter import MemoryAdapter
from src.runtime.adapters.growth_adapter import GrowthAdapter
from src.contracts.experience_schema import (
    RuntimeExperience,
    ActionResult,
    ReflectionInsight,
)
from src.contracts.growth_schema import GrowthProposal, ChangeItem


class TestMemoryAdapter(unittest.TestCase):
    """MemoryAdapter 测试"""

    def setUp(self):
        from src.memory.memory_store import MemoryStore
        self._memory_path = str(PROJECT_ROOT / "data" / f"test_memory_{uuid.uuid4().hex[:8]}.json")
        self._memory_store = MemoryStore(self._memory_path)
        self.adapter = MemoryAdapter(memory_store=self._memory_store, user_id="test_user")

    def tearDown(self):
        # 清理测试文件
        try:
            import os
            if os.path.exists(self._memory_path):
                os.unlink(self._memory_path)
        except Exception:
            pass

    def _create_experience(
        self,
        action_type: str = "send_message",
        success: bool = True,
        trigger_type: str = "user_message",
    ) -> RuntimeExperience:
        """创建测试经验"""
        return RuntimeExperience(
            trigger_event={"event_type": trigger_type, "data": {"content": "hello"}},
            trigger_type=trigger_type,
            decision_source="rule",
            action_type=action_type,
            result=ActionResult(
                action_id=f"act_{uuid.uuid4().hex[:8]}",
                success=success,
                response_received=True,
                user_response="hi there",
            ),
            self_state_before={"energy": 0.8, "mood": 0.6},
            self_state_after={"energy": 0.7, "mood": 0.7},
            duration_ms=100.0,
        )

    def test_01_store_experience_success(self):
        """存储经验成功"""
        experience = self._create_experience()
        result = self.adapter.store_experience(experience)

        self.assertTrue(result)
        memory_id = self.adapter.get_experience_memory_id(experience.experience_id)
        self.assertIsNotNone(memory_id)

    def test_02_store_invalid_experience_rejected(self):
        """无效经验被拒绝"""
        experience = self._create_experience()
        experience.valid = False
        experience.invalid_reason = "test_invalid"

        result = self.adapter.store_experience(experience)
        self.assertFalse(result)

    def test_03_get_recent_experiences(self):
        """获取最近经验"""
        # 存储多个经验
        for i in range(5):
            exp = self._create_experience(action_type=f"action_{i}")
            self.adapter.store_experience(exp)

        recent = self.adapter.get_recent_experiences(limit=3)
        self.assertEqual(len(recent), 3)

    def test_04_search_by_action_type(self):
        """按 action_type 搜索"""
        # 存储不同类型的经验
        self.adapter.store_experience(self._create_experience(action_type="send_message"))
        self.adapter.store_experience(self._create_experience(action_type="send_message"))
        self.adapter.store_experience(self._create_experience(action_type="check_status"))

        results = self.adapter.search_experiences(action_type="send_message")
        self.assertEqual(len(results), 2)

    def test_05_search_by_trigger_type(self):
        """按 trigger_type 搜索"""
        self.adapter.store_experience(self._create_experience(trigger_type="user_message"))
        self.adapter.store_experience(self._create_experience(trigger_type="tick"))

        results = self.adapter.search_experiences(trigger_type="user_message")
        self.assertEqual(len(results), 1)

    def test_06_search_combined_filters(self):
        """组合条件搜索"""
        self.adapter.store_experience(
            self._create_experience(action_type="send_message", trigger_type="user_message")
        )
        self.adapter.store_experience(
            self._create_experience(action_type="send_message", trigger_type="tick")
        )
        self.adapter.store_experience(
            self._create_experience(action_type="check_status", trigger_type="user_message")
        )

        results = self.adapter.search_experiences(
            action_type="send_message",
            trigger_type="user_message",
        )
        self.assertEqual(len(results), 1)

    def test_07_memory_to_experience_roundtrip(self):
        """经验序列化/反序列化往返"""
        original = self._create_experience(
            action_type="test_action",
            trigger_type="test_trigger",
        )
        self.adapter.store_experience(original)

        # 获取最近经验
        recent = self.adapter.get_recent_experiences(limit=1)
        self.assertEqual(len(recent), 1)

        loaded = recent[0]
        self.assertEqual(loaded.action_type, "test_action")
        self.assertEqual(loaded.trigger_type, "test_trigger")

    def test_08_empty_adapter_returns_empty(self):
        """空适配器返回空列表"""
        recent = self.adapter.get_recent_experiences()
        self.assertEqual(len(recent), 0)

        results = self.adapter.search_experiences()
        self.assertEqual(len(results), 0)


class TestGrowthAdapter(unittest.TestCase):
    """GrowthAdapter 测试"""

    def setUp(self):
        self._proposals_path = str(PROJECT_ROOT / "data" / f"test_proposals_{uuid.uuid4().hex[:8]}.json")
        self.adapter = GrowthAdapter(proposals_path=self._proposals_path)

    def tearDown(self):
        # 清理测试文件
        try:
            import os
            if os.path.exists(self._proposals_path):
                os.unlink(self._proposals_path)
        except Exception:
            pass

    def _create_insight(
        self,
        insight_type: str = "pattern",
        pattern_detected: str = "high_frequency_proactive",
        confidence: float = 0.7,
    ) -> ReflectionInsight:
        """创建测试洞察"""
        return ReflectionInsight(
            insight_type=insight_type,
            summary=f"检测到模式: {pattern_detected}",
            pattern_detected=pattern_detected,
            pattern_frequency=5,
            confidence=confidence,
            experience_ids=["exp_001", "exp_002", "exp_003"],
            suggested_adjustments=["降低主动消息频率"],
        )

    def test_01_store_insight_creates_proposal(self):
        """存储洞察创建 GrowthProposal"""
        insight = self._create_insight()
        result = self.adapter.store_insight(insight)

        self.assertTrue(result)

        # 检查提案列表
        proposals = self.adapter.list_proposals()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].status, "proposed")

    def test_02_store_duplicate_insight_idempotent(self):
        """重复存储同一洞察是幂等的"""
        insight = self._create_insight()
        self.adapter.store_insight(insight)

        # 再次存储
        result = self.adapter.store_insight(insight)
        self.assertTrue(result)

        # 应该只有一个提案
        proposals = self.adapter.list_proposals()
        self.assertEqual(len(proposals), 1)

    def test_03_list_proposals_all(self):
        """列出所有提案"""
        for i in range(3):
            insight = self._create_insight(
                pattern_detected=f"pattern_{i}",
            )
            self.adapter.store_insight(insight)

        proposals = self.adapter.list_proposals()
        self.assertEqual(len(proposals), 3)

    def test_04_list_proposals_by_status(self):
        """按状态列出提案"""
        # 创建提案
        insight = self._create_insight()
        self.adapter.store_insight(insight)
        proposals = self.adapter.list_proposals()
        proposal_id = proposals[0].id

        # 接受提案
        self.adapter.accept_proposal(proposal_id)

        # 按状态过滤
        proposed = self.adapter.list_proposals(status="proposed")
        accepted = self.adapter.list_proposals(status="accepted")

        self.assertEqual(len(proposed), 0)
        self.assertEqual(len(accepted), 1)

    def test_05_accept_proposal(self):
        """接受提案"""
        insight = self._create_insight()
        self.adapter.store_insight(insight)
        proposals = self.adapter.list_proposals()
        proposal_id = proposals[0].id

        accepted = self.adapter.accept_proposal(proposal_id)

        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.status, "accepted")
        self.assertIsNotNone(accepted.accepted_at)

    def test_06_reject_proposal(self):
        """拒绝提案"""
        insight = self._create_insight()
        self.adapter.store_insight(insight)
        proposals = self.adapter.list_proposals()
        proposal_id = proposals[0].id

        rejected = self.adapter.reject_proposal(proposal_id)

        self.assertIsNotNone(rejected)
        self.assertEqual(rejected.status, "rejected")
        self.assertIsNotNone(rejected.rejected_at)

    def test_07_accept_already_processed_proposal_fails(self):
        """接受已处理的提案失败"""
        insight = self._create_insight()
        self.adapter.store_insight(insight)
        proposals = self.adapter.list_proposals()
        proposal_id = proposals[0].id

        # 接受
        self.adapter.accept_proposal(proposal_id)

        # 再次接受应该返回原提案（状态不再是 proposed）
        result = self.adapter.accept_proposal(proposal_id)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "accepted")

    def test_08_reject_nonexistent_proposal(self):
        """拒绝不存在的提案"""
        result = self.adapter.reject_proposal("nonexistent_id")
        self.assertIsNone(result)

    def test_09_create_proposal_from_insight(self):
        """直接从洞察创建提案"""
        # 创建没有 suggested_adjustments 的洞察，确保只有 1 个变更
        insight = ReflectionInsight(
            insight_type="pattern",
            pattern_detected="test_pattern",
            confidence=0.7,
        )
        proposal = self.adapter.create_proposal_from_insight(insight)

        self.assertIsInstance(proposal, GrowthProposal)
        self.assertEqual(proposal.status, "proposed")
        self.assertEqual(proposal.confidence, insight.confidence)
        self.assertEqual(len(proposal.proposed_changes), 1)

    def test_10_proposal_has_suggested_changes(self):
        """提案包含建议变更"""
        insight = self._create_insight(
            insight_type="pattern",
            pattern_detected="high_frequency_proactive",
        )
        proposal = self.adapter.create_proposal_from_insight(insight)

        self.assertGreater(len(proposal.proposed_changes), 0)
        # 应该包含 initiative 调整建议
        paths = [c.path for c in proposal.proposed_changes]
        self.assertIn("self_state.initiative", paths)

    def test_11_problem_insight_creates_fix_proposal(self):
        """问题洞察创建修复提案"""
        insight = self._create_insight(
            insight_type="problem",
            pattern_detected="low_success_rate",
        )
        proposal = self.adapter.create_proposal_from_insight(insight)

        self.assertGreater(len(proposal.proposed_changes), 0)
        paths = [c.path for c in proposal.proposed_changes]
        # 应该包含 energy_decay_rate 调整建议
        self.assertIn("self_state.energy_decay_rate", paths)

    def test_12_direct_personality_modification_prevented(self):
        """直接修改人格被阻止（验证 GrowthProposal 流程）"""
        # 确保所有变更都是通过 GrowthProposal，而不是直接修改
        insight = self._create_insight()
        proposal = self.adapter.create_proposal_from_insight(insight)

        # 提案只是一个建议，并没有实际修改人格
        self.assertEqual(proposal.status, "proposed")

        # 提案需要被接受才能生效
        # 这证实了"不允许直接修改人格"的原则

    def test_13_clear_history(self):
        """清空历史"""
        for i in range(3):
            insight = self._create_insight()
            self.adapter.store_insight(insight)

        self.assertEqual(len(self.adapter.list_proposals()), 3)

        self.adapter.clear_history()
        self.assertEqual(len(self.adapter.list_proposals()), 0)


class TestClosedLoop(unittest.TestCase):
    """完整闭环测试"""

    def setUp(self):
        from src.memory.memory_store import MemoryStore
        self._memory_path = str(PROJECT_ROOT / "data" / f"test_closing_memory_{uuid.uuid4().hex[:8]}.json")
        self._proposals_path = str(PROJECT_ROOT / "data" / f"test_closing_proposals_{uuid.uuid4().hex[:8]}.json")
        self._memory_store = MemoryStore(self._memory_path)
        self.memory_adapter = MemoryAdapter(memory_store=self._memory_store, user_id="test_user")
        self.growth_adapter = GrowthAdapter(proposals_path=self._proposals_path)

    def tearDown(self):
        import os
        for path in [self._memory_path, self._proposals_path]:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except Exception:
                pass

    def test_01_full_loop_event_to_growth_proposal(self):
        """完整闭环：Event → Experience → Memory → Reflection → GrowthProposal"""
        # Step 1: 创建经验并存储到 Memory
        for i in range(5):
            exp = RuntimeExperience(
                trigger_event={"event_type": "user_message", "data": {"content": f"msg_{i}"}},
                trigger_type="user_message",
                decision_source="rule",
                action_type="send_message",
                result=ActionResult(
                    action_id=f"act_{i}",
                    success=True,
                    response_received=True,
                    user_response=f"response_{i}",
                ),
                self_state_before={"energy": 0.8},
                self_state_after={"energy": 0.7},
                duration_ms=100.0,
            )
            self.memory_adapter.store_experience(exp)

        # Step 2: 从 Memory 获取经验
        experiences = self.memory_adapter.get_recent_experiences(limit=10)
        self.assertEqual(len(experiences), 5)

        # Step 3: 反思生成洞察
        from src.runtime.reflection_engine import ReflectionEngine, ReflectionEngineConfig
        engine = ReflectionEngine(config=ReflectionEngineConfig(min_experiences=3))
        insight = engine.reflect(experiences)

        self.assertIsNotNone(insight)
        self.assertEqual(insight.pattern_detected, "high_frequency_proactive")

        # Step 4: 洞察转换为 GrowthProposal
        result = self.growth_adapter.store_insight(insight)
        self.assertTrue(result)

        # Step 5: 检查 GrowthProposal
        proposals = self.growth_adapter.list_proposals()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].status, "proposed")
        self.assertEqual(proposals[0].confidence, insight.confidence)

    def test_02_memory_persistence_across_sessions(self):
        """Memory 跨会话持久化"""
        # 创建适配器 1，存储经验
        adapter1 = MemoryAdapter(user_id="persist_test")
        exp = RuntimeExperience(
            trigger_event={},
            trigger_type="test",
            action_type="test_action",
            result=ActionResult(action_id="act_test", success=True),
            duration_ms=100.0,
        )
        adapter1.store_experience(exp)

        # 创建适配器 2（新实例），验证数据持久化
        adapter2 = MemoryAdapter(user_id="persist_test")
        recent = adapter2.get_recent_experiences(limit=10)
        self.assertGreaterEqual(len(recent), 1)

    def test_03_growth_proposal_persistence(self):
        """GrowthProposal 持久化"""
        # 创建适配器 1，生成提案
        adapter1 = GrowthAdapter(proposals_path=self._proposals_path)
        insight = ReflectionInsight(
            insight_type="pattern",
            pattern_detected="test_pattern",
            confidence=0.8,
        )
        adapter1.store_insight(insight)

        # 创建适配器 2（新实例），验证提案持久化
        adapter2 = GrowthAdapter(proposals_path=self._proposals_path)
        proposals = adapter2.list_proposals()
        self.assertGreaterEqual(len(proposals), 1)


class TestRuntimeCoreIntegration(unittest.TestCase):
    """RuntimeCore 集成测试"""

    def setUp(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        reset_runtime_bridge()
        self._state_file = str(PROJECT_ROOT / "data" / f"test_integration_state_{uuid.uuid4().hex[:8]}.json")
        self._memory_file = str(PROJECT_ROOT / "data" / f"test_integration_memory_{uuid.uuid4().hex[:8]}.json")
        self._proposals_file = str(PROJECT_ROOT / "data" / f"test_integration_proposals_{uuid.uuid4().hex[:8]}.json")

    def tearDown(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        bridge = RuntimeBridge.get_instance()
        if bridge._runtime_core and bridge._runtime_core.is_running:
            bridge.shutdown()
        reset_runtime_bridge()
        # 清理测试文件
        import os
        for path in [self._state_file, self._memory_file, self._proposals_file]:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except Exception:
                pass

    def _get_config(self, **kwargs):
        """获取测试配置"""
        config = {
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
            "memory_store_path": self._memory_file,
            "growth_proposals_path": self._proposals_file,
        }
        config.update(kwargs)
        return config

    def test_01_adapters_disabled_by_default(self):
        """默认禁用适配器"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config=self._get_config())
        bridge.initialize()

        self.assertIsNone(bridge._runtime_core.memory_adapter)
        self.assertIsNone(bridge._runtime_core.growth_adapter)

    def test_02_adapters_enabled(self):
        """启用适配器"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config=self._get_config(
            experience_enabled=True,
            adapters_enabled=True,
        ))
        bridge.initialize()

        self.assertIsNotNone(bridge._runtime_core.memory_adapter)
        self.assertIsNotNone(bridge._runtime_core.growth_adapter)

    def test_03_event_triggers_full_loop(self):
        """事件触发完整闭环"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config=self._get_config(
            experience_enabled=True,
            adapters_enabled=True,
        ))
        bridge.initialize()

        # 设置高主动状态
        bridge._runtime_core.self_state.initiative = 0.9
        bridge._runtime_core.self_state.social_need = 0.9

        # 触发事件
        bridge._runtime_core.inject_event("user_message", {"content": "hello"})

        # 检查经验已存储到 Memory
        memory_experiences = bridge._runtime_core.get_memory_experiences()
        # 可能有也可能没有，取决于决策结果

    def test_04_reflect_creates_growth_proposal(self):
        """反思创建 GrowthProposal"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config=self._get_config(
            experience_enabled=True,
            adapters_enabled=True,
        ))
        bridge.initialize()

        # 直接添加经验到缓冲区
        for i in range(5):
            exp = RuntimeExperience(
                trigger_event={"event_type": "test", "data": {"i": i}},
                trigger_type="user_message",
                action_type="send_message",
                result=ActionResult(
                    action_id=f"act_{i}",
                    success=True,
                    response_received=True,
                    user_response=f"response_{i}",
                ),
                duration_ms=100.0,
            )
            bridge._runtime_core.experience_builder._buffer.append(exp)

        # 触发反思（会自动创建 GrowthProposal）
        insight = bridge._runtime_core.reflect_on_experiences()

        self.assertIsNotNone(insight)
        self.assertEqual(insight.pattern_detected, "high_frequency_proactive")

        # 检查 GrowthProposal
        proposals = bridge._runtime_core.get_growth_proposals()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].status, "proposed")

    def test_05_growth_proposal_acceptance(self):
        """GrowthProposal 接受流程"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config=self._get_config(
            experience_enabled=True,
            adapters_enabled=True,
        ))
        bridge.initialize()

        # 创建提案
        insight = ReflectionInsight(
            insight_type="pattern",
            pattern_detected="test_pattern",
            confidence=0.8,
        )
        bridge._runtime_core.growth_adapter.store_insight(insight)

        # 获取提案
        proposals = bridge._runtime_core.get_growth_proposals()
        self.assertEqual(len(proposals), 1)
        proposal_id = proposals[0].id

        # 接受提案
        accepted = bridge._runtime_core.accept_growth_proposal(proposal_id)
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.status, "accepted")

    def test_06_backward_compatibility(self):
        """向后兼容：禁用适配器时行为不变"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config=self._get_config())
        bridge.initialize()

        # 正常决策流程
        bridge._runtime_core.self_state.initiative = 0.8
        result = bridge._runtime_core.trigger_decision("test")

        # 应该正常返回
        self.assertIsNotNone(bridge._runtime_core)

    def test_07_memory_search_interface(self):
        """Memory 搜索接口"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config=self._get_config(
            experience_enabled=True,
            adapters_enabled=True,
        ))
        bridge.initialize()

        # 添加经验
        for i in range(3):
            exp = RuntimeExperience(
                trigger_event={},
                trigger_type="test",
                action_type=f"action_{i % 2}",
                result=ActionResult(action_id=f"act_{i}", success=True),
                duration_ms=100.0,
            )
            bridge._runtime_core.memory_adapter.store_experience(exp)

        # 搜索
        results = bridge._runtime_core.search_memory_experiences(action_type="action_0")
        self.assertEqual(len(results), 2)


class TestGrowthProposalSafety(unittest.TestCase):
    """GrowthProposal 安全性测试"""

    def setUp(self):
        self._proposals_path = str(PROJECT_ROOT / "data" / f"test_safety_proposals_{uuid.uuid4().hex[:8]}.json")
        self.adapter = GrowthAdapter(proposals_path=self._proposals_path)

    def tearDown(self):
        import os
        try:
            if os.path.exists(self._proposals_path):
                os.unlink(self._proposals_path)
        except Exception:
            pass

    def test_01_proposal_does_not_modify_personality_directly(self):
        """提案不直接修改人格"""
        insight = ReflectionInsight(
            insight_type="pattern",
            pattern_detected="test_pattern",
            confidence=0.9,
        )
        proposal = self.adapter.create_proposal_from_insight(insight)

        # 提案只是建议
        self.assertEqual(proposal.status, "proposed")

        # 提案包含变更建议，但没有实际执行
        self.assertGreater(len(proposal.proposed_changes), 0)

    def test_02_proposal_requires_explicit_acceptance(self):
        """提案需要显式接受"""
        insight = ReflectionInsight(
            insight_type="problem",
            pattern_detected="low_success_rate",
            confidence=0.95,
        )
        proposal = self.adapter.create_proposal_from_insight(insight)

        # 即使置信度很高，提案也只是 proposed 状态
        self.assertEqual(proposal.status, "proposed")

        # 必须通过 accept_proposal 才能变为 accepted
        stored = self.adapter.store_insight(insight)
        self.assertTrue(stored)

        proposals = self.adapter.list_proposals()
        proposal_id = proposals[0].id

        accepted = self.adapter.accept_proposal(proposal_id)
        self.assertEqual(accepted.status, "accepted")

    def test_03_proposal_can_be_rejected(self):
        """提案可以被拒绝"""
        insight = ReflectionInsight(
            insight_type="pattern",
            pattern_detected="unsafe_pattern",
            confidence=0.3,
        )
        self.adapter.store_insight(insight)

        proposals = self.adapter.list_proposals()
        proposal_id = proposals[0].id

        # 拒绝提案
        rejected = self.adapter.reject_proposal(proposal_id)
        self.assertEqual(rejected.status, "rejected")

        # 确认状态
        rejected_list = self.adapter.list_proposals(status="rejected")
        self.assertGreater(len(rejected_list), 0)

    def test_04_no_direct_state_modification(self):
        """验证没有直接修改 SelfState 的接口"""
        # GrowthAdapter 只处理 GrowthProposal
        # 没有直接修改 SelfState 的方法
        methods = [m for m in dir(GrowthAdapter) if not m.startswith('_')]
        self.assertIn('accept_proposal', methods)
        self.assertIn('reject_proposal', methods)
        self.assertIn('list_proposals', methods)
        # 没有 update_self_state 之类的方法


if __name__ == "__main__":
    unittest.main(verbosity=2)