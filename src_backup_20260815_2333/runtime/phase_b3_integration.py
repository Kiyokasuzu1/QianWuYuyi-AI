# -*- coding: utf-8 -*-
"""
src/runtime/phase_b3_integration.py

Phase B.3 Runtime Integration —— ActionDispatcher 接入层

本文件是 Phase B.3 的"接入胶水",**不修改**任何核心模块:

  - 不修改 RuntimeCore
  - 不修改 RuntimeSupervisor
  - 不修改 ActionDispatcher
  - 不修改 ProactiveEngine
  - 不修改 AutonomousScheduler
  - 不修改 CognitiveCore

集成原理(利用 Phase A 已预留的接入点,0 侵入):
  ┌──────────────────────────────────────────┐
  │ RuntimeSupervisor._tick_iteration()  [Phase A 已就绪]
  │  step 1: runtime_core.tick()
  │  step 2: cognitive_core.emit_event(system.tick)
  │  step 3: proactive_engine.scan_all_users()
  │  step 4: if action_dispatcher:           [Phase A 已就绪]
  │             action_dispatcher.flush_pending()  [try/except 已隔离]
  │  step 5: heartbeat.beat()
  └──────────────────────────────────────────┘

B.3 接入路径(最小化, 零侵入):
  start_runtime.py
        │
        │ 构造 ProactiveEngine (B.2 已就绪)
        │ 构造 ActionDispatcher (B.3 新增,但不注册任何 handler)
        │ 构造 RuntimeB3Bridge(engine, dispatcher)
        │
        ▼
  RuntimeSupervisor(action_dispatcher=b3_bridge)
        │
        ▼
  每个 tick:
    b3_bridge.flush_pending()
        │
        ├─→ engine.get_pending_actions()  [只读]
        ├─→ engine.execute_action(id)     [自动过 ActionConfidenceGate]
        ├─→ proposed_action_to_runtime_action()  [纯映射]
        └─→ dispatcher.dispatch(action)   [同步,handler 未注册→no_handler 状态]

B.3 硬约束(项目红线):
  - 不绕过 ActionConfidenceGate(由 engine.execute_action 内部强制)
  - 不自动发消息(handler 不注册,即使 dispatch 也不外发)
  - 不启动 InitiativeBridge
  - 不修改 ProactiveEngine / ActionDispatcher
  - 默认关闭,显式 enable 才启动
  - 全部 fail-soft

本模块职责:
  1. 提供 B.3 默认 config 注入(apply_phase_b3_config)
  2. 提供 ProposedAction → Action 映射(proposed_action_to_runtime_action)
  3. 提供 RuntimeB3Bridge:flush_pending() 周期把 pending 安全送入 dispatcher
  4. 提供 RuntimeB3Bridge:summarize_b3() 只读状态摘要
  5. 提供 safe_flush_pending() 全局异常隔离包装
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ============================================================
# Phase B.3 默认配置
# ============================================================

PHASE_B3_DEFAULT_CONFIG: Dict[str, Any] = {
    # 主开关:dispatcher 子模块启用
    "dispatcher": {
        "enabled": False,          # 默认关闭,显式 enable 才启动
        "max_per_tick": 3,         # 每次 tick 最多处理 N 条 pending
        "respect_quiet_hours": True,  # 保留(占位,后续阶段可联动 QuietHours)
    },
}

PHASE_B3_NAME = "phase_b3"
PHASE_B3_VERSION = "1.0.0"


# ============================================================
# 配置注入
# ============================================================

def apply_phase_b3_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.3 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B3_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并

    Args:
        user_cfg: 用户/CLI/config.yaml 提供的配置(可能为 None)

    Returns:
        合并后的新 dict
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B3_DEFAULT_CONFIG.items():
        if isinstance(v, dict):
            existing = merged.get(k)
            if isinstance(existing, dict):
                # 递归合并
                sub: Dict[str, Any] = {}
                sub.update(v)
                sub.update(existing)
                merged[k] = sub
            else:
                merged[k] = {k2: v2 for k2, v2 in v.items()}
        else:
            merged.setdefault(k, v)
    return merged


def is_phase_b3_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.3 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    dispatcher = cfg.get("dispatcher", {})
    if not isinstance(dispatcher, dict):
        return False
    return bool(dispatcher.get("enabled", False))


def is_b3_enabled_simple(user_cfg: Optional[Dict[str, Any]]) -> bool:
    """
    简化版判定:支持 cfg["dispatcher_enabled"]=True 直接开关。

    优先级:
      cfg["dispatcher_enabled"] (顶层 bool) > cfg["dispatcher"]["enabled"]
    """
    if not isinstance(user_cfg, dict):
        return False
    if "dispatcher_enabled" in user_cfg:
        return bool(user_cfg.get("dispatcher_enabled"))
    return is_phase_b3_enabled(user_cfg)


# ============================================================
# Action Mapper(ProposedAction -> runtime Action)
# ============================================================

def proposed_action_to_runtime_action(
    proposed: Any,
    runtime_action_cls: Any = None,
) -> Optional[Any]:
    """
    将 ProactiveEngine 的 ProposedAction 映射为 ActionDispatcher 的 Action。

    字段映射:
      action_id   <- proposed.action_id
      action_type <- proposed.action_type.value (enum -> str, 字符串则保持)
      payload     <- {
                       "user_id":     proposed.user_id,
                       "content":     proposed.content,
                       "confidence":  proposed.confidence,
                       "source":      "proactive_engine",
                       "trigger_reason": proposed.trigger_reason,
                       "necessity_score":  proposed.necessity_score,
                       "disturbance_risk": proposed.disturbance_risk,
                     }
      reason      <- proposed.trigger_reason(无则用 "proactive_proposed")

    Args:
        proposed: ProposedAction 实例(duck-typed)
        runtime_action_cls: Action 类(可选,默认从 src.runtime.action_dispatcher 导入)
                            允许测试或外部覆盖。

    Returns:
        Action 实例;若 proposed 为 None 或缺关键字段,返回 None(不抛错)。
    """
    if proposed is None:
        return None

    # 解析 action_type (enum 或 str)
    action_type_value = getattr(proposed, "action_type", None)
    if action_type_value is None:
        logger.debug("[phase_b3] mapper: missing action_type, skip")
        return None
    if hasattr(action_type_value, "value"):
        action_type_str = str(action_type_value.value)
    else:
        action_type_str = str(action_type_value)

    # 解析 action_id
    action_id = getattr(proposed, "action_id", None)
    if not action_id:
        logger.debug("[phase_b3] mapper: missing action_id, skip")
        return None

    # payload(完整信息,handler 可见,但目前 handler 不注册)
    payload: Dict[str, Any] = {
        "user_id": getattr(proposed, "user_id", None),
        "content": getattr(proposed, "content", None),
        "confidence": float(getattr(proposed, "confidence", 0.0) or 0.0),
        "source": "proactive_engine",
        "trigger_reason": getattr(proposed, "trigger_reason", ""),
        "necessity_score": float(getattr(proposed, "necessity_score", 0.0) or 0.0),
        "disturbance_risk": float(getattr(proposed, "disturbance_risk", 0.0) or 0.0),
    }
    # 附 context(若有)
    ctx = getattr(proposed, "context", None)
    if isinstance(ctx, dict) and ctx:
        payload["context"] = dict(ctx)

    reason = str(getattr(proposed, "trigger_reason", "") or "proactive_proposed")

    # 构造 Action(优先用注入的 cls,否则从 action_dispatcher 导入)
    if runtime_action_cls is None:
        try:
            from src.runtime.action_dispatcher import Action as _Action
            runtime_action_cls = _Action
        except Exception as e:
            logger.debug(f"[phase_b3] mapper: 导入 Action 失败(已隔离): {e}")
            return None

    try:
        return runtime_action_cls(
            action_id=action_id,
            action_type=action_type_str,
            payload=payload,
            reason=reason,
        )
    except Exception as e:
        logger.debug(f"[phase_b3] mapper: 构造 Action 失败(已隔离): {e}")
        return None


# ============================================================
# RuntimeB3Bridge
# ============================================================

class RuntimeB3Bridge:
    """
    Phase B.3 Bridge —— ProactiveEngine → ActionDispatcher 的受控通道

    设计目的:
      - 把 ProactiveEngine 产生的 ProposedAction 安全送入 ActionDispatcher
      - 不修改 ProactiveEngine / ActionDispatcher 任何实现
      - 强制经过 ActionConfidenceGate(通过 engine.execute_action)
      - 幂等:同一 action_id 不会被重复 dispatch
      - 异常隔离:任何步骤异常只记日志,不抛出
      - 限速:每次最多处理 max_per_tick 条

    用法:
        bridge = RuntimeB3Bridge(
            proactive_engine=engine,
            action_dispatcher=dispatcher,
            max_per_tick=3,
        )
        result = bridge.flush_pending()  # 供 supervisor step 4 调用
        status = bridge.summarize_b3()
    """

    def __init__(
        self,
        proactive_engine: Any,
        action_dispatcher: Any,
        max_per_tick: int = 3,
    ) -> None:
        self._engine = proactive_engine
        self._dispatcher = action_dispatcher
        self._max_per_tick = max(1, int(max_per_tick))

        # 已 dispatch 过的 action_id(幂等)
        self._dispatched_ids: Set[str] = set()
        self._dispatched_lock = threading.Lock()

        # 累计统计
        self._total_processed = 0
        self._total_dispatched = 0
        self._total_rejected = 0
        self._total_skipped = 0
        self._total_errors = 0

        # 最近 N 条结果(只读)
        self._recent_results: List[Dict[str, Any]] = []
        self._recent_lock = threading.Lock()
        self._max_recent = 50

        logger.info(
            f"[phase_b3] RuntimeB3Bridge 初始化: "
            f"engine={proactive_engine is not None}, "
            f"dispatcher={action_dispatcher is not None}, "
            f"max_per_tick={self._max_per_tick}"
        )

    # --------------------------------------------------------
    # 公开属性
    # --------------------------------------------------------

    @property
    def engine(self) -> Any:
        return self._engine

    @property
    def dispatcher(self) -> Any:
        return self._dispatcher

    def is_enabled(self) -> bool:
        """B.3 是否真正可用(engine + dispatcher 都存在)"""
        return self._engine is not None and self._dispatcher is not None

    # --------------------------------------------------------
    # flush_pending()(核心方法,供 supervisor step 4 调用)
    # --------------------------------------------------------

    def flush_pending(self) -> Dict[str, int]:
        """
        周期把 pending 流转为 dispatched。

        流程:
          1. 读 proactive_engine.get_pending_actions()(只读)
          2. 限速:最多取 max_per_tick 条
          3. 跳过已 dispatch 过的(幂等)
          4. 对每条:proactive_engine.execute_action(action_id)
              - 成功(过 gate):映射成 Action,然后 dispatcher.dispatch(action)
              - 失败(被 gate 拒绝/推迟/跳过):只记 rejected,不 dispatch
          5. 累加统计,记录最近结果

        Returns:
            {"processed": int, "dispatched": int, "rejected": int, "skipped": int}

        异常:永不向上抛(任何步骤异常都隔离到 _total_errors)
        """
        processed = 0
        dispatched = 0
        rejected = 0
        skipped = 0

        # 防御:任一依赖缺失,直接返回零计数(不抛错)
        if not self.is_enabled():
            return {"processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0}

        # 1. 取 pending
        try:
            pending = self._engine.get_pending_actions() or []
        except Exception as e:
            self._total_errors += 1
            logger.debug(f"[phase_b3] get_pending_actions 异常(已隔离): {e}")
            return {"processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0}

        if not pending:
            return {"processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0}

        # 2. 限速
        pending = list(pending)[: self._max_per_tick]

        # 3-5. 逐条处理
        for proposed in pending:
            try:
                action_id = getattr(proposed, "action_id", None)
                if not action_id:
                    continue

                # 幂等检查
                with self._dispatched_lock:
                    if action_id in self._dispatched_ids:
                        skipped += 1
                        self._total_skipped += 1
                        self._record_recent({
                            "action_id": action_id,
                            "stage": "skipped_duplicate",
                            "ts": time.time(),
                        })
                        continue

                processed += 1
                self._total_processed += 1

                # 4. 强制过 ActionConfidenceGate(execute_action 内部)
                ok = False
                try:
                    gate_result = self._engine.execute_action(action_id)
                    ok = bool(gate_result)
                except Exception as e:
                    self._total_errors += 1
                    logger.debug(f"[phase_b3] execute_action 异常(已隔离): {e}")
                    rejected += 1
                    self._total_rejected += 1
                    self._record_recent({
                        "action_id": action_id,
                        "stage": "execute_exception",
                        "ts": time.time(),
                        "error": repr(e),
                    })
                    continue

                if not ok:
                    # 克制层拒绝/推迟/跳过 —— 绝对不 dispatch
                    rejected += 1
                    self._total_rejected += 1
                    self._record_recent({
                        "action_id": action_id,
                        "stage": "gate_rejected",
                        "ts": time.time(),
                    })
                    continue

                # 5. 过门后,映射为 Action
                action = proposed_action_to_runtime_action(proposed)
                if action is None:
                    self._total_errors += 1
                    logger.debug(
                        f"[phase_b3] mapper 返回 None,action_id={action_id}(已隔离)"
                    )
                    rejected += 1
                    self._total_rejected += 1
                    self._record_recent({
                        "action_id": action_id,
                        "stage": "mapper_none",
                        "ts": time.time(),
                    })
                    continue

                # 6. 送入 dispatcher
                try:
                    self._dispatcher.dispatch(action)
                except Exception as e:
                    self._total_errors += 1
                    logger.debug(
                        f"[phase_b3] dispatcher.dispatch 异常(已隔离): {e}"
                    )
                    rejected += 1
                    self._total_rejected += 1
                    self._record_recent({
                        "action_id": action_id,
                        "stage": "dispatch_exception",
                        "ts": time.time(),
                        "error": repr(e),
                    })
                    continue

                # 标记为已 dispatch(幂等)
                with self._dispatched_lock:
                    self._dispatched_ids.add(action_id)

                dispatched += 1
                self._total_dispatched += 1
                self._record_recent({
                    "action_id": action_id,
                    "action_type": getattr(action, "action_type", None),
                    "stage": "dispatched",
                    "ts": time.time(),
                })

            except Exception as e:
                # 兜底:单条处理异常,不影响其他条
                self._total_errors += 1
                logger.debug(f"[phase_b3] flush_pending 单条异常(已隔离): {e}")
                rejected += 1
                self._total_rejected += 1
                continue

        return {
            "processed": processed,
            "dispatched": dispatched,
            "rejected": rejected,
            "skipped": skipped,
        }

    # --------------------------------------------------------
    # summarize_b3()
    # --------------------------------------------------------

    def summarize_b3(self) -> Dict[str, Any]:
        """
        返回 B.3 状态摘要(只读,供 Dashboard / status_loop)。

        Returns:
            {
                "available": bool,           # engine + dispatcher 都注入
                "enabled": bool,             # 同 available
                "phase_b3_enabled": bool,
                "phase_b3_name": str,
                "phase_b3_version": str,
                "pending": int,              # 当前 pending 数
                "processed": int,            # 累计 processed
                "dispatched": int,           # 累计 dispatched
                "rejected": int,             # 累计 rejected
                "skipped": int,              # 累计 skipped
                "dispatched_unique": int,    # 唯一 action_id 数
                "errors": int,               # 累计 errors
                "max_per_tick": int,
                "ts": float,
            }
        """
        # 当前 pending
        pending = 0
        if self._engine is not None and hasattr(self._engine, "get_pending_actions"):
            try:
                pending = len(self._engine.get_pending_actions() or [])
            except Exception as e:
                logger.debug(f"[phase_b3] summarize: get_pending 异常(已隔离): {e}")
                pending = -1

        with self._dispatched_lock:
            dispatched_unique = len(self._dispatched_ids)

        return {
            "available": self.is_enabled(),
            "enabled": self.is_enabled(),
            "phase_b3_enabled": self.is_enabled(),
            "phase_b3_name": PHASE_B3_NAME,
            "phase_b3_version": PHASE_B3_VERSION,
            "pending": pending,
            "processed": self._total_processed,
            "dispatched": self._total_dispatched,
            "rejected": self._total_rejected,
            "skipped": self._total_skipped,
            "dispatched_unique": dispatched_unique,
            "errors": self._total_errors,
            "max_per_tick": self._max_per_tick,
            "ts": time.time(),
        }

    # --------------------------------------------------------
    # get_recent_results()(只读副本)
    # --------------------------------------------------------

    def get_recent_results(self, limit: int = 20) -> List[Dict[str, Any]]:
        """返回最近 N 条流转结果(只读副本)"""
        with self._recent_lock:
            items = list(self._recent_results[-limit:])
        return items

    # --------------------------------------------------------
    # 内部辅助
    # --------------------------------------------------------

    def _record_recent(self, item: Dict[str, Any]) -> None:
        try:
            with self._recent_lock:
                self._recent_results.append(item)
                if len(self._recent_results) > self._max_recent:
                    self._recent_results = self._recent_results[-self._max_recent:]
        except Exception as e:
            logger.debug(f"[phase_b3] _record_recent 异常(已隔离): {e}")


# ============================================================
# 工厂
# ============================================================

def create_b3_bridge(
    proactive_engine: Any,
    action_dispatcher: Any,
    max_per_tick: int = 3,
) -> RuntimeB3Bridge:
    """工厂:创建 RuntimeB3Bridge 实例"""
    return RuntimeB3Bridge(
        proactive_engine=proactive_engine,
        action_dispatcher=action_dispatcher,
        max_per_tick=max_per_tick,
    )


# ============================================================
# 全局异常隔离包装(供外部可选调用)
# ============================================================

def safe_flush_pending(bridge: Any) -> Dict[str, int]:
    """
    全局安全 flush(任何异常都吸收)。

    Args:
        bridge: RuntimeB3Bridge 实例(可为 None)

    Returns:
        {"ok": bool, "result": dict|None, "error": str|None}
    """
    if bridge is None:
        return {"ok": False, "result": None, "error": "bridge is None"}
    if not hasattr(bridge, "flush_pending"):
        return {"ok": False, "result": None, "error": "missing flush_pending"}
    try:
        result = bridge.flush_pending() or {}
        return {"ok": True, "result": result, "error": None}
    except Exception as e:
        return {"ok": False, "result": None, "error": repr(e)}


__all__ = [
    "PHASE_B3_DEFAULT_CONFIG",
    "PHASE_B3_NAME",
    "PHASE_B3_VERSION",
    "apply_phase_b3_config",
    "is_phase_b3_enabled",
    "is_b3_enabled_simple",
    "proposed_action_to_runtime_action",
    "RuntimeB3Bridge",
    "create_b3_bridge",
    "safe_flush_pending",
]
