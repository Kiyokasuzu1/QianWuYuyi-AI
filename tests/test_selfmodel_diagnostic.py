# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_diagnostic.py

Phase 3.5.2 Step 2: SelfModel 诊断模块单元测试

目标：
- 验证 src/admin/selfmodel_diagnostic.py 在以下三种场景下行为正确：
  1. 空目录（未初始化）
  2. 有数据（beliefs/history/reflection 三个文件均有合法记录）
  3. 文件损坏（部分行 JSON 非法、空行、缺字段等）

约束：
- 不依赖 RuntimeCore / RuntimeBridge / Orchestrator
- 不依赖 Growth / Personality 写链路
- 使用临时目录，避免污染 data/
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

# ============================================================
# 路径设置
# ============================================================

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 反依赖断言
# ============================================================

FORBIDDEN_IMPORTS = {
    "src.runtime.runtime_core",
    "src.runtime.runtime_bridge",
    "src.orchestrator",
    "src.growth",
}


@pytest.fixture(autouse=True)
def _verify_no_runtime_dependency():
    """运行测试时确认未引入 runtime/growth 写链路。"""
    yield
    # 不强制 fail，只作为 hook；保留供未来严格化


# ============================================================
# 临时目录 fixture
# ============================================================

@pytest.fixture
def temp_data_dir():
    """提供独立临时目录作为 SelfModel 数据目录。"""
    tmp = Path(tempfile.mkdtemp(prefix="phase_3_5_2_diag_"))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# JSONL 写入工具
# ============================================================

