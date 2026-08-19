# -*- coding: utf-8 -*-
"""
tests/test_runtime_context_storage.py

Phase 6.1 —— RuntimeContext Persistence Adapter 完整测试。

覆盖:
    1. TestSave (4)              —— save 成功 / 文件创建 / dict 正确 / context 不变
    2. TestLoad (4)              —— 恢复 / 不存在返回 None / 非法 JSON / 未知字段忽略
    3. TestRoundTrip (3)         —— save -> load 字段一致
    4. TestAtomicWrite (3)       —— tmp 文件使用 / 失败恢复
    5. TestDelete (3)            —— 删除存在 / 删除不存在 / 幂等
    6. TestExists (2)            —— 存在 / 不存在
    7. TestIsolation (4)         —— 零业务 import / 零 LLM / 仅 stdlib
    8. TestSchema (3)            —— schema 保留 / 不兼容 / 默认
    9. TestSecurity (5)          —— path traversal / 非法 id / 长度
    10. TestList (1)             —— list_ids
    11. TestEdge (3)             —— 边界

合计: >= 35 tests
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def tmp_storage(tmp_path):
    """使用 tmp_path 的 storage 实例。"""
    from src.runtime.context_storage import RuntimeContextStorage
    return RuntimeContextStorage(base_dir=str(tmp_path / "ctx_storage"))


@pytest.fixture
def success_context():
    """success 状态的 RuntimeContext(带 outputs / metadata / error=None)。"""
    from src.runtime.lifecycle_context import RuntimeContext
    c = RuntimeContext(
        session_id="s_storage_1",
        lifecycle_id="boot_001",
        inputs={"k": "v"},
        metadata={"trace_id": "tr_1", "host": "test"},
    )
    return c.mark_success(outputs={"ok": True, "count": 3})


@pytest.fixture
def failed_context():
    """failed 状态的 RuntimeContext。"""
    from src.runtime.lifecycle_context import (
        RuntimeContext,
        LIFECYCLE_STATE_FAILED,
    )
    return RuntimeContext(
        session_id="s_fail",
        lifecycle_id="chat_session_002",
        state=LIFECYCLE_STATE_FAILED,
        error="some error",
    )


# ============================================================
# 1. TestSave
# ============================================================
class TestSave:
    def test_save_success(self, tmp_storage, success_context):
        """save 成功返回正确信封。"""
        result = tmp_storage.save(success_context)
        assert result["saved"] is True
        assert result["lifecycle_id"] == "boot_001"
        assert "path" in result
        assert "timestamp" in result
        assert result["timestamp"].endswith("Z")

    def test_save_creates_file(self, tmp_storage, success_context):
        """save 后文件被创建。"""
        result = tmp_storage.save(success_context)
        path = Path(result["path"])
        assert path.exists()
        assert path.is_file()

    def test_save_dict_correct(self, tmp_storage, success_context):
        """save 后 JSON 内容正确。"""
        result = tmp_storage.save(success_context)
        path = Path(result["path"])
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["schema_version"] == "1.0"
        assert "saved_at" in payload
        assert "context" in payload
        assert payload["context"]["lifecycle_id"] == "boot_001"
        assert payload["context"]["session_id"] == "s_storage_1"
        assert payload["context"]["state"] == "success"

    def test_save_does_not_modify_context(self, tmp_storage, success_context):
        """save 不修改 context。"""
        original_state = success_context.state
        original_outputs = copy.deepcopy(dict(success_context.outputs))
        original_lifecycle_id = success_context.lifecycle_id
        tmp_storage.save(success_context)
        assert success_context.state == original_state
        assert dict(success_context.outputs) == original_outputs
        assert success_context.lifecycle_id == original_lifecycle_id

    def test_save_returns_dict(self, tmp_storage, success_context):
        """save 返回值是 dict。"""
        result = tmp_storage.save(success_context)
        assert isinstance(result, dict)


# ============================================================
# 2. TestLoad
# ============================================================
class TestLoad:
    def test_load_success(self, tmp_storage, success_context):
        """load 成功恢复 context。"""
        tmp_storage.save(success_context)
        loaded = tmp_storage.load("boot_001")
        assert loaded is not None
        assert loaded.lifecycle_id == "boot_001"
        assert loaded.session_id == "s_storage_1"
        assert loaded.state == "success"

    def test_load_missing_returns_none(self, tmp_storage):
        """load 不存在返回 None。"""
        loaded = tmp_storage.load("nonexistent_xxx")
        assert loaded is None

    def test_load_invalid_json_returns_none(self, tmp_storage):
        """load 非法 JSON 返回 None(不抛)。"""
        # 手动写入非法 JSON
        path = tmp_storage.base_dir / "broken_001.json"
        path.write_text("{ this is not valid json", encoding="utf-8")
        loaded = tmp_storage.load("broken_001")
        assert loaded is None

    def test_load_unknown_fields_ignored(self, tmp_storage):
        """load 未知字段被 RuntimeContext.from_dict 自动忽略。"""
        # 写入带未知字段的合法 JSON
        path = tmp_storage.base_dir / "future_001.json"
        payload = {
            "schema_version": "1.0",
            "saved_at": "2026-08-01T00:00:00Z",
            "context": {
                "session_id": "s_x",
                "lifecycle_id": "future_001",
                "state": "success",
                "future_field_v2": "should be ignored",
                "another_unknown": {"nested": "value"},
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = tmp_storage.load("future_001")
        assert loaded is not None
        assert loaded.lifecycle_id == "future_001"
        # 未知字段不应出现在 ctx 上
        assert not hasattr(loaded, "future_field_v2")
        assert not hasattr(loaded, "another_unknown")

    def test_load_invalid_lifecycle_id_returns_none(self, tmp_storage):
        """load 非法 lifecycle_id 返回 None。"""
        loaded = tmp_storage.load("../etc/passwd")
        assert loaded is None

    def test_load_non_dict_payload(self, tmp_storage):
        """load 时 payload 不是 dict -> None。"""
        path = tmp_storage.base_dir / "weird_001.json"
        path.write_text(json.dumps("just a string"), encoding="utf-8")
        loaded = tmp_storage.load("weird_001")
        assert loaded is None

    def test_load_missing_context_field(self, tmp_storage):
        """payload.context 不是 dict -> None。"""
        path = tmp_storage.base_dir / "noctx_001.json"
        path.write_text(json.dumps({
            "schema_version": "1.0",
            "saved_at": "2026-08-01T00:00:00Z",
            "context": "not a dict",
        }), encoding="utf-8")
        loaded = tmp_storage.load("noctx_001")
        assert loaded is None


# ============================================================
# 3. TestRoundTrip
# ============================================================
class TestRoundTrip:
    def test_roundtrip_session_id(self, tmp_storage, success_context):
        """save -> load: session_id 一致。"""
        tmp_storage.save(success_context)
        loaded = tmp_storage.load("boot_001")
        assert loaded.session_id == success_context.session_id

    def test_roundtrip_lifecycle_id(self, tmp_storage, success_context):
        """save -> load: lifecycle_id 一致。"""
        tmp_storage.save(success_context)
        loaded = tmp_storage.load("boot_001")
        assert loaded.lifecycle_id == success_context.lifecycle_id

    def test_roundtrip_state(self, tmp_storage, success_context):
        """save -> load: state 一致。"""
        tmp_storage.save(success_context)
        loaded = tmp_storage.load("boot_001")
        assert loaded.state == success_context.state

    def test_roundtrip_outputs(self, tmp_storage, success_context):
        """save -> load: outputs 一致。"""
        tmp_storage.save(success_context)
        loaded = tmp_storage.load("boot_001")
        assert dict(loaded.outputs) == dict(success_context.outputs)

    def test_roundtrip_metadata(self, tmp_storage, success_context):
        """save -> load: metadata 一致。"""
        tmp_storage.save(success_context)
        loaded = tmp_storage.load("boot_001")
        assert dict(loaded.metadata) == dict(success_context.metadata)

    def test_roundtrip_failed_context(self, tmp_storage, failed_context):
        """failed context save -> load。"""
        tmp_storage.save(failed_context)
        loaded = tmp_storage.load("chat_session_002")
        assert loaded is not None
        assert loaded.state == "failed"
        assert loaded.error == "some error"


# ============================================================
# 4. TestAtomicWrite
# ============================================================
class TestAtomicWrite:
    def test_atomic_write_uses_tmp(self, tmp_storage, success_context, monkeypatch):
        """save 时使用 tmp 文件(通过 mkstemp 验证)。"""
        import src.runtime.context_storage as cs_mod
        from unittest.mock import patch

        called = {"n": 0}
        original = cs_mod.tempfile.mkstemp

        def counting_mkstemp(*args, **kwargs):
            called["n"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(cs_mod.tempfile, "mkstemp", counting_mkstemp)
        tmp_storage.save(success_context)
        assert called["n"] >= 1

    def test_atomic_write_uses_replace(self, tmp_storage, success_context, monkeypatch):
        """save 使用 os.replace 做原子 rename。"""
        import src.runtime.context_storage as cs_mod
        from unittest.mock import patch

        called = {"n": 0}
        original = cs_mod.os.replace

        def counting_replace(*args, **kwargs):
            called["n"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(cs_mod.os, "replace", counting_replace)
        tmp_storage.save(success_context)
        assert called["n"] >= 1

    def test_atomic_write_cleans_tmp_on_failure(
        self, tmp_storage, success_context, monkeypatch
    ):
        """写入失败时 tmp 文件被清理。"""
        import src.runtime.context_storage as cs_mod
        from unittest.mock import patch

        def broken_replace(*args, **kwargs):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(cs_mod.os, "replace", broken_replace)
        with pytest.raises(cs_mod.ContextStorageError):
            tmp_storage.save(success_context)
        # 检查没有残留 .tmp 文件
        tmp_files = list(tmp_storage.base_dir.glob(".boot_001.json.*.tmp"))
        assert len(tmp_files) == 0


# ============================================================
# 5. TestDelete
# ============================================================
class TestDelete:
    def test_delete_existing(self, tmp_storage, success_context):
        """删除已存在的 context。"""
        tmp_storage.save(success_context)
        assert tmp_storage.exists("boot_001")
        result = tmp_storage.delete("boot_001")
        assert result is True
        assert not tmp_storage.exists("boot_001")

    def test_delete_missing(self, tmp_storage):
        """删除不存在的 context(幂等,返回 True)。"""
        result = tmp_storage.delete("never_existed")
        assert result is True  # 幂等

    def test_delete_invalid_id(self, tmp_storage):
        """删除非法 id 返回 False。"""
        result = tmp_storage.delete("../etc/passwd")
        assert result is False

    def test_delete_then_load_returns_none(self, tmp_storage, success_context):
        """删除后 load 返回 None。"""
        tmp_storage.save(success_context)
        tmp_storage.delete("boot_001")
        loaded = tmp_storage.load("boot_001")
        assert loaded is None


# ============================================================
# 6. TestExists
# ============================================================
class TestExists:
    def test_exists_returns_true_after_save(self, tmp_storage, success_context):
        tmp_storage.save(success_context)
        assert tmp_storage.exists("boot_001") is True

    def test_exists_returns_false_for_missing(self, tmp_storage):
        assert tmp_storage.exists("nope") is False

    def test_exists_returns_false_for_invalid_id(self, tmp_storage):
        assert tmp_storage.exists("../escape") is False

    def test_exists_returns_false_for_tmp_files(self, tmp_storage):
        """tmp 残留文件不应被识别为已存在。"""
        # 创建一个 tmp 残留
        tmp_file = tmp_storage.base_dir / ".boot_001.json.abc.tmp"
        tmp_file.write_text("partial", encoding="utf-8")
        # exists("boot_001") 应返回 False (它看的是 .json 文件)
        assert tmp_storage.exists("boot_001") is False


# ============================================================
# 7. TestIsolation
# ============================================================
class TestIsolation:
    def test_storage_no_business_import(self):
        """context_storage.py 源码禁止 import 业务模块。"""
        from src.runtime import context_storage as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 仅检查真正的 import 语句
        import_lines = []
        for line in src.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("#")
                or stripped.startswith('"""')
                or stripped.startswith("'''")
            ):
                continue
            if stripped.startswith("from ") or stripped.startswith("import "):
                import_lines.append(stripped)
        joined = "\n".join(import_lines)
        forbidden = [
            "from src.memory", "import src.memory",
            "from src.emotion", "import src.emotion",
            "from src.growth", "import src.growth",
            "from src.personality", "import src.personality",
            "from src.relationship", "import src.relationship",
            "from src.llm", "import src.llm",
            "from src.events", "import src.events",
            "from src.audit", "import src.audit",
        ]
        for f in forbidden:
            assert f not in joined, f"禁止 import: {f}"

    def test_storage_no_llm_keywords(self):
        """context_storage.py 源码无 LLM 关键词。"""
        from src.runtime import context_storage as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        import_lines = []
        for line in src.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("#")
                or stripped.startswith('"""')
                or stripped.startswith("'''")
            ):
                continue
            if stripped.startswith("from ") or stripped.startswith("import "):
                import_lines.append(stripped)
        joined = "\n".join(import_lines).lower()
        forbidden = ["openai", "anthropic", "claude", "src.llm", "import llm"]
        for kw in forbidden:
            assert kw not in joined, f"禁止 LLM 关键词: {kw}"

    def test_storage_only_stdlib_imports(self):
        """storage 顶层 import 仅 stdlib。"""
        from src.runtime import context_storage as mod
        import re
        src = open(mod.__file__, "r", encoding="utf-8").read()
        imports = re.findall(
            r"^(?:from|import)\s+(\S+)", src, flags=re.MULTILINE
        )
        for name in imports:
            top = name.split(".")[0]
            assert top in {"__future__", "json", "logging", "os", "re",
                           "tempfile", "threading", "datetime", "pathlib",
                           "typing"}, \
                f"非 stdlib 顶层 import: {name}"

    def test_storage_does_not_import_eventhub(self):
        """context_storage.py 不引用任何 EventHub 符号(代码层,非注释/docstring)。"""
        import ast as _ast

        from src.runtime import context_storage as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()

        # 用 AST 提取所有真实 import / 引用名,避免被注释/docstring 干扰
        try:
            tree = _ast.parse(src)
        except Exception:  # pragma: no cover
            tree = None

        all_names = []
        if tree is not None:
            for node in _ast.walk(tree):
                if isinstance(node, _ast.Import):
                    for alias in node.names:
                        all_names.append(alias.name)
                elif isinstance(node, _ast.ImportFrom):
                    if node.module:
                        all_names.append(node.module)
                    for alias in node.names:
                        all_names.append(alias.name)
                elif isinstance(node, _ast.Name):
                    all_names.append(node.id)
                elif isinstance(node, _ast.Attribute):
                    # 拼接 a.b.c 的根名
                    cur = node
                    while isinstance(cur, _ast.Attribute):
                        cur = cur.value
                    if isinstance(cur, _ast.Name):
                        all_names.append(cur.id)

        code_text = "\n".join(all_names)
        forbidden_symbols = [
            "EventHub",
            "EventBus",
            "EventPublisher",
            "RuntimeEventPublisher",
            "publish_event",
            "EventSink",
        ]
        for sym in forbidden_symbols:
            assert sym not in code_text, f"代码层禁止引用: {sym}"


