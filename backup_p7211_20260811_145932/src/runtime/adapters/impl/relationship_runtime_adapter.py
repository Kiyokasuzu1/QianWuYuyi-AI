# -*- coding: utf-8 -*-
"""
src/runtime/adapters/impl/relationship_runtime_adapter.py

Phase C.5 Relationship Runtime Integration —— RelationshipRuntimeAdapter

职责:
  - 实现 Phase C.1 CycleAdapter 协议(duck-type)
  - **只读** Relationship 状态:
      relationship_state (RelationshipState,属性只读)
      relationship_model (RelationshipModel,只读查询)
      relationship_repository (Repository,只调 load_*)
  - 把结果写 ctx.relationship_output (统一格式)
  - 全部 fail-soft

**核心原则 (硬约束)**:
  - 绝对不调 RelationshipIntelligenceEngine.process_interaction() —— 内部会触发:
      state.familiarity/trust/collaboration += delta
      state.relationship_stage = ...
      model.interaction_history.append(...)
      model.trust_changes.append(...)
      model.emotional_patterns.append(...)
      model.shared_experiences.append(...)
      model.relationship_milestones.append(...)
  - 绝对不调 RelationshipRepository.save_*() (写盘)
  - 绝对不调 state.familiarity/trust/collaboration = X (赋值)
  - 绝对不调 model list append/extend
  - 绝对不调 PersonalityResolver.resolve()
  - 绝对不触发 Growth / SelfModel / PersonalityEvolution 任何写入

不修改:
  - RuntimeCore / RuntimeBridge / RuntimeCycleOrchestrator
  - RuntimeCycleContext (relationship_output 字段已存在)
  - cycle_adapter.py / cycle_event.py / cycle_context.py
  - Relationship System 任何核心文件 (src/relationship/**)
  - Memory / Emotion / Personality / Growth 系统
  - B.4-B.13

设计原则:
  - 与已有 relationship adapter (integration/tasks) 共存,只新增文件,不改旧文件
  - 外部注入优先,缺省时尝试自建 (失败也无害,返回 degraded)
  - 所有异常均 fail-soft
  - 读取时通过 dict() / list() 浅拷贝,杜绝副作用
"""
from __future__ import annotations

import copy
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.runtime.cycle_adapter import (
    STANDARD_ADAPTER_RELATIONSHIP,
)
from src.runtime.cycle_context import RuntimeCycleContext

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本 + 常量
# ============================================================

RELATIONSHIP_RUNTIME_ADAPTER_SCHEMA_VERSION = "1.0"
PHASE_C5_NAME = "phase_c5"
PHASE_C5_VERSION = "1.0.0"

PHASE_C5_STAGE_RELATIONSHIP_READ = "phase_c5_relationship_read"
PHASE_C5_STAGE_RELATIONSHIP_DEGRADED = "phase_c5_relationship_degraded"

ALL_PHASE_C5_STAGES = (
    PHASE_C5_STAGE_RELATIONSHIP_READ,
    PHASE_C5_STAGE_RELATIONSHIP_DEGRADED,
)

SOURCE_NAME = "relationship_runtime_adapter"


# ============================================================
# 关系阶段标签
# ============================================================

STAGE_LABEL_MAP: Dict[str, str] = {
    "initial": "初步接触",
    "developing": "正在发展",
    "stable": "稳定互动",
    "deep_collaboration": "深度协作",
}


# ============================================================
# RelationshipSnapshot(只读,不可变)
# ============================================================


@dataclass
class _RelationshipSnapshot:
    """Relationship 状态轻量快照(只读,不可变)。"""
    state: Dict[str, Any] = field(default_factory=dict)
    model_snapshot: Dict[str, Any] = field(default_factory=dict)
    stage_label: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    schema_version: str = RELATIONSHIP_RUNTIME_ADAPTER_SCHEMA_VERSION

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


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
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


def _safe_copy_list(src: Any, max_items: int = 5) -> List[Any]:
    """安全复制 list-like(只读前 max_items 项,返回新 list)。"""
    if src is None:
        return []
    if not isinstance(src, (list, tuple)):
        return []
    try:
        # 浅拷贝:任何 dict 也只 copy dict,不再深递归
        out: List[Any] = []
        for i, item in enumerate(src):
            if i >= max_items:
                break
            if isinstance(item, dict):
                out.append(dict(item))
            elif isinstance(item, (list, tuple)):
                out.append(list(item))
            elif isinstance(item, (str, int, float, bool)) or item is None:
                out.append(item)
            else:
                # 未知类型,转 repr
                out.append(_safe_str(repr(item), ""))
        return out
    except Exception:  # noqa: BLE001
        return []


