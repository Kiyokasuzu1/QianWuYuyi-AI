# -*- coding: utf-8 -*-
"""
src/runtime/recovery/runtime_recovery.py

Phase C.7.3 Runtime Recovery & Self-Diagnosis —— RuntimeRecovery

职责:
  - 接收 RuntimeDiagnoser 的诊断结果(diagnosis)
  - 执行"安全"的 Runtime 恢复动作
  - 不修改任何业务状态(只读业务边界)
  - 任何异常都不得抛出,返回 {success: bool, actions: [...], ...}

=========================
硬约束
=========================
禁止:
  - 调用 GrowthEngine.apply() / GrowthState.save() / ProposalManager.apply_proposal()
  - 调用 PersonalityResolver.resolve() / TraitStateUpdater.apply()
  - 调用 adapter.process_cycle() / 重新执行 cycle
  - 修改 src/growth/** / src/personality/** / src/runtime/self_model/**

允许:
  - 清理 Observer 缓存
  - 重新 attach adapter(adapter.attach())
  - 重新 health_check(adapter.health_check())
  - 重置 Runtime 临时状态
  - 标记 degraded
  - 生成 recovery report

异常处理:
  - recover() 永不抛异常
  - 任何内部异常 → 返回 {success: false, error: "..."}
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本 + 常量
# ============================================================

RUNTIME_RECOVERY_SCHEMA_VERSION = "1.0"
RUNTIME_RECOVERY_NAME = "runtime_recovery"
RUNTIME_RECOVERY_VERSION = "1.0.0"

# 允许的恢复动作(冻结)
RECOVERY_ACTION_CLEAR_OBSERVER_CACHE = "clear_observer_cache"
RECOVERY_ACTION_REATTACH_ADAPTERS = "reattach_adapters"
RECOVERY_ACTION_RECHECK_HEALTH = "recheck_health"
RECOVERY_ACTION_RESET_TEMP_STATE = "reset_temp_state"
RECOVERY_ACTION_MARK_DEGRADED = "mark_degraded"

ALL_RECOVERY_ACTIONS: List[str] = [
    RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
    RECOVERY_ACTION_REATTACH_ADAPTERS,
    RECOVERY_ACTION_RECHECK_HEALTH,
    RECOVERY_ACTION_RESET_TEMP_STATE,
    RECOVERY_ACTION_MARK_DEGRADED,
]

# 类型匹配表:从诊断类型 / 严重级别 → 应执行的动作
# (允许 RuntimeRecovery 自主决策,无需 Diagnoser 推荐)
TYPE_TO_ACTIONS: Dict[str, List[str]] = {
    # 规则 1: adapter 不健康 → 重新 attach + 重新 health
    "adapter_unhealthy": [
        RECOVERY_ACTION_REATTACH_ADAPTERS,
        RECOVERY_ACTION_RECHECK_HEALTH,
    ],
    # 规则 2: runtime 整体降级 → 全套恢复
    "runtime_degraded": [
        RECOVERY_ACTION_REATTACH_ADAPTERS,
        RECOVERY_ACTION_RECHECK_HEALTH,
        RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
        RECOVERY_ACTION_RESET_TEMP_STATE,
    ],
    # 规则 3: 连续失败 → 重置临时状态 + 标记 degraded
    "runtime_failure_streak": [
        RECOVERY_ACTION_RESET_TEMP_STATE,
        RECOVERY_ACTION_MARK_DEGRADED,
    ],
    # 规则 4: audit 不可用 → 仅清理缓存
    "audit_unavailable": [
        RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
    ],
    # 规则 5: persistence 降级 → 仅清理缓存 + 重置临时状态
    "persistence_degraded": [
        RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
        RECOVERY_ACTION_RESET_TEMP_STATE,
    ],
    # 规则 6: cycle 失败率高 → 重置临时状态 + 重新 health
    "cycle_failure_rate_high": [
        RECOVERY_ACTION_RESET_TEMP_STATE,
        RECOVERY_ACTION_RECHECK_HEALTH,
    ],
    # 规则 7: observer 不可用 → 仅清理缓存
    "observer_unavailable": [
        RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
    ],
}

# 严重级别 → 动作
SEVERITY_TO_ACTIONS: Dict[str, List[str]] = {
    "low": [RECOVERY_ACTION_CLEAR_OBSERVER_CACHE],
    "medium": [
        RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
        RECOVERY_ACTION_RECHECK_HEALTH,
    ],
    "high": [
        RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
        RECOVERY_ACTION_REATTACH_ADAPTERS,
        RECOVERY_ACTION_RECHECK_HEALTH,
        RECOVERY_ACTION_RESET_TEMP_STATE,
        RECOVERY_ACTION_MARK_DEGRADED,
    ],
}


# ============================================================
# 时间工具
# ============================================================


def _now_iso() -> str:
    """ISO 8601 UTC timestamp(fail-soft)"""
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        try:
            return datetime.utcnow().isoformat() + "Z"
        except Exception:  # noqa: BLE001
            return "1970-01-01T00:00:00Z"


def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(d, dict):
            return d.get(key, default)
        return default
    except Exception:  # noqa: BLE001
        return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        return bool(v)
    except Exception:  # noqa: BLE001
        return default


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        return []
    except Exception:  # noqa: BLE001
        return []


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:  # noqa: BLE001
        return default


# ============================================================
# RuntimeRecovery
# ============================================================


class RuntimeRecovery:
    """
    Runtime 安全恢复器(Phase C.7.3 / v1.0)

    基于 RuntimeDiagnoser 的诊断结果,执行一组"安全"的恢复动作。

    严格约束:
      - 不修改任何业务状态(只读业务边界)
      - 不调用 process_cycle()
      - 不调用任何 Growth / Personality 写入方法
      - recover() 永不抛异常

    Schema 契约(冻结于 v1.0):
      {
        "schema_version": "1.0",
        "success": bool,
        "actions": [
          {
            "action": str,             # 动作名
            "result": "ok" | "skipped" | "error",
            "target": Optional[str],   # 目标组件
            "error": Optional[str],    # 错误信息(result=error 时)
            "details": dict,           # 附加上下文
          }
        ],
        "issues_addressed": int,        # 本次处理 issue 数
        "actions_executed": int,        # 实际执行动作数
        "actions_skipped": int,         # 跳过动作数
        "actions_errored": int,         # 失败动作数
        "timestamp": str,
        "recovery": {
          "name": "runtime_recovery",
          "version": "1.0.0",
          "schema_version": "1.0",
        },
        "error": Optional[str],         # 顶层异常
      }
    """

    SCHEMA_VERSION = RUNTIME_RECOVERY_SCHEMA_VERSION
    NAME = RUNTIME_RECOVERY_NAME
    VERSION = RUNTIME_RECOVERY_VERSION

    def __init__(
        self,
        observer: Any = None,
        orchestrator: Any = None,
        max_actions_per_issue: int = 4,
        fail_fast: bool = False,
    ) -> None:
        """
        Args:
            observer: RuntimeObserver 实例(可选)
            orchestrator: RuntimeCycleOrchestrator 实例(可选)
            max_actions_per_issue: 每个 issue 最多触发的动作数(防失控)
            fail_fast: 单个 action 失败时是否立即终止(默认 False:继续尝试其他 action)
        """
        self._observer = observer
        self._orchestrator = orchestrator
        self._max_actions_per_issue = max(1, int(max_actions_per_issue or 4))
        self._fail_fast = bool(fail_fast)
        self._lock = threading.RLock()
        # 内部统计
        self._recover_count: int = 0
        self._last_recover_ts: Optional[str] = None
        self._last_error: Optional[str] = None
        self._total_actions_executed: int = 0
        self._total_actions_errored: int = 0

    # --------------------------------------------------------
    # 依赖注入(允许在 recover 之前动态替换)
    # --------------------------------------------------------

    def set_observer(self, observer: Any) -> None:
        with self._lock:
            self._observer = observer

    def set_orchestrator(self, orchestrator: Any) -> None:
        with self._lock:
            self._orchestrator = orchestrator

    def attach(self, observer: Any = None, orchestrator: Any = None) -> None:
        """附加依赖(便于链式注入)"""
        with self._lock:
            if observer is not None:
                self._observer = observer
            if orchestrator is not None:
                self._orchestrator = orchestrator

    # --------------------------------------------------------
    # 恢复主入口
    # --------------------------------------------------------

    def recover(
        self,
        diagnosis: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        基于 diagnosis 执行安全恢复动作。

        Args:
            diagnosis: RuntimeDiagnoser.diagnose() 的输出(可为 None)

        Returns:
            恢复结果 dict(永不抛异常)
        """
        try:
            with self._lock:
                actions: List[Dict[str, Any]] = []
                addressed = 0
                executed = 0
                skipped = 0
                errored = 0
                top_error: Optional[str] = None

                # 1) 诊断为空 → 直接返回失败 + 提示(无法恢复)
                if not isinstance(diagnosis, dict):
                    actions.append(self._action_result(
                        action=RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
                        target="observer",
                        result="skipped",
                        details={"reason": "diagnosis_unavailable"},
                    ))
                    return self._build_report(
                        success=False,
                        actions=actions,
                        issues_addressed=0,
                        actions_executed=0,
                        actions_skipped=1,
                        actions_errored=0,
                        top_error="diagnosis_unavailable",
                    )

                # 2) 整体健康 → 仅做缓存清理(实际执行 clear_cache)
                if _safe_bool(_safe_get(diagnosis, "healthy", False)):
                    res = self._execute_action(RECOVERY_ACTION_CLEAR_OBSERVER_CACHE)
                    actions.append(res)
                    result_str = _safe_get(res, "result", "error")
                    if result_str == "ok":
                        executed += 1
                    elif result_str == "skipped":
                        skipped += 1
                    else:
                        errored += 1
                        if top_error is None:
                            top_error = _safe_get(res, "error", "unknown")
                    return self._build_report(
                        success=(errored == 0),
                        actions=actions,
                        issues_addressed=0,
                        actions_executed=executed,
                        actions_skipped=skipped,
                        actions_errored=errored,
                        top_error=top_error,
                    )

                # 3) 收集待执行动作(按 issue 类型 + 严重级别去重)
                issues = _safe_list(_safe_get(diagnosis, "issues", []))
                if not issues:
                    # diagnosis unhealthy 但无 issues → 仅清理缓存
                    actions.append(self._action_result(
                        action=RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
                        target="runtime",
                        result="ok",
                        details={"reason": "no_specific_issues"},
                    ))
                    executed += 1
                    return self._build_report(
                        success=True,
                        actions=actions,
                        issues_addressed=0,
                        actions_executed=executed,
                        actions_skipped=0,
                        actions_errored=0,
                        top_error=None,
                    )

                # 4) 计划动作(去重 + 保序)
                planned: List[str] = []
                seen: set = set()
                severity = _safe_str(_safe_get(diagnosis, "severity", "low"), "low")
                # 先按 severity 加入基础动作
                for act in SEVERITY_TO_ACTIONS.get(severity, []):
                    if act not in seen and act in ALL_RECOVERY_ACTIONS:
                        seen.add(act)
                        planned.append(act)

                # 再按 issue 类型追加(并限制每 issue 触发数)
                for iss in issues:
                    if not isinstance(iss, dict):
                        continue
                    addressed += 1
                    type_ = _safe_str(_safe_get(iss, "type", ""), "")
                    extra = TYPE_TO_ACTIONS.get(type_, [])
                    per_issue_count = 0
                    for act in extra:
                        if act in ALL_RECOVERY_ACTIONS and act not in seen:
                            seen.add(act)
                            planned.append(act)
                            per_issue_count += 1
                            if per_issue_count >= self._max_actions_per_issue:
                                break

                # 5) 执行动作(每个动作 fail-soft 隔离)
                for act in planned:
                    res = self._execute_action(act)
                    actions.append(res)
                    result_str = _safe_get(res, "result", "error")
                    if result_str == "ok":
                        executed += 1
                    elif result_str == "skipped":
                        skipped += 1
                    else:  # error
                        errored += 1
                        if top_error is None:
                            top_error = _safe_get(res, "error", "unknown")
                        if self._fail_fast:
                            break

                # 6) 任何 errored > 0 → success=False(整体失败)
                overall_success = (errored == 0)

                return self._build_report(
                    success=overall_success,
                    actions=actions,
                    issues_addressed=addressed,
                    actions_executed=executed,
                    actions_skipped=skipped,
                    actions_errored=errored,
                    top_error=top_error,
                )

        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = repr(exc)
            logger.debug(f"[phase_c7_3] recovery.recover 异常(已隔离): {exc}")
            return {
                "schema_version": self.SCHEMA_VERSION,
                "success": False,
                "actions": [
                    self._action_result(
                        action="recovery_internal_error",
                        target="recovery",
                        result="error",
                        details={"error": repr(exc)},
                    )
                ],
                "issues_addressed": 0,
                "actions_executed": 0,
                "actions_skipped": 0,
                "actions_errored": 1,
                "timestamp": _now_iso(),
                "recovery": {
                    "name": self.NAME,
                    "version": self.VERSION,
                    "schema_version": self.SCHEMA_VERSION,
                },
                "error": repr(exc),
            }

    # --------------------------------------------------------
    # 动作执行
    # --------------------------------------------------------

    def _execute_action(self, action: str) -> Dict[str, Any]:
        """执行单个恢复动作(永不抛异常)"""
        try:
            handler = _ACTION_HANDLERS.get(action)
            if handler is None:
                return self._action_result(
                    action=action,
                    target=None,
                    result="error",
                    details={"reason": "unknown_action"},
                    error="unknown_action",
                )
            return handler(self)
        except Exception as exc:  # noqa: BLE001
            return self._action_result(
                action=action,
                target=None,
                result="error",
                details={"reason": "handler_exception"},
                error=repr(exc),
            )

    def _action_clear_observer_cache(self) -> Dict[str, Any]:
        """清理 Observer 内部缓存(只读业务边界)。"""
        details: Dict[str, Any] = {"cleared": False}
        try:
            obs = self._observer
            if obs is None:
                return self._action_result(
                    action=RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
                    target="observer",
                    result="skipped",
                    details={"reason": "observer_not_attached"},
                )
            # 优先调 observer.clear_cache()(若有)
            clear_fn = getattr(obs, "clear_cache", None)
            if callable(clear_fn):
                try:
                    result = clear_fn()
                    details["cleared"] = bool(result)
                    details["method"] = "clear_cache"
                    return self._action_result(
                        action=RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
                        target="observer",
                        result="ok",
                        details=details,
                    )
                except Exception as exc:  # noqa: BLE001
                    details["method"] = "clear_cache"
                    details["error"] = repr(exc)
                    return self._action_result(
                        action=RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
                        target="observer",
                        result="error",
                        details=details,
                        error=repr(exc),
                    )
            # fallback:重置 observer 内部统计字段
            for attr in ("_last_snapshot_ts", "_last_error", "_snapshot_count"):
                try:
                    if hasattr(obs, attr):
                        if attr == "_snapshot_count":
                            try:
                                setattr(obs, attr, 0)
                            except Exception:  # noqa: BLE001
                                pass
                        else:
                            try:
                                setattr(obs, attr, None)
                            except Exception:  # noqa: BLE001
                                pass
                except Exception:  # noqa: BLE001
                    continue
            details["cleared"] = True
            details["method"] = "reset_internal_state"
            return self._action_result(
                action=RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
                target="observer",
                result="ok",
                details=details,
            )
        except Exception as exc:  # noqa: BLE001
            return self._action_result(
                action=RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
                target="observer",
                result="error",
                details={"reason": "exception"},
                error=repr(exc),
            )

    def _action_reattach_adapters(self) -> Dict[str, Any]:
        """重新 attach 所有 adapter(只调用 adapter.attach(),不执行 cycle)。"""
        details: Dict[str, Any] = {
            "reattached": [],
            "skipped": [],
            "errored": [],
        }
        try:
            orch = self._orchestrator
            if orch is None:
                return self._action_result(
                    action=RECOVERY_ACTION_REATTACH_ADAPTERS,
                    target="adapters",
                    result="skipped",
                    details={"reason": "orchestrator_not_attached"},
                )
            # 列出 adapter
            adapters_list: List[Any] = []
            list_fn = getattr(orch, "list_adapters", None)
            if callable(list_fn):
                try:
                    res = list_fn()
                    if isinstance(res, list):
                        adapters_list = list(res)
                except Exception:  # noqa: BLE001
                    pass
            if not adapters_list:
                # fallback: orchestrator._adapters.list()
                adp_obj = getattr(orch, "_adapters", None)
                if adp_obj is not None:
                    list2 = getattr(adp_obj, "list", None)
                    if callable(list2):
                        try:
                            res = list2()
                            if isinstance(res, list):
                                adapters_list = list(res)
                        except Exception:  # noqa: BLE001
                            pass
            if not adapters_list:
                return self._action_result(
                    action=RECOVERY_ACTION_REATTACH_ADAPTERS,
                    target="adapters",
                    result="skipped",
                    details={"reason": "no_adapters"},
                )

            # 逐个 attach
            for item in adapters_list:
                if not isinstance(item, dict):
                    continue
                name = _safe_str(item.get("name", ""), "unknown")
                adapter_obj = item.get("adapter") or item.get("instance")
                if adapter_obj is None:
                    details["skipped"].append({"name": name, "reason": "no_instance"})
                    continue
                try:
                    attach_fn = getattr(adapter_obj, "attach", None)
                    if not callable(attach_fn):
                        details["skipped"].append({"name": name, "reason": "no_attach_method"})
                        continue
                    attach_fn()  # 不传参(只读业务边界)
                    details["reattached"].append(name)
                except Exception as exc:  # noqa: BLE001
                    details["errored"].append({"name": name, "error": repr(exc)})

            if details["errored"]:
                # 视为部分失败(整体 success=False 由 recover 汇总)
                return self._action_result(
                    action=RECOVERY_ACTION_REATTACH_ADAPTERS,
                    target="adapters",
                    result="error",
                    details=details,
                    error="some_attach_failed",
                )
            return self._action_result(
                action=RECOVERY_ACTION_REATTACH_ADAPTERS,
                target="adapters",
                result="ok",
                details=details,
            )
        except Exception as exc:  # noqa: BLE001
            return self._action_result(
                action=RECOVERY_ACTION_REATTACH_ADAPTERS,
                target="adapters",
                result="error",
                details={"reason": "exception"},
                error=repr(exc),
            )

    def _action_recheck_health(self) -> Dict[str, Any]:
        """重新对所有 adapter 执行 health_check(只读)。"""
        details: Dict[str, Any] = {
            "checked": [],
            "unhealthy": [],
            "errored": [],
        }
        try:
            orch = self._orchestrator
            if orch is None:
                return self._action_result(
                    action=RECOVERY_ACTION_RECHECK_HEALTH,
                    target="adapters",
                    result="skipped",
                    details={"reason": "orchestrator_not_attached"},
                )
            adapters_list: List[Any] = []
            list_fn = getattr(orch, "list_adapters", None)
            if callable(list_fn):
                try:
                    res = list_fn()
                    if isinstance(res, list):
                        adapters_list = list(res)
                except Exception:  # noqa: BLE001
                    pass
            if not adapters_list:
                adp_obj = getattr(orch, "_adapters", None)
                if adp_obj is not None:
                    list2 = getattr(adp_obj, "list", None)
                    if callable(list2):
                        try:
                            res = list2()
                            if isinstance(res, list):
                                adapters_list = list(res)
                        except Exception:  # noqa: BLE001
                            pass

            for item in adapters_list:
                if not isinstance(item, dict):
                    continue
                name = _safe_str(item.get("name", ""), "unknown")
                adapter_obj = item.get("adapter") or item.get("instance")
                if adapter_obj is None:
                    details["errored"].append({"name": name, "error": "no_instance"})
                    continue
                try:
                    hc_fn = getattr(adapter_obj, "health_check", None)
                    if not callable(hc_fn):
                        details["errored"].append({"name": name, "error": "no_health_check_method"})
                        continue
                    hc = hc_fn()
                    healthy = _extract_healthy(hc)
                    details["checked"].append({"name": name, "healthy": bool(healthy)})
                    if not healthy:
                        details["unhealthy"].append(name)
                except Exception as exc:  # noqa: BLE001
                    details["errored"].append({"name": name, "error": repr(exc)})

            if details["errored"]:
                return self._action_result(
                    action=RECOVERY_ACTION_RECHECK_HEALTH,
                    target="adapters",
                    result="error",
                    details=details,
                    error="health_check_failed",
                )
            return self._action_result(
                action=RECOVERY_ACTION_RECHECK_HEALTH,
                target="adapters",
                result="ok",
                details=details,
            )
        except Exception as exc:  # noqa: BLE001
            return self._action_result(
                action=RECOVERY_ACTION_RECHECK_HEALTH,
                target="adapters",
                result="error",
                details={"reason": "exception"},
                error=repr(exc),
            )

    def _action_reset_temp_state(self) -> Dict[str, Any]:
        """重置 Runtime 临时状态(只清理 _recent_errors / _consecutive_failures 等)。"""
        details: Dict[str, Any] = {"reset": False}
        try:
            orch = self._orchestrator
            if orch is None:
                return self._action_result(
                    action=RECOVERY_ACTION_RESET_TEMP_STATE,
                    target="runtime",
                    result="skipped",
                    details={"reason": "orchestrator_not_attached"},
                )
            reset_attrs = (
                "_recent_errors",
                "_consecutive_failures",
                "_last_error",
                "_last_degraded_reason",
            )
            for attr in reset_attrs:
                if not hasattr(orch, attr):
                    continue
                try:
                    cur = getattr(orch, attr, None)
                    if isinstance(cur, int):
                        try:
                            setattr(orch, attr, 0)
                        except Exception:  # noqa: BLE001
                            pass
                    elif isinstance(cur, list):
                        try:
                            setattr(orch, attr, [])
                        except Exception:  # noqa: BLE001
                            pass
                    else:
                        try:
                            setattr(orch, attr, None)
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    continue
            details["reset"] = True
            details["target"] = "orchestrator_temp_state"
            return self._action_result(
                action=RECOVERY_ACTION_RESET_TEMP_STATE,
                target="runtime",
                result="ok",
                details=details,
            )
        except Exception as exc:  # noqa: BLE001
            return self._action_result(
                action=RECOVERY_ACTION_RESET_TEMP_STATE,
                target="runtime",
                result="error",
                details={"reason": "exception"},
                error=repr(exc),
            )

    def _action_mark_degraded(self) -> Dict[str, Any]:
        """标记 Runtime 为 degraded(只设置标记,不执行写入)。"""
        details: Dict[str, Any] = {"marked": False}
        try:
            orch = self._orchestrator
            if orch is None:
                return self._action_result(
                    action=RECOVERY_ACTION_MARK_DEGRADED,
                    target="runtime",
                    result="skipped",
                    details={"reason": "orchestrator_not_attached"},
                )
            # 不直接修改业务字段;只触发 orchestrator 自身的标记方法(若有)
            mark_fn = getattr(orch, "mark_degraded", None)
            if callable(mark_fn):
                try:
                    mark_fn()
                    details["marked"] = True
                    details["method"] = "mark_degraded"
                    return self._action_result(
                        action=RECOVERY_ACTION_MARK_DEGRADED,
                        target="runtime",
                        result="ok",
                        details=details,
                    )
                except Exception as exc:  # noqa: BLE001
                    return self._action_result(
                        action=RECOVERY_ACTION_MARK_DEGRADED,
                        target="runtime",
                        result="error",
                        details={"reason": "mark_failed", "error": repr(exc)},
                        error=repr(exc),
                    )
            # fallback:不修改任何字段,仅记录(不破坏只读边界)
            details["marked"] = False
            details["method"] = "noop_no_method"
            return self._action_result(
                action=RECOVERY_ACTION_MARK_DEGRADED,
                target="runtime",
                result="skipped",
                details=details,
            )
        except Exception as exc:  # noqa: BLE001
            return self._action_result(
                action=RECOVERY_ACTION_MARK_DEGRADED,
                target="runtime",
                result="error",
                details={"reason": "exception"},
                error=repr(exc),
            )

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------

    def _action_result(
        self,
        action: str,
        target: Optional[str],
        result: str,
        details: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "action": str(action),
            "target": target,
            "result": str(result or "error"),
            "error": error,
            "details": dict(details or {}),
        }

    def _build_report(
        self,
        success: bool,
        actions: List[Dict[str, Any]],
        issues_addressed: int,
        actions_executed: int,
        actions_skipped: int,
        actions_errored: int,
        top_error: Optional[str],
    ) -> Dict[str, Any]:
        with self._lock:
            self._recover_count += 1
            self._last_recover_ts = _now_iso()
            self._total_actions_executed += int(actions_executed)
            self._total_actions_errored += int(actions_errored)
            self._last_error = top_error
        return {
            "schema_version": self.SCHEMA_VERSION,
            "success": bool(success),
            "actions": list(actions),
            "issues_addressed": int(issues_addressed),
            "actions_executed": int(actions_executed),
            "actions_skipped": int(actions_skipped),
            "actions_errored": int(actions_errored),
            "timestamp": self._last_recover_ts,
            "recovery": {
                "name": self.NAME,
                "version": self.VERSION,
                "schema_version": self.SCHEMA_VERSION,
            },
            "error": top_error,
        }

    # --------------------------------------------------------
    # 状态查询(只读)
    # --------------------------------------------------------

    @property
    def recover_count(self) -> int:
        with self._lock:
            return int(self._recover_count)

    @property
    def last_recover_ts(self) -> Optional[str]:
        with self._lock:
            return self._last_recover_ts

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "recover_count": int(self._recover_count),
                "last_recover_ts": self._last_recover_ts,
                "last_error": self._last_error,
                "total_actions_executed": int(self._total_actions_executed),
                "total_actions_errored": int(self._total_actions_errored),
                "has_observer": self._observer is not None,
                "has_orchestrator": self._orchestrator is not None,
            }