# ============================================================
# 8. TestSchema
# ============================================================
class TestSchema:
    def test_schema_version_preserved(self, tmp_storage, success_context):
        """save 后 schema_version=1.0 保留。"""
        result = tmp_storage.save(success_context)
        path = Path(result["path"])
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        assert payload["schema_version"] == "1.0"

    def test_incompatible_schema_returns_none(self, tmp_storage):
        """schema_version 不兼容 -> load 返回 None。"""
        path = tmp_storage.base_dir / "future_999.json"
        path.write_text(json.dumps({
            "schema_version": "99.0",  # 不支持的版本
            "saved_at": "2099-01-01T00:00:00Z",
            "context": {
                "session_id": "s",
                "lifecycle_id": "future_999",
            },
        }), encoding="utf-8")
        loaded = tmp_storage.load("future_999")
        assert loaded is None

    def test_missing_schema_version_returns_none(self, tmp_storage):
        """缺少 schema_version 字段 -> load 返回 None。"""
        path = tmp_storage.base_dir / "nover_001.json"
        path.write_text(json.dumps({
            "saved_at": "2026-08-01T00:00:00Z",
            "context": {
                "lifecycle_id": "nover_001",
            },
        }), encoding="utf-8")
        loaded = tmp_storage.load("nover_001")
        assert loaded is None