def _build_degraded_output(error: str = "") -> Dict[str, Any]:
    """构造降级输出(fail-soft 时使用)。"""
    return {
        "relationship_available": False,
        "degraded": True,
        "source": SOURCE_NAME,
        "error": _safe_str(error, "unknown"),
    }


def _get_familiarity_label(score: float) -> str:
    """熟悉度标签(纯函数,不依赖 Relationship System)。"""
    if score < 0.2:
        return "陌生"
    if score < 0.4:
        return "初识"
    if score < 0.6:
        return "熟悉中"
    if score < 0.8:
        return "很熟悉"
    return "非常熟悉"


def _get_trust_label(score: float) -> str:
    """信任度标签(纯函数)。"""
    if score < 0.2:
        return "陌生"
    if score < 0.4:
        return "试探"
    if score < 0.6:
        return "初步信任"
    if score < 0.8:
        return "信任"
    return "深度信任"


def _get_collaboration_label(score: float) -> str:
    """协作度标签(纯函数)。"""
    if score < 0.2:
        return "尚未协作"
    if score < 0.4:
        return "偶尔协作"
    if score < 0.6:
        return "尝试协作"
    if score < 0.8:
        return "稳定协作"
    return "深度协作"


def _get_interaction_frequency_label(score: float) -> str:
    """互动频率标签(纯函数)。"""
    if score < 0.2:
        return "很少互动"
    if score < 0.4:
        return "偶尔互动"
    if score < 0.6:
        return "经常互动"
    if score < 0.8:
        return "频繁互动"
    return "持续互动"


def _generate_relationship_summary(
    stage: str,
    stage_label: str,
    familiarity: float,
    trust: float,
    collaboration: float,
    total_interactions: int,
) -> str:
    """生成关系描述(纯函数,独立实现以避免调用 Resolver/Engine)。"""
    parts: List[str] = []

    # 阶段描述
    if stage_label:
        parts.append(f"羽依与用户的关系处于{stage_label}阶段")

    # 维度描述
    if familiarity >= 0.6:
        parts.append("彼此已经比较熟悉")
    elif familiarity >= 0.3:
        parts.append("正在逐渐了解对方")
    else:
        parts.append("彼此仍在建立初步认识")

    if trust >= 0.6:
        parts.append("建立了较深的信任")
    elif trust >= 0.3:
        parts.append("正在建立信任感")
    else:
        parts.append("信任感尚在建立中")

    if collaboration >= 0.6:
        parts.append("已形成稳定协作")
    elif collaboration >= 0.3:
        parts.append("开始尝试协作")

    # 互动次数
    if total_interactions > 0:
        parts.append(f"目前累计互动 {total_interactions} 次")

    if not parts:
        return "羽依与用户的关系正在自然形成。"

    return "，" + "，".join(parts[1:]) + "。" if len(parts) > 1 else parts[0] + "。"


def _boundary_check_safe(text: str) -> str:
    """关系边界安全检查(纯函数;命中违规时返回空字符串)。"""
    if not text:
        return ""
    try:
        from src.relationship.relationship_boundary import (
            RelationshipBoundary,
            BoundaryLevel,
        )
        result = RelationshipBoundary().check_expression(text)
        if result.level != BoundaryLevel.SAFE:
            return ""
    except Exception:  # noqa: BLE001
        pass
    return text


# ============================================================
# RelationshipRuntimeAdapter(主类)
# ============================================================


