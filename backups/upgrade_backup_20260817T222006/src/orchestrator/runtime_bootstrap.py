# -*- coding: utf-8 -*-
"""
src/orchestrator/runtime_bootstrap.py

Phase 5.0-C: RuntimeBootstrap —— Runtime 生命周期自动组装与恢复协调器。

职责:
- 一次性组装 Runtime 生命周期依赖(PR / SMO / Audit / LongLoop)
- 把 SelfModelOrchestrator 注入到已存在的 Orchestrator
- 构造 LongLoop(注入 audit_logger 与 checkpoint_provider)
- 启动期:PR.initialize + restore_on_startup + RuntimeSnapshot 加载/初始化 + 审计
- 退出时:LongLoop 通过 checkpoint_provider 落 RuntimeSnapshot

边界(严格遵守):
- 不修改 Orchestrator / ResponseEngine / RuntimeCore / LongLoop / SMO / PR / Audit 任何代码
- 不接管事件循环;不创建 EventBus / MessageQueue / Scheduler / RuntimeBuilder
- 不构造业务 MemoryStore / VectorMemory / PersonalityResolver(由 Orchestrator 通过 RuntimeBridge 拿)
- 不调用 RuntimeBridge.initialize()
- 不引入 LLM SDK
- 全部构造/注入异常隔离,不阻塞主流程
- 启动期不抛,失败时返回 None

依赖关系:
    RuntimeBootstrap → PersistenceRuntime / SelfModelOrchestrator /
                       RuntimeAuditLogger / LongLoop /
                       RuntimeSnapshot / RuntimeSnapshotStore / RuntimeSnapshotBuilder
    反向引用:无
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Callable, Dict, Optional


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
RUNTIME_BOOTSTRAP_SCHEMA_VERSION = "1.0"

DEFAULT_IDENTITY_ID = "yuyi_default"
DEFAULT_AUDIT_LOG_PATH = "data/runtime_audit.jsonl"
DEFAULT_RUNTIME_SNAPSHOT_PATH = "data/runtime_snapshot.json"
DEFAULT_CHECKPOINT_INTERVAL = 10


# ============================================================
# 辅助
# ============================================================
def _now_iso() -> str:
    """返回 ISO 8601 UTC 时间字符串。失败回退到 naive utcnow。"""
    try:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        try:
            from datetime import datetime as _dt
            return _dt.utcnow().isoformat() + "Z"
        except Exception:  # noqa: BLE001
            return ""


def _safe_str(value: Any, max_len: int = 240) -> str:
    """安全字符串化,过长截断。"""
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _safe_int(value: Any, default: int = 0) -> int:
    """安全 int 转换。"""
    try:
        return int(value or 0)
    except Exception:  # noqa: BLE001
        return default


# ============================================================
# RuntimeBootstrap
# ============================================================
class RuntimeBootstrap:
    """Runtime 自动组装与启动/恢复协调器(Phase 5.0-C / v1.0)。

    使用方式:
        bootstrap = RuntimeBootstrap(orchestrator=orch)
        loop = bootstrap.start()
        loop.run()                    # 阻塞直到 shutdown

    或手动两步:
        bootstrap.configure()         # 一次性组装(可选,start() 会自动调用)
        loop = bootstrap.start(
            input_provider=lambda: input("你: "),
            on_reply=lambda u, r: print("羽依:", r),
            on_goodbye=lambda: print("bye"),
        )
        loop.run()

    关键不变量:
    - 不修改 Orchestrator / ResponseEngine / RuntimeCore 任何代码
    - 不创建 EventBus / MessageQueue / Scheduler
    - 全部异常隔离(构造期 + 启动期 + checkpoint_provider)
    - 启动期不抛;失败时返回 None + 记录 self._start_error
    - configure() / start() 幂等;重复调用不重建
    - 线程安全(RLock)
    - LongLoop 退出时会通过 checkpoint_provider 自动落 RuntimeSnapshot

    字段约定:
    - identity_id: 根标识(同时绑定到 PR / SMO / Snapshot)
    - audit_log_path: RuntimeAuditLogger 的落盘路径
    - runtime_snapshot_path: RuntimeSnapshot 的落盘路径(JSON)
    - checkpoint_interval: LongLoop 周期 checkpoint 触发间隔(轮)
    """

    name: str = "runtime_bootstrap"
    schema_version: str = RUNTIME_BOOTSTRAP_SCHEMA_VERSION

    def __init__(
        self,
        orchestrator: Any,
        identity_id: str = DEFAULT_IDENTITY_ID,
        audit_log_path: str = DEFAULT_AUDIT_LOG_PATH,
        runtime_snapshot_path: str = DEFAULT_RUNTIME_SNAPSHOT_PATH,
        checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL,
        persistence_runtime: Optional[Any] = None,
        self_model_orchestrator: Optional[Any] = None,
        audit_logger: Optional[Any] = None,
    ) -> None:
        """构造 RuntimeBootstrap。

        参数:
        - orchestrator: 必传。已存在的 Orchestrator 实例。
        - identity_id: 根标识(默认 "yuyi_default")。
        - audit_log_path: RuntimeAuditLogger 落盘路径。
        - runtime_snapshot_path: RuntimeSnapshot 落盘路径(JSON)。
        - checkpoint_interval: LongLoop 周期 checkpoint 间隔(0 = 仅 final checkpoint)。
        - persistence_runtime: 可选,预构造的 PersistenceRuntime(默认 None → 内部自建)。
        - self_model_orchestrator: 可选,预构造的 SMO(默认 None → 内部自建并注入 PR)。
        - audit_logger: 可选,预构造的 RuntimeAuditLogger(默认 None → 内部自建)。

        异常:
        - orchestrator 为 None → ValueError(必传)
        """
        if orchestrator is None:
            raise ValueError("RuntimeBootstrap: orchestrator is required")

        self._lock = threading.RLock()

        # 必传
        self._orchestrator: Any = orchestrator
        self._identity_id: str = str(identity_id or DEFAULT_IDENTITY_ID)

        # 路径/配置
        self._audit_log_path: str = str(audit_log_path or DEFAULT_AUDIT_LOG_PATH)
        self._snapshot_path: str = str(runtime_snapshot_path or DEFAULT_RUNTIME_SNAPSHOT_PATH)
        self._checkpoint_interval: int = max(0, int(checkpoint_interval or 0))

        # 可选注入(默认 None → configure() 时自建)
        self._persistence_injected: Optional[Any] = persistence_runtime
        self._smo_injected: Optional[Any] = self_model_orchestrator
        self._audit_injected: Optional[Any] = audit_logger

        # 内部组件(configure() 之后填充)
        self._persistence: Optional[Any] = None
        self._self_model_orchestrator: Optional[Any] = None
        self._audit: Optional[Any] = None
        self._loop: Optional[Any] = None
        self._checkpoint_provider: Optional[Callable[[Dict[str, Any]], None]] = None

        # Snapshot 相关(由 runtime_snapshot 模块管理)
        self._snapshot_store: Optional[Any] = None
        self._snapshot_builder: Optional[Any] = None
        self._snapshot: Optional[Any] = None
        self._boot_mode: str = "fresh"  # fresh | restore
        self._snapshot_write_count: int = 0
        self._snapshot_read_count: int = 0

        # 状态机
        self._configured: bool = False
        self._started: bool = False
        self._configure_error: Optional[str] = None
        self._start_error: Optional[str] = None

    # --------------------------------------------------------
    # 公开属性(只读,线程安全)
    # --------------------------------------------------------
    @property
    def identity_id(self) -> str:
        with self._lock:
            return self._identity_id

    @property
    def is_configured(self) -> bool:
        with self._lock:
            return self._configured

    @property
    def is_started(self) -> bool:
        with self._lock:
            return self._started

    @property
    def boot_mode(self) -> str:
        with self._lock:
            return self._boot_mode

    @property
    def audit_logger(self) -> Optional[Any]:
        with self._lock:
            # 优先返回已构建的,其次返回注入的
            return self._audit if self._audit is not None else self._audit_injected

    @property
    def persistence_runtime(self) -> Optional[Any]:
        with self._lock:
            return self._persistence if self._persistence is not None else self._persistence_injected

    @property
    def self_model_orchestrator(self) -> Optional[Any]:
        with self._lock:
            return self._self_model_orchestrator if self._self_model_orchestrator is not None else self._smo_injected

    @property
    def long_loop(self) -> Optional[Any]:
        with self._lock:
            return self._loop

    @property
    def snapshot_path(self) -> str:
        with self._lock:
            return self._snapshot_path

    @property
    def snapshot_store(self) -> Optional[Any]:
        with self._lock:
            return self._snapshot_store

    @property
    def snapshot_builder(self) -> Optional[Any]:
        with self._lock:
            return self._snapshot_builder

    @property
    def configure_error(self) -> Optional[str]:
        with self._lock:
            return self._configure_error

    @property
    def start_error(self) -> Optional[str]:
        with self._lock:
            return self._start_error

    # --------------------------------------------------------
    # configure — 一次性组装
    # --------------------------------------------------------
    def configure(self) -> bool:
        """执行一次性组装(幂等)。

        顺序:
        1) 构造 RuntimeAuditLogger(若未注入)
        2) 构造 PersistenceRuntime(若未注入)
        3) 构造 SelfModelOrchestrator(若未注入,注入 PR)
        4) 通过 orchestrator.configure_self_model(smo) 注入
        5) 构造 SnapshotStore + SnapshotBuilder
        6) 构造 checkpoint_provider
        7) 构造 LongLoop 推迟到 start()(需要 input_provider 等)

        返回:
        - True 成功
        - False 失败(已记录到 self.configure_error)
        """
        with self._lock:
            if self._configured:
                return True
            try:
                # 1) Audit Logger
                if self._audit_injected is not None:
                    self._audit = self._audit_injected
                else:
                    self._audit = self._build_audit_logger()

                # 2) PersistenceRuntime
                if self._persistence_injected is not None:
                    self._persistence = self._persistence_injected
                else:
                    self._persistence = self._build_persistence_runtime()

                # 3) SelfModelOrchestrator
                if self._smo_injected is not None:
                    self._self_model_orchestrator = self._smo_injected
                else:
                    self._self_model_orchestrator = self._build_self_model_orchestrator()

                # 4) 注入到 Orchestrator(若 Orchestrator 提供该方法)
                try:
                    configure_method = getattr(self._orchestrator, "configure_self_model", None)
                    if callable(configure_method):
                        configure_method(self._self_model_orchestrator)
                    else:
                        logger.debug(
                            "RuntimeBootstrap: Orchestrator 不含 configure_self_model(),跳过注入"
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "RuntimeBootstrap: Orchestrator.configure_self_model 失败(已隔离): %s",
                        exc,
                    )

                # 5) 构造 Snapshot Store + Builder
                self._snapshot_store, self._snapshot_builder = self._build_snapshot_components()

                # 6) 构造 checkpoint_provider
                self._checkpoint_provider = self._build_checkpoint_provider()

                self._configured = True
                self._configure_error = None
                return True
            except Exception as exc:  # noqa: BLE001
                self._configure_error = _safe_str(repr(exc), max_len=300)
                logger.warning("RuntimeBootstrap.configure 失败(已隔离): %s", exc)
                return False

    # --------------------------------------------------------
    # 内部构造器
    # --------------------------------------------------------
    def _build_audit_logger(self) -> Any:
        try:
            from src.runtime.audit_log.runtime_audit_logger import RuntimeAuditLogger
            return RuntimeAuditLogger(
                log_path=self._audit_log_path,
                max_bytes=10 * 1024 * 1024,
                enabled=True,
                auto_flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("RuntimeBootstrap: 构造 RuntimeAuditLogger 失败(已隔离): %s", exc)
            return None

    def _build_persistence_runtime(self) -> Any:
        try:
            from src.runtime.self_model.persistence.persistence_runtime import (
                PersistenceRuntime,
            )
            return PersistenceRuntime(auto_startup_checkpoint=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RuntimeBootstrap: 构造 PersistenceRuntime 失败(已隔离): %s", exc)
            return None

    def _build_self_model_orchestrator(self) -> Any:
        try:
            from src.orchestrator.self_model_orchestrator import (
                SelfModelOrchestrator,
            )
            return SelfModelOrchestrator(
                foundation=None,
                evolution_engine=None,
                reflection_engine=None,
                consistency_checker=None,
                persistence_runtime=self._persistence,
                identity_id=self._identity_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("RuntimeBootstrap: 构造 SelfModelOrchestrator 失败(已隔离): %s", exc)
            return None

    def _build_snapshot_components(self) -> Any:
        """构造 RuntimeSnapshotStore + RuntimeSnapshotBuilder。

        返回 (store, builder);任意一步失败返回 (None, None)。
        """
        store = None
        builder = None
        try:
            from src.orchestrator.runtime_snapshot import (
                RuntimeSnapshotStore,
                RuntimeSnapshotBuilder,
            )
            store = RuntimeSnapshotStore(
                path=self._snapshot_path,
                auto_create_dir=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("RuntimeBootstrap: 构造 RuntimeSnapshotStore 失败(已隔离): %s", exc)
            store = None

        try:
            from src.orchestrator.runtime_snapshot import (
                RuntimeSnapshotBuilder,
            )
            builder = RuntimeSnapshotBuilder(
                identity_id=self._identity_id,
                store=store,
                persistence_runtime=self._persistence,
                audit_logger=self._audit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("RuntimeBootstrap: 构造 RuntimeSnapshotBuilder 失败(已隔离): %s", exc)
            builder = None

        return store, builder

    def _build_checkpoint_provider(self) -> Callable[[Dict[str, Any]], None]:
        """构造落盘 callable(给 LongLoop 注入)。

        职责:
        1) 接收 LongLoop 提供的 state dict
        2) 委托 RuntimeSnapshotBuilder 合并 + 落盘
        3) 异常完全隔离,不影响 LongLoop 主流程
        """
        bootstrap_ref = self

        def _provider(state: Dict[str, Any]) -> None:
            try:
                builder = bootstrap_ref._snapshot_builder
                if builder is None:
                    return
                # 1) 合并 → 新 snapshot
                snap = builder.build_from_state(state)
                if snap is None:
                    return
                # 2) 落盘 + 审计
                ok = builder.commit(snap, reason=str(getattr(snap, "source", "checkpoint")))
                if ok:
                    with bootstrap_ref._lock:
                        bootstrap_ref._snapshot_write_count += 1
                    # 同步 bootstrap 内部 _snapshot 引用
                    with bootstrap_ref._lock:
                        bootstrap_ref._snapshot = snap
            except Exception as exc:  # noqa: BLE001
                # 防御性:Provider 失败不影响 LongLoop 主流程
                logger.warning("RuntimeBootstrap.checkpoint_provider 失败(已隔离): %s", exc)

        return _provider

    # --------------------------------------------------------
    # start — 启动入口
    # --------------------------------------------------------
    def start(
        self,
        input_provider: Optional[Callable[[], str]] = None,
        on_reply: Optional[Callable[[str, str], None]] = None,
        on_goodbye: Optional[Callable[[], None]] = None,
        banner: Optional[str] = None,
    ) -> Optional[Any]:
        """启动 RuntimeBootstrap(幂等)。

        步骤:
        1) 若未 configure,自动 configure
        2) PR.initialize(identity_id)
        3) PR.restore_on_startup(identity_id) — SelfModel 恢复
        4) 加载/初始化 RuntimeSnapshot
        5) 判定 boot_mode(fresh | restore)
        6) 构造 LongLoop(注入 audit + checkpoint_provider)
        7) audit.log_loop_start(...)
        8) 返回 LongLoop

        参数:
        - input_provider: 用户输入源(默认 None → LongLoop 用内置 input())
        - on_reply: 自定义输出回调(默认 None → LongLoop 默认 print)
        - on_goodbye: 退出前钩子
        - banner: 启动横幅

        返回:
        - LongLoop 实例(成功)
        - None(失败,已记录到 self.start_error)
        """
        with self._lock:
            if self._started and self._loop is not None:
                return self._loop

            # 1) configure
            if not self._configured:
                ok = self.configure()
                if not ok:
                    self._start_error = self._configure_error or "configure_failed"
                    return None

            try:
                # 2) PR.initialize
                if self._persistence is not None:
                    try:
                        self._persistence.initialize(self._identity_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "RuntimeBootstrap: PR.initialize 失败(已隔离): %s", exc,
                        )

                # 3) PR.restore_on_startup
                if self._persistence is not None:
                    try:
                        self._persistence.restore_on_startup(
                            self._identity_id,
                            create_checkpoint=False,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "RuntimeBootstrap: PR.restore_on_startup 失败(已隔离): %s", exc,
                        )

                # 4) 加载/初始化 snapshot
                loaded, mode = self._load_or_init_snapshot()
                self._snapshot = loaded
                self._boot_mode = mode

                # 5) 更新 boot 字段
                assert self._snapshot is not None
                new_count = self._snapshot.mark_boot(self._boot_mode)
                self._write_snapshot_to_disk(self._snapshot)
                # 同步 builder 的 last_snapshot
                if self._snapshot_builder is not None:
                    try:
                        with self._snapshot_builder._lock:  # type: ignore[attr-defined]
                            self._snapshot_builder._last_snapshot = self._snapshot.copy()  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001
                        pass

                # 6) 构造 LongLoop
                loop = self._build_long_loop(
                    input_provider=input_provider,
                    on_reply=on_reply,
                    on_goodbye=on_goodbye,
                    banner=banner,
                )
                if loop is None:
                    self._start_error = "long_loop_build_failed"
                    return None
                self._loop = loop

                # 7) 审计 loop_start
                if self._audit is not None:
                    try:
                        self._audit.log_loop_start(
                            identity_id=self._identity_id,
                            boot_mode=self._boot_mode,
                            boot_count=new_count,
                            persistence_available=self._persistence is not None,
                            self_model_orchestrator_available=self._self_model_orchestrator is not None,
                        )
                    except Exception:  # noqa: BLE001
                        pass

                self._started = True
                self._start_error = None
                return loop
            except Exception as exc:  # noqa: BLE001
                self._start_error = _safe_str(repr(exc), max_len=300)
                logger.warning("RuntimeBootstrap.start 失败(已隔离): %s", exc)
                return None

    def _load_or_init_snapshot(self) -> Any:
        """加载或初始化 snapshot,返回 (snap, boot_mode)。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshot,
            SOURCE_FRESH,
            SOURCE_RESTORE,
        )
        store = self._snapshot_store
        builder = self._snapshot_builder

        # 尝试加载
        loaded = None
        if store is not None:
            try:
                loaded = store.load()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "RuntimeBootstrap: 加载 snapshot 异常(已隔离): %s", exc,
                )
                loaded = None

        if loaded is not None and isinstance(loaded, RuntimeSnapshot) and loaded.is_schema_compatible():
            with self._lock:
                self._snapshot_read_count += 1
            return loaded, SOURCE_RESTORE

        # fresh 模式:构造 initial
        if builder is not None:
            try:
                fresh = builder.build_initial()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "RuntimeBootstrap: build_initial 失败(已隔离): %s", exc,
                )
                fresh = RuntimeSnapshot(identity_id=self._identity_id)
        else:
            fresh = RuntimeSnapshot(identity_id=self._identity_id)
        # 写一份 initial 到磁盘
        self._write_snapshot_to_disk(fresh)
        return fresh, SOURCE_FRESH

    def _write_snapshot_to_disk(self, snap: Any) -> bool:
        """统一入口:落盘 snapshot(由 Store 负责)。"""
        store = self._snapshot_store
        if store is None or snap is None:
            return False
        try:
            ok = store.save(snap)
            if ok:
                with self._lock:
                    self._snapshot_write_count += 1
            return ok
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RuntimeBootstrap: snapshot 落盘异常(已隔离): %s", exc,
            )
            return False

    def _build_long_loop(
        self,
        input_provider: Optional[Callable[[], str]],
        on_reply: Optional[Callable[[str, str], None]],
        on_goodbye: Optional[Callable[[], None]],
        banner: Optional[str],
    ) -> Optional[Any]:
        try:
            from src.orchestrator.long_loop import LongLoop
            return LongLoop(
                input_provider=input_provider,
                orchestrator=self._orchestrator,
                checkpoint_provider=self._checkpoint_provider,
                on_reply=on_reply,
                banner=banner,
                on_goodbye=on_goodbye,
                checkpoint_interval=self._checkpoint_interval,
                audit_logger=self._audit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("RuntimeBootstrap: 构造 LongLoop 失败(已隔离): %s", exc)
            return None

    # --------------------------------------------------------
    # 观测接口
    # --------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """返回当前 RuntimeSnapshot(纯读,不触发落盘)。"""
        with self._lock:
            if self._snapshot is None:
                from src.orchestrator.runtime_snapshot import RuntimeSnapshot
                return RuntimeSnapshot(identity_id=self._identity_id).to_dict()
            try:
                return self._snapshot.to_dict()
            except Exception:  # noqa: BLE001
                return {}

    def health(self) -> Dict[str, Any]:
        """聚合健康度自检。"""
        with self._lock:
            h: Dict[str, Any] = {
                "name": self.name,
                "schema_version": self.schema_version,
                "identity_id": self._identity_id,
                "is_configured": self._configured,
                "is_started": self._started,
                "boot_mode": self._boot_mode,
                "configure_error": self._configure_error,
                "start_error": self._start_error,
                "components": {
                    "persistence_runtime": self._persistence is not None,
                    "self_model_orchestrator": self._self_model_orchestrator is not None,
                    "audit_logger": self._audit is not None,
                    "long_loop": self._loop is not None,
                    "checkpoint_provider": self._checkpoint_provider is not None,
                    "snapshot_store": self._snapshot_store is not None,
                    "snapshot_builder": self._snapshot_builder is not None,
                },
                "snapshot": {
                    "path": self._snapshot_path,
                    "write_count": self._snapshot_write_count,
                    "read_count": self._snapshot_read_count,
                    "current": (
                        self._snapshot.to_dict()
                        if self._snapshot is not None and hasattr(self._snapshot, "to_dict")
                        else None
                    ),
                },
            }
            # 子组件 health 合并(若可用)
            try:
                if self._persistence is not None and hasattr(self._persistence, "health_check"):
                    h["persistence_health"] = self._persistence.health_check()
            except Exception:  # noqa: BLE001
                h["persistence_health"] = None
            try:
                if self._self_model_orchestrator is not None and hasattr(
                    self._self_model_orchestrator, "health_check"
                ):
                    h["self_model_orchestrator_health"] = self._self_model_orchestrator.health_check()
            except Exception:  # noqa: BLE001
                h["self_model_orchestrator_health"] = None
            try:
                if self._audit is not None and hasattr(self._audit, "health_check"):
                    h["audit_health"] = self._audit.health_check()
            except Exception:  # noqa: BLE001
                h["audit_health"] = None
            try:
                if self._snapshot_store is not None and hasattr(self._snapshot_store, "health_check"):
                    h["snapshot_store_health"] = self._snapshot_store.health_check()
            except Exception:  # noqa: BLE001
                h["snapshot_store_health"] = None
            return h

    def stats(self) -> Dict[str, Any]:
        """基础统计(轻量版)。"""
        with self._lock:
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "identity_id": self._identity_id,
                "is_configured": self._configured,
                "is_started": self._started,
                "boot_mode": self._boot_mode,
                "snapshot_write_count": self._snapshot_write_count,
                "snapshot_read_count": self._snapshot_read_count,
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"RuntimeBootstrap("
                f"identity_id={self._identity_id!r}, "
                f"configured={self._configured}, "
                f"started={self._started}, "
                f"boot_mode={self._boot_mode!r})"
            )


__all__ = [
    "RuntimeBootstrap",
    "RUNTIME_BOOTSTRAP_SCHEMA_VERSION",
    "DEFAULT_IDENTITY_ID",
    "DEFAULT_AUDIT_LOG_PATH",
    "DEFAULT_RUNTIME_SNAPSHOT_PATH",
    "DEFAULT_CHECKPOINT_INTERVAL",
]
