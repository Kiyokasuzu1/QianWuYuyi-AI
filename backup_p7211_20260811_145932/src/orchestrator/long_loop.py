# -*- coding: utf-8 -*-
"""
src/orchestrator/long_loop.py

Phase 5.0-A: LongLoop —— main.py 使用的最小生命周期循环。

设计原则:
- 只负责生命周期管理 / checkpoint / 优雅退出
- 不创建 EventBus / MessageQueue / TickScheduler / RuntimeBuilder
- 不重新设计 Runtime 架构
- 不接管 Orchestrator.process() 的内部实现
- 状态机: STOPPED -> STARTING -> RUNNING -> DRAINING -> STOPPED

职责:
1. 接收 input 提供者 (callable: Optional[str])
2. 接收 Orchestrator (duck-typed: 任何带 process(input)->str / clear_history() 的对象)
3. 接收 checkpoint 提供者 (callable: state_dict -> None)
4. start() / run() / request_shutdown()
5. Ctrl+C / EOF / exit 关键字均触发 request_shutdown

约束:
- 任一输入处理异常被隔离,不退出循环(由 Orchestrator 自身隔离; LongLoop 仅做最终兜底)
- 任一 checkpoint 异常被隔离,不退出循环
- request_shutdown() 线程安全
- run() 阻塞直到收到 shutdown 信号
"""
from __future__ import annotations

import enum
import logging
import threading
from typing import Any, Callable, Dict, List, Optional


logger = logging.getLogger(__name__)


# ============================================================
# 状态机
# ============================================================
class LongLoopState(str, enum.Enum):
    """LongLoop 生命周期状态。"""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"


LONG_LOOP_SCHEMA_VERSION = "1.0"


