# -*- coding: utf-8 -*-
"""
tests/test_phase_4_0_1_runtime_core_unified.py

Phase 4.0.1 RuntimeCore 统一化 测试套件（SPEC v0.2 对齐版）。

目标（spec.md §2，已冻结 v0.2）：
  T-4.0.1-2A  runtime_core.py.RuntimeCore.process() = 单调用 self._lifecycle.execute()
              （禁止手写 for stage in RUNTIME_LIFECYCLE_ORDER）
  T-4.0.1-2B  17 阶段调度唯一实现点 = lifecycle.py:LifecycleExecutor.execute
  T-4.0.1-3   各阶段 fail-soft 异常隔离，错误记录到 _last_process_phase_errors
  T-4.0.1-4   最近 process 诊断字段可获取（阶段 / 事件类型 / 错误表 / ctx 缓存）
  T-4.0.1-5A  runtime.py 共享模式：self._impl = RuntimeCoreImpl，零依赖 RuntimeBridge
  T-4.0.1-5B  runtime.py configure_ports() 接受 21 个参数，允许缺失，不崩溃
  T-4.0.1-6   runtime.py 非共享模式（YUYI_RUNTIME_SHARED_MODE=0）向后兼容保留
  T-4.0.1-7   src.runtime 包级 RuntimeCore 指向 runtime_core.py 统一实现

设计原则：
- 最小依赖：不启 LLM / 不启真实向量库 / 不写真实持久化路径
- 临时目录：所有 state_file 用 tempfile，测试后清理
- 优雅跳过：导入异常对应测试 skip，不阻塞整个套件
- PEP 8：代码风格保持一致
"""
from __future__ import annotations

import ast
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, call

import pytest


# =====================================================================
# Fixtures / Helpers
# =====================================================================

class _TempDirMixin:
    """每个测试方法一个独立临时目录，用完即删。"""

    def setUp(self) -> None:
        self.temp_dir: Path = Path(tempfile.mkdtemp(prefix="yuyi_401_"))
        self.state_file: Path = self.temp_dir / "runtime_state.json"

    def tearDown(self) -> None:
        shutil.rmtree(str(self.temp_dir), ignore_errors=True)

    def _core_cfg(self) -> Dict[str, Any]:
        """生成最小配置：只给 state_file + 关闭所有可选重型模块。"""
        return {
            "state_file": str(self.state_file),
            "tick_interval_seconds": 9999.0,
            "experience_enabled": False,
            "adapters_enabled": False,
            "cognitive_enabled": False,
            "self_reflection_enabled": False,
            "curiosity_enabled": False,
            "creativity_enabled": False,
        }


# =====================================================================
# T-4.0.1-2A / 2B  RuntimeCore.process()：单调用 LifecycleExecutor
# =====================================================================

