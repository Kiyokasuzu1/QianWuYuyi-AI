# -*- coding: utf-8 -*-
"""
src/runtime/adapters/impl/growth_runtime_adapter.py

Phase C.6 Growth Runtime Integration —— GrowthRuntimeAdapter

职责:
  - 实现 Phase C.1 CycleAdapter 协议(duck-type)
  - **只读** Growth 状态:
      growth_state.get() / get_metric() (只读查询,深拷贝)
      growth_engine.get_state() / apply_evaluated() (只读)
      growth_evaluator.evaluate() (只读)
      proposal_store.list() / get() (只读)
  - 把结果写 ctx.growth_output (append 一个统一格式 dict)
  - 全部 fail-soft

**核心原则 (硬约束)**:
  - 绝对不调 GrowthEngine.apply() / apply_batch() / reset()
  - 绝对不调 GrowthState.save() / update_metrics() / add_*() / set_*() / mark_*() / reset()
  - 绝对不调 ProposalStore.save() / update() / delete()
  - 绝对不调 ProposalManager.create_proposal() / accept_proposal() /
    reject_proposal() / apply_proposal()
  - 绝对不调 PersonalityAdapter.apply_proposal() / PersonalityResolver.resolve()
  - 绝对不调 self_model_store.update() / evolution_engine.update_trait()

不修改:
  - RuntimeCore / RuntimeBridge / RuntimeCycleOrchestrator
  - RuntimeCycleContext (growth_output 字段已存在)
  - src/growth/ 下任何文件
  - src/personality/ 下任何文件
  - src/runtime/self_model/ 下任何文件
  - src/runtime/adapters/impl/growth_adapter_impl.py (旧实现,不动)

设计原则:
  - 与已有 GrowthAdapterImpl 并存,只新增文件,不改旧文件
  - GrowthState / ProposalStore 缺省时自建,缺省失败返回 degraded
  - 所有异常均 fail-soft
  - 读取时通过 deep copy 复制,杜绝任何对内部状态的修改
"""
from __future__ import annotations

import copy
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.runtime.cycle_adapter import (
    STANDARD_ADAPTER_GROWTH,
)
from src.runtime.cycle_context import RuntimeCycleContext

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本 + 常量
# ============================================================

GROWTH_RUNTIME_ADAPTER_SCHEMA_VERSION = "1.0"
PHASE_C6_NAME = "phase_c6"
PHASE_C6_VERSION = "1.0.0"

PHASE_C6_STAGE_GROWTH_READ = "phase_c6_growth_read"
PHASE_C6_STAGE_GROWTH_DEGRADED = "phase_c6_growth_degraded"

ALL_PHASE_C6_STAGES = (
    PHASE_C6_STAGE_GROWTH_READ,
    PHASE_C6_STAGE_GROWTH_DEGRADED,
)

SOURCE_NAME = "growth_runtime_adapter"

# recent_records 最多返回数量(避免 ctx 过大)
DEFAULT_RECENT_RECORDS_LIMIT = 10
# pending_proposals 最多返回数量
DEFAULT_PENDING_PROPOSALS_LIMIT = 20
# milestones 最多返回数量
DEFAULT_MILESTONES_LIMIT = 20
# growth_history 最多返回数量
DEFAULT_GROWTH_HISTORY_LIMIT = 10


# ============================================================
# GrowthOutputSnapshot(只读,不可变)
# ============================================================


@dataclass
class _GrowthOutputSnapshot:
    """Growth 状态轻量快照(只读,不可变)。"""
    metrics: Dict[str, float] = field(default_factory=dict)
    behaviors: Dict[str, bool] = field(default_factory=dict)
    identities: List[str] = field(default_factory=list)
    milestones: List[Dict[str, Any]] = field(default_factory=list)
    recent_records: List[Dict[str, Any]] = field(default_factory=list)
    growth_history: List[Dict[str, Any]] = field(default_factory=list)
    pending_proposals: List[Dict[str, Any]] = field(default_factory=list)
    proposal_stats: Dict[str, int] = field(default_factory=dict)
    growth_signals: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    schema_version: str = GROWTH_RUNTIME_ADAPTER_SCHEMA_VERSION

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


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


def _now_iso() -> str:
    try:
        return datetime.utcnow().isoformat() + "Z"
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.now().isoformat()


def _safe_deepcopy(v: Any) -> Any:
    """深拷贝(任何异常都返回原值的浅拷贝或空容器)。"""
    try:
        return copy.deepcopy(v)
    except Exception:
        # 兜底:返回原值或空容器
        if isinstance(v, dict):
            return {}
        if isinstance(v, list):
            return []
        return v


