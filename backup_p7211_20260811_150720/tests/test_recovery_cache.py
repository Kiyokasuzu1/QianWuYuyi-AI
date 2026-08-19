"""
Phase A.1: ExperienceCache 单元测试

覆盖：
- exists / load / save / clear 接口
- 字段过滤（_source_memory_id / confidence / related_dimensions 等禁止字段）
- 字段集合严格性（仅 6 个公开字段）
- 父目录自动创建
- 原子写（无残留 tmp）
- JSON 损坏兜底
- 重启持久化（跨实例）
- 边界：非 list / 非 dict 元素
"""

import json
import pytest
from pathlib import Path

from src.recovery.experience_cache import (
    ExperienceCache,
    ALLOWED_FIELDS,
    FORBIDDEN_FIELDS,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def cache_path(tmp_path):
    return tmp_path / "historical_experience_cache.json"


@pytest.fixture
def cache(cache_path):
    return ExperienceCache(cache_path=cache_path)


def make_valid_experience(exp_id: str = "exp_1", category: str = "origin") -> dict:
    """构造一个合法的 6 字段 experience"""
    return {
        "experience_id": exp_id,
        "category": category,
        "summary": "羽依由清夏铃创建",
        "evidence": "2026-07-22 清夏铃开始创建羽依项目",
        "timestamp": "2026-07-22T10:00:00",
        "importance": 0.95,
    }


# ============================================================
# 1. exists / load 基础
# ============================================================

class TestExists:
    def test_cache_not_exists_returns_empty(self, cache, cache_path):
        """不存在文件 load() == []"""
        assert cache_path.exists() is False
        assert cache.exists() is False
        assert cache.load() == []

    def test_exists_after_save(self, cache):
        cache.save([make_valid_experience()])
        assert cache.exists() is True


# ============================================================
# 2. save + load 闭环
# ============================================================

class TestSaveAndLoad:
    def test_save_and_load(self, cache):
        """save 后重新实例化可以读取"""
        exps = [
            make_valid_experience("exp_1", "origin"),
            make_valid_experience("exp_2", "relationship"),
        ]
        cache.save(exps)
        loaded = cache.load()
        assert len(loaded) == 2
        assert loaded[0]["experience_id"] == "exp_1"
        assert loaded[1]["experience_id"] == "exp_2"

    def test_save_empty_list(self, cache):
        """保存空列表也合法"""
        cache.save([])
        assert cache.load() == []

    def test_round_trip_preserves_fields(self, cache):
        original = make_valid_experience()
        cache.save([original])
        loaded = cache.load()
        for key in ALLOWED_FIELDS:
            assert loaded[0][key] == original[key]


# ============================================================
# 3. 字段过滤（核心）
# ============================================================

class TestFieldFiltering:
    def test_field_filtering(self, cache, cache_path):
        """保存前过滤非法字段"""
        dirty = {
            "_source_memory_id": "mem_xxx",
            "experience_id": "exp_1",
            "category": "origin",
            "summary": "羽依诞生",
            "evidence": "...",
            "timestamp": "...",
            "importance": 0.9,
            "confidence": 0.8,           # 禁止
            "related_dimensions": [],    # 禁止
            "source_memory_id": "mem_xxx",  # 禁止
            "trigger_events": ["e1"],   # 禁止（GrowthRecord）
            "changes": {},               # 禁止（GrowthRecord）
        }
        cache.save([dirty])
        loaded = cache.load()
        assert len(loaded) == 1
        item = loaded[0]
        # 禁止字段不应出现
        assert "_source_memory_id" not in item
        assert "source_memory_id" not in item
        assert "confidence" not in item
        assert "related_dimensions" not in item
        assert "trigger_events" not in item
        assert "changes" not in item
        # 公开字段保留
        assert item["experience_id"] == "exp_1"
        assert item["category"] == "origin"

    def test_only_public_fields_saved(self, cache, cache_path):
        """读取 json 文件原文，字段集合严格等于 ALLOWED_FIELDS"""
        cache.save([make_valid_experience()])
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        assert isinstance(raw, list)
        assert len(raw) == 1
        assert set(raw[0].keys()) == ALLOWED_FIELDS

    def test_save_with_extra_fields_only_keeps_allowed(self, cache, cache_path):
        """输入含大量额外字段，输出只保留 6 个"""
        big = make_valid_experience()
        big.update({
            "extra1": "x",
            "extra2": 123,
            "_internal": "y",
            "growth_record": {"x": 1},
        })
        cache.save([big])
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        assert set(raw[0].keys()) == ALLOWED_FIELDS


# ============================================================
# 4. JSON 损坏兜底
# ============================================================

class TestCorruption:
    def test_corrupted_json_returns_empty(self, cache, cache_path):
        """损坏 JSON 不抛异常，返回 []"""
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("INVALID JSON{{{", encoding="utf-8")
        assert cache.load() == []

    def test_non_list_returns_empty(self, cache, cache_path):
        """非 list 顶层结构返回 []"""
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"not": "list"}), encoding="utf-8")
        assert cache.load() == []

    def test_non_dict_items_filtered(self, cache, cache_path):
        """list 中非 dict 元素被过滤"""
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(["string_item", 123, None, make_valid_experience()]),
            encoding="utf-8",
        )
        loaded = cache.load()
        assert len(loaded) == 1
        assert loaded[0]["experience_id"] == "exp_1"

    def test_missing_required_fields_filtered(self, cache, cache_path):
        """缺 experience_id 或 category 的元素被过滤"""
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        bad = [
            {"category": "origin"},  # 缺 experience_id
            {"experience_id": "x"},   # 缺 category
            make_valid_experience(),  # 合法
        ]
        cache_path.write_text(json.dumps(bad), encoding="utf-8")
        loaded = cache.load()
        assert len(loaded) == 1
        assert loaded[0]["experience_id"] == "exp_1"


