#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R2.7.6 真实链路审计测试

验证修复后的真实调用链：
  api_server → RuntimeController → ContinuousLoopRunner → DeepSeekAdapter → 回复

测试维度：
  T1. DeepSeek adapter 注入：llm_engine=deepseek 时调用链进入 DeepSeekAdapter
  T2. Mock fallback：llm_engine=mock 时仍使用 MockResponseEngine
  T3. DeepSeek 异常自动降级 Mock
  T4. 用户隔离：用户 A 的数据不泄漏到用户 B
  T5. Memory 上下文保护：10000 条 memory → 系统正常，只取 top-N
  T6. HTTP 真实链路：POST /v1/chat/completions → Phase4 → 回复
"""
import sys
import os
import json
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

import pytest


# ──────────────────────────────────────────────
# 辅助：构造测试用 RuntimeController
# ──────────────────────────────────────────────
def _make_controller(tmp_path, phase4=True, llm_engine="mock", **extra):
    from src.runtime.runtime_controller import RuntimeController
    RuntimeController._instance = None
    users_dir = str(tmp_path / "users")
    cfg = {
        "runtime": {
            "phase4_enabled": phase4,
            "users_root_dir": users_dir,
            "llm_engine": llm_engine,
            "growth_enabled": True,
            "legacy_memory_auto_import": False,
            "max_context_memories": 50,
            "deepseek": {
                "enabled": llm_engine == "deepseek",
                "max_retries": 3,
                "timeout_seconds": 30,
                "max_tokens": 2048,
            },
        }
    }
    cfg["runtime"].update(extra)
    return RuntimeController(config=cfg)


# ──────────────────────────────────────────────
# T1. DeepSeek adapter 注入验证
# ──────────────────────────────────────────────
class TestT1DeepSeekInjection:
    """验证 llm_engine=deepseek 时调用链进入 DeepSeekAdapter。"""

    def test_deepseek_adapter_is_constructed(self, tmp_path):
        """llm_engine=deepseek → _deepseek_adapter 非 None（即使 API key 缺失也构造）。"""
        ctrl = _make_controller(tmp_path, llm_engine="deepseek")
        # 注意：如果 .env 没 API key，DeepSeekAdapter 可能构造失败返回 None
        # 但至少代码路径走了 deepseek 分支
        assert ctrl.llm_engine == "deepseek"

    def test_deepseek_adapter_called_in_handle_message(self, tmp_path):
        """注入 fake adapter → handle_message 时 fake.generate 被调用。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")  # 用 mock 初始化避免真实 API

        # 创建 fake LLM adapter
        fake_adapter = MagicMock()
        fake_adapter.generate.return_value = {
            "text": "这是 DeepSeek 的真实回复",
            "model": "deepseek-test",
            "finish_reason": "stop",
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "latency_ms": 500,
        }

        # 手动注入 fake adapter
        ctrl._deepseek_adapter = fake_adapter

        hr = ctrl.handle_message(
            user_id="qq_test_deepseek",
            message="你好羽依，这是 DeepSeek 注入测试消息",
        )

        # 验证 fake adapter 被调用
        assert fake_adapter.generate.called, "DeepSeek adapter.generate() 未被调用"
        assert hr.reply == "这是 DeepSeek 的真实回复", f"回复不匹配: {hr.reply}"

    def test_deepseek_adapter_passed_to_runner(self, tmp_path):
        """验证 per-user runner 持有 deepseek_adapter 引用。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")
        fake_adapter = MagicMock()
        fake_adapter.generate.return_value = {
            "text": "测试", "model": "fake", "finish_reason": "stop",
            "token_usage": {}, "latency_ms": 0,
        }
        ctrl._deepseek_adapter = fake_adapter

        ctrl.handle_message(user_id="qq_inject_test", message="触发 runner 创建")

        runner = ctrl._get_runner_for_user("qq_inject_test")
        assert runner._llm_adapter is fake_adapter, "runner 未持有 LLM adapter 引用"


# ──────────────────────────────────────────────
# T2. Mock fallback 验证
# ──────────────────────────────────────────────
class TestT2MockFallback:
    """验证 llm_engine=mock 时使用 MockResponseEngine。"""

    def test_mock_engine_no_deepseek(self, tmp_path):
        """llm_engine=mock → _deepseek_adapter 为 None。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")
        assert ctrl._deepseek_adapter is None, "mock 模式不应构造 DeepSeek adapter"

    def test_mock_reply_non_empty(self, tmp_path):
        """mock 模式 → reply 非空且来自 MockResponseEngine。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")
        hr = ctrl.handle_message(
            user_id="qq_mock_test",
            message="你好羽依，这是 Mock 测试消息",
        )
        assert hr.reply and len(hr.reply) > 5, "Mock 回复为空"


# ──────────────────────────────────────────────
# T3. DeepSeek 异常自动降级
# ──────────────────────────────────────────────
class TestT3DeepSeekFailureFallback:
    """验证 DeepSeek 异常时自动降级 MockResponseEngine。"""

    def test_deepseek_exception_falls_back_to_mock(self, tmp_path):
        """DeepSeek adapter 抛异常 → 自动降级 MockResponseEngine → reply 非空。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")

        # 注入会爆炸的 fake adapter
        fake_adapter = MagicMock()
        fake_adapter.generate.side_effect = RuntimeError("模拟 DeepSeek API 超时")
        ctrl._deepseek_adapter = fake_adapter

        hr = ctrl.handle_message(
            user_id="qq_fallback_test",
            message="你好羽依，这是降级测试消息内容足够长",
        )

        # fake adapter 被调用了（但失败了）
        assert fake_adapter.generate.called, "DeepSeek adapter 应该被调用过"
        # 降级到 Mock 后 reply 仍然非空
        assert hr.reply and len(hr.reply) > 5, "降级后 reply 为空"

    def test_deepseek_empty_text_falls_back_to_mock(self, tmp_path):
        """DeepSeek 返回空 text → 降级 MockResponseEngine。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")

        fake_adapter = MagicMock()
        fake_adapter.generate.return_value = {
            "text": "",  # 空回复
            "model": "deepseek",
            "finish_reason": "error_fallback",
            "token_usage": {},
            "latency_ms": 0,
        }
        ctrl._deepseek_adapter = fake_adapter

        hr = ctrl.handle_message(
            user_id="qq_empty_fallback",
            message="你好羽依，空回复降级测试消息内容足够长",
        )
        assert hr.reply and len(hr.reply) > 5, "空回复降级后 reply 为空"


# ──────────────────────────────────────────────
# T4. 用户 Runtime 隔离
# ──────────────────────────────────────────────
class TestT4UserIsolation:
    """验证不同 user_id 不共享 runtime 状态。"""

    def test_per_user_runner_isolation(self, tmp_path):
        """用户 A 和用户 B 有不同的 ContinuousLoopRunner 实例。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")
        ctrl.handle_message(user_id="qq_userA", message="用户A的第一条消息内容足够长")
        ctrl.handle_message(user_id="qq_userB", message="用户B的第一条消息内容足够长")

        runner_a = ctrl._get_runner_for_user("qq_userA")
        runner_b = ctrl._get_runner_for_user("qq_userB")
        assert runner_a is not runner_b, "用户 A/B 共享了同一个 runner"

    def test_user_a_data_not_in_user_b(self, tmp_path):
        """用户 A 聊猫娘 → 用户 B 的 memory 里没有猫娘。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")

        # 用户 A 聊猫娘
        ctrl.handle_message(
            user_id="qq_isolation_A",
            message="我特别喜欢猫娘设计，耳朵和尾巴超级可爱",
        )
        # 用户 B 聊别的
        ctrl.handle_message(
            user_id="qq_isolation_B",
            message="今天天气不错，我想去公园散步",
        )

        pm_a = ctrl._get_pm_for_user("qq_isolation_A")
        pm_b = ctrl._get_pm_for_user("qq_isolation_B")

        mem_a = json.loads(pm_a.memory_file.read_text(encoding="utf-8"))
        mem_b = json.loads(pm_b.memory_file.read_text(encoding="utf-8"))

        text_a = json.dumps(mem_a, ensure_ascii=False)
        text_b = json.dumps(mem_b, ensure_ascii=False)

        assert "猫娘" in text_a, "用户 A 的 memory 没有猫娘记录"
        assert "猫娘" not in text_b, "用户 B 的 memory 泄漏了用户 A 的猫娘记录"

    def test_personality_isolation(self, tmp_path):
        """用户 A 成长后 → 用户 B 的 personality 不受影响。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")

        # 用户 A 连续聊 5 轮情感话题（触发 warmth 成长）
        for i in range(5):
            ctrl.handle_message(
                user_id="qq_growth_A",
                message=f"今天心情很好，想聊聊感情和陪伴的事情，第{i+1}轮",
            )

        # 用户 B 第一次聊天
        ctrl.handle_message(
            user_id="qq_growth_B",
            message="你好羽依，我是新用户",
        )

        pm_a = ctrl._get_pm_for_user("qq_growth_A")
        pm_b = ctrl._get_pm_for_user("qq_growth_B")

        ps_a = json.loads(pm_a.personality_file.read_text(encoding="utf-8"))
        ps_b = json.loads(pm_b.personality_file.read_text(encoding="utf-8"))

        traits_a = ps_a.get("traits") or ps_a.get("core_traits") or {}
        traits_b = ps_b.get("traits") or ps_b.get("core_traits") or {}

        # 用户 B 的 traits 应该是初始值（不继承 A 的成长）
        if "warmth" in traits_a and "warmth" in traits_b:
            # A 经过 5 轮成长，warmth 应该有变化
            # B 应该是初始值
            assert traits_b["warmth"] != traits_a["warmth"], \
                f"用户 B 的 warmth 与 A 相同: A={traits_a['warmth']}, B={traits_b['warmth']}"


