#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/start_runtime.py

Phase 3.5 Runtime Startup —— 独立常驻 Runtime 进程入口

启动流程:
  load config
    ↓
  初始化 RuntimeCore(可选,从 config 读取 enabled)
    ↓
  初始化 YuyiCognitiveCore
    ↓
  初始化 HeartbeatCollector
    ↓
  初始化 RuntimeSupervisor
    ↓
  supervisor.start()
    ↓
  保持进程运行(主线程阻塞)

关闭:
  SIGINT / SIGTERM 触发 supervisor.stop()
  → runtime_core.save_lifecycle()

用法:
  python scripts/start_runtime.py
  python scripts/start_runtime.py --tick-interval 30
  python scripts/start_runtime.py --no-runtime-core  # 仅 supervisor 主循环,无业务模块

约束:
  - 不修改任何核心业务模块
  - 仅做启动编排与常驻进程
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

# 确保 src 可导入
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 日志
# ============================================================

def setup_logging(level: str = "INFO") -> None:
    """配置日志格式"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 降低一些噪声模块的日志级别
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


logger = logging.getLogger("start_runtime")


# ============================================================
# 配置加载
# ============================================================

def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    加载 config.yaml(如存在),否则返回默认配置
    """
    cfg: Dict[str, Any] = {
        "runtime": {
            "enabled": True,
            "tick_interval_seconds": 60,
            "startup_grace_seconds": 5,
            "autonomous_scheduler_enabled": False,  # 阶段 A 默认关闭
        },
        "proactive": {
            "enabled": False,  # 阶段 A 默认关闭,阶段 B 接入
        },
        "dispatcher": {
            "enabled": False,  # 阶段 B.3 默认关闭
            "max_per_tick": 3,
        },
        "initiative": {
            "enabled": False,  # 阶段 A 默认关闭
        },
        "heartbeat": {
            "enabled": True,
            "timeout_seconds": 120,
        },
        "governance": {
            "enabled": False,             # 阶段 B.4 默认关闭
            "max_retry": 0,
            "timeout_seconds": 30,
            "cooldown_seconds": 0,
            "duplicate_detection": True,
            "audit_enabled": True,
        },
        "action_persistence": {
            "enabled": True,              # 阶段 B.5 默认开启(但不阻塞)
            "path": "data/action_lifecycle.jsonl",
            "max_bytes": 10 * 1024 * 1024,
            "max_records": 50000,
            "auto_recover": True,
        },
    }

    if config_path is None:
        config_path = str(PROJECT_ROOT / "config.yaml")
    if not os.path.exists(config_path):
        logger.info(f"config.yaml 未找到({config_path}),使用默认配置")
        return cfg

    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        # 合并到 cfg(loaded 优先)
        for k, v in loaded.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
        logger.info(f"已加载配置: {config_path}")
    except Exception as e:
        logger.warning(f"加载 config.yaml 失败,使用默认配置: {e}")

    return cfg


# ============================================================
# 组件初始化(失败时降级,不影响 supervisor 启动)
# ============================================================

