# -*- coding: utf-8 -*-
"""
src/runtime/phase_b2_integration.py

Phase B.2 Runtime Integration —— ProactiveEngine 接入层

本文件是 Phase B.2 的"接入胶水",**不修改**任何核心模块:

  - 不修改 RuntimeCore
  - 不修改 ProactiveEngine
  - 不修改 RuntimeSupervisor
  - 不修改 AutonomousScheduler
  - 不修改 Personality / Memory / Growth / Emotion 内部

集成原理(利用 Phase A 已预留的接入点,0 侵入):
  ┌──────────────────────────┐
  │ RuntimeSupervisor._tick_iteration()  [Phase A 已就绪]
  │  step 1: runtime_core.tick()
  │  step 2: cognitive_core.emit_event(system.tick)
  │  step 3: if proactive_engine: proactive_engine.scan_all_users()
  │          [try/except 已隔离]
  │  ...
  └──────────────────────────┘

B.2 只需要在 start_runtime.py 中显式构造 ProactiveEngine 并注入 supervisor,
supervisor 已有的 step 3 会自动周期性调用 scan_all_users()。

ProactiveEngine 自身:
  - start() 时注册 ProactiveLayer 到 CognitiveCore
  - scan_all_users() 扫描所有已知用户,产生 ProposedAction
  - propose_action() 通过 ActionConfidenceGate 评估
  - execute_action() 通过克制层后真正执行

本模块职责:
  1. 提供 B.2 默认 config 注入(apply_phase_b2_config)
  2. 提供 ProactiveEngine 状态摘要(summarize_proactive_engine)
  3. 提供 RuntimeB2Bridge 轻量包装,只读 + 防崩溃
  4. 全部 fail-soft,任何异常只记日志不影响 supervisor

约束(项目硬红线):
  - 不修改任何核心业务模块
  - 不主动发送消息给用户(ProactiveEngine 自身已通过 ActionConfidenceGate 克制)
  - 不修改 Personality / Memory / Growth 内部
  - 不启动 InitiativeBridge / ActionDispatcher(后续阶段)
  - 默认关闭,显式 enable 才启动
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Phase B.2 默认配置
# ============================================================

PHASE_B2_DEFAULT_CONFIG: Dict[str, Any] = {
    # 主开关:proactive 子模块启用
    "proactive": {
        "enabled": False,  # 默认关闭,显式 enable 才启动
        "max_history": 100,
    },
}

PHASE_B2_NAME = "phase_b2"
PHASE_B2_VERSION = "1.0.0"


# ============================================================
# 配置注入
# ============================================================

def apply_phase_b2_config(user_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 B.2 默认配置合并到用户配置中。

    规则:
      - 用户显式给定的 key 优先级最高
      - 其余用 PHASE_B2_DEFAULT_CONFIG 兜底
      - 不修改原 dict(返回新 dict)
      - 嵌套 dict 递归合并

    Args:
        user_cfg: 用户/CLI/config.yaml 提供的配置(可能为 None)

    Returns:
        合并后的新 dict,可直接传给 start_runtime 内部使用
    """
    merged: Dict[str, Any] = {}
    if isinstance(user_cfg, dict):
        merged = {k: v for k, v in user_cfg.items()}

    for k, v in PHASE_B2_DEFAULT_CONFIG.items():
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


def is_phase_b2_enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    """判断 B.2 是否在 cfg 中启用"""
    if not isinstance(cfg, dict):
        return False
    proactive = cfg.get("proactive", {})
    if not isinstance(proactive, dict):
        return False
    return bool(proactive.get("enabled", False))


def is_b2_enabled_simple(user_cfg: Optional[Dict[str, Any]]) -> bool:
    """
    简化版判定:支持 cfg["proactive_enabled"]=True 直接开关。

    优先级:
      cfg["proactive_enabled"] (顶层 bool) > cfg["proactive"]["enabled"]
    """
    if not isinstance(user_cfg, dict):
        return False
    if "proactive_enabled" in user_cfg:
        return bool(user_cfg.get("proactive_enabled"))
    return is_phase_b2_enabled(user_cfg)


# ============================================================
# 状态摘要(只读,供 Dashboard / health check)
# ============================================================

def summarize_proactive_engine(engine: Any) -> Optional[Dict[str, Any]]:
    """
    提取 ProactiveEngine 状态摘要。engine 为 None 时返回 None。

    始终 fail-soft:任何异常返回 None 并打日志,不抛出。

    返回字段:
      {
        "available": True,
        "running": bool,
        "stats": {total_proposed, executed, rejected, deferred, execution_rate, by_type},
        "pending_count": int,
        "recent_count": int,
        "gate_users": int,
      }
    """
    if engine is None:
        return None
    try:
        running = bool(getattr(engine, "_running", False))
        # stats
        stats: Dict[str, Any] = {}
        if hasattr(engine, "get_action_stats"):
            try:
                stats = engine.get_action_stats() or {}
            except Exception as e:
                logger.debug(f"[phase_b2] get_action_stats 异常(已隔离): {e}")
                stats = {}
        # pending
        pending_count = 0
        if hasattr(engine, "get_pending_actions"):
            try:
                pending_count = len(engine.get_pending_actions() or [])
            except Exception as e:
                logger.debug(f"[phase_b2] get_pending_actions 异常(已隔离): {e}")
                pending_count = -1
        # recent(总数,不去重)
        recent_count = 0
        if hasattr(engine, "get_recent_actions"):
            try:
                recent_count = len(engine.get_recent_actions(limit=10000) or [])
            except Exception as e:
                logger.debug(f"[phase_b2] get_recent_actions 异常(已隔离): {e}")
                recent_count = -1
        # gate users(有 user_preference 的数量)
        gate_users = 0
        try:
            gate = getattr(engine, "_confidence_gate", None)
            if gate is not None and hasattr(gate, "_user_preferences"):
                gate_users = len(gate._user_preferences)
        except Exception:
            gate_users = -1

        # 错误计数(最近 N 次 propose/execute 的连续失败数,本阶段不持久化,先为 0)
        errors = 0
        try:
            err_counter = getattr(engine, "_error_count", None)
            if isinstance(err_counter, int):
                errors = err_counter
        except Exception:
            errors = 0

        return {
            "available": True,
            "running": running,
            "stats": stats,
            "pending_count": pending_count,
            "recent_count": recent_count,
            "gate_users": gate_users,
            "errors": errors,
        }
    except Exception as e:
        logger.debug(f"[phase_b2] summarize_proactive_engine 异常(已隔离): {e}")
        return None


