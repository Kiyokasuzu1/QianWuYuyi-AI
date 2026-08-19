# -*- coding: utf-8 -*-
"""
src/runtime/phase_b_integration.py

Phase B.1 Runtime Integration —— AutonomousScheduler 接入层

本文件是 Phase B.1 的"接入胶水",**不修改**任何核心模块:

  - 不修改 RuntimeCore
  - 不修改 AutonomousScheduler
  - 不修改 RuntimeSupervisor
  - 不修改 Personality / Memory / Growth / Emotion 内部

集成原理(利用现有调用链,0 侵入):
  ┌─────────────────┐
  │ RuntimeSupervisor._tick_iteration()  [Phase A 已就绪]
  │  step 1: runtime_core.tick()
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │ RuntimeCore.tick()  [已存在]
  │  if autonomous_scheduler:
  │      autonomous_scheduler.on_tick(self)
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │ AutonomousScheduler.on_tick(runtime)  [已存在]
  │  - memory_maintenance (每 10 ticks)
  │  - identity_check      (每 15 ticks)
  │  - growth_evaluation   (每 8 ticks)
  │  - reflection          (每 1 tick)
  └─────────────────┘

B.1 只需要在 RuntimeCore config 中显式开启 autonomous_scheduler_enabled=True,
supervisor 周期性调用 tick() 就会自动驱动 scheduler。

本模块职责:
  1. 提供 B.1 默认 config 注入(apply_phase_b1_config)
  2. 提供 scheduler 状态摘要(summarize_scheduler)
  3. 提供 RuntimeB1Bridge 轻量包装,只读 + 防崩溃
  4. 全部 fail-soft,任何异常只记日志不影响 supervisor

约束(项目硬红线):
  - 不修改任何核心业务模块
  - 不自动接受 GrowthProposal(scheduler 内部已保证)
  - 不调用 LLM
  - 不启动 ProactiveEngine / InitiativeBridge(后续阶段)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Phase B.1 默认配置
# ============================================================
# 这些值只在用户未显式覆盖时生效
# 与 AutonomousSchedulerConfig 字段一一对应
# 见 src/runtime/autonomous_scheduler.py:AutonomousSchedulerConfig

PHASE_B1_DEFAULT_CONFIG: Dict[str, Any] = {
    # RuntimeCore 适配器总开关(autonomous_scheduler 初始化位于
    # `if adapters_enabled:` 分支内,line 344-660+)
    "adapters_enabled": True,

    # 主开关
    "autonomous_scheduler_enabled": True,

    # AutonomousSchedulerConfig 字段
    "as_history_limit": 400,
    "as_memory_interval_ticks": 10,
    "as_identity_interval_ticks": 15,
    "as_growth_eval_interval_ticks": 8,
    "as_reflection_interval_ticks": 1,
    "as_min_memories_for_maintenance": 5,
    "as_proposal_evaluation_batch": 3,
}


PHASE_B1_NAME = "phase_b1"
PHASE_B1_VERSION = "1.0.0"


# ============================================================
# 配置注入
# ============================================================

def apply_phase_b1_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.1 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B1_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)

    Args:
        user_cfg: 用户/CLI/config.yaml 提供的配置(可能为 None)

    Returns:
        合并后的新 dict,可直接传给 RuntimeCore(config=...)
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged.update(user_cfg)
    for k, v in PHASE_B1_DEFAULT_CONFIG.items():
        merged.setdefault(k, v)
    return merged


def is_phase_b1_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.1 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    return bool(cfg.get("autonomous_scheduler_enabled", False))


# ============================================================
# 状态摘要(只读,供 Dashboard / health check)
# ============================================================

def summarize_scheduler(scheduler: Any) -> Optional[Dict[str, Any]]:
    """
    提取 scheduler 状态摘要。scheduler 为 None 时返回 None。

    始终 fail-soft:任何异常返回 None 并打日志。
    """
    if scheduler is None:
        return None
    try:
        snap: Optional[Dict[str, Any]] = None
        if hasattr(scheduler, "get_snapshot"):
            snap = scheduler.get_snapshot() or None
        history_count = 0
        if hasattr(scheduler, "get_history"):
            try:
                history_count = len(scheduler.get_history(limit=10000))
            except Exception:
                history_count = -1
        cfg_dict: Dict[str, Any] = {}
        try:
            cfg_obj = getattr(scheduler, "cfg", None)
            if cfg_obj is not None and hasattr(cfg_obj, "__dict__"):
                cfg_dict = {k: v for k, v in vars(cfg_obj).items() if not k.startswith("_")}
        except Exception:
            cfg_dict = {}

        return {
            "available": True,
            "snapshot": snap,
            "history_count": history_count,
            "config": cfg_dict,
        }
    except Exception as e:  # 兜底:绝不抛
        logger.debug(f"[phase_b1] summarize_scheduler 异常(已隔离): {e}")
        return None


def summarize_runtime_b1(runtime_core: Any) -> Dict[str, Any]:
    """
    提取 RuntimeCore 上与 B.1 相关的状态。

    Returns:
        {
          "phase_b1_enabled": bool,
          "scheduler_available": bool,
          "scheduler": {...} | None,
          "tick_count": int,
        }
    """
    scheduler: Any = getattr(runtime_core, "autonomous_scheduler", None) if runtime_core is not None else None
    tick_count = 0
    if runtime_core is not None and hasattr(runtime_core, "tick_count"):
        try:
            tick_count = int(getattr(runtime_core, "tick_count"))
        except Exception:
            tick_count = 0

    return {
        "phase_b1_enabled": scheduler is not None,
        "scheduler_available": scheduler is not None,
        "scheduler": summarize_scheduler(scheduler),
        "tick_count": tick_count,
    }


# ============================================================
# 轻量 Bridge(可选,只读 + 缓存最近一次摘要)
# ============================================================

class RuntimeB1Bridge:
    """
    Phase B.1 Bridge —— 对 RuntimeCore 上 scheduler 能力的轻量包装

    设计目的:
      - 给 start_runtime.py / Dashboard 一个稳定访问点
      - 不修改 RuntimeCore / Supervisor
      - 只读,绝对不调用任何会触发副作用的方法
      - 缓存最近一次摘要,减少重复 get_history 调用

    用法:
        bridge = RuntimeB1Bridge(runtime_core)
        status = bridge.get_status()
        # status["scheduler"]["snapshot"]["total_tasks"]
    """

    def __init__(self, runtime_core: Any) -> None:
        self._runtime_core = runtime_core
        self._last_summary: Optional[Dict[str, Any]] = None
        self._last_summary_ts: float = 0.0

    @property
    def runtime_core(self) -> Any:
        return self._runtime_core

    def get_scheduler(self) -> Any:
        if self._runtime_core is None:
            return None
        return getattr(self._runtime_core, "autonomous_scheduler", None)

    def get_status(self) -> Dict[str, Any]:
        """获取 B.1 状态摘要"""
        import time as _time
        now = _time.time()
        # 1 秒内复用缓存,避免高频轮询时频繁调用 get_history
        if self._last_summary is not None and (now - self._last_summary_ts) < 1.0:
            return self._last_summary

        summary = summarize_runtime_b1(self._runtime_core)
        summary["phase_b1_name"] = PHASE_B1_NAME
        summary["phase_b1_version"] = PHASE_B1_VERSION
        summary["ts"] = now

        self._last_summary = summary
        self._last_summary_ts = now
        return summary

    def get_history(self, limit: int = 20) -> list:
        """读取 scheduler 最近 history(只读)"""
        scheduler = self.get_scheduler()
        if scheduler is None or not hasattr(scheduler, "get_history"):
            return []
        try:
            return list(scheduler.get_history(limit=limit) or [])
        except Exception as e:
            logger.debug(f"[phase_b1] get_history 异常(已隔离): {e}")
            return []


# ============================================================
# 工厂
# ============================================================

def create_b1_bridge(runtime_core: Any) -> RuntimeB1Bridge:
    """工厂:创建 RuntimeB1Bridge 实例"""
    return RuntimeB1Bridge(runtime_core=runtime_core)


__all__ = [
    "PHASE_B1_DEFAULT_CONFIG",
    "PHASE_B1_NAME",
    "PHASE_B1_VERSION",
    "apply_phase_b1_config",
    "is_phase_b1_enabled",
    "summarize_scheduler",
    "summarize_runtime_b1",
    "RuntimeB1Bridge",
    "create_b1_bridge",
]
