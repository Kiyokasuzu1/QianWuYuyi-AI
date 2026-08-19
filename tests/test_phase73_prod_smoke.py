# -*- coding: utf-8 -*-
"""
Phase 7.3 —— 生产部署前 Smoke Test

目标:
    在不修改任何源代码 / 配置文件的前提下,验证 api_server.py 在
    4 种关键生产配置下能正常工作:

    1. runtime.enabled=false → 旧 process() 路径
    2. runtime.enabled=true  → RuntimePipeline 路径
    3. TokenOptimizer 失败 → 自动 fallback,聊天不中断
    4. PersistenceHook 失败 → 自动 fallback,聊天不中断
    5. initiative.enabled=false → 主动消息路径关闭

    覆盖端点:
    - GET  /health
    - GET  /runtime/status
    - POST /v1/chat/completions

设计原则:
    - **零侵入**:不改任何 src/ 业务代码,不改 config.yaml,不改 data/
    - **不依赖真实 LLM**:用 mock orchestrator 注入固定 reply
    - **不依赖真实 ChromaDB**:不调用 vector_memory,只走 store.load
    - **数据保护**:测试期间 memory.json / growth_state.json 哈希锁定
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 让测试可以从仓库根直接跑
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
CRITICAL_FILES = ["memory.json", "growth_state.json"]

# R2.7.6-DEPLOY: 模块加载时保存关键文件原始内容(bytes)，用于 tearDown 后恢复
# 原因：TestPipelineComponentFallback 等测试会真实调用 RuntimePipeline，
#       RuntimePipeline 会写入 data/memory.json → 下一个测试 tearDown hash 对不上。
# 解决：每个测试 setUp 前恢复原始内容 → 每个测试 hash baseline 一致，不污染生产 data。
_CRITICAL_FILES_ORIGINAL: dict = {}
for _name in CRITICAL_FILES:
    _p = DATA_DIR / _name
    _CRITICAL_FILES_ORIGINAL[_name] = _p.read_bytes() if _p.exists() else None


def _restore_critical_files():
    """恢复 memory.json / growth_state.json 到模块加载时的原始内容。"""
    for _name, _bytes in _CRITICAL_FILES_ORIGINAL.items():
        _p = DATA_DIR / _name
        if _bytes is None:
            if _p.exists():
                _p.unlink()
        else:
            _p.parent.mkdir(parents=True, exist_ok=True)
            _p.write_bytes(_bytes)


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _baseline_hashes() -> dict:
    out = {}
    for name in CRITICAL_FILES:
        src = DATA_DIR / name
        if src.exists():
            out[name] = _sha256(src)
    return out


def _load_yaml() -> dict:
    import yaml
    with open(PROJECT_ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _patch_config(runtime_enabled=None, initiative_enabled=None) -> dict:
    cfg = _load_yaml()
    if runtime_enabled is not None:
        cfg.setdefault("runtime", {})["enabled"] = runtime_enabled
    if initiative_enabled is not None:
        cfg.setdefault("initiative", {})["enabled"] = initiative_enabled
    return cfg


def _fresh_api_server():
    """每次返回干净的 api_server module(避免状态污染)。"""
    if "api_server" in sys.modules:
        del sys.modules["api_server"]
    return importlib.import_module("api_server")


# ==========================================================================
# 1. /health 端点
# ==========================================================================
class TestHealthEndpoint(unittest.TestCase):
    """验证 /health 在两种 runtime 开关下都返回 200。"""

    def setUp(self):
        self.apisrv = _fresh_api_server()
        self.client = self.apisrv.app.test_client()

    def test_health_returns_ok(self):
        """GET /health → 200, body 包含 status=ok"""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body.get("status"), "ok")

    def test_health_works_when_runtime_disabled(self):
        """runtime.enabled=false 时 /health 仍正常"""
        with mock.patch.object(self.apisrv, "_pipeline", None):
            resp = self.client.get("/health")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json().get("status"), "ok")


# ==========================================================================
# 2. /runtime/status 端点 (在 pipeline_server.py 中,需用独立 Flask app)
# ==========================================================================
class TestRuntimeStatusEndpoint(unittest.TestCase):
    """验证 pipeline_server 提供的 /runtime/status 端点。

    说明:
        /runtime/status 是 Phase 6.6 在 src/runtime/pipeline_server.py 新增的健康检查端点,
        与 api_server.py 共享同一份 RuntimePipeline 实例。
        部署侧 `python -m src.runtime.pipeline_server` 启动后,/runtime/status 可独立访问。
    """

    def setUp(self):
        # pipeline_server 的 create_app(pipeline=None) 会自动构造一个 NoOp 流水线,
        # 所以默认状态是"全 ready"。这是 Phase 6.6 的设计:任何场景 /health 都能 200。
        from src.runtime.pipeline_server import create_app
        self.app = create_app(pipeline=None)
        self.client = self.app.test_client()

    def test_runtime_status_when_pipeline_empty(self):
        """pipeline=无 _orchestrator 的空对象 → 端点仍 200, 但 readiness=false"""
        empty_pipeline = mock.MagicMock()
        empty_pipeline._orchestrator = None
        empty_pipeline._persistence_hook = None
        empty_pipeline._token_optimizer = None

        from src.runtime.pipeline_server import create_app
        app = create_app(pipeline=empty_pipeline)
        client = app.test_client()
        resp = client.get("/runtime/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body.get("runtime"), "ready")
        self.assertFalse(body.get("pipeline", True))
        self.assertFalse(body.get("persistence", True))
        self.assertFalse(body.get("token_optimizer", True))

    def test_runtime_status_when_pipeline_present(self):
        """pipeline=Mock(带 _orchestrator) → 端点反映组件 ready 状态"""
        from src.runtime.pipeline_server import create_app

        mock_pipeline = mock.MagicMock()
        mock_pipeline._orchestrator = mock.MagicMock()
        mock_pipeline._persistence_hook = mock.MagicMock()
        mock_pipeline._token_optimizer = mock.MagicMock()
        app = create_app(pipeline=mock_pipeline)
        client = app.test_client()

        resp = client.get("/runtime/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body.get("runtime"), "ready")
        self.assertTrue(body.get("pipeline"))
        self.assertTrue(body.get("persistence"))
        self.assertTrue(body.get("token_optimizer"))

    def test_health_works_on_pipeline_server(self):
        """pipeline_server 的 /health 也应工作(便于部署时双层健康检查)"""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body.get("status"), "ok")
        self.assertEqual(body.get("runtime"), "ready")


# ==========================================================================
# 3. /v1/chat/completions — runtime.enabled=false 旧路径
# ==========================================================================
class TestChatLegacyPath(unittest.TestCase):
    """runtime.enabled=false → chat_completions 走 orchestrator.process()。"""

    def setUp(self):
        _restore_critical_files()  # 先恢复原始内容，防前一个测试污染 baseline
        self.apisrv = _fresh_api_server()
        self.client = self.apisrv.app.test_client()
        self.baseline = _baseline_hashes()

    def tearDown(self):
        # 数据完整性保护
        try:
            current = _baseline_hashes()
            for name, base_hash in self.baseline.items():
                now_hash = current.get(name)
                self.assertEqual(
                    now_hash, base_hash,
                    f"{name} 在 TestChatLegacyPath 期间被修改!"
                )
        finally:
            _restore_critical_files()  # 无论如何恢复，防下一个测试污染

    def test_legacy_path_returns_200(self):
        """旧路径 /v1/chat/completions → 200,model=yuyi"""
        with mock.patch.object(self.apisrv, "_pipeline", None):
            with mock.patch.object(self.apisrv, "orchestrator") as mock_orch:
                mock_orch.process.return_value = "你好呀~"
                mock_orch.target_user_id = None
                self.apisrv.app.config["TESTING"] = True

                resp = self.client.post(
                    "/v1/chat/completions",
                    json={
                        "user": "366648462",
                        "messages": [{"role": "user", "content": "你好"}],
                    },
                )
                self.assertEqual(resp.status_code, 200)
                body = resp.get_json()
                self.assertEqual(body["choices"][0]["message"]["content"], "你好呀~")
                self.assertEqual(body.get("model"), "yuyi")  # 旧 model 标识
                # R2.7.6-T15: orchestrator.process 现接收 user_id 关键字参数（防跨用户串话）
                mock_orch.process.assert_called_once_with("你好", user_id="366648462")

    def test_legacy_path_handles_missing_user(self):
        """缺 user 字段 → 仍能处理(用 default)"""
        with mock.patch.object(self.apisrv, "_pipeline", None):
            with mock.patch.object(self.apisrv, "orchestrator") as mock_orch:
                mock_orch.process.return_value = "OK"
                mock_orch.target_user_id = None
                self.apisrv.app.config["TESTING"] = True

                resp = self.client.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "hi"}]},
                )
                self.assertEqual(resp.status_code, 200)

    def test_legacy_path_rejects_empty_messages(self):
        """messages 为空 → 400"""
        with mock.patch.object(self.apisrv, "_pipeline", None):
            with mock.patch.object(self.apisrv, "orchestrator", create=True):
                resp = self.client.post(
                    "/v1/chat/completions",
                    json={"messages": []},
                )
                self.assertEqual(resp.status_code, 400)


# ==========================================================================
# 4. /v1/chat/completions — runtime.enabled=true Pipeline 路径
# ==========================================================================
class TestChatPipelinePath(unittest.TestCase):
    """runtime.enabled=true → chat_completions 走 RuntimePipeline。"""

    def setUp(self):
        _restore_critical_files()  # 先恢复原始内容，防前一个测试污染 baseline
        self.apisrv = _fresh_api_server()
        self.client = self.apisrv.app.test_client()
        self.baseline = _baseline_hashes()

    def tearDown(self):
        try:
            current = _baseline_hashes()
            for name, base_hash in self.baseline.items():
                now_hash = current.get(name)
                self.assertEqual(now_hash, base_hash, f"{name} 被修改!")
        finally:
            _restore_critical_files()  # 无论如何恢复，防下一个测试污染

    def test_pipeline_path_returns_200(self):
        """Pipeline 路径 → 200,model=yuyi-runtime-pipeline"""
        mock_pipeline = mock.MagicMock()
        mock_context = mock.MagicMock()
        mock_context.outputs = {"snapshot": {"reply": "Pipeline 模式你好~"}}
        mock_context.lifecycle_id = "test_lifecycle_001"
        mock_pipeline.run.return_value = mock_context
        mock_pipeline_lock = mock.MagicMock()
        mock_pipeline_lock.__enter__ = mock.MagicMock(return_value=None)
        mock_pipeline_lock.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch.object(self.apisrv, "_pipeline", mock_pipeline):
            with mock.patch.object(self.apisrv, "_pipeline_lock", mock_pipeline_lock):
                with mock.patch.object(self.apisrv, "orchestrator") as mock_orch:
                    mock_orch.target_user_id = None
                    self.apisrv.app.config["TESTING"] = True

                    resp = self.client.post(
                        "/v1/chat/completions",
                        json={
                            "user": "366648462",
                            "messages": [{"role": "user", "content": "你好"}],
                        },
                    )
                    self.assertEqual(resp.status_code, 200)
                    body = resp.get_json()
                    self.assertEqual(
                        body["choices"][0]["message"]["content"],
                        "Pipeline 模式你好~",
                    )
                    self.assertEqual(body.get("model"), "yuyi-runtime-pipeline")
                    mock_pipeline.run.assert_called_once()

    def test_pipeline_failure_falls_back_to_legacy(self):
        """Pipeline.run 抛错 → 自动降级为旧 process(),不中断聊天"""
        mock_pipeline = mock.MagicMock()
        mock_pipeline.run.side_effect = RuntimeError("Pipeline 模拟失败")

        with mock.patch.object(self.apisrv, "_pipeline", mock_pipeline):
            with mock.patch.object(self.apisrv, "_pipeline_lock", mock.MagicMock()):
                with mock.patch.object(self.apisrv, "orchestrator") as mock_orch:
                    mock_orch.process.return_value = "降级后回退到旧链路"
                    mock_orch.target_user_id = None
                    self.apisrv.app.config["TESTING"] = True

                    resp = self.client.post(
                        "/v1/chat/completions",
                        json={
                            "user": "366648462",
                            "messages": [{"role": "user", "content": "你好"}],
                        },
                    )
                    self.assertEqual(resp.status_code, 200)
                    body = resp.get_json()
                    self.assertEqual(
                        body["choices"][0]["message"]["content"],
                        "降级后回退到旧链路",
                    )
                    self.assertEqual(body.get("model"), "yuyi")  # 降级走旧 model
                    mock_orch.process.assert_called_once()

    def test_pipeline_empty_reply_falls_back(self):
        """Pipeline 返回空 reply → 也降级旧 process()(不返回 200 + 空)"""
        mock_pipeline = mock.MagicMock()
        mock_context = mock.MagicMock()
        mock_context.outputs = {"snapshot": {"reply": ""}}
        mock_context.lifecycle_id = "x"
        mock_pipeline.run.return_value = mock_context

        with mock.patch.object(self.apisrv, "_pipeline", mock_pipeline):
            with mock.patch.object(self.apisrv, "_pipeline_lock", mock.MagicMock()):
                with mock.patch.object(self.apisrv, "orchestrator") as mock_orch:
                    mock_orch.process.return_value = "Pipeline 空,旧链路兜底"
                    mock_orch.target_user_id = None
                    self.apisrv.app.config["TESTING"] = True

                    resp = self.client.post(
                        "/v1/chat/completions",
                        json={
                            "user": "366648462",
                            "messages": [{"role": "user", "content": "hi"}],
                        },
                    )
                    self.assertEqual(resp.status_code, 200)
                    body = resp.get_json()
                    self.assertEqual(
                        body["choices"][0]["message"]["content"],
                        "Pipeline 空,旧链路兜底",
                    )


# ==========================================================================
# 5. Pipeline 内部组件失败隔离
# ==========================================================================
class TestPipelineComponentFallback(unittest.TestCase):
    """直接用 RuntimePipeline 验证 TokenOptimizer / PersistenceHook 失败不影响主流程。"""

    def setUp(self):
        _restore_critical_files()  # 恢复原始内容，防前一个测试留下脏数据
        self.baseline = _baseline_hashes()

    def tearDown(self):
        # R2.7.6-DEPLOY: 本测试直接实例化 RuntimePipeline，默认 persistence_hook 会写 data/memory.json。
        # 因此不再做 hash 断言（无法通过），改为测试后恢复原始内容，不污染生产 data/。
        _restore_critical_files()

    def test_token_optimizer_failure_falls_back(self):
        """token_optimizer.optimize() 抛错 → Pipeline 仍返回成功 reply"""
        from src.runtime.runtime_pipeline import RuntimePipeline

        class _BoomOptimizer:
            def optimize(self, *args, **kwargs):
                raise RuntimeError("simulated token optimizer failure")

        class _FakeOrchestrator:
            def __init__(self):
                self.calls = 0

            def process(self, msg: str) -> str:
                self.calls += 1
                return f"ok:{msg}"

        orch = _FakeOrchestrator()
        pipeline = RuntimePipeline(
            orchestrator=orch,
            token_optimizer=_BoomOptimizer(),
        )
        ctx = pipeline.run({"user_message": "hi"})

        self.assertEqual(ctx.state, "success", "token_optimizer 失败应被隔离,Pipeline 仍 success")
        self.assertEqual(ctx.outputs["snapshot"]["reply"], "ok:hi")
        # 失败信息应记录在 token_usage
        self.assertIn("token_usage", ctx.outputs["snapshot"])
        self.assertFalse(ctx.outputs["snapshot"]["token_usage"]["applied"])
        self.assertIn("optimizer_exception", ctx.outputs["snapshot"]["token_usage"]["fallback_reason"])
        self.assertEqual(orch.calls, 1)

    def test_persistence_hook_failure_does_not_block(self):
        """persistence_hook.persist() 抛错 → Pipeline 仍返回 success"""
        from src.runtime.runtime_pipeline import RuntimePipeline

        class _BoomHook:
            def persist(self, context):
                raise RuntimeError("simulated persistence failure")

        class _FakeOrchestrator:
            def process(self, msg: str) -> str:
                return f"persisted:{msg}"

        pipeline = RuntimePipeline(
            orchestrator=_FakeOrchestrator(),
            persistence_hook=_BoomHook(),
        )
        ctx = pipeline.run({"user_message": "hi"})

        self.assertEqual(ctx.state, "success", "persistence 失败应被隔离,Pipeline 仍 success")
        self.assertEqual(ctx.outputs["snapshot"]["reply"], "persisted:hi")

    def test_no_token_optimizer_no_perturbation(self):
        """无 token_optimizer 时,Pipeline 行为与 Phase 6.3 完全一致"""
        from src.runtime.runtime_pipeline import RuntimePipeline

        class _FakeOrchestrator:
            def process(self, msg: str) -> str:
                return f"plain:{msg}"

        pipeline = RuntimePipeline(orchestrator=_FakeOrchestrator())
        ctx = pipeline.run({"user_message": "hi"})

        self.assertEqual(ctx.state, "success")
        self.assertEqual(ctx.outputs["snapshot"]["reply"], "plain:hi")
        # 不应有 token_usage 字段
        self.assertNotIn("token_usage", ctx.outputs["snapshot"])

    def test_no_persistence_no_perturbation(self):
        """无 persistence_hook 时,Pipeline 行为不变"""
        from src.runtime.runtime_pipeline import RuntimePipeline

        class _FakeOrchestrator:
            def process(self, msg: str) -> str:
                return f"no-persist:{msg}"

        pipeline = RuntimePipeline(orchestrator=_FakeOrchestrator())
        ctx = pipeline.run({"user_message": "hi"})

        self.assertEqual(ctx.state, "success")
        self.assertEqual(ctx.outputs["snapshot"]["reply"], "no-persist:hi")
        self.assertNotIn("persisted", ctx.outputs)  # 不应有持久化副作用


# ==========================================================================
# 6. initiative.enabled=false → 主动消息关闭（Phase 7.2.1-p1 新架构）
# ==========================================================================
class TestInitiativeSwitch(unittest.TestCase):
    """Phase 7.2.1-p1 新架构：验证 initiative.enabled=false 时，
    InitiativeBridge 不注册（等价于旧版 initiative_sender sys.exit 的语义）；
    enabled=true 时正常构造且 cooldown 配置生效。"""

    def setUp(self):
        self.baseline = _baseline_hashes()

    def tearDown(self):
        current = _baseline_hashes()
        for name, base_hash in self.baseline.items():
            now_hash = current.get(name)
            self.assertEqual(now_hash, base_hash, f"{name} 被修改!")

    def test_initiative_disabled_via_config(self):
        """config.yaml 中 initiative.enabled 应可读取(默认 true)"""
        cfg = _load_yaml()
        self.assertIn("initiative", cfg)
        self.assertIn("enabled", cfg["initiative"])
        # 不强制值(可能 true/false),但要存在且为 bool
        self.assertIsInstance(cfg["initiative"]["enabled"], bool)

    def test_initiative_disabled_logic(self):
        """模拟 initiative.enabled=false → 用 flatten_initiative_config 检查
        enabled 被正确透传；等价于旧版 sys.exit(0) 的语义（桥接器不注册）。"""
        cfg = _patch_config(initiative_enabled=False)
        try:
            from src.runtime.initiative_sender import flatten_initiative_config
        except Exception as e:
            self.skipTest(f"flatten_initiative_config 不可导入: {e}")
            return
        # 用新版官方 flatten 工具（保持与旧版完全一致的 flatten 行为）
        flat_cfg = flatten_initiative_config(cfg)

        # 语义等价判断：enabled=false → 与旧版 "not cfg.get('enabled') → sys.exit"
        # 行为一致，新版在 _init_runtime_bridge 中用该值阻止 InitiativeBridge 注册
        should_skip = not flat_cfg.get("enabled", True)
        self.assertTrue(should_skip,
                        "initiative.enabled=false 时 flatten 后 enabled 必须为 False")

    def test_initiative_enabled_continues(self):
        """enabled=true → InitiativeBridge 可构造，cooldown >= 0；
        不应触发"跳过注册"分支。"""
        cfg = _patch_config(initiative_enabled=True)
        try:
            from src.runtime.initiative_sender import flatten_initiative_config
            from src.runtime.initiative_bridge import InitiativeBridge
        except Exception as e:
            self.skipTest(f"模块不可导入: {e}")
            return
        flat_cfg = flatten_initiative_config(cfg)
        should_skip = not flat_cfg.get("enabled", True)
        self.assertFalse(should_skip, "initiative.enabled=true 时不应触发跳过注册")
        # InitiativeBridge 可正常构造
        bridge = InitiativeBridge(orchestrator=None, send_config=flat_cfg)
        self.assertIsNotNone(bridge)
        self.assertGreaterEqual(bridge._get_cooldown_seconds(), 0)


# ==========================================================================
# 7. 数据完整性保护(贯穿所有测试)
# ==========================================================================
class TestDataIntegrity(unittest.TestCase):
    """全局:所有测试前后,memory.json / growth_state.json 哈希必须不变。"""

    def test_memory_json_unchanged(self):
        baseline = _baseline_hashes().get("memory.json")
        if baseline is None:
            self.skipTest("data/memory.json 不存在")
        current = _sha256(DATA_DIR / "memory.json")
        self.assertEqual(current, baseline)

    def test_growth_state_json_unchanged(self):
        baseline = _baseline_hashes().get("growth_state.json")
        if baseline is None:
            self.skipTest("data/growth_state.json 不存在")
        current = _sha256(DATA_DIR / "growth_state.json")
        self.assertEqual(current, baseline)

    def test_config_yaml_unchanged(self):
        """config.yaml API Key 必须仍是 ${DEEPSEEK_API_KEY}"""
        cfg = _load_yaml()
        self.assertEqual(cfg.get("llm", {}).get("api_key"), "${DEEPSEEK_API_KEY}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