def init_runtime_core(cfg: Dict[str, Any]) -> Any:
    """尝试构造 RuntimeCore(失败返回 None)

    Phase B.1 接入点:
      - 当 cfg["runtime"]["scheduler_enabled"] 或 cfg["autonomous_scheduler_enabled"] = True
        时,通过 apply_phase_b1_config() 注入 AutonomousScheduler 所需配置
      - 不修改 RuntimeCore 任何内部实现
    """
    runtime_cfg = cfg.get("runtime", {})
    if not runtime_cfg.get("enabled", True):
        logger.info("[init] runtime.enabled=false,跳过 RuntimeCore 构造")
        return None

    try:
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.phase_b_integration import (
            apply_phase_b1_config,
            is_phase_b1_enabled,
        )

        # B.1 启用检测:CLI flag 或 config key 任一为 true 即可
        b1_enabled = bool(
            runtime_cfg.get("scheduler_enabled", False)
            or cfg.get("autonomous_scheduler_enabled", False)
            or runtime_cfg.get("autonomous_scheduler_enabled", False)
        )

        # 当 B.1 启用时,autonomous_scheduler_enabled 必须为 True
        # 优先用 cfg 里的显式值,否则默认为 True
        if b1_enabled:
            base_config: Dict[str, Any] = {
                "autonomous_scheduler_enabled": bool(
                    runtime_cfg.get("autonomous_scheduler_enabled", False)
                    or cfg.get("autonomous_scheduler_enabled", False)
                    or True  # B.1 开启时强制 True
                ),
            }
            base_config = apply_phase_b1_config(base_config)
            logger.info(
                "[init][phase_b1] AutonomousScheduler 已启用 "
                f"(memory={base_config.get('as_memory_interval_ticks')}, "
                f"identity={base_config.get('as_identity_interval_ticks')}, "
                f"growth_eval={base_config.get('as_growth_eval_interval_ticks')}, "
                f"reflection={base_config.get('as_reflection_interval_ticks')})"
            )
        else:
            base_config = {
                "autonomous_scheduler_enabled": runtime_cfg.get(
                    "autonomous_scheduler_enabled", False
                ),
            }
            logger.info("[init][phase_b1] AutonomousScheduler 未启用(B.1 off)")

        rc = RuntimeCore(config=base_config)

        # B.1 状态校验(启动后 sanity check)
        if b1_enabled:
            actual_scheduler = getattr(rc, "autonomous_scheduler", None)
            if actual_scheduler is not None:
                logger.info("[init][phase_b1] RuntimeCore.autonomous_scheduler 已就绪")
            else:
                logger.warning("[init][phase_b1] scheduler 配置已注入但未创建(检查 RuntimeCore 内部日志)")

        logger.info("[init] RuntimeCore 已构造")
        return rc
    except Exception as e:
        logger.warning(f"[init] RuntimeCore 构造失败(已隔离): {e}")
        return None


def init_cognitive_core() -> Any:
    """尝试构造 YuyiCognitiveCore(失败返回 None)"""
    try:
        from src.core.yuyi_cognitive_core import YuyiCognitiveCore
        cc = YuyiCognitiveCore.get_instance()
        started = cc.start()
        if started:
            logger.info("[init] YuyiCognitiveCore 已启动")
        else:
            logger.info("[init] YuyiCognitiveCore 已在运行")
        return cc
    except Exception as e:
        logger.warning(f"[init] YuyiCognitiveCore 构造失败(已隔离): {e}")
        return None


def init_heartbeat_collector(cfg: Dict[str, Any]) -> Any:
    """尝试获取 HeartbeatCollector 单例"""
    heartbeat_cfg = cfg.get("heartbeat", {})
    if not heartbeat_cfg.get("enabled", True):
        logger.info("[init] heartbeat.enabled=false,跳过 HeartbeatCollector")
        return None

    try:
        from src.core.heartbeat import HeartbeatCollector
        timeout = float(heartbeat_cfg.get("timeout_seconds", 120))
        hc = HeartbeatCollector.get_instance(timeout=timeout)
        logger.info(f"[init] HeartbeatCollector 已就绪(timeout={timeout}s)")
        return hc
    except Exception as e:
        logger.warning(f"[init] HeartbeatCollector 初始化失败(已隔离): {e}")
        return None


def init_proactive_engine(cfg: Dict[str, Any], force: bool = False) -> Any:
    """
    Phase A: 默认不构造 ProactiveEngine
    Phase B.2: 通过 cfg["proactive"]["enabled"]=true 或 force=True 启用

    Args:
        cfg: 完整配置 dict
        force: 显式 force 启动(用于 --enable-proactive CLI)
    """
    if not force and not cfg.get("proactive", {}).get("enabled", False):
        return None
    try:
        from src.proactive.proactive_engine import ProactiveEngine
        pe = ProactiveEngine()
        ok = pe.start()
        if ok:
            logger.info("[init][phase_b2] ProactiveEngine enabled")
        else:
            logger.warning("[init][phase_b2] ProactiveEngine start() returned False")
        return pe
    except Exception as e:
        logger.warning(f"[init] ProactiveEngine 启动失败(已隔离): {e}")
        return None