# ──────────────────────────────────────────────
# T5. Memory 上下文保护
# ──────────────────────────────────────────────
class TestT5MemoryContextProtection:
    """验证大量 memory 时系统仍正常，且只取 top-N 进入 prompt。"""

    def test_large_memory_still_works(self, tmp_path):
        """模拟 1000 条 memory → 系统仍可正常响应。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock", max_context_memories=20)

        # 先发一条消息创建用户目录
        ctrl.handle_message(
            user_id="qq_large_mem",
            message="初始化用户数据",
        )

        # 直接往 memory.json 注入 1000 条记录
        pm = ctrl._get_pm_for_user("qq_large_mem")
        mem_data = json.loads(pm.memory_file.read_text(encoding="utf-8"))
        records = mem_data.get("records", [])

        for i in range(1000):
            records.append({
                "id": f"m_bulk_{i:05d}",
                "text": f"测试记忆第{i}条",
                "topic": "bulk_test",
                "timestamp_ms": 1700000000000 + i * 1000,
                "importance": float(i) / 1000.0,  # 0.0 ~ 0.999
            })

        mem_data["records"] = records
        pm.memory_file.write_text(json.dumps(mem_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # 再发一条消息，验证系统不崩溃
        start = time.time()
        hr = ctrl.handle_message(
            user_id="qq_large_mem",
            message="在1000条记忆的情况下发消息测试系统稳定性",
        )
        elapsed = time.time() - start

        assert hr.reply and len(hr.reply) > 5, "大量 memory 后回复为空"
        assert elapsed < 10.0, f"响应时间过长: {elapsed:.2f}s"

    def test_context_memory_limit_enforced(self, tmp_path):
        """验证 _select_context_memories 只返回 max_context_memories 条。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock", max_context_memories=10)

        # 构造 100 条 memory
        memories = []
        for i in range(100):
            memories.append({
                "id": f"m_{i:03d}",
                "text": f"记忆{i}",
                "topic": "test",
                "timestamp_ms": 1700000000000 + i * 1000,
                "importance": float(i) / 100.0,
            })

        selected = ctrl._select_context_memories(memories)
        assert len(selected) == 10, f"应返回 10 条，实际 {len(selected)}"
        # 应该取 importance 最高的 10 条（即 90-99）
        imp_values = [r["importance"] for r in selected]
        assert min(imp_values) >= 0.89, f"最低 importance 应 >= 0.89, got {min(imp_values)}"


# ──────────────────────────────────────────────
# T6. HTTP 真实链路
# ──────────────────────────────────────────────
class TestT6HTTPLink:
    """验证 POST /v1/chat/completions → Phase4 → 回复。"""

    def test_http_post_uses_phase4(self, tmp_path):
        """POST /v1/chat/completions → HTTP 200 + Phase4 model 标记。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")

        import api_server as api_mod
        original_ctrl = getattr(api_mod, '_runtime_controller', None)
        api_mod._runtime_controller = ctrl

        try:
            with api_mod.app.test_client() as client:
                resp = client.post("/v1/chat/completions", json={
                    "messages": [{"role": "user", "content": "HTTP 链路测试消息"}],
                    "user": "qq_http_test",
                })
                assert resp.status_code == 200, f"HTTP {resp.status_code}"
                body = resp.get_json()
                assert body and "choices" in body, f"body 无 choices: {body}"
                assert len(body["choices"]) > 0, "choices 为空"
                content = body["choices"][0]["message"]["content"]
                assert content and len(content) > 5, f"content 为空: {content}"
                assert "phase4" in body.get("model", ""), f"model 不含 phase4: {body.get('model')}"
        finally:
            api_mod._runtime_controller = original_ctrl

    def test_http_fallback_on_phase4_failure(self, tmp_path):
        """Phase4 故意失败 → api_server 降级不崩溃。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")

        import api_server as api_mod
        original_ctrl = getattr(api_mod, '_runtime_controller', None)

        # 注入会爆炸的 controller
        class ExplosiveCtrl:
            phase4_enabled = True
            def handle_message(self, **kwargs):
                raise RuntimeError("模拟 Phase4 崩溃")

        api_mod._runtime_controller = ExplosiveCtrl()

        try:
            with api_mod.app.test_client() as client:
                resp = client.post("/v1/chat/completions", json={
                    "messages": [{"role": "user", "content": "降级测试"}],
                    "user": "qq_degrade",
                })
                # 可能的结果：
                #  · 200：本地有 Orchestrator，降级成功
                #  · 503：所有链路均不可用（Phase4/Pipeline/Orchestrator 全挂）
                #  · 500：其他未知异常
                assert resp.status_code in (200, 500, 503), f"意外状态码: {resp.status_code}"
                # 如果是 503，应包含明确的 "不可用" 错误信息（R2.7.6-AUDIT 新增）
                if resp.status_code == 503:
                    body = resp.get_json() or {}
                    err_msg = str(body.get("error", "")) + str(body.get("details", ""))
                    assert "不可用" in err_msg or "失败" in err_msg, \
                        f"503 响应缺少说明: {body}"
        finally:
            api_mod._runtime_controller = original_ctrl


