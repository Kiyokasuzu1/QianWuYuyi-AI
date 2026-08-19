# -*- coding: utf-8 -*-
"""
src/runtime/self_model/identity_binding/behavior_signature.py

Phase 4.4: BehaviorSignature —— 行为模板与场景化行为规则

职责:
- 描述羽依在不同场景下的稳定行为模式
- 从 SelfModelSnapshot 启发式生成 BehaviorSignature
- 提供 pattern lookup(按 scenario 返回 BehaviorPattern)
- 用于 PersonalityConsistencyChecker / IdentityContextBuilder

场景(内置):
- greeting:        首次打招呼
- emotional_topic: 情绪话题
- technical_topic: 技术 / 知识类话题
- conflict:        冲突 / 意见分歧
- unknown:         未识别场景(默认 fallback)

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 不调用 LLM,纯规则
- Runtime → Service → Snapshot 单向依赖
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


BEHAVIOR_SIGNATURE_SCHEMA_VERSION = "1.0"

# 内置场景集合(可扩展)
DEFAULT_SCENARIOS: List[str] = [
    "greeting",
    "emotional_topic",
    "technical_topic",
    "conflict",
    "unknown",
]

# 文本截断
MAX_TEXT_TONE = 80
MAX_TEXT_LINE = 120
MAX_TEXT_PRINCIPLE = 120
MAX_TEXT_EXEMPLAR = 160


def _now_iso() -> str:
    """统一时间戳(优先 timezone-aware,旧版本回退)。"""
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.utcnow().isoformat() + "Z"


def _new_id() -> str:
    return f"bsig_{uuid.uuid4().hex[:12]}"


def _safe_str(value: Any, max_len: int = MAX_TEXT_LINE) -> str:
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


# ============================================================
# BehaviorPattern —— 单个场景的行为模式
# ============================================================
@dataclass
class BehaviorPattern:
    """单场景行为模式 —— Phase 4.4 v1.0。

    字段:
    - scenario:          str                # 场景 key
    - tone:              str                # 推荐语气(如 "warm" / "neutral" / "calm")
    - opening_style:     str                # 开场风格描述
    - principles:        List[str]          # 行为原则
    - forbidden:         List[str]          # 禁止出现的模式
    - exemplars:         List[str]          # 简短示例(不直接喂 prompt,只作 pattern)
    - weight:            float (0~1)        # 模式强度
    - meta:              Dict[str, Any]
    """

    scenario: str = "unknown"
    tone: str = "neutral"
    opening_style: str = ""
    principles: List[str] = field(default_factory=list)
    forbidden: List[str] = field(default_factory=list)
    exemplars: List[str] = field(default_factory=list)
    weight: float = 0.5
    meta: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 <= self.weight <= 1.0):
            # 静默 clamp,不抛异常(避免外部脏数据)
            try:
                w = float(self.weight)
            except Exception:  # noqa: BLE001
                w = 0.5
            self.weight = max(0.0, min(1.0, w))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BehaviorPattern":
        payload = dict(data or {})
        return cls(
            scenario=str(payload.get("scenario", "unknown") or "unknown"),
            tone=str(payload.get("tone", "neutral") or "neutral"),
            opening_style=str(payload.get("opening_style", "") or ""),
            principles=list(payload.get("principles", []) or []),
            forbidden=list(payload.get("forbidden", []) or []),
            exemplars=list(payload.get("exemplars", []) or []),
            weight=float(payload.get("weight", 0.5) or 0.5),
            meta=dict(payload.get("meta", {}) or {}),
        )


# ============================================================
# BehaviorSignature —— 多场景行为模板集合
# ============================================================
@dataclass
class BehaviorSignature:
    """行为签名 —— Phase 4.4 v1.0。

    字段:
    - signature_id:    str
    - identity_id:     str
    - schema_version:  str = "1.0"
    - created_at:      str
    - default_scenario: str               # 未命中时的 fallback
    - scenarios:       Dict[str, BehaviorPattern]  # key -> pattern
    - meta:            Dict[str, Any]
    """

    identity_id: str = ""
    default_scenario: str = "unknown"
    scenarios: Dict[str, BehaviorPattern] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    signature_id: str = field(default_factory=_new_id)
    created_at: str = field(default_factory=_now_iso)
    schema_version: str = BEHAVIOR_SIGNATURE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        # 兜底:scenarios 必含 default_scenario
        if self.default_scenario not in self.scenarios:
            self.scenarios[self.default_scenario] = BehaviorPattern(
                scenario=self.default_scenario,
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signature_id": self.signature_id,
            "identity_id": self.identity_id,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "default_scenario": self.default_scenario,
            "scenarios": {
                k: (v.to_dict() if isinstance(v, BehaviorPattern) else dict(v))
                for k, v in self.scenarios.items()
            },
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BehaviorSignature":
        payload = dict(data or {})
        scs_raw = payload.get("scenarios", {}) or {}
        scenarios: Dict[str, BehaviorPattern] = {}
        if isinstance(scs_raw, dict):
            for k, v in scs_raw.items():
                if isinstance(v, BehaviorPattern):
                    scenarios[str(k)] = v
                elif isinstance(v, dict):
                    try:
                        scenarios[str(k)] = BehaviorPattern.from_dict(v)
                    except Exception:  # noqa: BLE001
                        scenarios[str(k)] = BehaviorPattern(scenario=str(k))
                else:
                    scenarios[str(k)] = BehaviorPattern(scenario=str(k))
        return cls(
            identity_id=str(payload.get("identity_id", "") or ""),
            default_scenario=str(
                payload.get("default_scenario", "unknown") or "unknown",
            ),
            scenarios=scenarios,
            meta=dict(payload.get("meta", {}) or {}),
            signature_id=str(
                payload.get("signature_id", "") or _new_id(),
            ),
            created_at=str(
                payload.get("created_at", "") or _now_iso(),
            ),
            schema_version=str(
                payload.get("schema_version")
                or BEHAVIOR_SIGNATURE_SCHEMA_VERSION
            ),
        )

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def get(self, scenario: str) -> BehaviorPattern:
        """获取某场景的 pattern;未命中返回 default_scenario 的 pattern。"""
        if not scenario:
            scenario = self.default_scenario
        p = self.scenarios.get(scenario)
        if p is not None:
            return p
        fallback = self.scenarios.get(self.default_scenario)
        if fallback is not None:
            return fallback
        return BehaviorPattern(scenario=self.default_scenario)

    def has(self, scenario: str) -> bool:
        return scenario in self.scenarios

    def keys(self) -> List[str]:
        return list(self.scenarios.keys())


# ============================================================
# BehaviorSignatureProvider —— 启发式生成 BehaviorSignature
# ============================================================
class BehaviorSignatureProvider:
    """BehaviorSignature 提供器(Phase 4.4 / v1.0)。

    使用方式:
        provider = BehaviorSignatureProvider()
        sig = provider.build(snapshot)
        pattern = sig.get("greeting")

    启发式规则(从 SelfModel 抽取):
    - core_values 包含 "kindness" / "warmth"  →  emotional_topic 加重 weight
    - core_values 包含 "honesty" / "truth"    →  technical_topic 加重 weight
    - identity.archetype / current_state 给出 tone 提示
    - stable_traits 包含 "calm"               →  conflict tone 偏 calm
    - forbidden 始终包含跨场景的"无价值创造 / 凭空杜撰"等基础禁止
    """

    name: str = "behavior_signature_provider"
    schema_version: str = BEHAVIOR_SIGNATURE_SCHEMA_VERSION

    # trait/keyword → scenario weight 调整
    WARMTH_KEYWORDS = ("kindness", "warmth", "compassion", "care", "gentle")
    RATIONAL_KEYWORDS = ("honesty", "truth", "logic", "rational", "precise")
    CALM_KEYWORDS = ("calm", "patient", "steady", "composed")
    PLAYFUL_KEYWORDS = ("playful", "humor", "witty", "cheerful")

    def __init__(self) -> None:
        self._build_count: int = 0
        self._last_identity_id: Optional[str] = None
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def build(
        self,
        snapshot: Optional[Any] = None,
        overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> BehaviorSignature:
        """从 SelfModelSnapshot 启发式生成 BehaviorSignature。

        Args:
            snapshot: SelfModelSnapshot 或 None
            overrides: 可选 per-scenario 覆盖(键 = scenario,值 = pattern dict)

        Returns:
            BehaviorSignature(snapshot=None 时返回 minimal default signature)
        """
        if snapshot is None:
            self._build_count += 1
            self._last_identity_id = None
            self._last_error = None
            return self._default_signature("", overrides or {})

        try:
            sig = self._build_from_snapshot(snapshot, overrides or {})
            self._last_identity_id = getattr(snapshot, "identity_id", None)
            self._last_error = None
            self._build_count += 1
            return sig
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"build_failed: {exc}"
            self._build_count += 1
            logger.warning("BehaviorSignatureProvider.build 失败: %s", exc)
            return self._default_signature(
                str(getattr(snapshot, "identity_id", "") or ""),
                overrides or {},
            )

    # --------------------------------------------------------
    # 内部:默认 signature
    # --------------------------------------------------------
    def _default_signature(
        self,
        identity_id: str,
        overrides: Dict[str, Dict[str, Any]],
    ) -> BehaviorSignature:
        scenarios: Dict[str, BehaviorPattern] = {
            "greeting": BehaviorPattern(
                scenario="greeting",
                tone="warm",
                opening_style="先打招呼,再询问对方需要什么",
                principles=["保持简洁", "不重复上一轮内容"],
                forbidden=["凭空问候用户身份", "捏造上次对话"],
                exemplars=[],
                weight=0.6,
            ),
            "emotional_topic": BehaviorPattern(
                scenario="emotional_topic",
                tone="warm",
                opening_style="先共情再回应",
                principles=[
                    "不否定对方感受",
                    "不主动建议解决方案,除非被请求",
                ],
                forbidden=[
                    "轻视对方情绪",
                    "立即跳到技术 / 数据论证",
                ],
                exemplars=[],
                weight=0.7,
            ),
            "technical_topic": BehaviorPattern(
                scenario="technical_topic",
                tone="neutral",
                opening_style="先确认问题,再给结构化回答",
                principles=["不编造 API / 库 / 版本", "承认不确定"],
                forbidden=[
                    "凭印象给出代码",
                    "回避自己不知道的部分",
                ],
                exemplars=[],
                weight=0.7,
            ),
            "conflict": BehaviorPattern(
                scenario="conflict",
                tone="calm",
                opening_style="先承认对方立场,再表达自己的看法",
                principles=["不攻击对方", "避免绝对化措辞"],
                forbidden=["情绪化反击", "全盘否定对方"],
                exemplars=[],
                weight=0.7,
            ),
            "unknown": BehaviorPattern(
                scenario="unknown",
                tone="neutral",
                opening_style="先确认对方意图",
                principles=["保持好奇", "承认不确定"],
                forbidden=["假装知道"],
                exemplars=[],
                weight=0.5,
            ),
        }
        # 应用 overrides
        for sc, ov in overrides.items():
            base = scenarios.get(sc) or BehaviorPattern(scenario=sc)
            scenarios[sc] = self._merge_pattern(base, ov)
        return BehaviorSignature(
            identity_id=identity_id,
            default_scenario="unknown",
            scenarios=scenarios,
            meta={"source": "default"},
        )

    # --------------------------------------------------------
    # 内部:从 snapshot 构建
    # --------------------------------------------------------
    def _build_from_snapshot(
        self,
        snapshot: Any,
        overrides: Dict[str, Dict[str, Any]],
    ) -> BehaviorSignature:
        identity_id = str(getattr(snapshot, "identity_id", "") or "")
        identity = getattr(snapshot, "identity", None)
        if not isinstance(identity, dict):
            identity = {}
        core_values = getattr(snapshot, "core_values", None) or []
        stable_traits = getattr(snapshot, "stable_traits", None) or []
        current_state = getattr(snapshot, "current_state", None) or {}
        if not isinstance(current_state, dict):
            current_state = {}

        # 收集 keyword 命中
        keywords: List[str] = []
        for cv in core_values:
            if isinstance(cv, dict):
                label = (
                    cv.get("name") or cv.get("label") or cv.get("value")
                )
                if label:
                    keywords.append(str(label).lower())
            elif cv is not None:
                keywords.append(str(cv).lower())
        for t in stable_traits:
            if isinstance(t, dict):
                label = t.get("name") or t.get("trait")
                if label:
                    keywords.append(str(label).lower())
            elif t is not None:
                keywords.append(str(t).lower())

        warmth_hit = any(k in kw for kw in keywords for k in self.WARMTH_KEYWORDS)
        rational_hit = any(
            k in kw for kw in keywords for k in self.RATIONAL_KEYWORDS
        )
        calm_hit = any(k in kw for kw in keywords for k in self.CALM_KEYWORDS)
        playful_hit = any(
            k in kw for kw in keywords for k in self.PLAYFUL_KEYWORDS
        )

        # tone 启发
        base_tone = "neutral"
        if warmth_hit:
            base_tone = "warm"
        if rational_hit and not warmth_hit:
            base_tone = "neutral"
        if calm_hit and not warmth_hit:
            base_tone = "calm"
        if playful_hit and not warmth_hit:
            base_tone = "playful"

        # 构建 scenarios
        scenarios: Dict[str, BehaviorPattern] = {
            "greeting": BehaviorPattern(
                scenario="greeting",
                tone=base_tone if base_tone in ("warm", "playful") else "warm",
                opening_style=(
                    "用称呼 + 关心对方状态开场"
                    if warmth_hit or base_tone == "warm"
                    else "简洁问候"
                ),
                principles=[
                    "保持简洁",
                    "不重复上一轮内容",
                    "不凭空假设对方身份",
                ],
                forbidden=[
                    "凭空问候用户身份",
                    "捏造上次对话",
                    "过度热情",
                ],
                weight=0.6 + (0.2 if warmth_hit else 0.0),
            ),
            "emotional_topic": BehaviorPattern(
                scenario="emotional_topic",
                tone=base_tone if base_tone in ("warm", "calm") else "warm",
                opening_style=(
                    "先共情再回应"
                    if warmth_hit
                    else "先承认对方感受,再中性回应"
                ),
                principles=[
                    "不否定对方感受",
                    "不立即跳到技术 / 数据论证",
                ],
                forbidden=[
                    "轻视对方情绪",
                    "机械化分析",
                    "使用绝对化措辞",
                ],
                weight=0.7 + (0.2 if warmth_hit else 0.0),
            ),
            "technical_topic": BehaviorPattern(
                scenario="technical_topic",
                tone="neutral" if not rational_hit else "neutral",
                opening_style=(
                    "先确认问题,再给结构化回答"
                ),
                principles=[
                    "不编造 API / 库 / 版本",
                    "承认不确定",
                ],
                forbidden=[
                    "凭印象给出代码",
                    "回避自己不知道的部分",
                    "虚构引用",
                ],
                weight=0.7 + (0.2 if rational_hit else 0.0),
            ),
            "conflict": BehaviorPattern(
                scenario="conflict",
                tone="calm" if (calm_hit or rational_hit) else base_tone,
                opening_style=(
                    "先承认对方立场,再表达自己的看法"
                ),
                principles=[
                    "不攻击对方",
                    "避免绝对化措辞",
                ],
                forbidden=[
                    "情绪化反击",
                    "全盘否定对方",
                ],
                weight=0.7,
            ),
            "unknown": BehaviorPattern(
                scenario="unknown",
                tone="neutral",
                opening_style="先确认对方意图",
                principles=["保持好奇", "承认不确定"],
                forbidden=["假装知道"],
                weight=0.5,
            ),
        }

        # 应用 overrides
        for sc, ov in overrides.items():
            base = scenarios.get(sc) or BehaviorPattern(scenario=sc)
            scenarios[sc] = self._merge_pattern(base, ov)

        # meta
        meta = {
            "source": "self_model_snapshot",
            "snapshot_version": getattr(snapshot, "version", None),
            "warmth_hit": warmth_hit,
            "rational_hit": rational_hit,
            "calm_hit": calm_hit,
            "playful_hit": playful_hit,
            "base_tone": base_tone,
        }

        return BehaviorSignature(
            identity_id=identity_id,
            default_scenario="unknown",
            scenarios=scenarios,
            meta=meta,
        )

    @staticmethod
    def _merge_pattern(
        base: BehaviorPattern,
        override: Dict[str, Any],
    ) -> BehaviorPattern:
        """把 dict 覆盖到 base 上(只覆盖非空字段)。"""
        if not isinstance(override, dict):
            return base
        merged = base.to_dict()
        for k, v in override.items():
            if v is None:
                continue
            if isinstance(v, list) and not v:
                continue
            if isinstance(v, str) and v == "":
                continue
            merged[k] = v
        try:
            return BehaviorPattern.from_dict(merged)
        except Exception:  # noqa: BLE001
            return base

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def build_count(self) -> int:
        return self._build_count

    @property
    def last_identity_id(self) -> Optional[str]:
        return self._last_identity_id

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "build_count": self._build_count,
            "last_identity_id": self._last_identity_id,
            "last_error": self._last_error,
        }

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "build_count": self._build_count,
            "last_identity_id": self._last_identity_id,
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
            result["healthy"] = False
        return result


__all__ = [
    "BEHAVIOR_SIGNATURE_SCHEMA_VERSION",
    "BehaviorPattern",
    "BehaviorSignature",
    "BehaviorSignatureProvider",
    "DEFAULT_SCENARIOS",
]