# ============================================================
# 9. TestSecurity
# ============================================================
class TestSecurity:
    def test_path_traversal_double_dot(self, tmp_storage, success_context):
        """path traversal: '..' 被拒绝。"""
        # 构造一个 name 含 .. 的 context(理论上不会发生,但应防御)
        from src.runtime.lifecycle_context import RuntimeContext
        bad = RuntimeContext(
            session_id="s", lifecycle_id="..",  # 非法
        )
        with pytest.raises(Exception):
            tmp_storage.save(bad)

    def test_path_traversal_slash_rejected(self, tmp_storage, success_context):
        """path traversal: '/' 被 _validate_lifecycle_id 拒绝。"""
        from src.runtime.lifecycle_context import RuntimeContext
        bad = RuntimeContext(
            session_id="s", lifecycle_id="a/b",
        )
        with pytest.raises(Exception):
            tmp_storage.save(bad)

    def test_path_traversal_backslash_rejected(self, tmp_storage):
        """load 时 '\\' 被拒绝。"""
        loaded = tmp_storage.load("..\\windows\\system32")
        assert loaded is None

    def test_invalid_lifecycle_id_special_chars(self, tmp_storage):
        """特殊字符 (@#$%^&*) 被拒绝。"""
        for bad_id in ["abc@", "id space", "id!", "id?", "id&"]:
            loaded = tmp_storage.load(bad_id)
            assert loaded is None

    def test_empty_lifecycle_id_rejected(self, tmp_storage):
        """空 lifecycle_id 被拒绝。"""
        loaded = tmp_storage.load("")
        assert loaded is None

    def test_too_long_lifecycle_id_rejected(self, tmp_storage):
        """超长 lifecycle_id 被拒绝。"""
        long_id = "a" * 300  # > MAX
        loaded = tmp_storage.load(long_id)
        assert loaded is None

    def test_storage_resolves_path_within_base(self, tmp_storage, success_context):
        """保存后路径严格在 base_dir 内。"""
        tmp_storage.save(success_context)
        path = tmp_storage.base_dir / "boot_001.json"
        assert path.exists()
        # 验证 path 解析后仍在 base_dir 内
        assert path.resolve().is_relative_to(tmp_storage.base_dir.resolve())


