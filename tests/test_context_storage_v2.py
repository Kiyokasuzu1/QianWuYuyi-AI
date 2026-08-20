# -*- coding: utf-8 -*-
"""
tests/test_context_storage_v2.py

P2.3-A.2.6 Phase 1 —— context_storage 的 RuntimeContext v2 支持测试。

覆盖:
    1. v1 load 不变        —— v1 信封文件仍走 lifecycle v1 from_dict 旧逻辑
    2. v2 roundtrip        —— v2 保存/恢复后七层字段一致
    3. schema_version 保留 —— v1 信封 "1.0" / v2 信封 "2.0"，load 双向保留
    4. 门禁不退化          —— 非法对象 save 照旧抛错；不支持信封照旧 None
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_storage(tmp_path):
    from src.runtime.context_storage import RuntimeContextStorage
    return RuntimeContextStorage(base_dir=str(tmp_path / "ctx_storage_v2"))


def _make_v2():
    """构造一个带七层内容的 v2 上下文。"""
    from src.runtime.request_context import (
        CognitiveSnapshot,
        RuntimeContext,
        StateSnapshots,
    )
    return RuntimeContext(
        session_id="s_v2_1",
        lifecycle_id="v2_boot_001",
        state="success",
        inputs={"user_message": "你好", "user_id": "123456"},
        outputs={"reply": "你好呀", "source": "runtime"},
        metadata={"k": "v"},
        cognitive=CognitiveSnapshot(
            memory_refs=["m_1"],
            retrieved_knowledge={"summary": "x"},
            reasoning_context={"note": "y"},
        ),
        state_snapshots=StateSnapshots(
            emotion={"dominant": "calm"},
            relationship={"level": 2},
            personality={"tone": "gentle"},
            growth={"stage": "steady"},
            self_model={"confidence": 0.8},
        ),
    )


# ============================================================
# 1. v1 load 不变
# ============================================================
class TestV1LoadUnchanged:
    def test_v1_roundtrip_returns_lifecycle_type(self, tmp_storage):
        """v1 上下文 save -> load 仍返回 lifecycle v1 类型（旧逻辑）。"""
        from src.runtime.lifecycle_context import RuntimeContext as V1
        v1 = V1(
            session_id="s1", lifecycle_id="old_001",
            inputs={"k": "v"}, metadata={"trace_id": "tr_1"},
        ).mark_success(outputs={"ok": True})
        tmp_storage.save(v1)
        loaded = tmp_storage.load("old_001")
        assert loaded is not None
        assert loaded.schema_version == "1.0"
        assert loaded.lifecycle_id == "old_001"
        assert loaded.session_id == "s1"
        assert loaded.state == "success"
        assert dict(loaded.outputs) == {"ok": True}
        # 信封仍为 1.0
        payload = json.loads((tmp_storage.base_dir / "old_001.json").read_text("utf-8"))
        assert payload["schema_version"] == "1.0"

    def test_v1_envelope_file_dispatch_unchanged(self, tmp_storage):
        """手工 v1 信封文件仍被 lifecycle v1 from_dict 解析。"""
        (tmp_storage.base_dir / "manual_001.json").write_text(json.dumps({
            "schema_version": "1.0",
            "saved_at": "2026-08-01T00:00:00Z",
            "context": {
                "session_id": "s_x",
                "lifecycle_id": "manual_001",
                "state": "failed",
                "error": "boom",
            },
        }), encoding="utf-8")
        loaded = tmp_storage.load("manual_001")
        assert loaded is not None
        assert loaded.schema_version == "1.0"
        assert loaded.state == "failed"
        assert loaded.error == "boom"


# ============================================================
# 2. v2 roundtrip
# ============================================================
class TestV2RoundTrip:
    def test_v2_save_load_returns_v2_type(self, tmp_storage):
        v2 = _make_v2()
        result = tmp_storage.save(v2)
        assert result["saved"] is True
        loaded = tmp_storage.load("v2_boot_001")
        assert loaded is not None
        assert loaded.schema_version == "2.0"
        # 七层均在
        for layer in ("identity", "request", "perception", "cognitive",
                      "state_snapshots", "mutations", "audit"):
            assert hasattr(loaded, layer), f"缺失 v2 层: {layer}"

    def test_v2_roundtrip_field_equality(self, tmp_storage):
        v2 = _make_v2()
        tmp_storage.save(v2)
        loaded = tmp_storage.load("v2_boot_001")
        assert loaded.to_dict() == v2.to_dict()

    def test_v2_roundtrip_cognitive_and_state_snapshots(self, tmp_storage):
        v2 = _make_v2()
        tmp_storage.save(v2)
        loaded = tmp_storage.load("v2_boot_001")
        assert loaded.cognitive.memory_refs == ["m_1"]
        assert loaded.cognitive.retrieved_knowledge == {"summary": "x"}
        assert loaded.state_snapshots.emotion == {"dominant": "calm"}
        assert loaded.state_snapshots.relationship == {"level": 2}
        assert loaded.state_snapshots.self_model == {"confidence": 0.8}
        assert loaded.outputs == {"reply": "你好呀", "source": "runtime"}

    def test_v2_and_v1_coexist_in_same_storage(self, tmp_storage):
        from src.runtime.lifecycle_context import RuntimeContext as V1
        v1 = V1(session_id="s1", lifecycle_id="mix_v1_001")
        v2 = _make_v2()
        tmp_storage.save(v1)
        tmp_storage.save(v2)
        assert set(tmp_storage.list_ids()) == {"mix_v1_001", "v2_boot_001"}
        l1 = tmp_storage.load("mix_v1_001")
        l2 = tmp_storage.load("v2_boot_001")
        assert l1.schema_version == "1.0"
        assert l2.schema_version == "2.0"


# ============================================================
# 3. schema_version 保留
# ============================================================
class TestSchemaVersionPreserved:
    def test_v2_envelope_is_2_0(self, tmp_storage):
        tmp_storage.save(_make_v2())
        payload = json.loads(
            (tmp_storage.base_dir / "v2_boot_001.json").read_text("utf-8")
        )
        assert payload["schema_version"] == "2.0"
        assert payload["context"]["schema_version"] == "2.0"

    def test_v1_envelope_still_1_0(self, tmp_storage):
        from src.runtime.lifecycle_context import RuntimeContext as V1
        tmp_storage.save(V1(session_id="s", lifecycle_id="env_001"))
        payload = json.loads(
            (tmp_storage.base_dir / "env_001.json").read_text("utf-8")
        )
        assert payload["schema_version"] == "1.0"

    def test_loaded_schema_version_preserved_both_directions(self, tmp_storage):
        from src.runtime.lifecycle_context import RuntimeContext as V1
        tmp_storage.save(V1(session_id="s", lifecycle_id="sv_001"))
        tmp_storage.save(_make_v2())
        assert tmp_storage.load("sv_001").schema_version == "1.0"
        assert tmp_storage.load("v2_boot_001").schema_version == "2.0"


# ============================================================
# 4. 门禁不退化
# ============================================================
class TestGatesUnchanged:
    def test_unsupported_envelope_still_none(self, tmp_storage):
        (tmp_storage.base_dir / "fut_001.json").write_text(json.dumps({
            "schema_version": "99.0",
            "saved_at": "2099-01-01T00:00:00Z",
            "context": {"session_id": "s", "lifecycle_id": "fut_001"},
        }), encoding="utf-8")
        assert tmp_storage.load("fut_001") is None

    def test_save_garbage_still_raises(self, tmp_storage):
        for bad in ("not a context", None, 123, {"schema_version": "2.0"}):
            with pytest.raises(Exception):
                tmp_storage.save(bad)  # type: ignore[arg-type]

    def test_load_v2_envelope_bad_context_dict_returns_none(self, tmp_storage):
        """v2 信封 + 非 dict context → None（不抛）。"""
        (tmp_storage.base_dir / "bad_v2_001.json").write_text(json.dumps({
            "schema_version": "2.0",
            "saved_at": "2026-08-01T00:00:00Z",
            "context": "not a dict",
        }), encoding="utf-8")
        assert tmp_storage.load("bad_v2_001") is None
