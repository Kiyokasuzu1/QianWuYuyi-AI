"""
Phase A.1: ExperienceExtractor 单元测试

覆盖：
- str / list[dict] content 归一化
- XML 标签剥离
- 严格 if/elif/else 分类（origin > relationship > project > interaction）
- importance 计算（含 continuity 去重）
- 长度过滤、纯问候过滤、无关键词过滤
- 字段集合严格性（不含 _source_memory_id / confidence / related_dimensions）
- 排序与截断（≤ 40）
- 内存文件不存在/损坏
"""

import json
import pytest
from pathlib import Path

from src.recovery.experience_extractor import ExperienceExtractor


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def extractor():
    return ExperienceExtractor()


@pytest.fixture
def write_memory(tmp_path):
    """工厂 fixture：写入 memory.json 并返回路径"""
    def _write(memories):
        path = tmp_path / "memory.json"
        path.write_text(json.dumps(memories, ensure_ascii=False), encoding="utf-8")
        return path
    return _write


# ============================================================
# 1. str content 归一化
# ============================================================

class TestNormalizeContent:
    def test_str_content_passthrough(self, extractor):
        assert extractor._normalize_content("你好羽依") == "你好羽依"

    def test_str_empty(self, extractor):
        assert extractor._normalize_content("") == ""

    def test_none_content(self, extractor):
        assert extractor._normalize_content(None) == ""


# ============================================================
# 2. list content 归一化
# ============================================================

class TestListContent:
    def test_list_text_items_concatenated(self, extractor):
        content = [
            {"type": "text", "text": "羽依诞生"},
            {"type": "text", "text": "清夏铃创建"},
        ]
        result = extractor._normalize_content(content)
        assert "羽依诞生" in result
        assert "清夏铃创建" in result

    def test_list_with_non_text_items_ignored(self, extractor):
        content = [
            {"type": "image", "url": "..."},
            {"type": "text", "text": "羽依诞生"},
        ]
        result = extractor._normalize_content(content)
        assert result == "羽依诞生"

    def test_empty_list(self, extractor):
        assert extractor._normalize_content([]) == ""


# ============================================================
# 3. XML 标签剥离
# ============================================================

class TestStripXmlTags:
    def test_system_reminder_stripped(self, extractor):
        text = "羽依诞生<system_reminder>ID: 123</system_reminder>了"
        result = extractor._strip_xml_tags(text)
        assert "羽依诞生" in result
        assert "ID: 123" in result  # 内部内容保留
        assert "<system_reminder>" not in result

    def test_rag_faiss_memory_stripped(self, extractor):
        text = "<RAG-Faiss-Memory>羽依项目创建历史</RAG-Faiss-Memory>"
        result = extractor._strip_xml_tags(text)
        assert "羽依项目创建历史" in result
        assert "<RAG-Faiss-Memory>" not in result

    def test_extra_instruction_stripped(self, extractor):
        text = "<extra_instruction>summary</extra_instruction>羽依"
        result = extractor._strip_xml_tags(text)
        assert "羽依" in result
        assert "<extra_instruction>" not in result

    def test_empty_text(self, extractor):
        assert extractor._strip_xml_tags("") == ""


# ============================================================
# 4. 严格分类（核心）
# ============================================================

class TestClassify:
    def test_origin_classification(self, extractor):
        cat, matched = extractor._classify("羽依诞生于2026年")
        assert cat == "origin"
        assert "羽依" in matched
        assert "诞生" in matched

    def test_relationship_classification(self, extractor):
        """不含 origin 关键词时，relationship 优先"""
        cat, matched = extractor._classify("清夏铃和用户一起陪伴了很长时间")
        assert cat == "relationship"
        assert "清夏铃" in matched
        assert "陪伴" in matched

    def test_relationship_when_origin_keyword_absent(self, extractor):
        cat, _ = extractor._classify("我们一直在一起，不要忘记彼此")
        assert cat == "relationship"

    def test_project_classification(self, extractor):
        cat, matched = extractor._classify("Phase 8.3 完成了系统模块")
        assert cat == "project"
        assert "Phase" in matched
        assert "系统" in matched
        assert "模块" in matched

    def test_interaction_fallback(self, extractor):
        cat, matched = extractor._classify("今天天气真好")
        assert cat == "interaction"
        assert matched == set()

    def test_origin_priority_over_project(self, extractor):
        """关键：'羽依项目诞生' 应被分类为 origin 而非 project"""
        cat, _ = extractor._classify("羽依项目诞生于2026年")
        assert cat == "origin"

    def test_relationship_priority_over_project(self, extractor):
        """关键：'清夏铃一起完成 Phase 8' 应被分类为 relationship（无 origin 关键词）"""
        cat, _ = extractor._classify("清夏铃和我一起完成 Phase 8 系统重构")
        assert cat == "relationship"


# ============================================================
# 5. importance 计算
# ============================================================

