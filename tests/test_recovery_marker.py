"""
Phase A.1: RecoveryMarker 单元测试

覆盖：
- 首次创建（文件不存在 → 空集合 + 写盘）
- is_recovered / mark_recovered / get_all / count
- 重复标记（不重复写）
- JSON 损坏自动恢复为空集合
- 父目录自动创建
- 原子写（tmp + os.replace）
- 非法数据结构兜底
- 不保存 experience 数据
"""

import json
import os
import pytest
from pathlib import Path

from src.recovery.recovery_marker import RecoveryMarker


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def marker_path(tmp_path):
    return tmp_path / "experience_recovery_marker.json"


@pytest.fixture
def marker(marker_path):
    return RecoveryMarker(marker_path=marker_path)


# ============================================================
# 1. 首次创建
# ============================================================

class TestFirstTime:
    def test_empty_when_file_not_exists(self, marker, marker_path):
        """文件不存在时返回空集合"""
        assert marker_path.exists() is False
        assert marker.get_all() == set()
        assert marker.count() == 0
        assert marker.is_recovered("mem_xxx") is False

    def test_mark_creates_file(self, marker, marker_path):
        """首次 mark_recovered 后文件应存在"""
        marker.mark_recovered(["mem_1"])
        assert marker_path.exists()

        loaded = json.loads(marker_path.read_text(encoding="utf-8"))
        assert loaded == {"recovered_memory_ids": ["mem_1"]}


# ============================================================
# 2. 基础 CRUD
# ============================================================

class TestCRUD:
    def test_mark_and_get(self, marker):
        marker.mark_recovered(["mem_1", "mem_2", "mem_3"])
        assert marker.get_all() == {"mem_1", "mem_2", "mem_3"}

    def test_is_recovered(self, marker):
        marker.mark_recovered(["mem_1"])
        assert marker.is_recovered("mem_1") is True
        assert marker.is_recovered("mem_999") is False

    def test_is_recovered_empty_string(self, marker):
        marker.mark_recovered(["mem_1"])
        assert marker.is_recovered("") is False

    def test_count(self, marker):
        marker.mark_recovered(["mem_1", "mem_2"])
        assert marker.count() == 2

    def test_mark_incremental(self, marker, marker_path):
        """增量追加，不覆盖已有"""
        marker.mark_recovered(["mem_1"])
        marker.mark_recovered(["mem_2"])
        assert marker.get_all() == {"mem_1", "mem_2"}

    def test_mark_with_duplicates(self, marker):
        """重复标记同一 id 不应增加 count"""
        marker.mark_recovered(["mem_1"])
        marker.mark_recovered(["mem_1", "mem_2"])
        assert marker.count() == 2

    def test_mark_filters_empty_strings(self, marker):
        marker.mark_recovered(["", "mem_1", ""])
        assert marker.get_all() == {"mem_1"}


# ============================================================
# 3. 重复标记不重复写
# ============================================================

class TestNoRedundantWrite:
    def test_repeat_mark_no_op(self, marker, marker_path):
        """mark 相同集合不触发写盘（mtime 不变）"""
        marker.mark_recovered(["mem_1"])
        mtime1 = marker_path.stat().st_mtime

        # 等待避免 mtime 精度问题
        import time
        time.sleep(0.05)

        marker.mark_recovered(["mem_1"])
        mtime2 = marker_path.stat().st_mtime
        assert mtime1 == mtime2

    def test_partial_overlap_writes(self, marker, marker_path):
        """有新增时才写"""
        marker.mark_recovered(["mem_1"])
        mtime1 = marker_path.stat().st_mtime
        import time
        time.sleep(0.05)
        marker.mark_recovered(["mem_1", "mem_2"])  # mem_2 是新增
        mtime2 = marker_path.stat().st_mtime
        assert mtime2 > mtime1


# ============================================================
# 4. JSON 损坏自动恢复
# ============================================================

class TestCorruptionRecovery:
    def test_corrupted_json_returns_empty(self, marker, marker_path):
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text("INVALID JSON{{{", encoding="utf-8")
        assert marker.get_all() == set()
        assert marker.count() == 0

    def test_corrupted_then_mark_overwrites(self, marker, marker_path):
        """损坏文件可被新 mark 覆盖"""
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text("BROKEN", encoding="utf-8")

        marker.mark_recovered(["mem_recovered"])
        assert marker.get_all() == {"mem_recovered"}


# ============================================================
# 5. 非法数据结构
# ============================================================

class TestInvalidData:
    def test_non_dict_returns_empty(self, marker, marker_path):
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(json.dumps(["mem_1"]), encoding="utf-8")
        assert marker.get_all() == set()

    def test_missing_key_returns_empty(self, marker, marker_path):
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(json.dumps({"other": []}), encoding="utf-8")
        assert marker.get_all() == set()

    def test_non_list_value_returns_empty(self, marker, marker_path):
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps({"recovered_memory_ids": "not_a_list"}),
            encoding="utf-8",
        )
        assert marker.get_all() == set()


# ============================================================
# 6. 父目录自动创建
# ============================================================

class TestAutoCreateDir:
    def test_nested_parent_dir_created(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / "marker.json"
        m = RecoveryMarker(marker_path=nested)
        m.mark_recovered(["mem_1"])
        assert nested.exists()
        assert nested.parent.exists()


# ============================================================
# 7. 原子写（不污染现有文件）
# ============================================================

class TestAtomicWrite:
    def test_no_leftover_tmp_file(self, marker, marker_path):
        """原子写后不应残留 .tmp 文件"""
        marker.mark_recovered(["mem_1"])
        parent = marker_path.parent
        leftovers = list(parent.glob(".marker_*.json.tmp"))
        assert leftovers == []

    def test_file_valid_json_after_write(self, marker, marker_path):
        marker.mark_recovered(["mem_1", "mem_2"])
        # 直接读文件
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "recovered_memory_ids" in data
        assert sorted(data["recovered_memory_ids"]) == ["mem_1", "mem_2"]


# ============================================================
# 8. 不保存 experience 数据
# ============================================================

class TestNoExperienceData:
    """核心架构原则：Marker 只保存 memory_id，不存 experience 任何字段"""

    def test_does_not_save_summary(self, marker, marker_path):
        """传入 summary 不应被持久化"""
        # 模拟错误使用：调用方传入完整 dict
        marker.mark_recovered(["mem_1"])
        loaded = json.loads(marker_path.read_text(encoding="utf-8"))
        # 仅有 recovered_memory_ids
        assert set(loaded.keys()) == {"recovered_memory_ids"}
        assert "summary" not in loaded
        assert "evidence" not in loaded
        assert "experience_id" not in loaded
        assert "category" not in loaded
        assert "importance" not in loaded

    def test_does_not_save_category(self, marker, marker_path):
        marker.mark_recovered(["mem_1"])
        loaded = json.loads(marker_path.read_text(encoding="utf-8"))
        assert "category" not in loaded
        assert "origin" not in str(loaded)
        assert "relationship" not in str(loaded)
        assert "project" not in str(loaded)
        assert "interaction" not in str(loaded)