class RelationshipRuntimeAdapter:
    """
    Relationship 系统 Runtime Cycle Adapter (Phase C.5 / v1.0)

    **Read-Only Adapter** —— 只读取 Relationship 状态,绝不修改。

    实现 CycleAdapter 协议(duck-type):
      name             = "relationship"
      schema_version   = "1.0"
      attach()         - 接入 Relationship(可选自建)
      detach()         - 解除
      health_check()   - 健康检查
      process_cycle()  - 一次 cycle 处理
      snapshot()       - 快照

    业务行为:
      - 读 ctx.input_event / ctx.personality_output / ctx.metadata
      - 读 relationship_state 属性 (只读)
      - 读 relationship_model.get_snapshot() (只读)
      - 读 relationship_repository.load_*() (只读)
      - 统一格式写 ctx.relationship_output

    全部 fail-soft: State 不存在 / Model 损坏 / 字段缺失 / 空状态均降级。
    """

    name: str = STANDARD_ADAPTER_RELATIONSHIP
    schema_version: str = RELATIONSHIP_RUNTIME_ADAPTER_SCHEMA_VERSION

    def __init__(
        self,
        relationship_state: Any = None,
        relationship_model: Any = None,
        relationship_repository: Any = None,
        user_id: str = "yuyi",
    ) -> None:
        self._user_id = str(user_id or "yuyi")

        # 外部注入优先
        self._state = relationship_state
        self._model = relationship_model
        self._repository = relationship_repository

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
        """接入 Relationship(可选自建)。"""
        with self._lock:
            if self._state is None or self._model is None:
                try:
                    # 尝试通过 repository 自建
                    repo = self._repository
                    if repo is None:
                        try:
                            from src.relationship.relationship_repository import (
                                RelationshipRepository,
                            )
                            repo = RelationshipRepository(user_id=self._user_id)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                f"[phase_c5] RelationshipRepository 自建失败: {exc}"
                            )
                            repo = None

                    if repo is not None:
                        if self._state is None:
                            try:
                                self._state = repo.load_state()
                            except Exception as exc:  # noqa: BLE001
                                logger.debug(
                                    f"[phase_c5] load_state 失败: {exc}"
                                )
                                self._state = None
                        if self._model is None:
                            try:
                                self._model = repo.load_relationship_model()
                            except Exception as exc:  # noqa: BLE001
                                logger.debug(
                                    f"[phase_c5] load_relationship_model 失败: {exc}"
                                )
                                self._model = None
                        self._repository = repo
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[phase_c5] attach 自建失败(已隔离): {exc}")
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
                state_available = self._state is not None
                model_available = self._model is not None
                repository_available = self._repository is not None
                # 检查 state 是否可读
                state_readable = False
                if state_available:
                    try:
                        _ = self._state.relationship_stage
                        state_readable = True
                    except Exception:  # noqa: BLE001
                        state_readable = False
                status = (
                    "healthy"
                    if (state_available and state_readable)
                    else "degraded"
                )
                result: Dict[str, Any] = {
                    "adapter": STANDARD_ADAPTER_RELATIONSHIP,
                    "status": status,
                    "state_available": bool(state_available),
                    "model_available": bool(model_available),
                    "repository_available": bool(repository_available),
                    "state_readable": bool(state_readable),
                    "attached": bool(self._attached),
                    "process_count": int(self._process_count),
                    "read_count": int(self._read_count),
                    "degraded_count": int(self._degraded_count),
                    "user_id": str(self._user_id),
                    "schema_version": self.schema_version,
                }
            self._last_health = result
            return result
        except Exception as exc:  # noqa: BLE001
            return {
                "adapter": STANDARD_ADAPTER_RELATIONSHIP,
                "status": "degraded",
                "error": repr(exc),
            }

    def process_cycle(self, ctx: Any) -> Any:
        """一次 Runtime Cycle 处理(被 orchestrator 调用)。

        流程:
          1) 提取 input_event / personality_output / metadata
          2) 安全读 relationship_state 属性 (只读)
          3) 安全读 relationship_model.get_snapshot() (只读)
          4) 安全读 relationship_repository.load_*() (只读)
          5) 构造统一 output 写 ctx.relationship_output
          6) 缓存 last_output / last_snapshot
          7) 返回 ctx

        **任何异常都 fail-soft,绝不抛。**
        **绝不调 process_interaction() / save_*() / 任何会修改状态的接口。**
        """
        with self._lock:
            self._process_count += 1
        try:
            if not isinstance(ctx, RuntimeCycleContext):
                return ctx

            # 读 relationship 状态(只读)
            output, snap = self._read_relationship_safe()
            ctx.relationship_output = output

            # 缓存
            with self._lock:
                self._last_output = output
                self._last_snapshot = snap.to_dict() if snap else None
                if output.get("degraded"):
                    self._degraded_count += 1
                self._last_error = output.get("error")
            return ctx
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c5] process_cycle 异常(已隔离): {exc}")
            with self._lock:
                self._degraded_count += 1
                self._last_error = repr(exc)
            # 写入 degraded output
            try:
                if isinstance(ctx, RuntimeCycleContext):
                    ctx.relationship_output = _build_degraded_output(repr(exc))
            except Exception:  # noqa: BLE001
                pass
            return ctx

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "available": bool(
                    self._state is not None
                    and not self._is_degraded_snapshot()
                ),
                "last_state": dict(self._last_snapshot or {}),
                "process_count": int(self._process_count),
                "read_count": int(self._read_count),
                "degraded_count": int(self._degraded_count),
                "attached": bool(self._attached),
                "state_available": bool(self._state is not None),
                "model_available": bool(self._model is not None),
                "repository_available": bool(self._repository is not None),
                "user_id": str(self._user_id),
                "last_error": self._last_error,
            }

    # --------------------------------------------------------
    # 业务:读取 relationship 状态(只读,绝无副作用)
    # --------------------------------------------------------

    def read_relationship(self) -> Dict[str, Any]:
        """公开读接口(供 C.1 之外调用)。

        Returns:
            统一格式的 relationship_output dict
        """
        with self._lock:
            self._read_count += 1
        output, snap = self._read_relationship_safe()
        with self._lock:
            self._last_output = output
            self._last_snapshot = snap.to_dict() if snap else None
            if output.get("degraded"):
                self._degraded_count += 1
        return output

    def _read_relationship_safe(self) -> tuple:
        """安全读 relationship 状态,返回 (output_dict, snapshot)。

        **任何异常都返回 degraded output + 空 snapshot。**
        **绝不允许调用 process_interaction() / save_*() / 任何会修改状态的接口。**
        """
        # 1) State 不存在 → degraded
        if self._state is None:
            return (
                _build_degraded_output("state_not_attached"),
                _RelationshipSnapshot(),
            )

        # 2) 读取 current_metrics(纯属性访问)
        try:
            familiarity = _clamp(
                _safe_float(getattr(self._state, "familiarity", 0.0)),
                0.0, 1.0,
            )
            trust = _clamp(
                _safe_float(getattr(self._state, "trust", 0.0)),
                0.0, 1.0,
            )
            collaboration = _clamp(
                _safe_float(getattr(self._state, "collaboration", 0.0)),
                0.0, 1.0,
            )
            interaction_frequency = _clamp(
                _safe_float(getattr(self._state, "interaction_frequency", 0.0)),
                0.0, 1.0,
            )
            stage = _safe_str(getattr(self._state, "relationship_stage", "initial"), "initial")
            last_interaction_at = _safe_str(
                getattr(self._state, "last_interaction_at", ""), "",
            )
            communication_style = _safe_copy_list(
                getattr(self._state, "communication_style", None),
                max_items=20,
            )
        except Exception as exc:  # noqa: BLE001
            return (
                _build_degraded_output(f"state_attr_error: {exc!r}"),
                _RelationshipSnapshot(),
            )

        # 3) 读取 model snapshot(只读查询)
        model_snapshot: Dict[str, Any] = {}
        total_interactions: int = 0
        trust_changes_total: int = 0
        trust_changes_recent: List[Any] = []
        interaction_history_recent: List[Any] = []
        milestones_count: int = 0
        shared_experiences_count: int = 0
        if self._model is not None:
            try:
                get_snap_fn = getattr(self._model, "get_snapshot", None)
                if callable(get_snap_fn):
                    snap = get_snap_fn()
                    if isinstance(snap, dict):
                        model_snapshot = dict(snap)
                        total_interactions = _safe_int(snap.get("total_interactions", 0), 0)
                        trust_changes_total = _safe_int(
                            snap.get("total_trust_changes", 0), 0,
                        )
                        milestones_count = _safe_int(
                            snap.get("total_milestones", 0), 0,
                        )
                        shared_experiences_count = _safe_int(
                            snap.get("total_shared_experiences", 0), 0,
                        )
            except Exception:  # noqa: BLE001
                model_snapshot = {}

            # 信任变化记录(只读拷贝)
            try:
                trust_changes_src = getattr(self._model, "trust_changes", None)
                trust_changes_recent = _safe_copy_list(
                    trust_changes_src, max_items=5,
                )
            except Exception:  # noqa: BLE001
                trust_changes_recent = []

            # 互动历史(只读拷贝)
            try:
                interaction_history_src = getattr(
                    self._model, "interaction_history", None,
                )
                # 取最近 5 条
                if isinstance(interaction_history_src, list):
                    tail = interaction_history_src[-5:] if len(interaction_history_src) > 5 else interaction_history_src
                    interaction_history_recent = _safe_copy_list(tail, max_items=5)
                else:
                    interaction_history_recent = []
            except Exception:  # noqa: BLE001
                interaction_history_recent = []

        # 4) labels(纯函数)
        stage_label = STAGE_LABEL_MAP.get(stage, stage or "未定义")
        labels = {
            "familiarity_level": _get_familiarity_label(familiarity),
            "trust_level": _get_trust_label(trust),
            "collaboration_level": _get_collaboration_label(collaboration),
            "interaction_frequency_level": _get_interaction_frequency_label(
                interaction_frequency
            ),
            "stage_label": stage_label,
        }

        # 5) current_metrics(新 dict, 绝不可返回原对象)
        current_metrics: Dict[str, Any] = {
            "familiarity": float(familiarity),
            "trust": float(trust),
            "collaboration": float(collaboration),
            "interaction_frequency": float(interaction_frequency),
        }

        # 6) interaction_history 摘要
        interaction_history_summary: Dict[str, Any] = {
            "total_count": int(total_interactions),
            "recent": list(interaction_history_recent),
        }

        # 7) trust_changes 摘要
        trust_changes_summary: Dict[str, Any] = {
            "total_count": int(trust_changes_total),
            "recent": list(trust_changes_recent),
        }

        # 8) relationship_summary(纯函数 + boundary 检查)
        raw_summary = _generate_relationship_summary(
            stage=stage,
            stage_label=stage_label,
            familiarity=familiarity,
            trust=trust,
            collaboration=collaboration,
            total_interactions=total_interactions,
        )
        relationship_summary = _boundary_check_safe(raw_summary)

        # 9) 构造 output
        output: Dict[str, Any] = {
            "relationship_available": True,
            "current_metrics": current_metrics,
            "stage": str(stage),
            "stage_label": stage_label,
            "communication_style": list(communication_style),
            "labels": labels,
            "interaction_history": interaction_history_summary,
            "trust_changes": trust_changes_summary,
            "milestones_count": int(milestones_count),
            "shared_experiences_count": int(shared_experiences_count),
            "last_interaction_at": str(last_interaction_at),
            "relationship_summary": str(relationship_summary),
            "source": SOURCE_NAME,
            "degraded": False,
        }

        # 10) 构造 snapshot(缓存)
        snap = _RelationshipSnapshot(
            state=dict(current_metrics),
            model_snapshot=dict(model_snapshot),
            stage_label=stage_label,
        )

        return output, snap

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