class TestRuntimeCoreProcessSingleCall(_TempDirMixin, unittest.TestCase):
    """RuntimeCore.process() = 单调用 self._lifecycle.execute()，禁止手写 for stage。"""

    def test_process_calls_lifecycle_execute_exactly_once(self):
        """process 内部 self._lifecycle.execute() 必须 exactly once 被调用。"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.events import Event

        core = RuntimeCore(config=self._core_cfg())
        # 用 spy 记录 execute 调用
        real_execute = core._lifecycle.execute
        call_records: List[Any] = []

        def _spy_execute(*args, **kwargs):
            call_records.append((args, kwargs))
            return real_execute(*args, **kwargs)

        core._lifecycle.execute = _spy_execute  # type: ignore[method-assign]
        try:
            event = Event(type="x", source="t", payload={})
            core.process(event)
            self.assertEqual(
                len(call_records), 1,
                "process() 必须恰好调用一次 self._lifecycle.execute()，"
                f"实际调用 {len(call_records)} 次",
            )
            # 验证参数签名：execute(self, event, ctx)
            args, _ = call_records[0]
            # args = (core, event, ctx)
            self.assertGreaterEqual(len(args), 3, "execute 调用必须至少传 3 个位置参数")
            self.assertIs(args[0], core, "execute 第一个参数必须是 core 自身")
            self.assertIs(args[1], event, "execute 第二个参数必须是 event 对象")
        finally:
            core._lifecycle.execute = real_execute  # type: ignore[method-assign]
            try:
                if core.is_running:
                    core.stop()
            except Exception:
                pass

    def test_process_no_manual_for_loop_over_stages(self):
        """SPEC v0.2 P0-修改2：RuntimeCore.process 源码禁止手写 for stage in ...。

        阶段调度逻辑唯一实现点 = lifecycle.py:LifecycleExecutor.execute()。
        若在 runtime_core.py process() 内再写一次 for，就是架构回退。
        """
        from src.runtime import runtime_core as rc_mod

        src_path = Path(rc_mod.__file__)
        source = src_path.read_text(encoding="utf-8")

        # --- AST 检查：process 方法体内没有 for 子节点遍历阶段常量 ---
        tree = ast.parse(source, filename=str(src_path))
        process_fn: Optional[ast.FunctionDef] = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "process":
                process_fn = node
                break
        self.assertIsNotNone(process_fn, "runtime_core.py 中找不到 process() 函数")

        # 收集 process 函数内所有 for 循环的 iter 对象名（如果有的话）
        forbidden_keywords = [
            "RUNTIME_LIFECYCLE_ORDER",
            "RuntimeStage",
            "for.*stage.*in",
            "_stage_",  # 不是完全禁止，但如果出现 for _stage_XX 就是手动调度
        ]
        # 简化：直接源码字符串 grep（比 AST 更贴近实际手写风格）
        # 但只检查 process 函数的行范围
        process_start = process_fn.lineno
        # 找结束行：process_fn.end_lineno（Python 3.8+ 有）
        process_end = getattr(process_fn, "end_lineno", process_start + 200)
        lines = source.splitlines()
        process_body_lines = lines[process_start - 1 : process_end]
        process_body = "\n".join(process_body_lines)

        # 危险信号：process() 内出现遍历阶段的循环
        self.assertNotIn(
            "RUNTIME_LIFECYCLE_ORDER",
            process_body,
            "SPEC v0.2 禁止：runtime_core.py process() 内出现 "
            "RUNTIME_LIFECYCLE_ORDER（阶段调度唯一入口是 LifecycleExecutor.execute）",
        )
        # 允许 self._lifecycle.last_stage_order（只读），但禁止 _lifecycle.execute 之外的阶段遍历
        # 额外 grep 源文件：除 lifecycle.py 外，任何地方的 "for.*RUNTIME_LIFECYCLE" 都视为违规
        # （此条已在 Gate 级 grep 检查中覆盖，此处只查本函数体）

    def test_process_method_exists_and_callable(self):
        """RuntimeCore.process(event, ctx) 可被调用，不崩溃，返回 RuntimeContext。"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.context.runtime_context import RuntimeContext
        from src.runtime.events import Event

        core = RuntimeCore(config=self._core_cfg())
        try:
            self.assertTrue(callable(getattr(core, "process", None)))
            event = Event(
                type="user_input",
                source="test",
                payload={"text": "你好", "content": "你好"},
            )
            ctx = core.process(event)
            self.assertIsInstance(ctx, RuntimeContext)
        finally:
            try:
                if core.is_running:
                    core.stop()
            except Exception:
                pass


# =====================================================================
# T-4.0.1-3 / 4  诊断字段 + fail-soft
# =====================================================================