# ============================================================
# 动作处理器表(用于 _execute_action 分发)
# ============================================================

_ACTION_HANDLERS: Dict[str, Any] = {
    RECOVERY_ACTION_CLEAR_OBSERVER_CACHE: RuntimeRecovery._action_clear_observer_cache,
    RECOVERY_ACTION_REATTACH_ADAPTERS: RuntimeRecovery._action_reattach_adapters,
    RECOVERY_ACTION_RECHECK_HEALTH: RuntimeRecovery._action_recheck_health,
    RECOVERY_ACTION_RESET_TEMP_STATE: RuntimeRecovery._action_reset_temp_state,
    RECOVERY_ACTION_MARK_DEGRADED: RuntimeRecovery._action_mark_degraded,
}


# ============================================================
# 内部辅助:health 归一化
# ============================================================


def _extract_healthy(health: Any) -> bool:
    try:
        if not isinstance(health, dict):
            return False
        if "healthy" in health:
            return bool(health["healthy"])
        status = str(health.get("status", "") or "").strip().lower()
        return status == "healthy"
    except Exception:  # noqa: BLE001
        return False


# ============================================================
# 工厂 / 辅助 API
# ============================================================


def create_runtime_recovery(
    observer: Any = None,
    orchestrator: Any = None,
    max_actions_per_issue: int = 4,
    fail_fast: bool = False,
) -> RuntimeRecovery:
    """工厂函数:创建一个 RuntimeRecovery。"""
    return RuntimeRecovery(
        observer=observer,
        orchestrator=orchestrator,
        max_actions_per_issue=max_actions_per_issue,
        fail_fast=fail_fast,
    )