def create_relationship_runtime_adapter(
    relationship_state: Any = None,
    relationship_model: Any = None,
    relationship_repository: Any = None,
    user_id: str = "yuyi",
) -> RelationshipRuntimeAdapter:
    """工厂函数:创建一个 RelationshipRuntimeAdapter(只读)。"""
    return RelationshipRuntimeAdapter(
        relationship_state=relationship_state,
        relationship_model=relationship_model,
        relationship_repository=relationship_repository,
        user_id=user_id,
    )


def safe_get_relationship_adapter_summary(adapter: Any) -> Dict[str, Any]:
    """全局安全 summary。"""
    empty = {
        "name": STANDARD_ADAPTER_RELATIONSHIP,
        "schema_version": RELATIONSHIP_RUNTIME_ADAPTER_SCHEMA_VERSION,
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
    "RELATIONSHIP_RUNTIME_ADAPTER_SCHEMA_VERSION",
    "PHASE_C5_NAME",
    "PHASE_C5_VERSION",
    "PHASE_C5_STAGE_RELATIONSHIP_READ",
    "PHASE_C5_STAGE_RELATIONSHIP_DEGRADED",
    "ALL_PHASE_C5_STAGES",
    "SOURCE_NAME",
    "STAGE_LABEL_MAP",
    "_RelationshipSnapshot",
    "RelationshipRuntimeAdapter",
    "create_relationship_runtime_adapter",
    "safe_get_relationship_adapter_summary",
]