# ============================================================
# 10. TestList
# ============================================================
class TestList:
    def test_list_ids_empty(self, tmp_storage):
        """空 storage 列出空列表。"""
        assert tmp_storage.list_ids() == []

    def test_list_ids_returns_saved_ids(self, tmp_storage):
        """list_ids 返回所有已保存的 id。"""
        from src.runtime.lifecycle_context import RuntimeContext
        ctx_a = RuntimeContext(session_id="s1", lifecycle_id="alpha_001")
        ctx_b = RuntimeContext(session_id="s2", lifecycle_id="beta_002")
        ctx_c = RuntimeContext(session_id="s3", lifecycle_id="gamma_003")
        tmp_storage.save(ctx_a)
        tmp_storage.save(ctx_b)
        tmp_storage.save(ctx_c)
        ids = tmp_storage.list_ids()
        assert set(ids) == {"alpha_001", "beta_002", "gamma_003"}


# ============================================================
# 11. TestEdge
# ============================================================
class TestEdge:
    def test_save_with_empty_outputs(self, tmp_storage):
        """空 outputs 也能保存。"""
        from src.runtime.lifecycle_context import RuntimeContext
        c = RuntimeContext(session_id="s", lifecycle_id="empty_out_001")
        result = tmp_storage.save(c)
        assert result["saved"] is True
        loaded = tmp_storage.load("empty_out_001")
        assert loaded is not None
        assert loaded.outputs == {}

    def test_save_with_non_serializable_outputs_graceful(
        self, tmp_storage, success_context
    ):
        """context 含 default=str 的 fallback(JSON 序列化失败时)。"""
        # 制造一个含 datetime 的 outputs (默认 str fallback)
        from datetime import datetime
        c = success_context.with_update(
            outputs={"ts": datetime(2026, 8, 1, 12, 0, 0)}
        )
        # 应该能保存(因为 default=str)
        result = tmp_storage.save(c)
        assert result["saved"] is True

    def test_invalid_context_to_save(self, tmp_storage):
        """save 非法对象抛错。"""
        with pytest.raises(Exception):
            tmp_storage.save("not a context")  # type: ignore[arg-type]
        with pytest.raises(Exception):
            tmp_storage.save(None)  # type: ignore[arg-type]
        with pytest.raises(Exception):
            tmp_storage.save(123)  # type: ignore[arg-type]

    def test_concurrent_save_does_not_corrupt(self, tmp_storage):
        """并发 save 不会损坏(通过 RLock)。"""
        import threading
        from src.runtime.lifecycle_context import RuntimeContext

        errors = []

        def worker(i):
            try:
                c = RuntimeContext(
                    session_id=f"s_{i}",
                    lifecycle_id=f"concurrent_{i:03d}",
                )
                tmp_storage.save(c)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        ids = tmp_storage.list_ids()
        assert len(ids) == 10