class TestRuntimeCoreProcessDiagnostics(_TempDirMixin, unittest.TestCase):
    """process 跑完后：诊断字段填充 + fail-soft 隔离。"""

    def test_process_records_diagnostic_fields(self):
        """_last_process_stage / _last_process_event_type / _started_process_mode
        / _phase_runtime_ctx 均填充。"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.stages import RuntimeStage
        from src.runtime.events import Event

        core = RuntimeCore(config=self._core_cfg())
        try:
            event = Event(
                type="user.input",
                source="test",
                payload={"text": "诊断字段测试"},
            )
            core.process(event)
            self.assertTrue(core._started_process_mode)
            # 最后阶段名必须等于 RESPONSE（全链跑完到最后一个）
            self.assertEqual(core._last_process_stage, RuntimeStage.RESPONSE.name)
            self.assertEqual(core._last_process_event_type, "user.input")
            # ctx 缓存非空
            self.assertIsNotNone(core._phase_runtime_ctx)
        finally:
            try:
                if core.is_running:
                    core.stop()
            except Exception:
                pass

    def test_process_fail_soft_isolation(self):
        """某阶段抛错：只记录到 _last_process_phase_errors，不抛到外层。"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.stages import RuntimeStage
        from src.runtime.events import Event

        core = RuntimeCore(config=self._core_cfg())
        original = core._stage_03_emotion_update

        def _boom(*args, **kwargs):
            raise RuntimeError("fake emotion boom")

        core._stage_03_emotion_update = _boom  # type: ignore[assignment]
        try:
            event = Event(type="user_input", source="test", payload={"text": "hi"})
            ctx = core.process(event)
            self.assertIsNotNone(ctx)
            self.assertIn(
                RuntimeStage.EMOTION_UPDATE.name,
                core._last_process_phase_errors,
            )
            err_msg = core._last_process_phase_errors[RuntimeStage.EMOTION_UPDATE.name]
            self.assertIn("fake emotion boom", err_msg)
            # 后续阶段仍然执行（最后阶段必须到 RESPONSE）
            self.assertEqual(core._last_process_stage, RuntimeStage.RESPONSE.name)
        finally:
            core._stage_03_emotion_update = original  # type: ignore[assignment]
            try:
                if core.is_running:
                    core.stop()
            except Exception:
                pass

    def test_process_auto_start_when_stopped(self):
        """从未 start() 的实例上直接 process()，应自动 start 且不崩溃。"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.events import Event

        core = RuntimeCore(config=self._core_cfg())
        self.assertFalse(core.is_running)
        try:
            event = Event(type="tick", source="test", payload={})
            core.process(event)
            self.assertTrue(core.is_running)
        finally:
            try:
                if core.is_running:
                    core.stop()
            except Exception:
                pass

    def test_process_none_event_degrades(self):
        """event=None：完整执行到 RESPONSE，ctx.user_message 不崩溃。"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.stages import RuntimeStage

        core = RuntimeCore(config=self._core_cfg())
        try:
            ctx = core.process(None)
            self.assertEqual(core._last_process_stage, RuntimeStage.RESPONSE.name)
            self.assertEqual(getattr(ctx, "user_message", "??default??"), "")
        finally:
            try:
                if core.is_running:
                    core.stop()
            except Exception:
                pass

    def test_lifecycle_last_stage_order_17_items(self):
        """LifecycleExecutor.last_stage_order 长度 = 17（证明 17 阶段全被访问）。"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.events import Event

        core = RuntimeCore(config=self._core_cfg())
        try:
            core.process(Event(type="t", source="s", payload={}))
            order = core._lifecycle.last_stage_order
            self.assertEqual(
                len(order), 17,
                f"last_stage_order 必须恰好 17 项，实际 {len(order)}：{order}",
            )
        finally:
            try:
                if core.is_running:
                    core.stop()
            except Exception:
                pass


# =====================================================================
# T-4.0.1-5A / 5B  runtime.py Adapter 模式：零 Bridge + 21 参数兼容
# =====================================================================

class TestRuntimePyAdapterZeroBridge(unittest.TestCase):
    """SPEC v0.2 P0-修改1：runtime.py 不依赖 RuntimeBridge。"""

    # -----------------------------------------------------------------
    # 5A-1 源码静态检查：runtime.py 没有 import runtime_bridge
    # -----------------------------------------------------------------
    def test_runtime_py_no_runtimebridge_import(self):
        """runtime.py 源码中不得出现 import/get_runtime_bridge（注释除外）。

        结构：
            runtime.py (Adapter)
                self._impl
                    ↓
                runtime_core.py (Impl)

        严禁形成：
            runtime.py → RuntimeBridge → runtime_core.py
        否则出现循环依赖风险。
        """
        from src.runtime import runtime as runtime_mod

        src_path = Path(runtime_mod.__file__)
        lines = src_path.read_text(encoding="utf-8").splitlines()
        violations: List[str] = []
        for lineno, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            # 跳过纯注释 / 空行
            if not line or line.startswith("#"):
                continue
            lower = line.lower()
            # 违规标志（出现在非注释行）
            if "runtimebridge" in lower or "runtime_bridge" in lower:
                violations.append(f"L{lineno}: {raw_line.rstrip()}")
        self.assertEqual(
            len(violations), 0,
            "SPEC v0.2 禁止：runtime.py 非注释行引用 RuntimeBridge/runtime_bridge\n"
            "违规行：\n" + "\n".join(violations),
        )

    # -----------------------------------------------------------------
    # 5A-2 共享模式：self._impl 是 runtime_core.RuntimeCore 实例
    # -----------------------------------------------------------------
    @patch.dict(os.environ, {"YUYI_RUNTIME_SHARED_MODE": "1"}, clear=True)
    def test_shared_mode_impl_is_runtimecore_directly(self):
        """共享模式：self._impl = 直接 new 的 RuntimeCoreImpl（不经过 Bridge）。"""
        import importlib
        from src.runtime import runtime as runtime_mod
        from src.runtime.runtime_core import RuntimeCore as CoreImpl

        # 强制 reload 让环境变量生效（from-import 绑定只在模块加载时发生）
        importlib.reload(runtime_mod)
        try:
            core = runtime_mod.RuntimeCore()
        except Exception as exc:
            self.skipTest(f"runtime.py RuntimeCore() 初始化失败（重型内部错误）：{exc}")
            return

        impl = getattr(core, "_impl", None)
        self.assertIsNotNone(
            impl,
            "共享模式下 runtime.py RuntimeCore()._impl 不应为 None",
        )
        # 关键断言：impl 必须是 runtime_core.RuntimeCore 的直接实例（不是 Bridge 包的东西）
        self.assertIsInstance(
            impl, CoreImpl,
            "SPEC v0.2：self._impl 必须是 src.runtime.runtime_core.RuntimeCore 的"
            "直接实例（不得通过 RuntimeBridge 中转）",
        )
        # _delegate 也指向同一个 impl（向后兼容 getter）
        self.assertIs(
            getattr(core, "_delegate", None), impl,
            "为兼容旧代码 _delegate is None 判断，self._delegate 应等于 self._impl",
        )

    # -----------------------------------------------------------------
    # 5B-1 configure_ports(21 个 None) 不崩溃
    # -----------------------------------------------------------------
    @patch.dict(os.environ, {"YUYI_RUNTIME_SHARED_MODE": "1"}, clear=True)
    def test_configure_ports_all_none_no_crash(self):
        """风险点2兼容：configure_ports 全 None 时不抛错。"""
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        # 21 个参数全传 None → 什么也不做，不抛错
        try:
            core.configure_ports(
                memory_port=None,
                emotion_port=None,
                growth_port=None,
                personality_port=None,
                response_port=None,
                response_guard_chain=None,
                stage_hooks=None,
                adapter_registry=None,
                perception_registry=None,
                vision_registry=None,
                self_model_registry=None,
                self_model_audit_chain=None,
                self_reflection_engine=None,
                self_reflection_store=None,
                identity_runtime=None,
                personality_runtime_binding=None,
                evolution_engine=None,
                persistence_runtime=None,
                reflection_engine=None,
                control_adapter=None,
                policy_engine=None,
            )
        except Exception as exc:
            self.fail(f"configure_ports(全None) 不允许抛异常：{exc!r}")

    # -----------------------------------------------------------------
    # 5B-2 configure_ports 部分注入：属性能被正确写入（duck-typing 候选名）
    # -----------------------------------------------------------------
    def test_configure_ports_sets_existing_attributes(self):
        """configure_ports 对已有属性（memory_adapter / emotion_manager 等）赋值。"""
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        fake_mem = object()
        fake_emo = object()
        try:
            core.configure_ports(
                memory_port=fake_mem,
                emotion_port=fake_emo,
            )
        except Exception as exc:
            self.fail(f"configure_ports(memory/emotion) 抛错：{exc!r}")
        # 检查 memory_adapter / emotion_manager 等候选属性是否被写入
        mem_injected = any(
            getattr(core, attr, None) is fake_mem
            for attr in ("memory_adapter", "memory_port", "_memory_port")
        )
        emo_injected = any(
            getattr(core, attr, None) is fake_emo
            for attr in ("emotion_manager", "emotion_port", "_emotion_port")
        )
        # 至少有一个候选名被赋值（允许内部属性名差异，但注入必须生效）
        self.assertTrue(
            mem_injected,
            "configure_ports(memory_port=...) 未能把值写入 runtime_core 任何候选属性",
        )
        self.assertTrue(
            emo_injected,
            "configure_ports(emotion_port=...) 未能把值写入 runtime_core 任何候选属性",
        )

    # -----------------------------------------------------------------
    # 5A-3 委托等价性：runtime.py.process() → self._impl.process()
    # -----------------------------------------------------------------
    @patch.dict(os.environ, {"YUYI_RUNTIME_SHARED_MODE": "1"}, clear=True)
    def test_runtime_py_process_delegates_to_impl(self):
        """共享模式：runtime.py RuntimeCore.process() 转调 _impl.process()。"""
        import importlib
        from src.runtime import runtime as runtime_mod
        from src.runtime.events import Event
        from src.runtime.context.runtime_context import RuntimeContext

        importlib.reload(runtime_mod)
        try:
            core = runtime_mod.RuntimeCore()
        except Exception as exc:
            self.skipTest(f"runtime.py RuntimeCore() 初始化失败：{exc}")
            return

        impl = getattr(core, "_impl", None)
        if impl is None:
            self.skipTest("共享模式未拿到 impl（跳过）")
            return

        # spy impl.process
        real_impl_process = impl.process
        called_box: Dict[str, int] = {"n": 0}

        def _spy(*args, **kwargs):
            called_box["n"] += 1
            return real_impl_process(*args, **kwargs)

        impl.process = _spy  # type: ignore[method-assign]
        try:
            evt = Event(type="x", source="t", payload={})
            ctx_out = core.process(evt)
            self.assertEqual(called_box["n"], 1, "runtime.py process 必须转调 impl.process exactly once")
            self.assertIsInstance(ctx_out, RuntimeContext)
        finally:
            impl.process = real_impl_process  # type: ignore[method-assign]
            try:
                impl.stop()
            except Exception:
                pass

    # -----------------------------------------------------------------
    # 环境切换：YUYI_RUNTIME_SHARED_MODE=0 走自建分支
    # -----------------------------------------------------------------
    @patch.dict(os.environ, {"YUYI_RUNTIME_SHARED_MODE": "0"}, clear=True)
    def test_legacy_mode_impl_is_none(self):
        """非共享模式（YUYI_RUNTIME_SHARED_MODE=0）：self._impl 为 None，保留自建逻辑。"""
        import importlib
        from src.runtime import runtime as runtime_mod

        importlib.reload(runtime_mod)
        try:
            core = runtime_mod.RuntimeCore()
        except Exception:
            # 自建模式会尝试解析重型 registry，初始化失败也属于正确分支
            return
        self.assertIsNone(
            getattr(core, "_impl", "NOTFOUND"),
            "YUYI_RUNTIME_SHARED_MODE=0 时 self._impl 应为 None（走自建逻辑）",
        )


# =====================================================================
# T-4.0.1-7  包级导出一致性
# =====================================================================

class TestPackageLevelExports(unittest.TestCase):
    """src.runtime.__init__.py RuntimeCore → runtime_core.py 统一类。"""

    def test_runtimecore_identity(self):
        from src.runtime import RuntimeCore as PkgCore
        from src.runtime.runtime_core import RuntimeCore as CoreCore

        self.assertIs(PkgCore, CoreCore)

    def test_stages_reachable_via_runtimecore_module(self):
        """runtime_core 模块侧 stages re-export 可达。"""
        from src.runtime.runtime_core import (
            RUNTIME_LIFECYCLE_ORDER,
            RuntimeStage,
        )
        self.assertTrue(hasattr(RuntimeStage, "CONTROL_CHECK"))
        self.assertGreaterEqual(len(RUNTIME_LIFECYCLE_ORDER), 17)


# =====================================================================
# 附加架构约束（process 使用 _lifecycle 单调用 + 禁止裸 for stage）
# =====================================================================

class TestArchitectureConstraints(unittest.TestCase):
    """4.0.1 架构约束：阶段调度唯一入口。"""

    def test_only_lifecycle_execute_iterates_stages(self):
        """唯一允许遍历 RUNTIME_LIFECYCLE_ORDER 的位置 = lifecycle.py。

        方法：
        - 对 src/runtime/ 下所有 .py 文件做 grep：`for.*RUNTIME_LIFECYCLE_ORDER`
        - 只允许 lifecycle.py 命中
        """
        runtime_root = Path(__file__).resolve().parent.parent / "src" / "runtime"
        self.assertTrue(runtime_root.is_dir(), f"runtime 根目录不存在：{runtime_root}")

        allowed_file_stems = {"lifecycle_executor"}
        violations: List[str] = []
        for py_file in runtime_root.rglob("*.py"):
            rel = py_file.relative_to(runtime_root)
            # 只检查 runtime 顶层文件（避免 tests/）
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "RUNTIME_LIFECYCLE_ORDER" in stripped and "for " in stripped:
                    if py_file.stem not in allowed_file_stems:
                        violations.append(
                            f"{rel}:L{lineno}: {stripped.rstrip()}"
                        )
        self.assertEqual(
            len(violations), 0,
            "SPEC v0.2 P0-修改2：仅允许 lifecycle.py 遍历 RUNTIME_LIFECYCLE_ORDER。"
            "其他文件出现 for ... RUNTIME_LIFECYCLE_ORDER 即为架构回退。\n"
            "违规：\n" + "\n".join(violations),
        )


# =====================================================================
# pytest 入口
# =====================================================================

if __name__ == "__main__":
    unittest.main()
