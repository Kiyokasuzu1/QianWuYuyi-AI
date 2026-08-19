# src/contracts/existence.py
"""Contracts: ExistenceMilestone (羽依存在记录)

Phase D.6.1: 羽依人生事件统一契约。

核心设计原则 (与 D.5.5 / D.6.0 保持一致):
- 不伪造数据:默认值全为空 / 0.0 / False / UNKNOWN
- 证据优先:每个 Milestone 必须有 List[EvidenceReference],
  不允许 LLM "创造人生事件"。
- 三源合并(不做跨系统调用):
    ProposalStore / SelfModelProvider / EvolutionEngine
    各自输出统一格式 ExistenceMilestone,
    由 TimelineBuilder 负责排序 / 过滤 / 去重。
- 为 D.8 Growth解释系统 预留可解释性字段 (explanation.*)。

Milestone Type 定义(禁止随意追加,新类型必须走契约升级流程):
    growth        - Growth Proposal 被应用
    belief        - 核心信念 建立/改变
    reflection    - 反思记录(深刻洞察)
    trait_change  - 稳定特质出现显著变化
    relationship  - 与用户关系的重大变化(非即时情绪)
    identity      - 身份锚点 / 自我认知 重大更新

冻结策略 (与 D.5.5 Snapshot 一致):
- 字段语义锁定;追加字段走 schema_version 升级。
- 绝不删除字段,不兼容变更通过新 v2 dataclass 完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


# ============================================================
# 常量:Milestone Type / Source Type (冻结)
# ============================================================

# ExistenceMilestone.milestone_type 合法取值
MILESTONE_GROWTH = "growth"
MILESTONE_BELIEF = "belief"
MILESTONE_REFLECTION = "reflection"
MILESTONE_TRAIT_CHANGE = "trait_change"
MILESTONE_RELATIONSHIP = "relationship"
MILESTONE_IDENTITY = "identity"

VALID_MILESTONE_TYPES = (
    MILESTONE_GROWTH,
    MILESTONE_BELIEF,
    MILESTONE_REFLECTION,
    MILESTONE_TRAIT_CHANGE,
    MILESTONE_RELATIONSHIP,
    MILESTONE_IDENTITY,
)

# EvidenceReference.source_type 合法取值 (三源 + 未来扩展占位)
SOURCE_GROWTH_PROPOSAL = "growth_proposal"
SOURCE_SELF_MODEL_BELIEF = "selfmodel_belief"
SOURCE_SELF_MODEL_TRAIT = "selfmodel_trait"
SOURCE_SELF_MODEL_HISTORY = "selfmodel_history"
SOURCE_PERSONALITY_EVOLUTION = "personality_evolution"
SOURCE_REFLECTION_INSIGHT = "reflection_insight"
SOURCE_RELATIONSHIP_SNAPSHOT = "relationship_snapshot"
SOURCE_IDENTITY_ANCHOR = "identity_anchor"

VALID_SOURCE_TYPES = (
    SOURCE_GROWTH_PROPOSAL,
    SOURCE_SELF_MODEL_BELIEF,
    SOURCE_SELF_MODEL_TRAIT,
    SOURCE_SELF_MODEL_HISTORY,
    SOURCE_PERSONALITY_EVOLUTION,
    SOURCE_REFLECTION_INSIGHT,
    SOURCE_RELATIONSHIP_SNAPSHOT,
    SOURCE_IDENTITY_ANCHOR,
)

# schema 版本 (D.6.1 初版)
CANONICAL_SCHEMA_VERSION = "1.0"


def now_iso() -> str:
    """UTC ISO 8601 with Z suffix (与 src/contracts/*_schema.py 一致)。"""
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 1. EvidenceReference: 证据引用
# ============================================================

@dataclass
class EvidenceReference:
    """
    单条存在记录的证据来源。

    设计约束 (禁止违反):
    - source_id 必须指向可审计的真实对象(例如 proposal_id / belief_id),
      不允许使用 "llm_inferred" 或空字符串伪造。
    - confidence ∈ [0, 1]: 0.0 = 未知(默认),不自动填充 0.72。
    - 如果 source_type 不在 VALID_SOURCE_TYPES 内,视为未识别来源,
      TimelineBuilder 会在过滤阶段丢弃(不 crash)。
    """

    # 证据 ID (前缀 er_,避免与其他域 ID 冲突)
    evidence_id: str = field(
        default_factory=lambda: f"er_{uuid.uuid4().hex[:10]}"
    )

    # 来源类型 (必须 ∈ VALID_SOURCE_TYPES)
    source_type: str = ""

    # 来源对象的真实主键(proposal_id / belief_id / reflection_id ...)
    source_id: str = ""

    # 证据置信度 [0.0, 1.0];默认 0.0 = UNKNOWN
    confidence: float = 0.0

    # 可选:对证据的简短描述(不超过 80 字,纯事实不做叙事)
    note: str = ""

    # 可选:自由元数据(不得承载核心语义)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # schema 版本 (向后兼容)
    schema_version: str = CANONICAL_SCHEMA_VERSION

    # --------------------------------------------------------
    # 工具方法
    # --------------------------------------------------------
    def is_source_valid(self) -> bool:
        """source_type 是否属于受信任枚举。"""
        return self.source_type in VALID_SOURCE_TYPES

    def has_real_source(self) -> bool:
        """是否有真实且可追溯的来源对象。"""
        return self.is_source_valid() and bool(self.source_id)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ============================================================
# 2. ImpactMap: 影响范围声明
# ============================================================

@dataclass
class ImpactMap:
    """
    Milestone 对羽依存在维度的影响标记。

    设计原则 (D.6.1 预留 D.7 Identity 页面使用):
    - 默认全 False:不假定任何影响。
    - 每个 bool 只标记 "是否波及该维度",
      不承载程度(程度由 ExplanationMetadata.confidence + 各维度内部值表达)。
    """

    personality: bool = False
    belief: bool = False
    relationship: bool = False
    identity: bool = False
    memory: bool = False

    # schema 版本
    schema_version: str = CANONICAL_SCHEMA_VERSION

    def any_impact(self) -> bool:
        """至少一个维度被影响。"""
        return any([
            self.personality,
            self.belief,
            self.relationship,
            self.identity,
            self.memory,
        ])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 3. ExplanationMetadata: D.8 可解释性预留字段
# ============================================================

@dataclass
class ExplanationMetadata:
    """
    关于 Milestone "为什么存在" 的元数据。

    重要区分:
      - ExistenceMilestone.title / summary
        = 事实层:发生了什么(基于证据,可审计)

      - ExplanationMetadata.why_created
        = 解释层(未来 D.8 填充,当前允许空)
        由解释系统生成,不参与事实判定。

    默认值策略:
      - why_created = ""         (不填任何"推测性原因")
      - evidence_count = 0       (不伪造证据数)
      - confidence = 0.0         (不伪造置信度)
    """

    # 解释层:为什么这个 Milestone 被创建
    # D.6.1 默认留空,由未来 D.8 Explanation Engine 填充
    why_created: str = ""

    # 事实层:支撑该 Milestone 的证据条目数
    # D.6.1 Adapter 会基于 EvidenceReference 数量真实计算;
    # 默认 0 防止 "尚未统计即显示 5 条证据" 假数据
    evidence_count: int = 0

    # 事实层:聚合置信度
    # 计算由 TimelineBuilder 负责(例如 max / weighted);
    # 默认 0.0 绝不伪造 "0.82" 类中间值
    confidence: float = 0.0

    # 可选:稳定性得分 [0,1]
    # 例如同一条 Milestone 在多个时间窗口被再次确认 → 分数高
    # D.6.1 默认 0.0
    stability_score: float = 0.0

    # schema 版本
    schema_version: str = CANONICAL_SCHEMA_VERSION

    def has_explanation(self) -> bool:
        return bool(self.why_created)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 4. ExistenceMilestone: 存在记录 (核心契约)
# ============================================================

@dataclass
class ExistenceMilestone:
    """
    羽依存在记录 (Milestone)。

    这是 D.6.1 整个子阶段的核心契约。
    所有来源(Proposal / SelfModel / Evolution)都必须转换为该结构后
    才能进入 Timeline。

    防伪造闸口 (D.6.1.0 测试覆盖):
      - milestone_type 不在 VALID_MILESTONE_TYPES → is_well_formed() = False
      - sources 为空             → is_evidence_backed() = False
      - title / summary 都空     → is_well_formed() = False
      - confidence < 0 或 > 1    → 自动 clip 为 [0,1]
    """

    # 主键 (前缀 em_ 方便跨域追踪)
    milestone_id: str = field(
        default_factory=lambda: f"em_{uuid.uuid4().hex[:12]}"
    )

    # 发生时间 (UTC ISO 8601 with Z)
    # D.6.1 由 Adapter 从源对象真实取值;绝不使用 "now()" 伪造
    timestamp: str = ""

    # Milestone 类型 ∈ VALID_MILESTONE_TYPES
    milestone_type: str = ""

    # ---- 事实层:短标题 + 摘要 (纯事实,不叙事) ----
    # 例如:title = "温柔倾向开始稳定形成"
    #      summary = "经过多次互动后检测到对理解型回应的稳定偏好"
    title: str = ""
    summary: str = ""

    # ---- 证据链 ----
    # 绝不允许为了 "让 Timeline 好看" 而塞假证据。
    sources: List[EvidenceReference] = field(default_factory=list)

    # ---- 影响范围 ----
    impact: ImpactMap = field(default_factory=ImpactMap)

    # ---- 可解释性元数据 ----
    explanation: ExplanationMetadata = field(
        default_factory=ExplanationMetadata
    )

    # 可选:该 Milestone 影响的对象主键列表
    # 例:trait_change → ["warmth", "curiosity"]
    # 例:belief      → ["b_core_003"]
    # 例:growth      → ["pcr_ecf5397dc4"]
    affected_keys: List[str] = field(default_factory=list)

    # 可选:来源适配器标识 (D.6.1.1 调试用,不承载语义)
    adapter_name: str = ""

    # schema 版本
    schema_version: str = CANONICAL_SCHEMA_VERSION

    # ========================================================
    # 约束检查
    # ========================================================

    def has_valid_type(self) -> bool:
        return self.milestone_type in VALID_MILESTONE_TYPES

    def has_valid_timestamp(self) -> bool:
        if not self.timestamp:
            return False
        # 宽松校验:以 Z 结尾 (UTC)
        return self.timestamp.endswith("Z")

    def is_evidence_backed(self) -> bool:
        """
        至少有 1 条 有效且可追溯 的证据。
        这是防伪造的核心闸口 —— 没有证据 = 不进入 Timeline。
        """
        if not self.sources:
            return False
        return any(s.has_real_source() for s in self.sources)

    def has_fact_content(self) -> bool:
        """标题或摘要至少有一个非空。"""
        return bool(self.title or self.summary)

    def is_well_formed(self) -> bool:
        """
        形成完整存在记录的 4 条基本规则。
        TimelineBuilder 在过滤阶段只保留 is_well_formed() == True 的条目。
        """
        return (
            self.has_valid_type()
            and self.has_valid_timestamp()
            and self.is_evidence_backed()
            and self.has_fact_content()
        )

    def confidence(self) -> float:
        """对外统一置信度 = explanation.confidence,clamp [0,1]。"""
        c = self.explanation.confidence if self.explanation else 0.0
        return max(0.0, min(1.0, c))

    # ========================================================
    # 序列化
    # ========================================================

    def to_dict(self) -> Dict[str, Any]:
        return {
            "milestone_id": self.milestone_id,
            "timestamp": self.timestamp,
            "milestone_type": self.milestone_type,
            "title": self.title,
            "summary": self.summary,
            "sources": [s.to_dict() for s in self.sources],
            "impact": self.impact.to_dict() if self.impact else ImpactMap().to_dict(),
            "explanation": (
                self.explanation.to_dict()
                if self.explanation
                else ExplanationMetadata().to_dict()
            ),
            "affected_keys": list(self.affected_keys),
            "adapter_name": self.adapter_name,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ExistenceMilestone":
        """
        反序列化(宽松容错,避免旧数据 crash)。
        缺失字段自动回落为默认值(保持 UNKNOWN 原则)。
        类型不匹配:回落默认,不抛异常。
        """
        if not isinstance(d, dict):
            return cls()
        _mid_field = cls.__dataclass_fields__["milestone_id"]

        def _safe_str(v: Any, fallback: str = "") -> str:
            return v if isinstance(v, str) and v else fallback

        def _safe_factory_mid(v: Any):
            if isinstance(v, str) and v:
                return v
            # 任何非空串(milestone_id 不能是 int/其他类型)回落 uuid 默认
            if callable(_mid_field.default_factory):
                return _mid_field.default_factory()
            return f"em_{uuid.uuid4().hex[:12]}"

        def _safe_list(v: Any) -> List[Any]:
            return list(v) if isinstance(v, (list, tuple)) else []

        def _safe_int(v: Any, default: int = 0) -> int:
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        def _safe_float(v: Any, default: float = 0.0) -> float:
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        ms = cls(
            milestone_id=_safe_factory_mid(d.get("milestone_id")),
            timestamp=_safe_str(d.get("timestamp")),
            milestone_type=_safe_str(d.get("milestone_type")),
            title=_safe_str(d.get("title")),
            summary=_safe_str(d.get("summary")),
            affected_keys=_safe_list(d.get("affected_keys")),
            adapter_name=_safe_str(d.get("adapter_name")),
            schema_version=_safe_str(
                d.get("schema_version"), CANONICAL_SCHEMA_VERSION
            ) or CANONICAL_SCHEMA_VERSION,
        )
        # sources
        src_list = d.get("sources") or []
        if isinstance(src_list, list):
            _er_fld = EvidenceReference.__dataclass_fields__["evidence_id"]
            for s in src_list:
                if isinstance(s, dict):
                    _eid_val = s.get("evidence_id")
                    if isinstance(_eid_val, str) and _eid_val:
                        _eid = _eid_val
                    elif callable(_er_fld.default_factory):
                        _eid = _er_fld.default_factory()
                    else:
                        _eid = f"er_{uuid.uuid4().hex[:10]}"
                    ms.sources.append(EvidenceReference(
                        evidence_id=_eid,
                        source_type=_safe_str(s.get("source_type")),
                        source_id=_safe_str(s.get("source_id")),
                        confidence=_safe_float(s.get("confidence")),
                        note=_safe_str(s.get("note")),
                        metadata=dict(s.get("metadata") or {}) if isinstance(s.get("metadata"), (dict, type(None))) else {},
                        schema_version=_safe_str(
                            s.get("schema_version"), CANONICAL_SCHEMA_VERSION
                        ) or CANONICAL_SCHEMA_VERSION,
                    ))
        # impact
        imp = d.get("impact") if isinstance(d.get("impact"), dict) else {}
        ms.impact = ImpactMap(
            personality=bool(imp.get("personality")),
            belief=bool(imp.get("belief")),
            relationship=bool(imp.get("relationship")),
            identity=bool(imp.get("identity")),
            memory=bool(imp.get("memory")),
            schema_version=_safe_str(
                imp.get("schema_version"), CANONICAL_SCHEMA_VERSION
            ) or CANONICAL_SCHEMA_VERSION,
        )
        # explanation
        exp = d.get("explanation") if isinstance(d.get("explanation"), dict) else {}
        ms.explanation = ExplanationMetadata(
            why_created=_safe_str(exp.get("why_created")),
            evidence_count=_safe_int(exp.get("evidence_count")),
            confidence=_safe_float(exp.get("confidence")),
            stability_score=_safe_float(exp.get("stability_score")),
            schema_version=_safe_str(
                exp.get("schema_version"), CANONICAL_SCHEMA_VERSION
            ) or CANONICAL_SCHEMA_VERSION,
        )
        return ms


# ============================================================
# 5. Timeline 聚合器辅助常量 (D.6.1.2 预留)
# ============================================================

# Timeline 默认参数 (避免魔法数)
DEFAULT_TIMELINE_LIMIT = 50
DEFAULT_TIMELINE_MIN_CONFIDENCE = 0.20   # 低于 0.2 的视为证据不足,不展示
DEFAULT_TIMELINE_WINDOW_DAYS = 365       # 默认展示最近 1 年


# ============================================================
# 6. TimelineQuery (D.6.2 预留:分页/过滤查询契约,不实现逻辑)
# ============================================================

@dataclass
class TimelineQuery:
    """
    ExistenceTimeline 查询契约(Phase D.6.2.0 只声明字段,不实现执行器)。

    避免未来 UI 层硬编码 "last 50" 再重构。
    所有字段 Optional = None 时表示"不过滤该维度,取默认值"。

    预留使用方式(未来 D.6.2 Archive Tab 接分页 UI 时):
        q = TimelineQuery(
            types={"growth", "belief"},
            limit=20,
            cursor="em_xxx_timestamp_iso",
        )
        builder = ExistenceTimelineBuilder()
        builder.add(...)
        timeline, stats = builder.build(q.to_build_options())
    """

    # ---- 时间过滤 ----
    # ISO 8601 UTC with Z;None = 不限定
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    # ---- Milestone 类型白名单 ----
    # None = 全部允许;空集 = 不返回任何
    types: Optional[set] = None

    # ---- 置信度阈值(覆盖 DEFAULT_TIMELINE_MIN_CONFIDENCE) ----
    min_confidence: Optional[float] = None

    # ---- 分页 ----
    # limit = None → 使用 DEFAULT_TIMELINE_LIMIT
    limit: Optional[int] = None
    # 游标:上一次最后一条 milestone.timestamp (ISO Z)
    # None = 从头开始(第一页)
    cursor: Optional[str] = None
    # 分页方向:True = 游标之前(更旧),False = 游标之后(更新)
    cursor_before: bool = True

    # ---- 适配器过滤(调试用,生产通常置空) ----
    # None = 所有适配器;空列表 = 不接受任何适配器
    adapter_whitelist: Optional[List[str]] = None

    # ---- 受影响对象 key 过滤(例如只看 warmth 相关事件) ----
    # None = 不过滤;空列表 = 0 结果
    affected_keys: Optional[List[str]] = None

    # 预留可扩展元数据(不承载语义)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def has_time_window(self) -> bool:
        return bool(self.start_time or self.end_time)

    def has_types_filter(self) -> bool:
        return self.types is not None

    def has_cursor(self) -> bool:
        return bool(self.cursor)

    def types_filter_set(self) -> Optional[set]:
        if self.types is None:
            return None
        return {t for t in self.types if isinstance(t, str)}

    def to_limit(self, default: int = DEFAULT_TIMELINE_LIMIT) -> int:
        try:
            lim = int(self.limit) if self.limit is not None else int(default)
            if lim <= 0:
                return int(default)
            return lim
        except (TypeError, ValueError):
            return int(default)

    def to_min_confidence(self, default: float = DEFAULT_TIMELINE_MIN_CONFIDENCE) -> float:
        try:
            c = float(self.min_confidence) if self.min_confidence is not None else float(default)
            if c < 0.0:
                c = 0.0
            if c > 1.0:
                c = 1.0
            return c
        except (TypeError, ValueError):
            return float(default)

    def to_build_options(
        self,
        reference_now_utc: Optional[datetime] = None,
    ) -> "Any":  # 返回 TimelineBuildOptions, 懒引用避免循环 import
        """
        把 TimelineQuery 映射成 TimelineBuildOptions(给已有的 Builder 使用)。

        注意:
        - cursor 分页 不在此映射里(需要 TimelineBuildOptions 扩展,
          D.6.2 UI 分页前先保持 TimelineBuildOptions limit 语义即可)
        - time window 维度:start/end 通过 window_days + cutoff 表达不精确,
          此方法当前不覆盖 window_days;start/end 留待 Builder 升级后再消费。
        """
        # 懒 import:防止 contracts 层依赖 timeline 层循环
        from .existence_timeline import TimelineBuildOptions

        allowed_types = self.types_filter_set() if self.has_types_filter() else None

        return TimelineBuildOptions(
            min_confidence=self.to_min_confidence(),
            window_days=None,  # 不消费 window_days,由 start/end 在专门层处理
            reference_now_utc=reference_now_utc,
            order="desc",
            limit=self.to_limit(),
            allowed_types=allowed_types,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "types": list(self.types) if isinstance(self.types, (set, list)) else None,
            "min_confidence": self.min_confidence,
            "limit": self.limit,
            "cursor": self.cursor,
            "cursor_before": self.cursor_before,
            "adapter_whitelist": (
                list(self.adapter_whitelist)
                if isinstance(self.adapter_whitelist, list) else None
            ),
            "affected_keys": (
                list(self.affected_keys)
                if isinstance(self.affected_keys, list) else None
            ),
            "metadata": dict(self.metadata),
        }


__all__ = [
    # Type 常量
    "MILESTONE_GROWTH",
    "MILESTONE_BELIEF",
    "MILESTONE_REFLECTION",
    "MILESTONE_TRAIT_CHANGE",
    "MILESTONE_RELATIONSHIP",
    "MILESTONE_IDENTITY",
    "VALID_MILESTONE_TYPES",
    "SOURCE_GROWTH_PROPOSAL",
    "SOURCE_SELF_MODEL_BELIEF",
    "SOURCE_SELF_MODEL_TRAIT",
    "SOURCE_SELF_MODEL_HISTORY",
    "SOURCE_PERSONALITY_EVOLUTION",
    "SOURCE_REFLECTION_INSIGHT",
    "SOURCE_RELATIONSHIP_SNAPSHOT",
    "SOURCE_IDENTITY_ANCHOR",
    "VALID_SOURCE_TYPES",
    "CANONICAL_SCHEMA_VERSION",
    # 数据类
    "EvidenceReference",
    "ImpactMap",
    "ExplanationMetadata",
    "ExistenceMilestone",
    "TimelineQuery",
    # 常量
    "DEFAULT_TIMELINE_LIMIT",
    "DEFAULT_TIMELINE_MIN_CONFIDENCE",
    "DEFAULT_TIMELINE_WINDOW_DAYS",
    # 工具
    "now_iso",
]