def _write_jsonl(path: Path, items: List[Dict[str, Any]]) -> None:
    """将 list[dict] 写入 JSONL 文件（一行一条）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


# ============================================================
# T1: 空目录状态
# ============================================================

class TestDiagnosticEmptyDirectory:
    """空目录（未初始化）时诊断应正确报告全 0。"""

    def test_empty_directory_returns_zero_counts(self, temp_data_dir):
        """空目录：initialized=True, persistence_available=False, counts=0。"""
        from src.admin.selfmodel_diagnostic import SelfModelDiagnostic
        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        report = diag.run()

        # initialized：诊断模块已加载 → 总是 True
        assert report["initialized"] is True, \
            "诊断模块已就绪 → initialized=True（与数据目录是否存在无关）"
        assert report["persistence_available"] is False, \
            "空目录无任何 JSONL → persistence_available=False"
        assert report["data_directory"] == str(temp_data_dir)
        assert report["files"]["beliefs_count"] == 0
        assert report["files"]["history_count"] == 0
        assert report["files"]["reflection_count"] == 0
        assert report["runtime_adapter_connected"] is False

    def test_nonexistent_directory_returns_safe_default(self, tmp_path):
        """不存在目录：模块仍能返回安全 fallback，不崩。"""
        from src.admin.selfmodel_diagnostic import SelfModelDiagnostic
        nonexistent = tmp_path / "does_not_exist"
        diag = SelfModelDiagnostic(data_dir=str(nonexistent))
        report = diag.run()
        # 情况1：目录不存在 → initialized=true, persistence_available=false, counts=0
        assert report["initialized"] is True, \
            "诊断模块已就绪 → 即便目录不存在 initialized 也为 True"
        assert report["persistence_available"] is False
        assert report["files"]["beliefs_count"] == 0
        assert report["files"]["history_count"] == 0
        assert report["files"]["reflection_count"] == 0
        assert report["runtime_adapter_connected"] is False
        # 不应抛异常；结果必须是合法 dict
        assert isinstance(report, dict)

    def test_diagnose_module_function(self, temp_data_dir):
        """模块级 diagnose() 便捷函数。"""
        from src.admin.selfmodel_diagnostic import diagnose
        report = diagnose(data_dir=str(temp_data_dir))
        assert report["initialized"] is True
        assert report["files"]["beliefs_count"] == 0


# ============================================================
# T2: 有数据状态
# ============================================================

class TestDiagnosticWithData:
    """写入数据后，诊断应正确报告各文件记录数。"""

    def test_with_three_files_full_data(self, temp_data_dir):
        from src.admin.selfmodel_diagnostic import (
            SelfModelDiagnostic,
            BELIEFS_FILENAME,
            HISTORY_FILENAME,
            REFLECTION_FILENAME,
        )

        # 写 3 个 JSONL 文件
        beliefs = [
            {"belief_id": "b-1", "content": "c1", "confidence": 0.9},
            {"belief_id": "b-2", "content": "c2", "confidence": 0.8},
            {"belief_id": "b-3", "content": "c3", "confidence": 0.7},
        ]
        history = [
            {"event_id": "e-1", "event_type": "pcr_applied", "summary": "s1"},
            {"event_id": "e-2", "event_type": "pcr_applied", "summary": "s2"},
        ]
        reflections = [
            {"note_id": "n-1", "content": "r1", "reflection_type": "growth"},
        ]
        _write_jsonl(temp_data_dir / BELIEFS_FILENAME, beliefs)
        _write_jsonl(temp_data_dir / HISTORY_FILENAME, history)
        _write_jsonl(temp_data_dir / REFLECTION_FILENAME, reflections)

        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        report = diag.run()

        assert report["initialized"] is True
        assert report["persistence_available"] is True, \
            "三文件均存在 → persistence_available=True"
        assert report["data_directory"] == str(temp_data_dir)
        assert report["files"]["beliefs_count"] == 3
        assert report["files"]["history_count"] == 2
        assert report["files"]["reflection_count"] == 1
        assert report["runtime_adapter_connected"] is False

    def test_with_only_one_file(self, temp_data_dir):
        """只写一个文件时，persistence_available 应为 True。"""
        from src.admin.selfmodel_diagnostic import (
            SelfModelDiagnostic,
            BELIEFS_FILENAME,
        )
        beliefs = [
            {"belief_id": "b-only-1", "content": "x", "confidence": 0.5},
        ]
        _write_jsonl(temp_data_dir / BELIEFS_FILENAME, beliefs)

        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        report = diag.run()

        assert report["persistence_available"] is True
        assert report["files"]["beliefs_count"] == 1
        assert report["files"]["history_count"] == 0
        assert report["files"]["reflection_count"] == 0

    def test_with_meta_json(self, temp_data_dir):
        """meta.json 存在不影响基础字段，但 run_extended 应有 meta 摘要。"""
        from src.admin.selfmodel_diagnostic import (
            SelfModelDiagnostic,
            BELIEFS_FILENAME,
            META_FILENAME,
        )
        _write_jsonl(
            temp_data_dir / BELIEFS_FILENAME,
            [{"belief_id": "b-1", "content": "c"}],
        )
        meta = {
            "version": "1.0",
            "last_updated": "2026-07-30T00:00:00Z",
            "beliefs": {"count": 1, "last_saved": "2026-07-30T00:00:00Z"},
        }
        with open(temp_data_dir / META_FILENAME, "w", encoding="utf-8") as f:
            json.dump(meta, f)

        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        ext = diag.run_extended()

        assert ext["files"]["beliefs_count"] == 1
        assert "files_meta" in ext
        assert ext["files_meta"][BELIEFS_FILENAME]["exists"] is True
        assert ext["files_meta"][META_FILENAME]["exists"] is True
        assert "meta_summary" in ext
        assert ext["meta_summary"]["version"] == "1.0"


# ============================================================
# T3: 文件损坏容错
# ============================================================

class TestDiagnosticCorruptionTolerance:
    """文件损坏、空行、缺字段等异常情况下，诊断不应崩溃。"""

    def test_jsonl_with_corrupted_lines(self, temp_data_dir):
        """部分行 JSON 非法时，应跳过坏行只数好行。"""
        from src.admin.selfmodel_diagnostic import (
            SelfModelDiagnostic,
            BELIEFS_FILENAME,
        )
        path = temp_data_dir / BELIEFS_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"belief_id": "good-1", "content": "c1"}) + "\n")
            f.write("{this is not valid json\n")  # 坏行
            f.write("\n")                          # 空行
            f.write(json.dumps({"belief_id": "good-2"}) + "\n")
            f.write("[1, 2, 3]\n")                 # 非 dict 行
            f.write("\"plain string\"\n")           # 非 dict 行
            f.write(json.dumps({"belief_id": "good-3"}) + "\n")
            f.write('{"unterminated": ')            # 坏行（不完整）

        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        report = diag.run()

        # 3 条合法 dict 记录（good-1, good-2, good-3）
        assert report["files"]["beliefs_count"] == 3
        assert report["persistence_available"] is True

    def test_jsonl_all_corrupted_does_not_crash(self, temp_data_dir):
        """整文件全是坏行时，诊断不崩，返回 0。"""
        from src.admin.selfmodel_diagnostic import (
            SelfModelDiagnostic,
            HISTORY_FILENAME,
        )
        path = temp_data_dir / HISTORY_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("not json at all\n")
            f.write("}{ broken\n")
            f.write("\n")

        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        report = diag.run()

        assert report["files"]["history_count"] == 0
        # 文件存在但全是坏行：persistence_available 应仍为 True（文件存在）
        assert report["persistence_available"] is True

    def test_jsonl_empty_file(self, temp_data_dir):
        """空文件（0 字节）应返回 0，不崩。"""
        from src.admin.selfmodel_diagnostic import (
            SelfModelDiagnostic,
            REFLECTION_FILENAME,
        )
        path = temp_data_dir / REFLECTION_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        report = diag.run()
        assert report["files"]["reflection_count"] == 0
        assert report["persistence_available"] is True  # 文件存在

    def test_missing_key_fields_still_counted(self, temp_data_dir):
        """缺主键字段（belief_id 等）仍应被计数（诊断阶段不严格 schema）。"""
        from src.admin.selfmodel_diagnostic import (
            SelfModelDiagnostic,
            BELIEFS_FILENAME,
        )
        _write_jsonl(
            temp_data_dir / BELIEFS_FILENAME,
            [
                {"content": "no belief_id field"},
                {"belief_id": "has-key", "content": "ok"},
            ],
        )
        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        report = diag.run()
        # 两条都应被计为合法记录
        assert report["files"]["beliefs_count"] == 2

    def test_mixed_corruption_across_files(self, temp_data_dir):
        """多文件同时损坏时的混合场景。"""
        from src.admin.selfmodel_diagnostic import (
            SelfModelDiagnostic,
            BELIEFS_FILENAME,
            HISTORY_FILENAME,
            REFLECTION_FILENAME,
        )
        # beliefs 正常
        _write_jsonl(
            temp_data_dir / BELIEFS_FILENAME,
            [{"belief_id": "b1"}, {"belief_id": "b2"}],
        )
        # history 一半坏
        h_path = temp_data_dir / HISTORY_FILENAME
        with open(h_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"event_id": "e1"}) + "\n")
            f.write("bad line\n")
            f.write(json.dumps({"event_id": "e2"}) + "\n")
        # reflection 全部坏
        r_path = temp_data_dir / REFLECTION_FILENAME
        with open(r_path, "w", encoding="utf-8") as f:
            f.write("not json\n")
            f.write("\n")

        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        report = diag.run()

        assert report["files"]["beliefs_count"] == 2
        assert report["files"]["history_count"] == 2
        assert report["files"]["reflection_count"] == 0
        assert report["persistence_available"] is True
        assert report["runtime_adapter_connected"] is False


# ============================================================
# T4: 接口契约 / 输出结构
# ============================================================

class TestDiagnosticContract:
    """诊断输出结构必须严格符合契约。"""

    def test_required_fields_present(self, temp_data_dir):
        from src.admin.selfmodel_diagnostic import SelfModelDiagnostic
        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        report = diag.run()
        # 顶层必填字段
        for key in (
            "initialized",
            "persistence_available",
            "data_directory",
            "files",
            "runtime_adapter_connected",
        ):
            assert key in report, f"report 缺字段: {key}"
        # files 子字段
        for key in ("beliefs_count", "history_count", "reflection_count"):
            assert key in report["files"], f"files 缺字段: {key}"

    def test_runtime_adapter_connected_always_false(self, temp_data_dir):
        """本模块不接 Runtime；runtime_adapter_connected 必须恒为 False。"""
        from src.admin.selfmodel_diagnostic import SelfModelDiagnostic
        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        # 空目录
        assert diag.run()["runtime_adapter_connected"] is False
        # 有数据
        _write_jsonl(
            temp_data_dir / "beliefs.jsonl",
            [{"belief_id": "b1"}],
        )
        assert diag.run()["runtime_adapter_connected"] is False
        # 损坏
        (temp_data_dir / "history.jsonl").write_text("garbage\n", encoding="utf-8")
        assert diag.run()["runtime_adapter_connected"] is False

    def test_data_directory_is_absolute_string(self, temp_data_dir):
        """data_directory 字段应为字符串（绝对路径）。"""
        from src.admin.selfmodel_diagnostic import SelfModelDiagnostic
        diag = SelfModelDiagnostic(data_dir=str(temp_data_dir))
        report = diag.run()
        assert isinstance(report["data_directory"], str)
        assert report["data_directory"] == str(temp_data_dir)

    def test_default_data_dir(self, monkeypatch):
        """data_dir=None 时使用默认 data/self_model。"""
        from src.admin import selfmodel_diagnostic as diag_mod
        diag = diag_mod.SelfModelDiagnostic(data_dir=None)
        report = diag.run()
        # 默认路径为相对路径
        assert "data" in report["data_directory"]
        assert "self_model" in report["data_directory"]


# ============================================================
# T5: 确认无 Runtime / Orchestrator / Growth 依赖
# ============================================================

class TestDiagnosticNoRuntimeDependency:
    """
    诊断模块不得 import 以下任何模块：
    - src.runtime.runtime_core
    - src.runtime.runtime_bridge
    - src.orchestrator
    - src.growth（任意子模块）
    - src.personality（任意子模块；只读诊断也应不耦合 personality 写代码）

    说明：仅检测 import 语句 / 顶层模块引用，不检测 docstring/注释。
    """

    @staticmethod
    def _find_imports(text: str) -> List[str]:
        """提取所有 import 形式语句（包括 from...import / import ...）。"""
        import re
        out: List[str] = []
        # 1) from X import Y
        for m in re.finditer(r"^\s*from\s+([\w.]+)\s+import\s+", text, re.MULTILINE):
            out.append(("from", m.group(1)))
        # 2) import X[.Y[.Z]]
        for m in re.finditer(r"^\s*import\s+([\w.]+)", text, re.MULTILINE):
            out.append(("import", m.group(1)))
        return out

    def test_module_does_not_import_runtime_core(self):
        """selfmodel_diagnostic.py 不得 import runtime_core。"""
        from src.admin import selfmodel_diagnostic as diag_mod
        text = Path(diag_mod.__file__).read_text(encoding="utf-8")
        imports = self._find_imports(text)
        for kind, mod in imports:
            assert "runtime_core" not in mod, (
                f"selfmodel_diagnostic.py 不应 import runtime_core（发现 {kind} {mod!r}）"
            )

    def test_module_does_not_import_runtime_bridge(self):
        """selfmodel_diagnostic.py 不得 import runtime_bridge。"""
        from src.admin import selfmodel_diagnostic as diag_mod
        text = Path(diag_mod.__file__).read_text(encoding="utf-8")
        imports = self._find_imports(text)
        for kind, mod in imports:
            assert "runtime_bridge" not in mod, (
                f"selfmodel_diagnostic.py 不应 import runtime_bridge（发现 {kind} {mod!r}）"
            )

    def test_module_does_not_import_orchestrator(self):
        """selfmodel_diagnostic.py 不得 import orchestrator。"""
        from src.admin import selfmodel_diagnostic as diag_mod
        text = Path(diag_mod.__file__).read_text(encoding="utf-8")
        imports = self._find_imports(text)
        for kind, mod in imports:
            assert "orchestrator" not in mod, (
                f"selfmodel_diagnostic.py 不应 import orchestrator（发现 {kind} {mod!r}）"
            )

    def test_module_does_not_import_growth(self):
        """selfmodel_diagnostic.py 不得 import growth 系统。"""
        from src.admin import selfmodel_diagnostic as diag_mod
        text = Path(diag_mod.__file__).read_text(encoding="utf-8")
        imports = self._find_imports(text)
        for kind, mod in imports:
            assert not mod.startswith("src.growth"), (
                f"selfmodel_diagnostic.py 不应 import growth 系统（发现 {kind} {mod!r}）"
            )

    def test_module_does_not_import_personality(self):
        """selfmodel_diagnostic.py 不得 import personality 系统。"""
        from src.admin import selfmodel_diagnostic as diag_mod
        text = Path(diag_mod.__file__).read_text(encoding="utf-8")
        imports = self._find_imports(text)
        for kind, mod in imports:
            assert not mod.startswith("src.personality"), (
                f"selfmodel_diagnostic.py 不应 import personality 系统（发现 {kind} {mod!r}）"
            )

    def test_importing_diagnostic_does_not_load_runtime_modules(self):
        """运行诊断后 sys.modules 不应含有 runtime_core/bridge/orchestrator。"""
        import sys
        for mod in list(sys.modules.keys()):
            if mod in (
                "src.runtime.runtime_core",
                "src.runtime.runtime_bridge",
                "src.orchestrator",
            ):
                del sys.modules[mod]

        from src.admin.selfmodel_diagnostic import diagnose
        diagnose(data_dir="data/_unused_for_dep_test_")

        loaded = set(sys.modules.keys())
        for forbidden in (
            "src.runtime.runtime_core",
            "src.runtime.runtime_bridge",
            "src.orchestrator",
        ):
            assert forbidden not in loaded, (
                f"导入并运行 diagnose() 时不应加载 {forbidden}"
            )


# ============================================================
# T6: 端到端真实目录烟测（data/self_model）
# ============================================================

class TestDiagnosticRealDataDir:
    """对项目真实 data/self_model 目录的端到端诊断。"""

    def test_real_data_self_model_dir(self):
        from src.admin.selfmodel_diagnostic import diagnose
        report = diagnose(data_dir="data/self_model")
        assert isinstance(report, dict)
        assert "initialized" in report
        assert "persistence_available" in report
        assert "data_directory" in report
        assert "files" in report
        assert "runtime_adapter_connected" in report
        assert report["runtime_adapter_connected"] is False