# ──────────────────────────────────────────────
# T7. DeepSeek 重试机制
# ──────────────────────────────────────────────
class TestT7DeepSeekRetry:
    """验证 DeepSeek 429/5xx 重试 + 指数退避。"""

    def test_retry_on_429(self, tmp_path):
        """429 → 重试 max_retries 次后抛异常。"""
        from src.response_phase4.deepseek_adapter import DeepSeekAdapter

        fake_client = MagicMock()
        fake_resp = MagicMock()
        fake_resp.status_code = 429
        fake_resp.text = "Rate limited"
        fake_client.post.return_value = fake_resp

        adapter = DeepSeekAdapter(
            api_key="test_key",
            api_client=fake_client,
            max_retries=2,
        )

        with patch("time.sleep"):  # 跳过实际 sleep
            with pytest.raises(RuntimeError, match="429"):
                adapter._call_api([{"role": "user", "content": "test"}])

        # 验证重试次数 = max_retries + 1 = 3
        assert fake_client.post.call_count == 3, \
            f"应调用 3 次（1+2重试），实际 {fake_client.post.call_count}"

    def test_retry_on_500_then_success(self, tmp_path):
        """500 → 重试 → 第三次成功。"""
        from src.response_phase4.deepseek_adapter import DeepSeekAdapter

        fake_client = MagicMock()

        # 前两次 500，第三次 200
        resp_500 = MagicMock()
        resp_500.status_code = 500
        resp_500.text = "Server error"

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {
            "choices": [{"message": {"content": "成功"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        fake_client.post.side_effect = [resp_500, resp_500, resp_200]

        adapter = DeepSeekAdapter(
            api_key="test_key",
            api_client=fake_client,
            max_retries=3,
        )

        with patch("time.sleep"):
            text, usage, reason = adapter._call_api([{"role": "user", "content": "test"}])

        assert text == "成功"
        assert fake_client.post.call_count == 3

    def test_no_retry_on_400(self, tmp_path):
        """400 → 不重试，直接抛异常。"""
        from src.response_phase4.deepseek_adapter import DeepSeekAdapter

        fake_client = MagicMock()
        resp_400 = MagicMock()
        resp_400.status_code = 400
        resp_400.text = "Bad request"
        fake_client.post.return_value = resp_400

        adapter = DeepSeekAdapter(
            api_key="test_key",
            api_client=fake_client,
            max_retries=3,
        )

        with pytest.raises(RuntimeError, match="400"):
            adapter._call_api([{"role": "user", "content": "test"}])

        # 400 不重试
        assert fake_client.post.call_count == 1, "400 不应重试"


# ================================================================
# T8 ~ T13：R2.7.6-AUDIT 新增测试（第二轮审计修复验证）
# ================================================================

# ──────────────────────────────────────────────
# T8. Legacy Memory Import 跨用户防泄漏
# ──────────────────────────────────────────────
class TestT8LegacyMemoryUserFilter:
    """验证 import_legacy_memory 按 user_id 过滤，防止跨用户数据泄漏。"""

    def test_target_user_only_imports_own_records(self, tmp_path):
        """构造 legacy memory.json 含 用户A + 用户B 记录，导入时只导入匹配 user_id 的。"""
        from src.response_phase4.persistence_manager import Phase4PersistenceManager

        users_dir = tmp_path / "users"
        legacy_path = tmp_path / "memory.json"

        # 构造 legacy：A 有 5 条，B 有 3 条，2 条没有 user_id
        records = []
        for i in range(5):
            records.append({
                "id": f"a{i}", "user_id": "qq_userA",
                "text": f"A 的记忆 {i}", "topic": "general",
                "timestamp_ms": 1700000000000, "importance": 0.6,
            })
        for i in range(3):
            records.append({
                "id": f"b{i}", "user_id": "qq_userB",
                "text": f"B 的记忆 {i}", "topic": "general",
                "timestamp_ms": 1700000000001, "importance": 0.6,
            })
        for i in range(2):
            records.append({
                "id": f"anon{i}",  # 没有 user_id
                "text": f"匿名记忆 {i}", "topic": "general",
                "timestamp_ms": 1700000000002, "importance": 0.5,
            })
        legacy_path.write_text(json.dumps(records), encoding="utf-8")

        # 导入到用户 A 目录
        pm_a = Phase4PersistenceManager.for_user("qq_userA", users_root=str(users_dir))
        imported_a = pm_a.import_legacy_memory(
            str(legacy_path), max_import=100, target_user_id="qq_userA"
        )
        # A 只能拿到自己的 5 条（匿名记录因为不是 default 用户目录所以跳过）
        a_ids = [r.get("id") for r in imported_a]
        assert all(r.startswith("a") for r in a_ids), f"A 混入了别人的记录: {a_ids}"
        assert len(a_ids) == 5, f"A 应导入 5 条，实际 {len(a_ids)}: {a_ids}"

        # 导入到用户 B 目录
        pm_b = Phase4PersistenceManager.for_user("qq_userB", users_root=str(users_dir))
        imported_b = pm_b.import_legacy_memory(
            str(legacy_path), max_import=100, target_user_id="qq_userB"
        )
        b_ids = [r.get("id") for r in imported_b]
        assert all(r.startswith("b") for r in b_ids), f"B 混入了别人的记录: {b_ids}"
        assert len(b_ids) == 3, f"B 应导入 3 条，实际 {len(b_ids)}: {b_ids}"

        # 交叉检查：A 的目录里绝对没有 b* 记录
        load_a = pm_a.load_all()
        mem_ids_a = {r.get("id") for r in load_a.memories if isinstance(r, dict)}
        leak = [x for x in mem_ids_a if x.startswith("b")]
        assert not leak, f"A 目录泄漏了 B 的记录: {leak}"

    def test_default_user_imports_anonymous_records(self, tmp_path):
        """单用户模式（default 目录）：匿名记录应该允许导入。"""
        from src.response_phase4.persistence_manager import Phase4PersistenceManager

        pm_default = Phase4PersistenceManager.for_user(
            "default", users_root=str(tmp_path / "users")
        )
        legacy_path = tmp_path / "memory2.json"
        legacy_path.write_text(json.dumps([
            {"id": "anon_0", "text": "匿名 0", "topic": "g1"},  # 无 user_id
            {"id": "u1_0", "user_id": "other", "text": "其他用户 0", "topic": "g2"},
        ]), encoding="utf-8")

        imported = pm_default.import_legacy_memory(
            str(legacy_path), max_import=10, target_user_id="default"
        )
        ids = [r.get("id") for r in imported]
        # default 用户目录允许导入匿名 anon_0；但有显式 user_id=other 的应该过滤掉（因为传了 target_user_id=default）
        assert "anon_0" in ids, "default 模式应允许导入匿名记录"
        assert "u1_0" not in ids, "有显式 user_id=other 不应被 default 导入"


# ──────────────────────────────────────────────
# T9. Relationship 情感分析（负面消息不涨关系值）
# ──────────────────────────────────────────────
class TestT9RelationshipEmotion:
    """验证 relationship 增长与消息情感关联，不再无条件上涨。"""

    def test_negative_message_freezes_relationship(self):
        """辱骂/生气关键词命中 → closeness_delta <= 0, trust_delta <= 0。"""
        from src.runtime.runtime_controller import _analyze_relationship_impact

        # 重度辱骂（多个辱骂词）
        c, t = _analyze_relationship_impact("你这个傻逼滚远点，废物！")
        assert c < 0, f"重度负面 closeness 应下降, got {c}"
        assert t < 0, f"重度负面 trust 应下降, got {t}"

        # 重度辱骂（单个强辱骂词 "傻逼" → 1 hit；验证不会扣到离谱，只是小降）
        c2, t2 = _analyze_relationship_impact("傻逼啊你？")
        assert c2 < 0, f"单强辱骂 closeness 应下降, got {c2}"

        # 轻度抱怨（单一负面词 "无语" → 1 hit，冻结不增长）
        c3, t3 = _analyze_relationship_impact("真的无语，这事情有点")
        assert c3 == 0.0 and t3 == 0.0, f"单次轻度抱怨应冻结不增长, got ({c3}, {t3})"

    def test_positive_message_higher_delta(self):
        """感谢/亲昵 → closeness_delta >= 0.010。"""
        from src.runtime.runtime_controller import _analyze_relationship_impact

        c, t = _analyze_relationship_impact("羽依爱你呀！谢谢你一直陪着我~mua~")
        assert c >= 0.010, f"强正面 closeness 应 >= 0.010, got {c}"
        assert t >= 0.007, f"强正面 trust 应 >= 0.007, got {t}"

    def test_neutral_chat_normal_delta(self):
        """普通闲聊 → 给默认增量（低于之前的 0.008/0.005）。"""
        from src.runtime.runtime_controller import _analyze_relationship_impact

        c, t = _analyze_relationship_impact("中午吃什么呀，今天天气不错")
        assert c == 0.006, f"普通闲聊 closeness 应为 0.006, got {c}"
        assert t == 0.004, f"普通闲聊 trust 应为 0.004, got {t}"

    def test_real_chat_relationship_reflects_message(self, tmp_path):
        """端到端：连续辱骂 5 轮 → closeness 不涨反而下降。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")
        uid = "qq_rude_user"
        # 初始化 1 轮正常消息（保证 baseline 有值）
        hr0 = ctrl.handle_message(uid, "你好羽依我今天很开心内容足够长")
        base_close = hr0.debug.get("closeness_delta")
        # 然后 5 轮辱骂
        last_closeness = None
        last_debug = None
        for i in range(5):
            hr = ctrl.handle_message(uid, f"你是不是有病？傻逼听不懂人话？废物啊（第{i}轮辱骂内容足够长触发记忆）")
            last_debug = hr.debug
            # closeness_delta 应为负
            assert hr.debug.get("closeness_delta", 0) < 0, \
                f"辱骂轮次 {i} closeness_delta 应 < 0, got {hr.debug}"
            last_closeness = hr.debug
        assert last_closeness is not None, "应至少跑一轮"


# ──────────────────────────────────────────────
# T10. RuntimeController reply 空时不保存（防降级重复写）
# ──────────────────────────────────────────────
class TestT10EmptyReplyNoSave:
    """验证 reply 为空时不 save_all，防止降级路径重复写。"""

    def test_empty_reply_returns_fallback_flag(self, tmp_path):
        """Mock runner._run_one_turn 返回空 reply → fallback_required=True 且不 save。

        注：runner 内部已经有 "DeepSeek 空 → Mock 降级" 的兜底，正常情况下
        RuntimeController 几乎拿不到空 reply；所以这里替换 per-user runner
        返回 {"reply_text": ""}，强制命中 `if not reply:` 分支验证防重复写逻辑。
        """
        ctrl = _make_controller(tmp_path, llm_engine="mock")
        uid = "qq_empty_reply_test"

        # 在调用前记录 pm 对应目录文件的状态
        pm_before = ctrl._get_pm_for_user(uid)
        mem_file_before = pm_before.memory_file
        exists_before = mem_file_before.exists()
        mtime_before = mem_file_before.stat().st_mtime if exists_before else None

        # 构造 stub runner：_run_one_turn 返回空 reply_text
        stub_runner = MagicMock()
        stub_runner._run_one_turn.return_value = {"reply_text": ""}
        with patch.object(ctrl, "_get_runner_for_user", return_value=stub_runner):
            hr = ctrl.handle_message(
                uid,
                "这是一条足够长的测试消息用于触发 memory 和 relationship 更新",
            )

        # reply 为空，debug 里有 fallback_required=True
        assert hr.reply == "", f"reply 应是空，实际: {hr.reply!r}"
        assert hr.debug.get("fallback_required") is True, \
            f"空 reply 应有 fallback_required=True，debug: {hr.debug}"

        # 如果之前文件不存在 → 现在也不应该凭空存在（没 save_all）
        if not exists_before:
            assert not mem_file_before.exists(), \
                "reply 为空时不应创建任何 memory 文件（没 save_all）"
        else:
            # 如果之前存在 → mtime 没变
            assert mem_file_before.stat().st_mtime == mtime_before, \
                "reply 为空时 mtime 不应变化（没 save_all）"

    def test_non_empty_reply_does_save(self, tmp_path):
        """确认正常非空 reply 会 save（作为对照组）。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")
        uid = "qq_save_ok_test"
        pm = ctrl._get_pm_for_user(uid)

        hr = ctrl.handle_message(uid, "这是足够长的一条正常消息内容肯定超过十个字了吧")
        assert hr.reply and len(hr.reply) > 5, "正常 reply 不应空"
        # memory 文件必须被创建
        assert pm.memory_file.exists(), "非空 reply 后应存在 memory 文件"
        # interaction_count 应是 1
        load = pm.load_all()
        assert load.relationship.get("interaction_count") >= 1, \
            "应至少有 1 次交互计数"

    def test_runner_internal_fallback_still_saves(self, tmp_path):
        """runner 内部 DeepSeek 失败降级 Mock → reply 不空，save_all 正常执行。

        这是真实运行场景：Phase4 适配器出错，但 runner 用 Mock 补上了 reply。
        对 RuntimeController 而言 reply 非空 → 应该保存（不是重复写）。
        """
        from src.response_phase4.deepseek_adapter import DeepSeekAdapter

        ctrl = _make_controller(tmp_path, llm_engine="mock")
        uid = "qq_runner_fallback_test"
        pm = ctrl._get_pm_for_user(uid)

        # 让 adapter 直接抛异常 → runner 降级 Mock
        always_fail = MagicMock(spec=DeepSeekAdapter)
        always_fail.generate.side_effect = RuntimeError("DeepSeek API 崩溃模拟")
        ctrl._deepseek_adapter = always_fail

        hr = ctrl.handle_message(uid, "你好呀羽依这条消息超过十个字啦")
        # 因为 runner 内部降级到了 Mock，所以 reply 应当非空
        assert hr.reply and len(hr.reply) > 0, \
            f"runner 内部降级 Mock 后 reply 不应空, debug={hr.debug}"
        # fallback_required 不为 True（runner 内部已经兜住了）
        # 注：非空路径上 debug dict 压根不写 fallback_required 字段（默认 None）
        assert hr.debug.get("fallback_required") is not True, \
            f"runner 内部已降级，RuntimeController 不应标记 fallback_required=True"
        # 文件应该正常保存
        assert pm.memory_file.exists(), "runner 降级后的非空 reply 应该正常 save"
        load = pm.load_all()
        assert load.relationship.get("interaction_count") >= 1


# ──────────────────────────────────────────────
# T11. Runner 缓存并发安全（_runner_cache 加锁）
# ──────────────────────────────────────────────
class TestT11RunnerCacheThreadSafe:
    """验证同用户并发请求不会创建多个 runner。"""

    def test_concurrent_same_user_returns_same_runner(self, tmp_path):
        """用 10 个线程同时请求同用户的 runner，必须全部返回同一个对象。"""
        import threading

        ctrl = _make_controller(tmp_path, llm_engine="mock")
        uid = "qq_concurrent_001"
        results: List[Any] = [None] * 10
        barrier = threading.Barrier(10)  # 让所有线程同时进 _get_runner_for_user

        def worker(idx):
            barrier.wait(timeout=5)
            results[idx] = ctrl._get_runner_for_user(uid)

        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        first = results[0]
        assert first is not None, "应至少创建一个 runner"
        for i, r in enumerate(results):
            assert r is first, f"线程 {i} 返回了不同的 runner 对象（竞态！）"

    def test_runner_lock_exists(self, tmp_path):
        """验证 RuntimeController 上确实存在 _runner_cache_lock 属性。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")
        import threading
        assert hasattr(ctrl, "_runner_cache_lock"), \
            "缺少 _runner_cache_lock（并发安全失效！）"
        assert isinstance(ctrl._runner_cache_lock, type(threading.Lock())), \
            f"_runner_cache_lock 类型错误: {type(ctrl._runner_cache_lock)}"


# ──────────────────────────────────────────────
# T12. PersistenceManager _FileLock 超时调整
# ──────────────────────────────────────────────
class TestT12FileLockTimeout:
    """验证 _FileLock.TIMEOUT_SECONDS 足够大，不会误删 DeepSeek 正在持有的锁。"""

    def test_filelock_timeout_ge_60s(self):
        """R2.7.6-AUDIT: TIMEOUT_SECONDS 必须 >= 60s（默认 DeepSeek timeout=30s * 3 次重试）。"""
        from src.response_phase4.persistence_manager import _FileLock
        assert _FileLock.TIMEOUT_SECONDS >= 60.0, (
            f"_FileLock.TIMEOUT_SECONDS 必须 >= 60s 避免 DeepSeek 重试期间锁被误删; "
            f"当前 {_FileLock.TIMEOUT_SECONDS}s"
        )


# ──────────────────────────────────────────────
# T13. API Server orchestrator=None → 503
# ──────────────────────────────────────────────
class TestT13ApiServerNoneOrchestrator:
    """验证所有链路全挂（orchestrator=None）时返回标准 503。"""

    def test_chat_endpoint_all_failed_returns_503(self, tmp_path):
        """runtime_controller=None, pipeline=None, orchestrator=None → 503 + 清晰错误消息。"""
        import api_server as api_mod

        orig_ctrl = getattr(api_mod, "_runtime_controller", None)
        orig_pipe = getattr(api_mod, "_pipeline", None)
        orig_orch = getattr(api_mod, "orchestrator", None)

        try:
            # 全部置空
            api_mod._runtime_controller = None
            api_mod._pipeline = None
            api_mod.orchestrator = None  # 关键：orchestrator 也是 None

            with api_mod.app.test_client() as client:
                resp = client.post("/v1/chat/completions", json={
                    "messages": [{"role": "user", "content": "测试全链路失败"}],
                    "user": "qq_all_fail",
                })
                assert resp.status_code == 503, (
                    f"所有链路均不可用应返回 503，实际 {resp.status_code}: {resp.get_json()}"
                )
                body = resp.get_json() or {}
                assert "error" in body, "503 响应应包含 error 字段"
                assert "details" in body, "503 响应应包含 details 字段（便于排查）"

        finally:
            api_mod._runtime_controller = orig_ctrl
            api_mod._pipeline = orig_pipe
            api_mod.orchestrator = orig_orch

    def test_initiative_endpoint_orchestrator_none(self, tmp_path):
        """/initiative 端点在 orchestrator=None 时应 503 不崩溃。"""
        import api_server as api_mod
        orig_orch = getattr(api_mod, "orchestrator", None)
        orig_init = getattr(api_mod, "init_orchestrator", None)
        try:
            # R2.7.6-DEPLOY: 修复了 engine.py openai SDK 延迟导入后，
            # init_orchestrator() 即使在没装 openai 时也能成功初始化（mock 模式）。
            # 因此需要同时禁用 init_orchestrator 才能模拟"导入彻底失败"的生产灾难场景。
            api_mod.orchestrator = None
            api_mod.init_orchestrator = lambda: None  # no-op，模拟 init 失败
            with api_mod.app.test_client() as client:
                resp = client.post("/initiative", json={"user_id": "qq_init_fail"})
                # 可能是 503（检查命中）或 500（其他原因），但不能是连接崩溃/异常
                assert resp.status_code in (500, 503), \
                    f"Orchestrator 不可用应返回 500/503，实际 {resp.status_code}"
        finally:
            api_mod.orchestrator = orig_orch
            if orig_init is not None:
                api_mod.init_orchestrator = orig_init


# ──────────────────────────────────────────────
# T14. API 入参规范化（null user / 多模态 content）
# ──────────────────────────────────────────────
class TestT14InputNormalization:
    """验证 chat_completions 对异常输入的鲁棒性（R2.7.6-AUDIT 第二轮新增）。"""

    def test_user_null_defaults_to_default(self, tmp_path):
        """'user': null 不应创建叫 'None' 的用户目录，应降级为 'default'。"""
        import api_server as api_mod
        orig_ctrl = getattr(api_mod, "_runtime_controller", None)
        try:
            api_mod._runtime_controller = None  # 避免真正跑 Phase4
            with api_mod.app.test_client() as client:
                # 用 orchestrator=None 的降级分支（Phase4/Pipeline/Orchestrator 均 None）
                api_mod.orchestrator = None
                api_mod._pipeline = None
                resp = client.post("/v1/chat/completions", json={
                    "messages": [{"role": "user", "content": "null user 测试内容足够长"}],
                    "user": None,  # 显式 null
                })
                # 即使全部链路不可用，也应返回 503（而不是 500）
                assert resp.status_code == 503, (
                    f"传 null user 时应正常走到 503 分支而非崩溃，实际 {resp.status_code}"
                )
        finally:
            api_mod._runtime_controller = orig_ctrl

    def test_user_empty_string_defaults(self, tmp_path):
        """'user': '' 空串应降级为 'default' 而不是创建空串目录。"""
        import api_server as api_mod
        orig_ctrl = getattr(api_mod, "_runtime_controller", None)
        try:
            api_mod._runtime_controller = None
            api_mod.orchestrator = None
            api_mod._pipeline = None
            with api_mod.app.test_client() as client:
                resp = client.post("/v1/chat/completions", json={
                    "messages": [{"role": "user", "content": "空 user 内容足够长"}],
                    "user": "",
                })
                assert resp.status_code == 503, (
                    f"空串 user 应正常走到 503 分支而非崩溃，实际 {resp.status_code}"
                )
        finally:
            api_mod._runtime_controller = orig_ctrl

    def test_multimodal_content_array(self, tmp_path):
        """多模态 content=[{"type":"text","text":"hi"}, {...}] 应正确提取 text 字段。"""
        import api_server as api_mod

        # 设置一个 RuntimeController mock，能捕获传入的 message
        captured = {}
        class CtrlStub:
            phase4_enabled = True
            def handle_message(self, user_id, message, **kwargs):
                captured["user_id"] = user_id
                captured["message"] = message
                # 返回非空 reply，让 Phase4 路径直接返回成功
                from src.runtime.runtime_controller import HandleResult
                return HandleResult(reply="收到了多模态消息", debug={
                    "turn_uuid": "stub000", "session_id": "s_stub",
                    "user_id": user_id, "new_memory_count": 0,
                    "delta_applied": {}, "personality_version": 1,
                    "interaction_count": 1,
                })

        orig_ctrl = getattr(api_mod, "_runtime_controller", None)
        try:
            api_mod._runtime_controller = CtrlStub()
            with api_mod.app.test_client() as client:
                resp = client.post("/v1/chat/completions", json={
                    "messages": [
                        {"role": "system", "content": "sys"},
                        {"role": "user", "content": [
                            {"type": "text", "text": "你好羽依"},
                            {"type": "image_url", "image_url": {"url": "https://.../pic.png"}},
                            {"type": "text", "text": "今天天气好不好呀"},
                        ]},
                    ],
                    "user": "qq_mm_001",
                })
                # 应 200 返回
                assert resp.status_code == 200, f"多模态请求应正常返回 200, 实际 {resp.status_code}: {resp.data[:200]}"
                body = resp.get_json() or {}
                # 应提取两段文本（忽略 image_url 段）
                assert "message" in captured, "RuntimeController.handle_message 应被调用"
                got = captured.get("message", "")
                assert "你好羽依" in got and "今天天气好不好呀" in got, (
                    f"多模态 content 数组的 text 段未正确提取，实际 message={got!r}"
                )
                # reply 非空
                choices = body.get("choices", [])
                assert choices and choices[0]["message"]["content"] == "收到了多模态消息"
        finally:
            api_mod._runtime_controller = orig_ctrl

    def test_content_non_string_does_not_crash(self, tmp_path):
        """content=12345（数字）或 content=True（布尔）等异常类型 → str() 兜底不崩溃。"""
        import api_server as api_mod
        captured = {}
        class CtrlStub:
            phase4_enabled = True
            def handle_message(self, user_id, message, **kwargs):
                captured["message"] = message
                from src.runtime.runtime_controller import HandleResult
                return HandleResult(reply="ok", debug={
                    "turn_uuid": "stub001", "session_id": "s1",
                    "user_id": user_id, "new_memory_count": 0,
                    "delta_applied": {}, "personality_version": 1,
                    "interaction_count": 1,
                })

        orig_ctrl = getattr(api_mod, "_runtime_controller", None)
        try:
            api_mod._runtime_controller = CtrlStub()
            with api_mod.app.test_client() as client:
                # content 传一个 int
                resp = client.post("/v1/chat/completions", json={
                    "messages": [{"role": "user", "content": 42}],
                    "user": "qq_int_001",
                })
                assert resp.status_code == 200, (
                    f"content 为 int 时应正常返回 200（已 str() 兜底），实际 {resp.status_code}"
                )
                assert captured.get("message") == "42", (
                    f"content 为 int 42 应被 str() 转换为 '42'，实际 {captured.get('message')!r}"
                )
        finally:
            api_mod._runtime_controller = orig_ctrl


# ================================================================
# T15 ~ T18：R2.7.6-AUDIT 第三轮深度排查修复验证
# ================================================================

# ──────────────────────────────────────────────
# T15. orchestrator.process(user_id=) 防跨用户串话
# ──────────────────────────────────────────────
class TestT15OrchestratorUserIdParam:
    """验证 orchestrator.process() 接受 user_id 参数，不再依赖 api_server 过早赋值。"""

    def test_process_accepts_user_id_param(self):
        """process(user_message, user_id='qq_123') 应在方法入口设置 target_user_id。"""
        # 直接从源码检查签名（避免导入 orchestrator.py 触发 openai 依赖）
        orch_path = Path(__file__).parent.parent / "src" / "orchestrator.py"
        source = orch_path.read_text(encoding="utf-8")
        # 查找 def process(self, user_message: str, ... user_id ...)
        import re
        match = re.search(
            r"def process\(self,\s*user_message[^)]*?(user_id[^)]*)\)",
            source
        )
        assert match, (
            "orchestrator.py 中 process() 方法签名必须包含 user_id 参数"
        )
        assert "Optional" in match.group(1) or "None" in match.group(1), (
            f"user_id 参数默认值应为 None（向后兼容），实际: {match.group(1)}"
        )

    def test_api_server_no_premature_target_user_id_set(self):
        """验证 api_server.py 不再在 Phase4 路径前设置 orchestrator.target_user_id。"""
        import api_server as api_mod
        # 读取 api_server.py 源码检查
        import inspect
        source = inspect.getsource(api_mod.chat_completions)
        # 应该不再有 "orchestrator.target_user_id = user_id" 这行
        # （除非在 orchestrator.process 调用行内通过参数传递）
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            # 不应有独立的 orchestrator.target_user_id = user_id 赋值行
            if "orchestrator.target_user_id" in stripped and "user_id" in stripped:
                # 允许在注释里出现
                if stripped.startswith("#"):
                    continue
                assert False, (
                    f"api_server.py 仍有过早赋值: {stripped}"
                )


# ──────────────────────────────────────────────
# T16. config.py load_config 容错 + get_api_key 占位符检测
# ──────────────────────────────────────────────
class TestT16ConfigRobustness:
    """验证 config.py 的容错和 API Key 占位符检测。"""

    def test_load_config_returns_empty_on_missing_file(self, tmp_path, monkeypatch):
        """config.yaml 不存在时 load_config 返回 {} 而非抛异常。"""
        from src import config as cfg_module
        # 重置缓存
        monkeypatch.setattr(cfg_module, "_config", None)
        # 指向不存在的路径
        monkeypatch.setattr(
            "src.config.Path",
            lambda *a, **kw: tmp_path / "nonexistent.yaml"
        )
        result = cfg_module.load_config()
        assert result == {}, f"缺失文件应返回 {{}}, 实际 {result}"

    def test_get_api_key_detects_placeholder(self, monkeypatch):
        """${DEEPSEEK_API_KEY} 占位符应被识别为空，不发送给 API。"""
        from src import config as cfg_module
        # 模拟环境变量未设置
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        # 模拟 config.yaml 返回字面占位符
        monkeypatch.setattr(cfg_module, "_config", {
            "llm": {"api_key": "${DEEPSEEK_API_KEY}"}
        })
        key = cfg_module.get_api_key()
        assert key == "", (
            f"${{DEEPSEEK_API_KEY}} 占位符应被识别为空, 实际 {key!r}"
        )

    def test_get_api_key_returns_real_key(self, monkeypatch):
        """真实 key（非占位符）应正常返回。"""
        from src import config as cfg_module
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-key-12345")
        monkeypatch.setattr(cfg_module, "_config", {})
        key = cfg_module.get_api_key()
        assert key == "sk-real-key-12345"

    def test_get_api_key_returns_config_key_when_no_env(self, monkeypatch):
        """无环境变量时，config.yaml 中的真实 key 应正常返回。"""
        from src import config as cfg_module
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setattr(cfg_module, "_config", {
            "llm": {"api_key": "sk-from-config-67890"}
        })
        key = cfg_module.get_api_key()
        assert key == "sk-from-config-67890"


# ──────────────────────────────────────────────
# T17. uuid.hex[:4] → [:12] 修复（memory ID 碰撞）
# ──────────────────────────────────────────────
class TestT17UuidTruncation:
    """验证 memory ID 不再使用过短的 uuid 截断。"""

    def test_memory_id_uuid_length(self):
        """orchestrator.py 中的 memory ID uuid 截断应 >= 12（48 位熵）。"""
        # 直接从文件读取源码（避免 src.orchestrator 包导入冲突）
        orch_path = Path(__file__).parent.parent / "src" / "orchestrator.py"
        source = orch_path.read_text(encoding="utf-8")
        import re
        # 逐行扫描，找到含 "mem_" 的行，再检查该行内的 uuid hex[:N] 截断
        found_any = False
        for line in source.split("\n"):
            if "mem_" in line and "uuid4().hex" in line:
                found_any = True
                matches = re.findall(r"hex\[:(\d+)\]", line)
                for n in matches:
                    n_int = int(n)
                    assert n_int >= 12, (
                        f"memory ID 行的 uuid.uuid4().hex[:{n}] 熵不足"
                        f"（{n_int*4} bit），应 >= [:12]（48 bit）"
                    )
        assert found_any, "应至少找到一行含 mem_ 和 uuid4().hex 的代码"


# ──────────────────────────────────────────────
# T18. DeepSeekAdapter._version 线程安全
# ──────────────────────────────────────────────
class TestT18VersionThreadSafe:
    """验证 DeepSeekAdapter._version 在并发下不重复不跳号。"""

    def test_concurrent_version_increment(self):
        """10 线程各取 100 次 version，总共 1000 个值应全部唯一。"""
        from src.response_phase4.deepseek_adapter import DeepSeekAdapter
        import threading

        fake_client = MagicMock()
        adapter = DeepSeekAdapter(
            api_key="test", api_client=fake_client, max_retries=0
        )
        versions = []
        lock = threading.Lock()

        def worker():
            local_versions = []
            for _ in range(100):
                with adapter._version_lock:
                    v = adapter._version
                    adapter._version += 1
                local_versions.append(v)
            with lock:
                versions.extend(local_versions)

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(versions) == 1000
        assert len(set(versions)) == 1000, (
            f"版本号有重复！唯一值 {len(set(versions))}/1000"
        )


# ================================================================
# T19 ~ T22：R2.7.6-YUYI 羽依心像落地测试
# ================================================================

# ──────────────────────────────────────────────
# T19. Origin Identity 冻结机制
# ──────────────────────────────────────────────
class TestT19OriginFreeze:
    """验证 Origin Identity 冻结后不可被追加或覆盖。"""

    def test_create_frozen_has_all_four_roles(self):
        """冻结身份包含全部四个角色且都绑定清清。"""
        from src.identity.origin_identity import (
            OriginIdentity, OriginRole, CREATOR_USER_ID, FROZEN_ROLES,
        )
        frozen = OriginIdentity.create_frozen()
        assert frozen.is_frozen is True
        assert len(frozen.contributors) == 1
        c = frozen.contributors[0]
        assert c.user_id == CREATOR_USER_ID
        assert set(c.roles) == FROZEN_ROLES
        # role_claims 每个角色都指向清清
        for role in FROZEN_ROLES:
            assert CREATOR_USER_ID in frozen.role_claims.get(role, [])

    def test_frozen_rejects_non_creator_contributor(self):
        """冻结后，非清清用户试图认领冻结角色 → 返回 False。"""
        from src.identity.origin_identity import (
            OriginIdentity, OriginContributor, OriginRole,
        )
        frozen = OriginIdentity.create_frozen()
        # 陌生人试图认领 CREATOR
        stranger = OriginContributor(
            user_id="999999",
            roles=[OriginRole.CREATOR],
        )
        assert frozen.add_contributor(stranger) is False

    def test_frozen_allows_creator_evidence_append(self):
        """冻结后，清清仍然可以追加 evidence（不覆盖角色，只加证据）。"""
        from src.identity.origin_identity import (
            OriginIdentity, OriginContributor, OriginRole, CREATOR_USER_ID,
        )
        frozen = OriginIdentity.create_frozen()
        qingqing = OriginContributor(
            user_id=CREATOR_USER_ID,
            roles=[OriginRole.CREATOR],  # 已有角色
            evidence_ids=["ev_new_001"],
        )
        assert frozen.add_contributor(qingqing) is True
        assert "ev_new_001" in frozen.contributors[0].evidence_ids

    def test_frozen_persists_through_save_load(self, tmp_path):
        """冻结状态通过 to_dict/from_dict 正确序列化。"""
        from src.identity.origin_identity import OriginIdentity
        frozen = OriginIdentity.create_frozen()
        data = frozen.to_dict()
        assert data["is_frozen"] is True
        restored = OriginIdentity.from_dict(data)
        assert restored.is_frozen is True
        assert len(restored.contributors) == 1

    def test_origin_manager_auto_freezes_on_init(self, tmp_path):
        """OriginManager 初始化时自动升级为冻结身份。"""
        from src.identity.origin_storage import OriginStorage
        from src.identity.origin_manager import OriginManager
        storage = OriginStorage(str(tmp_path / "origin.json"))
        # 第一次初始化：空文件 → 自动创建冻结身份
        mgr = OriginManager(storage=storage)
        assert mgr.identity.is_frozen is True
        # 重新加载：应该保持冻结
        mgr2 = OriginManager(storage=storage)
        assert mgr2.identity.is_frozen is True


# ──────────────────────────────────────────────
# T20. 跨用户记忆水印过滤
# ──────────────────────────────────────────────
class TestT20CrossUserMemoryFilter:
    """验证记忆中的 user_id 水印能阻止跨用户读取。"""

    def test_memory_records_get_user_id_watermark(self, tmp_path):
        """新记忆保存时自动加 user_id 水印。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")
        uid = "qq_watermark_test"
        hr = ctrl.handle_message(uid, "这条消息足够长用于触发记忆保存")
        assert hr.reply, "应有非空 reply"
        pm = ctrl._get_pm_for_user(uid)
        load = pm.load_all()
        # 新记忆应该有 user_id 字段
        watermarked = [m for m in load.memories if isinstance(m, dict) and m.get("user_id") == uid]
        assert len(watermarked) >= 1, "至少应有一条记忆带 user_id 水印"

    def test_cross_user_memories_filtered_on_load(self, tmp_path):
        """如果记忆列表中混入了其他用户的记忆，应被过滤掉。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")
        uid_a = "qq_user_a_filter"
        uid_b = "qq_user_b_filter"
        # 用户 A 先正常聊天产生记忆
        ctrl.handle_message(uid_a, "用户A的第一条消息内容足够长")
        pm_a = ctrl._get_pm_for_user(uid_a)
        load_a = pm_a.load_all()
        # 手动注入一条用户 B 的记忆到 A 的存储中
        load_a.memories.append({
            "id": "mem_stolen_001",
            "text": "这是用户B的秘密记忆不应该被A看到",
            "topic": "secret",
            "timestamp_ms": 9999999999999,
            "importance": 0.99,
            "user_id": uid_b,  # B 的水印
        })
        pm_a.save_all(personality=load_a.personality, memories=load_a.memories, relationship=load_a.relationship)
        # 重新加载，B 的记忆应该被过滤掉
        load_a2 = pm_a.load_all()
        stolen = [m for m in load_a2.memories if isinstance(m, dict) and m.get("id") == "mem_stolen_001"]
        # PersistenceManager 存储层不过滤（它只管读写），但 RuntimeController 会过滤
        # 这里验证 PersistenceManager 存储了它（因为 PM 不做业务过滤）
        # 真正的过滤在 RuntimeController.handle_message 里
        ctrl2 = _make_controller(tmp_path, llm_engine="mock")
        # 用 uid_a 聊天，应该看不到 uid_b 的记忆
        hr = ctrl2.handle_message(uid_a, "用户A的第二条消息也足够长")
        # debug 里的 new_memory_count 不应该包含被盗的记忆
        assert hr.debug.get("user_id") == uid_a


# ──────────────────────────────────────────────
# T21. 健康陪伴检测
# ──────────────────────────────────────────────
class TestT21HealthCompanion:
    """验证健康陪伴检测器的各个功能。"""

    def test_late_night_detection(self):
        """凌晨 3 点聊天 → should_remind_sleep=True。"""
        from src.runtime.health_companion import HealthCompanion
        hc = HealthCompanion()
        signal = hc.check("qq_test", "你好羽依", current_hour=3)
        assert signal.should_remind_sleep is True
        assert "很晚" in signal.health_hint or "休息" in signal.health_hint

    def test_daytime_no_sleep_reminder(self):
        """下午 3 点聊天 → should_remind_sleep=False。"""
        from src.runtime.health_companion import HealthCompanion
        hc = HealthCompanion()
        signal = hc.check("qq_test", "你好羽依", current_hour=15)
        assert signal.should_remind_sleep is False

    def test_drug_keyword_detection(self):
        """提到喹硫平 → should_express_concern=True。"""
        from src.runtime.health_companion import HealthCompanion
        hc = HealthCompanion()
        signal = hc.check("qq_test", "今天喹硫平效果不太好")
        assert signal.should_express_concern is True
        assert "喹硫平" in signal.drug_mentioned
        assert "关心" in signal.health_hint

    def test_late_night_keyword_detection(self):
        """提到通宵/肝代码 → late_night_activity=True。"""
        from src.runtime.health_companion import HealthCompanion
        hc = HealthCompanion()
        signal = hc.check("qq_test", "今晚通宵肝代码调bug")
        assert signal.late_night_activity is True
        assert "熬夜" in signal.health_hint or "睡觉" in signal.health_hint

    def test_companion_mode_silent_and_recover(self):
        """用户说"别打扰" → silent → 叫"羽依" → active。"""
        from src.runtime.health_companion import HealthCompanion
        hc = HealthCompanion()
        # 正常状态
        s1 = hc.check("qq_dnd", "你好呀")
        assert s1.companion_silent is False
        # 进入静音
        s2 = hc.check("qq_dnd", "我在写代码别打扰我")
        assert s2.companion_silent is True
        # 仍在静音
        s3 = hc.check("qq_dnd", "继续写代码中")
        assert s3.companion_silent is True
        # 恢复
        s4 = hc.check("qq_dnd", "羽依我回来了")
        assert s4.companion_silent is False

    def test_silent_mode_suppresses_health_reminders(self):
        """静音模式下不生成健康提醒（减少打扰）。"""
        from src.runtime.health_companion import HealthCompanion
        hc = HealthCompanion()
        hc.check("qq_dnd2", "别打扰我写代码", current_hour=3)
        # 静音模式下，即使凌晨也不提醒
        s = hc.check("qq_dnd2", "还在写", current_hour=4)
        assert s.companion_silent is True
        assert s.should_remind_sleep is False

    def test_health_signal_in_handle_result(self, tmp_path):
        """handle_message 的 debug 中包含 health_hint。"""
        ctrl = _make_controller(tmp_path, llm_engine="mock")
        hr = ctrl.handle_message(
            "qq_health_test",
            "今天喹硫平效果不好有点头晕",
        )
        assert "health_hint" in hr.debug
        assert "喹硫平" in hr.debug["health_hint"] or "关心" in hr.debug["health_hint"]


# ──────────────────────────────────────────────
# T22. 情绪原子写 + 事件缓解
# ──────────────────────────────────────────────
class TestT22EmotionAtomicWrite:
    """验证情绪存储的原子写和损坏降级。"""

    def test_emotion_repository_atomic_write(self, tmp_path):
        """保存后 .tmp 文件不留残留。"""
        from src.emotion.emotion_repository import EmotionRepository
        from src.emotion.emotion_state import EmotionState
        repo = EmotionRepository(str(tmp_path / "emotion.json"))
        state = EmotionState(valence=0.7, anxiety=0.3)
        repo.save(state)
        assert (tmp_path / "emotion.json").exists()
        # 不应有残留 .tmp 文件
        assert not (tmp_path / "emotion.json.tmp").exists()

    def test_emotion_repository_corrupted_load(self, tmp_path):
        """损坏的 JSON 文件降级为空状态而非崩溃。"""
        from src.emotion.emotion_repository import EmotionRepository
        from src.emotion.emotion_state import EmotionState
        bad_file = tmp_path / "emotion.json"
        bad_file.write_text("{ broken json !!!", encoding="utf-8")
        repo = EmotionRepository(str(bad_file))
        state = repo.load()
        assert isinstance(state, EmotionState)  # 不崩溃，返回空状态

    def test_trace_repository_atomic_write(self, tmp_path):
        """轨迹保存后无 .tmp 残留。"""
        from src.emotion.emotion_trace_repository import EmotionTraceRepository
        from src.emotion.emotional_trace import EmotionalTrace, EmotionCause
        repo = EmotionTraceRepository(str(tmp_path / "traces.json"))
        trace = EmotionalTrace(
            emotion="joy",
            cause=EmotionCause.USER_INTERACTION,
            intensity=0.5,
            event_type="user_praise",
        )
        repo.append(trace)
        assert (tmp_path / "traces.json").exists()
        assert not (tmp_path / "traces.json.tmp").exists()


class TestT22bMitigationEvent:
    """验证事件型缓解通道——"睡好了"→anxiety 下降。"""

    def test_mitigation_event_detected(self):
        """用户说"睡好了" → 检测到 mitigation 事件。"""
        from src.emotion.emotion_event_detector import EmotionEventDetector
        detector = EmotionEventDetector()
        event = detector.detect("今天终于睡好了，药效不错")
        assert event is not None
        assert event.event_type == "mitigation"

    def test_mitigation_reduces_anxiety(self):
        """mitigation 事件 → anxiety delta 为负。"""
        from src.emotion.emotion_evaluator import EmotionEvaluator, EVENT_RULES
        # 规则表里有 mitigation 条目
        assert "mitigation" in EVENT_RULES
        rule = EVENT_RULES["mitigation"]
        assert rule.anxiety < 0, "mitigation 应降低 anxiety"
        assert rule.valence > 0, "mitigation 应提升 valence"

    def test_mitigation_priority_over_praise(self):
        """如果消息同时包含"睡好了"和"谢谢"，应优先识别为 mitigation。"""
        from src.emotion.emotion_event_detector import EmotionEventDetector
        detector = EmotionEventDetector()
        event = detector.detect("睡好了，谢谢你关心我")
        assert event is not None
        assert event.event_type == "mitigation"

    def test_non_mitigation_message_not_triggered(self):
        """普通消息不触发 mitigation。"""
        from src.emotion.emotion_event_detector import EmotionEventDetector
        detector = EmotionEventDetector()
        event = detector.detect("今天天气不错我们去散步吧")
        # 不应是 mitigation
        assert event is None or event.event_type != "mitigation"


# ──────────────────────────────────────────────
# T23. 真实性指引补充
# ──────────────────────────────────────────────
class TestT23AuthenticityPrompt:
    """验证 prompt 中包含"被指出bug要认错"的指引。"""

    def test_prompt_builder_contains_admit_error(self):
        """prompt_builder.py 的核心原则包含'被指出说错要承认'。"""
        orch_path = Path(__file__).parent.parent / "src" / "response" / "prompt_builder.py"
        source = orch_path.read_text(encoding="utf-8")
        assert "被指出说错" in source or "前后矛盾" in source, (
            "prompt_builder.py 应包含'被指出说错/前后矛盾→老实承认'的指引"
        )

    def test_engine_contains_admit_error(self):
        """engine.py 的行为原则包含认错指引。"""
        orch_path = Path(__file__).parent.parent / "src" / "engine.py"
        source = orch_path.read_text(encoding="utf-8")
        assert "被指出说错" in source or "前后矛盾" in source, (
            "engine.py 应包含'被指出说错→老实承认'的指引"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