# ============================================================
# 5. 父目录自动创建
# ============================================================

class TestAutoCreateDir:
    def test_parent_directory_created(self, tmp_path):
        """不存在 data/recovery/ 时 save 后自动创建"""
        nested = tmp_path / "a" / "b" / "c" / "cache.json"
        assert nested.parent.exists() is False
        c = ExperienceCache(cache_path=nested)
        c.save([make_valid_experience()])
        assert nested.exists()
        assert nested.parent.exists()

    def test_deeply_nested_path(self, tmp_path):
        deep = tmp_path / "x" / "y" / "z" / "deep_cache.json"
        c = ExperienceCache(cache_path=deep)
        c.save([make_valid_experience()])
        assert c.load()[0]["experience_id"] == "exp_1"


# ============================================================
# 6. 原子写
# ============================================================

class TestAtomicWrite:
    def test_atomic_write_no_residual_tmp(self, cache, cache_path):
        """保存后没有残留临时文件"""
        cache.save([make_valid_experience()])
        parent = cache_path.parent
        leftovers = list(parent.glob(".cache_*.json.tmp"))
        assert leftovers == []

    def test_atomic_write_overwrites_existing(self, cache, cache_path):
        """原子写能正确覆盖已有文件"""
        cache.save([make_valid_experience("exp_old")])
        cache.save([make_valid_experience("exp_new")])
        loaded = cache.load()
        assert len(loaded) == 1
        assert loaded[0]["experience_id"] == "exp_new"

    def test_save_creates_valid_json(self, cache, cache_path):
        """保存后文件是合法 JSON"""
        cache.save([make_valid_experience(), make_valid_experience("exp_2", "project")])
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 2


# ============================================================
# 7. clear
# ============================================================

class TestClear:
    def test_clear(self, cache, cache_path):
        """clear 后 exists() == False"""
        cache.save([make_valid_experience()])
        assert cache.exists() is True
        cache.clear()
        assert cache.exists() is False
        assert cache.load() == []

    def test_clear_when_not_exists(self, cache, cache_path):
        """clear 不存在的文件不抛错"""
        assert cache_path.exists() is False
        cache.clear()  # 不应抛错
        assert cache.exists() is False


# ============================================================
# 8. 重启持久化
# ============================================================

class TestRestartPersistence:
    def test_restart_persistence(self, cache_path):
        """cache1.save() 后重新创建 cache2，cache2.load() 数据一致"""
        exps = [
            make_valid_experience("exp_1", "origin"),
            make_valid_experience("exp_2", "relationship"),
        ]
        cache1 = ExperienceCache(cache_path=cache_path)
        cache1.save(exps)

        # 模拟进程重启
        cache2 = ExperienceCache(cache_path=cache_path)
        loaded = cache2.load()

        assert len(loaded) == 2
        assert loaded[0] == exps[0]
        assert loaded[1] == exps[1]

    def test_restart_with_dirty_input_cleaned(self, cache_path):
        """重启时即使保存了脏数据，加载时也只读 6 字段"""
        dirty = make_valid_experience()
        dirty["_source_memory_id"] = "mem_xxx"
        dirty["confidence"] = 0.99

        cache1 = ExperienceCache(cache_path=cache_path)
        cache1.save([dirty])

        cache2 = ExperienceCache(cache_path=cache_path)
        loaded = cache2.load()
        assert len(loaded) == 1
        item = loaded[0]
        assert "_source_memory_id" not in item
        assert "confidence" not in item
        assert set(item.keys()) == ALLOWED_FIELDS


# ============================================================
# 9. 架构合规性
# ============================================================

class TestArchitectureCompliance:
    """原则：Cache 不依赖 Marker / Extractor / Growth 系统"""

    @staticmethod
    def _get_imports(module_path: str) -> list:
        """用 AST 提取模块的 import 语句（不包含 docstring 中的文字）"""
        import ast
        source = Path(module_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.append(f"{module}.{alias.name}")
        return imports

    def test_cache_does_not_import_marker(self):
        """cache.py 不应 import recovery_marker"""
        import src.recovery.experience_cache as cache_mod
        imports = self._get_imports(cache_mod.__file__)
        for imp in imports:
            assert "recovery_marker" not in imp, f"cache.py 不应 import {imp}"
            assert "RecoveryMarker" not in imp, f"cache.py 不应 import {imp}"

    def test_cache_does_not_import_extractor(self):
        """cache.py 不应 import experience_extractor"""
        import src.recovery.experience_cache as cache_mod
        imports = self._get_imports(cache_mod.__file__)
        for imp in imports:
            assert "experience_extractor" not in imp, f"cache.py 不应 import {imp}"
            assert "ExperienceExtractor" not in imp, f"cache.py 不应 import {imp}"

    def test_cache_does_not_import_growth(self):
        """cache.py 不应 import 任何 src/growth/* 或 personality_growth_record"""
        import src.recovery.experience_cache as cache_mod
        imports = self._get_imports(cache_mod.__file__)
        for imp in imports:
            assert "src.growth" not in imp, f"cache.py 不应 import {imp}"
            assert "personality_growth_record" not in imp, f"cache.py 不应 import {imp}"