def init_action_dispatcher(cfg: Dict[str, Any], force: bool = False) -> Any:
    """
    Phase B.3: ActionDispatcher 接入(默认关闭)

    仅当 cfg["dispatcher"]["enabled"]=true 或 force=True 时构造并 start。
    不注册任何默认 handler(避免自动外发)。

    Args:
        cfg: 完整配置 dict
        force: 显式 force 启动(用于 --enable-dispatcher CLI)
    """
    if not force and not cfg.get("dispatcher", {}).get("enabled", False):
        return None
    try:
        from src.runtime.action_dispatcher import ActionDispatcher
        dispatcher = ActionDispatcher()
        logger.info("[init][phase_b3] ActionDispatcher enabled (no default handler registered)")
        return dispatcher
    except Exception as e:
        logger.warning(f"[init][phase_b3] ActionDispatcher 构造失败(已隔离): {e}")
        return None


def init_governance(
    cfg: Dict[str, Any],
    b3_bridge: Any,
    force: bool = False,
    persistence: Any = None,
) -> Any:
    """
    Phase B.4: Action Execution Governance(默认关闭)

    包装 RuntimeB3Bridge,加 governance + audit 钩子。
    仅当 cfg["governance"]["enabled"]=true 或 force=True 时构造。
    不修改任何核心模块;不自动发消息(handler 未注册 → B.3 行为)。

    Args:
        cfg: 完整配置 dict
        b3_bridge: 必填;RuntimeB3Bridge 实例(必须存在)
        force: 显式 force 启动(用于 --enable-governance CLI)
        persistence: Phase B.5 持久化管理器(可选,注入 B.4 生命周期)

    Returns:
        RuntimeB4Bridge 实例,或 None(governance 未启用 / b3_bridge 缺失)
    """
    gov_cfg = cfg.get("governance", {}) or {}
    if not isinstance(gov_cfg, dict):
        gov_cfg = {}

    if not force and not gov_cfg.get("enabled", False):
        logger.info("[init][phase_b4] governance.enabled=false,跳过 RuntimeB4Bridge")
        return None

    if b3_bridge is None:
        logger.warning(
            "[init][phase_b4] governance 启用但 b3_bridge=None,"
            "无受控链路可治理,跳过(已隔离)"
        )
        return None

    try:
        from src.runtime.phase_b4_integration import create_b4_bridge

        # 1) 准备 RuntimeAuditLogger(若审计启用)
        audit_logger = None
        if gov_cfg.get("audit_enabled", True):
            try:
                from src.runtime.audit_log.runtime_audit_logger import RuntimeAuditLogger
                audit_log_path = gov_cfg.get(
                    "audit_log_path", "data/action_governance_audit.jsonl"
                )
                audit_logger = RuntimeAuditLogger(log_path=audit_log_path)
                logger.info(
                    f"[init][phase_b4] RuntimeAuditLogger ready "
                    f"(path={audit_log_path})"
                )
            except Exception as e:
                logger.warning(
                    f"[init][phase_b4] RuntimeAuditLogger 初始化失败(已隔离,审计关闭): {e}"
                )
                audit_logger = None

        # 2) 构造 RuntimeB4Bridge(注入 persistence)
        b4_bridge = create_b4_bridge(
            inner_bridge=b3_bridge,
            cfg=cfg,
            audit_logger=audit_logger,
            persistence=persistence,
        )
        gov_state = "ON" if b4_bridge.is_governance_enabled() else "OFF"
        logger.info(
            f"[init][phase_b4] governance enabled (state={gov_state}), "
            f"policy={b4_bridge.policy.to_dict()}, "
            f"audit={'ON' if (b4_bridge.audit and b4_bridge.audit.is_enabled()) else 'OFF'}"
        )
        return b4_bridge
    except Exception as e:
        logger.warning(f"[init][phase_b4] RuntimeB4Bridge 构造失败(已隔离): {e}")
        return None


