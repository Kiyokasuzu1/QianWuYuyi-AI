"""
SelfModelProvider —— Admin 层的 SelfModel 视图只读 Provider

职责：
- 暴露羽依 SelfModel 的可视化数据给 Admin Panel
- 复用 Phase 6.1~6.4 既有 SelfModel 基础设施
- 不创建任何 store / authority 实例；只通过 RuntimeProvider/RuntimeBridge 读取
- 不修改 SelfBelief / SelfHistory / SelfReflection / SelfModelAdapter
- 不修改 SelfModelHealthChecker / SelfModelRetention
- 不修改 SelfModelBootstrap

数据流：
    Admin API
       ↓
    SelfModelProvider（只读桥接层）
       ↓
    RuntimeProvider → RuntimeBridge → RuntimeCore
       ↓                ↓
    adapter          bootstrap
       ↓
    _beliefs / _history / _reflections

允许：
    Admin → SelfModelProvider → list_beliefs / get_belief_why / get_evolution_timeline / ...
    Admin → SelfModelProvider → get_health_report（只读，不修复）
    Admin → SelfModelProvider → run_retention_dry_run（dry_run=True，不修改源数据）

禁止：
    Admin → SelfBeliefStore()            ❌
    Admin → SelfModelAdapter()           ❌
    Admin → mutate(any store)            ❌
    Admin → enforce_retention(dry_run=False)  ❌  # 永远只 dry_run
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 工具：安全获取属性
# ============================================================

def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# SelfModelProvider
# ============================================================

class SelfModelProvider:
    """
    Admin 层的 SelfModel 视图 Provider。

    设计原则（与 RuntimeProvider 一致）：
    - 单一职责：仅作为 Admin 与 SelfModel 之间的数据桥
    - 只读：所有方法只返回快照，不修改任何状态
    - 容错：RuntimeBridge 不可用时返回安全的 fallback
    - 无状态：所有数据从 RuntimeProvider/RuntimeBridge 实时获取

    注意：run_retention_dry_run 调用 SelfModelRetention.enforce_all(dry_run=True)
          dry_run=True 时不修改源数据（设计约束）。
    """

    def __init__(self, runtime_provider: Optional[Any] = None) -> None:
        self._runtime_provider = runtime_provider
        self._bridge = None
        self._bridge_error: Optional[str] = None
        self._try_init_bridge()

    def _try_init_bridge(self) -> None:
        """尝试获取 RuntimeBridge 单例。失败不抛异常。"""
        try:
            if self._runtime_provider is not None:
                self._bridge = self._runtime_provider.get_runtime_bridge()
            else:
                from src.admin.runtime_provider import get_runtime_provider
                self._runtime_provider = get_runtime_provider()
                self._bridge = self._runtime_provider.get_runtime_bridge()
        except Exception as e:
            self._bridge = None
            self._bridge_error = str(e)
            logger.debug(f"SelfModelProvider: RuntimeBridge 不可用: {e}")

    def _get_core(self) -> Any:
        if self._bridge is None:
            return None
        try:
            return self._bridge.get_runtime_core()
        except Exception:
            return None

    def _get_adapter(self) -> Any:
        """获取 SelfModelAdapter（如果未初始化则返回 None）。"""
        core = self._get_core()
        if core is None:
            return None
        try:
            if hasattr(core, "get_self_model_adapter"):
                return core.get_self_model_adapter()
        except Exception:
            pass
        return None

    def _get_bootstrap(self) -> Any:
        """获取 SelfModelBootstrap（如果未初始化则返回 None）。"""
        core = self._get_core()
        if core is None:
            return None
        try:
            if hasattr(core, "get_self_model_bootstrap"):
                return core.get_self_model_bootstrap()
        except Exception:
            pass
        return None

    # ============================================================
    # 状态
    # ============================================================

    def get_status(self) -> Dict[str, Any]:
        """
        SelfModel 子系统总状态。

        Returns:
            {
                "available": bool,         # adapter 是否就绪
                "bridge_error": str|None,
                "bootstrap": {...}         # bootstrap 状态（若有）
            }
        """
        adapter = self._get_adapter()
        bootstrap = self._get_bootstrap()
        return {
            "available": adapter is not None,
            "bridge_error": self._bridge_error,
            "bootstrap": self._read_bootstrap(bootstrap) if bootstrap is not None else None,
        }

    def _read_bootstrap(self, bootstrap: Any) -> Dict[str, Any]:
        try:
            return {
                "data_dir": _safe_get(bootstrap, "data_dir", ""),
                "persistence_attached": bool(_safe_get(bootstrap, "_persistence_attached", False)),
                "last_load_counts": dict(_safe_get(bootstrap, "last_load_counts", {}) or {}),
                "last_save_result": _safe_get(bootstrap, "last_save_result", None),
                "last_error": _safe_get(bootstrap, "_last_error", None),
            }
        except Exception as e:
            logger.debug(f"SelfModelProvider._read_bootstrap 失败: {e}")
            return {"error": str(e)}

    # ============================================================
    # Identity
    # ============================================================

    def get_identity(self) -> Dict[str, Any]:
        """
        获取 Identity 摘要。

        数据源优先级：
        1. SelfModelStore.get_active_self_model() → SelfModelV3
        2. core.personality_resolver.state 摘要
        3. fallback: 仅返回 identity_name

        Returns:
            {
                "available": bool,
                "identity_name": str,
                "identity_id": str|None,
                "core_values": List[str],
                "version": str|None,
                "source": "self_model_v3" | "resolver" | "fallback",
                "error": str|None
            }
        """
        result: Dict[str, Any] = {
            "available": False,
            "identity_name": "",  # P1-2: 不写死人格名
            "identity_id": None,
            "core_values": [],
            "version": None,
            "source": "fallback",
            "error": None,
        }
        try:
            # 1) 尝试 SelfModelStore.get_active_self_model()
            core = self._get_core()
            if core is not None and hasattr(core, "get_self_model_store"):
                try:
                    sms = core.get_self_model_store()
                    if sms is not None and hasattr(sms, "get_active_self_model"):
                        v3 = sms.get_active_self_model()
                        if v3 is not None:
                            result.update({
                                "available": True,
                                "identity_name": _safe_get(v3, "identity", "") or "",  # P1-2: 不写死人格名
                                "version": "v3",
                                "source": "self_model_v3",
                            })
                            return result
                except Exception as e:
                    logger.debug(f"SelfModelProvider.get_identity: SelfModelStore 失败: {e}")

            # 2) 尝试 resolver.state
            if core is not None and hasattr(core, "get_personality_resolver"):
                try:
                    resolver = core.get_personality_resolver()
                    if resolver is not None and hasattr(resolver, "state") and resolver.state is not None:
                        state = resolver.state
                        result.update({
                            "available": True,
                            "identity_name": "",  # P1-2: 不写死人格名
                            "core_values": list(_safe_get(state, "core_values", []) or []),
                            "version": "resolver",
                            "source": "resolver",
                        })
                        return result
                except Exception as e:
                    logger.debug(f"SelfModelProvider.get_identity: resolver 失败: {e}")
        except Exception as e:
            result["error"] = str(e)
        return result

    # ============================================================
    # Beliefs
    # ============================================================

    def list_beliefs(
        self,
        domain: Optional[str] = None,
        min_confidence: float = 0.0,
        include_inactive: bool = True,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        列出 SelfBelief。

        Returns:
            {
                "available": bool,
                "total": int,
                "active": int,
                "inactive": int,
                "by_domain": Dict[str, int],
                "items": List[Dict],
                "error": str|None
            }
        """
        result: Dict[str, Any] = {
            "available": False,
            "total": 0,
            "active": 0,
            "inactive": 0,
            "by_domain": {},
            "items": [],
            "error": None,
        }
        try:
            adapter = self._get_adapter()
            if adapter is None:
                result["error"] = "self_model_adapter unavailable"
                return result
            store = _safe_get(adapter, "_beliefs", None)
            if store is None or not hasattr(store, "all"):
                result["error"] = "beliefs_store unavailable"
                return result
            result["available"] = True
            all_beliefs = list(store.all())
            result["total"] = len(all_beliefs)
            by_domain: Dict[str, int] = {}
            items: List[Dict[str, Any]] = []
            for b in all_beliefs:
                try:
                    active = bool(_safe_get(b, "active", True))
                    if not include_inactive and not active:
                        continue
                    conf = _safe_float(_safe_get(b, "confidence", 0.0))
                    if conf < float(min_confidence):
                        continue
                    d = _safe_get(b, "domain", "value") or "value"
                    if domain and d != domain:
                        continue
                    by_domain[d] = by_domain.get(d, 0) + 1
                    if active:
                        result["active"] += 1
                    else:
                        result["inactive"] += 1
                    items.append({
                        "belief_id": _safe_get(b, "belief_id", ""),
                        "domain": d,
                        "content": _safe_get(b, "content", ""),
                        "confidence": round(conf, 4),
                        "version": int(_safe_get(b, "version", 1) or 1),
                        "evidence_count": int(_safe_get(b, "evidence_count", 1) or 1),
                        "sources": list(_safe_get(b, "sources", []) or []),
                        "active": active,
                        "first_seen": _safe_get(b, "first_seen", ""),
                        "last_confirmed": _safe_get(b, "last_confirmed", ""),
                    })
                except Exception:
                    continue
            # 按 confidence desc, last_confirmed desc 排序
            items.sort(
                key=lambda x: (x["confidence"], x["last_confirmed"]),
                reverse=True,
            )
            result["by_domain"] = by_domain
            result["items"] = items[: max(0, int(limit))]
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"SelfModelProvider.list_beliefs 失败: {e}")
        return result

    # ============================================================
    # Stable Traits (稳定特质: SelfModelV3 长期自我认知, 不是 Resolver.current 即时切片)
    # ============================================================
    def list_stable_traits(
        self,
        min_stability: float = 0.0,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        列出 SelfModelV3 中已经稳定下来的人格特质(≠ TraitState 当前人格)。

        两个不同源,禁止在桌面端互相赋值冒充。
        - Resolver.current → Dashboard TraitsCard "当前人格"(会小幅度抖动)
        - SelfModelV3.stable_traits → Archive StablePortraitCard "稳定自我认知"(变化慢)

        Returns:
            {
                "available": bool,
                "total": int,
                "items": List[{
                    "trait_id": str,
                    "name": str,
                    "value_pct": int (0-100),
                    "stability": float (0-1),
                    "evidence_count": int,
                    "first_observed": str|None,
                    "last_observed": str|None,
                    "trend_30d": float|None (正=上升, 负=下降, None=数据不足),
                }],
                "error": str|None,
                "source": "selfmodel_v3_stable"|None
            }
        """
        result: Dict[str, Any] = {
            "available": False,
            "total": 0,
            "items": [],
            "error": None,
            "source": None,
        }
        try:
            core = self._get_core()
            if core is None or not hasattr(core, "get_self_model_store"):
                result["error"] = "runtime_core_unavailable"
                return result
            sms = core.get_self_model_store()
            if sms is None or not hasattr(sms, "get_active_self_model"):
                result["error"] = "selfmodel_store_unavailable"
                return result
            v3 = sms.get_active_self_model()
            if v3 is None:
                result["error"] = "active_self_model_missing"
                return result
            traits_raw = _safe_get(v3, "stable_traits", None)
            if traits_raw is None or not hasattr(traits_raw, "__iter__"):
                # Fallback: 有些版本将 stable_traits 存在其他属性名上,尽力兼容
                for alt in ("stable_personality_traits", "long_term_traits", "_stable_traits"):
                    candidate = _safe_get(v3, alt, None)
                    if candidate is not None and hasattr(candidate, "__iter__"):
                        traits_raw = candidate
                        break
            if traits_raw is None or not hasattr(traits_raw, "__iter__"):
                # 没有任何稳定特质(可能用户刚启动,还没形成),不视为 error,返回空
                result["available"] = True
                result["source"] = "selfmodel_v3_stable"
                result["total"] = 0
                return result
            result["available"] = True
            result["source"] = "selfmodel_v3_stable"
            items: List[Dict[str, Any]] = []
            for t in traits_raw:
                try:
                    stab = _safe_float(_safe_get(t, "stability", 0.0))
                    if stab < float(min_stability):
                        continue
                    raw_val = _safe_float(_safe_get(t, "value", 0.0))
                    value_pct = int(round(max(0.0, min(1.0, raw_val)) * 100.0))
                    trend_raw = _safe_get(t, "trend_30d", None)
                    trend_30d = _safe_float(trend_raw) if trend_raw is not None else None
                    items.append({
                        "trait_id": _safe_get(t, "trait_id", "") or _safe_get(t, "id", "") or "",
                        "name": _safe_get(t, "name", "") or _safe_get(t, "label", "") or "",
                        "value_pct": value_pct,
                        "stability": round(stab, 4),
                        "evidence_count": int(_safe_get(t, "evidence_count", 0) or 0),
                        "first_observed": _safe_get(t, "first_observed", None),
                        "last_observed": _safe_get(t, "last_observed", None),
                        "trend_30d": (round(trend_30d, 4) if trend_30d is not None else None),
                    })
                except Exception:
                    continue
            # 排序: 稳定性 desc → evidence desc → name asc
            items.sort(
                key=lambda x: (-x["stability"], -x["evidence_count"], x["name"])
            )
            result["total"] = len(items)
            off = max(0, int(offset or 0))
            lim = max(1, min(500, int(limit or 100)))
            result["items"] = items[off:off + lim]
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"SelfModelProvider.list_stable_traits 失败: {e}")
        return result

    # ============================================================
    # History
    # ============================================================

    def list_history(
        self,
        event_type: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        列出 SelfHistory 事件。

        Returns:
            {
                "available": bool,
                "total": int,
                "by_event_type": Dict[str, int],
                "items": List[Dict],
                "error": str|None
            }
        """
        result: Dict[str, Any] = {
            "available": False,
            "total": 0,
            "by_event_type": {},
            "items": [],
            "error": None,
        }
        try:
            adapter = self._get_adapter()
            if adapter is None:
                result["error"] = "self_model_adapter unavailable"
                return result
            history = _safe_get(adapter, "_history", None)
            if history is None or not hasattr(history, "all"):
                result["error"] = "history unavailable"
                return result
            result["available"] = True
            events = list(history.all())
            result["total"] = len(events)
            by_type: Dict[str, int] = {}
            items: List[Dict[str, Any]] = []
            for ev in events:
                try:
                    et = _safe_get(ev, "event_type", "") or ""
                    ts = _safe_get(ev, "timestamp", "") or ""
                    if event_type and et != event_type:
                        continue
                    if since and ts and ts < since:
                        continue
                    if until and ts and ts > until:
                        continue
                    by_type[et] = by_type.get(et, 0) + 1
                    items.append({
                        "event_id": _safe_get(ev, "event_id", ""),
                        "event_type": et,
                        "timestamp": ts,
                        "source_type": _safe_get(ev, "source_type", ""),
                        "source_id": _safe_get(ev, "source_id", ""),
                        "summary": _safe_get(ev, "summary", ""),
                        "affected_traits": dict(_safe_get(ev, "affected_traits", {}) or {}),
                        "affected_beliefs": list(_safe_get(ev, "affected_beliefs", []) or []),
                        "actor": _safe_get(ev, "actor", ""),
                    })
                except Exception:
                    continue
            # 时间倒序
            items.sort(key=lambda x: x["timestamp"], reverse=True)
            result["by_event_type"] = by_type
            result["items"] = items[: max(0, int(limit))]
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"SelfModelProvider.list_history 失败: {e}")
        return result

    # ============================================================
    # Reflections
    # ============================================================

    def list_reflections(
        self,
        trigger_source: Optional[str] = None,
        reflection_type: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        列出 SelfReflection 笔记。

        Returns:
            {
                "available": bool,
                "total": int,
                "by_reflection_type": Dict[str, int],
                "by_trigger_source": Dict[str, int],
                "items": List[Dict],
                "error": str|None
            }
        """
        result: Dict[str, Any] = {
            "available": False,
            "total": 0,
            "by_reflection_type": {},
            "by_trigger_source": {},
            "items": [],
            "error": None,
        }
        try:
            adapter = self._get_adapter()
            if adapter is None:
                result["error"] = "self_model_adapter unavailable"
                return result
            store = _safe_get(adapter, "_reflections", None)
            if store is None or not hasattr(store, "all"):
                result["error"] = "reflections_store unavailable"
                return result
            result["available"] = True
            notes = list(store.all())
            result["total"] = len(notes)
            by_rt: Dict[str, int] = {}
            by_ts: Dict[str, int] = {}
            items: List[Dict[str, Any]] = []
            for n in notes:
                try:
                    ts = _safe_get(n, "trigger_source", "") or ""
                    rt = _safe_get(n, "reflection_type", "") or ""
                    conf = _safe_float(_safe_get(n, "confidence", 0.0))
                    if trigger_source and ts != trigger_source:
                        continue
                    if reflection_type and rt != reflection_type:
                        continue
                    if conf < float(min_confidence):
                        continue
                    by_rt[rt] = by_rt.get(rt, 0) + 1
                    by_ts[ts] = by_ts.get(ts, 0) + 1
                    items.append({
                        "note_id": _safe_get(n, "note_id", ""),
                        "timestamp": _safe_get(n, "timestamp", ""),
                        "trigger_source": ts,
                        "reflection_type": rt,
                        "content": _safe_get(n, "content", ""),
                        "confidence": round(conf, 4),
                        "related_belief_ids": list(_safe_get(n, "related_belief_ids", []) or []),
                        "related_trait_changes": dict(_safe_get(n, "related_trait_changes", {}) or {}),
                        "sources": list(_safe_get(n, "sources", []) or []),
                    })
                except Exception:
                    continue
            items.sort(key=lambda x: x["timestamp"], reverse=True)
            result["by_reflection_type"] = by_rt
            result["by_trigger_source"] = by_ts
            result["items"] = items[: max(0, int(limit))]
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"SelfModelProvider.list_reflections 失败: {e}")
        return result

    # ============================================================
    # Why 追溯（单条 belief）
    # ============================================================

    def get_belief_why(self, belief_id: str) -> Dict[str, Any]:
        """
        解释"为什么羽依现在这样认为"。

        复用 audit.self_model_audit.explain_why_belief()。
        """
        result: Dict[str, Any] = {
            "available": False,
            "belief": None,
            "origin": None,
            "pcr_link": None,
            "answer": "",
            "error": None,
        }
        if not belief_id:
            result["error"] = "belief_id required"
            return result
        try:
            adapter = self._get_adapter()
            if adapter is None:
                result["error"] = "self_model_adapter unavailable"
                return result
            beliefs = _safe_get(adapter, "_beliefs", None)
            history = _safe_get(adapter, "_history", None)
            reflections = _safe_get(adapter, "_reflections", None)
            from src.audit.self_model_audit import explain_why_belief
            out = explain_why_belief(
                belief_id=belief_id,
                beliefs_store=beliefs,
                history=history,
                reflections_store=reflections,
            )
            result["available"] = True
            result["belief"] = out.get("belief")
            result["origin"] = out.get("origin")
            result["pcr_link"] = out.get("pcr_link")
            result["answer"] = out.get("answer", "")
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"SelfModelProvider.get_belief_why 失败: {e}")
        return result

    # ============================================================
    # Evolution Timeline
    # ============================================================

    def get_evolution_timeline(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 200,
        sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        构建跨 belief/history/reflection/audit 的统一时间线。

        复用 audit.self_model_audit.build_evolution_timeline()。
        """
        result: Dict[str, Any] = {
            "available": False,
            "total": 0,
            "by_source": {},
            "items": [],
            "error": None,
        }
        try:
            adapter = self._get_adapter()
            if adapter is None:
                result["error"] = "self_model_adapter unavailable"
                return result
            beliefs = _safe_get(adapter, "_beliefs", None)
            history = _safe_get(adapter, "_history", None)
            reflections = _safe_get(adapter, "_reflections", None)
            from src.audit.self_model_audit import build_evolution_timeline
            items = build_evolution_timeline(
                beliefs_store=beliefs,
                history=history,
                reflections_store=reflections,
                start=start,
                end=end,
                limit=max(1, int(limit)) * 5,  # 提前取多，过滤后再截断
            )
            # 过滤 sources
            if sources:
                src_set = set(sources)
                items = [it for it in items if it.get("source") in src_set]
            items = items[: max(0, int(limit))]
            # 统计
            by_source: Dict[str, int] = {}
            for it in items:
                s = it.get("source", "unknown")
                by_source[s] = by_source.get(s, 0) + 1
            result["available"] = True
            result["total"] = len(items)
            result["by_source"] = by_source
            result["items"] = items
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"SelfModelProvider.get_evolution_timeline 失败: {e}")
        return result

    # ============================================================
    # PCR Link
    # ============================================================

    def get_pcr_related_events(self, proposal_id: str) -> Dict[str, Any]:
        """
        查找与 proposal_id 相关的所有事件（PCR 链路）。

        复用 audit.self_model_audit.find_pcr_related_events()。
        """
        result: Dict[str, Any] = {
            "available": False,
            "proposal_id": proposal_id,
            "history_events": [],
            "linked_beliefs": [],
            "linked_reflections": [],
            "audit_records": [],
            "summary": {
                "history_count": 0,
                "belief_count": 0,
                "reflection_count": 0,
                "audit_count": 0,
            },
            "error": None,
        }
        if not proposal_id:
            result["error"] = "proposal_id required"
            return result
        try:
            adapter = self._get_adapter()
            if adapter is None:
                result["error"] = "self_model_adapter unavailable"
                return result
            beliefs = _safe_get(adapter, "_beliefs", None)
            history = _safe_get(adapter, "_history", None)
            reflections = _safe_get(adapter, "_reflections", None)
            from src.audit.self_model_audit import find_pcr_related_events
            out = find_pcr_related_events(
                proposal_id=proposal_id,
                beliefs_store=beliefs,
                history=history,
                reflections_store=reflections,
                include_audit=True,
            )
            result["available"] = True
            result["history_events"] = out.get("history_events", [])
            result["linked_beliefs"] = out.get("linked_beliefs", [])
            result["linked_reflections"] = out.get("linked_reflections", [])
            result["audit_records"] = out.get("audit_records", [])
            result["summary"] = out.get("summary", result["summary"])
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"SelfModelProvider.get_pcr_related_events 失败: {e}")
        return result

    # ============================================================
    # Health
    # ============================================================

    def get_health_report(self) -> Dict[str, Any]:
        """
        运行 SelfModelHealthChecker.check()。

        不修复；只读报告。
        """
        result: Dict[str, Any] = {
            "available": False,
            "report": None,
            "error": None,
        }
        try:
            adapter = self._get_adapter()
            if adapter is None:
                result["error"] = "self_model_adapter unavailable"
                return result
            beliefs = _safe_get(adapter, "_beliefs", None)
            history = _safe_get(adapter, "_history", None)
            reflections = _safe_get(adapter, "_reflections", None)
            persistence = _safe_get(adapter, "_persistence", None)

            from src.personality.self_model_health import SelfModelHealthChecker
            checker = SelfModelHealthChecker(persistence=persistence)
            report = checker.check(
                beliefs_store=beliefs,
                history=history,
                reflections_store=reflections,
            )
            result["available"] = True
            result["report"] = report.to_dict()
            result["summary"] = report.summary()
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"SelfModelProvider.get_health_report 失败: {e}")
        return result

    # ============================================================
    # Retention
    # ============================================================

    def get_retention_status(self) -> Dict[str, Any]:
        """
        返回 Retention 摘要（不执行 enforce，只读 archive 状态）。

        注意：当前 SelfModelRetention 没有持久化 archive 状态（_archived_* 是
        内存 Set）。如果 adapter 重新创建，archive 会清零。这里返回：
        - 静态配额阈值
        - 当前各 store 数量
        - retention 报告（最近一次 dry_run，若有）
        """
        result: Dict[str, Any] = {
            "available": False,
            "thresholds": {},
            "current_counts": {},
            "summary": None,
            "error": None,
        }
        try:
            adapter = self._get_adapter()
            if adapter is None:
                result["error"] = "self_model_adapter unavailable"
                return result
            beliefs = _safe_get(adapter, "_beliefs", None)
            history = _safe_get(adapter, "_history", None)
            reflections = _safe_get(adapter, "_reflections", None)

            beliefs_total = 0
            beliefs_active = 0
            try:
                if beliefs is not None and hasattr(beliefs, "all"):
                    all_bs = list(beliefs.all())
                    beliefs_total = len(all_bs)
                    beliefs_active = sum(1 for b in all_bs if bool(_safe_get(b, "active", True)))
            except Exception:
                pass
            history_total = 0
            try:
                if history is not None and hasattr(history, "all"):
                    history_total = len(list(history.all()))
            except Exception:
                pass
            reflections_total = 0
            try:
                if reflections is not None and hasattr(reflections, "all"):
                    reflections_total = len(list(reflections.all()))
            except Exception:
                pass

            from src.personality.self_model_retention import (
                DEFAULT_MAX_ACTIVE_BELIEFS,
                DEFAULT_MIN_CONFIDENCE_FOR_ACTIVE,
                DEFAULT_MAX_IN_MEMORY_EVENTS,
                DEFAULT_IMPORTANCE_LOW_BELOW,
                DEFAULT_RECENT_WINDOW_DAYS,
                DEFAULT_HIGH_VALUE_MIN_CONFIDENCE,
            )
            result["thresholds"] = {
                "max_active_beliefs": DEFAULT_MAX_ACTIVE_BELIEFS,
                "min_confidence_for_active": DEFAULT_MIN_CONFIDENCE_FOR_ACTIVE,
                "max_in_memory_events": DEFAULT_MAX_IN_MEMORY_EVENTS,
                "importance_low_below": DEFAULT_IMPORTANCE_LOW_BELOW,
                "recent_window_days": DEFAULT_RECENT_WINDOW_DAYS,
                "high_value_min_confidence": DEFAULT_HIGH_VALUE_MIN_CONFIDENCE,
            }
            result["current_counts"] = {
                "beliefs_total": beliefs_total,
                "beliefs_active": beliefs_active,
                "beliefs_inactive": beliefs_total - beliefs_active,
                "history_total": history_total,
                "reflections_total": reflections_total,
            }
            result["available"] = True
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"SelfModelProvider.get_retention_status 失败: {e}")
        return result

    def run_retention_dry_run(self) -> Dict[str, Any]:
        """
        执行 retention dry-run（不修改源数据）。

        强制 dry_run=True，输出 RetentionReport.to_dict()。
        """
        result: Dict[str, Any] = {
            "available": False,
            "report": None,
            "dry_run": True,
            "applied": False,
            "error": None,
        }
        try:
            adapter = self._get_adapter()
            if adapter is None:
                result["error"] = "self_model_adapter unavailable"
                return result
            beliefs = _safe_get(adapter, "_beliefs", None)
            history = _safe_get(adapter, "_history", None)
            reflections = _safe_get(adapter, "_reflections", None)
            from src.personality.self_model_retention import SelfModelRetention
            retention = SelfModelRetention()
            report = retention.enforce_all(
                beliefs_store=beliefs,
                history=history,
                reflections_store=reflections,
                dry_run=True,
            )
            result["available"] = True
            result["report"] = report.to_dict()
            result["summary"] = report.summary()
            result["applied"] = bool(report.applied)
        except Exception as e:
            result["error"] = str(e)
            logger.warning(f"SelfModelProvider.run_retention_dry_run 失败: {e}")
        return result


# ============================================================
# 模块级单例
# ============================================================

_provider_instance: Optional[SelfModelProvider] = None


def get_self_model_provider() -> SelfModelProvider:
    """获取 SelfModelProvider 单例。"""
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = SelfModelProvider()
    return _provider_instance


def reset_self_model_provider_for_testing() -> None:
    """测试用：重置单例。"""
    global _provider_instance
    _provider_instance = None


__all__ = [
    "SelfModelProvider",
    "get_self_model_provider",
    "reset_self_model_provider_for_testing",
]
