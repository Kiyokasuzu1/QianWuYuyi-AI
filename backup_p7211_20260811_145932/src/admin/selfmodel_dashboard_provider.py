# -*- coding: utf-8 -*-
"""
src/admin/selfmodel_dashboard_provider.py

Phase 5.0 Dashboard Upgrade Step 7.2 —— SelfModel Dashboard Provider。

职责:
- 只读提供 SelfModel 数据给 Dashboard
- 数据源:已有 SelfModelProvider(不直接 import 业务模块)
- 提供 Identity / Traits / Capabilities / Beliefs / Evolution Timeline / Health

约束:
- 禁止 import 任何 src/personality / src/runtime.self_model 业务模块
- 仅通过 src.admin.self_model_provider 访问
- 严格容错,任何子组件不可用时返回 fallback
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def _safe_call(fn, *args: Any, **kwargs: Any) -> Any:
    try:
        if fn is None:
            return None
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("selfmodel_dashboard_provider safe_call 失败: %s", exc)
        return None


# 已知的 trait/capability 域(用于从 beliefs 派生)
_KNOWN_TRAIT_DOMAINS = {"trait", "value", "preference", "personality", "identity"}
_KNOWN_CAPABILITY_DOMAINS = {"capability", "skill", "ability"}


class SelfModelDashboardProvider:
    """
    SelfModel Dashboard 只读 Provider。

    注入:
        self_model_provider: 已有 SelfModelProvider(测试时可注入 mock)
    """

    def __init__(self, self_model_provider: Optional[Any] = None) -> None:
        self._lock = threading.RLock()
        self._self_model_provider = self_model_provider

    def _get_smp(self) -> Optional[Any]:
        with self._lock:
            if self._self_model_provider is not None:
                return self._self_model_provider
            try:
                from src.admin.self_model_provider import get_self_model_provider
                self._self_model_provider = get_self_model_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("SelfModelDashboardProvider: SelfModelProvider 不可用: %s", exc)
                self._self_model_provider = None
            return self._self_model_provider

    # --------------------------------------------------------
    # Identity
    # --------------------------------------------------------
    def get_identity(self) -> Dict[str, Any]:
        smp = self._get_smp()
        if smp is None:
            return self._empty_identity(reason="self_model_provider_unavailable")
        fn = _safe_get(smp, "get_identity")
        if not callable(fn):
            return self._empty_identity(reason="get_identity_not_callable")
        try:
            data = fn()
        except Exception as exc:  # noqa: BLE001
            logger.debug("SelfModelDashboardProvider.get_identity 异常: %s", exc)
            return self._empty_identity(reason=f"identity_error:{type(exc).__name__}")
        if not isinstance(data, dict):
            return self._empty_identity(reason="invalid_identity_payload")
        if not bool(data.get("available", False)):
            return self._empty_identity(reason=data.get("error") or "identity_unavailable")
        return {
            "available": True,
            "identity_name": str(data.get("identity_name", "") or ""),  # P1-2: 不写死人格名
            "identity_id": data.get("identity_id"),
            "core_values": list(data.get("core_values", []) or []),
            "version": data.get("version"),
            "source": data.get("source", "self_model"),
            "error": data.get("error"),
            "anchor": str(data.get("anchor", "") or ""),
            "stable": bool(data.get("stable", False)),
            "fallback": False,
        }

    def _empty_identity(self, *, reason: str) -> Dict[str, Any]:
        return {
            "available": False,
            "identity_name": "",  # P1-2: 不写死人格名
            "identity_id": None,
            "core_values": [],
            "version": None,
            "source": "fallback",
            "error": reason,
            "anchor": "",
            "stable": False,
            "fallback": True,
            "fallback_reason": reason,
        }

    # --------------------------------------------------------
    # Traits(从 beliefs 派生)
    # --------------------------------------------------------
    def get_traits(self) -> Dict[str, Any]:
        smp = self._get_smp()
        if smp is None:
            return self._empty_collection(reason="self_model_provider_unavailable")
        try:
            data = smp.list_beliefs(limit=200)
        except Exception as exc:  # noqa: BLE001
            logger.debug("SelfModelDashboardProvider.get_traits 异常: %s", exc)
            return self._empty_collection(reason=f"traits_error:{type(exc).__name__}")
        if not isinstance(data, dict):
            return self._empty_collection(reason="invalid_beliefs_payload")
        available = bool(data.get("available", False))
        if not available:
            return self._empty_collection(reason="beliefs_unavailable")
        items = data.get("items", []) or []
        traits: List[Dict[str, Any]] = []
        for b in items:
            domain = str(b.get("domain", "") or "")
            if domain not in _KNOWN_TRAIT_DOMAINS:
                continue
            traits.append({
                "trait_id": str(b.get("belief_id", "")),
                "domain": domain,
                "name": str(b.get("content", ""))[:60],
                "confidence": float(b.get("confidence", 0.0) or 0.0),
                "version": int(b.get("version", 1) or 1),
                "evidence_count": int(b.get("evidence_count", 1) or 1),
                "active": bool(b.get("active", True)),
            })
        return {
            "available": True,
            "total": len(traits),
            "items": traits,
            "fallback": False,
        }

    # --------------------------------------------------------
    # Capabilities(从 beliefs 派生)
    # --------------------------------------------------------
    def get_capabilities(self) -> Dict[str, Any]:
        smp = self._get_smp()
        if smp is None:
            return self._empty_collection(reason="self_model_provider_unavailable")
        try:
            data = smp.list_beliefs(limit=200)
        except Exception as exc:  # noqa: BLE001
            logger.debug("SelfModelDashboardProvider.get_capabilities 异常: %s", exc)
            return self._empty_collection(reason=f"capabilities_error:{type(exc).__name__}")
        if not isinstance(data, dict):
            return self._empty_collection(reason="invalid_beliefs_payload")
        available = bool(data.get("available", False))
        if not available:
            return self._empty_collection(reason="beliefs_unavailable")
        items = data.get("items", []) or []
        capabilities: List[Dict[str, Any]] = []
        for b in items:
            domain = str(b.get("domain", "") or "")
            if domain not in _KNOWN_CAPABILITY_DOMAINS:
                continue
            capabilities.append({
                "capability_id": str(b.get("belief_id", "")),
                "domain": domain,
                "name": str(b.get("content", ""))[:60],
                "confidence": float(b.get("confidence", 0.0) or 0.0),
                "evidence_count": int(b.get("evidence_count", 1) or 1),
                "active": bool(b.get("active", True)),
            })
        return {
            "available": True,
            "total": len(capabilities),
            "items": capabilities,
            "fallback": False,
        }

    def _empty_collection(self, *, reason: str) -> Dict[str, Any]:
        return {
            "available": False,
            "total": 0,
            "items": [],
            "fallback": True,
            "fallback_reason": reason,
        }

    # --------------------------------------------------------
    # Beliefs
    # --------------------------------------------------------
    def get_beliefs(self, limit: int = 50) -> Dict[str, Any]:
        smp = self._get_smp()
        if smp is None:
            return {
                "available": False,
                "total": 0,
                "active": 0,
                "inactive": 0,
                "by_domain": {},
                "items": [],
                "fallback": True,
                "fallback_reason": "self_model_provider_unavailable",
            }
        try:
            n = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            n = 50
        try:
            data = smp.list_beliefs(limit=n) if callable(_safe_get(smp, "list_beliefs")) else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("SelfModelDashboardProvider.get_beliefs 异常: %s", exc)
            return {
                "available": False,
                "total": 0, "active": 0, "inactive": 0,
                "by_domain": {}, "items": [],
                "fallback": True,
                "fallback_reason": f"beliefs_error:{type(exc).__name__}",
            }
        if not isinstance(data, dict):
            return {
                "available": False,
                "total": 0, "active": 0, "inactive": 0,
                "by_domain": {}, "items": [],
                "fallback": True,
                "fallback_reason": "invalid_beliefs_payload",
            }
        if not bool(data.get("available", False)):
            return {
                "available": False,
                "total": 0, "active": 0, "inactive": 0,
                "by_domain": {}, "items": [],
                "fallback": True,
                "fallback_reason": data.get("error") or "beliefs_unavailable",
            }
        return {
            "available": True,
            "total": int(data.get("total", 0) or 0),
            "active": int(data.get("active", 0) or 0),
            "inactive": int(data.get("inactive", 0) or 0),
            "by_domain": dict(data.get("by_domain", {}) or {}),
            "items": list(data.get("items", []) or []),
            "error": data.get("error"),
            "fallback": False,
        }

    # --------------------------------------------------------
    # Evolution Timeline
    # --------------------------------------------------------
    def get_evolution_timeline(self, limit: int = 50) -> Dict[str, Any]:
        smp = self._get_smp()
        if smp is None:
            return {
                "available": False,
                "total": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": "self_model_provider_unavailable",
            }
        try:
            n = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            n = 50
        try:
            data = smp.get_evolution_timeline(limit=n) if callable(_safe_get(smp, "get_evolution_timeline")) else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("SelfModelDashboardProvider.get_evolution_timeline 异常: %s", exc)
            return {
                "available": False,
                "total": 0, "items": [],
                "fallback": True,
                "fallback_reason": f"timeline_error:{type(exc).__name__}",
            }
        if not isinstance(data, dict):
            return {
                "available": False, "total": 0, "items": [],
                "fallback": True, "fallback_reason": "invalid_timeline_payload",
            }
        if not bool(data.get("available", False)):
            return {
                "available": False, "total": 0, "items": [],
                "fallback": True,
                "fallback_reason": data.get("error") or "timeline_unavailable",
            }
        return {
            "available": True,
            "total": int(data.get("total", 0) or 0),
            "items": list(data.get("items", []) or []),
            "error": data.get("error"),
            "fallback": False,
        }

    # --------------------------------------------------------
    # Health
    # --------------------------------------------------------
    def get_health(self) -> Dict[str, Any]:
        smp = self._get_smp()
        if smp is None:
            return {
                "available": False,
                "status": "unknown",
                "score": 0,
                "issues": [],
                "fallback": True,
                "fallback_reason": "self_model_provider_unavailable",
            }
        if not callable(_safe_get(smp, "get_health_report")):
            return {
                "available": False,
                "status": "unknown", "score": 0, "issues": [],
                "fallback": True,
                "fallback_reason": "health_not_callable",
            }
        try:
            data = smp.get_health_report()
        except Exception as exc:  # noqa: BLE001
            logger.debug("SelfModelDashboardProvider.get_health 异常: %s", exc)
            return {
                "available": False, "status": "unknown", "score": 0, "issues": [],
                "fallback": True,
                "fallback_reason": f"health_error:{type(exc).__name__}",
            }
        if not isinstance(data, dict):
            return {
                "available": False, "status": "unknown", "score": 0, "issues": [],
                "fallback": True, "fallback_reason": "invalid_health_payload",
            }
        if not bool(data.get("available", False)):
            return {
                "available": False, "status": "unknown", "score": 0, "issues": [],
                "fallback": True,
                "fallback_reason": data.get("error") or "health_unavailable",
            }
        report = data.get("report", {}) if isinstance(data, dict) else {}
        issues = []
        score = 100
        status = "healthy"
        if isinstance(report, dict):
            issues = list(report.get("issues", []) or [])
            score = int(report.get("score", 100) or 100)
            status = str(report.get("status", "healthy") or "healthy")
        return {
            "available": True,
            "status": status,
            "score": score,
            "issues": issues,
            "summary": data.get("summary"),
            "error": data.get("error"),
            "fallback": False,
        }

    # ============================================================
    # Phase C.1 P1-1: 扩展 SelfModel 视图,匹配 Dashboard V2 要求
    # 6 个新视图: identity_state / personality_state / growth_history /
    #            relationship_state / capability_boundary / personality_evolution_history
    # 设计原则: 全部只读;严格容错;不直接 import 业务模块
    # ============================================================

    def _get_runtime_provider(self) -> Optional[Any]:
        """通过 SelfModelProvider 间接拿 RuntimeProvider 引用(用于访问 personality/relationship)。"""
        smp = self._get_smp()
        if smp is None:
            return None
        # SelfModelProvider 内部持有 runtime_provider 引用(若 _runtime_provider 已注入)
        rp = _safe_get(smp, "_runtime_provider", None)
        if rp is not None:
            return rp
        # 兜底: 直接拿全局 RuntimeProvider
        try:
            from src.admin.runtime_provider import get_runtime_provider
            return get_runtime_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("SelfModelDashboardProvider: RuntimeProvider 不可用: %s", exc)
            return None

    def _get_self_model_store(self) -> Optional[Any]:
        """通过 RuntimeProvider 间接拿 SelfModelStore。"""
        rp = self._get_runtime_provider()
        if rp is None:
            return None
        try:
            store_fn = _safe_get(rp, "get_self_model_store")
            if callable(store_fn):
                return store_fn()
        except Exception as exc:  # noqa: BLE001
            logger.debug("SelfModelDashboardProvider: get_self_model_store 失败: %s", exc)
        return None

    # --------------------------------------------------------
    # identity_state(扩展身份状态,包含 core_values / anchor / stable / version)
    # --------------------------------------------------------
    def get_identity_state(self) -> Dict[str, Any]:
        """返回完整身份状态视图(比 /identity 字段更丰富,供 Dashboard 展示用)。"""
        smp = self._get_smp()
        if smp is None:
            return self._empty_identity_state(reason="self_model_provider_unavailable")
        if not callable(_safe_get(smp, "get_identity")):
            return self._empty_identity_state(reason="get_identity_not_callable")
        try:
            data = smp.get_identity()
        except Exception as exc:  # noqa: BLE001
            return self._empty_identity_state(reason=f"identity_error:{type(exc).__name__}")
        if not isinstance(data, dict):
            return self._empty_identity_state(reason="invalid_identity_payload")
        if not bool(data.get("available", False)):
            return self._empty_identity_state(reason=data.get("error") or "identity_unavailable")
        return {
            "available": True,
            "identity_id": data.get("identity_id"),
            "identity_name": str(data.get("identity_name", "") or ""),  # P1-2: 不写死人格名
            "core_values": list(data.get("core_values", []) or []),
            "anchor": str(data.get("anchor", "") or ""),
            "version": data.get("version"),
            "source": data.get("source", "self_model"),
            "stable": bool(data.get("stable", False)),
            "error": data.get("error"),
            "fallback": False,
        }

    def _empty_identity_state(self, *, reason: str) -> Dict[str, Any]:
        return {
            "available": False,
            "identity_id": None,
            "identity_name": "",  # P1-2: 不写死人格名
            "core_values": [],
            "anchor": "",
            "version": None,
            "source": "fallback",
            "stable": False,
            "error": reason,
            "fallback": True,
            "fallback_reason": reason,
        }

    # --------------------------------------------------------
    # personality_state(当前人格向量 / 状态)
    # --------------------------------------------------------
    def get_personality_state(self) -> Dict[str, Any]:
        """返回当前人格状态(从 SelfModelStore 或 PersonalityResolver 读取 current_traits)。"""
        # 1) 优先从 SelfModelStore 读取
        sms = self._get_self_model_store()
        if sms is not None:
            try:
                # 尝试 get_active_self_model(可能存在)
                get_active = _safe_get(sms, "get_active_self_model")
                if callable(get_active):
                    v3 = get_active()
                    if v3 is not None and isinstance(v3, dict):
                        traits = v3.get("current_traits") or v3.get("traits") or {}
                        if traits:
                            return {
                                "available": True,
                                "source": "self_model_store",
                                "traits": dict(traits),
                                "trait_count": len(traits),
                                "tensions": list(v3.get("personality_tensions", []) or []),
                                "fallback": False,
                            }
            except Exception as exc:  # noqa: BLE001
                logger.debug("SelfModelDashboardProvider.get_personality_state sms 失败: %s", exc)

        # 2) 兜底: 尝试从 PersonalityResolver 读
        rp = self._get_runtime_provider()
        if rp is not None:
            try:
                get_pr = _safe_get(rp, "get_personality_resolver")
                resolver = get_pr() if callable(get_pr) else None
                if resolver is not None:
                    state = _safe_get(resolver, "state", None)
                    if state is not None and hasattr(state, "__dict__"):
                        traits = _safe_get(state, "current_traits", {}) or _safe_get(state, "traits", {}) or {}
                        return {
                            "available": True,
                            "source": "personality_resolver",
                            "traits": dict(traits) if isinstance(traits, dict) else {},
                            "trait_count": len(traits) if isinstance(traits, dict) else 0,
                            "tensions": [],
                            "fallback": False,
                        }
            except Exception as exc:  # noqa: BLE001
                logger.debug("SelfModelDashboardProvider.get_personality_state resolver 失败: %s", exc)

        return {
            "available": False,
            "source": "fallback",
            "traits": {},
            "trait_count": 0,
            "tensions": [],
            "fallback": True,
            "fallback_reason": "personality_state_unavailable",
        }

    # --------------------------------------------------------
    # growth_history(GrowthRecord 列表)
    # --------------------------------------------------------
    def get_growth_history(self, limit: int = 50) -> Dict[str, Any]:
        """返回 GrowthRecord 历史(从 SelfModelStore 读 growth_history.records)。"""
        try:
            n = max(1, min(500, int(limit)))
        except (TypeError, ValueError):
            n = 50
        sms = self._get_self_model_store()
        if sms is None:
            return {
                "available": False,
                "total_count": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": "self_model_store_unavailable",
            }
        # 尝试多种读取方式(与 SelfModelStore 不同版本兼容)
        records: Optional[List[Dict[str, Any]]] = None
        try:
            getter = _safe_get(sms, "get_growth_history")
            if callable(getter):
                v = getter(limit=n)
                if isinstance(v, dict):
                    records = list(v.get("records", []) or [])
                elif isinstance(v, list):
                    records = v
        except Exception as exc:  # noqa: BLE001
            logger.debug("SelfModelDashboardProvider.get_growth_history getter 失败: %s", exc)

        if records is None:
            try:
                get_active = _safe_get(sms, "get_active_self_model")
                if callable(get_active):
                    v3 = get_active()
                    if isinstance(v3, dict):
                        gh = v3.get("growth_history", {}) or {}
                        if isinstance(gh, dict):
                            records = list(gh.get("records", []) or [])
            except Exception as exc:  # noqa: BLE001
                logger.debug("SelfModelDashboardProvider.get_growth_history active 失败: %s", exc)

        if records is None:
            return {
                "available": False,
                "total_count": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": "growth_history_not_exposed",
            }
        # 规范化 items 视图
        items: List[Dict[str, Any]] = []
        for r in records[:n]:
            if not isinstance(r, dict):
                continue
            items.append({
                "record_id": str(r.get("record_id") or r.get("id") or ""),
                "growth_signal": str(r.get("growth_signal", "") or ""),
                "growth_level": str(r.get("growth_level", "") or ""),
                "source_type": str(r.get("source_type", "") or ""),
                "affected_dimensions": dict(r.get("affected_dimensions", {}) or {}),
                "confidence": float(r.get("confidence", 0.0) or 0.0),
                "reason": str(r.get("reason", "") or ""),
                "created_at": str(r.get("created_at", "") or ""),
                "applied": bool(r.get("applied", False)),
            })
        return {
            "available": True,
            "total_count": len(records),
            "items": items,
            "fallback": False,
        }

    # --------------------------------------------------------
    # relationship_state(与清清的关系状态)
    # --------------------------------------------------------
    def get_relationship_state(self) -> Dict[str, Any]:
        """从运行时 RelationshipState 读取关系状态(只读快照)。"""
        rp = self._get_runtime_provider()
        if rp is None:
            return {
                "available": False,
                "trust": 0.0,
                "familiarity": 0.0,
                "bond_strength": 0.0,
                "shared_history": 0.0,
                "promise_level": 0.0,
                "activity_level": 0.0,
                "milestones": [],
                "fallback": True,
                "fallback_reason": "runtime_provider_unavailable",
            }
        # 1) 尝试通过 resolver 拿 relationship_state
        rel_state = None
        try:
            get_pr = _safe_get(rp, "get_personality_resolver")
            resolver = get_pr() if callable(get_pr) else None
            if resolver is not None:
                rel_state = _safe_get(resolver, "relationship_state", None)
                if rel_state is None:
                    # 兼容: resolver 直接持有 relationship_state 属性
                    for attr in ("_relationship_state", "rel_state"):
                        rel_state = _safe_get(resolver, attr, None)
                        if rel_state is not None:
                            break
        except Exception as exc:  # noqa: BLE001
            logger.debug("SelfModelDashboardProvider.get_relationship_state resolver 失败: %s", exc)

        # 2) 兜底: 从文件直接读 data/relationship_state.json(只读)
        if rel_state is None:
            try:
                from pathlib import Path
                import json
                state_path = Path("data/relationship_state.json")
                if state_path.exists():
                    with open(state_path, "r", encoding="utf-8") as f:
                        rel_state = json.load(f)
            except Exception as exc:  # noqa: BLE001
                logger.debug("SelfModelDashboardProvider.get_relationship_state file 失败: %s", exc)

        if not isinstance(rel_state, dict):
            return {
                "available": False,
                "trust": 0.0,
                "familiarity": 0.0,
                "bond_strength": 0.0,
                "shared_history": 0.0,
                "promise_level": 0.0,
                "activity_level": 0.0,
                "milestones": [],
                "fallback": True,
                "fallback_reason": "relationship_state_unavailable",
            }

        milestones = list(rel_state.get("milestones", []) or [])
        return {
            "available": True,
            "trust": float(rel_state.get("trust", 0.0) or 0.0),
            "familiarity": float(rel_state.get("familiarity", 0.0) or 0.0),
            "bond_strength": float(rel_state.get("bond_strength", 0.0) or 0.0),
            "shared_history": float(rel_state.get("shared_history", 0.0) or 0.0),
            "promise_level": float(rel_state.get("promise_level", 0.0) or 0.0),
            "activity_level": float(rel_state.get("activity_level", 0.0) or 0.0),
            "milestones_count": len(milestones),
            "milestones": milestones[:20],
            "important_events_count": len(list(rel_state.get("important_events", []) or [])),
            "last_updated": rel_state.get("last_updated"),
            "fallback": False,
        }

    # --------------------------------------------------------
    # capability_boundary(能力边界)
    # --------------------------------------------------------
    def get_capability_boundary(self) -> Dict[str, Any]:
        """返回能力边界(从 SelfModelStore 静态限制 + Runtime 运行时能力 派生)。"""
        static_limitations: List[str] = []
        runtime_capabilities: List[str] = []
        known_uncertainties: List[str] = [
            "LLM 知识截止",
            "无法访问实时网络",
            "无长期记忆索引外的精确回忆",
        ]

        # 1) 从 SelfModelStore 读
        sms = self._get_self_model_store()
        if sms is not None:
            try:
                get_active = _safe_get(sms, "get_active_self_model")
                if callable(get_active):
                    v3 = get_active()
                    if isinstance(v3, dict):
                        cap = v3.get("capability_boundary")
                        if isinstance(cap, dict):
                            static_limitations = list(cap.get("static_limitations", []) or [])
                            runtime_capabilities = list(cap.get("runtime_capabilities", []) or [])
                            known_uncertainties = list(cap.get("known_uncertainties", []) or known_uncertainties)
            except Exception as exc:  # noqa: BLE001
                logger.debug("SelfModelDashboardProvider.get_capability_boundary sms 失败: %s", exc)

        # 2) 兜底: 至少给一份"基线能力"
        if not runtime_capabilities:
            runtime_capabilities = [
                "chat_completions",
                "memory_recall",
                "personality_expression",
                "emotion_response",
                "growth_evaluation",
            ]
        if not static_limitations:
            static_limitations = [
                "no_self_initiative",        # Phase C.1 边界:不新增主动意识
                "no_agent_autonomy",         # Phase C.1 边界:不新增自主行动
                "no_dashboard_mutation",     # Dashboard 只读
            ]

        return {
            "available": True,
            "static_limitations": static_limitations,
            "runtime_capabilities": runtime_capabilities,
            "known_uncertainties": known_uncertainties,
            "last_updated": None,
            "fallback": False,
        }

    # --------------------------------------------------------
    # personality_evolution_history(人格演化历史)
    # --------------------------------------------------------
    def get_personality_evolution_history(self, limit: int = 50) -> Dict[str, Any]:
        """返回人格演化历史(从 SelfModelStore 读 personality_evolution_history.records)。"""
        try:
            n = max(1, min(500, int(limit)))
        except (TypeError, ValueError):
            n = 50
        sms = self._get_self_model_store()
        if sms is None:
            return {
                "available": False,
                "total_count": 0,
                "applied_count": 0,
                "rolled_back_count": 0,
                "current_personality_state": {},
                "items": [],
                "fallback": True,
                "fallback_reason": "self_model_store_unavailable",
            }

        evolution: Optional[Dict[str, Any]] = None
        try:
            get_active = _safe_get(sms, "get_active_self_model")
            if callable(get_active):
                v3 = get_active()
                if isinstance(v3, dict):
                    eh = v3.get("personality_evolution_history", {}) or {}
                    if isinstance(eh, dict):
                        evolution = eh
        except Exception as exc:  # noqa: BLE001
            logger.debug("SelfModelDashboardProvider.get_personality_evolution_history 失败: %s", exc)

        if not isinstance(evolution, dict):
            return {
                "available": False,
                "total_count": 0,
                "applied_count": 0,
                "rolled_back_count": 0,
                "current_personality_state": {},
                "items": [],
                "fallback": True,
                "fallback_reason": "evolution_history_unavailable",
            }

        records = list(evolution.get("records", []) or [])
        items: List[Dict[str, Any]] = []
        for r in records[:n]:
            if not isinstance(r, dict):
                continue
            items.append({
                "id": str(r.get("id", "") or ""),
                "timestamp": str(r.get("timestamp", "") or ""),
                "source_proposal_id": str(r.get("source_proposal_id", "") or ""),
                "source_growth_record_ids": list(r.get("source_growth_record_ids", []) or []),
                "changed_traits": dict(r.get("changed_traits", {}) or {}),
                "confidence": float(r.get("confidence", 0.0) or 0.0),
                "reason": str(r.get("reason", "") or ""),
                "status": str(r.get("status", "") or ""),
            })
        return {
            "available": True,
            "total_count": len(records),
            "applied_count": int(evolution.get("applied_count", 0) or 0),
            "rolled_back_count": int(evolution.get("rolled_back_count", 0) or 0),
            "current_personality_state": dict(evolution.get("current_personality_state", {}) or {}),
            "last_updated": evolution.get("last_updated"),
            "items": items,
            "fallback": False,
        }

    # --------------------------------------------------------
    # 组合:一次性返回所有 6 个 state(便于 Dashboard 一次拉取)
    # --------------------------------------------------------
    def get_full_state(self) -> Dict[str, Any]:
        """返回所有 6 个 SelfModel 视图的组合(只读)。"""
        return {
            "available": True,
            "identity_state": self.get_identity_state(),
            "personality_state": self.get_personality_state(),
            "growth_history": self.get_growth_history(limit=50),
            "relationship_state": self.get_relationship_state(),
            "capability_boundary": self.get_capability_boundary(),
            "personality_evolution_history": self.get_personality_evolution_history(limit=50),
        }


# ============================================================
# 模块级单例
# ============================================================
_provider_instance: Optional[SelfModelDashboardProvider] = None
_provider_lock = threading.Lock()


def get_selfmodel_dashboard_provider() -> SelfModelDashboardProvider:
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = SelfModelDashboardProvider()
    return _provider_instance


def reset_selfmodel_dashboard_provider_for_testing() -> None:
    global _provider_instance
    with _provider_lock:
        _provider_instance = None


__all__ = [
    "SelfModelDashboardProvider",
    "get_selfmodel_dashboard_provider",
    "reset_selfmodel_dashboard_provider_for_testing",
]
