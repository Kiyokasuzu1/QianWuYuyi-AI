# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/life_snapshot.py

Phase D.5 —— LifeSnapshot 统一生命状态层(Core / History / Activity 三段式)。

所有表现层(Dashboard / Archive / Live2D / Voice / Doll)统一读取同一个
LifeSnapshot, 避免每个 Widget 各自请求、各自解释数据导致状态不一致。

核心设计:
- 三段式拆分:CoreSnapshot + HistorySnapshot + ActivitySnapshot
  * Core: 身份 / 在线 / 人格 / 情绪(低频 10s, Live2D 只订阅这段)
  * History: Memory / Growth / Evolution(低频 30s, Archive 主要使用)
  * Activity: Initiative / Runtime 事件(高频 5s, Dashboard 活动卡)
- LifeSnapshot = Core + History + Activity 聚合体(对外读)
- 所有字段 Optional + DataQuality 标记,不伪造,优雅降级
- 纯数据类 + 纯函数 Builder, 不直接 import src.*, 不发起网络请求

约束:
- 不修改 src/**, 不新增 AI 模块, 只做数据聚合转换
- 任何字段解析失败都走 DataQuality 降级, 不抛错
- 不伪造默认数据: Emotion 无端点 -> mood=UNKNOWN; Growth 0 -> 阶段=未知
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# 复用 Phase D.3 已验证的 TimelineEntry + 解析 + 排序函数
from yuyi_desktop.ui.widgets.state_visualization import (
    TimelineEntry,
    parse_evolution_entries,
    parse_growth_entries,
    parse_memory_entries,
    sort_entries_by_time_desc,
    format_timestamp,
)

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
# DataQuality.status 取值
DQUALITY_OK = "ok"
DQUALITY_DEGRADED = "degraded"
DQUALITY_UNKNOWN = "unknown"
DQUALITY_OFFLINE = "offline"

DQUALITY_STATUSES = (DQUALITY_OK, DQUALITY_DEGRADED, DQUALITY_UNKNOWN, DQUALITY_OFFLINE)

# 卡片视觉常量(留给渲染层)
VIZ_ROUND_RADIUS = 12
VIZ_SPACING_PX = 16
VIZ_ACCENT_COLOR = "#8a7cff"  # 柔和蓝紫色重点色
VIZ_ACCENT_SOFT = "#b3a9ff"   # 更柔和的版本

# 默认安全值
DEFAULT_IDENTITY_NAME = ""  # P1-2: 不写死人格名,后端无数据时显示空
DEFAULT_MOOD_UNKNOWN = "UNKNOWN"


# ============================================================
# DataQuality —— 每个字段的可靠性标记
# ============================================================
@dataclass
class DataQuality:
    """单个/一组字段的数据可靠性标记。

    用于 UI 层:
    - ok        -> 正常显示
    - degraded  -> 显示但加 "数据可能过期" 灰色小字角标
    - unknown   -> 显示 "—" 或 "未接入",绝不伪造默认值
    - offline   -> 显示 "离线" 灰色

    注意: 禁止用 DataQuality 丢失与否来决定生成假数据。
    """

    status: str = DQUALITY_UNKNOWN
    source: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if self.status not in DQUALITY_STATUSES:
            self.status = DQUALITY_UNKNOWN

    @classmethod
    def ok(cls, source: str = "") -> "DataQuality":
        return cls(status=DQUALITY_OK, source=source, error="")

    @classmethod
    def degraded(cls, source: str = "", error: str = "") -> "DataQuality":
        return cls(status=DQUALITY_DEGRADED, source=source, error=error)

    @classmethod
    def offline(cls, source: str = "", error: str = "") -> "DataQuality":
        return cls(status=DQUALITY_OFFLINE, source=source, error=error)

    @classmethod
    def unknown(cls, source: str = "", error: str = "") -> "DataQuality":
        return cls(status=DQUALITY_UNKNOWN, source=source, error=error)

    @property
    def is_ok(self) -> bool:
        return self.status == DQUALITY_OK

    @property
    def is_unreliable(self) -> bool:
        """unknown 或 offline, UI 应显示降级占位。"""
        return self.status in (DQUALITY_UNKNOWN, DQUALITY_OFFLINE)


# ============================================================
# 子段(Fragments)
# ============================================================
@dataclass
class TraitValue:
    name: str = ""
    value: float = 0.0     # 0.0 ~ 1.0
    q: DataQuality = field(default_factory=DataQuality)

    def clamp(self) -> None:
        try:
            f = float(self.value)
            if f < 0.0:
                self.value = 0.0
            elif f > 1.0:
                self.value = 1.0
        except (TypeError, ValueError):
            self.value = 0.0
            self.q = DataQuality.unknown(error="trait_value_cast_failed")

    def percent(self) -> int:
        self.clamp()
        return int(round(self.value * 100.0))


@dataclass
class PersonalityFragment:
    traits: List[TraitValue] = field(default_factory=list)
    evolution_version: str = ""
    evolution_q: DataQuality = field(default_factory=DataQuality)


# ============================================================
# Phase D.6.0: Archive 用纯字段契约声明(绝不伪造,不填数据)
#
# D.6.0 只声明字段,Builder 暂时不填值(q=unknown,items=[])。
# D.6.1 Timeline / D.6.2 Archive UI 实际读取 D.6.0 Gateway 6 个端点后填充。
#
# 分类:
#   StablePortraitFragment -> Core 层(稳定自我认知摘要)
#   StableTraitEntry       -> History 层【形成中的特质】
#   StableBeliefEntry      -> History 层【核心信念】
#   ExistenceMilestone     -> History 层【存在记录】Timeline
# ============================================================

@dataclass
class StableTraitEntry:
    """SelfModelV3 稳定特质条目。

    来源: 仅来自 GET /api/v1/selfmodel/traits (SelfModelProvider.list_stable_traits)
    ❗️ 禁止从 Resolver.current / personality/traits 复制值冒充稳定特质!
    """
    trait_id: str = ""
    name: str = ""
    value_pct: int = 0                 # 0~100, 和 Provider 返回的 value_pct 对齐
    stability: float = 0.0             # 0~1, 越高越稳定
    evidence_count: int = 0
    first_observed: str = ""
    last_observed: str = ""
    trend_30d: Optional[float] = None  # 正=上升,负=下降,None=数据不足
    q: DataQuality = field(default_factory=DataQuality)


@dataclass
class StableBeliefEntry:
    """SelfModel 稳定信念条目。

    来源: 仅来自 GET /api/v1/selfmodel/beliefs
    """
    belief_id: str = ""
    domain: str = "value"
    content: str = ""
    confidence: float = 0.0
    version: int = 1
    evidence_count: int = 1
    sources: List[str] = field(default_factory=list)
    active: bool = True
    first_seen: str = ""
    last_confirmed: str = ""
    q: DataQuality = field(default_factory=DataQuality)


@dataclass
class ExistenceMilestone:
    """羽依存在记录的里程碑(D.6 Growth Timeline 主数据结构)。

    一条记录就是"在某个时间点发生了某件真实的、影响羽依状态的事"。
    示例:
      date="2026-08-02" title="温柔倾向增强 +0.02"
        source="growth_proposal_applied" kind="growth" confidence=0.82
      date="2026-07-16" title="第一次建立长期记忆系统"
        source="selfmodel_history" kind="milestone" confidence=1.0

    来源(三源合并,不编造):
      * growth_proposals   -> GET /api/v1/growth/proposals (status=applied/approved)
      * personality_evo    -> GET /api/v1/personality/evolution
      * selfmodel_history  -> GET /api/v1/selfmodel/history
    """
    date: str = ""                     # yyyy-mm-dd, UI 按日期分组展示
    timestamp_ms: int = 0              # 精确时间,用于排序
    title: str = ""                    # 一句话事实,不做解释层
    kind: str = ""                     # growth/belief/pcr_applied/milestone/memory_formed
    source: str = ""                   # growth/proposal/evolution/selfmodel_history/memory
    confidence: float = 0.0            # 0~1, 来自 Provider 原始 confidence
    evidence_count: int = 0            # 有多少原始记录支持这条(用于 UI 角标"12次互动"等)
    extra: Dict[str, Any] = field(default_factory=dict)  # 原始字段透传,不做结构化
    q: DataQuality = field(default_factory=DataQuality)


@dataclass
class StablePortraitFragment:
    """羽依稳定画像摘要(Core 层,低频 60s+)。

    D.6.0 只声明字段;D.6.3 StablePortrait 正式完成后 Builder 填充。
    所有字段默认值为"未知",绝不伪造默认"温柔72%"/"已存在 30 天"之类。
    """
    # 存在时长(从 selfmodel_status 推导,不准确就填 0 + q.unknown)
    existence_days: int = 0
    existence_days_q: DataQuality = field(default_factory=DataQuality)

    # 自我一致性(0~1, 来自 belief 稳定性 + traits 稳定度均值,不确定则 0 + q.unknown)
    self_consistency_score: float = 0.0
    self_consistency_q: DataQuality = field(default_factory=DataQuality)

    # 已确立的核心特质数量(有 >= stability>=0.7 的 trait 计数;稳定特质 0 -> 未知 q)
    stable_trait_count: int = 0
    stable_trait_count_q: DataQuality = field(default_factory=DataQuality)

    # 已确立的核心信念数量(active=true & confidence>=0.8 的 beliefs 计数)
    core_belief_count: int = 0
    core_belief_count_q: DataQuality = field(default_factory=DataQuality)

    def has_any_stable_info(self) -> bool:
        """UI 层用:只有当至少有一个稳定字段是 ok/degraded 时才显示 Portrait 区,否则显示"羽依尚未形成稳定自我认知"。"""
        return (
            not self.existence_days_q.is_unreliable
            or not self.self_consistency_q.is_unreliable
            or not self.stable_trait_count_q.is_unreliable
            or not self.core_belief_count_q.is_unreliable
        )


@dataclass
class LifeMoodFragment:
    """情绪状态。

    严格不伪造:
    - 后端无端点  -> mood=UNKNOWN, source=unknown, q=unknown
    - 后端有端点  -> mood=xxx, source=emotion_api, q=ok/degraded
    - 未来若支持推测: source=inferred, UI 需显式显示 "(推测)" 角标
    """

    mood: str = DEFAULT_MOOD_UNKNOWN
    intensity: float = 0.0
    cause: str = ""
    source: str = "unknown"   # emotion_api / inferred / unknown
    q: DataQuality = field(default_factory=DataQuality)

    def is_unknown(self) -> bool:
        return (
            not self.mood
            or self.mood.upper() == DEFAULT_MOOD_UNKNOWN
            or self.q.is_unreliable
        )


# ============================================================
# 三段式子快照(Core / History / Activity)
# ============================================================
@dataclass
class CoreSnapshot:
    """核心身份 + 存在 + 人格 + 情绪。

    刷新频率建议 10s。Live2D/Voice 只订阅这段,避免每次拖慢。
    """

    # identity
    identity_name: str = DEFAULT_IDENTITY_NAME
    identity_q: DataQuality = field(default_factory=DataQuality)

    # existence (活着?)
    online: Optional[bool] = None
    health: str = "unknown"
    existence_q: DataQuality = field(default_factory=DataQuality)

    # mood (情绪)
    mood: LifeMoodFragment = field(default_factory=LifeMoodFragment)

    # personality (人格切片)
    personality: PersonalityFragment = field(default_factory=PersonalityFragment)

    # ---- Phase D.6.0: 稳定自我认知(低频 60s+;D.6.3 前 Builder 不填) ----
    stable_portrait: StablePortraitFragment = field(default_factory=StablePortraitFragment)

    def source_available_count(self) -> int:
        n = 0
        if self.identity_q.is_ok:
            n += 1
        if self.existence_q.is_ok:
            n += 1
        if self.mood.q.is_ok:
            n += 1
        if self.personality.evolution_q.is_ok:
            n += 1
        # D.6.3+ stable_portrait 有 q.ok 再计入
        return n

    def source_total_count(self) -> int:
        return 4


@dataclass
class HistorySnapshot:
    """历史相关(记忆/成长/演化)。

    刷新频率建议 30s。Archive Tab 主要消费者。
    """

    growth_stage: str = "未知"       # 探索期 / 成长期 / 稳定期 / 未知(绝不伪造)
    growth_total: int = 0
    growth_latest_change: str = ""
    growth_q: DataQuality = field(default_factory=DataQuality)

    memory_total: int = 0
    memory_latest_event: str = ""
    memory_q: DataQuality = field(default_factory=DataQuality)

    # 最近 N 条时间线(三源合并 + 倒序)
    recent_events: List[TimelineEntry] = field(default_factory=list)

    # ---- Phase D.6.0: Archive 三大区 纯字段契约声明 (items 默认 [], q=unknown) ----
    # 【形成中的特质】 StableTraits  —— 仅来自 /selfmodel/traits (稳定特质,非即时切片)
    stable_traits: List[StableTraitEntry] = field(default_factory=list)
    stable_traits_q: DataQuality = field(default_factory=DataQuality)

    # 【核心信念】 StableBeliefs  —— 仅来自 /selfmodel/beliefs
    stable_beliefs: List[StableBeliefEntry] = field(default_factory=list)
    stable_beliefs_q: DataQuality = field(default_factory=DataQuality)

    # 【存在记录】 Growth Timeline Milestones (D.6.0 旧占位: 轻量扁平结构)
    # ⚠️ 保留字段仅为向后兼容;D.6.2+ UI 请使用 existence_timeline(完整 D.6.1 合同对象)
    existence_milestones: List[ExistenceMilestone] = field(default_factory=list)
    existence_milestones_q: DataQuality = field(default_factory=DataQuality)

    # 【反思】 Self Reflections —— 来自 /selfmodel/reflections
    reflections: List[Dict[str, Any]] = field(default_factory=list)
    reflections_q: DataQuality = field(default_factory=DataQuality)

    # =====================================================================
    # Phase D.6.2.0: 正式接入 D.6.1 ExistenceMilestone 合同(纯字段声明,不填值)
    #
    # existence_timeline: D.6.1 正式合同结构, 带 EvidenceReference/ImpactMap/ExplanationMetadata
    #   - 来源: LifeSnapshotService 内部调用 build_existence_timeline(3 Adapter 合并)
    #   - UI 不调用 Builder, 只从 LifeSnapshot.history 读取
    #   - 默认值: [] + q=UNKNOWN (绝不伪造默认人生)
    #
    # 兼容策略: 旧 existence_milestones 不删除, q=unknown 时 UI 自动降级到 existence_timeline
    # =====================================================================
    existence_timeline: List[Any] = field(default_factory=list)
    existence_quality: DataQuality = field(default_factory=DataQuality)

    # 【存在记录统计摘要】(来自 TimelineBuildStats,UI 展示 "50/124 passed" 类)
    existence_stats: Dict[str, Any] = field(default_factory=dict)
    existence_stats_q: DataQuality = field(default_factory=DataQuality)

    def source_available_count(self) -> int:
        n = 0
        if self.growth_q.is_ok:
            n += 1
        if self.memory_q.is_ok:
            n += 1
        # D.6.2+: 新增 existence_timeline / stable_traits / stable_beliefs 的 ok 状态也计入
        if not self.existence_quality.is_unreliable:
            n += 1
        if not self.stable_traits_q.is_unreliable:
            n += 1
        if not self.stable_beliefs_q.is_unreliable:
            n += 1
        return n

    def source_total_count(self) -> int:
        # 2 (原 growth/memory) + 3 (D.6.2: timeline / traits / beliefs)
        return 5


@dataclass
class ActivitySnapshot:
    """近期活动(主动行为 + Runtime 事件)。

    刷新频率建议 5s。Dashboard 活动卡消费者。
    """

    last_action: str = ""
    last_action_time: str = ""
    activity_q: DataQuality = field(default_factory=DataQuality)

    initiative_count: int = 0   # 可能的主动行为候选数 / 已发数,仅展示用
    initiative_q: DataQuality = field(default_factory=DataQuality)

    runtime_tick_count: int = 0
    runtime_q: DataQuality = field(default_factory=DataQuality)

    def source_available_count(self) -> int:
        n = 0
        if self.activity_q.is_ok:
            n += 1
        if self.initiative_q.is_ok:
            n += 1
        if self.runtime_q.is_ok:
            n += 1
        return n

    def source_total_count(self) -> int:
        return 3


# ============================================================
# LifeSnapshot —— 聚合体
# ============================================================
@dataclass
class LifeSnapshot:
    """统一生命状态。Dashboard/Archive/Live2D 都读它。"""

    core: CoreSnapshot = field(default_factory=CoreSnapshot)
    history: HistorySnapshot = field(default_factory=HistorySnapshot)
    activity: ActivitySnapshot = field(default_factory=ActivitySnapshot)

    # 聚合体层面的元信息
    snapshot_epoch_ms: int = 0
    fetch_elapsed_ms: float = 0.0

    # 便捷: 最近变化一句话(三选一 fallback)
    def latest_change(self) -> Tuple[str, DataQuality]:
        """最近一次变化摘要(从 history 三源 fallback 链取)。

        优先级:
          growth_latest_change -> memory_latest_event -> activity.last_action -> 空
        返回: (text, quality)  调用方根据 quality.is_unreliable 决定占位。
        """
        if self.history.growth_latest_change and self.history.growth_q.is_ok:
            return self.history.growth_latest_change, self.history.growth_q
        if self.history.memory_latest_event and self.history.memory_q.is_ok:
            return self.history.memory_latest_event, self.history.memory_q
        if self.activity.last_action and self.activity.activity_q.is_ok:
            return self.activity.last_action, self.activity.activity_q
        # degraded 级的内容也显示(但带 q.degraded)
        for text, q in (
            (self.history.growth_latest_change, self.history.growth_q),
            (self.history.memory_latest_event, self.history.memory_q),
            (self.activity.last_action, self.activity.activity_q),
        ):
            if text and q.status == DQUALITY_DEGRADED:
                return text, q
        return "", DataQuality.unknown(error="no_latest_change_source")

    def source_available_count(self) -> int:
        return (
            self.core.source_available_count()
            + self.history.source_available_count()
            + self.activity.source_available_count()
        )

    def source_total_count(self) -> int:
        return (
            self.core.source_total_count()
            + self.history.source_total_count()
            + self.activity.source_total_count()
        )

    def data_quality_summary(self) -> str:
        """Dashboard 底部状态栏一句话。"""
        ok = self.source_available_count()
        total = self.source_total_count()
        mood_tag = ""
        if self.core.mood.is_unknown():
            mood_tag = " · 情绪暂未接入(后端未暴露端点)"
        elif self.core.mood.source == "inferred":
            mood_tag = " · 情绪为推测值"
        elapsed = self.fetch_elapsed_ms
        elapsed_tag = f" · 聚合 {elapsed:.0f}ms" if elapsed > 0 else ""
        return f"数据 {ok}/{total} 源可用{mood_tag}{elapsed_tag}"

    # 序列化(给测试/调试,不传网络)
    def to_dict(self) -> Dict[str, Any]:
        return {
            "core": {
                "identity_name": self.core.identity_name,
                "online": self.core.online,
                "health": self.core.health,
                "mood": {
                    "mood": self.core.mood.mood,
                    "intensity": self.core.mood.intensity,
                    "cause": self.core.mood.cause,
                    "source": self.core.mood.source,
                },
                "traits": [
                    {"name": t.name, "value": t.value, "q": t.q.status}
                    for t in self.core.personality.traits
                ],
            },
            "history": {
                "growth_stage": self.history.growth_stage,
                "growth_total": self.history.growth_total,
                "memory_total": self.history.memory_total,
                "recent_events_count": len(self.history.recent_events),
                # D.6.2: 只展示条数,不展开(避免 dict 过大)
                "stable_traits_count": len(self.history.stable_traits),
                "stable_beliefs_count": len(self.history.stable_beliefs),
                "existence_milestones_v0_count": len(self.history.existence_milestones),
                "existence_timeline_count": len(self.history.existence_timeline),
                "existence_quality": {
                    "status": self.history.existence_quality.status,
                    "source": self.history.existence_quality.source,
                    "error": self.history.existence_quality.error,
                },
                "existence_stats_tail": (
                    dict(list(self.history.existence_stats.items())[:30])
                    if isinstance(self.history.existence_stats, dict)
                    else {}
                ),
            },
            "activity": {
                "last_action": self.activity.last_action,
                "initiative_count": self.activity.initiative_count,
            },
            "snapshot_epoch_ms": self.snapshot_epoch_ms,
            "fetch_elapsed_ms": self.fetch_elapsed_ms,
        }


# ============================================================
# LifeSnapshotBuilder —— 纯函数式聚合器
#
# 输入: raw dict(由 LifeSnapshotService 从 6+1 Service 并发拉取后组装)
# 输出: LifeSnapshot
# ============================================================
class LifeSnapshotBuilder:
    """把 6 个 Service 返回的原始 dict 聚合成 LifeSnapshot。

    纯函数、无网络、无锁、可重入、异常不扩散。
    每一步解析失败都会设置对应 DataQuality.xxx(error=...) 并继续。
    """

    # ---- Public 接口 --------------------------------------------------
    @classmethod
    def build(cls, raw: Dict[str, Any], elapsed_ms: float = 0.0) -> LifeSnapshot:
        if not isinstance(raw, dict):
            raw = {}
        snap = LifeSnapshot(fetch_elapsed_ms=float(elapsed_ms or 0.0))
        snap.snapshot_epoch_ms = int(time.time() * 1000)
        try:
            cls._fill_core(snap.core, raw)
            cls._fill_history(snap.history, raw)
            cls._fill_activity(snap.activity, raw)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[LifeSnapshotBuilder] build 异常(已降级): %s", exc)
        return snap

    # ---- Core ----------------------------------------------------------
    @classmethod
    def _fill_core(cls, core: CoreSnapshot, raw: Dict[str, Any]) -> None:
        # Identity
        personality_overview = raw.get("personality_overview")
        if isinstance(personality_overview, dict):
            name = str(
                personality_overview.get("identity_name")
                or ""
            ).strip()
            if name:
                core.identity_name = name
                core.identity_q = DataQuality.ok(source="personality_overview")
            else:
                core.identity_q = DataQuality.degraded(
                    source="personality_overview", error="identity_name_empty"
                )
        else:
            core.identity_q = DataQuality.offline(
                source="personality_overview", error="personality_missing"
            )

        # Existence
        runtime_overview = raw.get("runtime_overview")
        conn_status = raw.get("connection") or {}
        health_raw = raw.get("runtime_health") or {}
        online = None
        health_str = "unknown"
        if isinstance(runtime_overview, dict):
            online = bool(
                runtime_overview.get("online")
                or runtime_overview.get("status", {}).get("online")
                if isinstance(runtime_overview.get("status"), dict)
                else runtime_overview.get("online")
            )
            health_str = str(
                runtime_overview.get("health") or runtime_overview.get("v2", {}).get("health")
                if isinstance(runtime_overview.get("v2"), dict)
                else runtime_overview.get("health") or "unknown"
            ).lower()
        if isinstance(conn_status, dict) and online is None:
            online = bool(conn_status.get("connected"))
        if isinstance(health_raw, dict):
            h = str(
                health_raw.get("status") or health_raw.get("state") or ""
            ).lower()
            if h:
                health_str = h
        core.online = online
        core.health = health_str or "unknown"
        if isinstance(runtime_overview, dict) or isinstance(health_raw, dict):
            ok_flags = [
                isinstance(runtime_overview, dict) and bool(runtime_overview.get("available")),
                isinstance(health_raw, dict) and bool(health_raw.get("success"))
                or str(health_raw.get("status", "")).lower()
                in ("ok", "healthy", "up", "running", "degraded", "warn"),
            ]
            if any(ok_flags):
                core.existence_q = DataQuality.ok(source="runtime")
            else:
                core.existence_q = DataQuality.degraded(
                    source="runtime", error="runtime_not_fully_available"
                )
        else:
            core.existence_q = DataQuality.offline(source="runtime", error="runtime_missing")

        # Mood(绝不伪造: emotion_overview 缺失 -> UNKNOWN)
        emotion_overview = raw.get("emotion_overview")
        if isinstance(emotion_overview, dict) and bool(emotion_overview.get("available")):
            mood_val = str(emotion_overview.get("mood") or "").strip()
            if mood_val:
                core.mood.mood = mood_val
                try:
                    core.mood.intensity = max(
                        0.0,
                        min(1.0, float(emotion_overview.get("intensity", 0.0) or 0.0)),
                    )
                except (TypeError, ValueError):
                    core.mood.intensity = 0.0
                core.mood.cause = str(emotion_overview.get("cause") or "")
                core.mood.source = "emotion_api"
                core.mood.q = DataQuality.ok(source="emotion_api")
            else:
                core.mood.q = DataQuality.degraded(
                    source="emotion_api", error="mood_value_empty"
                )
        else:
            # 无端点 or available=false -> UNKNOWN, 不推测
            core.mood.mood = DEFAULT_MOOD_UNKNOWN
            core.mood.source = "unknown"
            reason = ""
            if isinstance(emotion_overview, dict):
                reason = str(emotion_overview.get("error") or "emotion_not_available")
            else:
                reason = "emotion_endpoint_missing"
            core.mood.q = DataQuality.unknown(source="emotion", error=reason)

        # Personality traits + evolution
        cls._fill_personality(core.personality, raw)

    @classmethod
    def _fill_personality(cls, pers: PersonalityFragment, raw: Dict[str, Any]) -> None:
        # Evolution version
        personality_overview = raw.get("personality_overview")
        version = ""
        if isinstance(personality_overview, dict):
            v = personality_overview.get("version")
            if v is not None and str(v) != "":
                version = str(v)
        if version:
            pers.evolution_version = version
            pers.evolution_q = DataQuality.ok(source="personality_overview.version")
        else:
            pers.evolution_q = DataQuality.unknown(
                source="personality_overview", error="evolution_version_empty"
            )

        # Traits(来源 personality_traits: list[dict] or list[tuple])
        pers.traits.clear()
        traits_raw = raw.get("personality_traits")
        if not isinstance(traits_raw, list) or not traits_raw:
            # 允许从 personality_overview.personality_v2.traits 退化
            if isinstance(personality_overview, dict):
                v2 = personality_overview.get("personality_v2")
                snap = None
                if isinstance(v2, dict):
                    snap = v2.get("snapshot") or v2.get("traits")
                if isinstance(snap, dict):
                    tr = snap.get("traits")
                    if isinstance(tr, list):
                        traits_raw = tr
                elif isinstance(snap, list):
                    traits_raw = snap
        if isinstance(traits_raw, list):
            for item in traits_raw:
                name = ""
                value = 0.0
                q = DataQuality.ok(source="personality_traits")
                try:
                    if isinstance(item, dict):
                        name = str(item.get("name") or item.get("trait") or "").strip()
                        v = item.get("value") or item.get("score") or 0.0
                        value = float(v)
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        name = str(item[0] or "").strip()
                        value = float(item[1])
                    else:
                        q = DataQuality.unknown(error="trait_item_unknown_format")
                except (TypeError, ValueError):
                    q = DataQuality.unknown(error="trait_value_cast_failed")
                    value = 0.0
                if not name:
                    continue
                tv = TraitValue(name=name, value=value, q=q)
                tv.clamp()
                pers.traits.append(tv)
            if pers.traits:
                # ok: 至少有一个 trait
                if pers.evolution_q.status == DQUALITY_UNKNOWN:
                    pers.evolution_q = DataQuality.ok(source="personality_traits")

    # ---- History -------------------------------------------------------
    @classmethod
    def _fill_history(cls, hist: HistorySnapshot, raw: Dict[str, Any]) -> None:
        # Growth
        growth_overview = raw.get("growth_overview")
        if isinstance(growth_overview, dict):
            try:
                hist.growth_total = int(growth_overview.get("total") or 0)
            except (TypeError, ValueError):
                hist.growth_total = 0
            hist.growth_q = (
                DataQuality.ok(source="growth_overview")
                if bool(growth_overview.get("available"))
                else DataQuality.degraded(source="growth_overview", error="growth_degraded")
            )
            # latest change: 从 growth_recent 第一条取(下面统一处理)
        else:
            hist.growth_q = DataQuality.offline(source="growth_overview", error="growth_missing")

        # Memory
        memory_overview = raw.get("memory_overview")
        if isinstance(memory_overview, dict):
            try:
                hist.memory_total = int(
                    memory_overview.get("total")
                    or memory_overview.get("total_count")
                    or 0
                )
            except (TypeError, ValueError):
                hist.memory_total = 0
            hist.memory_q = (
                DataQuality.ok(source="memory_overview")
                if bool(memory_overview.get("available"))
                else DataQuality.degraded(source="memory_overview", error="memory_degraded")
            )
        else:
            hist.memory_q = DataQuality.offline(source="memory_overview", error="memory_missing")

        # Growth stage(启发式,绝不伪造默认"探索期")
        stage = cls._heuristic_growth_stage(hist.growth_total, hist.memory_total)
        if stage:
            hist.growth_stage = stage
        else:
            hist.growth_stage = "未知"

        # 最近经历(三源合并 + 排序 + TOP 12,实际 UI 只取前 6)
        hist.recent_events.clear()
        try:
            growth_recent = raw.get("growth_recent") or []
            memory_recent = raw.get("memory_recent") or []
            personality_evolution = raw.get("personality_evolution") or []
            gr = parse_growth_entries(growth_recent) if isinstance(growth_recent, list) else []
            mr = parse_memory_entries(memory_recent) if isinstance(memory_recent, list) else []
            pr = parse_evolution_entries(personality_evolution) if isinstance(personality_evolution, list) else []
            merged: List[TimelineEntry] = []
            merged.extend(gr)
            merged.extend(mr)
            merged.extend(pr)
            # 去重: 相同 timestamp(到分钟) + 相同 title 合并
            dedup: Dict[Tuple[float, str], TimelineEntry] = {}
            for e in merged:
                key_ts = float(e.sort_key) if e.sort_key is not None else -1.0
                # 分钟级对齐(避免同一秒内两条相同记录重复展示)
                key_ts_floor = float(int(key_ts / 60.0)) if key_ts > 0 else key_ts
                key = (key_ts_floor, (e.title or "").strip())
                if key in dedup:
                    # 保留 score 更高的 / fields 更长的
                    exist = dedup[key]
                    if e.score > exist.score or len(e.fields) > len(exist.fields):
                        dedup[key] = e
                else:
                    dedup[key] = e
            sorted_list = sort_entries_by_time_desc(list(dedup.values()))
            hist.recent_events = sorted_list[:12]

            # 最新变化(从 recent_events 取第一条) fallback 填 growth_latest_change/memory_latest_event
            if hist.recent_events:
                first = hist.recent_events[0]
                # 根据 kind 填对应字段(注意:不覆盖已经从别处拿到的有 q.ok 的值)
                text = first.title or first.summary
                if text:
                    if first.kind in ("proposal", "growth") and not hist.growth_latest_change:
                        hist.growth_latest_change = text
                    elif first.kind in ("memory",) and not hist.memory_latest_event:
                        hist.memory_latest_event = text
        except Exception as exc:  # noqa: BLE001
            logger.warning("[LifeSnapshotBuilder] recent_events 聚合异常: %s", exc)

        # 如果 growth_latest_change 仍空,从 growth_recent 第一条退化取
        if not hist.growth_latest_change:
            gr_raw = raw.get("growth_recent")
            if isinstance(gr_raw, list) and gr_raw:
                first = gr_raw[0]
                if isinstance(first, dict):
                    t = str(first.get("title") or first.get("summary") or first.get("reason") or "").strip()
                    if t:
                        hist.growth_latest_change = t
                        if hist.growth_q.is_unreliable:
                            hist.growth_q = DataQuality.degraded(
                                source="growth_recent", error="fallback_from_recent"
                            )

        if not hist.memory_latest_event:
            mr_raw = raw.get("memory_recent")
            if isinstance(mr_raw, list) and mr_raw:
                first = mr_raw[0]
                if isinstance(first, dict):
                    t = str(first.get("content") or first.get("summary") or first.get("title") or "").strip()
                    if len(t) > 64:
                        t = t[:61] + "..."
                    if t:
                        hist.memory_latest_event = t
                        if hist.memory_q.is_unreliable:
                            hist.memory_q = DataQuality.degraded(
                                source="memory_recent", error="fallback_from_recent"
                            )

        # =====================================================================
        # Phase D.6.2: Stable Portrait (稳定特质 + 稳定信念) + ExistenceTimeline(存在记录)
        #
        # 设计原则:
        # - 只有当 raw 中出现过 LifeSnapshotService 放下的 6 个 D.6.2 key 之一,才执行
        #   这一步 → 保持对"D.5.5/D.6.0 不含这些 key 的 raw"的向后兼容
        #   (旧 Builder 调用路径 → existence_* / stable_* 仍然维持默认值)
        # - 懒 import src.contracts.existence_*,避免 yuyi_desktop 核心层硬依赖 contracts 层
        # - 任何异常全部捕获,降级 q=degraded/unknown,绝不 crash
        # - 所有结果 = 纯数据转换,不调用 LLM / 写接口
        # =====================================================================
        d62_keys = (
            "selfmodel_beliefs_items",
            "selfmodel_history_items",
            "selfmodel_reflections_items",
            "selfmodel_stable_traits_items",
            "personality_evolution_v2_items",
            "growth_proposals_v2_items",
        )
        has_any_d62_key = any(k in raw for k in d62_keys)
        if has_any_d62_key:
            try:
                cls._fill_d62_stable_portrait(hist, raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[LifeSnapshotBuilder] D.6.2 stable portrait 异常(降级): %s", exc)
                hist.stable_traits_q = DataQuality.degraded(
                    source="stable_traits", error=f"exc_{type(exc).__name__}"
                )
                hist.stable_beliefs_q = DataQuality.degraded(
                    source="stable_beliefs", error=f"exc_{type(exc).__name__}"
                )
            try:
                cls._fill_d62_existence_timeline(hist, raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[LifeSnapshotBuilder] D.6.2 existence timeline 异常(降级): %s", exc)
                hist.existence_quality = DataQuality.degraded(
                    source="existence_timeline", error=f"exc_{type(exc).__name__}"
                )
                hist.existence_stats_q = hist.existence_quality

    # =====================================================================
    # D.6.2 helpers: stable portrait
    # =====================================================================
    @classmethod
    def _fill_d62_stable_portrait(cls, hist: HistorySnapshot, raw: Dict[str, Any]) -> None:
        """把 selfmodel stable_traits + beliefs items 转换成 LifeSnapshot Contract 对象。"""
        # ---- Stable traits ----
        hist.stable_traits.clear()
        traits_items = raw.get("selfmodel_stable_traits_items") or []
        traits_success = False
        if isinstance(traits_items, list) and traits_items:
            for it in traits_items:
                if not isinstance(it, dict):
                    continue
                tid = str(it.get("trait_id") or it.get("name") or "").strip()
                if not tid:
                    continue
                entry = StableTraitEntry(
                    trait_id=tid,
                    name=str(it.get("name") or tid).strip(),
                    value_pct=cls._safe_int(it.get("value_pct"), 0),
                    stability=cls._safe_float(it.get("stability"), 0.0),
                    evidence_count=cls._safe_int(it.get("evidence_count"), 0),
                    first_observed=str(it.get("first_observed") or "").strip(),
                    last_observed=str(it.get("last_observed") or "").strip(),
                    trend_30d=(
                        float(it.get("trend_30d"))
                        if it.get("trend_30d") is not None
                        else None
                    ),
                    q=DataQuality.ok(source="selfmodel_stable_traits"),
                )
                hist.stable_traits.append(entry)
            traits_success = True
        hist.stable_traits_q = (
            DataQuality.ok(source="selfmodel_stable_traits")
            if traits_success
            else DataQuality.unknown(source="selfmodel_stable_traits", error="no_items")
        )

        # ---- Stable beliefs ----
        hist.stable_beliefs.clear()
        beliefs_items = raw.get("selfmodel_beliefs_items") or []
        beliefs_success = False
        if isinstance(beliefs_items, list) and beliefs_items:
            for it in beliefs_items:
                if not isinstance(it, dict):
                    continue
                bid = str(it.get("belief_id") or "").strip()
                if not bid:
                    continue
                content = str(it.get("content") or "").strip()
                if not content:
                    continue
                entry = StableBeliefEntry(
                    belief_id=bid,
                    domain=str(it.get("domain") or "value").strip(),
                    content=content,
                    confidence=cls._safe_float(it.get("confidence"), 0.0),
                    version=cls._safe_int(it.get("version"), 1),
                    evidence_count=cls._safe_int(it.get("evidence_count"), 0),
                    sources=list(it.get("sources") or [])
                    if isinstance(it.get("sources"), list)
                    else [],
                    active=bool(it.get("active", True)),
                    first_seen=str(it.get("first_seen") or "").strip(),
                    last_confirmed=str(it.get("last_confirmed") or "").strip(),
                    q=DataQuality.ok(source="selfmodel_beliefs"),
                )
                hist.stable_beliefs.append(entry)
            beliefs_success = True
        hist.stable_beliefs_q = (
            DataQuality.ok(source="selfmodel_beliefs")
            if beliefs_success
            else DataQuality.unknown(source="selfmodel_beliefs", error="no_items")
        )

    # =====================================================================
    # D.6.2 helpers: existence timeline
    # =====================================================================
    @classmethod
    def _fill_d62_existence_timeline(cls, hist: HistorySnapshot, raw: Dict[str, Any]) -> None:
        """用 D.6.1 的 3 Adapter + TimelineBuilder 把 6 个 items 列表转换成存在记录。

        懒 import contracts 层,避免桌面端核心层在 contracts 不存在时直接 crash。
        """
        # 懒 load D.6.1 模块 (任意一个 import 失败 → 降级 unknown)
        try:
            from src.contracts.existence_adapters import (
                EvolutionMilestoneAdapter,
                ProposalMilestoneAdapter,
                SelfModelMilestoneAdapter,
            )
            from src.contracts.existence_timeline import (
                DEFAULT_TIMELINE_LIMIT,
                DEFAULT_TIMELINE_MIN_CONFIDENCE,
                DEFAULT_TIMELINE_WINDOW_DAYS,
                ExistenceTimelineBuilder,
                TimelineBuildOptions,
            )
        except Exception as exc:  # noqa: BLE001
            hist.existence_quality = DataQuality.offline(
                source="existence_timeline",
                error=f"contracts_unavailable:{type(exc).__name__}",
            )
            hist.existence_stats_q = hist.existence_quality
            return

        proposals = raw.get("growth_proposals_v2_items") or []
        self_hist = raw.get("selfmodel_history_items") or []
        self_refl = raw.get("selfmodel_reflections_items") or []
        self_beliefs_envelope = {"items": list(raw.get("selfmodel_beliefs_items") or [])}
        self_traits_envelope = {"items": list(raw.get("selfmodel_stable_traits_items") or [])}
        self_history_envelope = {"items": list(self_hist) if isinstance(self_hist, list) else []}
        self_reflections_envelope = {"items": list(self_refl) if isinstance(self_refl, list) else []}
        evolution_items = raw.get("personality_evolution_v2_items") or []

        # 至少有一个数据源是 list → 认为后端可达
        any_list_received = any(
            isinstance(x, list) and len(x) > 0
            for x in (
                proposals,
                self_hist,
                self_refl,
                raw.get("selfmodel_beliefs_items"),
                raw.get("selfmodel_stable_traits_items"),
                evolution_items,
            )
        )

        proposal_adapter = ProposalMilestoneAdapter()
        selfmodel_adapter = SelfModelMilestoneAdapter()
        evolution_adapter = EvolutionMilestoneAdapter()

        # Adapter 结果(各自 try/catch 独立,不互相影响)
        ms_proposal: List[Any] = []
        ms_self: List[Any] = []
        ms_evolution: List[Any] = []
        try:
            if isinstance(proposals, list):
                ms_proposal = list(proposal_adapter.convert_many(proposals))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Builder] proposal adapter failed: %s", exc)
        try:
            ms_self = list(selfmodel_adapter.convert_all(
                beliefs_envelope=self_beliefs_envelope,
                history_envelope=self_history_envelope,
                reflections_envelope=self_reflections_envelope,
                stable_traits_envelope=self_traits_envelope,
            ))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Builder] selfmodel adapter failed: %s", exc)
        try:
            if isinstance(evolution_items, list):
                ms_evolution = list(evolution_adapter.convert_evolution_records(evolution_items))
            # 此外若 evolution 信封中 items 有 通用 timeline 格式 也可以过 convert_evolution_envelope
            # 但现在只取 records list
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Builder] evolution adapter failed: %s", exc)

        total_adapter_out = len(ms_proposal) + len(ms_self) + len(ms_evolution)
        builder = ExistenceTimelineBuilder()
        builder.add(ms_proposal)
        builder.add(ms_self)
        builder.add(ms_evolution)
        options = TimelineBuildOptions(
            min_confidence=DEFAULT_TIMELINE_MIN_CONFIDENCE,
            window_days=DEFAULT_TIMELINE_WINDOW_DAYS,
            order="desc",
            limit=DEFAULT_TIMELINE_LIMIT,
            allowed_types=None,
        )
        timeline, stats = builder.build(options)

        # 填到 snapshot history (clear first to avoid duplicates)
        hist.existence_timeline.clear()
        for m in timeline:
            hist.existence_timeline.append(m)
        hist.existence_stats = stats.to_dict()

        # quality 判定:
        #   有任何一个适配器产出 or 有 items 拉到过 → ok/degraded;
        #   否则 unknown
        if len(hist.existence_timeline) > 0:
            hist.existence_quality = DataQuality.ok(source="existence_timeline")
        elif any_list_received and total_adapter_out > 0:
            # 有原始条目,但经过 L1/L2/L3 过滤后全被拒绝 → degraded
            hist.existence_quality = DataQuality.degraded(
                source="existence_timeline",
                error="all_milestones_filtered",
            )
        elif any_list_received:
            hist.existence_quality = DataQuality.degraded(
                source="existence_timeline",
                error="adapters_no_output",
            )
        else:
            hist.existence_quality = DataQuality.unknown(
                source="existence_timeline",
                error="no_datasource_items",
            )
        # stats 质量 = 同步 existence_quality
        hist.existence_stats_q = hist.existence_quality

    @staticmethod
    def _safe_int(v: Any, default: int = 0) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _heuristic_growth_stage(cls, growth_total: int, memory_total: int) -> str:
        """启发式映射到阶段,绝不返回"探索期"作为默认值。

        只有在两个数字里至少有一个 > 0 时才返回非"未知"的阶段。
        """
        total = int(growth_total or 0) + int(memory_total or 0)
        if total <= 0:
            return ""
        if total < 50:
            return "探索期"
        if total < 500:
            return "成长期"
        return "稳定期"

    # ---- Activity ------------------------------------------------------
    @classmethod
    def _fill_activity(cls, act: ActivitySnapshot, raw: Dict[str, Any]) -> None:
        # Initiative
        ini_overview = raw.get("initiative_overview")
        if isinstance(ini_overview, dict):
            try:
                cnt = int(
                    ini_overview.get("possible_action_count")
                    or ini_overview.get("interest_count")
                    or ini_overview.get("filtered_count")
                    or 0
                )
                act.initiative_count = cnt
            except (TypeError, ValueError):
                act.initiative_count = 0
            act.initiative_q = (
                DataQuality.ok(source="initiative_overview")
                if bool(ini_overview.get("available"))
                else DataQuality.degraded(source="initiative_overview", error="initiative_degraded")
            )
        else:
            act.initiative_q = DataQuality.offline(
                source="initiative_overview", error="initiative_missing"
            )

        # Runtime ticks
        runtime_overview = raw.get("runtime_overview")
        if isinstance(runtime_overview, dict):
            try:
                act.runtime_tick_count = int(runtime_overview.get("recent_tick_count") or 0)
            except (TypeError, ValueError):
                act.runtime_tick_count = 0
            act.runtime_q = (
                DataQuality.ok(source="runtime_overview")
                if bool(runtime_overview.get("available"))
                else DataQuality.degraded(source="runtime_overview", error="runtime_degraded")
            )
        else:
            act.runtime_q = DataQuality.offline(source="runtime_overview", error="runtime_missing")

        # Last action
        last_text = ""
        last_time = ""
        actions = raw.get("initiative_actions")
        if isinstance(actions, list) and actions:
            first = actions[0]
            if isinstance(first, dict):
                text = str(
                    first.get("summary")
                    or first.get("message")
                    or first.get("title")
                    or first.get("content")
                    or ""
                ).strip()
                if len(text) > 80:
                    text = text[:77] + "..."
                ts = first.get("timestamp") or first.get("created_at") or first.get("time") or ""
                if text:
                    last_text = text
                    last_time = format_timestamp(str(ts)) if ts else ""
        if not last_text:
            # 退化 -> recent_events 第一条 activity/signal
            hist_events = raw.get("_recent_events_fallback") or []
            if isinstance(hist_events, list) and hist_events:
                for ev in hist_events:
                    if not isinstance(ev, TimelineEntry):
                        continue
                    if ev.kind in ("initiative", "signal", "action"):
                        if ev.title:
                            last_text = ev.title
                            last_time = format_timestamp(ev.timestamp) if ev.timestamp else ""
                            break
        if last_text:
            act.last_action = last_text
            act.last_action_time = last_time or ""
            act.activity_q = DataQuality.ok(source="initiative_actions")
        else:
            # 完全没有 -> 标记 unknown,绝不写"暂无主动行为记录"(UI 层决定占位文本)
            act.activity_q = DataQuality.unknown(
                source="initiative_actions", error="no_recent_action"
            )


__all__ = [
    # Constants
    "DQUALITY_OK",
    "DQUALITY_DEGRADED",
    "DQUALITY_UNKNOWN",
    "DQUALITY_OFFLINE",
    "VIZ_ROUND_RADIUS",
    "VIZ_SPACING_PX",
    "VIZ_ACCENT_COLOR",
    "VIZ_ACCENT_SOFT",
    "DEFAULT_IDENTITY_NAME",
    "DEFAULT_MOOD_UNKNOWN",
    # Data classes
    "DataQuality",
    "TraitValue",
    "PersonalityFragment",
    "LifeMoodFragment",
    # Phase D.6.0 新契约
    "StableTraitEntry",
    "StableBeliefEntry",
    "ExistenceMilestone",
    "StablePortraitFragment",
    # 三段式
    "CoreSnapshot",
    "HistorySnapshot",
    "ActivitySnapshot",
    "LifeSnapshot",
    # Builder
    "LifeSnapshotBuilder",
]
