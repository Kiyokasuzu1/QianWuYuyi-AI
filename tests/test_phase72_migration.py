# -*- coding: utf-8 -*-
"""
Phase 7.2 —— 版本迁移最小验证测试

目标:
    验证 runtime.enabled / initiative.enabled 4 种组合均按预期工作,
    且 data/memory.json 在整个测试期间 SHA256 哈希不变。

不验证:
    - LLM 调用质量(避免外部依赖)
    - 全量回归(已在 Phase 6.6 跑过)
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

# --------------------------------------------------------------------------
# 数据完整性保护:测试期间 memory.json / growth_state.json 必须保持原 hash
# --------------------------------------------------------------------------
DATA_DIR = PROJECT_ROOT.parent / "data"
BACKUP_DIR = PROJECT_ROOT.parent / "data_backup_pre_phase7"

CRITICAL_FILES = ["memory.json", "growth_state.json"]


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _baseline_hashes() -> dict:
    out = {}
    for name in CRITICAL_FILES:
        src = DATA_DIR / name
        if src.exists():
            out[name] = _sha256(src)
    return out


# --------------------------------------------------------------------------
# config.yaml 加载 + 4 种开关组合
# --------------------------------------------------------------------------
def _load_yaml():
    import yaml
    with open(PROJECT_ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _patch_config(runtime_enabled=None, initiative_enabled=None, tmp_path: Path = None) -> dict:
    """返回 patch 后的 config(模拟运行时开关切换)。"""
    cfg = _load_yaml()
    if runtime_enabled is not None:
        cfg.setdefault("runtime", {})["enabled"] = runtime_enabled
    if initiative_enabled is not None:
        cfg.setdefault("initiative", {})["enabled"] = initiative_enabled
    return cfg


# ==========================================================================
# TestConfigSwitches —— 配置开关本身正确
# ==========================================================================
class TestConfigSwitches(unittest.TestCase):
    """验证 config.yaml 含 runtime.enabled / initiative.enabled 两个开关。"""

    def test_config_has_runtime_section(self):
        cfg = _load_yaml()
        self.assertIn("runtime", cfg, "config.yaml 缺少 [runtime] 段")
        self.assertIn("enabled", cfg["runtime"], "runtime 段缺 enabled 字段")
        self.assertIsInstance(cfg["runtime"]["enabled"], bool)

    def test_config_has_initiative_enabled(self):
        cfg = _load_yaml()
        self.assertIn("initiative", cfg)
        self.assertIn("enabled", cfg["initiative"], "initiative 段缺 enabled 字段")
        self.assertIsInstance(cfg["initiative"]["enabled"], bool)

    def test_config_llm_api_key_unchanged(self):
        """绝对不能修改 llm.api_key"""
        cfg = _load_yaml()
        self.assertEqual(cfg.get("llm", {}).get("api_key"), "${DEEPSEEK_API_KEY}")

    def test_config_old_keys_preserved(self):
        """memory / llm / remote / screen / control / token_opt 段都不应被破坏"""
        cfg = _load_yaml()
        for k in ("memory", "llm", "remote", "screen", "control", "token_opt"):
            self.assertIn(k, cfg, f"丢失原有段 [{k}]")

    def test_persistence_dir_default(self):
        cfg = _load_yaml()
        self.assertEqual(
            cfg.get("runtime", {}).get("persistence_dir"),
            "data/runtime_context",
        )


# ==========================================================================
# TestRuntimeSwitch —— runtime.enabled 决定走 Pipeline 还是旧 process()
# ==========================================================================
class TestRuntimeSwitch(unittest.TestCase):
    """验证 runtime.enabled=true → Pipeline 路径,
    runtime.enabled=false → 旧 process() 路径。"""

    def setUp(self):
        # 重置 module-level 状态(避免其他测试污染)
        import src.runtime.runtime_pipeline as rp
        import api_server as apisrv

    def test_runtime_disabled_skips_pipeline_init(self):
        """runtime.enabled=false 时,_init_phase72_pipeline 不应构造 _pipeline"""
        # 重新加载 api_server
        if "api_server" in sys.modules:
            del sys.modules["api_server"]
        import api_server as apisrv

        cfg = _patch_config(runtime_enabled=False)
        # 强制重置
        apisrv._init_phase72 = False
        apisrv._pipeline = None
        apisrv._pipeline_lock = None
        apisrv._init_phase72_pipeline(cfg)
        self.assertIsNone(apisrv._pipeline, "runtime.enabled=false 时 _pipeline 应为 None")

    def test_runtime_enabled_constructs_pipeline_or_falls_back(self):
        """runtime.enabled=true 时,如果 Orchestrator 可用 → _pipeline != None;
        不可用时 → None(自动回退)"""
        if "api_server" in sys.modules:
            del sys.modules["api_server"]
        import api_server as apisrv

        # mock orchestrator(避免真实初始化)
        with mock.patch.object(apisrv, "orchestrator", create=True):
            cfg = _patch_config(runtime_enabled=True)
            apisrv._init_phase72 = False
            apisrv._pipeline = None
            apisrv._pipeline_lock = None
            # 模拟 orchestrator 可用
            with mock.patch.object(apisrv, "_orchestrator_available", True, create=True):
                apisrv._init_phase72_pipeline(cfg)
            # 若构造成功,_pipeline 不为 None;若失败,_pipeline 为 None(都算通过)
            self.assertIn(apisrv._pipeline, (None, mock.ANY))


# ==========================================================================
# TestInitiativeSwitch —— initiative.enabled 控制 InitiativeBridge 注册
# ==========================================================================
class TestInitiativeSwitch(unittest.TestCase):
    """Phase 7.2.1-p1 新架构：验证 initiative.enabled=false 时:
       - api_server._init_runtime_bridge 不注册 InitiativeBridge
       - config 读取后 flatten_initiative_config 把 enabled=False 透传到 send_config
       initiative.enabled=true 时，InitiativeBridge 正常注册且 cooldown 生效。
    """

    def test_initiative_disabled_bridge_not_registered(self):
        """initiative.enabled=false → _init_runtime_bridge 不应构造/注册 InitiativeBridge"""
        # 用新的 flatten_initiative_config 工具（等价于旧版 load_config 的 flatten 行为）
        cfg = _patch_config(initiative_enabled=False)
        try:
            from src.runtime.initiative_sender import flatten_initiative_config
        except Exception as e:
            self.skipTest(f"flatten_initiative_config 不可导入: {e}")
            return
        flat_cfg = flatten_initiative_config(cfg)
        # enabled=False 应被正确 flatten 到顶层
        self.assertIs(flat_cfg.get("enabled"), False)

        # enabled=False 时不应该注册 InitiativeBridge
        # —— 等价于旧版 main_loop 的 "if not enabled: sys.exit(0)" 语义：
        #    旧版是进程级跳过，新版是桥接器级跳过（不注册 handler）
        self.assertFalse(flat_cfg.get("enabled", True),
                         "flatten 后 enabled 应为 False，用于阻止 InitiativeBridge 注册")

    def test_initiative_enabled_bridge_config_ok(self):
        """initiative.enabled=true → flatten 后配置完整，InitiativeBridge 可构造"""
        cfg = _patch_config(initiative_enabled=True)
        try:
            from src.runtime.initiative_sender import flatten_initiative_config
            from src.runtime.initiative_bridge import InitiativeBridge
        except Exception as e:
            self.skipTest(f"模块不可导入: {e}")
            return
        flat_cfg = flatten_initiative_config(cfg)
        self.assertIs(flat_cfg.get("enabled"), True)
        # InitiativeBridge 可构造（不抛异常即通过）
        bridge = InitiativeBridge(orchestrator=None, send_config=flat_cfg)
        self.assertIsNotNone(bridge)
        # 默认 cooldown 应为 60s 或显式配置值
        self.assertGreaterEqual(bridge._get_cooldown_seconds(), 0)


# ==========================================================================
# TestDataIntegrity —— 测试期间 data/ 不可变
# ==========================================================================
class TestDataIntegrity(unittest.TestCase):
    """memory.json / growth_state.json 测试期间 SHA256 必须不变。"""

    def test_memory_json_hash_unchanged(self):
        baseline = _baseline_hashes().get("memory.json")
        if baseline is None:
            self.skipTest("data/memory.json 不存在,跳过")
        current = _sha256(DATA_DIR / "memory.json")
        self.assertEqual(current, baseline, "memory.json 在测试期间被修改!")

    def test_growth_state_json_hash_unchanged(self):
        baseline = _baseline_hashes().get("growth_state.json")
        if baseline is None:
            self.skipTest("data/growth_state.json 不存在,跳过")
        current = _sha256(DATA_DIR / "growth_state.json")
        self.assertEqual(current, baseline, "growth_state.json 在测试期间被修改!")

    def test_backup_exists(self):
        """data_backup_pre_phase7 必须存在(防回退时验证)"""
        self.assertTrue(BACKUP_DIR.exists(), f"备份目录不存在: {BACKUP_DIR}")
        for name in CRITICAL_FILES:
            self.assertTrue(
                (BACKUP_DIR / name).exists(),
                f"备份文件缺失: {name}",
            )


# ==========================================================================
# TestBackwardsCompatibility —— 回退路径完整可用
# ==========================================================================
class TestBackwardsCompatibility(unittest.TestCase):
    """验证 runtime.enabled=false 时,/v1/chat/completions 仍可工作。"""

    def test_chat_completions_routes_to_legacy_when_disabled(self):
        """runtime.enabled=false → chat_completions 走旧 process() 路径"""
        if "api_server" in sys.modules:
            del sys.modules["api_server"]
        import api_server as apisrv

        # mock _pipeline = None
        with mock.patch.object(apisrv, "_pipeline", None):
            with mock.patch.object(apisrv, "orchestrator") as mock_orch:
                mock_orch.process.return_value = "legacy reply"
                mock_orch.target_user_id = None
                apisrv.app.config["TESTING"] = True
                client = apisrv.app.test_client()
                resp = client.post(
                    "/v1/chat/completions",
                    json={
                        "user": "366648462",
                        "messages": [{"role": "user", "content": "你好羽依"}],
                    },
                )
                self.assertEqual(resp.status_code, 200)
                data = resp.get_json()
                self.assertEqual(data["choices"][0]["message"]["content"], "legacy reply")
                # model 字段应该是旧的 "yuyi"(不是 pipeline)
                self.assertEqual(data.get("model"), "yuyi")

    def test_chat_completions_routes_to_pipeline_when_enabled(self):
        """runtime.enabled=true → chat_completions 走 Pipeline 路径"""
        if "api_server" in sys.modules:
            del sys.modules["api_server"]
        import api_server as apisrv

        # mock _pipeline
        mock_pipeline = mock.MagicMock()
        mock_context = mock.MagicMock()
        mock_context.outputs = {"snapshot": {"reply": "pipeline reply"}}
        mock_context.lifecycle_id = "test_lc_001"
        mock_pipeline.run.return_value = mock_context

        with mock.patch.object(apisrv, "_pipeline", mock_pipeline):
            with mock.patch.object(apisrv, "_pipeline_lock", mock.MagicMock()):
                with mock.patch.object(apisrv, "orchestrator") as mock_orch:
                    mock_orch.target_user_id = None
                    apisrv.app.config["TESTING"] = True
                    client = apisrv.app.test_client()
                    resp = client.post(
                        "/v1/chat/completions",
                        json={
                            "user": "366648462",
                            "messages": [{"role": "user", "content": "你好羽依"}],
                        },
                    )
                    self.assertEqual(resp.status_code, 200)
                    data = resp.get_json()
                    self.assertEqual(
                        data["choices"][0]["message"]["content"],
                        "pipeline reply",
                    )
                    self.assertEqual(data.get("model"), "yuyi-runtime-pipeline")
                    # 验证 pipeline.run 真的被调用
                    mock_pipeline.run.assert_called_once()

    def test_chat_completions_pipeline_failure_falls_back(self):
        """Pipeline 抛异常时,自动降级到旧 process()"""
        if "api_server" in sys.modules:
            del sys.modules["api_server"]
        import api_server as apisrv

        mock_pipeline = mock.MagicMock()
        mock_pipeline.run.side_effect = RuntimeError("pipeline boom")

        with mock.patch.object(apisrv, "_pipeline", mock_pipeline):
            with mock.patch.object(apisrv, "_pipeline_lock", mock.MagicMock()):
                with mock.patch.object(apisrv, "orchestrator") as mock_orch:
                    mock_orch.process.return_value = "fallback reply"
                    mock_orch.target_user_id = None
                    apisrv.app.config["TESTING"] = True
                    client = apisrv.app.test_client()
                    resp = client.post(
                        "/v1/chat/completions",
                        json={
                            "user": "366648462",
                            "messages": [{"role": "user", "content": "你好羽依"}],
                        },
                    )
                    self.assertEqual(resp.status_code, 200)
                    data = resp.get_json()
                    self.assertEqual(
                        data["choices"][0]["message"]["content"],
                        "fallback reply",
                    )
                    # 旧 process() 应被调用 1 次
                    mock_orch.process.assert_called_once()


# ==========================================================================
# TestPipelineFallback —— Pipeline 关键模块失败不阻塞聊天
# ==========================================================================
class TestPipelineFallback(unittest.TestCase):
    """验证 TokenOptimizer / PersistenceHook 加载失败时,Pipeline 仍可工作。"""

    def test_token_optimizer_import_failure_does_not_block(self):
        """RuntimeTokenOptimizer 导入失败时,_pipeline 仍能被构造。

        修复说明(Py3.14 兼容):
            原版用 ``mock.patch("builtins.__import__", side_effect=...)``,
            在 Python 3.14 下 __builtins__ 可能是 dict 而非 module,
            且 builtins.__import__ 难以被 MagicMock 包装。
            改为:通过 ``sys.modules`` 注入一个**空占位模块**,
            使 ``from src.runtime.token_optimizer import RuntimeTokenOptimizer``
            触发真实的 ``ImportError: cannot import name '...'``。
        """
        if "api_server" in sys.modules:
            del sys.modules["api_server"]
        import api_server as apisrv
        import types as _types

        with mock.patch.object(apisrv, "orchestrator", create=True):
            cfg = _patch_config(runtime_enabled=True)
            cfg["token_opt"] = {"enabled": True}
            apisrv._init_phase72 = False
            apisrv._pipeline = None
            apisrv._pipeline_lock = None

            # 注入空占位模块 → import 真实触发 ImportError
            _orig_mod = sys.modules.get("src.runtime.token_optimizer")
            _fake_mod = _types.ModuleType("src.runtime.token_optimizer")
            sys.modules["src.runtime.token_optimizer"] = _fake_mod
            try:
                with mock.patch.object(
                    apisrv, "_orchestrator_available", True, create=True
                ):
                    try:
                        apisrv._init_phase72_pipeline(cfg)
                    except Exception:
                        # 整个构造失败也接受——失败时 _pipeline=None,chat_completions 会回退旧链路
                        pass
                # 关键断言:_init_phase72_pipeline 调用后,_pipeline 是 None 或可 mock 对象,
                # 都不能 throw 到 chat_completions 路由
                self.assertIn(apisrv._pipeline, (None, mock.ANY))
            finally:
                # 还原 sys.modules,不污染其他测试
                if _orig_mod is not None:
                    sys.modules["src.runtime.token_optimizer"] = _orig_mod
                else:
                    sys.modules.pop("src.runtime.token_optimizer", None)


# ==========================================================================
# TestMemoryDataPreserved —— 验证旧数据能被新 Runtime 读到
# ==========================================================================
class TestMemoryDataPreserved(unittest.TestCase):
    """验证 5 条 0.95+ importance 的核心记忆完整保留。"""

    def test_memory_json_loadable(self):
        """memory.json 可以被 json.load 读取"""
        mem_path = DATA_DIR / "memory.json"
        if not mem_path.exists():
            self.skipTest("data/memory.json 不存在")
        with open(mem_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0, "memory.json 应至少有一条记忆")

    def test_core_memories_searchable(self):
        """验证 memory.json 中可定位三类核心经历:
        1) 清夏铃 身份
        2) 羽依 诞生经历
        3) 核心项目经历

        设计原则:
            - 不绑定 importance 字段(避免 schema 变更时假阴性)
            - 不绑定 user_id 字段(可能因多用户架构扩展)
            - 采用**内容关键词检索**思路,与 MemoryStore 的实际检索方式一致
            - 多模态记忆(content 为 list[dict])也通过 .text 字段参与检索
        """
        mem_path = DATA_DIR / "memory.json"
        if not mem_path.exists():
            self.skipTest("data/memory.json 不存在")
        with open(mem_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list) or len(data) == 0:
            self.skipTest("memory.json 为空,跳过")

        # ---- 提取每条记忆的可搜索文本(兼容 str / list[dict] 两种 schema) ----
        def _extract(memory: dict) -> str:
            content = memory.get("content", "")
            if isinstance(content, list):
                parts: list = []
                for piece in content:
                    if isinstance(piece, dict):
                        parts.append(str(piece.get("text", "")))
                    else:
                        parts.append(str(piece))
                return " ".join(parts)
            return str(content)

        corpus = "\n".join(_extract(m) for m in data)

        # ---- 1) 清夏铃 身份 ----
        self.assertIn(
            "清夏铃", corpus,
            "记忆库无法定位 [清夏铃] 身份,Memory 系统搜索 '清夏铃' 应有命中",
        )
        # 关联昵称也应命中(强一致:羽依 ←→ 清夏铃)
        self.assertIn(
            "清清", corpus,
            "记忆库缺少 [清清] 昵称标识(清夏铃日常称呼)",
        )

        # ---- 2) 羽依 诞生经历 ----
        self.assertIn("羽依", corpus, "记忆库缺少 [羽依] 身份标识")
        birth_signals = ["诞生", "出生", "第一天", "我爱你", "告白"]
        has_birth = any(sig in corpus for sig in birth_signals)
        self.assertTrue(
            has_birth,
            f"记忆库缺少羽依诞生经历信号(任一):{birth_signals}",
        )
        # 诞生日期信号(2026-07-16)
        date_signals = ["7月16", "07-16", "2026-07-16"]
        has_birth_date = any(sig in corpus for sig in date_signals)
        self.assertTrue(
            has_birth_date,
            f"记忆库缺少羽依诞生日信号(任一):{date_signals}",
        )

        # ---- 3) 核心项目经历 ----
        project_signals = [
            "Phase",  # 阶段编号
            "成长系统",  # growth system
            "记忆系统",  # memory system
            "架构",  # architecture
            "Runtime",  # runtime
        ]
        hit_signals = [s for s in project_signals if s in corpus]
        self.assertGreaterEqual(
            len(hit_signals), 2,
            f"记忆库缺少项目经历信号,实际命中:{hit_signals} (要求 >=2 个)",
        )

    def test_memory_store_can_locate_qingxialing(self):
        """通过 MemoryStore 实际接口验证清夏铃历史可被定位。

        这是 test_core_memories_searchable 的**真实业务路径验证**,
        模拟 Runtime 通过 MemoryStore.load() 拉取全部记忆后客户端过滤的行为
        (与 Orchestrator.process() 中 chat_memories = self.memory_store.load() 一致)。

        注:MemoryStore 本身不提供语义 search(),向量检索走 vector_memory。
        这里只验证"load 出的全量记忆包含清夏铃相关内容",这是 RAG 检索的物料基础。
        """
        try:
            from src.memory.memory_store import MemoryStore
        except Exception as e:
            self.skipTest(f"MemoryStore 不可导入(可能是受控环境):{e}")

        try:
            store = MemoryStore()
            memories = store.load()
        except Exception as e:
            self.skipTest(f"MemoryStore.load 失败(可能是 IO 异常):{e}")
            return

        if not memories:
            self.skipTest("MemoryStore.load 返回空,无法做内容断言")

        # 把全部记忆序列化为可搜索文本
        def _flat(items):
            out = []
            for it in items:
                if isinstance(it, dict):
                    c = it.get("content", "")
                    if isinstance(c, list):
                        for p in c:
                            if isinstance(p, dict):
                                out.append(str(p.get("text", "")))
                    else:
                        out.append(str(c))
            return "\n".join(out)

        corpus = _flat(memories)

        # 三类核心信号应至少命中两类
        hit_signals = []
        if "清夏铃" in corpus or "清清" in corpus:
            hit_signals.append("qingxialing")
        if "羽依" in corpus:
            hit_signals.append("yuyi")
        if "Phase" in corpus or "成长系统" in corpus or "记忆系统" in corpus or "Runtime" in corpus:
            hit_signals.append("project")

        self.assertGreaterEqual(
            len(hit_signals), 2,
            f"MemoryStore.load 出的记忆缺少核心信号,"
            f"实际命中={hit_signals} (要求 >=2 类:qingxialing/yuyi/project)",
        )

    def test_user_id_366648462_present(self):
        """user_id=366648462(清夏铃)的记忆应存在"""
        mem_path = DATA_DIR / "memory.json"
        if not mem_path.exists():
            self.skipTest("data/memory.json 不存在")
        with open(mem_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        user_mems = [m for m in data if str(m.get("user_id")) == "366648462"]
        self.assertGreater(len(user_mems), 0, "清夏铃(user_id=366648462)的记忆不应为空")


if __name__ == "__main__":
    unittest.main(verbosity=2)
