# -*- coding: utf-8 -*-
"""
src/runtime/adapters/impl/personality_runtime_adapter.py

Phase C.4 Personality Runtime Integration —— PersonalityRuntimeAdapter

职责:
  - 实现 Phase C.1 CycleAdapter 协议(duck-type)
  - **只读** Personality 状态:
      resolver._trait_states (浅拷贝 + 安全读取)
      resolver.self_model_store.get() (只读查询)
      resolver.state.get() (只读查询)
      resolver.relationship_state (只读查询)
  - 把结果写 ctx.personality_output (统一格式)
  - 全部 fail-soft

**核心原则 (硬约束)**:
  - 绝对不调 PersonalityResolver.resolve() —— 内部会触发:
      SelfModelStore.update() / evolution_engine.update_trait() /
      personality_history.record_change() / _trait_states 漂移
  - 绝对不调 apply_update()
  - 绝对不调 TraitStateUpdater.apply()
  - 绝对不调 evolution_engine.update_trait()
  - 绝对不调 self_model_store.update()
  - 绝对不调 personality_history.record_change()

不修改:
  - RuntimeCore / RuntimeBridge / RuntimeCycleOrchestrator
  - RuntimeCycleContext (personality_output 字段已存在)
  - PersonalityResolver / PersonalityState / TraitState
  - TraitStateUpdater / SelfModel 系统
  - Growth / Relationship / Memory / Emotion System
  - B.4-B.13

设计原则:
  - 与已有 PersonalityAdapterImpl 并存,只新增文件,不改旧文件
  - PersonalityResolver 缺省时自建,缺省失败返回 degraded
  - 所有异常均 fail-soft
  - 读取时通过浅拷贝 _trait_states 字典 + 纯函数,杜绝副作用
"""
from __future__ import annotations

import copy
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.runtime.cycle_adapter import (
    STANDARD_ADAPTER_PERSONALITY,
)
from src.runtime.cycle_context import RuntimeCycleContext

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本 + 常量
# ============================================================

PERSONALITY_RUNTIME_ADAPTER_SCHEMA_VERSION = "1.0"
PHASE_C4_NAME = "phase_c4"
PHASE_C4_VERSION = "1.0.0"

PHASE_C4_STAGE_PERSONALITY_READ = "phase_c4_personality_read"
PHASE_C4_STAGE_PERSONALITY_DEGRADED = "phase_c4_personality_degraded"

ALL_PHASE_C4_STAGES = (
    PHASE_C4_STAGE_PERSONALITY_READ,
    PHASE_C4_STAGE_PERSONALITY_DEGRADED,
)

SOURCE_NAME = "personality_runtime_adapter"


# ============================================================
# 基础人格维度 (来自 PersonalityProfile.BASE)
# ============================================================

# 6 维核心特质 (固定顺序)
CORE_TRAITS: List[str] = [
    "warmth",
    "gentleness",
    "shyness",
    "sensitivity",
    "emotional_expression",
    "caring",
]

# 派生维度
EXTENDED_TRAITS: List[str] = [
    "dependence",
    "self_identity",
    "self_expression",
    "initiative",
    "care_level",
    "directness",
    "playfulness",
]

# 兜底基础值(当 _trait_states 缺维度时使用)
TRAIT_FALLBACK_VALUES: Dict[str, float] = {
    # core
    "warmth": 0.70,
    "gentleness": 0.80,
    "shyness": 0.75,
    "sensitivity": 0.80,
    "emotional_expression": 0.65,
    "caring": 0.70,
    # extended
    "dependence": 0.5,
    "self_identity": 0.3,
    "self_expression": 0.3,
    "initiative": 0.3,
    "care_level": 0.3,
    "directness": 0.3,
    "playfulness": 0.3,
}


# ============================================================
# PersonalitySnapshot(只读,不可变)
# ============================================================


@dataclass
class _PersonalitySnapshot:
    """Personality 状态轻量快照(只读,不可变)。"""
    traits: Dict[str, float] = field(default_factory=dict)
    attachment_label: str = "初识"
    familiarity_label: str = "怀疑"
    persona_summary: str = ""
    active_tensions: List[Dict[str, Any]] = field(default_factory=list)
    identity_summary: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    schema_version: str = PERSONALITY_RUNTIME_ADAPTER_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 内部工具