# ============================================================
# 轻量 Bridge(可选,只读 + 缓存最近一次摘要)
# ============================================================

class RuntimeB2Bridge:
    """
    Phase B.2 Bridge —— 对 ProactiveEngine 状态的轻量只读封装

    设计目的:
      - 给 start_runtime.py / Dashboard 一个稳定访问点
      - 不修改 RuntimeCore / Supervisor / ProactiveEngine
      - 只读,绝对不调用任何会触发副作用的方法
      - 缓存最近一次摘要,减少重复 get_action_stats() 调用

    用法:
        bridge = RuntimeB2Bridge(proactive_engine)
        status = bridge.get_status()
        # status["stats"]["total_proposed"]
        # status["stats"]["executed"]
    """

    def __init__(self, proactive_engine: Any) -> None:
        self._engine = proactive_engine
        self._last_summary: Optional[Dict[str, Any]] = None
        self._last_summary_ts: float = 0.0
        self._lock = threading.Lock()

    @property
    def engine(self) -> Any:
        return self._engine

    def is_enabled(self) -> bool:
        """ProactiveEngine 是否已注入并可用"""
        return self._engine is not None

    def get_status(self) -> Dict[str, Any]:
        """获取 B.2 状态摘要(1s 内复用缓存)"""
        now = time.time()
        with self._lock:
            if self._last_summary is not None and (now - self._last_summary_ts) < 1.0:
                return self._last_summary

        summary = summarize_proactive_engine(self._engine)
        if summary is None:
            # 不可用时返回稳定结构
            summary = {
                "available": False,
                "running": False,
                "stats": {},
                "pending_count": 0,
                "recent_count": 0,
                "gate_users": 0,
                "errors": 0,
            }
        else:
            summary["available"] = True

        summary["phase_b2_enabled"] = self.is_enabled()
        summary["phase_b2_name"] = PHASE_B2_NAME
        summary["phase_b2_version"] = PHASE_B2_VERSION
        summary["ts"] = now

        with self._lock:
            self._last_summary = summary
            self._last_summary_ts = now
        return summary

    def get_recent_actions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """读取最近 ProposedAction(只读,转 dict 副本)"""
        if self._engine is None or not hasattr(self._engine, "get_recent_actions"):
            return []
        try:
            actions = self._engine.get_recent_actions(limit=limit) or []
            return [a.to_dict() if hasattr(a, "to_dict") else dict(a) for a in actions]
        except Exception as e:
            logger.debug(f"[phase_b2] get_recent_actions 异常(已隔离): {e}")
            return []


# ============================================================
# 工厂
# ============================================================

def create_b2_bridge(proactive_engine: Any) -> RuntimeB2Bridge:
    """工厂:创建 RuntimeB2Bridge 实例"""
    return RuntimeB2Bridge(proactive_engine=proactive_engine)


# ============================================================
# 错误计数器辅助(可选,挂到 engine 上用于统计)
# ============================================================

def _ensure_error_counter(engine: Any) -> int:
    """确保 engine 上有 _error_count 属性,返回当前值"""
    if engine is None:
        return 0
    if not hasattr(engine, "_error_count"):
        try:
            engine._error_count = 0
        except Exception:
            return 0
    return int(getattr(engine, "_error_count", 0))


def safe_scan_all_users(engine: Any) -> Dict[str, Any]:
    """
    安全调用 scan_all_users(),隔离异常并累计错误计数。

    Returns:
        {"ok": bool, "scanned": int|None, "error": str|None}
    """
    if engine is None:
        return {"ok": False, "scanned": None, "error": "engine is None"}
    if not hasattr(engine, "scan_all_users"):
        return {"ok": False, "scanned": None, "error": "missing scan_all_users"}

    before_users = len(getattr(engine, "_user_last_active", {}) or {})
    try:
        engine.scan_all_users()
        return {"ok": True, "scanned": before_users, "error": None}
    except Exception as e:
        try:
            engine._error_count = int(getattr(engine, "_error_count", 0)) + 1
        except Exception:
            pass
        return {"ok": False, "scanned": before_users, "error": repr(e)}


__all__ = [
    "PHASE_B2_DEFAULT_CONFIG",
    "PHASE_B2_NAME",
    "PHASE_B2_VERSION",
    "apply_phase_b2_config",
    "is_phase_b2_enabled",
    "is_b2_enabled_simple",
    "summarize_proactive_engine",
    "RuntimeB2Bridge",
    "create_b2_bridge",
    "safe_scan_all_users",
    "_ensure_error_counter",
]