def init_persistence(cfg: Dict[str, Any]) -> Any:
    """
    Phase B.5: Action Lifecycle 持久化(默认开启但不阻塞)

    构造 ActionPersistenceManager,自动从 JSONL load_state 恢复。
    写盘失败 → 自动降级为 memory-only,不抛错。
    """
    p_cfg = cfg.get("action_persistence", {}) or {}
    if not isinstance(p_cfg, dict):
        p_cfg = {}

    if not p_cfg.get("enabled", True):
        logger.info("[init][persistence] persistence disabled (opt-out)")
        return None

    try:
        from src.runtime.action_persistence import create_persistence_manager
        auto_recover = bool(p_cfg.get("auto_recover", True))
        mgr = create_persistence_manager(cfg=cfg, auto_recover=auto_recover)

        # 启动日志
        h = mgr.health_check()
        logger.info(
            f"[Yuyi Runtime][persistence] action lifecycle storage ready "
            f"(path={h.get('path')}, in_memory={h.get('in_memory_actions')}, "
            f"recovered={h.get('recover_count')}, degraded={h.get('degraded')})"
        )
        return mgr
    except Exception as e:
        logger.warning(f"[init][persistence] 构造失败(已隔离,memory-only): {e}")
        return None


# ============================================================
# 状态打印
# ============================================================

def print_status_loop(supervisor: Any, interval_seconds: float = 30.0,
                     b1_bridge: Any = None, b2_bridge: Any = None,
                     b3_bridge: Any = None, b4_bridge: Any = None,
                     persistence: Any = None) -> Any:
    """
    周期性打印 supervisor 状态(独立线程)

    保持主线程活跃,便于观察
    """
    stop_event = threading.Event()

    def _loop() -> None:
        while not stop_event.is_set():
            try:
                status = supervisor.get_status()
                logger.info(
                    f"[Yuyi Runtime] status: running={status['running']}, "
                    f"ticks={status['tick_count']}, errors={status['error_count']}, "
                    f"uptime={status['uptime']}s"
                )
                # Phase B.1 状态
                if b1_bridge is not None:
                    try:
                        b1 = b1_bridge.get_status()
                        if b1.get("phase_b1_enabled"):
                            sched = b1.get("scheduler") or {}
                            snap = sched.get("snapshot") or {}
                            logger.info(
                                f"[Yuyi Runtime][phase_b1] scheduler: "
                                f"total_tasks={snap.get('total_tasks', 0)}, "
                                f"executed={snap.get('executed_tasks', 0)}, "
                                f"failed={snap.get('failed_tasks', 0)}, "
                                f"last={snap.get('last_task_type', '-')}"
                            )
                    except Exception as e:
                        logger.debug(f"[status_loop][phase_b1] 异常(已隔离): {e}")
                # Phase B.2 状态
                if b2_bridge is not None:
                    try:
                        b2 = b2_bridge.get_status()
                        if b2.get("phase_b2_enabled"):
                            stats = b2.get("stats") or {}
                            logger.info(
                                f"[Yuyi Runtime][phase_b2] proactive: "
                                f"proposed={stats.get('total_proposed', 0)}, "
                                f"executed={stats.get('executed', 0)}, "
                                f"rejected={stats.get('rejected', 0)}, "
                                f"deferred={stats.get('deferred', 0)}, "
                                f"pending={b2.get('pending_count', 0)}"
                            )
                    except Exception as e:
                        logger.debug(f"[status_loop][phase_b2] 异常(已隔离): {e}")
                # Phase B.3 状态
                if b3_bridge is not None:
                    try:
                        b3 = b3_bridge.summarize_b3()
                        if b3.get("enabled"):
                            logger.info(
                                f"[Yuyi Runtime][phase_b3] dispatcher: "
                                f"enabled={b3.get('enabled')}, "
                                f"pending={b3.get('pending', 0)}, "
                                f"processed={b3.get('processed', 0)}, "
                                f"dispatched={b3.get('dispatched', 0)}, "
                                f"rejected={b3.get('rejected', 0)}, "
                                f"skipped={b3.get('skipped', 0)}, "
                                f"errors={b3.get('errors', 0)}"
                            )
                    except Exception as e:
                        logger.debug(f"[status_loop][phase_b3] 异常(已隔离): {e}")
                # Phase B.4 状态
                if b4_bridge is not None:
                    try:
                        b4 = b4_bridge.summarize_b4()
                        if b4.get("governance_on"):
                            audit = b4.get("audit") or {}
                            pers = b4.get("persistence") or {}
                            logger.info(
                                f"[Yuyi Runtime][phase_b4] governance: "
                                f"governance={b4.get('governance_on')}, "
                                f"ledger={b4.get('ledger_count', 0)}, "
                                f"audited={b4.get('total_audited', 0)}, "
                                f"duplicates={b4.get('total_duplicates_blocked', 0)}, "
                                f"errors={b4.get('total_errors', 0)}, "
                                f"audit_writes={audit.get('write_count', 0)}, "
                                f"persist_writes={pers.get('write_count', 0)}, "
                                f"persist_degraded={pers.get('degraded', False)}"
                            )
                    except Exception as e:
                        logger.debug(f"[status_loop][phase_b4] 异常(已隔离): {e}")
                # Phase B.5 状态(仅当 b4_bridge 不存在时单独打印)
                if b4_bridge is None and persistence is not None:
                    try:
                        h = persistence.health_check()
                        logger.info(
                            f"[Yuyi Runtime][persistence] action_lifecycle: "
                            f"enabled={h.get('enabled')}, "
                            f"in_memory={h.get('in_memory_actions')}, "
                            f"writes={h.get('write_count')}, "
                            f"errors={h.get('write_error_count')}, "
                            f"recovered={h.get('recover_count')}, "
                            f"degraded={h.get('degraded')}"
                        )
                    except Exception as e:
                        logger.debug(f"[status_loop][persistence] 异常(已隔离): {e}")
            except Exception as e:
                logger.debug(f"[status_loop] 异常(已隔离): {e}")

            if stop_event.wait(timeout=interval_seconds):
                break

    t = threading.Thread(target=_loop, name="status-printer", daemon=True)
    t.start()
    return stop_event