# ============================================================
# LongLoop
# ============================================================
class LongLoop:
    """main.py 使用的最小生命周期循环(Phase 5.0-A / v1.0)。

    使用方式:
        loop = LongLoop(
            input_provider=input,
            orchestrator=orch,
            checkpoint_provider=lambda state: persist(state),
            on_reply=lambda user, reply: print(f"羽依: {reply}"),
        )
        loop.start()
        try:
            loop.run()  # 阻塞直到 shutdown
        except KeyboardInterrupt:
            loop.request_shutdown("KeyboardInterrupt")

    关键不变量:
    - request_shutdown() 线程安全
    - 任一轮内异常被 try/except 隔离,不退出循环
    - Ctrl+C / EOF / "exit" 都会触发 request_shutdown
    - STOPPED 状态调用 start() 是幂等的(返回 False)
    - start() 已运行后再次调用返回 False
    - run() 会自动调用 start()(若未启动)
    - 退出前会调用一次 final checkpoint
    """

    name: str = "long_loop"
    schema_version: str = LONG_LOOP_SCHEMA_VERSION

    # 退出关键字(不区分大小写,前后 strip)
    EXIT_KEYWORDS = ("exit", "quit", "再见", "退出", "拜拜")

    # 清空会话关键字
    CLEAR_KEYWORDS = ("/clear", ":clear", "清空", "重置")

    def __init__(
        self,
        input_provider: Optional[Callable[[], str]] = None,
        orchestrator: Optional[Any] = None,
        checkpoint_provider: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_reply: Optional[Callable[[str, str], None]] = None,
        banner: Optional[str] = None,
        on_goodbye: Optional[Callable[[], None]] = None,
        checkpoint_interval: int = 0,
        audit_logger: Optional[Any] = None,
    ) -> None:
        """构造 LongLoop。

        参数:
        - input_provider: 可调用对象,返回用户输入字符串。
          若为 None,默认使用内置 input()(便于测试时可注入)。
        - orchestrator: 任何带 process(str) -> str 和 clear_history() 的对象。
          若为 None,LongLoop 退化为 no-op loop(测试用)。
        - checkpoint_provider: 可调用对象,接收 state dict。
          若提供,在 checkpoint 触发时被调用,异常被隔离。
        - on_reply: 可选,接收 (user_input, reply) 用于自定义输出。
          若为 None,默认打印 "羽依: {reply}"。
        - banner: 启动时打印的横幅;若为 None 则不打印。
        - on_goodbye: 退出前调用的钩子(可用于最后一句告别)。
        - checkpoint_interval: 多少轮触发一次 checkpoint。
          0 = 不在轮内触发 checkpoint,仅在退出时触发最终 checkpoint。
        - audit_logger: 可选 RuntimeAuditLogger 实例(Phase 5.0-B)。
          若提供,会在 start/turn/checkpoint/exception/stop 阶段写审计日志。
          写入异常被 RuntimeAuditLogger 自身完全隔离,不影响主流程。
          默认 None(向后兼容 Phase 5.0-A)。
        """
        self._lock = threading.RLock()

        self._input_provider = input_provider
        self._orchestrator = orchestrator
        self._checkpoint_provider = checkpoint_provider
        self._on_reply = on_reply
        self._banner = banner
        self._on_goodbye = on_goodbye
        self._checkpoint_interval = max(0, int(checkpoint_interval or 0))
        # Phase 5.0-B: 可选 audit hook(默认 None,完全向后兼容)
        self._audit_logger = audit_logger

        # 状态机
        self._state: LongLoopState = LongLoopState.STOPPED

        # 退出信号
        self._shutdown_requested: bool = False
        self._shutdown_reason: Optional[str] = None

        # 统计
        self._turn_count: int = 0
        self._error_count: int = 0
        self._checkpoint_count: int = 0
        self._started_at: Optional[str] = None
        self._stopped_at: Optional[str] = None

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def state(self) -> LongLoopState:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._state in (LongLoopState.RUNNING, LongLoopState.DRAINING)

    @property
    def shutdown_requested(self) -> bool:
        with self._lock:
            return self._shutdown_requested

    @property
    def shutdown_reason(self) -> Optional[str]:
        with self._lock:
            return self._shutdown_reason

    @property
    def started_at(self) -> Optional[str]:
        with self._lock:
            return self._started_at

    @property
    def stopped_at(self) -> Optional[str]:
        with self._lock:
            return self._stopped_at

    @property
    def turn_count(self) -> int:
        with self._lock:
            return self._turn_count

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._error_count

    @property
    def checkpoint_count(self) -> int:
        with self._lock:
            return self._checkpoint_count

    # --------------------------------------------------------
    # 状态转换
    # --------------------------------------------------------
    def _transition(self, new_state: LongLoopState) -> None:
        with self._lock:
            old = self._state
            if old == new_state:
                return
            # 合法转移
            valid = {
                LongLoopState.STOPPED: {LongLoopState.STARTING},
                LongLoopState.STARTING: {LongLoopState.RUNNING, LongLoopState.STOPPED},
                LongLoopState.RUNNING: {LongLoopState.DRAINING, LongLoopState.STOPPED},
                LongLoopState.DRAINING: {LongLoopState.STOPPED},
            }
            if new_state not in valid.get(old, set()):
                logger.warning(
                    "LongLoop 非法状态转移: %s -> %s (忽略)", old, new_state,
                )
                return
            self._state = new_state
            logger.debug("LongLoop 状态: %s -> %s", old, new_state)

    # --------------------------------------------------------
    # start / run / request_shutdown
    # --------------------------------------------------------
    def start(self) -> bool:
        """进入 STARTING -> RUNNING。返回 True 表示成功;已运行则返回 False。"""
        with self._lock:
            if self._state != LongLoopState.STOPPED:
                logger.debug("LongLoop.start() 被忽略,当前 state=%s", self._state)
                return False
            self._transition(LongLoopState.STARTING)
            self._started_at = self._now_iso()
            if self._banner:
                print(self._banner)
            self._transition(LongLoopState.RUNNING)
        # Phase 5.0-B: 审计(锁外,fire-and-forget)
        self._audit_call("log_loop_start")
        return True

    def request_shutdown(self, reason: Optional[str] = None) -> None:
        """请求关闭(线程安全)。不会强制中断当前轮。"""
        with self._lock:
            if self._shutdown_requested:
                return
            self._shutdown_requested = True
            self._shutdown_reason = str(reason) if reason else "requested"
            if self._state == LongLoopState.RUNNING:
                self._transition(LongLoopState.DRAINING)

    def run(self) -> None:
        """主循环: 阻塞直到 shutdown。

        流程:
        1. 若 STOPPED,自动 start()
        2. 反复: 读取输入 -> 关键字判断 -> 调用 orchestrator.process() -> 输出
        3. 任一轮异常被隔离
        4. 退出前: transition STOPPED, 触发 final checkpoint
        """
        if self.state == LongLoopState.STOPPED:
            self.start()

        if self._orchestrator is None:
            logger.warning("LongLoop: orchestrator 未注入, run() 立即返回")
            self._finalize()
            return

        try:
            while not self.shutdown_requested:
                # 读取输入
                try:
                    user_input = self._read_input()
                except EOFError:
                    self.request_shutdown("EOF")
                    break
                except KeyboardInterrupt:
                    self.request_shutdown("KeyboardInterrupt")
                    break
                except Exception as exc:  # noqa: BLE001
                    self._record_error(exc)
                    print(f"\n[LongLoop] 读取输入异常: {exc}")
                    continue

                if user_input is None:
                    # input_provider 返回 None: 视为 EOF
                    self.request_shutdown("input_provider returned None")
                    break

                user_input = str(user_input).strip()

                if not user_input:
                    continue

                # 退出关键字
                if self._is_exit_keyword(user_input):
                    self.request_shutdown("exit keyword")
                    break

                # 清空关键字
                if self._is_clear_keyword(user_input):
                    try:
                        clear_method = getattr(self._orchestrator, "clear_history", None)
                        if clear_method is not None:
                            clear_method()
                        print("羽依: 好,我忘掉刚才的了。我们重新开始吧。\n")
                    except Exception as exc:  # noqa: BLE001
                        self._record_error(exc)
                        print(f"[LongLoop] clear_history 异常: {exc}")
                    continue

                # 处理单轮对话
                self._handle_turn(user_input)

                # 周期 checkpoint
                if (
                    self._checkpoint_interval > 0
                    and self._turn_count % self._checkpoint_interval == 0
                ):
                    self._safe_checkpoint("periodic")
        except Exception as exc:  # noqa: BLE001
            # 顶层兜底:不退出进程,只记录
            self._record_error(exc)
            logger.exception("LongLoop 顶层异常(已隔离): %s", exc)
            self.request_shutdown(f"top-level exception: {exc!r}")
        finally:
            self._finalize()

    # --------------------------------------------------------
    # 单轮处理
    # --------------------------------------------------------
    def _handle_turn(self, user_input: str) -> None:
        """处理单轮对话。异常被隔离。"""
        with self._lock:
            self._turn_count += 1
            turn_id = f"turn_{self._turn_count}"

        # Phase 5.0-B: 审计 - 事件开始
        self._audit_call(
            "log_event_start",
            event_id=turn_id,
            user_input=user_input,
        )

        t0 = None
        try:
            from time import perf_counter
            t0 = perf_counter()
        except Exception:  # noqa: BLE001
            t0 = None

        try:
            reply = self._orchestrator.process(user_input)
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            self._audit_call(
                "log_exception",
                phase="long_loop.orchestrator.process",
                exc=exc,
                event_id=turn_id,
            )
            print(f"\n⚠️ 系统异常: {exc}")
            import traceback
            traceback.print_exc()
            # 仍然记录 event_end(success=False)
            duration_ms = None
            if t0 is not None:
                try:
                    duration_ms = (perf_counter() - t0) * 1000.0
                except Exception:  # noqa: BLE001
                    duration_ms = None
            self._audit_call(
                "log_event_end",
                event_id=turn_id,
                reply=None,
                duration_ms=duration_ms,
                success=False,
            )
            return

        duration_ms = None
        if t0 is not None:
            try:
                duration_ms = (perf_counter() - t0) * 1000.0
            except Exception:  # noqa: BLE001
                duration_ms = None

        # Phase 5.0-B: 审计 - 事件结束
        self._audit_call(
            "log_event_end",
            event_id=turn_id,
            reply=reply,
            duration_ms=duration_ms,
            success=True,
        )

        # 输出回复
        if self._on_reply is not None:
            try:
                self._on_reply(user_input, reply)
            except Exception as exc:  # noqa: BLE001
                self._record_error(exc)
                self._audit_call(
                    "log_exception",
                    phase="long_loop.on_reply",
                    exc=exc,
                    event_id=turn_id,
                )
                # 兜底打印
                print(f"羽依: {reply}\n")
        else:
            print(f"羽依: {reply}\n")

    # --------------------------------------------------------
    # 输入读取
    # --------------------------------------------------------
    def _read_input(self) -> Optional[str]:
        provider = self._input_provider
        if provider is None:
            provider = input
        return provider()

    @classmethod
    def _is_exit_keyword(cls, text: str) -> bool:
        return text.lower() in {kw.lower() for kw in cls.EXIT_KEYWORDS}

    @classmethod
    def _is_clear_keyword(cls, text: str) -> bool:
        return text.lower() in {kw.lower() for kw in cls.CLEAR_KEYWORDS}

    # --------------------------------------------------------
    # checkpoint
    # --------------------------------------------------------
    def _safe_checkpoint(self, reason: str) -> None:
        if self._checkpoint_provider is None:
            return
        state = self._collect_state()
        try:
            self._checkpoint_provider(state)
            with self._lock:
                self._checkpoint_count += 1
            logger.debug("LongLoop checkpoint 触发: %s", reason)
            # Phase 5.0-B: 审计
            self._audit_call("log_checkpoint", reason=reason, state=state)
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)
            self._audit_call(
                "log_exception",
                phase="long_loop.checkpoint",
                exc=exc,
            )
            logger.warning("LongLoop checkpoint 失败(已隔离): %s", exc)

    def _collect_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "schema_version": self.schema_version,
                "turn_count": self._turn_count,
                "error_count": self._error_count,
                "started_at": self._started_at,
                "state": self._state.value,
            }

    # --------------------------------------------------------
    # 退出收尾
    # --------------------------------------------------------
    def _finalize(self) -> None:
        # 先切到 STOPPED,确保 final checkpoint 记录的是已停止状态
        self._transition(LongLoopState.STOPPED)
        self._stopped_at = self._now_iso()

        # 触发最终 checkpoint
        self._safe_checkpoint("final")

        # 触发 goodbye 钩子
        if self._on_goodbye is not None:
            try:
                self._on_goodbye()
            except Exception as exc:  # noqa: BLE001
                self._record_error(exc)
                self._audit_call(
                    "log_exception",
                    phase="long_loop.on_goodbye",
                    exc=exc,
                )
                logger.warning("LongLoop on_goodbye 失败(已隔离): %s", exc)

        # Phase 5.0-B: 审计 - 循环结束
        self._audit_call(
            "log_loop_stop",
            reason=self._shutdown_reason,
        )

    # --------------------------------------------------------
    # 辅助
    # --------------------------------------------------------
    def _record_error(self, exc: BaseException) -> None:
        with self._lock:
            self._error_count += 1

    def _audit_call(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        """Phase 5.0-B: 调用 audit_logger 方法,完全 fire-and-forget 隔离。

        audit_logger 自身方法已隔离所有异常;此处再包一层防御性 try/except,
        保证任何 audit 路径问题都不影响 LongLoop 主流程。
        """
        audit = self._audit_logger
        if audit is None:
            return
        try:
            method = getattr(audit, method_name, None)
            if method is None:
                return
            try:
                method(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                # 审计方法自身抛错 → 静默丢弃
                logger.warning("LongLoop audit 调用失败(已隔离): %s", exc)
        except Exception:  # noqa: BLE001
            # getattr 失败或其他 → 静默
            pass

    @staticmethod
    def _now_iso() -> str:
        try:
            from datetime import datetime, timezone
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        except (AttributeError, TypeError):  # pragma: no cover
            from datetime import datetime
            return datetime.utcnow().isoformat() + "Z"

    # --------------------------------------------------------
    # 健康度
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "state": self._state.value,
                "shutdown_requested": self._shutdown_requested,
                "shutdown_reason": self._shutdown_reason,
                "turn_count": self._turn_count,
                "error_count": self._error_count,
                "checkpoint_count": self._checkpoint_count,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "components": {
                    "input_provider": self._input_provider is not None,
                    "orchestrator": self._orchestrator is not None,
                    "checkpoint_provider": self._checkpoint_provider is not None,
                    "on_reply": self._on_reply is not None,
                    "on_goodbye": self._on_goodbye is not None,
                    "audit_logger": self._audit_logger is not None,
                },
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._turn_count = 0
            self._error_count = 0
            self._checkpoint_count = 0
            self._started_at = None
            self._stopped_at = None
            self._shutdown_requested = False
            self._shutdown_reason = None
            self._state = LongLoopState.STOPPED


__all__ = [
    "LongLoop",
    "LongLoopState",
    "LONG_LOOP_SCHEMA_VERSION",
]