# ============================================================


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    try:
        v = float(v)
        if v < lo:
            return lo
        if v > hi:
            return hi
        return v
    except (TypeError, ValueError):
        return lo


def _build_degraded_output(error: str = "") -> Dict[str, Any]:
    """构造降级输出(fail-soft 时使用)。"""
    return {
        "personality_available": False,
        "degraded": True,
        "source": SOURCE_NAME,
        "error": _safe_str(error, "unknown"),
    }


def _get_attachment_label(score: float) -> str:
    """依恋等级标签(纯函数,不依赖 Resolver)。"""
    if score < 0.2:
        return "初识"
    if score < 0.4:
        return "探索"
    if score < 0.6:
        return "靠近"
    if score < 0.8:
        return "依赖"
    return "安全依恋"


def _get_interaction_familiarity_label(score: float) -> str:
    """交互熟悉度标签(纯函数,不依赖 Resolver)。"""
    if score < 0.2:
        return "怀疑"
    if score < 0.4:
        return "试探"
    if score < 0.6:
        return "信任"
    if score < 0.8:
        return "深信"
    return "完全信任"


def _generate_persona_summary(
    warmth: float,
    shyness: float,
    emotional_expression: float,
    self_expression: float,
    initiative: float,
    care_level: float,
) -> str:
    """生成 persona 描述(纯函数,独立实现以避免调用 Resolver)。"""
    parts: List[str] = []

    if warmth >= 0.7:
        parts.append("性格温暖而柔和")
    elif warmth >= 0.5:
        parts.append("待人温和友善")

    if shyness >= 0.7:
        parts.append("内心带有一丝羞怯")
    elif shyness >= 0.5:
        parts.append("偶尔会流露出害羞的一面")

    if emotional_expression >= 0.7:
        parts.append("情绪表达自然流畅")
    elif emotional_expression >= 0.5:
        parts.append("能够自然地表达自己的感受")

    if self_expression >= 0.6:
        parts.append("有自己的想法并愿意表达")

    if initiative >= 0.6 and care_level >= 0.6:
        parts.append("会主动关注对方的表达和状态")
    elif care_level >= 0.6:
        parts.append("会在交流中关注对方的表达和状态")

    if not parts:
        return "羽依正在逐渐认识这个世界和身边的人。"

    return "羽依" + "，".join(parts) + "。"


def _detect_tensions_safe(traits: Dict[str, float]) -> List[Dict[str, Any]]:
    """检测人格矛盾(只读纯函数,失败时返回空列表)。"""
    if not isinstance(traits, dict) or not traits:
        return []
    try:
        from src.personality.personality_tension import detect_tensions
        active = detect_tensions(traits)
        if not isinstance(active, list):
            return []
        # 转 dict(只读,深拷贝)
        out: List[Dict[str, Any]] = []
        for t in active:
            try:
                if isinstance(t, dict):
                    out.append(copy.deepcopy(t))
                else:
                    out.append({
                        "name": _safe_str(getattr(t, "name", ""), ""),
                        "dimensions": list(getattr(t, "dimensions", []) or []),
                        "intensity": _safe_float(getattr(t, "intensity", 0.0)),
                        "description": _safe_str(getattr(t, "description", ""), ""),
                        "active": bool(getattr(t, "active", True)),
                    })
            except Exception:  # noqa: BLE001
                continue
        return out
    except Exception:  # noqa: BLE001
        return []


# ============================================================
# PersonalityRuntimeAdapter(主类)
# ============================================================