# ============================================================
# 主流程
# ============================================================

def main() -> int:
    """Runtime 启动入口"""
    parser = argparse.ArgumentParser(description="QianWuYuyi-AI Runtime 常驻监督器")
    parser.add_argument(
        "--config", type=str, default=None,
        help="配置文件路径(默认: config.yaml)",
    )
    parser.add_argument(
        "--tick-interval", type=int, default=None,
        help="tick 间隔(秒),覆盖 config",
    )
    parser.add_argument(
        "--startup-grace", type=float, default=None,
        help="启动 grace(秒),覆盖 config",
    )
    parser.add_argument(
        "--no-runtime-core", action="store_true",
        help="不构造 RuntimeCore(纯 supervisor 模式)",
    )
    parser.add_argument(
        "--no-cognitive-core", action="store_true",
        help="不构造 YuyiCognitiveCore",
    )
    parser.add_argument(
        "--no-heartbeat", action="store_true",
        help="不构造 HeartbeatCollector",
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别",
    )
    parser.add_argument(
        "--enable-scheduler", action="store_true",
        help="[Phase B.1] 启用 AutonomousScheduler(memory/identity/growth/reflection 周期任务)",
    )
    parser.add_argument(
        "--enable-proactive", action="store_true",
        help="[Phase B.2] 启用 ProactiveEngine(周期性扫描用户,生成主动行为建议)",
    )
    parser.add_argument(
        "--enable-dispatcher", action="store_true",
        help="[Phase B.3] 启用 ActionDispatcher(消费 ProactiveEngine pending,默认不注册 handler)",
    )
    parser.add_argument(
        "--enable-governance", action="store_true",
        help="[Phase B.4] 启用 Action Execution Governance(包装 B.3 bridge,加 lifecycle/audit 治理)",
    )
    args = parser.parse_args()

    setup_logging(args.log_level)
    logger.info("=" * 60)
    logger.info("[Yuyi Runtime] starting...")
    logger.info("=" * 60)

    # 1. load config
    cfg = load_config(args.config)

    # CLI 参数覆盖
    runtime_cfg = cfg.setdefault("runtime", {})
    if args.tick_interval is not None:
        runtime_cfg["tick_interval_seconds"] = args.tick_interval
    if args.startup_grace is not None:
        runtime_cfg["startup_grace_seconds"] = args.startup_grace
    if args.no_runtime_core:
        runtime_cfg["enabled"] = False
    if args.enable_scheduler:
        runtime_cfg["scheduler_enabled"] = True
    if args.enable_proactive:
        cfg.setdefault("proactive", {})["enabled"] = True
        logger.info("[Yuyi Runtime][phase_b2] ProactiveEngine enabled (via --enable-proactive)")
    if args.enable_dispatcher:
        cfg.setdefault("dispatcher", {})["enabled"] = True
        logger.info("[Yuyi Runtime][phase_b3] ActionDispatcher enabled (via --enable-dispatcher)")
    if args.enable_governance:
        cfg.setdefault("governance", {})["enabled"] = True
        logger.info("[Yuyi Runtime][phase_b4] governance enabled (via --enable-governance)")

    tick_interval = int(runtime_cfg.get("tick_interval_seconds", 60))
    startup_grace = float(runtime_cfg.get("startup_grace_seconds", 5))
    logger.info(
        f"[init] tick_interval={tick_interval}s, "
        f"startup_grace={startup_grace}s"
    )

    # 2. 初始化 RuntimeCore
    runtime_core = None
    if not args.no_runtime_core:
        runtime_core = init_runtime_core(cfg)
    else:
        logger.info("[init] --no-runtime-core 模式,跳过 RuntimeCore")

    # 3. 初始化 CognitiveCore
    cognitive_core = None
    if not args.no_cognitive_core:
        cognitive_core = init_cognitive_core()
    else:
        logger.info("[init] --no-cognitive-core 模式,跳过 CognitiveCore")

    # 4. 初始化 HeartbeatCollector
    heartbeat = None
    if not args.no_heartbeat:
        heartbeat = init_heartbeat_collector(cfg)
    else:
        logger.info("[init] --no-heartbeat 模式,跳过 HeartbeatCollector")

    # 5. 初始化 ProactiveEngine(阶段 A 默认 None,阶段 B.2 通过 CLI/config 启用)
    try:
        from src.runtime.phase_b2_integration import apply_phase_b2_config
        cfg = apply_phase_b2_config(cfg)
    except Exception as _e:
        logger.warning(f"[init][phase_b2] apply_phase_b2_config 失败(已隔离): {_e}")
    proactive = init_proactive_engine(cfg, force=args.enable_proactive)

    # 5.5 初始化 ActionDispatcher(阶段 B.3 通过 CLI/config 启用)
    try:
        from src.runtime.phase_b3_integration import apply_phase_b3_config
        cfg = apply_phase_b3_config(cfg)
    except Exception as _e:
        logger.warning(f"[init][phase_b3] apply_phase_b3_config 失败(已隔离): {_e}")
    dispatcher = init_action_dispatcher(cfg, force=args.enable_dispatcher)

    # 5.6 构造 RuntimeB3Bridge(只在 B.2 + B.3 都启用时构造;否则不连)
    b3_bridge = None
    try:
        from src.runtime.phase_b3_integration import create_b3_bridge
        if proactive is not None and dispatcher is not None:
            max_per_tick = int(cfg.get("dispatcher", {}).get("max_per_tick", 3))
            b3_bridge = create_b3_bridge(
                proactive_engine=proactive,
                action_dispatcher=dispatcher,
                max_per_tick=max_per_tick,
            )
            logger.info(
                f"[Yuyi Runtime][phase_b3] bridge ready "
                f"(engine=attached, dispatcher=attached, max_per_tick={max_per_tick})"
            )
        else:
            logger.info(
                f"[Yuyi Runtime][phase_b3] bridge not created "
                f"(engine={proactive is not None}, dispatcher={dispatcher is not None})"
            )
    except Exception as e:
        logger.warning(f"[Yuyi Runtime][phase_b3] bridge 创建失败(已隔离): {e}")
        b3_bridge = None

    # 5.7 构造 RuntimeB4Bridge(默认关闭;--enable-governance 或 cfg 启用)
    b4_bridge = None
    try:
        from src.runtime.phase_b4_integration import (
            apply_phase_b4_config,
            is_phase_b4_enabled,
        )
        cfg = apply_phase_b4_config(cfg)
        if is_phase_b4_enabled(cfg) or args.enable_governance:
            b4_bridge = init_governance(
                cfg, b3_bridge=b3_bridge, force=args.enable_governance,
                persistence=persistence,
            )
            if b4_bridge is not None:
                logger.info("[Yuyi Runtime][phase_b4] governance enabled")
        else:
            logger.info("[Yuyi Runtime][phase_b4] governance disabled (opt-in)")
    except Exception as e:
        logger.warning(f"[Yuyi Runtime][phase_b4] bridge 创建失败(已隔离): {e}")
        b4_bridge = None

    # 5.8 构造 ActionPersistenceManager(B.5 持久化;默认开启,失败降级)
    persistence = None
    try:
        from src.runtime.action_persistence import (
            apply_phase_b5_config,
            is_phase_b5_enabled,
        )
        cfg = apply_phase_b5_config(cfg)
        if is_phase_b5_enabled(cfg):
            persistence = init_persistence(cfg)
    except Exception as e:
        logger.warning(f"[Yuyi Runtime][persistence] bridge 创建失败(已隔离): {e}")
        persistence = None

    # 6. 构造 RuntimeSupervisor
    from src.runtime.supervisor import RuntimeSupervisor

    # 关键注入点:Supervisor 只看 flush_pending()
    # - B.3 默认: action_dispatcher=b3_bridge
    # - B.4 启用: action_dispatcher=b4_bridge(包 b3_bridge)
    # - B.4 关闭但 b3 启用: action_dispatcher=b3_bridge
    final_action_dispatcher = b4_bridge if b4_bridge is not None else b3_bridge

    supervisor = RuntimeSupervisor(
        runtime_core=runtime_core,
        cognitive_core=cognitive_core,
        heartbeat_collector=heartbeat,
        proactive_engine=proactive,
        action_dispatcher=final_action_dispatcher,  # 阶段 B.3/B.4 注入(可能为 None)
        tick_interval_seconds=tick_interval,
        startup_grace_seconds=startup_grace,
        enabled=True,
        name="runtime_supervisor",
    )

    # 7. 启动 supervisor
    started = supervisor.start()
    if not started:
        logger.error("[Yuyi Runtime] supervisor 启动失败")
        return 1
    logger.info("[Yuyi Runtime] supervisor started")
    logger.info(
        f"[Yuyi Runtime] waiting for first tick "
        f"(grace {startup_grace}s + tick_interval {tick_interval}s)..."
    )

    # 7.5 Phase B.1 Bridge(只读,用于日志/Dashboard)
    b1_bridge = None
    try:
        from src.runtime.phase_b_integration import create_b1_bridge
        b1_bridge = create_b1_bridge(runtime_core)
        b1_status = b1_bridge.get_status()
        if b1_status.get("phase_b1_enabled"):
            logger.info(
                f"[Yuyi Runtime][phase_b1] bridge ready, "
                f"scheduler_available={b1_status.get('scheduler_available')}, "
                f"history_count={b1_status.get('scheduler', {}).get('history_count', 0) if b1_status.get('scheduler') else 0}"
            )
        else:
            logger.info("[Yuyi Runtime][phase_b1] bridge ready (B.1 not enabled)")
    except Exception as e:
        logger.warning(f"[Yuyi Runtime][phase_b1] bridge 创建失败(已隔离): {e}")
        b1_bridge = None

    # 7.6 Phase B.2 Bridge(只读,用于日志/Dashboard)
    b2_bridge = None
    try:
        from src.runtime.phase_b2_integration import create_b2_bridge
        b2_bridge = create_b2_bridge(proactive)
        if b2_bridge.is_enabled():
            b2_status = b2_bridge.get_status()
            logger.info(
                f"[Yuyi Runtime][phase_b2] bridge ready, "
                f"running={b2_status.get('running')}, "
                f"pending={b2_status.get('pending_count', 0)}, "
                f"recent={b2_status.get('recent_count', 0)}"
            )
        else:
            logger.info("[Yuyi Runtime][phase_b2] bridge ready (B.2 not enabled)")
    except Exception as e:
        logger.warning(f"[Yuyi Runtime][phase_b2] bridge 创建失败(已隔离): {e}")
        b2_bridge = None

    # 8. 启动状态打印线程
    status_stop = print_status_loop(
        supervisor, interval_seconds=30.0,
        b1_bridge=b1_bridge, b2_bridge=b2_bridge, b3_bridge=b3_bridge,
        b4_bridge=b4_bridge, persistence=persistence,
    )

    # 9. 主线程阻塞,直到收到信号
    try:
        # 用 threading.Event + 主线程无限等待
        stop_event = threading.Event()
        try:
            # 注册额外信号 handler,触发 stop_event
            import signal as _sig
            def _sig_handler(signum: int, frame: Any) -> None:
                logger.info(f"[Yuyi Runtime] 主线程收到信号 {signum},触发退出")
                stop_event.set()
            _sig.signal(_sig.SIGINT, _sig_handler)
            _sig.signal(_sig.SIGTERM, _sig_handler)
        except Exception:
            pass

        # 主线程阻塞
        while not stop_event.is_set():
            stop_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        logger.info("[Yuyi Runtime] KeyboardInterrupt,准备退出")
    finally:
        # 10. 退出
        status_stop.set()
        supervisor.stop(timeout=5.0)
        logger.info("[Yuyi Runtime] exited cleanly")
        # 给一个最终状态
        try:
            final = supervisor.get_status()
            logger.info(
                f"[Yuyi Runtime] final status: "
                f"total_ticks={final['tick_count']}, errors={final['error_count']}, "
                f"uptime={final['uptime']}s"
            )
        except Exception:
            pass
        # Phase B.1 最终状态
        if b1_bridge is not None:
            try:
                b1_final = b1_bridge.get_status()
                sched = b1_final.get("scheduler") or {}
                snap = sched.get("snapshot") or {}
                logger.info(
                    f"[Yuyi Runtime][phase_b1] final: "
                    f"total_tasks={snap.get('total_tasks', 0)}, "
                    f"executed={snap.get('executed_tasks', 0)}, "
                    f"failed={snap.get('failed_tasks', 0)}"
                )
            except Exception:
                pass
        # Phase B.2 最终状态
        if b2_bridge is not None:
            try:
                b2_final = b2_bridge.get_status()
                stats = b2_final.get("stats") or {}
                logger.info(
                    f"[Yuyi Runtime][phase_b2] final: "
                    f"proposed={stats.get('total_proposed', 0)}, "
                    f"executed={stats.get('executed', 0)}, "
                    f"rejected={stats.get('rejected', 0)}, "
                    f"pending={b2_final.get('pending_count', 0)}"
                )
            except Exception:
                pass
        # Phase B.3 最终状态
        if b3_bridge is not None:
            try:
                b3_final = b3_bridge.summarize_b3()
                logger.info(
                    f"[Yuyi Runtime][phase_b3] final: "
                    f"enabled={b3_final.get('enabled')}, "
                    f"processed={b3_final.get('processed', 0)}, "
                    f"dispatched={b3_final.get('dispatched', 0)}, "
                    f"rejected={b3_final.get('rejected', 0)}, "
                    f"skipped={b3_final.get('skipped', 0)}, "
                    f"errors={b3_final.get('errors', 0)}"
                )
            except Exception:
                pass
        # Phase B.4 最终状态
        if b4_bridge is not None:
            try:
                b4_final = b4_bridge.summarize_b4()
                audit_final = b4_final.get("audit") or {}
                pers_final = b4_final.get("persistence") or {}
                logger.info(
                    f"[Yuyi Runtime][phase_b4] final: "
                    f"governance={b4_final.get('governance_on')}, "
                    f"ledger={b4_final.get('ledger_count', 0)}, "
                    f"audited={b4_final.get('total_audited', 0)}, "
                    f"duplicates={b4_final.get('total_duplicates_blocked', 0)}, "
                    f"errors={b4_final.get('total_errors', 0)}, "
                    f"audit_writes={audit_final.get('write_count', 0)}, "
                    f"persist_writes={pers_final.get('write_count', 0)}, "
                    f"persist_degraded={pers_final.get('degraded', False)}"
                )
            except Exception:
                pass
        # Phase B.5 最终状态
        if persistence is not None:
            try:
                h = persistence.health_check()
                logger.info(
                    f"[Yuyi Runtime][persistence] final: "
                    f"enabled={h.get('enabled')}, "
                    f"in_memory={h.get('in_memory_actions')}, "
                    f"writes={h.get('write_count')}, "
                    f"errors={h.get('write_error_count')}, "
                    f"recovered={h.get('recover_count')}, "
                    f"degraded={h.get('degraded')}, "
                    f"path={h.get('path')}"
                )
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
