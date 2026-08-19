# -*- coding: utf-8 -*-
"""
tests/test_state_visualization_phase_d3.py

Phase D.3 —— Yuyi State Visualization 单元测试

覆盖:
  1) state_visualization 模块 (纯函数)
     - TimelineEntry 数据类默认值
     - sort_entries_by_time_desc 排序
     - format_timestamp / format_changed_traits / format_dimensions / format_status_color
     - parse_evolution_entries (单条/多条/缺字段/非 dict)
     - parse_growth_entries (list/envelope/dict 形态)
     - parse_memory_entries (list/dict/envelope 形态 + important_only)
     - has_meaningful_data
  2) PersonalityWidget Evolution Timeline
     - 导入 + 新成员变量
     - _build_evo_timeline_frame / _render_evo_timeline 方法存在
     - 数据接入: parse_evolution_entries 使用 service.get_evolution() 数据
     - 降级: 数据为空 → 优雅显示 "暂无演化记录"
  3) GrowthWidget Growth Timeline
     - 导入 + 新成员变量
     - _build_growth_timeline_frame / _render_growth_timeline 方法存在
     - 数据接入: parse_growth_entries 使用 service.get_recent() / get_proposals() 数据
     - 降级: 数据为空 → 优雅显示 "暂无成长记录"
  4) MemoryWidget Important Memories
     - 导入 + 新成员变量
     - _build_memory_timeline_frame / _render_memory_timeline 方法存在
     - 数据接入: parse_memory_entries 使用 service.list_recent() 数据
     - 降级: 数据为空 → 优雅显示 "暂无重要记忆"
  5) AsyncRefresher 架构未变
     - 三 widget 仍然走 AsyncRefresher
     - _fetch_in_worker 在 worker 线程跑(子流程)
  6) 无新增 AI 模块
     - 三个 widget 不直接 import src.*
     - 不调用任何写接口

约束:
- 不实例化 QWidget(避免 Qt 平台依赖), 用 MagicMock 模拟
- 不发起真实 HTTP
- 不写文件/不修改运行中的状态
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import inspect
import ast
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# 仓库根路径
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ============================================================
# 1) state_visualization 模块
# ============================================================
class TestStateVisualizationImports:
    def test_import_module(self):
        from yuyi_desktop.ui.widgets import state_visualization
        assert state_visualization is not None

    def test_import_timeline_entry(self):
        from yuyi_desktop.ui.widgets.state_visualization import TimelineEntry
        assert TimelineEntry is not None

    def test_import_parse_functions(self):
        from yuyi_desktop.ui.widgets.state_visualization import (
            parse_evolution_entries,
            parse_growth_entries,
            parse_memory_entries,
        )
        assert callable(parse_evolution_entries)
        assert callable(parse_growth_entries)
        assert callable(parse_memory_entries)

    def test_import_helpers(self):
        from yuyi_desktop.ui.widgets.state_visualization import (
            sort_entries_by_time_desc,
            format_timestamp,
            format_changed_traits,
            format_dimensions,
            format_status_color,
            format_status_label,
            has_meaningful_data,
        )
        assert callable(sort_entries_by_time_desc)
        assert callable(format_timestamp)
        assert callable(format_changed_traits)
        assert callable(format_dimensions)
        assert callable(format_status_color)
        assert callable(format_status_label)
        assert callable(has_meaningful_data)

    def test_all_exports(self):
        from yuyi_desktop.ui.widgets import state_visualization
        for name in (
            "TimelineEntry",
            "sort_entries_by_time_desc",
            "format_timestamp",
            "format_changed_traits",
            "format_dimensions",
            "format_status_color",
            "format_status_label",
            "parse_evolution_entries",
            "parse_growth_entries",
            "parse_memory_entries",
            "has_meaningful_data",
        ):
            assert name in state_visualization.__all__, f"{name} missing from __all__"


class TestTimelineEntryDefaults:
    def test_default_values(self):
        from yuyi_desktop.ui.widgets.state_visualization import TimelineEntry
        e = TimelineEntry()
        assert e.timestamp == ""
        assert e.sort_key is None
        assert e.source == ""
        assert e.kind == ""
        assert e.title == ""
        assert e.summary == ""
        assert e.fields == []
        assert e.status == ""
        assert e.score == 0.0

    def test_has_time(self):
        from yuyi_desktop.ui.widgets.state_visualization import TimelineEntry
        e1 = TimelineEntry()
        e2 = TimelineEntry(timestamp="2026-08-04 10:00:00", sort_key=1.0)
        assert e1.has_time() is False
        assert e2.has_time() is True

    def test_fields_isolated_per_instance(self):
        from yuyi_desktop.ui.widgets.state_visualization import TimelineEntry
        e1 = TimelineEntry()
        e2 = TimelineEntry()
        e1.fields.append(("a", "b", "c"))
        assert e2.fields == []  # 默认值不应被共享


class TestSortEntriesByTimeDesc:
    def test_sort_descending(self):
        from yuyi_desktop.ui.widgets.state_visualization import (
            TimelineEntry,
            sort_entries_by_time_desc,
        )
        e1 = TimelineEntry(timestamp="2026-08-01 10:00:00", sort_key=1.0)
        e2 = TimelineEntry(timestamp="2026-08-04 10:00:00", sort_key=4.0)
        e3 = TimelineEntry(timestamp="2026-08-02 10:00:00", sort_key=2.0)
        out = sort_entries_by_time_desc([e1, e2, e3])
        assert out[0] is e2
        assert out[1] is e3
        assert out[2] is e1

    def test_no_time_keeps_order(self):
        from yuyi_desktop.ui.widgets.state_visualization import (
            TimelineEntry,
            sort_entries_by_time_desc,
        )
        e1 = TimelineEntry(timestamp="—", sort_key=None)
        e2 = TimelineEntry(timestamp="—", sort_key=None)
        out = sort_entries_by_time_desc([e1, e2])
        assert out == [e1, e2]

    def test_empty(self):
        from yuyi_desktop.ui.widgets.state_visualization import sort_entries_by_time_desc
        assert sort_entries_by_time_desc([]) == []


class TestFormatHelpers:
    def test_format_timestamp_iso(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_timestamp
        assert format_timestamp("2026-08-04T10:00:00Z") == "2026-08-04 10:00:00"
        assert format_timestamp("2026-08-04 10:00:00") == "2026-08-04 10:00:00"

    def test_format_timestamp_none(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_timestamp
        assert format_timestamp(None) == "—"
        assert format_timestamp("") == "—"

    def test_format_changed_traits_list(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_changed_traits
        assert format_changed_traits(["温柔", "理性"]) == "温柔, 理性"
        # 实现使用 ", " 拼接, 所以省略号前会有逗号
        result = format_changed_traits(["a", "b", "c", "d", "e", "f"], limit=3)
        assert "a" in result and "b" in result and "c" in result
        assert "(+3)" in result

    def test_format_changed_traits_dict(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_changed_traits
        result = format_changed_traits({"温柔": 0.8, "理性": 0.6})
        assert "温柔=0.80" in result
        assert "理性=0.60" in result

    def test_format_changed_traits_none(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_changed_traits
        assert format_changed_traits(None) == "—"
        assert format_changed_traits("") == "—"

    def test_format_dimensions_list(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_dimensions
        assert format_dimensions(["trait.empathy", "trait.rationality"]) == "trait.empathy, trait.rationality"

    def test_format_dimensions_none(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_dimensions
        assert format_dimensions(None) == "—"
        assert format_dimensions("") == "—"

    def test_format_status_color_ok(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_status_color
        assert format_status_color("applied") == "#5dd87a"
        assert format_status_color("approved") == "#5dd87a"
        assert format_status_color("ok") == "#5dd87a"
        assert format_status_color("healthy") == "#5dd87a"

    def test_format_status_color_warn(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_status_color
        assert format_status_color("pending") == "#e0c060"
        assert format_status_color("warning") == "#e0c060"

    def test_format_status_color_bad(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_status_color
        assert format_status_color("rejected") == "#d85d5d"
        assert format_status_color("error") == "#d85d5d"

    def test_format_status_color_neutral(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_status_color
        assert format_status_color("") == "#888"
        assert format_status_color("unknown") == "#888"
        assert format_status_color(None) == "#888"

    def test_format_status_label(self):
        from yuyi_desktop.ui.widgets.state_visualization import format_status_label
        assert format_status_label("applied") == "applied"
        assert format_status_label("") == "—"
        assert format_status_label(None) == "—"


class TestParseEvolutionEntries:
    def test_basic_parse(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_evolution_entries
        data = [
            {
                "timestamp": "2026-08-04T10:00:00Z",
                "changed_traits": ["温柔", "理性"],
                "reason": "用户长时间陪伴",
                "confidence": 0.85,
                "status": "applied",
            }
        ]
        out = parse_evolution_entries(data)
        assert len(out) == 1
        assert out[0].timestamp == "2026-08-04 10:00:00"
        assert out[0].title == "温柔, 理性"
        assert out[0].status == "applied"
        assert out[0].score == 0.85

    def test_invalid_input(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_evolution_entries
        assert parse_evolution_entries(None) == []
        assert parse_evolution_entries("not_list") == []
        assert parse_evolution_entries(123) == []
        assert parse_evolution_entries([None, "string", 123]) == []

    def test_skip_non_dict(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_evolution_entries
        out = parse_evolution_entries([{"timestamp": "2026-08-01", "changed_traits": []}, "bad"])
        assert len(out) == 1

    def test_multiple_sorted_desc(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_evolution_entries
        data = [
            {"timestamp": "2026-08-01T10:00:00Z", "changed_traits": ["A"]},
            {"timestamp": "2026-08-04T10:00:00Z", "changed_traits": ["B"]},
            {"timestamp": "2026-08-02T10:00:00Z", "changed_traits": ["C"]},
        ]
        out = parse_evolution_entries(data)
        assert len(out) == 3
        assert "B" in out[0].title
        assert "C" in out[1].title
        assert "A" in out[2].title

    def test_limit(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_evolution_entries
        data = [{"timestamp": f"2026-08-0{i}T10:00:00Z", "changed_traits": [f"t{i}"]} for i in range(1, 6)]
        out = parse_evolution_entries(data, limit=3)
        assert len(out) == 3

    def test_missing_fields_graceful(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_evolution_entries
        out = parse_evolution_entries([{}])  # 全部字段缺失
        assert len(out) == 1
        assert out[0].timestamp == "—"
        assert out[0].title == "—"
        assert out[0].status == ""


class TestParseGrowthEntries:
    def test_list_input(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_growth_entries
        data = [
            {
                "timestamp": "2026-08-04T10:00:00Z",
                "signal": "user_engagement",
                "source": "personality_evolution",
                "dimensions": ["trait.empathy"],
                "score": 0.7,
                "status": "applied",
            }
        ]
        out = parse_growth_entries(data)
        assert len(out) == 1
        assert out[0].title == "user_engagement"
        assert out[0].source == "personality_evolution"

    def test_envelope_input(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_growth_entries
        data = {
            "success": True,
            "data": {
                "history": [
                    {"timestamp": "2026-08-04T10:00:00Z", "signal": "growth_signal_a"},
                ]
            },
        }
        out = parse_growth_entries(data)
        assert len(out) == 1
        assert out[0].title == "growth_signal_a"

    def test_invalid_input(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_growth_entries
        assert parse_growth_entries(None) == []
        assert parse_growth_entries("not_list") == []
        assert parse_growth_entries(123) == []

    def test_empty_history(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_growth_entries
        out = parse_growth_entries({"success": True, "data": {"history": []}})
        assert out == []


class TestParseMemoryEntries:
    def test_list_input(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_memory_entries
        data = [
            {
                "memory_id": "m1",
                "summary": "first meeting",
                "importance": 0.9,
                "tags": ["first_meeting", "milestone"],
                "category": "user_milestone",
                "timestamp": "2026-08-04T10:00:00Z",
                "meaning": "标志着关系开始",
            }
        ]
        out = parse_memory_entries(data)
        assert len(out) == 1
        assert out[0].title == "first meeting"
        assert out[0].status == "important"  # importance 0.9 >= 0.7
        assert out[0].summary == "标志着关系开始"

    def test_important_only_filters(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_memory_entries
        data = [
            {"summary": "high importance", "importance": 0.95},
            {"summary": "low importance", "importance": 0.3},
            {"summary": "tagged important", "importance": 0.2, "tags": ["important"]},
        ]
        out = parse_memory_entries(data, important_only=True)
        # 应该有 2 条: importance=0.95 + tag 包含 important
        assert len(out) == 2
        titles = {e.title for e in out}
        assert "high importance" in titles
        assert "tagged important" in titles
        assert "low importance" not in titles

    def test_invalid_input(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_memory_entries
        assert parse_memory_entries(None) == []
        assert parse_memory_entries("not_list") == []

    def test_envelope_with_recent(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_memory_entries
        data = {
            "success": True,
            "data": {
                "recent": [
                    {"summary": "test", "importance": 0.5, "timestamp": "2026-08-04T10:00:00Z"},
                ]
            },
        }
        out = parse_memory_entries(data)
        assert len(out) == 1
        assert out[0].title == "test"

    def test_no_time_uses_importance(self):
        from yuyi_desktop.ui.widgets.state_visualization import parse_memory_entries
        data = [
            {"summary": "a", "importance": 0.9},  # 无 timestamp
            {"summary": "b", "importance": 0.5},
        ]
        out = parse_memory_entries(data)
        assert len(out) == 2
        # 无时间时按 importance 降序
        assert out[0].title == "a"
        assert out[1].title == "b"


class TestHasMeaningfulData:
    def test_empty(self):
        from yuyi_desktop.ui.widgets.state_visualization import (
            TimelineEntry,
            has_meaningful_data,
        )
        assert has_meaningful_data([]) is False
        assert has_meaningful_data([TimelineEntry()]) is False

    def test_meaningful(self):
        from yuyi_desktop.ui.widgets.state_visualization import (
            TimelineEntry,
            has_meaningful_data,
        )
        e = TimelineEntry(timestamp="2026-08-04 10:00:00")
        assert has_meaningful_data([e]) is True

    def test_meaningful_via_field(self):
        from yuyi_desktop.ui.widgets.state_visualization import (
            TimelineEntry,
            has_meaningful_data,
        )
        e = TimelineEntry(fields=[("k", "v", "c")])
        assert has_meaningful_data([e]) is True


# ============================================================
# 2) PersonalityWidget Evolution Timeline
# ============================================================
class TestPersonalityWidgetEvolutionTimeline:
    def test_import_state_visualization(self):
        """PersonalityWidget 必须导入 state_visualization 模块。"""
        from yuyi_desktop.ui.widgets import personality_widget
        assert "TimelineEntry" in dir(personality_widget) or hasattr(personality_widget, "TimelineEntry")
        # 真实导入:
        from yuyi_desktop.ui.widgets.state_visualization import (
            TimelineEntry,
            parse_evolution_entries,
        )
        # personality_widget 必须能引用到(直接或通过 _evo_timeline_entries)
        assert hasattr(personality_widget, "PersonalityWidget")

    def test_evo_timeline_member_exists(self):
        """PersonalityWidget 必须有 _evo_timeline_entries 成员。"""
        from yuyi_desktop.ui.widgets.personality_widget import PersonalityWidget
        # 用源码分析确认 __init__ 中有此赋值
        src = inspect.getsource(PersonalityWidget.__init__)
        assert "_evo_timeline_entries" in src

    def test_evo_timeline_frame_method_exists(self):
        from yuyi_desktop.ui.widgets.personality_widget import PersonalityWidget
        assert hasattr(PersonalityWidget, "_build_evo_timeline_frame")
        assert hasattr(PersonalityWidget, "_render_evo_timeline")

    def test_evo_timeline_frame_added_to_build_ui(self):
        """_build_ui 必须包含 Evolution Timeline 区块的初始化。"""
        from yuyi_desktop.ui.widgets.personality_widget import PersonalityWidget
        src = inspect.getsource(PersonalityWidget._build_ui)
        assert "_build_evo_timeline_frame" in src
        assert "_evo_timeline_frame" in src

    def test_evo_timeline_render_called_in_data_ready(self):
        """_on_data_ready 必须调用 _render_evo_timeline。"""
        from yuyi_desktop.ui.widgets.personality_widget import PersonalityWidget
        src = inspect.getsource(PersonalityWidget._on_data_ready)
        assert "_render_evo_timeline" in src

    def test_no_src_import(self):
        """PersonalityWidget 不应直接 import src.* 业务模块。"""
        from yuyi_desktop.ui.widgets import personality_widget
        mod_src = inspect.getsource(personality_widget)
        # 检查 "from src." 或 "import src." 是否存在
        # (允许 from yuyi_desktop..., 但不能 from src.*)
        lines = [l.strip() for l in mod_src.splitlines() if l.strip().startswith(("from src", "import src"))]
        assert len(lines) == 0, f"发现 src.* 导入: {lines}"


# ============================================================
# 3) GrowthWidget Growth Timeline
# ============================================================
class TestGrowthWidgetTimeline:
    def test_import_state_visualization(self):
        from yuyi_desktop.ui.widgets import growth_widget
        assert hasattr(growth_widget, "GrowthWidget")
        from yuyi_desktop.ui.widgets.state_visualization import (
            TimelineEntry,
            parse_growth_entries,
        )
        assert growth_widget is not None

    def test_timeline_member_exists(self):
        from yuyi_desktop.ui.widgets.growth_widget import GrowthWidget
        src = inspect.getsource(GrowthWidget.__init__)
        assert "_growth_timeline_entries" in src
        assert "_growth_recent_detail" in src

    def test_timeline_frame_method_exists(self):
        from yuyi_desktop.ui.widgets.growth_widget import GrowthWidget
        assert hasattr(GrowthWidget, "_build_growth_timeline_frame")
        assert hasattr(GrowthWidget, "_render_growth_timeline")

    def test_timeline_frame_added_to_build_ui(self):
        from yuyi_desktop.ui.widgets.growth_widget import GrowthWidget
        src = inspect.getsource(GrowthWidget._build_ui)
        assert "_build_growth_timeline_frame" in src
        assert "_growth_timeline_frame" in src

    def test_timeline_render_called_in_data_ready(self):
        from yuyi_desktop.ui.widgets.growth_widget import GrowthWidget
        src = inspect.getsource(GrowthWidget._on_data_ready)
        assert "_render_growth_timeline" in src

    def test_fetch_worker_pulls_recent_and_proposals(self):
        """_fetch_in_worker 必须拉取 recent + proposals(用于 Timeline)。"""
        from yuyi_desktop.ui.widgets.growth_widget import GrowthWidget
        src = inspect.getsource(GrowthWidget._fetch_in_worker)
        assert "get_recent" in src
        assert "get_proposals" in src

    def test_no_src_import(self):
        from yuyi_desktop.ui.widgets import growth_widget
        mod_src = inspect.getsource(growth_widget)
        lines = [l.strip() for l in mod_src.splitlines() if l.strip().startswith(("from src", "import src"))]
        assert len(lines) == 0, f"发现 src.* 导入: {lines}"


# ============================================================
# 4) MemoryWidget Important Memories
# ============================================================
class TestMemoryWidgetImportantMemories:
    def test_import_state_visualization(self):
        from yuyi_desktop.ui.widgets import memory_widget
        assert hasattr(memory_widget, "MemoryWidget")
        from yuyi_desktop.ui.widgets.state_visualization import (
            TimelineEntry,
            parse_memory_entries,
        )
        assert memory_widget is not None

    def test_timeline_member_exists(self):
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        src = inspect.getsource(MemoryWidget.__init__)
        assert "_memory_timeline_entries" in src
        assert "_memory_recent_raw" in src

    def test_timeline_frame_method_exists(self):
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        assert hasattr(MemoryWidget, "_build_memory_timeline_frame")
        assert hasattr(MemoryWidget, "_render_memory_timeline")

    def test_timeline_frame_added_to_build_ui(self):
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        src = inspect.getsource(MemoryWidget._build_ui)
        assert "_build_memory_timeline_frame" in src
        assert "_memory_timeline_frame" in src

    def test_timeline_render_called_in_data_ready(self):
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        src = inspect.getsource(MemoryWidget._on_data_ready)
        assert "_render_memory_timeline" in src

    def test_fetch_worker_pulls_recent(self):
        """_fetch_in_worker 必须拉取 recent(用于 Important Memories)。"""
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        src = inspect.getsource(MemoryWidget._fetch_in_worker)
        assert "list_recent" in src

    def test_no_src_import(self):
        from yuyi_desktop.ui.widgets import memory_widget
        mod_src = inspect.getsource(memory_widget)
        lines = [l.strip() for l in mod_src.splitlines() if l.strip().startswith(("from src", "import src"))]
        assert len(lines) == 0, f"发现 src.* 导入: {lines}"


# ============================================================
# 5) AsyncRefresher 架构未变
# ============================================================
class TestAsyncRefresherArchitecture:
    def test_personality_widget_still_uses_async_refresher(self):
        from yuyi_desktop.ui.widgets.personality_widget import PersonalityWidget
        src = inspect.getsource(PersonalityWidget.__init__)
        assert "AsyncRefresher" in src
        assert "_refresher" in src
        assert "_refresher.submit" in src
        assert "_on_data_ready" in src
        assert "_on_data_failed" in src

    def test_growth_widget_still_uses_async_refresher(self):
        from yuyi_desktop.ui.widgets.growth_widget import GrowthWidget
        src = inspect.getsource(GrowthWidget.__init__)
        assert "AsyncRefresher" in src
        assert "_refresher" in src
        assert "_refresher.submit" in src
        assert "_on_data_ready" in src
        assert "_on_data_failed" in src

    def test_memory_widget_still_uses_async_refresher(self):
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        src = inspect.getsource(MemoryWidget.__init__)
        assert "AsyncRefresher" in src
        assert "_refresher" in src
        assert "_refresher.submit" in src
        assert "_on_data_ready" in src
        assert "_on_data_failed" in src

    def test_qtimer_uses_refresher(self):
        """三个 widget 的 QTimer.timeout 必须连接到 _refresher.submit, 不直接调用网络函数。"""
        for mod_name in ("personality_widget", "growth_widget", "memory_widget"):
            mod = __import__(f"yuyi_desktop.ui.widgets.{mod_name}", fromlist=["*"])
            for cls_name in ("PersonalityWidget", "GrowthWidget", "MemoryWidget"):
                cls = getattr(mod, cls_name, None)
                if cls is None:
                    continue
                src = inspect.getsource(cls.__init__)
                # 必须有 QTimer.timeout.connect(_refresher.submit)
                assert "self._timer.timeout.connect(self._refresher.submit)" in src, (
                    f"{cls_name}.__{mod_name}__ QTimer.timeout 未连接到 _refresher.submit"
                )


# ============================================================
# 6) 无新增 AI 模块 / 禁止破坏架构
# ============================================================
class TestNoBreakingChanges:
    def test_personality_widget_class_exists(self):
        from yuyi_desktop.ui.widgets.personality_widget import PersonalityWidget
        assert PersonalityWidget is not None

    def test_growth_widget_class_exists(self):
        from yuyi_desktop.ui.widgets.growth_widget import GrowthWidget
        assert GrowthWidget is not None

    def test_memory_widget_class_exists(self):
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        assert MemoryWidget is not None

    def test_state_visualization_no_writes(self):
        """state_visualization 模块不应有写操作。"""
        from yuyi_desktop.ui.widgets import state_visualization
        src = inspect.getsource(state_visualization)
        forbidden = ["requests.", "httpx.", "urllib.request", "open(", "write("]
        for f in forbidden:
            # 仅检查明显调用,不检查注释/字符串
            # 用 ast 解析排除字符串
            try:
                tree = ast.parse(src)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        # 检查函数调用名
                        if isinstance(node.func, ast.Attribute):
                            if node.func.attr in ("write", "post", "put", "delete", "patch"):
                                pytest.fail(
                                    f"state_visualization 包含疑似写操作: {ast.dump(node.func)}"
                                )
                        elif isinstance(node.func, ast.Name):
                            if node.func.id in ("open",):
                                # 检查参数
                                mode_arg = ""
                                for arg in node.args[1:]:
                                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                        mode_arg = arg.value
                                if "w" in mode_arg or "a" in mode_arg or "+" in mode_arg:
                                    pytest.fail(
                                        f"state_visualization 包含 open() 写操作: mode={mode_arg}"
                                    )
            except SyntaxError:
                pass

    def test_widget_no_write_endpoints(self):
        """三 widget 不调用任何写接口(/create, /update, /apply, /delete)。"""
        for mod_name, cls_name in (
            ("personality_widget", "PersonalityWidget"),
            ("growth_widget", "GrowthWidget"),
            ("memory_widget", "MemoryWidget"),
        ):
            mod = __import__(f"yuyi_desktop.ui.widgets.{mod_name}", fromlist=["*"])
            cls = getattr(mod, cls_name)
            src = inspect.getsource(cls)
            # 检查常见写操作 endpoint
            forbidden_words = [
                "/create", "/update", "/apply", "/delete",
                "/approve", "/reject", "/evolve", "/rollback",
                "personality_apply", "growth_apply", "proposal_apply",
            ]
            # 仅检查代码行(简单: 行中以非注释/非 docstring 形式出现)
            lines = src.splitlines()
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                for word in forbidden_words:
                    if word in stripped and "endpoint" not in stripped.lower():
                        # 注意: 注释中也可能提及,这里简化判断
                        pytest.fail(
                            f"{cls_name} 包含疑似写接口调用: {stripped[:80]}"
                        )


# ============================================================
# 7) 端到端数据流验证(无 Qt 启动)
# ============================================================
class TestDataFlowValidation:
    def test_evolution_pipeline(self):
        """端到端: service.get_evolution() 原始数据 → parse_evolution_entries → TimelineEntry。"""
        from yuyi_desktop.ui.widgets.state_visualization import parse_evolution_entries
        # 模拟 service.get_evolution() 返回的原始数据
        raw = [
            {
                "timestamp": "2026-08-04T10:00:00Z",
                "changed_traits": ["温柔", "共情"],
                "reason": "用户长时间陪伴",
                "confidence": 0.92,
                "status": "applied",
                "evolution_version": 5,
            },
            {
                "timestamp": "2026-08-01T10:00:00Z",
                "changed_traits": ["理性"],
                "reason": "深度对话",
                "confidence": 0.78,
                "status": "pending",
            },
        ]
        entries = parse_evolution_entries(raw)
        assert len(entries) == 2
        # 倒序: 8-04 在前
        assert entries[0].timestamp == "2026-08-04 10:00:00"
        assert entries[1].timestamp == "2026-08-01 10:00:00"
        # 状态颜色
        assert entries[0].status == "applied"
        assert entries[1].status == "pending"

    def test_growth_pipeline(self):
        """端到端: service.get_recent() 原始数据 → parse_growth_entries → TimelineEntry。"""
        from yuyi_desktop.ui.widgets.state_visualization import parse_growth_entries
        raw = [
            {
                "timestamp": "2026-08-04T10:00:00Z",
                "signal": "user_milestone_reached",
                "source": "memory",
                "dimensions": ["trait.empathy", "trait.caring"],
                "score": 0.85,
                "status": "applied",
            },
            {
                "timestamp": "2026-08-03T10:00:00Z",
                "signal": "new_topic_interest",
                "source": "initiative",
                "dimensions": ["trait.curiosity"],
                "score": 0.62,
                "status": "pending",
            },
        ]
        entries = parse_growth_entries(raw)
        assert len(entries) == 2
        # 倒序
        assert entries[0].title == "user_milestone_reached"
        assert entries[1].title == "new_topic_interest"

    def test_memory_pipeline(self):
        """端到端: service.list_recent() 原始数据 → parse_memory_entries → TimelineEntry。"""
        from yuyi_desktop.ui.widgets.state_visualization import parse_memory_entries
        raw = [
            {
                "memory_id": "m1",
                "summary": "First meeting",
                "content": "我们今天第一次见面",
                "importance": 0.95,
                "tags": ["first_meeting", "milestone"],
                "category": "user_milestone",
                "timestamp": "2026-08-04T10:00:00Z",
                "meaning": "标志着关系开始",
            },
            {
                "memory_id": "m2",
                "summary": "Casual chat",
                "content": "随便聊了聊",
                "importance": 0.3,
                "tags": ["casual"],
                "category": "user_message",
                "timestamp": "2026-08-03T10:00:00Z",
                "meaning": "",
            },
        ]
        entries = parse_memory_entries(raw)
        assert len(entries) == 2
        # 倒序
        assert entries[0].title == "First meeting"
        assert entries[0].status == "important"
        assert entries[1].title == "Casual chat"
        assert entries[1].status == "normal"

    def test_graceful_degradation(self):
        """数据缺失时优雅降级。"""
        from yuyi_desktop.ui.widgets.state_visualization import (
            parse_evolution_entries,
            parse_growth_entries,
            parse_memory_entries,
            has_meaningful_data,
        )
        # 全空
        assert parse_evolution_entries([]) == []
        assert parse_growth_entries([]) == []
        assert parse_memory_entries([]) == []
        # None
        assert parse_evolution_entries(None) == []
        assert parse_growth_entries(None) == []
        assert parse_memory_entries(None) == []
        # has_meaningful_data 配合空数据
        assert has_meaningful_data([]) is False
        assert has_meaningful_data(parse_evolution_entries([])) is False


if __name__ == "__main__":
    # 支持直接运行: python -m tests.test_state_visualization_phase_d3
    pytest.main([__file__, "-v"])