class TestCalcImportance:
    def test_base_only_no_match(self, extractor):
        imp = extractor._calc_importance("interaction", set(), 0.0)
        assert imp == 0.3

    def test_origin_keyword(self, extractor):
        imp = extractor._calc_importance("origin", {"羽依"}, 0.0)
        assert imp == 0.3 + 0.25  # 0.55

    def test_origin_with_continuity(self, extractor):
        imp = extractor._calc_importance("origin", {"羽依", "第一次"}, 0.0)
        # continuity set 去重：只算一次
        assert imp == 0.3 + 0.25 + 0.15  # 0.70

    def test_origin_with_high_source_importance(self, extractor):
        imp = extractor._calc_importance("origin", {"羽依"}, 0.9)
        assert imp == 0.3 + 0.25 + 0.05  # 0.60

    def test_continuity_dedup(self, extractor):
        """同一条 memory 命中多个 continuity 关键词只算一次"""
        imp1 = extractor._calc_importance(
            "origin", {"羽依", "一直", "回来"}, 0.0
        )
        imp2 = extractor._calc_importance(
            "origin", {"羽依", "一直", "回来", "以后", "第一次"}, 0.0
        )
        # 两者 continuity 都是 +0.15（去重）
        assert imp1 == imp2

    def test_max_clamped_to_1(self, extractor):
        imp = extractor._calc_importance("origin", {"羽依", "一直"}, 1.0)
        # 0.3 + 0.25 + 0.15 + 0.05 = 0.75
        assert imp == pytest.approx(0.75)
        assert imp <= 1.0


# ============================================================
# 6. 过滤规则
# ============================================================

class TestFiltering:
    def test_too_short_filtered(self, extractor, write_memory):
        path = write_memory([
            {"id": "mem_1", "content": "你好", "timestamp": "2026-07-26T00:00:00"}
        ])
        result = extractor.extract(path)
        assert result == []

    def test_greeting_filtered(self, extractor, write_memory):
        path = write_memory([
            {"id": "mem_1", "content": "晚安", "timestamp": "2026-07-26T00:00:00"}
        ])
        result = extractor.extract(path)
        assert result == []

    def test_no_keyword_filtered(self, extractor, write_memory):
        path = write_memory([
            {"id": "mem_1", "content": "今天天气真好适合出去玩", "timestamp": "2026-07-26T00:00:00"}
        ])
        result = extractor.extract(path)
        assert result == []

    def test_missing_id_filtered(self, extractor, write_memory):
        path = write_memory([
            {"content": "羽依诞生了", "timestamp": "2026-07-26T00:00:00"}
        ])
        result = extractor.extract(path)
        assert result == []


# ============================================================
# 7. 端到端 extract()
# ============================================================

class TestExtract:
    def test_origin_extracted(self, extractor, write_memory):
        path = write_memory([
            {"id": "mem_1", "content": "羽依诞生于2026年7月，由清夏铃创建",
             "timestamp": "2026-07-22T10:00:00", "importance": 0.95}
        ])
        result = extractor.extract(path)
        assert len(result) == 1
        assert result[0]["category"] == "origin"
        assert result[0]["_source_memory_id"] == "mem_1"
        assert "羽依" in result[0]["summary"] or "羽依" in result[0]["evidence"]

    def test_field_set_strict(self, extractor, write_memory):
        """字段集合严格：仅 6 个公开字段 + 1 个内部字段"""
        path = write_memory([
            {"id": "mem_1", "content": "羽依诞生了，这个名字由清夏铃起",
             "timestamp": "2026-07-22T10:00:00"}
        ])
        result = extractor.extract(path)
        assert len(result) == 1
        exp = result[0]
        public_fields = {"experience_id", "category", "summary", "evidence", "timestamp", "importance"}
        internal_fields = {"_source_memory_id"}
        all_fields = set(exp.keys())
        assert all_fields == public_fields | internal_fields
        # 禁止字段
        assert "source_memory_id" not in all_fields
        assert "confidence" not in all_fields
        assert "related_dimensions" not in all_fields

    def test_max_40_items(self, extractor, write_memory):
        # 50 条均含 origin 关键词（每条 > 15 字符）
        memories = [
            {"id": f"mem_{i}", "content": f"羽依诞生记录第{i:03d}号历史片段内容",
             "timestamp": f"2026-07-{(i % 30) + 1:02d}T10:00:00", "importance": 0.5}
            for i in range(50)
        ]
        path = write_memory(memories)
        result = extractor.extract(path)
        assert len(result) == 40

    def test_sort_by_importance_then_timestamp(self, extractor, write_memory):
        path = write_memory([
            {"id": "low", "content": "项目代码完成了系统模块的重构工作",
             "timestamp": "2026-07-22T10:00:00", "importance": 0.5},
            {"id": "high", "content": "羽依诞生了，这个名字由清夏铃起",
             "timestamp": "2026-07-23T10:00:00", "importance": 0.5},
        ])
        result = extractor.extract(path)
        assert len(result) == 2
        assert result[0]["_source_memory_id"] == "high"  # origin 排序在前
        assert result[0]["importance"] > result[1]["importance"]

    def test_empty_memory_file(self, extractor, tmp_path):
        path = tmp_path / "memory.json"
        path.write_text("[]", encoding="utf-8")
        assert extractor.extract(path) == []

    def test_missing_memory_file(self, extractor, tmp_path):
        path = tmp_path / "nonexistent.json"
        assert extractor.extract(path) == []

    def test_corrupted_memory_file(self, extractor, tmp_path):
        path = tmp_path / "memory.json"
        path.write_text("INVALID JSON{{{", encoding="utf-8")
        assert extractor.extract(path) == []


# ============================================================
# 8. XML 集成
# ============================================================

class TestXmlIntegration:
    def test_list_with_xml_stripping(self, extractor, write_memory):
        path = write_memory([
            {"id": "mem_1", "content": [
                {"type": "text", "text": "<system_reminder>User: 123</system_reminder>羽依诞生"},
                {"type": "text", "text": "清夏铃创建羽依"},
            ], "timestamp": "2026-07-22T10:00:00"}
        ])
        result = extractor.extract(path)
        assert len(result) == 1
        assert result[0]["category"] == "origin"
        # 内部真实内容保留
        assert "羽依诞生" in result[0]["evidence"]
        assert "清夏铃" in result[0]["evidence"]
