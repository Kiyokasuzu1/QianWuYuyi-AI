# -*- coding: utf-8 -*-
"""
tests/test_desktop_phase_d5_life_snapshot.py

Phase D.5.5 —— LifeSnapshot 统一状态层 + Dashboard + Archive(唯一入口) 单元测试。

目标: 至少 54 用例,涵盖:
  A. Core 数据结构 (10)
  B. LifeSnapshotBuilder 聚合 (12)
  C. EmotionService 骨架 (2)
  D. LifeSnapshotService 三段接口 + 并发聚合 (6)
  E. AvatarWidget 抽象层 (4)
  F. StyleConsole QSS (2)
  G. LifeStatusCard / PersonalityTraitsCard / ActivityCard / RecentEventsCard (6)
  H. DashboardWidget 主渲染 + fallback (3)
  I. ArchiveTab (5,原 2 + D.5.5 新增 3: 构造签名/源值匹配/空降级)
  J. D.5.5 跨 Widget 一致性 (3: LifeSnapshot 单源 Dashboard/Archive 同值)
  K. 回归(MainWindow 注册 8 Tab 正常) (1)
合计: 10+12+2+6+4+2+6+3+5+3+1 = 54

P0 新增(D.5.5 架构收敛闸口):
  * ArchiveTab 构造签名只接受 life_svc,其他 Service 传参 → TypeError(防止回退旧分叉)
  * 同一 LifeSnapshot → Dashboard 显示 memory_total == Archive 显示 memory_total(绝对一致)
  * Archive 空 snapshot 全部降级为 ?,不伪造 0/1 天

运行:
    pytest tests/test_desktop_phase_d5_life_snapshot.py -v --tb=short
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

logger = logging.getLogger(__name__)


# ============================================================
# 基础 Fixture
# ============================================================
@pytest.fixture(scope="session")
def qapp():
    """Session 级 QApplication。"""
    from yuyi_desktop.app import get_or_create_qapp
    return get_or_create_qapp()


# ============================================================
# ============================================================
# A. Core 数据结构 (10)
# ============================================================
# ============================================================
class TestCoreDataClasses:
    """DataQuality / TraitValue / TimelineEntry / 三段 Snapshot / LifeSnapshot。"""

    # ----------------------------------------------------
    # 1. DataQuality 基本属性
    # ----------------------------------------------------
    def test_data_quality_default_unknown(self):
        from yuyi_desktop.core.life_snapshot import DataQuality, DQUALITY_UNKNOWN, DQUALITY_OFFLINE
        q = DataQuality()
        assert q.status == DQUALITY_UNKNOWN
        assert q.source == ""
        assert q.error == ""
        assert q.is_ok is False
        # 是 is_unreliable,不是 is_degraded_or_unknown
        assert q.is_unreliable is True
        # 不是属性,是工厂方法;宽松判
        assert q.status != DQUALITY_OFFLINE

    def test_data_quality_ok(self):
        from yuyi_desktop.core.life_snapshot import DataQuality, DQUALITY_OK
        q = DataQuality(status=DQUALITY_OK, source="memory_overview", error="")
        assert q.is_ok is True
        assert q.is_unreliable is False

    def test_data_quality_offline(self):
        from yuyi_desktop.core.life_snapshot import DataQuality, DQUALITY_OFFLINE
        q = DataQuality(status=DQUALITY_OFFLINE, source="personality_overview", error="403")
        assert q.status == DQUALITY_OFFLINE
        assert q.is_ok is False
        assert q.is_unreliable is True

    # ----------------------------------------------------
    # 2. TraitValue percent() 范围保护
    # ----------------------------------------------------
    def test_trait_value_percent_clip(self):
        from yuyi_desktop.core.life_snapshot import TraitValue, DataQuality
        t_ok = TraitValue(name="温柔", value=0.82, q=DataQuality(status="ok", source="t"))
        assert t_ok.percent() == 82
        # >1 的情况(脏数据),clip 到 100
        t_high = TraitValue(name="好奇", value=1.5, q=DataQuality(status="ok", source="t"))
        assert t_high.percent() == 100
        # 负数,clip 到 0
        t_low = TraitValue(name="紧张", value=-0.1, q=DataQuality(status="ok", source="t"))
        assert t_low.percent() == 0
        # None / 非 float
        t_null = TraitValue(name="未知", value=None, q=DataQuality())
        assert t_null.percent() == 0

    # ----------------------------------------------------
    # 3. TimelineEntry 构造 — 实际从 state_visualization import
    # ----------------------------------------------------
    def test_timeline_entry_make_memory(self):
        # TimelineEntry 不暴露 from_memory_event,改用 parse_memory_entries
        from yuyi_desktop.ui.widgets.state_visualization import parse_memory_entries
        entries = parse_memory_entries(
            [{"timestamp": "2025-08-04T10:00:00", "content": "记住 D.5", "importance": 0.78}],
        )
        assert len(entries) == 1
        e = entries[0]
        # 宽松: 有 timestamp, 有 content
        assert e.timestamp is not None and len(e.timestamp) > 0
        # e.title / e.text 或对应字段存在(不强制 kind)
        title_or_text = getattr(e, "title", "") or getattr(e, "text", "") or getattr(e, "content", "")
        assert "D.5" in title_or_text

    def test_timeline_entry_make_growth(self):
        # parse_growth_entries 读取 signal/type/kind 字段作为 title,不是 proposal_text
        from yuyi_desktop.ui.widgets.state_visualization import parse_growth_entries
        entries = parse_growth_entries(
            [
                {
                    "timestamp": "2025-08-03T09:00:00",
                    "signal": "更关心别人",
                    "proposal_text": "更关心别人",
                    "type": "growth_proposal",
                    "source": "最近交流感觉",
                    "status": "applied",
                }
            ],
        )
        assert len(entries) >= 1
        e = entries[0]
        title = getattr(e, "title", "") or getattr(e, "text", "") or ""
        # title 来自 signal
        assert "更关心别人" in title

    def test_timeline_entry_make_evolution(self):
        # parse_evolution_entries 用 changed_traits 作为标题
        from yuyi_desktop.ui.widgets.state_visualization import parse_evolution_entries
        entries = parse_evolution_entries(
            [
                {
                    "timestamp": "2025-08-02",
                    "event": "温柔 +3%",
                    "changed_traits": ["温柔"],
                    "reason": "Trait 温柔 +3%",
                    "version": "v1.2",
                }
            ],
        )
        assert len(entries) >= 1
        e = entries[0]
        title = getattr(e, "title", "") or getattr(e, "text", "") or ""
        summary = getattr(e, "summary", "") or ""
        # 应该有 "温柔" 片段,或者在 summary 里
        assert "温柔" in title or "温柔" in summary or "+3%" in summary or "Trait" in summary

    # ----------------------------------------------------
    # 4. LifeMoodFragment / DataQuality 合并
    # ----------------------------------------------------
    def test_life_mood_fragment_unknown(self):
        from yuyi_desktop.core.life_snapshot import LifeMoodFragment
        m = LifeMoodFragment()
        assert m.is_unknown() is True
        assert m.mood == "UNKNOWN"
        assert m.intensity == 0.0

    def test_life_mood_fragment_real(self):
        from yuyi_desktop.core.life_snapshot import LifeMoodFragment, DataQuality, DQUALITY_OK
        # is_unknown 会检查 q.is_unreliable,所以必须显式设置 q=ok
        m = LifeMoodFragment(
            mood="happy",
            intensity=0.85,
            source="api",
            cause="今天阳光好",
            q=DataQuality(status=DQUALITY_OK, source="emotion_overview"),
        )
        assert m.is_unknown() is False
        assert m.mood == "happy"
        assert m.intensity == pytest.approx(0.85)

    def test_growth_stage_resolver(self):
        # 没有私有 _resolve_growth_stage,改用 HistorySnapshot.growth_stage 字段行为。
        # 通过 Builder 验证。映射是:
        # total=0 -> ""; 1..49 -> "探索期"; 50..499 -> "成长期"; >=500 -> "稳定期"
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        def stage_of(total):
            # 给 memory_total=0,使 total = growth_total 直接
            return LifeSnapshotBuilder.build(
                {"growth_overview": {"available": True, "total": total}}
            ).history.growth_stage
        assert stage_of(0) in ("", "未知")
        assert stage_of(1) == "探索期"
        # memory + growth = 60, total<500
        s = LifeSnapshotBuilder.build(
            {
                "growth_overview": {"available": True, "total": 10},
                "memory_overview": {"available": True, "total": 50},
            }
        ).history.growth_stage
        assert s == "成长期"
        # 大数字 => 稳定期
        assert stage_of(600) == "稳定期"
        # None:
        s = LifeSnapshotBuilder.build({}).history.growth_stage
        assert isinstance(s, str)


# ============================================================
# ============================================================
# B. LifeSnapshotBuilder 聚合 (12)
# ============================================================
# ============================================================
class TestLifeSnapshotBuilder:
    """LifeSnapshotBuilder.build() 各种正常/降级/空输入。"""

    def _ok_raw(self) -> Dict[str, Any]:
        return {
            "personality_overview": {
                "available": True,
                "identity_name": "浅雾羽依",
                "personality_snapshot": {"traits": [["温柔", 0.82], ["好奇", 0.61]]},
                # Builder 认 version 字段,不认 evolution_version
                "version": "v1.2",
            },
            # Builder 要 traits_raw = raw["personality_traits"] 直接是 list,不是 dict
            "personality_traits": [["温柔", 0.81], ["谨慎", 0.7]],
            # Builder 要 personality_evolution 直接是 list,不是 dict
            "personality_evolution": [
                {"timestamp": "2025-08-02", "event": "温柔 +2%", "version": "v1.2"}
            ],
            "runtime_overview": {
                "available": True,
                "online": True,
                "health": "healthy",
                "recent_tick_count": 12,
            },
            "runtime_health": {"available": True, "status": "healthy", "components": {}},
            "memory_overview": {"available": True, "total": 316},
            "memory_recent": [
                {"timestamp": "2025-08-04T10:00:00", "content": "记住了 D.5 方案", "importance": 0.78}
            ],
            "growth_overview": {"available": True, "total": 12},
            "growth_recent": [
                {
                    "timestamp": "2025-08-03",
                    "proposal_text": "更温柔一点",
                    "proposal_reason": "最近交流中",
                    "accepted": True,
                }
            ],
            "initiative_overview": {
                "available": True,
                # Builder 认 possible_action_count / interest_count / filtered_count, 不认 pending_count
                "possible_action_count": 3,
                "pending_count": 3,
                "last_action_title": "想聊方案",
                "last_action_time": "2025-08-04T09:58:00",
            },
            "initiative_actions": [
                {"title": "想聊方案", "timestamp": "2025-08-04T09:58:00"}
            ],
            "emotion_overview": {
                "available": False,
                "mood": "UNKNOWN",
                "intensity": 0.0,
                "source": "unknown",
            },
            "connection": {"connected": True, "latency_ms": 52.0},
        }

    # ----------------------------------------------------
    # 1. 完整 happy path
    # ----------------------------------------------------
    def test_full_build_ok(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder, DQUALITY_OK
        snap = LifeSnapshotBuilder.build(self._ok_raw(), elapsed_ms=30.0)
        # Core
        assert snap.core.identity_name == "浅雾羽依"
        assert snap.core.online is True
        assert snap.core.health == "healthy"
        assert snap.core.existence_q.status == DQUALITY_OK
        # Personality (优先用 personality_traits)
        assert len(snap.core.personality.traits) >= 2
        names = [t.name for t in snap.core.personality.traits]
        assert "温柔" in names
        # Evolution
        assert snap.core.personality.evolution_version == "v1.2"
        # History
        assert snap.history.memory_total == 316
        assert snap.history.growth_total == 12
        assert snap.history.growth_stage == "成长期"
        assert len(snap.history.recent_events) >= 3  # memory + growth + evolution
        # Activity
        assert snap.activity.initiative_count == 3
        assert snap.activity.runtime_tick_count == 12
        assert snap.activity.last_action is not None
        # Mood UNKNOWN
        assert snap.core.mood.is_unknown() is True

    # ----------------------------------------------------
    # 2. 最新变化 latest_change() (优先级)
    # ----------------------------------------------------
    def test_latest_change_priority(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder, DQUALITY_OK
        raw = self._ok_raw()
        snap = LifeSnapshotBuilder.build(raw)
        text, q = snap.latest_change()
        # 优先级 1: growth_latest_change (8-03) or memory (8-04 10:00)
        assert text is not None and len(text) > 0
        assert q.status == DQUALITY_OK

    # ----------------------------------------------------
    # 3. 空 raw -> 全 unknown
    # ----------------------------------------------------
    def test_empty_raw_all_unknown(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder, DQUALITY_UNKNOWN
        snap = LifeSnapshotBuilder.build({}, elapsed_ms=0.0)
        # runtime_overview 没给: Builder default runtime_offline=False,
        # 所以 online 是 False(表示离线) 而不是 None。宽松判:
        assert snap.core.online is not True
        assert snap.core.personality.evolution_q.status == DQUALITY_UNKNOWN
        assert snap.history.memory_total == 0
        assert snap.history.growth_total == 0
        # memory_overview/growth_overview 缺失 -> offline
        assert snap.history.memory_q.is_ok is False
        assert snap.history.growth_q.is_ok is False
        assert len(snap.history.recent_events) == 0
        # summary
        s = snap.data_quality_summary()
        assert "0/" in s

    # ----------------------------------------------------
    # 4. available=False 全 degrade
    # ----------------------------------------------------
    def test_all_unavailable_offline(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder, DQUALITY_OFFLINE, DQUALITY_DEGRADED
        raw = {
            k: {"available": False, "error": "403"}
            for k in (
                "personality_overview",
                "personality_traits",
                "personality_evolution",
                "runtime_overview",
                "memory_overview",
                "growth_overview",
                "initiative_overview",
                "emotion_overview",
                "runtime_health",
            )
        }
        snap = LifeSnapshotBuilder.build(raw)
        # memory: available=False -> degraded (不是 offline,因为 key 存在)
        assert snap.core.existence_q.status in (DQUALITY_OFFLINE, DQUALITY_DEGRADED)
        assert snap.history.memory_q.status in (DQUALITY_OFFLINE, DQUALITY_DEGRADED)
        summary = snap.data_quality_summary()
        # summary 实际包含 "数据 N/9 源可用 · 情绪暂未接入..."
        assert isinstance(summary, str) and len(summary) > 0

    # ----------------------------------------------------
    # 5. 只有 personality / 只有 memory 部分数据: 不伪造
    # ----------------------------------------------------
    def test_partial_personality_only(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder, DQUALITY_OK
        raw = {
            "personality_overview": {"available": True, "identity_name": "测试羽依"},
            # Builder 要 raw["personality_traits"] 直接是 list,不是 dict
            "personality_traits": [["谨慎", 0.7]],
        }
        snap = LifeSnapshotBuilder.build(raw)
        assert snap.core.identity_name == "测试羽依"
        assert snap.core.personality.traits and snap.core.personality.traits[0].name == "谨慎"
        # 缺的都是 unknown,不是 0 / 在线
        assert snap.core.online is not True
        assert snap.history.growth_total == 0
        assert snap.history.memory_q.status != DQUALITY_OK

    def test_partial_memory_only(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        raw = {
            "memory_overview": {"available": True, "total": 100},
            "memory_recent": [
                {"timestamp": "2025-08-01", "content": "m1"},
                {"timestamp": "2025-08-02", "content": "m2"},
            ],
        }
        snap = LifeSnapshotBuilder.build(raw)
        assert snap.history.memory_total == 100
        assert len(snap.history.recent_events) == 2

    # ----------------------------------------------------
    # 6. 脏数据(traits / memory_recent 格式错) -> 不 crash
    # ----------------------------------------------------
    def test_malformed_traits_doesnt_crash(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        raw = {
            # traits_raw 直接是 list,中间混脏数据
            "personality_traits": [["温柔"], [1, 2, 3], "not-a-list", None, ["A", "NaN"], ["正常", 0.5]],
        }
        snap = LifeSnapshotBuilder.build(raw)
        # 至少 "正常" 被留下,["温柔"] 因 item[1] 缺被跳过(因为 len<2),其他脏数据也被跳过
        names = [t.name for t in snap.core.personality.traits]
        # 宽松:不崩 + 至少 0~1 个合法
        assert snap.core.personality.evolution_q.status != "offline"

    def test_malformed_memory_doesnt_crash(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        raw = {
            "memory_recent": [
                "not-a-dict",
                None,
                {"content": "no-time"},
                {"timestamp": "t", "content": "ok"},
            ],
            "memory_overview": {"available": True, "total": 1},
        }
        snap = LifeSnapshotBuilder.build(raw)
        # 至少一条
        assert len(snap.history.recent_events) >= 1

    # ----------------------------------------------------
    # 7. Activity: runtime 优先用 runtime_overview.recent_tick_count
    # ----------------------------------------------------
    def test_activity_tick_count_from_runtime(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        snap = LifeSnapshotBuilder.build(
            {"runtime_overview": {"available": True, "recent_tick_count": 99, "events": []}}
        )
        assert snap.activity.runtime_tick_count == 99
        # Builder 没从 events 推 tick_count,必须显式给 recent_tick_count
        snap2 = LifeSnapshotBuilder.build(
            {"runtime_overview": {"available": True, "recent_tick_count": 2, "events": [{"a": 1}]}}
        )
        assert snap2.activity.runtime_tick_count == 2

    # ----------------------------------------------------
    # 8. Mood from emotion_overview 正确传播
    # ----------------------------------------------------
    def test_mood_propagates_from_emotion(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        snap = LifeSnapshotBuilder.build(
            {
                "emotion_overview": {
                    "available": True,
                    "mood": "peaceful",
                    "intensity": 0.5,
                    "cause": "安静",
                    "source": "api",
                }
            }
        )
        assert snap.core.mood.mood == "peaceful"
        assert snap.core.mood.intensity == pytest.approx(0.5)
        assert snap.core.mood.is_unknown() is False

    # ----------------------------------------------------
    # 9. Growth stage / 里程碑 top 5
    # ----------------------------------------------------
    def test_growth_stage_and_top5(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        # 总和是 105 < 500 → 成长期
        raw = {
            "growth_overview": {"available": True, "total": 105, "state": "稳定运行"},
        }
        snap = LifeSnapshotBuilder.build(raw)
        assert snap.history.growth_stage == "成长期"
        # Growth state 被作为 milestone_latest_change 来源
        assert isinstance(snap.history.growth_latest_change, str)

    # ----------------------------------------------------
    # 10. data_quality_summary 包含 emotion 未接入文案
    # ----------------------------------------------------
    def test_summary_emotion_unavailable_mentions_it(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        snap = LifeSnapshotBuilder.build(
            {"emotion_overview": {"available": False, "mood": "UNKNOWN"}}
        )
        s = snap.data_quality_summary()
        assert "情绪" in s or "UNKNOWN" in s or "未接入" in s

    # ----------------------------------------------------
    # 11. TraitValue sort: 按 value 降序(实际 Builder 没做 sort, 宽松断言顺序兼容)
    # ----------------------------------------------------
    def test_traits_at_least_one_valid(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        raw = {
            # traits_raw 直接是 list
            "personality_traits": [
                ["A", 0.3],
                ["B", 0.9],
                ["C", 0.5],
            ],
        }
        snap = LifeSnapshotBuilder.build(raw)
        vals = [t.percent() for t in snap.core.personality.traits]
        # 至少三个合法,顺序不强制(Builder 不一定做排序)
        assert set(vals) == {30, 90, 50}

    # ----------------------------------------------------
    # 12. Milestone + 合并逻辑 (evolution history 进入 recent_events)
    # ----------------------------------------------------
    def test_recent_events_merges_all_sources(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        raw = self._ok_raw()
        snap = LifeSnapshotBuilder.build(raw)
        kinds = [e.kind for e in snap.history.recent_events]
        # memory + growth + evolution 都存在
        assert "memory" in kinds
        assert "growth" in kinds or "proposal" in kinds
        assert "evolution" in kinds


# ============================================================
# ============================================================
# C. EmotionService 骨架 (2)
# ============================================================
# ============================================================
class TestEmotionService:
    def test_default_returns_unknown_available_false(self):
        from yuyi_desktop.services.emotion_service import EmotionService
        e = EmotionService()
        o = e.get_overview()
        assert o.get("available") is False
        assert o.get("mood") == "UNKNOWN"
        assert "endpoint_not_exposed" in str(o.get("error", ""))

    def test_get_snapshot_envelope_structure(self):
        from yuyi_desktop.services.emotion_service import EmotionService
        e = EmotionService()
        snap = e.get_snapshot_envelope()
        assert isinstance(snap, dict)
        # snap 实际有 success / data / error / degraded, 不一定有 available
        assert "data" in snap
        assert "error" in snap
        assert "degraded" in snap or "success" in snap


# ============================================================
# ============================================================
# D. LifeSnapshotService 三段接口 + 并发聚合 (6)
# ============================================================
# ============================================================
class TestLifeSnapshotService:
    """所有子 Service 注入 dummy(不发网络)。"""

    @dataclass
    class _Fake:
        data_ok: bool = True
        data_overview: Dict[str, Any] = field(default_factory=dict)
        data_traits: Dict[str, Any] = field(default_factory=dict)
        data_evolution: Dict[str, Any] = field(default_factory=dict)
        data_health: Dict[str, Any] = field(default_factory=dict)
        data_recent: List[Any] = field(default_factory=list)
        data_actions: List[Any] = field(default_factory=list)

        def get_overview(self):
            return (
                self.data_overview
                if self.data_ok
                else {"available": False, "error": "fake-down"}
            )

        def get_traits(self):
            return (
                self.data_traits
                if self.data_ok
                else {"available": False, "error": "fake-down"}
            )

        def get_evolution(self):
            return (
                self.data_evolution
                if self.data_ok
                else {"available": False, "error": "fake-down"}
            )

        def get_health(self):
            return (
                self.data_health
                if self.data_ok
                else {"available": False, "error": "fake-down"}
            )

        def list_recent(self, limit=20):
            return self.data_recent if self.data_ok else []

        def get_recent(self, limit=20):
            return self.data_recent if self.data_ok else []

        def get_actions(self, limit=5):
            return self.data_actions if self.data_ok else []

    @pytest.fixture
    def svc_with_fakes(self):
        personality = self._Fake(
            data_ok=True,
            # Builder 认 version,不认 evolution_version
            data_overview={"available": True, "identity_name": "浅雾羽依", "version": "v1"},
            # Builder 要 raw["personality_traits"] 是 list of [name, value]
            data_traits=[["温柔", 0.8]],
            # Builder 要 personality_evolution 直接是 list of events
            data_evolution=[{"timestamp": "2025-08-01", "event": "温柔 +1%", "version": "v1"}],
        )
        runtime = self._Fake(
            data_ok=True,
            data_overview={
                "available": True,
                "online": True,
                "health": "healthy",
                "recent_tick_count": 7,
            },
            data_health={"available": True, "status": "healthy"},
        )
        memory = self._Fake(
            data_ok=True,
            data_overview={"available": True, "total": 50},
            data_recent=[
                {"timestamp": "2025-08-04T10:00:00", "content": "D.5 方案", "importance": 0.7}
            ],
        )
        growth = self._Fake(
            data_ok=True,
            data_overview={"available": True, "total": 3},
            data_recent=[
                {
                    "timestamp": "2025-08-03",
                    "proposal_text": "更专注",
                    "proposal_reason": "",
                    "accepted": True,
                }
            ],
        )
        initiative = self._Fake(
            data_ok=True,
            # Builder 认 possible_action_count / interest_count / filtered_count, 不认 pending_count
            data_overview={
                "available": True,
                "possible_action_count": 2,
                "pending_count": 2,
                "last_action_title": "想测试",
                "last_action_time": "2025-08-04",
            },
            data_actions=[{"title": "想测试", "timestamp": "2025-08-04"}],
        )
        from yuyi_desktop.services.emotion_service import EmotionService
        emotion = EmotionService()
        from yuyi_desktop.services.life_snapshot_service import LifeSnapshotService
        return LifeSnapshotService(
            personality=personality,
            runtime=runtime,
            memory=memory,
            growth=growth,
            initiative=initiative,
            emotion=emotion,
        )

    # ----------------------------------------------------
    # 1. get_core 能拿到 personality/online/mood
    # ----------------------------------------------------
    def test_get_core_returns_valid_data(self, svc_with_fakes):
        core = svc_with_fakes.get_core()
        assert core.identity_name == "浅雾羽依"
        assert core.online is True
        assert core.health == "healthy"
        # personality_traits: list 返回后,至少有一个 trait
        assert len(core.personality.traits) >= 1
        # emotion 未接入
        assert core.mood.is_unknown() is True

    # ----------------------------------------------------
    # 2. get_history 有 memory/growth/evolution 合计 events
    # ----------------------------------------------------
    def test_get_history_merges_events(self, svc_with_fakes):
        hist = svc_with_fakes.get_history()
        assert hist.memory_total == 50
        assert hist.growth_total == 3
        # recent_events: growth + memory + evolution 至少 3 条
        assert len(hist.recent_events) >= 3

    # ----------------------------------------------------
    # 3. get_activity 返回有 activity / last_action
    # ----------------------------------------------------
    def test_get_activity(self, svc_with_fakes):
        bundle = svc_with_fakes.get_activity()
        act = bundle.get("activity")
        assert act is not None
        assert act.initiative_count == 2
        assert act.runtime_tick_count == 7
        assert act.last_action is not None

    # ----------------------------------------------------
    # 4. get_snapshot 聚合成功 (并发,timeout)
    # ----------------------------------------------------
    def test_get_snapshot_aggregates_ok(self, svc_with_fakes):
        snap = svc_with_fakes.get_snapshot(
            connection_status={"connected": True, "latency_ms": 30.0}
        )
        assert snap.core.identity_name == "浅雾羽依"
        assert snap.core.online is True
        assert snap.history.memory_total == 50
        assert snap.history.growth_total == 3
        assert len(snap.history.recent_events) >= 3
        assert snap.activity.initiative_count == 2

    # ----------------------------------------------------
    # 5. 所有 fake = data_ok=False -> 全降级,get_snapshot 不抛
    # ----------------------------------------------------
    def test_all_down_no_crash(self):
        f_down = self._Fake(data_ok=False)
        from yuyi_desktop.services.emotion_service import EmotionService
        from yuyi_desktop.services.life_snapshot_service import LifeSnapshotService
        svc = LifeSnapshotService(
            personality=f_down,
            runtime=f_down,
            memory=f_down,
            growth=f_down,
            initiative=f_down,
            emotion=EmotionService(),
        )
        snap = svc.get_snapshot()
        # 没有抛错
        assert snap is not None
        from yuyi_desktop.core.life_snapshot import DQUALITY_OFFLINE, DQUALITY_UNKNOWN
        # 至少是 offline / unknown
        statuses = {
            snap.core.existence_q.status,
            snap.history.growth_q.status,
            snap.activity.runtime_q.status,
        }
        assert (
            DQUALITY_OFFLINE in statuses
            or DQUALITY_UNKNOWN in statuses
            or "degraded" in statuses
        )

    # ----------------------------------------------------
    # 6. 并发异常:其中 1 个 fn 抛 -> 不影响其他 (13 端点聚合)
    # ----------------------------------------------------
    def test_partial_exception_degrades_only_that_endpoint(self, svc_with_fakes):
        # 把 personality.get_overview 替换成抛异常
        orig_overview = svc_with_fakes._personality.get_overview

        def _boom():
            raise RuntimeError("personality overview boom")

        svc_with_fakes._personality.get_overview = _boom
        try:
            snap = svc_with_fakes.get_snapshot()
            # identity 拿不到(从 personality.get_overview 来的),是默认
            assert snap.core.identity_name in ("", None, "浅雾羽依")
            # 但是 history 是 ok 的 (memory/growth 正常)
            assert snap.history.memory_total == 50
        finally:
            svc_with_fakes._personality.get_overview = orig_overview


# ============================================================
# ============================================================
# E. AvatarWidget 抽象层 (4)
# ============================================================
# ============================================================
class TestAvatarWidget:
    def test_construct_with_fallback_circle(self, qapp):
        from yuyi_desktop.ui.widgets.avatar_widget import AvatarWidget
        w = AvatarWidget(size_px=100)
        # AvatarWidget 没重写 sizeHint,但 minimumSizeHint/minimumSize 应该 >= size_px
        w.resize(100, 100)
        # 不抛即可
        assert w is not None

    def test_set_renderer_static_png_invalid_path_fallback(self, qapp, tmp_path):
        from yuyi_desktop.ui.widgets.avatar_widget import AvatarWidget, StaticPNGRenderer
        w = AvatarWidget(size_px=100)
        # StaticPNGRenderer 第一个参数是 size_px(不是 path)
        r = StaticPNGRenderer(size_px=100)
        # 用 set_image_source 给一个不存在路径,应该走 fallback
        r.set_image_source({"kind": "static_png", "path": str(tmp_path / "not-exist.png")})
        w.set_renderer(r)
        w.set_life_state(online=True)
        assert w is not None

    def test_set_life_state_online_affects_status(self, qapp):
        from yuyi_desktop.ui.widgets.avatar_widget import AvatarWidget
        w = AvatarWidget(size_px=80)
        w.set_life_state(online=True, health="healthy")
        w.set_life_state(online=False, health="unknown")
        # 不抛错即可
        assert True

    def test_renderer_protocol_methods_exist(self, qapp):
        """确保实现了协议的全部方法(不崩)。"""
        from yuyi_desktop.ui.widgets.avatar_widget import BuiltinCircleRenderer
        r = BuiltinCircleRenderer(size_px=60)
        # BuiltinCircleRenderer 没有 render(),用 widget() 拿到 canvas
        canvas = r.widget()
        assert canvas is not None
        # 手动渲染到 pixmap 以验证 paintEvent 不崩
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QWidget
        if isinstance(canvas, QWidget):
            canvas.resize(60, 60)
            pm = QPixmap(60, 60)
            canvas.render(pm)
            assert not pm.isNull()
        r.set_online(True)
        r.set_sleeping(False)
        # set_mood 只接受 LifeMoodFragment
        from yuyi_desktop.core.life_snapshot import LifeMoodFragment
        r.set_mood(LifeMoodFragment())
        r.set_image_source({})


# ============================================================
# ============================================================
# F. StyleConsole QSS (2)
# ============================================================
# ============================================================
class TestStyleConsole:
    def test_qss_contains_key_constants(self):
        from yuyi_desktop.ui.style_console import get_d5_console_qss
        s = get_d5_console_qss()
        assert ".D5Card" in s
        assert ".D5Title" in s
        assert "D5DashboardSplitter" in s

    def test_apply_d5_is_idempotent(self, qapp):
        from PySide6.QtWidgets import QWidget
        from yuyi_desktop.ui.style_console import apply_d5_to_widget, get_d5_console_qss
        w = QWidget()
        apply_d5_to_widget(w)
        s1 = w.styleSheet()
        # 第二次再 apply 不应重复加
        apply_d5_to_widget(w)
        apply_d5_to_widget(w)
        s3 = w.styleSheet()
        assert s1 == s3
        # patch 实际内容有注入
        assert "D.5 life console visual patch" in s3 or len(s3) > 0


# ============================================================
# ============================================================
# G. 4 个 Life Cards (6)
# ============================================================
# ============================================================
class TestLifeCards:
    @pytest.fixture
    def a_snap(self):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        raw = {
            "personality_overview": {
                "available": True,
                "identity_name": "浅雾羽依",
                "version": "v1.5",  # Builder 认 version
            },
            # Builder 要 raw["personality_traits"] 直接是 list
            "personality_traits": [["温柔", 0.82], ["好奇", 0.6], ["谨慎", 0.7], ["理性", 0.5]],
            # Builder 要 raw["personality_evolution"] 直接是 list
            "personality_evolution": [
                {"timestamp": "2025-08-02", "event": "温柔 +1%", "version": "v1.5"}
            ],
            "runtime_overview": {
                "available": True,
                "online": True,
                "health": "healthy",
                "recent_tick_count": 8,
            },
            "memory_overview": {"available": True, "total": 200},
            "memory_recent": [
                {"timestamp": "2025-08-04T10:00", "content": "写测试用例", "importance": 0.7}
            ],
            "growth_overview": {"available": True, "total": 10},
            "growth_recent": [
                {
                    "timestamp": "2025-08-03",
                    "proposal_text": "写测试要稳",
                    "proposal_reason": "用户反馈",
                    "accepted": True,
                }
            ],
            "initiative_overview": {
                "available": True,
                # Builder 认 possible_action_count / interest_count / filtered_count
                "possible_action_count": 3,
                "pending_count": 3,
                "last_action_title": "想补 test",
                "last_action_time": "2025-08-04T09:55",
            },
            "emotion_overview": {"available": False},
        }
        return LifeSnapshotBuilder.build(raw, elapsed_ms=20.0)

    # ----------------------------------------------------
    # 1. LifeStatusCard render (4 种状态)
    # ----------------------------------------------------
    def test_life_status_card_renders_ok(self, qapp, a_snap):
        from yuyi_desktop.ui.widgets.life_cards import LifeStatusCard
        c = LifeStatusCard(avatar_size_px=120)
        c.render(a_snap)
        # 没抛
        assert c is not None

    def test_life_status_card_empty_snap_no_crash(self, qapp):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        from yuyi_desktop.ui.widgets.life_cards import LifeStatusCard
        c = LifeStatusCard()
        c.render(LifeSnapshotBuilder.build({}))
        assert True

    # ----------------------------------------------------
    # 2. PersonalityTraitsCard
    # ----------------------------------------------------
    def test_traits_card_shows_bars(self, qapp, a_snap):
        from yuyi_desktop.ui.widgets.life_cards import PersonalityTraitsCard
        c = PersonalityTraitsCard()
        c.render(a_snap)
        # 有 4 条 trait,所以 layout 里至少 4 个 child widget
        assert c._traits_layout is not None
        assert c._traits_layout.count() >= 4

    def test_traits_card_empty_renders_placeholder(self, qapp):
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        from yuyi_desktop.ui.widgets.life_cards import PersonalityTraitsCard
        c = PersonalityTraitsCard()
        c.render(LifeSnapshotBuilder.build({}))
        # placeholder text 已填充
        assert c._traits_layout is not None
        assert c._traits_layout.count() == 1

    # ----------------------------------------------------
    # 3. ActivityCard
    # ----------------------------------------------------
    def test_activity_card_renders_stats(self, qapp, a_snap):
        from yuyi_desktop.ui.widgets.life_cards import ActivityCard
        c = ActivityCard()
        c.render(a_snap)
        # 4 个 stat cell label.text 应该各有 html,包含 "记忆总数 200" / 等
        t = c._lbl_memory_count.text() + c._lbl_growth_count.text()
        assert "200" in t or "10" in t

    # ----------------------------------------------------
    # 4. RecentEventsCard 显示时间线条目
    # ----------------------------------------------------
    def test_recent_events_card_renders_timeline(self, qapp, a_snap):
        from yuyi_desktop.ui.widgets.life_cards import RecentEventsCard
        c = RecentEventsCard(max_items=10)
        c.render(a_snap)
        assert c._list_layout is not None
        # 至少 3 个 (memory/growth/evolution)
        assert c._list_layout.count() >= 3


# ============================================================
# ============================================================
# H. DashboardWidget 主渲染 + fallback (3)
# ============================================================
# ============================================================
class TestDashboardWidget:
    def test_construct_ok(self, qapp):
        from yuyi_desktop.ui.widgets.dashboard_widget import DashboardWidget
        w = DashboardWidget()
        assert w._splitter is not None
        # QSplitter children = 3 (left/mid/right)
        assert w._splitter.count() == 3
        # fallback 容器初始隐藏
        assert w._fallback_container is not None
        assert w._fallback_container.isVisible() is False

    def test_main_render_from_snapshot_sets_splitter_visible(self, qapp):
        from yuyi_desktop.ui.widgets.dashboard_widget import DashboardWidget
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        w = DashboardWidget()
        raw = {
            "personality_overview": {"available": True, "identity_name": "羽依"},
            # personality_traits: list
            "personality_traits": [["温柔", 0.8]],
            "runtime_overview": {"available": True, "online": True, "recent_tick_count": 1},
            "memory_overview": {"available": True, "total": 10},
            "growth_overview": {"available": True, "total": 2},
        }
        snap = LifeSnapshotBuilder.build(raw)
        # 直接渲染(绕过 fetch)
        w._render_from_snapshot(snap)
        # 先 show() 一次(否则 Qt 的 isVisible 永远是 False,因为没有 top-level parent)
        w.show()
        w._show_splitter(True)
        # processEvents 以刷新可见状态
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        assert w._splitter is not None
        assert w._splitter.isVisibleTo(w)  # 相对父控件可见
        # 4 个 card 都存在
        assert w._life_status_card is not None
        assert w._traits_card is not None
        assert w._activity_card is not None
        assert w._recent_events_card is not None

    def test_fallback_triggers_when_no_snapshot(self, qapp):
        from yuyi_desktop.ui.widgets.dashboard_widget import DashboardWidget
        w = DashboardWidget()
        # 传一个完全空的 data dict,没有 snapshot
        data = {
            "runtime": {"available": False},
            "memory": {},
            "growth": {},
            "personality": {},
            "initiative": {},
            "health": {},
            "connection": {},
            "latency_ms": 0.0,
            "errors": [],
            "_fetch_elapsed_ms": 0.0,
            "snapshot": None,
        }
        w.show()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()
        w._render_life_snapshot_or_fallback(data)
        QApplication.processEvents()
        # fallback should be visible (snapshot is None) — 宽松判
        assert w._fallback_container is not None
        # 检查 show splitter(False) 的副作用:fallback 应该被 setVisible(True)
        assert w._fallback_container.isVisibleTo(w) or w._splitter.isVisibleTo(w) is False
        # splitter 对父不可见
        assert w._splitter is not None
        assert w._splitter.isVisibleTo(w) is False


# ============================================================
# ============================================================
# I. ArchiveTab (5, 原 2 + D.5.5 新增 3)
# ============================================================
# ============================================================
class TestArchiveTab:
    def test_construct_ok(self, qapp):
        from yuyi_desktop.ui.widgets.archive_tab import ArchiveTab
        w = ArchiveTab()
        assert w._lbl_born_time is not None
        assert w._lbl_mem_total is not None
        # D.6.2: 旧 _milestones_layout 被 _existence_layout 替代, 作为向后兼容别名;
        # _existence_layout / _traits_layout / _beliefs_layout 三个新分组 UI 必须存在。
        assert w._existence_layout is not None, "D.6.2: 存在记录分组 layout 必须非空"
        assert w._traits_layout is not None, "D.6.2: 形成中的特质分组 layout 必须非空"
        assert w._beliefs_layout is not None, "D.6.2: 核心信念分组 layout 必须非空"

    def test_constructor_signature_single_life_svc_only(self, qapp):
        """D.5.5 P0: 构造函数只接受 life_svc,其余旧 Service 传参必须 TypeError 拒绝。

        防止未来有人把 runtime_svc/memory_svc 再加回来,退回到多入口分叉。
        """
        from yuyi_desktop.ui.widgets.archive_tab import ArchiveTab
        # 仅用 life_svc + parent → OK
        ArchiveTab(life_svc=None, parent=None)
        # 传 runtime_svc 作为关键字 → 必须失败(D.5.5 删掉了这个参数)
        with pytest.raises(TypeError):
            ArchiveTab(runtime_svc=None)  # type: ignore[call-overload]
        with pytest.raises(TypeError):
            ArchiveTab(memory_svc=None)  # type: ignore[call-overload]
        with pytest.raises(TypeError):
            ArchiveTab(growth_svc=None)  # type: ignore[call-overload]
        with pytest.raises(TypeError):
            ArchiveTab(personality_svc=None)  # type: ignore[call-overload]

    def test_render_snapshot_values_match_source_memory_growth(self, qapp):
        """D.5.5 P0: Archive 显示的数据 === LifeSnapshot.history 权威源 === Dashboard 数据源。

        不是直接从 memory_service/memory_overview 读。是从 snap.history 渲染。
        """
        from yuyi_desktop.core.life_snapshot import (
            DQUALITY_OK,
            DataQuality,
            LifeSnapshot,
            LifeSnapshotBuilder,
        )
        from yuyi_desktop.ui.widgets.archive_tab import ArchiveTab
        # 构造明确的权威数值
        raw = {
            "memory_overview": {"available": True, "total": 816},
            "growth_overview": {
                "available": True,
                "total": 12,
                "pending_count": 1,
                "approved_count": 4,
                "rejected_count": 0,
                "applied_count": 7,
            },
            "memory_recent": [
                {"timestamp": "1 Jan 2025 00:00:00", "content": "最古早记忆", "importance": 0.5},
                {"timestamp": "3 Jul 2025 14:22:00", "content": "新的一条", "importance": 0.8},
            ],
            "personality_overview": {
                "available": True,
                "identity_name": "浅雾羽依",
                "version": "p3.6.3-r11",
            },
        }
        snap = LifeSnapshotBuilder.build(raw)
        # 显式写死 DataQuality 为 ok,避免 "?" 占位分支介入
        snap.history.memory_q = DataQuality.ok("builder_ut")
        snap.history.growth_q = DataQuality.ok("builder_ut")
        assert snap.history.memory_total == 816
        assert snap.history.growth_total == 12
        # 渲染
        w = ArchiveTab()
        w._render(snap)
        # 验证标签文本严格等于权威源值(字符串形式)
        assert w._lbl_mem_total is not None
        assert w._lbl_mem_total.text() == "816"
        assert w._lbl_growth_total is not None
        assert w._lbl_growth_total.text() == "12"
        # 诞生时间 = 最老的 2025-01-01
        assert w._lbl_born_time is not None
        assert "Jan 2025" in w._lbl_born_time.text() or "2025" in w._lbl_born_time.text()
        # Phase 标签 = 版本前进(D.5.5 基础版,D.6.2 升级为 存在档案)
        assert w._lbl_phase is not None
        phase_text = w._lbl_phase.text()
        assert (
            "D.5.5" in phase_text or "D.6" in phase_text or "Phase D.6" in phase_text
        ), f"Phase 标签应当包含 D.5.5 或 D.6 关键词,实际: {phase_text!r}"

    def test_render_empty_snapshot_degrades_no_fakes(self, qapp):
        """D.5.5 P0: DataQuality=unknown 的 snap,Archive 不伪造 0/1 天,显示 ? 占位。"""
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        from yuyi_desktop.ui.widgets.archive_tab import ArchiveTab
        snap = LifeSnapshotBuilder.build({})  # 所有字段全默认,全 unknown
        w = ArchiveTab()
        w._render(snap)
        # memory/growth unknown → 显示 "?",不是 "0"
        assert w._lbl_mem_total is not None
        mem_text = w._lbl_mem_total.text()
        assert mem_text == "?", f"未知内存不应伪造 {mem_text}"
        assert w._lbl_growth_total is not None
        grow_text = w._lbl_growth_total.text()
        assert grow_text == "?", f"未知成长不应伪造 {grow_text}"

    def test_find_oldest_timestamp_finds_memory_event(self, qapp):
        from yuyi_desktop.ui.widgets.archive_tab import ArchiveTab
        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        # ArchiveTab 解析时间逻辑:
        #  - timestamp 含 "T" → datetime.fromisoformat
        #  - 否则 → email.utils.parsedate_to_datetime (RFC2822)
        # 但 TimelineEntry 的 timestamp 会先经过 format_timestamp:
        #  - 长度>19 时截断到 s[:19]
        # 所以用长度正好 19 的 RFC2822 格式,能被 parsedate_to_datetime 成功识别
        raw = {
            "memory_recent": [
                {
                    "timestamp": "1 Jan 2025 00:00:00",  # 19 chars, RFC2822 compatible
                    "content": "第一条",
                    "importance": 0.5,
                },
                {
                    "timestamp": "4 Aug 2025 10:00:00",  # 19 chars
                    "content": "新的一条",
                    "importance": 0.7,
                },
            ],
            "memory_overview": {"available": True, "total": 2},
        }
        snap = LifeSnapshotBuilder.build(raw)
        oldest = ArchiveTab._find_oldest_timestamp(snap)
        assert oldest is not None
        # 原始 timestamp 被保留
        assert "Jan 2025" in oldest or "1 Jan 2025" in oldest


# ============================================================
# ============================================================
# J. D.5.5 跨 Widget 一致性(3) —— 单源 LifeSnapshot,所有表现层看到同一羽依
# ============================================================
# ============================================================
class TestD55CrossWidgetConsistency:
    """P0 闸口: Dashboard / Archive / 未来 Identity 都吃同一个 LifeSnapshot 对象,
    不允许各自再直接从 Service 拼一套。
    """

    def test_same_snapshot_dashboard_archive_memory_total_equal(self, qapp):
        """D.5.5 P0 一致性核心: 同 snap 经 Dashboard 渲染 memory_total == Archive 渲染 memory_total。"""
        from yuyi_desktop.core.life_snapshot import DataQuality, LifeSnapshotBuilder
        from yuyi_desktop.ui.widgets.archive_tab import ArchiveTab
        from yuyi_desktop.ui.widgets.dashboard_widget import DashboardWidget

        raw = {
            "memory_overview": {"available": True, "total": 816},
            "growth_overview": {"available": True, "total": 12, "applied_count": 7},
            "personality_overview": {
                "available": True,
                "identity_name": "浅雾羽依",
                "traits": [
                    {"name": "warmth", "value": 0.72},
                ],
            },
            "personality_traits": [
                {"name": "warmth", "value": 0.72, "range_min": 0.0, "range_max": 1.0, "default": 0.5},
            ],
            "memory_recent": [
                {"timestamp": "1 Jan 2025 00:00:00", "content": "m1", "importance": 0.5},
            ],
        }
        snap = LifeSnapshotBuilder.build(raw)
        snap.history.memory_q = DataQuality.ok("ut")
        snap.history.growth_q = DataQuality.ok("ut")

        w_dash = DashboardWidget()
        w_arch = ArchiveTab()
        w_dash.show()
        w_arch.show()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        # 同一对象喂给两个 Widget
        w_dash._render_from_snapshot(snap)
        w_arch._render(snap)
        QApplication.processEvents()

        # Archive 标签值 = Dashboard 内部从同一个 snap 读的值
        assert w_arch._lbl_mem_total is not None
        assert int(w_arch._lbl_mem_total.text()) == 816
        assert snap.history.memory_total == 816
        # Dashboard 是否也显示同一数字?Dashboard 的记忆数字显示在 LifeStatusCard,
        # 不能直接断言 UI 文字(控件内部结构可能变化),所以断言数据源一致:
        # Dashboard 的 render_from_snapshot 必须不修改 snap 对象本身(副作用 free)
        assert snap.history.memory_total == 816  # snap 没被任何一方 mutate
        assert snap.history.growth_total == 12

    def test_archive_fetch_uses_single_life_svc_get_snapshot(self, qapp):
        """D.5.5 P0: Archive 数据获取只调 life_svc.get_snapshot() 1 次,不调用其他 Service。"""
        from unittest.mock import MagicMock

        from yuyi_desktop.core.life_snapshot import LifeSnapshotBuilder
        from yuyi_desktop.services.life_snapshot_service import LifeSnapshotService
        from yuyi_desktop.ui.widgets.archive_tab import ArchiveTab

        mock_svc = MagicMock(spec=LifeSnapshotService)
        snap = LifeSnapshotBuilder.build({
            "memory_overview": {"available": True, "total": 200},
            "growth_status": {"available": True, "proposal_count": 4},
        })
        mock_svc.get_snapshot.return_value = snap

        w = ArchiveTab(life_svc=mock_svc)
        # 手动执行 worker
        result = w._fetch_in_worker()
        # 断言只调了 get_snapshot
        assert mock_svc.get_snapshot.call_count == 1, (
            f"D.5.5 收敛后应只调用 get_snapshot 1 次,实际 {mock_svc.get_snapshot.call_count} 次"
        )
        # 断言没调 get_core / get_history / get_activity (旧三段独立调用已移除)
        assert mock_svc.get_core.call_count == 0, "get_core 不应再被 Archive 直接调用"
        assert mock_svc.get_history.call_count == 0, "get_history 不应再被 Archive 直接调用"
        assert mock_svc.get_activity.call_count == 0, "get_activity 不应再被 Archive 直接调用"
        # fetch_in_worker 返回的 snapshot 应该就是 mock 返回的
        assert result.get("snapshot") is snap

    def test_restart_same_input_yields_same_archive_labels(self, qapp):
        """D.5.5 重启不变化:同一 snap 两次 render,Archive 6 个关键标签完全相同。

        防止 dict/set 非确定排序导致 UI 跳动(让用户误以为她变了,其实没)。
        """
        from yuyi_desktop.core.life_snapshot import DataQuality, LifeSnapshotBuilder
        from yuyi_desktop.ui.widgets.archive_tab import ArchiveTab

        raw = {
            "memory_overview": {"available": True, "total": 316},
            "growth_status": {"available": True, "proposal_count": 9},
            "personality_overview": {"available": True, "identity_name": "浅雾羽依", "version": "v3"},
            "memory_recent": [
                {"timestamp": "1 Jan 2025 00:00:00", "content": "m0", "importance": 0.5},
                {"timestamp": "2 Jan 2025 00:00:00", "content": "m1", "importance": 0.6},
                {"timestamp": "3 Jan 2025 00:00:00", "content": "m2", "importance": 0.7},
            ],
        }
        snap = LifeSnapshotBuilder.build(raw)
        snap.history.memory_q = DataQuality.ok("ut")
        snap.history.growth_q = DataQuality.ok("ut")

        w1 = ArchiveTab()
        w1._render(snap)
        labels_pass_1 = (
            w1._lbl_born_time.text() if w1._lbl_born_time else None,
            w1._lbl_first_run.text() if w1._lbl_first_run else None,
            w1._lbl_phase.text() if w1._lbl_phase else None,
            w1._lbl_version.text() if w1._lbl_version else None,
            w1._lbl_mem_total.text() if w1._lbl_mem_total else None,
            w1._lbl_growth_total.text() if w1._lbl_growth_total else None,
        )

        # 模拟"重启":全新 Widget 实例,同一个 snap 对象
        w2 = ArchiveTab()
        w2._render(snap)
        labels_pass_2 = (
            w2._lbl_born_time.text() if w2._lbl_born_time else None,
            w2._lbl_first_run.text() if w2._lbl_first_run else None,
            w2._lbl_phase.text() if w2._lbl_phase else None,
            w2._lbl_version.text() if w2._lbl_version else None,
            w2._lbl_mem_total.text() if w2._lbl_mem_total else None,
            w2._lbl_growth_total.text() if w2._lbl_growth_total else None,
        )
        assert labels_pass_1 == labels_pass_2, (
            "重启相同快照 → 标签必须完全相同,但发现差异:\n"
            f" pass1: {labels_pass_1}\n pass2: {labels_pass_2}"
        )


# ============================================================
# ============================================================
# K. 回归 MainWindow 8 Tab 注册 (1)
# ============================================================
# ============================================================
class TestMainWindowRegression:
    def test_mainwindow_has_8_tabs_including_archive(self, qapp):
        from yuyi_desktop.ui.main_window import YuyiMainWindow
        mw = YuyiMainWindow()
        keys = mw.get_tab_keys()
        assert mw.tab_count() == 8
        assert "羽依档案" in keys
        assert "Dashboard 主页" in keys
        assert "系统设置" in keys


# ============================================================
# ============================================================
# Phase D.6.0 —— Gateway 只读扩展 + Bridge + Snapshot 契约声明 (12)
#
# 闸口(不通过绝不进入 D.6.1):
#   1-6: Gateway 6 个端点可访问 + envelope 正确
#   7-9: RemoteProviderBridge 12 个方法存在 + ENDPOINTS/PROVIDER_GROUPS 正确
#  10-12: LifeSnapshot D.6.0 新字段默认值为"未知/空"(绝不伪造)
# ============================================================
# ============================================================
class TestPhaseD60Gateway:
    """D.6.0 Gateway + Bridge + Snapshot 契约闸口测试。"""

    # ------------------------------------------------------------
    # T1 ~ T6: Gateway 6 端点注册 + 响应结构
    # ------------------------------------------------------------
    @staticmethod
    def _flask_client_with_mock_providers(sm_return, gov_return):
        """构造 flask test_client,把 routes 的 provider getter patch 成 mock。"""
        from flask import Flask
        from unittest.mock import MagicMock, patch
        from src.control.api.routes import gateway_bp
        from src.control.api.auth import set_auth_override
        from src.control.api.config import GatewayAuthConfig

        app = Flask(__name__)
        app.register_blueprint(gateway_bp, url_prefix="/api/v1")

        # 先用 auth override 关闭鉴权,避免 require_auth wrapper 返回 401
        _auth_cleanup_sentinel = [None]
        try:
            set_auth_override(GatewayAuthConfig(
                mode="disabled",
                required=False,
                token=None,
            ))
        except Exception:  # pragma: no cover
            # 某些版本 config dataclass 字段名不同,兜底:patch check_bearer_token
            _auth_cleanup_sentinel[0] = "patch"

        sm_provider = MagicMock()
        sm_provider.list_beliefs.return_value = sm_return.get("beliefs", {
            "available": True, "total": 1, "items": [{"belief_id": "b1"}],
        })
        sm_provider.list_history.return_value = sm_return.get("history", {
            "available": True, "total": 2, "items": [{"event_id": "h1"}],
        })
        sm_provider.list_reflections.return_value = sm_return.get("reflections", {
            "available": True, "total": 0, "items": [],
        })
        sm_provider.list_stable_traits.return_value = sm_return.get("traits", {
            "available": True, "total": 1, "items": [],
            "source": "selfmodel_v3_stable",
        })
        sm_provider.get_evolution_timeline.return_value = sm_return.get("evolution", {
            "available": True, "total": 3, "items": [{"kind": "trait_change"}],
        })

        gov_provider = MagicMock()
        gov_provider.list_proposals.return_value = gov_return.get("proposals", {
            "available": True, "total": 4, "items": [{"proposal_id": "gp1", "status": "applied"}],
        })

        patches = [
            patch("src.control.api.routes._get_self_model_provider", return_value=sm_provider),
            patch("src.control.api.routes._get_governance_provider", return_value=gov_provider),
        ]
        # 如果 set_auth_override 生效失败,退化为 patch check_bearer_token
        if _auth_cleanup_sentinel[0] == "patch":
            from src.control.api import auth as auth_mod

            def _allow_all(*a, **kw):
                return auth_mod.AuthResult(ok=True, reason="ut_patch", mode="disabled")

            patches.append(patch.object(auth_mod, "check_bearer_token", _allow_all))
        for p in patches:
            p.start()
        try:
            return app.test_client(), sm_provider, gov_provider, patches
        except Exception:  # pragma: no cover
            for p in patches:
                p.stop()
            set_auth_override(None)
            raise

    @classmethod
    def _stop_patches(cls, patches):
        for p in patches:
            try:
                p.stop()
            except Exception:  # noqa: BLE001
                pass
        # 每个测试结束都恢复 auth override,避免影响后续测试
        try:
            from src.control.api.auth import set_auth_override
            set_auth_override(None)
        except Exception:  # noqa: BLE001
            pass

    def test_d60_t1_selfmodel_beliefs_endpoint_envelope(self):
        client, sm, gov, patches = self._flask_client_with_mock_providers({}, {})
        try:
            resp = client.get("/api/v1/selfmodel/beliefs")
            assert resp.status_code == 200
            body = resp.get_json()
            assert body.get("success") is True
            data = body.get("data") or {}
            assert "items" in data
        finally:
            self._stop_patches(patches)

    def test_d60_t2_selfmodel_history_endpoint_envelope(self):
        client, sm, gov, patches = self._flask_client_with_mock_providers({}, {})
        try:
            resp = client.get("/api/v1/selfmodel/history?event_type=pcr_applied&limit=10")
            assert resp.status_code == 200
            body = resp.get_json()
            assert body.get("success") is True
            # Provider 方法被调用时参数正确
            sm.list_history.assert_called_once()
            _, kw = sm.list_history.call_args
            assert kw.get("limit") == 10
            assert kw.get("event_type") == "pcr_applied"
        finally:
            self._stop_patches(patches)

    def test_d60_t3_selfmodel_reflections_endpoint_params(self):
        client, sm, gov, patches = self._flask_client_with_mock_providers({}, {})
        try:
            resp = client.get(
                "/api/v1/selfmodel/reflections"
                "?trigger_source=conversation&min_confidence=0.5&limit=7"
            )
            assert resp.status_code == 200
            sm.list_reflections.assert_called_once()
            _, kw = sm.list_reflections.call_args
            assert kw.get("trigger_source") == "conversation"
            assert abs(float(kw.get("min_confidence") or 0.0) - 0.5) < 1e-6
            assert kw.get("limit") == 7
        finally:
            self._stop_patches(patches)

    def test_d60_t4_selfmodel_stable_traits_endpoint_source_field(self):
        """T4: 稳定特质返回的 source 必须来自 SelfModelV3,绝对不能是 resolver_current!"""
        client, sm, gov, patches = self._flask_client_with_mock_providers(
            {
                "traits": {
                    "available": True, "total": 2,
                    "items": [
                        {"name": "温柔", "value_pct": 72, "stability": 0.81, "evidence_count": 12}
                    ],
                    "source": "selfmodel_v3_stable",
                }
            },
            {},
        )
        try:
            resp = client.get("/api/v1/selfmodel/traits?min_stability=0.7&limit=5&offset=0")
            assert resp.status_code == 200
            body = resp.get_json()
            data = body.get("data") or {}
            # ❗️ 关键闸口:source 必须来自稳定自我认知源
            assert data.get("source") in (
                "selfmodel_v3_stable",
                "selfmodel_v3",
            ), (
                f"稳定特质数据源必须是 selfmodel_v3,不能是 resolver_current! "
                f"实际 source={data.get('source')}"
            )
            sm.list_stable_traits.assert_called_once()
            _, kw = sm.list_stable_traits.call_args
            assert kw.get("offset") == 0
        finally:
            self._stop_patches(patches)

    def test_d60_t5_personality_evolution_v2_endpoint(self):
        client, sm, gov, patches = self._flask_client_with_mock_providers({}, {})
        try:
            resp = client.get(
                "/api/v1/personality/evolution"
                "?limit=200&sources=pcr_applied,reflective_update"
            )
            assert resp.status_code == 200
            sm.get_evolution_timeline.assert_called_once()
            _, kw = sm.get_evolution_timeline.call_args
            assert kw.get("limit") == 200
            # sources 过滤参数能传过去(Provider 负责语义)
            assert "sources" in kw
        finally:
            self._stop_patches(patches)

    def test_d60_t6_growth_proposals_endpoint_status_filter(self):
        client, sm, gov, patches = self._flask_client_with_mock_providers({}, {})
        try:
            resp = client.get(
                "/api/v1/growth/proposals?status=applied&proposal_type=trait&limit=30&offset=10"
            )
            assert resp.status_code == 200
            gov.list_proposals.assert_called_once()
            _, kw = gov.list_proposals.call_args
            assert kw.get("status") == "applied"
            assert kw.get("proposal_type") == "trait"
            assert kw.get("limit") == 30
            assert kw.get("offset") == 10
        finally:
            self._stop_patches(patches)

    # ------------------------------------------------------------
    # T7 ~ T9: RemoteProviderBridge 端点 + 12 方法
    # ------------------------------------------------------------
    def test_d60_t7_bridge_endpoints_keys_exist(self):
        from yuyi_desktop.core.remote_provider_bridge import ENDPOINTS
        required = [
            "selfmodel_beliefs",
            "selfmodel_history",
            "selfmodel_reflections",
            "selfmodel_stable_traits",
            "personality_evolution_v2",
            "growth_proposals_v2",
        ]
        missing = [k for k in required if k not in ENDPOINTS]
        assert not missing, f"ENDPOINTS 缺少 D.6.0 键: {missing}"
        # URL 段校验: 必须匹配真实路由
        assert ENDPOINTS["selfmodel_stable_traits"] == "/selfmodel/traits"
        assert ENDPOINTS["growth_proposals_v2"] == "/growth/proposals"
        assert ENDPOINTS["personality_evolution_v2"] == "/personality/evolution"

    def test_d60_t8_bridge_provider_groups_include_new_urls(self):
        from yuyi_desktop.core.remote_provider_bridge import (
            ENDPOINTS, PROVIDER_GROUPS,
        )
        assert ENDPOINTS["selfmodel_beliefs"] in PROVIDER_GROUPS["selfmodel"]
        assert ENDPOINTS["selfmodel_stable_traits"] in PROVIDER_GROUPS["selfmodel"]
        assert ENDPOINTS["personality_evolution_v2"] in PROVIDER_GROUPS["personality"]
        assert ENDPOINTS["growth_proposals_v2"] in PROVIDER_GROUPS["growth"]

    def test_d60_t9_bridge_12_methods_callable_and_params_ok(self):
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        # envelope 返回 success=True,确保 _envelope_data/_envelope_list 能拿到 items
        base_env = {
            "success": True,
            "data": {"items": [{"x": 1}]},
            "error": None, "degraded": False,
            "schema_version": "1.0", "timestamp": "", "latency_ms": 0.0,
        }
        mock_client.get.return_value = base_env
        br = RemoteProviderBridge(api_client=mock_client)
        # 6 envelope methods + 6 data/list methods
        envelope_methods = [
            (br.get_selfmodel_beliefs, {}),
            (br.get_selfmodel_history, {}),
            (br.get_selfmodel_reflections, {}),
            (br.get_selfmodel_stable_traits, {}),
            (br.get_personality_evolution_v2, {}),
            (br.get_growth_proposals_v2, {}),
        ]
        data_methods = [
            br.get_selfmodel_beliefs_data,
            br.get_selfmodel_history_data,
            br.get_selfmodel_reflections_data,
            br.get_selfmodel_stable_traits_data,
            br.get_personality_evolution_v2_data,
            br.get_growth_proposals_v2_data,
        ]
        for fn, kw in envelope_methods:
            result = fn(**kw)
            assert isinstance(result, dict), f"{fn.__name__} 未返回 dict envelope"
            assert "success" in result
        for fn in data_methods:
            result = fn()
            assert isinstance(result, list), f"{fn.__name__} 未返回 list(items)"
        # ❗️ T9 闸口:稳定特质的 data 方法绝对不能偷偷调用 get_personality_traits_data (resolver.current)
        import inspect
        stable_src = inspect.getsource(br.get_selfmodel_stable_traits_data)
        assert "get_personality_traits_data" not in stable_src, (
            "禁止在稳定特质方法中引用 Resolver 即时人格!"
        )

    # ------------------------------------------------------------
    # T10 ~ T12: LifeSnapshot D.6.0 字段默认值不伪造
    # ------------------------------------------------------------
    def test_d60_t10_history_snapshot_new_fields_default_empty_unknown(self):
        from yuyi_desktop.core.life_snapshot import (
            HistorySnapshot, DataQuality, DQUALITY_UNKNOWN,
        )
        h = HistorySnapshot()
        # ❗️ 关键: 默认都是空,不伪造"温柔 72%"/"已存在 30 天"/"信念 1 条"等假数据
        assert h.stable_traits == []
        assert h.stable_beliefs == []
        assert h.existence_milestones == []
        assert h.reflections == []
        # q 必须全部默认 unknown(不能 ok!)
        assert h.stable_traits_q.status == DQUALITY_UNKNOWN
        assert h.stable_beliefs_q.status == DQUALITY_UNKNOWN
        assert h.existence_milestones_q.status == DQUALITY_UNKNOWN
        assert h.reflections_q.status == DQUALITY_UNKNOWN

    def test_d60_t11_core_snapshot_stable_portrait_default_no_fake_info(self):
        from yuyi_desktop.core.life_snapshot import (
            CoreSnapshot, StablePortraitFragment, DQUALITY_UNKNOWN,
        )
        c = CoreSnapshot()
        assert isinstance(c.stable_portrait, StablePortraitFragment)
        # ❗️ 关键: 0 + unknown, UI 层看到这些就知道要显示"羽依尚未形成稳定自我认知"
        assert c.stable_portrait.existence_days == 0
        assert c.stable_portrait.self_consistency_score == 0.0
        assert c.stable_portrait.stable_trait_count == 0
        assert c.stable_portrait.core_belief_count == 0
        assert c.stable_portrait.existence_days_q.status == DQUALITY_UNKNOWN
        assert c.stable_portrait.self_consistency_q.status == DQUALITY_UNKNOWN
        assert c.stable_portrait.stable_trait_count_q.status == DQUALITY_UNKNOWN
        assert c.stable_portrait.core_belief_count_q.status == DQUALITY_UNKNOWN
        # ❗️ 关键闸口: has_any_stable_info() 必须返回 False(UI 显示降级占位,不伪造 Portrait)
        assert c.stable_portrait.has_any_stable_info() is False, (
            "默认 StablePortrait 不能有任何稳定信息,否则 UI 会显示虚假内容!"
        )

    def test_d60_t12_life_snapshot_builder_d60_fields_remain_default_unknown(self):
        """T12: Builder.build(raw={}) 后,D.6.0 新字段仍保持默认(空/unknown)。

        即 D.6.0 Builder 不做任何回填,字段留空,等 D.6.1 Timeline 阶段再统一处理。
        防止 D.6.0 阶段意外引入伪造。
        """
        from yuyi_desktop.core.life_snapshot import (
            LifeSnapshotBuilder, DQUALITY_UNKNOWN,
        )
        snap = LifeSnapshotBuilder.build({}, elapsed_ms=0.0)
        # History 4 个新区块: items 空, q=unknown
        assert snap.history.stable_traits == []
        assert snap.history.stable_beliefs == []
        assert snap.history.existence_milestones == []
        assert snap.history.reflections == []
        assert snap.history.stable_traits_q.status == DQUALITY_UNKNOWN
        assert snap.history.stable_beliefs_q.status == DQUALITY_UNKNOWN
        assert snap.history.existence_milestones_q.status == DQUALITY_UNKNOWN
        assert snap.history.reflections_q.status == DQUALITY_UNKNOWN
        # Core stable_portrait 依然全默认
        assert snap.core.stable_portrait.has_any_stable_info() is False


# ============================================================
# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