def _build_degraded_output(error: str = "") -> Dict[str, Any]:
    """构造降级输出(fail-soft 时使用)。"""
    return {
        "growth_available": False,
        "degraded": True,
        "source": SOURCE_NAME,
        "schema_version": GROWTH_RUNTIME_ADAPTER_SCHEMA_VERSION,
        "current_metrics": {},
        "behaviors": {},
        "identities": [],
        "milestones": [],
        "recent_records": [],
        "growth_history": [],
        "pending_proposals": [],
        "proposal_stats": {},
        "growth_signals": [],
        "error": _safe_str(error, "unknown"),
        "timestamp": _now_iso(),
    }


# ============================================================
# GrowthRuntimeAdapter(主类)
# ============================================================


class GrowthRuntimeAdapter:
    """
    Growth 系统 Runtime Cycle Adapter (Phase C.6 / v1.0)

    **Read-Only Adapter** —— 只读取 Growth 状态,绝不修改。

    实现 CycleAdapter 协议(duck-type):
      name             = "growth"
      schema_version   = "1.0"
      attach()         - 接入 GrowthState/Engine/Evaluator/ProposalStore(可选自建)
      detach()         - 解除
      health_check()   - 健康检查
      process_cycle()  - 一次 cycle 处理
      snapshot()       - 快照

    业务行为:
      - 读 ctx.input_event / ctx.metadata
      - 读 growth_state.get() (深拷贝)
      - 读 growth_state.get_metric(key) (单值读取)
      - 读 growth_engine.get_state() (等价于 state.get(),深拷贝)
      - 读 growth_engine.apply_evaluated() (生成 GrowthRecord,不修改状态)
      - 读 growth_evaluator.evaluate() (只读评估)
      - 读 proposal_store.list() / get() (只读查询)
      - 统一格式写 ctx.growth_output (append 一个 dict)

    全部 fail-soft: GrowthState 缺失 / state 损坏 / 字段缺失 / 空状态均降级。
    """

    name: str = STANDARD_ADAPTER_GROWTH
    schema_version: str = GROWTH_RUNTIME_ADAPTER_SCHEMA_VERSION

    def __init__(
        self,
        growth_state: Any = None,
        growth_engine: Any = None,
        growth_evaluator: Any = None,
        proposal_store: Any = None,
        user_id: str = "yuyi",
        recent_records_limit: int = DEFAULT_RECENT_RECORDS_LIMIT,
        pending_proposals_limit: int = DEFAULT_PENDING_PROPOSALS_LIMIT,
        milestones_limit: int = DEFAULT_MILESTONES_LIMIT,
        growth_history_limit: int = DEFAULT_GROWTH_HISTORY_LIMIT,
    ) -> None:
        self._user_id = str(user_id or "yuyi")
        self._recent_records_limit = int(recent_records_limit or DEFAULT_RECENT_RECORDS_LIMIT)
        self._pending_proposals_limit = int(pending_proposals_limit or DEFAULT_PENDING_PROPOSALS_LIMIT)
        self._milestones_limit = int(milestones_limit or DEFAULT_MILESTONES_LIMIT)
        self._growth_history_limit = int(growth_history_limit or DEFAULT_GROWTH_HISTORY_LIMIT)

        # 外部注入优先(只读视图)
        self._state = growth_state
        self._engine = growth_engine
        self._evaluator = growth_evaluator
        self._proposal_store = proposal_store

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
        """接入 GrowthState/Engine/Evaluator/ProposalStore(可选自建)。"""
        with self._lock:
            # 自建缺失组件
            if self._state is None:
                try:
                    # V1.0-1C: bridge-first 收口（复用运行时权威 GrowthState，
                    # 避免自建陈旧副本覆盖 data/growth_state.json）
                    from src.growth.growth_state import resolve_authority_growth_state
                    self._state = resolve_authority_growth_state()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[phase_c6] GrowthState 自建失败: {exc}")
                    self._state = None
            if self._engine is None:
                try:
                    from src.growth.growth_engine import GrowthEngine
                    self._engine = GrowthEngine(state=self._state)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[phase_c6] GrowthEngine 自建失败: {exc}")
                    self._engine = None
            if self._evaluator is None:
                try:
                    from src.growth.growth_evaluator import GrowthEvaluator
                    self._evaluator = GrowthEvaluator()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[phase_c6] GrowthEvaluator 自建失败: {exc}")
                    self._evaluator = None
            if self._proposal_store is None:
                try:
                    from src.growth.proposal_store import ProposalStore
                    self._proposal_store = ProposalStore()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"[phase_c6] ProposalStore 自建失败: {exc}")
                    self._proposal_store = None
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
                engine_available = self._engine is not None
                evaluator_available = self._evaluator is not None
                proposal_store_available = self._proposal_store is not None
                # 至少 state 可用就算 healthy
                status = "healthy" if state_available else "degraded"
                result: Dict[str, Any] = {
                    "status": status,
                    "adapter": SOURCE_NAME,
                    "schema_version": GROWTH_RUNTIME_ADAPTER_SCHEMA_VERSION,
                    "state_available": bool(state_available),
                    "engine_available": bool(engine_available),
                    "evaluator_available": bool(evaluator_available),
                    "proposal_store_available": bool(proposal_store_available),
                    "attached": bool(self._attached),
                    "process_count": int(self._process_count),
                    "read_count": int(self._read_count),
                    "degraded_count": int(self._degraded_count),
                    "user_id": str(self._user_id),
                }
            self._last_health = result
            return result
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "degraded",
                "adapter": SOURCE_NAME,
                "schema_version": GROWTH_RUNTIME_ADAPTER_SCHEMA_VERSION,
                "error": repr(exc),
            }

    def process_cycle(self, ctx: Any) -> Any:
        """一次 Runtime Cycle 处理(被 orchestrator 调用)。

        流程:
          1) 校验 ctx 类型
          2) 安全读 growth_state(深拷贝)
          3) 安全读 proposal_store(只读查询)
          4) 构造统一 output
          5) append 到 ctx.growth_output
          6) 缓存 last_output / last_snapshot
          7) 返回 ctx

        **任何异常都 fail-soft,绝不抛。**
        **绝不调任何修改 Growth/Personality/SelfModel 的接口。**
        """
        with self._lock:
            self._process_count += 1
        try:
            if not isinstance(ctx, RuntimeCycleContext):
                return ctx

            # 读 growth 状态(只读,深拷贝)
            output, snap = self._read_growth_safe()
            ctx.growth_output.append(output)

            # 缓存
            with self._lock:
                self._last_output = output
                self._last_snapshot = snap.to_dict() if snap else None
                if output.get("degraded"):
                    self._degraded_count += 1
                self._last_error = output.get("error")
            return ctx
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c6] process_cycle 异常(已隔离): {exc}")
            with self._lock:
                self._degraded_count += 1
                self._last_error = repr(exc)
            # 写入 degraded output
            try:
                if isinstance(ctx, RuntimeCycleContext):
                    ctx.growth_output.append(_build_degraded_output(repr(exc)))
            except Exception:  # noqa: BLE001
                pass
            return ctx

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": str(self.name),
                "schema_version": str(self.schema_version),
                "available": bool(
                    self._state is not None
                    and not self._is_degraded_snapshot()
                ),
                "last_output": dict(self._last_output) if self._last_output else None,
                "last_snapshot": dict(self._last_snapshot) if self._last_snapshot else None,
                "process_count": int(self._process_count),
                "read_count": int(self._read_count),
                "degraded_count": int(self._degraded_count),
                "attached": bool(self._attached),
                "state_available": bool(self._state is not None),
                "engine_available": bool(self._engine is not None),
                "evaluator_available": bool(self._evaluator is not None),
                "proposal_store_available": bool(self._proposal_store is not None),
                "user_id": str(self._user_id),
                "last_error": self._last_error,
            }

    # --------------------------------------------------------
    # 业务:读取 growth 状态(只读,绝无副作用)
    # --------------------------------------------------------

    def read_growth(self) -> Dict[str, Any]:
        """公开读接口(供 C.1 之外调用)。

        Returns:
            统一格式的 growth_output dict
        """
        with self._lock:
            self._read_count += 1
        output, snap = self._read_growth_safe()
        with self._lock:
            self._last_output = output
            self._last_snapshot = snap.to_dict() if snap else None
            if output.get("degraded"):
                self._degraded_count += 1
        return output

    def _read_growth_safe(self) -> tuple:
        """安全读 growth 状态,返回 (output_dict, snapshot)。

        **任何异常都返回 degraded output + 空 snapshot。**
        **绝不允许调用任何修改状态的接口。**
        """
        # 1) State 不存在 → degraded
        if self._state is None:
            return (
                _build_degraded_output("growth_state_not_attached"),
                _GrowthOutputSnapshot(),
            )

        # 2) 安全读 state(深拷贝,绝不在原 dict 上写)
        state_data: Dict[str, Any] = {}
        try:
            get_fn = getattr(self._state, "get", None)
            if callable(get_fn):
                raw = get_fn()
                if isinstance(raw, dict):
                    state_data = _safe_deepcopy(raw)
                else:
                    # 非 dict → 视为损坏,降级
                    return (
                        _build_degraded_output(
                            f"growth_state_get_returned_non_dict: {type(raw).__name__}"
                        ),
                        _GrowthOutputSnapshot(),
                    )
            else:
                return (
                    _build_degraded_output("growth_state_get_not_callable"),
                    _GrowthOutputSnapshot(),
                )
        except Exception as exc:  # noqa: BLE001
            return (
                _build_degraded_output(f"growth_state_get_error: {exc!r}"),
                _GrowthOutputSnapshot(),
            )

        # 3) 解析 current_metrics
        current_metrics: Dict[str, float] = {}
        try:
            metrics = state_data.get("metrics", {}) or {}
            if isinstance(metrics, dict):
                for k, v in metrics.items():
                    try:
                        current_metrics[str(k)] = round(float(v), 4)
                    except (TypeError, ValueError):
                        continue
        except Exception:  # noqa: BLE001
            current_metrics = {}

        # 4) 解析 behaviors
        behaviors: Dict[str, bool] = {}
        try:
            beh = state_data.get("behaviors", {}) or {}
            if isinstance(beh, dict):
                for k, v in beh.items():
                    behaviors[str(k)] = bool(v)
        except Exception:  # noqa: BLE001
            behaviors = {}

        # 5) 解析 identities
        identities: List[str] = []
        try:
            ids = state_data.get("identities", []) or []
            if isinstance(ids, list):
                identities = [str(x) for x in ids]
        except Exception:  # noqa: BLE001
            identities = []

        # 6) 解析 milestones(只取最近 N 条,深拷贝)
        milestones: List[Dict[str, Any]] = []
        try:
            ms = state_data.get("milestones", []) or []
            if isinstance(ms, list):
                sliced = ms[-self._milestones_limit:] if self._milestones_limit > 0 else ms
                for m in sliced:
                    if isinstance(m, dict):
                        milestones.append(_safe_deepcopy(m))
        except Exception:  # noqa: BLE001
            milestones = []

        # 7) 解析 growth_history(只取最近 N 条,深拷贝)
        growth_history: List[Dict[str, Any]] = []
        try:
            gh = state_data.get("growth_history", []) or []
            if isinstance(gh, list):
                sliced = gh[-self._growth_history_limit:] if self._growth_history_limit > 0 else gh
                for item in sliced:
                    if isinstance(item, dict):
                        growth_history.append(_safe_deepcopy(item))
        except Exception:  # noqa: BLE001
            growth_history = []

        # 8) 解析 recent_records(GrowthRecord 仅在 apply_evaluated 时生成,这里从 growth_history 中提取)
        recent_records: List[Dict[str, Any]] = []
        try:
            # recent_records 取自 growth_history 的最近 N 条(只读)
            sliced = growth_history[-self._recent_records_limit:] if self._recent_records_limit > 0 else growth_history
            recent_records = list(sliced)
        except Exception:  # noqa: BLE001
            recent_records = []

        # 9) 读取 pending_proposals + proposal_stats(只读)
        pending_proposals, proposal_stats = self._read_proposals_safe()

        # 10) growth_signals: 来自 growth_history 的 mode/topic 聚合
        growth_signals = self._compute_growth_signals_safe(growth_history)

        # 11) 构造 output
        output: Dict[str, Any] = {
            "growth_available": True,
            "degraded": False,
            "source": SOURCE_NAME,
            "schema_version": GROWTH_RUNTIME_ADAPTER_SCHEMA_VERSION,
            "current_metrics": current_metrics,
            "behaviors": behaviors,
            "identities": identities,
            "milestones": milestones,
            "recent_records": recent_records,
            "growth_history": growth_history,
            "pending_proposals": pending_proposals,
            "proposal_stats": proposal_stats,
            "growth_signals": growth_signals,
            "error": None,
            "timestamp": _now_iso(),
        }

        # 12) 构造 snapshot(缓存)
        snap = _GrowthOutputSnapshot(
            metrics=dict(current_metrics),
            behaviors=dict(behaviors),
            identities=list(identities),
            milestones=list(milestones),
            recent_records=list(recent_records),
            growth_history=list(growth_history),
            pending_proposals=list(pending_proposals),
            proposal_stats=dict(proposal_stats),
            growth_signals=list(growth_signals),
        )
        return output, snap

    def _read_proposals_safe(self) -> tuple:
        """读 pending proposals(只读)。

        Returns:
            (pending_proposals, proposal_stats)
        """
        empty_proposals: List[Dict[str, Any]] = []
        empty_stats: Dict[str, int] = {
            "total": 0,
            "pending": 0,
            "accepted": 0,
            "rejected": 0,
            "applied": 0,
        }

        if self._proposal_store is None:
            return empty_proposals, empty_stats

        try:
            # 1) 读所有 proposals(只读查询)
            list_fn = getattr(self._proposal_store, "list", None)
            if not callable(list_fn):
                return empty_proposals, empty_stats

            # 尝试带 status 过滤(若支持)
            try:
                all_proposals = list_fn(
                    status=None,
                    limit=max(100, self._pending_proposals_limit * 3),
                    offset=0,
                )
            except TypeError:
                # 旧版 API 不支持 status/limit/offset kwargs
                all_proposals = list_fn()

            if not isinstance(all_proposals, list):
                return empty_proposals, empty_stats

            # 2) 统计
            stats: Dict[str, int] = {
                "total": len(all_proposals),
                "pending": 0,
                "accepted": 0,
                "rejected": 0,
                "applied": 0,
            }
            pending: List[Dict[str, Any]] = []

            for p in all_proposals:
                try:
                    # 尝试 to_dict()
                    if hasattr(p, "to_dict") and callable(p.to_dict):
                        try:
                            d = p.to_dict()
                        except Exception:
                            d = None
                    elif isinstance(p, dict):
                        d = dict(p)
                    else:
                        d = None

                    if not isinstance(d, dict):
                        continue

                    status = str(d.get("status", "unknown"))
                    if status in stats:
                        stats[status] = stats.get(status, 0) + 1

                    # 只收集 pending,且限制数量
                    if status == "pending" and len(pending) < self._pending_proposals_limit:
                        # 深拷贝(只读)
                        pending.append(_safe_deepcopy(d))
                except Exception:  # noqa: BLE001
                    continue

            return pending, stats
        except Exception:  # noqa: BLE001
            return empty_proposals, empty_stats

    def _compute_growth_signals_safe(
        self, growth_history: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """从 growth_history 聚合 growth_signals(纯函数,只读)。"""
        signals: List[Dict[str, Any]] = []
        try:
            if not isinstance(growth_history, list):
                return signals
            # 取最近 N 条历史,提取 mode + topic
            for item in growth_history[-self._growth_history_limit:]:
                if not isinstance(item, dict):
                    continue
                try:
                    signals.append({
                        "meaning": _safe_str(item.get("meaning", "")),
                        "topic": _safe_str(item.get("topic", "")),
                        "mode": _safe_str(item.get("mode", "")),
                        "time": _safe_str(item.get("time", "")),
                    })
                except Exception:
                    continue
        except Exception:  # noqa: BLE001
            signals = []
        return signals

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
            return int(self._process_count)

    @property
    def read_count(self) -> int:
        with self._lock:
            return int(self._read_count)

    @property
    def degraded_count(self) -> int:
        with self._lock:
            return int(self._degraded_count)

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error


# ============================================================
# 工厂
# ============================================================


def create_growth_runtime_adapter(
    growth_state: Any = None,
    growth_engine: Any = None,
    growth_evaluator: Any = None,
    proposal_store: Any = None,
    user_id: str = "yuyi",
) -> GrowthRuntimeAdapter:
    """工厂函数:创建一个 GrowthRuntimeAdapter(只读)。

    所有参数可选;未提供时在 attach() 时自建。
    """
    return GrowthRuntimeAdapter(
        growth_state=growth_state,
        growth_engine=growth_engine,
        growth_evaluator=growth_evaluator,
        proposal_store=proposal_store,
        user_id=user_id,
    )


def safe_get_growth_adapter_summary(adapter: Any) -> Dict[str, Any]:
    """全局安全 summary(fail-soft)。"""
    empty = {
        "name": STANDARD_ADAPTER_GROWTH,
        "schema_version": GROWTH_RUNTIME_ADAPTER_SCHEMA_VERSION,
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
    "GROWTH_RUNTIME_ADAPTER_SCHEMA_VERSION",
    "PHASE_C6_NAME",
    "PHASE_C6_VERSION",
    "PHASE_C6_STAGE_GROWTH_READ",
    "PHASE_C6_STAGE_GROWTH_DEGRADED",
    "ALL_PHASE_C6_STAGES",
    "SOURCE_NAME",
    "DEFAULT_RECENT_RECORDS_LIMIT",
    "DEFAULT_PENDING_PROPOSALS_LIMIT",
    "DEFAULT_MILESTONES_LIMIT",
    "DEFAULT_GROWTH_HISTORY_LIMIT",
    "_GrowthOutputSnapshot",
    "GrowthRuntimeAdapter",
    "create_growth_runtime_adapter",
    "safe_get_growth_adapter_summary",
]
