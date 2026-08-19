# src/contracts/identity.py
"""Phase D.7.0: Identity Contract (身份合同层)。

定义
====
1. IdentitySnapshot  — 顶层身份快照(未来 LifeSnapshot 接入 D.7.2 时使用)
2. IdentityProfile   — 聚合后的身份画像(由 D.7.1 IdentityAggregator 产出)
3. IdentityEvidence  — 单条身份证据(指向稳定特质/信念/存在记录)
4. FormationSource   — 单个存在记录 Milestone 对某条身份组件的贡献

这四个对象 = 「事实层」。
   ❌ 不调用 LLM
   ❌ 不生成人生故事 / 人生章节 / "温柔少女"这种叙事
   ✅ 只描述: 有哪些证据 → 形成了哪些身份组件

冻结原则 (D.6.1 / D.5.5 同):
- 字段语义锁定;新增字段走 schema_version
- 不兼容变更通过新 v2 dataclass 完成,绝不删除字段
- 默认值全为空 / 0 / 0.0 / False / UNKNOWN — 不伪造默认身份
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


# ============================================================
# 常量集合 (冻结;新增需走契约升级流程)
# ============================================================

# IdentityQuality.status 合法取值
IDENTITY_STATUS_UNKNOWN = "unknown"
IDENTITY_STATUS_AGGREGATING = "aggregating"
IDENTITY_STATUS_OK = "ok"
IDENTITY_STATUS_DEGRADED = "degraded"
IDENTITY_STATUS_OFFLINE = "offline"
IDENTITY_STATUS_LOCKED = "locked"          # 手动确认锁定,不可再被聚合器自动改写

VALID_IDENTITY_STATUSES = (
    IDENTITY_STATUS_UNKNOWN,
    IDENTITY_STATUS_AGGREGATING,
    IDENTITY_STATUS_OK,
    IDENTITY_STATUS_DEGRADED,
    IDENTITY_STATUS_OFFLINE,
    IDENTITY_STATUS_LOCKED,
)

# IdentityEvidence.source_type 合法取值
ID_SOURCE_STABLE_TRAIT = "stable_trait"
ID_SOURCE_STABLE_BELIEF = "stable_belief"
ID_SOURCE_EXISTENCE_MILESTONE = "existence_milestone"
ID_SOURCE_SELF_MODEL_HISTORY = "selfmodel_history"
ID_SOURCE_RELATIONSHIP_SNAPSHOT = "relationship_snapshot"

VALID_IDENTITY_SOURCE_TYPES = (
    ID_SOURCE_STABLE_TRAIT,
    ID_SOURCE_STABLE_BELIEF,
    ID_SOURCE_EXISTENCE_MILESTONE,
    ID_SOURCE_SELF_MODEL_HISTORY,
    ID_SOURCE_RELATIONSHIP_SNAPSHOT,
)

# CoreValueEntry.origin_type 合法取值
ORIGIN_STABLE_TRAIT = "stable_trait"
ORIGIN_STABLE_BELIEF = "stable_belief"
ORIGIN_REFLECTION = "reflection"
ORIGIN_EXISTENCE_MILESTONE = "existence_milestone"
ORIGIN_MANUAL = "manual"              # 手工锚定,不来自自动聚合

VALID_ORIGIN_TYPES = (
    ORIGIN_STABLE_TRAIT,
    ORIGIN_STABLE_BELIEF,
    ORIGIN_REFLECTION,
    ORIGIN_EXISTENCE_MILESTONE,
    ORIGIN_MANUAL,
)

# 当前 schema 版本
IDENTITY_SCHEMA_VERSION = "1.0"


def _utcnow_iso() -> str:
    """UTC ISO 8601 with Z suffix。默认值使用空字符串 "" 而不是这个函数!
    仅当 Aggregator 真正完成构建、写入真实时间时才调用。
    """
    return datetime.utcnow().isoformat() + "Z"


def _clamp_unit(value: float) -> float:
    """把 float 夹到 [0.0, 1.0] 闭区间;None / NaN → 0.0。"""
    try:
        if value is None:
            return 0.0
        v = float(value)
        if v != v:  # NaN
            return 0.0
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return v
    except (TypeError, ValueError):
        return 0.0


# ============================================================
# 0. IdentityQuality — Identity 专用数据质量
# ============================================================

@dataclass
class IdentityQuality:
    """身份组件 / 画像 / 快照 的质量状态。

    相比 DataQuality,增加了 证据覆盖度 / 稳定度指数 / 手动锁定 审计字段。

    默认 status = unknown, 绝不自动推断 ok。
    """

    status: str = IDENTITY_STATUS_UNKNOWN
    source: str = ""
    error: str = ""
    # 身份组件由多少可用证据覆盖(0..1);0.0 未知,>=0.30 算"开始有基础"
    evidence_coverage_pct: float = 0.0
    # 身份稳定度指数 (0..1);0.0 未知或完全无稳定性
    stability_index: float = 0.0
    # 上次人工审计时间 (ISO);""=从未审计
    last_audited_at: str = ""
    # 自由字段: 质量相关的调试元信息
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in VALID_IDENTITY_STATUSES:
            self.status = IDENTITY_STATUS_UNKNOWN
        self.evidence_coverage_pct = _clamp_unit(self.evidence_coverage_pct)
        self.stability_index = _clamp_unit(self.stability_index)
        if not isinstance(self.meta, dict):
            self.meta = {}

    @property
    def is_reliable(self) -> bool:
        """是否可以被 Identity UI 作为"可信身份"展示。

        条件: status 为 ok 或 locked,且 evidence_coverage_pct >= 0.30
        — 也就是至少 30% 的身份组件有证据支撑,否则算空壳。
        """
        if self.status not in (IDENTITY_STATUS_OK, IDENTITY_STATUS_LOCKED):
            return False
        return self.evidence_coverage_pct >= 0.30

    @property
    def is_editable(self) -> bool:
        """locked 状态下不允许聚合器自动修改。"""
        return self.status != IDENTITY_STATUS_LOCKED

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_reliable"] = self.is_reliable
        d["is_editable"] = self.is_editable
        return d

    @classmethod
    def from_dict(cls, data: Any) -> "IdentityQuality":
        if not isinstance(data, dict):
            return cls()
        return cls(
            status=str(data.get("status", IDENTITY_STATUS_UNKNOWN)) or IDENTITY_STATUS_UNKNOWN,
            source=str(data.get("source", "") or ""),
            error=str(data.get("error", "") or ""),
            evidence_coverage_pct=float(data.get("evidence_coverage_pct", 0.0) or 0.0),
            stability_index=float(data.get("stability_index", 0.0) or 0.0),
            last_audited_at=str(data.get("last_audited_at", "") or ""),
            meta=dict(data.get("meta") or {}),
        )


# ============================================================
# 1. FormationSource — 单个 Milestone 对身份组件的贡献
# ============================================================

@dataclass
class FormationSource:
    """一个存在记录 (ExistenceMilestone) 如何形成某条身份组件。

    示例:
        milestone_id = em_xxxxxxxxxx
        formation_reason = "trait_change milestone: warmth 0.60 → 0.72,跨越稳定阈值 0.70"
        contribution_weight = 0.62
    """

    # 必须指向存在记录层的 ExistenceMilestone.milestone_id
    milestone_id: str = ""

    # 纯事实性的理由 (不超过 160 字;禁止 LLM 叙事,只描述规则或数据变化)
    formation_reason: str = ""

    # 该 Milestone 对该身份组件的贡献度 [0,1];默认 0.0 = 未知
    contribution_weight: float = 0.0

    # 可选: Milestone.timestamp 的镜像拷贝 (用于排序/调试,默认空不填充)
    milestone_timestamp_iso: str = ""

    def __post_init__(self) -> None:
        self.contribution_weight = _clamp_unit(self.contribution_weight)
        self.formation_reason = (self.formation_reason or "").strip()[:160]
        self.milestone_id = (self.milestone_id or "").strip()[:80]

    @property
    def well_formed(self) -> bool:
        """来源是否可用。至少要有 milestone_id AND formation_reason 非空。"""
        return bool(self.milestone_id) and bool(self.formation_reason)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "milestone_id": self.milestone_id,
            "formation_reason": self.formation_reason,
            "contribution_weight": self.contribution_weight,
            "milestone_timestamp_iso": self.milestone_timestamp_iso,
            "well_formed": self.well_formed,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "FormationSource":
        if not isinstance(data, dict):
            return cls()
        return cls(
            milestone_id=str(data.get("milestone_id", "") or ""),
            formation_reason=str(data.get("formation_reason", "") or ""),
            contribution_weight=float(data.get("contribution_weight", 0.0) or 0.0),
            milestone_timestamp_iso=str(data.get("milestone_timestamp_iso", "") or ""),
        )


# ============================================================
# 2. IdentityEvidence — 单条身份证据 (稳定特质/信念/存在记录)
# ============================================================

@dataclass
class IdentityEvidence:
    """身份组件的一条底层证据。

    证据 ID 前缀 ide_,避免和 existence.er_ / memory.m_ / growth.p_ 等冲突。
    """

    evidence_id: str = field(
        default_factory=lambda: f"ide_{uuid.uuid4().hex[:10]}"
    )

    # 来源类型 ∈ VALID_IDENTITY_SOURCE_TYPES
    source_type: str = ""

    # 来源对象主键: stable_trait 的 trait_id / stable_belief 的 belief_id /
    #               existence_milestone 的 milestone_id / ...
    source_id: str = ""

    # 可选:具体到来源对象的哪个字段。例:"warmth.stability" / "content"
    source_field: str = ""

    # 该证据对于对应身份组件的贡献权重 [0,1];默认 0.0 = 未知(不自动推断 1.0)
    weight: float = 0.0

    # 纯事实性备注,≤ 80 字;不做叙事
    note: str = ""

    # 证据本身的置信度 [0,1];默认 0.0
    confidence: float = 0.0

    # 证据发生 / 创建 时间 (ISO);默认空(不自动填 now)
    timestamp_iso: str = ""

    def __post_init__(self) -> None:
        self.weight = _clamp_unit(self.weight)
        self.confidence = _clamp_unit(self.confidence)
        self.source_type = (self.source_type or "").strip()
        self.source_id = (self.source_id or "").strip()[:80]
        self.source_field = (self.source_field or "").strip()[:80]
        self.note = (self.note or "").strip()[:80]
        self.timestamp_iso = (self.timestamp_iso or "").strip()[:40]

    @property
    def valid_source(self) -> bool:
        return self.source_type in VALID_IDENTITY_SOURCE_TYPES and bool(self.source_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_field": self.source_field,
            "weight": self.weight,
            "note": self.note,
            "confidence": self.confidence,
            "timestamp_iso": self.timestamp_iso,
            "valid_source": self.valid_source,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "IdentityEvidence":
        if not isinstance(data, dict):
            return cls()
        return cls(
            evidence_id=str(data.get("evidence_id") or f"ide_{uuid.uuid4().hex[:10]}"),
            source_type=str(data.get("source_type", "") or ""),
            source_id=str(data.get("source_id", "") or ""),
            source_field=str(data.get("source_field", "") or ""),
            weight=float(data.get("weight", 0.0) or 0.0),
            note=str(data.get("note", "") or ""),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            timestamp_iso=str(data.get("timestamp_iso", "") or ""),
        )


# ============================================================
# 3. 身份组件条目 (CoreValue / PersonalityAnchor / BeliefAnchor)
# ============================================================

@dataclass
class CoreValueEntry:
    """单个「核心价值」条目。

    注意:
    - content = 纯事实的 核心价值描述,例:"优先理解他人再给出回答"。
      ❌ 不允许: "我是一个温柔的人" — 这是叙事,留给 D.7.2 UI。
      ✅ 允许: "稳定信念:理解比快速回答重要,证据 7 条"
    - 没有 formation_sources AND evidence_count == 0 → q.status 必须 unknown
    """

    # 稳定短 id;推荐前缀 "v_" 例:v_understand_first
    value_id: str = ""

    # 核心价值内容(纯事实描述,≤ 120 字)
    content: str = ""

    # 身份组件的来源类型 ∈ VALID_ORIGIN_TYPES
    origin_type: str = ORIGIN_STABLE_BELIEF

    # 对应来源对象的主键: belief_id / trait_id / reflection_id / milestone_id
    origin_id: str = ""

    # 核心价值成立置信度 [0,1];默认 0.0
    confidence: float = 0.0

    # 支撑该核心价值的独立证据(IdentityEvidence)条数(聚合器算完填入)
    evidence_count: int = 0

    # 该核心价值第一次形成时间 (ISO);默认空(不自动 now)
    first_formed_at: str = ""

    # 该核心价值最近一次被新证据确认 / 修订 (ISO);默认空
    last_confirmed_at: str = ""

    # 形成过程: 哪些 Milestone 形成了这条价值
    formation_sources: List[FormationSource] = field(default_factory=list)

    # 底层 IdentityEvidence 列表 (D.7.1 Aggregator 填)
    evidence: List[IdentityEvidence] = field(default_factory=list)

    # 该组件自身的质量
    q: IdentityQuality = field(default_factory=IdentityQuality)

    def __post_init__(self) -> None:
        self.value_id = (self.value_id or "").strip()[:64]
        self.content = (self.content or "").strip()[:120]
        self.origin_id = (self.origin_id or "").strip()[:80]
        self.confidence = _clamp_unit(self.confidence)
        try:
            self.evidence_count = max(0, int(self.evidence_count or 0))
        except (TypeError, ValueError):
            self.evidence_count = 0
        if self.origin_type not in VALID_ORIGIN_TYPES:
            self.origin_type = ORIGIN_STABLE_BELIEF
        # 合规性: 没有任何来源支撑 → q 降级 unknown
        if (
            not self.formation_sources
            and not self.evidence
            and self.evidence_count == 0
        ):
            if self.q.status != IDENTITY_STATUS_LOCKED:
                self.q.status = IDENTITY_STATUS_UNKNOWN
                self.q.error = "no_evidence_or_formation_sources"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value_id": self.value_id,
            "content": self.content,
            "origin_type": self.origin_type,
            "origin_id": self.origin_id,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "first_formed_at": self.first_formed_at,
            "last_confirmed_at": self.last_confirmed_at,
            "formation_sources": [s.to_dict() for s in self.formation_sources],
            "evidence": [e.to_dict() for e in self.evidence],
            "q": self.q.to_dict() if is_dataclass(self.q) else IdentityQuality.from_dict(self.q).to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "CoreValueEntry":
        if not isinstance(data, dict):
            return cls()
        return cls(
            value_id=str(data.get("value_id", "") or ""),
            content=str(data.get("content", "") or ""),
            origin_type=str(data.get("origin_type", ORIGIN_STABLE_BELIEF) or ORIGIN_STABLE_BELIEF),
            origin_id=str(data.get("origin_id", "") or ""),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            evidence_count=int(data.get("evidence_count", 0) or 0),
            first_formed_at=str(data.get("first_formed_at", "") or ""),
            last_confirmed_at=str(data.get("last_confirmed_at", "") or ""),
            formation_sources=[
                FormationSource.from_dict(x) for x in (data.get("formation_sources") or [])
            ],
            evidence=[
                IdentityEvidence.from_dict(x) for x in (data.get("evidence") or [])
            ],
            q=IdentityQuality.from_dict(data.get("q")),
        )


@dataclass
class PersonalityAnchorEntry:
    """单个「稳定人格锚点」条目 (来自 StableTrait)。

    这是 Identity 和 D.6.2 StableTraits 的桥接层。
    区别于 Resolver.current(即时值),这里只描述「长期稳定」的部分。
    """

    trait_id: str = ""
    name: str = ""

    # 稳定度 [0,1];>=0.70 算"形成中锚点";>=0.85 算"稳固锚点"
    stability: float = 0.0

    # 当前特质强度 0..100 (镜像 stable_traits.value_pct)
    current_value_pct: int = 0

    # 独立观察证据次数 (镜像 stable_traits.evidence_count)
    evidence_count: int = 0

    first_observed_at: str = ""
    last_observed_at: str = ""

    # 近 30 天趋势; None = 未知 / 0.0 = 持平
    trend_30d: Optional[float] = None

    # 形成来源 Milestones
    formation_sources: List[FormationSource] = field(default_factory=list)

    # 底层证据 (IdentityEvidence 级)
    evidence: List[IdentityEvidence] = field(default_factory=list)

    q: IdentityQuality = field(default_factory=IdentityQuality)

    def __post_init__(self) -> None:
        self.trait_id = (self.trait_id or "").strip()[:64]
        self.name = (self.name or "").strip()[:64]
        self.stability = _clamp_unit(self.stability)
        try:
            self.current_value_pct = max(0, min(100, int(self.current_value_pct or 0)))
        except (TypeError, ValueError):
            self.current_value_pct = 0
        try:
            self.evidence_count = max(0, int(self.evidence_count or 0))
        except (TypeError, ValueError):
            self.evidence_count = 0
        if self.trend_30d is not None:
            try:
                self.trend_30d = float(self.trend_30d)
            except (TypeError, ValueError):
                self.trend_30d = None
        # 合规性:没有任何支撑 → unknown
        if (
            not self.formation_sources
            and not self.evidence
            and self.evidence_count == 0
            and self.stability == 0.0
        ):
            if self.q.status != IDENTITY_STATUS_LOCKED:
                self.q.status = IDENTITY_STATUS_UNKNOWN
                self.q.error = "no_stability_or_evidence"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trait_id": self.trait_id,
            "name": self.name,
            "stability": self.stability,
            "current_value_pct": self.current_value_pct,
            "evidence_count": self.evidence_count,
            "first_observed_at": self.first_observed_at,
            "last_observed_at": self.last_observed_at,
            "trend_30d": self.trend_30d,
            "formation_sources": [s.to_dict() for s in self.formation_sources],
            "evidence": [e.to_dict() for e in self.evidence],
            "q": self.q.to_dict() if is_dataclass(self.q) else IdentityQuality.from_dict(self.q).to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "PersonalityAnchorEntry":
        if not isinstance(data, dict):
            return cls()
        trend = data.get("trend_30d")
        trend_val: Optional[float] = None
        if trend is not None:
            try:
                trend_val = float(trend)
            except (TypeError, ValueError):
                trend_val = None
        return cls(
            trait_id=str(data.get("trait_id", "") or ""),
            name=str(data.get("name", "") or ""),
            stability=float(data.get("stability", 0.0) or 0.0),
            current_value_pct=int(data.get("current_value_pct", 0) or 0),
            evidence_count=int(data.get("evidence_count", 0) or 0),
            first_observed_at=str(data.get("first_observed_at", "") or ""),
            last_observed_at=str(data.get("last_observed_at", "") or ""),
            trend_30d=trend_val,
            formation_sources=[
                FormationSource.from_dict(x) for x in (data.get("formation_sources") or [])
            ],
            evidence=[
                IdentityEvidence.from_dict(x) for x in (data.get("evidence") or [])
            ],
            q=IdentityQuality.from_dict(data.get("q")),
        )


@dataclass
class BeliefAnchorEntry:
    """单个「稳定信念锚点」条目 (来自 StableBelief)。"""

    belief_id: str = ""

    # 领域 ∈ value / interaction / self / world / relationship / work / other
    domain: str = "value"

    # 信念原文(纯事实镜像 stable_beliefs.content)
    content: str = ""

    confidence: float = 0.0
    version: int = 1
    evidence_count: int = 0

    # 是否仍然 active (信念被撤回时 = False)
    active: bool = True

    first_seen_at: str = ""
    last_confirmed_at: str = ""

    # 底层 IdentityEvidence (支撑这条信念的 stable_trait / memory_refs / reflections)
    evidence: List[IdentityEvidence] = field(default_factory=list)

    # 形成过程 Milestones
    formation_sources: List[FormationSource] = field(default_factory=list)

    q: IdentityQuality = field(default_factory=IdentityQuality)

    def __post_init__(self) -> None:
        self.belief_id = (self.belief_id or "").strip()[:64]
        self.domain = (self.domain or "value").strip()[:32]
        self.content = (self.content or "").strip()[:500]
        self.confidence = _clamp_unit(self.confidence)
        try:
            self.version = max(1, int(self.version or 1))
        except (TypeError, ValueError):
            self.version = 1
        try:
            self.evidence_count = max(0, int(self.evidence_count or 0))
        except (TypeError, ValueError):
            self.evidence_count = 0
        # 合规性:没有支撑 → unknown
        if (
            not self.formation_sources
            and not self.evidence
            and self.evidence_count == 0
            and self.confidence == 0.0
        ):
            if self.q.status != IDENTITY_STATUS_LOCKED:
                self.q.status = IDENTITY_STATUS_UNKNOWN
                self.q.error = "no_confidence_or_evidence"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "belief_id": self.belief_id,
            "domain": self.domain,
            "content": self.content,
            "confidence": self.confidence,
            "version": self.version,
            "evidence_count": self.evidence_count,
            "active": self.active,
            "first_seen_at": self.first_seen_at,
            "last_confirmed_at": self.last_confirmed_at,
            "evidence": [e.to_dict() for e in self.evidence],
            "formation_sources": [s.to_dict() for s in self.formation_sources],
            "q": self.q.to_dict() if is_dataclass(self.q) else IdentityQuality.from_dict(self.q).to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "BeliefAnchorEntry":
        if not isinstance(data, dict):
            return cls()
        return cls(
            belief_id=str(data.get("belief_id", "") or ""),
            domain=str(data.get("domain", "value") or "value"),
            content=str(data.get("content", "") or ""),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            version=int(data.get("version", 1) or 1),
            evidence_count=int(data.get("evidence_count", 0) or 0),
            active=bool(data.get("active", True)),
            first_seen_at=str(data.get("first_seen_at", "") or ""),
            last_confirmed_at=str(data.get("last_confirmed_at", "") or ""),
            evidence=[
                IdentityEvidence.from_dict(x) for x in (data.get("evidence") or [])
            ],
            formation_sources=[
                FormationSource.from_dict(x) for x in (data.get("formation_sources") or [])
            ],
            q=IdentityQuality.from_dict(data.get("q")),
        )


# ============================================================
# 4. IdentityProfile — 聚合后的身份画像 (D.7.1 Aggregator 输出)
# ============================================================

@dataclass
class IdentityProfile:
    """Identity Aggregator 的输出:从 StableTraits / Beliefs / ExistenceTimeline
    聚合出来的、结构化的「羽依是谁(事实版)」。

    约束:
    - identity_name 默认 "" (不自动填"羽依" / "浅雾羽依"等名字)
    - identity_canonical_version 只允许由 Aggregator 基于规则递增,绝不自增
    - 每一条 core_values / personality_anchors / belief_anchors 都必须有
      formation_sources 或 evidence (否则 q 直接 unknown)
    """

    # 画像构建 id;前缀 "ip_"
    profile_id: str = field(
        default_factory=lambda: f"ip_{uuid.uuid4().hex[:12]}"
    )

    # Aggregator 完成构建时写入的时间;默认空 = 未构建
    built_at_iso: str = ""

    # 身份名 (非自动生成)
    identity_name: str = ""

    # 身份版本号: 1 = 初版,仅在明确的身份组件发生版本化变更时 +1
    identity_canonical_version: int = 1

    # 三大组件
    core_values: List[CoreValueEntry] = field(default_factory=list)
    personality_anchors: List[PersonalityAnchorEntry] = field(default_factory=list)
    belief_anchors: List[BeliefAnchorEntry] = field(default_factory=list)

    # 该画像总共引用了多少个 FormationSource (存在记录层 Milestones)
    formation_sources_total: int = 0

    # 该画像总共引用了多少条 IdentityEvidence
    evidence_total: int = 0

    # 画像层面的 schema 版本
    schema_version: str = IDENTITY_SCHEMA_VERSION

    # 画像整体质量
    q: IdentityQuality = field(default_factory=IdentityQuality)

    # 可选: 画像唯一签名 (稳定 hash);默认空 ""。
    # D.7.1 聚合完成后由 Aggregator 生成,UI/Debug 可用来比较两版画像是否一致。
    identity_signature: str = ""

    def __post_init__(self) -> None:
        self.identity_name = (self.identity_name or "").strip()[:64]
        self.identity_signature = (self.identity_signature or "").strip()[:128]
        self.built_at_iso = (self.built_at_iso or "").strip()[:40]
        try:
            self.identity_canonical_version = max(
                1, int(self.identity_canonical_version or 1)
            )
        except (TypeError, ValueError):
            self.identity_canonical_version = 1

    # ------------------------------------------------------------------
    # 工具:快速元信息 (D.7.2 UI 可直接用,不用自己算)
    # ------------------------------------------------------------------
    @property
    def component_counts(self) -> Dict[str, int]:
        return {
            "core_values": len(self.core_values),
            "personality_anchors": len(self.personality_anchors),
            "belief_anchors": len(self.belief_anchors),
        }

    @property
    def reliable_component_ratio(self) -> float:
        """三个组件列表中,有多少比例的条目是 q.is_reliable。

        用于 q.evidence_coverage_pct 的参考计算源。
        """
        totals = 0
        reliable = 0
        for group in (self.core_values, self.personality_anchors, self.belief_anchors):
            for entry in group:
                totals += 1
                if entry.q.is_reliable:
                    reliable += 1
        if totals == 0:
            return 0.0
        return reliable / totals

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "built_at_iso": self.built_at_iso,
            "identity_name": self.identity_name,
            "identity_canonical_version": self.identity_canonical_version,
            "core_values": [c.to_dict() for c in self.core_values],
            "personality_anchors": [a.to_dict() for a in self.personality_anchors],
            "belief_anchors": [b.to_dict() for b in self.belief_anchors],
            "formation_sources_total": self.formation_sources_total,
            "evidence_total": self.evidence_total,
            "schema_version": self.schema_version,
            "q": self.q.to_dict() if is_dataclass(self.q) else IdentityQuality.from_dict(self.q).to_dict(),
            "identity_signature": self.identity_signature,
            "component_counts": self.component_counts,
            "reliable_component_ratio": self.reliable_component_ratio,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "IdentityProfile":
        if not isinstance(data, dict):
            return cls()
        return cls(
            profile_id=str(data.get("profile_id") or f"ip_{uuid.uuid4().hex[:12]}"),
            built_at_iso=str(data.get("built_at_iso", "") or ""),
            identity_name=str(data.get("identity_name", "") or ""),
            identity_canonical_version=int(data.get("identity_canonical_version", 1) or 1),
            core_values=[
                CoreValueEntry.from_dict(x) for x in (data.get("core_values") or [])
            ],
            personality_anchors=[
                PersonalityAnchorEntry.from_dict(x) for x in (data.get("personality_anchors") or [])
            ],
            belief_anchors=[
                BeliefAnchorEntry.from_dict(x) for x in (data.get("belief_anchors") or [])
            ],
            formation_sources_total=int(data.get("formation_sources_total", 0) or 0),
            evidence_total=int(data.get("evidence_total", 0) or 0),
            schema_version=str(data.get("schema_version", IDENTITY_SCHEMA_VERSION) or IDENTITY_SCHEMA_VERSION),
            q=IdentityQuality.from_dict(data.get("q")),
            identity_signature=str(data.get("identity_signature", "") or ""),
        )


# ============================================================
# 5. IdentitySnapshot — 顶层快照 (D.7.2 接入 LifeSnapshot)
# ============================================================

@dataclass
class IdentitySnapshot:
    """Identity 顶层快照(未来直接放到 LifeSnapshot.identity_snapshot 里)。

    为了 D.7.2 的无痛接入,我们现在就把它定义好:
    - 结构镜像 HistorySnapshot
    - 所有字段都有对应的 q.status,方便 UI 统一降级。
    """

    snapshot_id: str = field(
        default_factory=lambda: f"ids_{uuid.uuid4().hex[:12]}"
    )

    # 构建时间;默认空 "" (绝不自动 now)
    created_at_iso: str = ""

    # 画像主体(未构建 = None → 默认 None,不是空 Profile,避免 UI 误判有数据)
    profile: Optional[IdentityProfile] = None

    # Aggregator 构建期间产生的 warnings (去重后的人类可读警告字符串列表)
    warnings: List[str] = field(default_factory=list)

    # D.7.2 聚合时使用的 build 上下文 (纯调试信息,默认空 dict)
    generation_context: Dict[str, Any] = field(default_factory=dict)

    # 顶层质量
    q: IdentityQuality = field(default_factory=IdentityQuality)

    schema_version: str = IDENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.created_at_iso = (self.created_at_iso or "").strip()[:40]
        if not isinstance(self.warnings, list):
            self.warnings = []
        if not isinstance(self.generation_context, dict):
            self.generation_context = {}

    @property
    def is_built(self) -> bool:
        """Profile 是否已经真正构建完成 (不是默认空壳)。"""
        return self.profile is not None and bool(self.profile.built_at_iso)

    @property
    def component_counts(self) -> Dict[str, int]:
        if self.profile is None:
            return {"core_values": 0, "personality_anchors": 0, "belief_anchors": 0}
        return self.profile.component_counts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at_iso": self.created_at_iso,
            "profile": self.profile.to_dict() if self.profile is not None else None,
            "warnings": list(self.warnings or []),
            "generation_context": dict(self.generation_context or {}),
            "q": self.q.to_dict() if is_dataclass(self.q) else IdentityQuality.from_dict(self.q).to_dict(),
            "schema_version": self.schema_version,
            "is_built": self.is_built,
            "component_counts": self.component_counts,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "IdentitySnapshot":
        if not isinstance(data, dict):
            return cls()
        return cls(
            snapshot_id=str(data.get("snapshot_id") or f"ids_{uuid.uuid4().hex[:12]}"),
            created_at_iso=str(data.get("created_at_iso", "") or ""),
            profile=(
                IdentityProfile.from_dict(data.get("profile"))
                if data.get("profile") is not None
                else None
            ),
            warnings=[str(x) for x in (data.get("warnings") or [])],
            generation_context=dict(data.get("generation_context") or {}),
            q=IdentityQuality.from_dict(data.get("q")),
            schema_version=str(data.get("schema_version", IDENTITY_SCHEMA_VERSION) or IDENTITY_SCHEMA_VERSION),
        )


# ============================================================
# __all__ (外部导出门面)
# ============================================================

__all__ = [
    # Status / source constants
    "IDENTITY_STATUS_UNKNOWN",
    "IDENTITY_STATUS_AGGREGATING",
    "IDENTITY_STATUS_OK",
    "IDENTITY_STATUS_DEGRADED",
    "IDENTITY_STATUS_OFFLINE",
    "IDENTITY_STATUS_LOCKED",
    "VALID_IDENTITY_STATUSES",
    "ID_SOURCE_STABLE_TRAIT",
    "ID_SOURCE_STABLE_BELIEF",
    "ID_SOURCE_EXISTENCE_MILESTONE",
    "ID_SOURCE_SELF_MODEL_HISTORY",
    "ID_SOURCE_RELATIONSHIP_SNAPSHOT",
    "VALID_IDENTITY_SOURCE_TYPES",
    "ORIGIN_STABLE_TRAIT",
    "ORIGIN_STABLE_BELIEF",
    "ORIGIN_REFLECTION",
    "ORIGIN_EXISTENCE_MILESTONE",
    "ORIGIN_MANUAL",
    "VALID_ORIGIN_TYPES",
    "IDENTITY_SCHEMA_VERSION",
    # 核心 dataclasses
    "IdentityQuality",
    "FormationSource",
    "IdentityEvidence",
    "CoreValueEntry",
    "PersonalityAnchorEntry",
    "BeliefAnchorEntry",
    "IdentityProfile",
    "IdentitySnapshot",
]