def safe_recover(
    recovery: Optional[RuntimeRecovery],
    diagnosis: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """全局安全 recover(任何异常都吸收)"""
    if recovery is None:
        recovery = create_runtime_recovery()
    try:
        return recovery.recover(diagnosis) or {}
    except Exception:  # noqa: BLE001
        return {
            "schema_version": RUNTIME_RECOVERY_SCHEMA_VERSION,
            "success": False,
            "actions": [],
            "issues_addressed": 0,
            "actions_executed": 0,
            "actions_skipped": 0,
            "actions_errored": 0,
            "timestamp": _now_iso(),
            "recovery": {
                "name": RUNTIME_RECOVERY_NAME,
                "version": RUNTIME_RECOVERY_VERSION,
                "schema_version": RUNTIME_RECOVERY_SCHEMA_VERSION,
            },
            "error": "safe_recover_exception",
        }


# ============================================================
# 公共 API
# ============================================================


__all__ = [
    "RUNTIME_RECOVERY_SCHEMA_VERSION",
    "RUNTIME_RECOVERY_NAME",
    "RUNTIME_RECOVERY_VERSION",
    "RECOVERY_ACTION_CLEAR_OBSERVER_CACHE",
    "RECOVERY_ACTION_REATTACH_ADAPTERS",
    "RECOVERY_ACTION_RECHECK_HEALTH",
    "RECOVERY_ACTION_RESET_TEMP_STATE",
    "RECOVERY_ACTION_MARK_DEGRADED",
    "ALL_RECOVERY_ACTIONS",
    "TYPE_TO_ACTIONS",
    "SEVERITY_TO_ACTIONS",
    "RuntimeRecovery",
    "create_runtime_recovery",
    "safe_recover",
    "_now_iso",
]
