# -*- coding: utf-8 -*-
"""
src/admin/growth_dashboard_provider.py

Phase C.1 P1-1 —— Growth Dashboard Provider。

职责:
- 只读提供 Growth 数据给 Dashboard
- 数据源:ProposalStorage(只读引用),SelfModelStore(只读)
- 提供:Proposal 数量 / accepted/rejected/review 数 / 最近 growth 记录 / evolution_history
- 严格只读,不允许触发成长

约束:
- 不直接 import 任何底层业务模块(memory / emotion / runtime core)
- 仅依赖 src.growth.proposal.storage(只读)
- 严格容错,任何子组件不可用时返回 fallback
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
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
        logger.debug("growth_dashboard_provider safe_call 失败: %s", exc)
        return None


class GrowthDashboardProvider:
    """
    Growth Dashboard 只读 Provider。

    注入:
        proposal_storage: 已有 ProposalStorage(测试时可注入 mock)
        self_model_dashboard_provider: 用于读取 growth_history / personality_evolution_history
    """

    DEFAULT_LIMIT = 20
    MAX_LIMIT = 200

    def __init__(
        self,
        proposal_storage: Optional[Any] = None,
        self_model_dashboard_provider: Optional[Any] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._proposal_storage = proposal_storage
        self._selfmodel_dp = self_model_dashboard_provider

    def _get_storage(self) -> Optional[Any]:
        with self._lock:
            if self._proposal_storage is not None:
                return self._proposal_storage
            try:
                from src.growth.proposal.storage import get_proposal_storage
                self._proposal_storage = get_proposal_storage()
            except Exception as exc:  # noqa: BLE001
                logger.debug("GrowthDashboardProvider: ProposalStorage 不可用: %s", exc)
                self._proposal_storage = None
            return self._proposal_storage

    def _get_selfmodel_dp(self) -> Optional[Any]:
        with self._lock:
            if self._selfmodel_dp is not None:
                return self._selfmodel_dp
            try:
                from src.admin.selfmodel_dashboard_provider import get_selfmodel_dashboard_provider
                self._selfmodel_dp = get_selfmodel_dashboard_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("GrowthDashboardProvider: SelfModelDashboardProvider 不可用: %s", exc)
                self._selfmodel_dp = None
            return self._selfmodel_dp

    # ============================================================
    # 工具:获取所有 proposal dict(兼容 dataclass / dict 两种形态)
    # ============================================================
    def _load_all_proposals(self) -> List[Dict[str, Any]]:
        """从 ProposalStorage 加载所有 proposal(只读)。"""
        storage = self._get_storage()
        if storage is None:
            return []
        # 优先 list_all(限制较大值,然后在内存中再过滤)
        try:
            list_all = _safe_get(storage, "list_all")
            if callable(list_all):
                items = list_all(limit=1000, offset=0)
                return self._normalize_proposals(items)
        except Exception as exc:  # noqa: BLE001
            logger.debug("GrowthDashboardProvider._load_all_proposals list_all 失败: %s", exc)
        # 兜底:从 json_file 读
        try:
            json_file = _safe_get(storage, "json_file", None)
            if json_file is not None and Path(str(json_file)).exists():
                import json
                with open(str(json_file), "r", encoding="utf-8") as f:
                    data = json.load(f)
                return list(data.get("proposals", []) or [])
        except Exception as exc:  # noqa: BLE001
            logger.debug("GrowthDashboardProvider._load_all_proposals json 失败: %s", exc)
        return []

    @staticmethod
    def _normalize_proposals(items: List[Any]) -> List[Dict[str, Any]]:
        """把 GrowthProposal dataclass / dict 统一为 dict。"""
        out: List[Dict[str, Any]] = []
        for p in items:
            if isinstance(p, dict):
                out.append(p)
            else:
                to_dict = _safe_get(p, "to_dict", None)
                if callable(to_dict):
                    try:
                        d = to_dict()
                        if isinstance(d, dict):
                            out.append(d)
                    except Exception:
                        pass
        return out

    # ============================================================
    # Summary(Proposal 数量 / accepted/rejected/review 数量)
    # ============================================================
    def get_summary(self) -> Dict[str, Any]:
        """
        汇总 Proposal 状态统计。

        Returns:
            {
                "available": bool,
                "total": int,
                "pending": int,
                "approved": int,
                "rejected": int,
                "applied": int,
                "cancelled": int,
                "review": int,                 # 待 review(pending + approved 但未 applied)
                "by_type": Dict[str, int],
                "last_updated": str|None,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        proposals = self._load_all_proposals()
        if not proposals and self._get_storage() is None:
            return {
                "available": False,
                "total": 0,
                "pending": 0,
                "approved": 0,
                "rejected": 0,
                "applied": 0,
                "cancelled": 0,
                "review": 0,
                "by_type": {},
                "last_updated": None,
                "fallback": True,
                "fallback_reason": "proposal_storage_unavailable",
            }
        try:
            counts = {
                "pending": 0,
                "approved": 0,
                "rejected": 0,
                "applied": 0,
                "cancelled": 0,
            }
            by_type: Dict[str, int] = {}
            last_ts: Optional[str] = None
            for p in proposals:
                status = str(p.get("status", "") or "").lower()
                if status in counts:
                    counts[status] += 1
                pt = str(p.get("proposal_type", "") or "")
                if pt:
                    by_type[pt] = by_type.get(pt, 0) + 1
                ts = str(p.get("timestamp", "") or "")
                if ts and (last_ts is None or ts > last_ts):
                    last_ts = ts
            review_count = counts["pending"]  # pending 即待 review
            return {
                "available": True,
                "total": len(proposals),
                "pending": counts["pending"],
                "approved": counts["approved"],
                "rejected": counts["rejected"],
                "applied": counts["applied"],
                "cancelled": counts["cancelled"],
                "review": review_count,
                "by_type": by_type,
                "last_updated": last_ts,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("GrowthDashboardProvider.get_summary 异常: %s", exc)
            return {
                "available": False,
                "total": 0,
                "pending": 0,
                "approved": 0,
                "rejected": 0,
                "applied": 0,
                "cancelled": 0,
                "review": 0,
                "by_type": {},
                "last_updated": None,
                "fallback": True,
                "fallback_reason": f"summary_error:{type(exc).__name__}",
            }

    # ============================================================
    # Recent(最近 growth 记录 - 包含 proposal + growth_record)
    # ============================================================
    def get_recent(self, limit: int = 20) -> Dict[str, Any]:
        """
        列出最近 growth 相关记录(按时间倒序):
        1) Proposal(proposal 包含 applied 后的最终 growth 事件)
        2) GrowthRecord(从 SelfModelStore 派生)

        Returns:
            {
                "available": bool,
                "total": int,
                "items": [{
                    "kind": "proposal" | "growth_record",
                    "id": str,
                    "timestamp": str,
                    "type": str,
                    "status": str,
                    "summary": str,
                    "details": Dict[str, Any]
                }, ...],
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n = max(1, min(self.MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            n = self.DEFAULT_LIMIT
        items: List[Dict[str, Any]] = []
        # 1) Proposal
        proposals = self._load_all_proposals()
        for p in proposals:
            items.append({
                "kind": "proposal",
                "id": str(p.get("proposal_id", "") or ""),
                "timestamp": str(p.get("timestamp", "") or ""),
                "type": str(p.get("proposal_type", "") or ""),
                "status": str(p.get("status", "") or ""),
                "summary": str(p.get("reason", "") or p.get("narrative", "") or ""),
                "details": {
                    "confidence": float(p.get("confidence", 0.0) or 0.0),
                    "affected_dimensions": dict(p.get("affected_dimensions", {}) or {}),
                    "source": str(p.get("source", "") or ""),
                },
            })
        # 2) GrowthRecord(从 SelfModelStore 派生)
        smp = self._get_selfmodel_dp()
        if smp is not None:
            try:
                growth_data = smp.get_growth_history(limit=n)
                if isinstance(growth_data, dict) and bool(growth_data.get("available", False)):
                    for r in list(growth_data.get("items", []) or []):
                        if not isinstance(r, dict):
                            continue
                        items.append({
                            "kind": "growth_record",
                            "id": str(r.get("record_id", "") or ""),
                            "timestamp": str(r.get("created_at", "") or ""),
                            "type": str(r.get("growth_signal", "") or ""),
                            "status": "applied" if r.get("applied") else "pending",
                            "summary": str(r.get("reason", "") or ""),
                            "details": {
                                "growth_level": str(r.get("growth_level", "") or ""),
                                "source_type": str(r.get("source_type", "") or ""),
                                "affected_dimensions": dict(r.get("affected_dimensions", {}) or {}),
                                "confidence": float(r.get("confidence", 0.0) or 0.0),
                            },
                        })
            except Exception as exc:  # noqa: BLE001
                logger.debug("GrowthDashboardProvider.get_recent growth_history 失败: %s", exc)

        # 按 timestamp 倒序
        items.sort(
            key=lambda x: (str(x.get("timestamp", "") or ""), str(x.get("id", "") or "")),
            reverse=True,
        )
        top = items[:n]
        if not items and not proposals:
            return {
                "available": True,
                "total": 0,
                "items": [],
                "fallback": False,
                "fallback_reason": None,
            }
        return {
            "available": True,
            "total": len(items),
            "items": top,
            "fallback": False,
            "fallback_reason": None,
        }

    # ============================================================
    # Evolution History(从 SelfModelStore 读 personality_evolution_history)
    # ============================================================
    def get_evolution_history(self, limit: int = 50) -> Dict[str, Any]:
        """
        透传 SelfModelDashboardProvider.get_personality_evolution_history
        + 补充 Proposal applied 后的演化事件(applied proposals)

        Returns:
            {
                "available": bool,
                "total": int,
                "applied_count": int,
                "rolled_back_count": int,
                "items": [...],
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n = max(1, min(self.MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            n = 50
        smp = self._get_selfmodel_dp()
        if smp is None:
            return {
                "available": False,
                "total": 0,
                "applied_count": 0,
                "rolled_back_count": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": "selfmodel_dashboard_provider_unavailable",
            }
        try:
            evo = smp.get_personality_evolution_history(limit=n)
        except Exception as exc:  # noqa: BLE001
            logger.debug("GrowthDashboardProvider.get_evolution_history 失败: %s", exc)
            return {
                "available": False,
                "total": 0,
                "applied_count": 0,
                "rolled_back_count": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": f"evolution_error:{type(exc).__name__}",
            }
        if not isinstance(evo, dict) or not bool(evo.get("available", False)):
            return {
                "available": False,
                "total": 0,
                "applied_count": 0,
                "rolled_back_count": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": (evo or {}).get("fallback_reason") or "evolution_unavailable",
            }
        # 补充: 已 applied 的 proposals 也算 evolution
        proposals = self._load_all_proposals()
        applied_proposals = [p for p in proposals if str(p.get("status", "") or "").lower() == "applied"]
        items: List[Dict[str, Any]] = list(evo.get("items", []) or [])
        for p in applied_proposals:
            items.append({
                "id": str(p.get("proposal_id", "") or ""),
                "timestamp": str(p.get("timestamp", "") or ""),
                "source_proposal_id": str(p.get("proposal_id", "") or ""),
                "source_growth_record_ids": [],
                "changed_traits": dict(p.get("after_state", {}) or {}),
                "confidence": float(p.get("confidence", 0.0) or 0.0),
                "reason": str(p.get("reason", "") or ""),
                "status": "applied",
            })
        # 按 timestamp 倒序
        items.sort(
            key=lambda x: (str(x.get("timestamp", "") or ""), str(x.get("id", "") or "")),
            reverse=True,
        )
        return {
            "available": True,
            "total": len(items),
            "applied_count": int(evo.get("applied_count", 0) or 0) + len(applied_proposals),
            "rolled_back_count": int(evo.get("rolled_back_count", 0) or 0),
            "current_personality_state": dict(evo.get("current_personality_state", {}) or {}),
            "last_updated": evo.get("last_updated"),
            "items": items[:n],
            "fallback": False,
            "fallback_reason": None,
        }

    # ============================================================
    # Combined(一次拉取所有 growth 视图)
    # ============================================================
    def get_combined(self, recent_limit: int = 20, evolution_limit: int = 50) -> Dict[str, Any]:
        return {
            "available": True,
            "summary": self.get_summary(),
            "recent": self.get_recent(limit=recent_limit),
            "evolution_history": self.get_evolution_history(limit=evolution_limit),
        }


# ============================================================
# 模块级单例
# ============================================================
_provider_instance: Optional[GrowthDashboardProvider] = None
_provider_lock = threading.Lock()


def get_growth_dashboard_provider() -> GrowthDashboardProvider:
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = GrowthDashboardProvider()
    return _provider_instance


def reset_growth_dashboard_provider_for_testing() -> None:
    global _provider_instance
    with _provider_lock:
        _provider_instance = None


__all__ = [
    "GrowthDashboardProvider",
    "get_growth_dashboard_provider",
    "reset_growth_dashboard_provider_for_testing",
]