class PersonalityRuntimeAdapter:
    """
    Personality 系统 Runtime Cycle Adapter (Phase C.4 / v1.0)

    **Read-Only Adapter** —— 只读取 Personality 状态,绝不修改。

    实现 CycleAdapter 协议(duck-type):
      name             = "personality"
      schema_version   = "1.0"
      attach()         - 接入 PersonalityResolver(可选自建)
      detach()         - 解除
      health_check()   - 健康检查
      process_cycle()  - 一次 cycle 处理
      snapshot()       - 快照

    业务行为:
      - 读 ctx.input_event / ctx.emotion_output / ctx.metadata
      - 读 resolver._trait_states (浅拷贝, 安全)
      - 读 resolver.state / relationship_state (只读查询)
      - 读 resolver.self_model_store.get() (只读)
      - 统一格式写 ctx.personality_output

    全部 fail-soft: Manager 不存在 / state 损坏 / 字段缺失 / 空状态均降级。
    """

    name: str = STANDARD_ADAPTER_PERSONALITY
    schema_version: str = PERSONALITY_RUNTIME_ADAPTER_SCHEMA_VERSION

    def __init__(
        self,
        personality_resolver: Any = None,
        user_id: str = "yuyi",
    ) -> None:
        self._user_id = str(user_id or "yuyi")

        # 外部注入优先
        self._resolver = personality_resolver

        # 内部状态
        self._attached: bool = False
        self._lock = threading.RLock()
        self._process_count: int = 0
        self._read_count: int = 0
        self._degraded_count: int = 0
        self._last_snapshot: Optional[Dict[str, Any]] = None
        self._last_health: Optional[Dict[str, Any]] = None
        self._last_output: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # CycleAdapter 协议
    # --------------------------------------------------------

    def attach(self) -> bool:
        """接入 Personality Resolver(可选自建)。"""
        with self._lock:
            if self._resolver is None:
                try:
                    from src.personality.personality_resolver import (
                        PersonalityResolver,
                    )
                    self._resolver = PersonalityResolver()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[phase_c4] PersonalityResolver 自建失败: {exc}")
                    self._resolver = None
            self._attached = True
        return True

    def detach(self) -> bool:
        with self._lock:
            self._attached = False
        return True

    def is_attached(self) -> bool:
        with self._lock:
            return self._attached

    def health_check(self) -> Dict[str, Any]:
        try:
            with self._lock:
                resolver_available = self._resolver is not None
                trait_states_available = False
                trait_states_error: Optional[str] = None
                if resolver_available:
                    try:
                        ts = getattr(self._resolver, "_trait_states", None)
                        if isinstance(ts, dict):
                            trait_states_available = True
                    except Exception as exc:  # noqa: BLE001
                        trait_states_error = repr(exc)
                status = "healthy" if (
                    resolver_available and trait_states_available
                ) else "degraded"
                result: Dict[str, Any] = {
                    "adapter": STANDARD_ADAPTER_PERSONALITY,
                    "status": status,
                    "resolver_available": bool(resolver_available),
                    "trait_states_available": bool(trait_states_available),
                    "attached": bool(self._attached),
                    "process_count": int(self._process_count),
                    "read_count": int(self._read_count),
                    "degraded_count": int(self._degraded_count),
                    "user_id": str(self._user_id),
                    "schema_version": self.schema_version,
                }
                if trait_states_error:
                    result["trait_states_error"] = trait_states_error
            self._last_health = result
            return result
        except Exception as exc:  # noqa: BLE001
            return {
                "adapter": STANDARD_ADAPTER_PERSONALITY,
                "status": "degraded",
                "error": repr(exc),
            }

    def process_cycle(self, ctx: Any) -> Any:
        """一次 Runtime Cycle 处理(被 orchestrator 调用)。

        流程:
          1) 提取 input_event / emotion_output / metadata
          2) 安全读 resolver._trait_states (浅拷贝)
          3) 安全读 resolver.state / relationship_state (只读查询)
          4) 安全读 resolver.self_model_store.get() (只读)
          5) 构造统一 output 写 ctx.personality_output
          6) 缓存 last_output / last_snapshot
          7) 返回 ctx

        **任何异常都 fail-soft,绝不抛。**
        **绝不调 resolver.resolve() / apply_update() / evolution_engine / self_model_store.update()。**
        """
        with self._lock:
            self._process_count += 1
        try:
            if not isinstance(ctx, RuntimeCycleContext):
                return ctx

            # 读 personality 状态(只读)
            output, snap = self._read_personality_safe()
            ctx.personality_output = output

            # 缓存
            with self._lock:
                self._last_output = output
                self._last_snapshot = snap.to_dict() if snap else None
                if output.get("degraded"):
                    self._degraded_count += 1
                self._last_error = output.get("error")
            return ctx
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c4] process_cycle 异常(已隔离): {exc}")
            with self._lock:
                self._degraded_count += 1
                self._last_error = repr(exc)
            # 写入 degraded output
            try:
                if isinstance(ctx, RuntimeCycleContext):
                    ctx.personality_output = _build_degraded_output(repr(exc))
            except Exception:  # noqa: BLE001
                pass
            return ctx

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "available": bool(
                    self._resolver is not None
                    and not self._is_degraded_snapshot()
                ),
                "last_state": dict(self._last_snapshot or {}),
                "process_count": int(self._process_count),
                "read_count": int(self._read_count),
                "degraded_count": int(self._degraded_count),
                "attached": bool(self._attached),
                "resolver_available": bool(self._resolver is not None),
                "user_id": str(self._user_id),
                "last_error": self._last_error,
            }

    # --------------------------------------------------------
    # 业务:读取 personality 状态(只读,绝无副作用)
    # --------------------------------------------------------

    def read_personality(self) -> Dict[str, Any]:
        """公开读接口(供 C.1 之外调用)。

        Returns:
            统一格式的 personality_output dict
        """
        with self._lock:
            self._read_count += 1
        output, snap = self._read_personality_safe()
        with self._lock:
            self._last_output = output
            self._last_snapshot = snap.to_dict() if snap else None
            if output.get("degraded"):
                self._degraded_count += 1
        return output

    def _read_personality_safe(self) -> tuple:
        """安全读 personality 状态,返回 (output_dict, snapshot)。

        **任何异常都返回 degraded output + 空 snapshot。**
        **绝不允许调用 resolver.resolve() / apply_update() / 任何会修改状态的接口。**
        """
        # 1) Resolver 不存在 → degraded
        if self._resolver is None:
            return (
                _build_degraded_output("resolver_not_attached"),
                _PersonalitySnapshot(),
            )

        # 2) Resolver 缺 _trait_states → degraded
        try:
            trait_states = getattr(self._resolver, "_trait_states", None)
        except Exception as exc:  # noqa: BLE001
            return (
                _build_degraded_output(f"trait_states_attr_error: {exc!r}"),
                _PersonalitySnapshot(),
            )
        if not isinstance(trait_states, dict):
            return (
                _build_degraded_output("trait_states_not_found"),
                _PersonalitySnapshot(),
            )

        # 3) 读取核心 6 维 + 派生维度(浅拷贝,绝不在原 dict 上写)
        current_traits: Dict[str, float] = {}
        for dim in CORE_TRAITS:
            raw = trait_states.get(dim)
            if isinstance(raw, dict):
                v = _safe_float(raw.get("current_value"), TRAIT_FALLBACK_VALUES[dim])
            else:
                v = TRAIT_FALLBACK_VALUES[dim]
            current_traits[dim] = _clamp(v, 0.0, 1.0)

        # 4) 派生维度(从 GrowthState metrics 推算,与 Resolver 公式保持一致)
        extended_traits: Dict[str, float] = self._compute_extended_traits()

        # 5) labels(纯函数)
        attachment_score = self._get_attachment_score()
        familiarity_score = self._get_familiarity_score()
        labels = {
            "attachment_level": _get_attachment_label(attachment_score),
            "interaction_familiarity_level": _get_interaction_familiarity_label(
                familiarity_score
            ),
        }

        # 6) persona_summary(纯函数)
        persona_summary = _generate_persona_summary(
            warmth=current_traits.get("warmth", 0.5),
            shyness=current_traits.get("shyness", 0.5),
            emotional_expression=current_traits.get("emotional_expression", 0.5),
            self_expression=extended_traits.get("self_expression", 0.3),
            initiative=extended_traits.get("initiative", 0.3),
            care_level=extended_traits.get("care_level", 0.3),
        )

        # 7) active_tensions(纯函数,不触发 Resolver 副作用)
        all_traits = dict(current_traits)
        all_traits.update(extended_traits)
        active_tensions = _detect_tensions_safe(all_traits)

        # 8) identity_summary(只读查询, 不调 store.update)
        identity_summary = self._read_identity_summary_safe()

        # 9) 构造 output
        output: Dict[str, Any] = {
            "personality_available": True,
            "current_traits": current_traits,
            "extended_traits": extended_traits,
            "labels": labels,
            "persona_summary": persona_summary,
            "active_tensions": active_tensions,
            "identity_summary": identity_summary,
            "source": SOURCE_NAME,
            "degraded": False,
        }

        # 10) 构造 snapshot(缓存)
        snap = _PersonalitySnapshot(
            traits=dict(current_traits, **extended_traits),
            attachment_label=labels["attachment_level"],
            familiarity_label=labels["interaction_familiarity_level"],
            persona_summary=persona_summary,
            active_tensions=list(active_tensions),
            identity_summary=identity_summary,
        )

        return output, snap

    def _compute_extended_traits(self) -> Dict[str, float]:
        """从 GrowthState metrics 推算派生维度(纯函数,不调 Resolver)。"""
        # 缺省兜底
        ext = {
            "dependence": 0.5,
            "self_identity": 0.3,
            "self_expression": 0.3,
            "initiative": 0.3,
            "care_level": 0.3,
            "directness": 0.3,
            "playfulness": 0.3,
        }
        try:
            state = getattr(self._resolver, "state", None)
            metrics: Dict[str, Any] = {}
            if state is not None:
                try:
                    data = state.get()  # GrowthState.get() 纯查询
                    if isinstance(data, dict):
                        metrics = dict(data.get("metrics", {}) or {})
                except Exception:  # noqa: BLE001
                    metrics = {}

            # bond
            rel_bond = 0.0
            rel_familiarity = 0.0
            rel_trust = 0.0
            try:
                rel_state = getattr(self._resolver, "relationship_state", None)
                if rel_state is not None:
                    try:
                        rel_bond = _safe_float(rel_state.get_bond_strength())
                    except Exception:  # noqa: BLE001
                        rel_bond = 0.0
                    try:
                        rel_familiarity = _safe_float(rel_state.get_familiarity())
                    except Exception:  # noqa: BLE001
                        rel_familiarity = 0.0
                    try:
                        rel_trust = _safe_float(rel_state.get_trust())
                    except Exception:  # noqa: BLE001
                        rel_trust = 0.0
            except Exception:  # noqa: BLE001
                pass

            # 与 Resolver.resolve() 中的公式对齐
            growth_closeness = _safe_float(metrics.get("closeness", 0))
            growth_trust = _safe_float(metrics.get("trust", 0))
            growth_security = _safe_float(metrics.get("security", 0))
            growth_awareness = _safe_float(metrics.get("self_awareness", 0))
            growth_confidence = _safe_float(metrics.get("self_confidence", 0))
            growth_attachment = _safe_float(metrics.get("attachment", 0))
            growth_identity_strength = _safe_float(
                metrics.get("identity_strength", 0)
            )

            ext["dependence"] = _clamp(
                0.5
                + growth_attachment * 0.3
                + growth_closeness * 0.1
                + rel_bond * 0.15,
                0.0,
                1.0,
            )
            ext["self_identity"] = _clamp(
                growth_identity_strength + growth_awareness * 0.5, 0.0, 1.0
            )
            ext["self_expression"] = _clamp(
                0.2
                + growth_awareness * 0.3
                + growth_confidence * 0.3
                + growth_identity_strength * 0.2
                + rel_trust * 0.1,
                0.0,
                1.0,
            )
            ext["initiative"] = _clamp(
                0.25
                + growth_confidence * 0.3
                + growth_identity_strength * 0.15
                + rel_familiarity * 0.1,
                0.0,
                1.0,
            )
            ext["care_level"] = _clamp(
                0.25
                + growth_closeness * 0.4
                + rel_bond * 0.2,
                0.0,
                1.0,
            )
            ext["directness"] = _clamp(
                0.25
                + growth_confidence * 0.4
                + rel_familiarity * 0.1,
                0.0,
                1.0,
            )
            ext["playfulness"] = _clamp(
                0.2
                + growth_closeness * 0.3
                + growth_security * 0.2
                + rel_familiarity * 0.15,
                0.0,
                1.0,
            )
        except Exception:  # noqa: BLE001
            pass
        return ext

    def _get_attachment_score(self) -> float:
        """读取 attachment 分数(只读)。"""
        try:
            state = getattr(self._resolver, "state", None)
            if state is None:
                return 0.0
            data = state.get()
            if not isinstance(data, dict):
                return 0.0
            metrics = data.get("metrics", {})
            if not isinstance(metrics, dict):
                return 0.0
            return _safe_float(metrics.get("attachment", 0.0))
        except Exception:  # noqa: BLE001
            return 0.0

    def _get_familiarity_score(self) -> float:
        """读取 familiarity 分数(只读)。"""
        try:
            growth_trust = 0.0
            rel_trust = 0.0
            state = getattr(self._resolver, "state", None)
            if state is not None:
                try:
                    data = state.get()
                    if isinstance(data, dict):
                        metrics = data.get("metrics", {})
                        if isinstance(metrics, dict):
                            growth_trust = _safe_float(metrics.get("trust", 0.0))
                except Exception:  # noqa: BLE001
                    growth_trust = 0.0
            rel_state = getattr(self._resolver, "relationship_state", None)
            if rel_state is not None:
                try:
                    rel_trust = _safe_float(rel_state.get_trust())
                except Exception:  # noqa: BLE001
                    rel_trust = 0.0
            return _clamp(growth_trust * 0.4 + rel_trust * 0.6, 0.0, 1.0)
        except Exception:  # noqa: BLE001
            return 0.0

    def _read_identity_summary_safe(self) -> str:
        """读 self_model.identity_summary(只读查询,绝不调 store.update)。"""
        try:
            store = getattr(self._resolver, "self_model_store", None)
            if store is None:
                return ""
            get_fn = getattr(store, "get", None)
            if not callable(get_fn):
                return ""
            sm = get_fn()
            if not isinstance(sm, dict):
                return ""
            return _safe_str(sm.get("identity_summary", ""), "")
        except Exception:  # noqa: BLE001
            return ""

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------

    def _is_degraded_snapshot(self) -> bool:
        """判断最近一次 read 是否降级。"""
        if not self._last_output:
            return True
        return bool(self._last_output.get("degraded"))

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------

    @property
    def last_output(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._last_output) if self._last_output else None

    @property
    def last_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._last_snapshot) if self._last_snapshot else None

    @property
    def process_count(self) -> int:
        with self._lock:
            return self._process_count

    @property
    def read_count(self) -> int:
        with self._lock:
            return self._read_count

    @property
    def degraded_count(self) -> int:
        with self._lock:
            return self._degraded_count

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error


