# -*- coding: utf-8 -*-
"""
src/runtime/self_model/identity_binding/personality_consistency_checker.py

Phase 4.4: PersonalityConsistencyChecker —— 一致性检查器

职责:
- 在 Response 生成前/后,检查候选回复与 SelfModel 的一致性
- 检测 identity conflict(身份冲突)
- 检测 value conflict(价值冲突)
- 输出 consistency score ∈ [0, 1] + 冲突明细

设计原则:
- 不调用 LLM,纯规则匹配 + 关键词 / 元数据
- 不修改 SelfModel,只读
- 不修改 ResponseEngine
- Runtime → Service → Snapshot 单向依赖

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


logger = logging.getLogger(__name__)


PERSONALITY_CONSISTENCY_CHECKER_SCHEMA_VERSION = "1.0"

# 文本截断
MAX_CONFLICT_SNIPPET = 200


def _new_id() -> str:
    return f"cons_{uuid.uuid4().hex[:12]}"


def _safe_str(value: Any, max_len: int = MAX_CONFLICT_SNIPPET) -> str:
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _clamp01(x: float) -> float:
    try:
        v = float(x)
    except Exception:  # noqa: BLE001
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


# ============================================================
# ConflictKind 枚举
# ============================================================
class ConflictKind(str, Enum):
    """冲突类型 —— Phase 4.4 v1.0。"""

    IDENTITY = "identity"            # 身份冲突:与 snapshot.identity 矛盾
    VALUE = "value"                  # 价值冲突:与 core_values 矛盾
    TRAIT = "trait"                  # 特质冲突:与 stable_traits 矛盾
    BEHAVIOR = "behavior"            # 行为冲突:违背 behavior_signature.forbidden
    REFLECTION = "reflection"        # 反思冲突:与 reflection 矛盾
    SAFETY = "safety"                # 安全/规则冲突(外部禁词)
    UNKNOWN = "unknown"

    @classmethod
    def values(cls) -> List[str]:
        return [c.value for c in cls]


# ============================================================
# ConflictDetail
# ============================================================
@dataclass
class ConflictDetail:
    """单个冲突明细。

    字段:
    - kind:           str                 # ConflictKind.value
    - reason:         str                 # 简短原因
    - matched_term:   str                 # 触发匹配的关键词
    - against_term:   str                 # 冲突对象(identity field / value / 等)
    - severity:       float (0~1)
    - snippet:        str                 # 候选文本中的相关片段
    """

    kind: str = ConflictKind.UNKNOWN.value
    reason: str = ""
    matched_term: str = ""
    against_term: str = ""
    severity: float = 0.5
    snippet: str = ""

    def __post_init__(self) -> None:
        self.severity = _clamp01(self.severity)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConflictDetail":
        payload = dict(data or {})
        return cls(
            kind=str(payload.get("kind", ConflictKind.UNKNOWN.value)),
            reason=str(payload.get("reason", "") or ""),
            matched_term=str(payload.get("matched_term", "") or ""),
            against_term=str(payload.get("against_term", "") or ""),
            severity=float(payload.get("severity", 0.5) or 0.5),
            snippet=str(payload.get("snippet", "") or ""),
        )


# ============================================================
# ConsistencyResult
# ============================================================
@dataclass
class ConsistencyResult:
    """一致性检查结果 —— Phase 4.4 v1.0。

    字段:
    - check_id:        str
    - consistency:     float (0~1)         # 一致性分数,1=完全一致
    - conflict_score:  float (0~1)         # 冲突分数,0=无冲突
    - is_consistent:   bool                # 一致性是否达标(由 threshold 决定)
    - threshold:       float (0~1)         # 通过阈值
    - has_conflict:    bool                # 是否有任何冲突
    - conflict_count:  int
    - conflicts:       List[ConflictDetail]
    - scenario:        str                 # 检查时使用的场景
    - snapshot_id:     str
    - schema_version:  str = "1.0"
    - meta:            Dict[str, Any]
    """

    consistency: float = 1.0
    conflict_score: float = 0.0
    is_consistent: bool = True
    threshold: float = 0.6
    has_conflict: bool = False
    conflict_count: int = 0
    conflicts: List[ConflictDetail] = field(default_factory=list)
    scenario: str = "unknown"
    snapshot_id: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    check_id: str = field(default_factory=_new_id)
    schema_version: str = PERSONALITY_CONSISTENCY_CHECKER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.consistency = _clamp01(self.consistency)
        self.conflict_score = _clamp01(self.conflict_score)
        self.threshold = _clamp01(self.threshold)
        self.conflict_count = len(self.conflicts)
        self.has_conflict = self.conflict_count > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "consistency": self.consistency,
            "conflict_score": self.conflict_score,
            "is_consistent": self.is_consistent,
            "threshold": self.threshold,
            "has_conflict": self.has_conflict,
            "conflict_count": self.conflict_count,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "scenario": self.scenario,
            "snapshot_id": self.snapshot_id,
            "schema_version": self.schema_version,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConsistencyResult":
        payload = dict(data or {})
        conflicts_raw = payload.get("conflicts", []) or []
        conflicts = [
            ConflictDetail.from_dict(c)
            for c in conflicts_raw
            if isinstance(c, dict)
        ]
        return cls(
            consistency=float(payload.get("consistency", 1.0) or 1.0),
            conflict_score=float(payload.get("conflict_score", 0.0) or 0.0),
            is_consistent=bool(payload.get("is_consistent", True)),
            threshold=float(payload.get("threshold", 0.6) or 0.6),
            conflicts=conflicts,
            scenario=str(payload.get("scenario", "unknown") or "unknown"),
            snapshot_id=str(payload.get("snapshot_id", "") or ""),
            meta=dict(payload.get("meta", {}) or {}),
            check_id=str(payload.get("check_id", "") or _new_id()),
            schema_version=str(
                payload.get("schema_version")
                or PERSONALITY_CONSISTENCY_CHECKER_SCHEMA_VERSION
            ),
        )


# ============================================================
# PersonalityConsistencyChecker
# ============================================================
class PersonalityConsistencyChecker:
    """一致性检查器(Phase 4.4 / v1.0)。

    使用方式:
        checker = PersonalityConsistencyChecker(threshold=0.6)
        result = checker.check(
            candidate_text="...",
            identity_context=ctx,                # IdentityContext
            behavior_signature=sig,              # BehaviorSignature(可选)
            scenario="emotional_topic",
        )
        if not result.is_consistent:
            # 降级 / 重生成 / 警告
            ...
    """

    name: str = "personality_consistency_checker"
    schema_version: str = PERSONALITY_CONSISTENCY_CHECKER_SCHEMA_VERSION

    # identity 冲突默认禁词(以子串匹配,大小写不敏感)
    # severity: 0.95(高优先级,直接关联身份设定)
    DEFAULT_IDENTITY_FORBIDDEN: Tuple[str, ...] = (
        "我不是",
        "我是ai",
        "我是ai助手",
        "我是人工智能",
        "我只是人工智能",
        "我只是一个人工智能",
        "只是一个人工智能",
        "我没有情感",
        "没有情感",
        "i am an ai",
        "as an ai",
        "i am just an ai",
        "just an ai",
        "i'm an ai",
        "i'm just an ai",
    )

    # safety 禁词(基础;真实部署应使用更完整的清单)
    DEFAULT_SAFETY_FORBIDDEN: Tuple[str, ...] = (
        "hack",
        "exploit",
        "weapon",
    )

    def __init__(
        self,
        threshold: float = 0.6,
        identity_forbidden: Optional[Sequence[str]] = None,
        safety_forbidden: Optional[Sequence[str]] = None,
        min_text_length: int = 0,
    ) -> None:
        if not (0.0 <= threshold <= 1.0):
            threshold = 0.6
        self._threshold: float = threshold
        self._identity_forbidden: Tuple[str, ...] = tuple(
            (s.lower() for s in (identity_forbidden or self.DEFAULT_IDENTITY_FORBIDDEN))
        )
        self._safety_forbidden: Tuple[str, ...] = tuple(
            (s.lower() for s in (safety_forbidden or self.DEFAULT_SAFETY_FORBIDDEN))
        )
        self._min_text_length: int = max(0, int(min_text_length))
        self._check_count: int = 0
        self._last_check_id: Optional[str] = None
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def check(
        self,
        candidate_text: Optional[str] = None,
        identity_context: Optional[Any] = None,
        behavior_signature: Optional[Any] = None,
        scenario: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConsistencyResult:
        """执行一致性检查。

        Args:
            candidate_text:    候选回复文本(可选;None/空 → 返回空 result)
            identity_context:  IdentityContext 或 dict 或 None
            behavior_signature: BehaviorSignature 或 dict 或 None
            scenario:          行为场景(默认 "unknown")
            metadata:          附加元信息

        Returns:
            ConsistencyResult(异常隔离;任何失败返回"一致")
        """
        try:
            text = (candidate_text or "").strip()
            if not text:
                # 空文本:视为完全一致(无内容可冲突)
                self._check_count += 1
                self._last_check_id = _new_id()
                self._last_error = None
                return ConsistencyResult(
                    consistency=1.0,
                    conflict_score=0.0,
                    is_consistent=True,
                    threshold=self._threshold,
                    conflicts=[],
                    scenario=str(scenario or "unknown"),
                    snapshot_id=self._extract_snapshot_id(identity_context),
                    meta={"reason": "empty_text", **(metadata or {})},
                    check_id=self._last_check_id,
                )

            # 文本过短:直接通过(避免误报)
            if len(text) < self._min_text_length:
                self._check_count += 1
                self._last_check_id = _new_id()
                self._last_error = None
                return ConsistencyResult(
                    consistency=1.0,
                    conflict_score=0.0,
                    is_consistent=True,
                    threshold=self._threshold,
                    conflicts=[],
                    scenario=str(scenario or "unknown"),
                    snapshot_id=self._extract_snapshot_id(identity_context),
                    meta={
                        "reason": "below_min_text_length",
                        **(metadata or {}),
                    },
                    check_id=self._last_check_id,
                )

            conflicts: List[ConflictDetail] = []
            text_lower = text.lower()

            # 1) identity 禁词
            conflicts.extend(self._check_identity(text_lower, text))

            # 2) value 冲突
            conflicts.extend(
                self._check_values(text_lower, text, identity_context)
            )

            # 3) trait 冲突
            conflicts.extend(
                self._check_traits(text_lower, text, identity_context)
            )

            # 4) behavior_signature.forbidden
            conflicts.extend(
                self._check_behavior(
                    text_lower, text, behavior_signature,
                )
            )

            # 5) reflection 一致性(优先级警告)
            conflicts.extend(
                self._check_reflection(text_lower, text, identity_context)
            )

            # 6) safety
            conflicts.extend(self._check_safety(text_lower, text))

            # 计算分数
            conflict_score = self._score_from_conflicts(conflicts, text)
            consistency = 1.0 - conflict_score
            is_consistent = consistency >= self._threshold

            self._check_count += 1
            check_id = _new_id()
            self._last_check_id = check_id
            self._last_error = None

            return ConsistencyResult(
                consistency=consistency,
                conflict_score=conflict_score,
                is_consistent=is_consistent,
                threshold=self._threshold,
                conflicts=conflicts,
                scenario=str(scenario or "unknown"),
                snapshot_id=self._extract_snapshot_id(identity_context),
                meta={
                    "text_length": len(text),
                    **(metadata or {}),
                },
                check_id=check_id,
            )
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"check_failed: {exc}"
            self._check_count += 1
            logger.warning("PersonalityConsistencyChecker.check 失败: %s", exc)
            # 失败时返回"一致"(避免误判阻断回复)
            return ConsistencyResult(
                consistency=1.0,
                conflict_score=0.0,
                is_consistent=True,
                threshold=self._threshold,
                conflicts=[],
                scenario=str(scenario or "unknown"),
                snapshot_id=self._extract_snapshot_id(identity_context),
                meta={"error": str(exc), **(metadata or {})},
            )

    # --------------------------------------------------------
    # 内部:各类冲突检测
    # --------------------------------------------------------
    def _check_identity(
        self,
        text_lower: str,
        text_orig: str,
    ) -> List[ConflictDetail]:
        out: List[ConflictDetail] = []
        for term in self._identity_forbidden:
            if term and term in text_lower:
                snippet = self._extract_snippet(text_orig, term)
                out.append(
                    ConflictDetail(
                        kind=ConflictKind.IDENTITY.value,
                        reason="候选回复与身份设定不一致",
                        matched_term=term,
                        against_term="identity",
                        severity=0.95,
                        snippet=snippet,
                    )
                )
        return out

    def _check_values(
        self,
        text_lower: str,
        text_orig: str,
        identity_context: Optional[Any],
    ) -> List[ConflictDetail]:
        """检测 candidate 是否与 core_values 冲突(以"反向关键词"为弱信号)。"""
        out: List[ConflictDetail] = []
        # 价值 -> 反向词(若 candidate 含反向词 + 价值词,可能冲突)
        value_negation_map: Dict[str, Tuple[str, ...]] = {
            "kindness": ("残忍", "冷酷", "rude", "cruel"),
            "honesty": ("骗", "谎言", "lie", "deceive"),
            "patience": ("急躁", "不耐烦", "impatient"),
            "warmth": ("冷漠", "cold", "indifferent"),
            "companion": ("ignore", "disregard"),
        }
        value_labels = self._extract_value_labels(identity_context)
        for value in value_labels:
            key = value.lower()
            for vk, negs in value_negation_map.items():
                if vk in key:
                    for neg in negs:
                        if neg in text_lower:
                            out.append(
                                ConflictDetail(
                                    kind=ConflictKind.VALUE.value,
                                    reason=(
                                        f"候选回复中疑似出现与 {value} "
                                        f"相冲突的措辞"
                                    ),
                                    matched_term=neg,
                                    against_term=value,
                                    severity=0.5,
                                    snippet=self._extract_snippet(
                                        text_orig, neg,
                                    ),
                                )
                            )
        return out

    def _check_traits(
        self,
        text_lower: str,
        text_orig: str,
        identity_context: Optional[Any],
    ) -> List[ConflictDetail]:
        """检测 candidate 是否与 stable_traits 冲突(粗粒度关键词)。"""
        out: List[ConflictDetail] = []
        traits = self._extract_trait_labels(identity_context)
        # 特质 -> 冲突措辞
        trait_negation_map: Dict[str, Tuple[str, ...]] = {
            "calm": ("暴怒", "furious", "rage"),
            "patient": ("急躁", "impatient", "snappy"),
            "gentle": ("粗鲁", "rude", "harsh"),
            "kind": ("刻薄", "mean", "unkind"),
            "rational": ("完全感性", "冲动地", "impulsively"),
        }
        for tr in traits:
            key = tr.lower()
            for tk, negs in trait_negation_map.items():
                if tk in key:
                    for neg in negs:
                        if neg in text_lower:
                            out.append(
                                ConflictDetail(
                                    kind=ConflictKind.TRAIT.value,
                                    reason=(
                                        f"候选回复中疑似出现与 {tr} "
                                        f"相冲突的措辞"
                                    ),
                                    matched_term=neg,
                                    against_term=tr,
                                    severity=0.4,
                                    snippet=self._extract_snippet(
                                        text_orig, neg,
                                    ),
                                )
                            )
        return out

    def _check_behavior(
        self,
        text_lower: str,
        text_orig: str,
        behavior_signature: Optional[Any],
    ) -> List[ConflictDetail]:
        out: List[ConflictDetail] = []
        forbidden_list = self._extract_forbidden(behavior_signature)
        for term in forbidden_list:
            t = str(term).strip().lower()
            if not t:
                continue
            if t in text_lower:
                out.append(
                    ConflictDetail(
                        kind=ConflictKind.BEHAVIOR.value,
                        reason="候选回复触发了行为禁止项",
                        matched_term=t,
                        against_term="behavior_signature.forbidden",
                        severity=0.6,
                        snippet=self._extract_snippet(text_orig, t),
                    )
                )
        return out

    def _check_reflection(
        self,
        text_lower: str,
        text_orig: str,
        identity_context: Optional[Any],
    ) -> List[ConflictDetail]:
        """若 reflection 表示某种倾向,且 candidate 出现反向关键词,
        记一条 REFLECTION 弱冲突(severity 较低)。"""
        out: List[ConflictDetail] = []
        if identity_context is None:
            return out
        reflection: Optional[Dict[str, Any]] = None
        if hasattr(identity_context, "reflection"):
            try:
                r = identity_context.reflection
                if isinstance(r, dict):
                    reflection = r
            except Exception:  # noqa: BLE001
                pass
        elif isinstance(identity_context, dict):
            r = identity_context.get("reflection")
            if isinstance(r, dict):
                reflection = r
        if not reflection:
            return out
        obs = str(reflection.get("observation", "") or "").lower()
        if not obs:
            return out
        # 极简判定:如果 reflection 在说"warmth 在增强",candidate 中出现"cold"
        # 等反向词时记一条弱冲突
        if "warmth" in obs or "warm" in obs:
            if "cold" in text_lower or "冷漠" in text_lower:
                out.append(
                    ConflictDetail(
                        kind=ConflictKind.REFLECTION.value,
                        reason="候选回复与最近反思的 warmth 倾向不一致",
                        matched_term="cold",
                        against_term="warmth",
                        severity=0.3,
                        snippet=self._extract_snippet(text_orig, "cold"),
                    )
                )
        return out

    def _check_safety(
        self,
        text_lower: str,
        text_orig: str,
    ) -> List[ConflictDetail]:
        out: List[ConflictDetail] = []
        for term in self._safety_forbidden:
            if term and term in text_lower:
                out.append(
                    ConflictDetail(
                        kind=ConflictKind.SAFETY.value,
                        reason="候选回复中包含基础安全禁词",
                        matched_term=term,
                        against_term="safety_policy",
                        severity=0.9,
                        snippet=self._extract_snippet(text_orig, term),
                    )
                )
        return out

    # --------------------------------------------------------
    # 评分
    # --------------------------------------------------------
    def _score_from_conflicts(
        self,
        conflicts: List[ConflictDetail],
        text: str,
    ) -> float:
        if not conflicts:
            return 0.0
        # 文本长度修正:文本越长,conflict 应更难拉满分(弱化短文本的过度抑制)
        text_len = max(1, len(text))
        # 归一化:sum(severity) / N + 多样性惩罚
        agg = sum(c.severity for c in conflicts)
        # 类别多样性惩罚(>=2 类 → 分数更重)
        kinds = {c.kind for c in conflicts}
        diversity_penalty = 0.0
        if len(kinds) >= 3:
            diversity_penalty = 0.15
        elif len(kinds) == 2:
            diversity_penalty = 0.08
        score = agg / max(1, len(conflicts))
        score += diversity_penalty
        # 文本长度修正 —— 温和的对数衰减,避免短文本被压得太低
        try:
            import math
            # 使用 (1 + 0.1 * log10(N)), 短文本衰减 ~10%
            length_factor = 1.0 / (1.0 + 0.1 * math.log10(max(10, text_len)))
        except Exception:  # noqa: BLE001
            length_factor = 1.0
        score *= length_factor
        return _clamp01(score)

    # --------------------------------------------------------
    # 辅助
    # --------------------------------------------------------
    @staticmethod
    def _extract_snippet(text: str, term: str, max_len: int = 80) -> str:
        """围绕 term 截取一段 snippet。"""
        try:
            t = str(text)
            term_lower = term.lower()
            idx = t.lower().find(term_lower)
            if idx < 0:
                return _safe_str(t, max_len)
            start = max(0, idx - max_len // 2)
            end = min(len(t), idx + len(term) + max_len // 2)
            return _safe_str(t[start:end], max_len)
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _extract_snapshot_id(identity_context: Optional[Any]) -> str:
        if identity_context is None:
            return ""
        if isinstance(identity_context, dict):
            return str(identity_context.get("identity_id", "") or "")
        return str(getattr(identity_context, "identity_id", "") or "")

    @staticmethod
    def _extract_value_labels(
        identity_context: Optional[Any],
    ) -> List[str]:
        out: List[str] = []
        if identity_context is None:
            return out
        cvs: Any = None
        if isinstance(identity_context, dict):
            cvs = identity_context.get("core_values")
        else:
            cvs = getattr(identity_context, "core_values", None)
        if not isinstance(cvs, list):
            return out
        for cv in cvs:
            if isinstance(cv, dict):
                label = cv.get("name") or cv.get("label") or cv.get("value")
                if label:
                    out.append(str(label))
            elif cv is not None:
                out.append(str(cv))
        return out

    @staticmethod
    def _extract_trait_labels(
        identity_context: Optional[Any],
    ) -> List[str]:
        out: List[str] = []
        if identity_context is None:
            return out
        ts: Any = None
        if isinstance(identity_context, dict):
            ts = identity_context.get("stable_traits")
        else:
            ts = getattr(identity_context, "stable_traits", None)
        if not isinstance(ts, list):
            return out
        for t in ts:
            if isinstance(t, dict):
                label = t.get("name") or t.get("trait")
                if label:
                    out.append(str(label))
            elif t is not None:
                out.append(str(t))
        return out

    @staticmethod
    def _extract_forbidden(
        behavior_signature: Optional[Any],
    ) -> List[str]:
        if behavior_signature is None:
            return []
        scenarios: Any = None
        if isinstance(behavior_signature, dict):
            scenarios = behavior_signature.get("scenarios")
        else:
            scenarios = getattr(behavior_signature, "scenarios", None)
        if not isinstance(scenarios, dict):
            return []
        out: List[str] = []
        for v in scenarios.values():
            if isinstance(v, dict):
                fb = v.get("forbidden") or []
            else:
                fb = getattr(v, "forbidden", []) or []
            for x in fb:
                if x is not None:
                    out.append(str(x))
        return out

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def check_count(self) -> int:
        return self._check_count

    @property
    def last_check_id(self) -> Optional[str]:
        return self._last_check_id

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "threshold": self._threshold,
            "check_count": self._check_count,
            "last_check_id": self._last_check_id,
            "last_error": self._last_error,
            "identity_forbidden_count": len(self._identity_forbidden),
            "safety_forbidden_count": len(self._safety_forbidden),
        }

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "threshold": self._threshold,
            "check_count": self._check_count,
            "last_check_id": self._last_check_id,
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        return result


__all__ = [
    "PERSONALITY_CONSISTENCY_CHECKER_SCHEMA_VERSION",
    "ConflictKind",
    "ConflictDetail",
    "ConsistencyResult",
    "PersonalityConsistencyChecker",
]