# ============================================================
# 工厂
# ============================================================


def create_personality_runtime_adapter(
    personality_resolver: Any = None,
    user_id: str = "yuyi",
) -> PersonalityRuntimeAdapter:
    """工厂函数:创建一个 PersonalityRuntimeAdapter(只读)。"""
    return PersonalityRuntimeAdapter(
        personality_resolver=personality_resolver,
        user_id=user_id,
    )


def safe_get_personality_adapter_summary(adapter: Any) -> Dict[str, Any]:
    """全局安全 summary。"""
    empty = {
        "name": STANDARD_ADAPTER_PERSONALITY,
        "schema_version": PERSONALITY_RUNTIME_ADAPTER_SCHEMA_VERSION,
        "available": False,
        "attached": False,
    }
    if adapter is None:
        return empty
    try:
        return adapter.snapshot() or empty
    except Exception:  # noqa: BLE001
        return empty


# ============================================================
# 公共 API
# ============================================================


__all__ = [
    "PERSONALITY_RUNTIME_ADAPTER_SCHEMA_VERSION",
    "PHASE_C4_NAME",
    "PHASE_C4_VERSION",
    "PHASE_C4_STAGE_PERSONALITY_READ",
    "PHASE_C4_STAGE_PERSONALITY_DEGRADED",
    "ALL_PHASE_C4_STAGES",
    "SOURCE_NAME",
    "CORE_TRAITS",
    "EXTENDED_TRAITS",
    "TRAIT_FALLBACK_VALUES",
    "_PersonalitySnapshot",
    "PersonalityRuntimeAdapter",
    "create_personality_runtime_adapter",
    "safe_get_personality_adapter_summary",
]
