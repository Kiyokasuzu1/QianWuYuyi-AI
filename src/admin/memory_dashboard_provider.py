# -*- coding: utf-8 -*-
"""
src/admin/memory_dashboard_provider.py

Phase 5.0 Dashboard Upgrade Step 7.3 —— Memory Dashboard Provider。

职责:
- 只读提供 Memory 数据给 Dashboard
- 数据源:RuntimeProvider.get_memory_store()(间接访问 Memory Authority)
- 禁止直接 import 任何底层记忆业务模块
- 严格容错,任何子组件不可用时返回 fallback

API:
- get_summary() -> {total_count, important_count, recent_count, last_update, available, fallback}
- list_recent(limit=20) -> {items, total, available, fallback}
- list_important(limit=20) -> {items, total, available, fallback}
- get_timeline(range='7d') -> {buckets, total, range, available, fallback}
- get_memory(memory_id) -> {memory, available, fallback}

约束:
- 禁止:直接 import 底层记忆模块 / 情绪 / 成长 / 人格 / 关系 / 运行时核心 / self_model
- 仅依赖: src.admin.runtime_provider (Admin 间接访问层)
- 所有失败必须返回 fallback=true,绝不能"空数据但标记真实"
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 工具函数
# ============================================================

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
        logger.debug("memory_dashboard_provider safe_call 失败: %s", exc)
        return None


_RANGE_PATTERN = re.compile(r"^\s*(\d+)\s*([dhmw])\s*$", re.IGNORECASE)
_RANGE_DAYS = {"d": 1, "h": 0, "m": 0, "w": 7}
_RANGE_HOURS = {"d": 24, "h": 1, "m": 1 / 60.0, "w": 24 * 7}


def _parse_range(range_str: str) -> Tuple[float, str]:
    """
    解析 range 字符串为 (hours, normalized_label)。

    支持:
        "7d" -> 7 days
        "24h" -> 24 hours
        "30m" -> 30 minutes
        "2w" -> 2 weeks

    Returns:
        (hours: float, label: str)
    """
    if not range_str or not isinstance(range_str, str):
        return 24 * 7, "7d"
    s = str(range_str).strip()
    m = _RANGE_PATTERN.match(s)
    if not m:
        return 24 * 7, "7d"
    n = int(m.group(1))
    unit = m.group(2).lower()
    hours = n * _RANGE_HOURS[unit]
    label = f"{n}{unit.lower()}"
    return max(0.0, float(hours)), label


def _parse_iso(ts: Any) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        # 兼容 "Z" 后缀
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        # 去掉 tz(我们只关心相对时间)
        if dt.tzinfo is not None:
            try:
                dt = dt.replace(tzinfo=None)
            except Exception:
                pass
        return dt
    except Exception:
        return None


def _extract_importance(memory: Dict[str, Any]) -> float:
    """从 memory 中提取 importance(metadata.importance 优先,顶层 importance 兜底)。"""
    if not isinstance(memory, dict):
        return 0.0
    md = memory.get("metadata")
    if isinstance(md, dict) and "importance" in md:
        try:
            return float(md.get("importance", 0.0) or 0.0)
        except (TypeError, ValueError):
            pass
    if "importance" in memory:
        try:
            return float(memory.get("importance", 0.0) or 0.0)
        except (TypeError, ValueError):
            pass
    return 0.0


def _extract_summary(memory: Dict[str, Any]) -> str:
    """提取 memory 的摘要文本。"""
    if not isinstance(memory, dict):
        return ""
    content = memory.get("content")
    if isinstance(content, str) and content:
        return content[:200]
    summary = memory.get("summary")
    if isinstance(summary, str) and summary:
        return summary[:200]
    return ""


def _memory_view(memory: Dict[str, Any]) -> Dict[str, Any]:
    """将 memory 规范化为 Dashboard 视图结构。"""
    if not isinstance(memory, dict):
        return {}
    md = memory.get("metadata") or {}
    if not isinstance(md, dict):
        md = {}
    return {
        "id": str(memory.get("id", "") or ""),
        "summary": _extract_summary(memory),
        "content": str(memory.get("content", "") or ""),
        "memory_type": str(
            md.get("memory_type")
            or memory.get("memory_type")
            or md.get("type")
            or memory.get("type")
            or "general"
        ),
        "importance": _extract_importance(memory),
        "created_at": str(memory.get("timestamp", "") or ""),
        "user_id": memory.get("user_id"),
        "role": str(memory.get("role", "") or ""),
        "tags": list(md.get("tags", []) or []),
        "emotion_tag": str(md.get("emotion_tag", "") or ""),
        "owner": str(md.get("owner", "") or ""),
        "fallback": False,
    }


# ============================================================
# Provider
# ============================================================
class MemoryDashboardProvider:
    """
    Memory Dashboard 只读 Provider。

    注入:
        runtime_provider: 已有 RuntimeProvider(测试时可注入 mock)
    """

    # 重要记忆阈值(与 runtime_provider 保持一致:>=0.7)
    IMPORTANT_THRESHOLD = 0.7
    # 默认每页
    DEFAULT_LIMIT = 20
    # 最大 limit(防止过载)
    MAX_LIMIT = 200

    def __init__(self, runtime_provider: Optional[Any] = None) -> None:
        self._lock = threading.RLock()
        self._runtime_provider = runtime_provider

    def _get_rp(self) -> Optional[Any]:
        with self._lock:
            if self._runtime_provider is not None:
                return self._runtime_provider
            try:
                from src.admin.runtime_provider import get_runtime_provider
                self._runtime_provider = get_runtime_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("MemoryDashboardProvider: RuntimeProvider 不可用: %s", exc)
                self._runtime_provider = None
            return self._runtime_provider

    def _get_store(self) -> Optional[Any]:
        """通过 RuntimeProvider 间接获取 MemoryStore(只读引用)。"""
        rp = self._get_rp()
        if rp is None:
            return None
        store = _safe_call(_safe_get(rp, "get_memory_store"))
        return store

    def _load_all(self) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        加载所有 memory。

        Returns:
            (ok, memories) - ok=False 表示不可用
        """
        store = self._get_store()
        if store is None:
            return False, []
        load_fn = _safe_get(store, "load")
        if not callable(load_fn):
            return False, []
        try:
            data = load_fn()
        except Exception as exc:  # noqa: BLE001
            logger.debug("MemoryDashboardProvider._load_all 异常: %s", exc)
            return False, []
        if not isinstance(data, list):
            return False, []
        # 仅保留 dict 项
        items = [m for m in data if isinstance(m, dict)]
        return True, items

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------
    def get_summary(self) -> Dict[str, Any]:
        """
        汇总信息:总数 / 重要数 / 最近数 / 最后更新。

        Returns:
            {
                "available": bool,
                "total_count": int,
                "important_count": int,
                "recent_count": int,    # 最近 7 天内
                "last_update": str|None,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        ok, memories = self._load_all()
        if not ok:
            return {
                "available": False,
                "total_count": 0,
                "important_count": 0,
                "recent_count": 0,
                "last_update": None,
                "fallback": True,
                "fallback_reason": "memory_store_unavailable",
            }
        try:
            important_count = 0
            recent_count = 0
            last_update: Optional[str] = None
            seven_days_ago = datetime.now() - timedelta(days=7)
            for m in memories:
                if _extract_importance(m) >= self.IMPORTANT_THRESHOLD:
                    important_count += 1
                ts = _parse_iso(m.get("timestamp"))
                if ts is not None:
                    if ts >= seven_days_ago:
                        recent_count += 1
                    if last_update is None or str(m.get("timestamp", "")) > last_update:
                        last_update = str(m.get("timestamp", "") or "")
            return {
                "available": True,
                "total_count": len(memories),
                "important_count": important_count,
                "recent_count": recent_count,
                "last_update": last_update,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("MemoryDashboardProvider.get_summary 异常: %s", exc)
            return {
                "available": False,
                "total_count": 0,
                "important_count": 0,
                "recent_count": 0,
                "last_update": None,
                "fallback": True,
                "fallback_reason": f"summary_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # Recent
    # --------------------------------------------------------
    def list_recent(self, limit: int = 20) -> Dict[str, Any]:
        """
        列出最近记忆(按 timestamp 倒序)。

        Returns:
            {
                "available": bool,
                "total": int,
                "items": [memory_view],
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n = max(1, min(self.MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            n = self.DEFAULT_LIMIT
        ok, memories = self._load_all()
        if not ok:
            return {
                "available": False,
                "total": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": "memory_store_unavailable",
            }
        try:
            # 按 timestamp 倒序(无时间戳的排到最后)
            def _ts(m: Dict[str, Any]) -> str:
                return str(m.get("timestamp", "") or "")

            sorted_items = sorted(
                memories,
                key=lambda m: (_ts(m) or "", str(m.get("id", "") or "")),
                reverse=True,
            )
            top = sorted_items[:n]
            items = [_memory_view(m) for m in top]
            return {
                "available": True,
                "total": len(memories),
                "items": items,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("MemoryDashboardProvider.list_recent 异常: %s", exc)
            return {
                "available": False,
                "total": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": f"recent_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # Important
    # --------------------------------------------------------
    def list_important(self, limit: int = 20) -> Dict[str, Any]:
        """
        列出高重要度记忆(importance >= 阈值)。

        Returns:
            {
                "available": bool,
                "total": int,            # 重要记忆总数
                "items": [memory_view],  # 重要记忆(按 importance 倒序,再按时间倒序)
                "threshold": float,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n = max(1, min(self.MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            n = self.DEFAULT_LIMIT
        ok, memories = self._load_all()
        if not ok:
            return {
                "available": False,
                "total": 0,
                "items": [],
                "threshold": self.IMPORTANT_THRESHOLD,
                "fallback": True,
                "fallback_reason": "memory_store_unavailable",
            }
        try:
            important = [m for m in memories if _extract_importance(m) >= self.IMPORTANT_THRESHOLD]
            # 按 importance 倒序,再按时间倒序
            important.sort(
                key=lambda m: (
                    _extract_importance(m),
                    str(m.get("timestamp", "") or ""),
                ),
                reverse=True,
            )
            top = important[:n]
            return {
                "available": True,
                "total": len(important),
                "items": [_memory_view(m) for m in top],
                "threshold": self.IMPORTANT_THRESHOLD,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("MemoryDashboardProvider.list_important 异常: %s", exc)
            return {
                "available": False,
                "total": 0,
                "items": [],
                "threshold": self.IMPORTANT_THRESHOLD,
                "fallback": True,
                "fallback_reason": f"important_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # Timeline
    # --------------------------------------------------------
    def get_timeline(self, range_str: str = "7d") -> Dict[str, Any]:
        """
        按天聚合记忆时间线。

        Args:
            range_str: 范围字符串,如 "7d" / "24h" / "30m" / "2w"

        Returns:
            {
                "available": bool,
                "range": str,        # 标准化后的 range 标签
                "buckets": [{date, count, important_count}, ...],
                "total": int,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        hours, label = _parse_range(range_str)
        ok, memories = self._load_all()
        if not ok:
            return {
                "available": False,
                "range": label,
                "buckets": [],
                "total": 0,
                "fallback": True,
                "fallback_reason": "memory_store_unavailable",
            }
        try:
            now = datetime.now()
            cutoff = now - timedelta(hours=hours) if hours > 0 else now
            # 按日期聚合(date -> {"count":0, "important_count":0})
            agg: Dict[str, Dict[str, int]] = {}
            for m in memories:
                ts = _parse_iso(m.get("timestamp"))
                if ts is None or ts < cutoff:
                    continue
                date_key = ts.strftime("%Y-%m-%d")
                if date_key not in agg:
                    agg[date_key] = {"count": 0, "important_count": 0}
                agg[date_key]["count"] += 1
                if _extract_importance(m) >= self.IMPORTANT_THRESHOLD:
                    agg[date_key]["important_count"] += 1
            # 排序(日期升序)
            buckets: List[Dict[str, Any]] = []
            for d in sorted(agg.keys()):
                buckets.append({
                    "date": d,
                    "count": int(agg[d]["count"]),
                    "important_count": int(agg[d]["important_count"]),
                })
            return {
                "available": True,
                "range": label,
                "buckets": buckets,
                "total": sum(b["count"] for b in buckets),
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("MemoryDashboardProvider.get_timeline 异常: %s", exc)
            return {
                "available": False,
                "range": label,
                "buckets": [],
                "total": 0,
                "fallback": True,
                "fallback_reason": f"timeline_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # Single memory detail
    # --------------------------------------------------------
    def get_memory(self, memory_id: str) -> Dict[str, Any]:
        """
        获取单条记忆详情。

        Returns:
            {
                "available": bool,
                "memory": memory_view|None,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        if not memory_id or not isinstance(memory_id, str):
            return {
                "available": False,
                "memory": None,
                "fallback": True,
                "fallback_reason": "invalid_memory_id",
            }
        store = self._get_store()
        if store is None:
            return {
                "available": False,
                "memory": None,
                "fallback": True,
                "fallback_reason": "memory_store_unavailable",
            }
        get_fn = _safe_get(store, "get_by_id")
        if not callable(get_fn):
            return {
                "available": False,
                "memory": None,
                "fallback": True,
                "fallback_reason": "get_by_id_not_callable",
            }
        try:
            m = get_fn(memory_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("MemoryDashboardProvider.get_memory 异常: %s", exc)
            return {
                "available": False,
                "memory": None,
                "fallback": True,
                "fallback_reason": f"get_error:{type(exc).__name__}",
            }
        if m is None or not isinstance(m, dict):
            return {
                "available": False,
                "memory": None,
                "fallback": True,
                "fallback_reason": "memory_not_found",
            }
        return {
            "available": True,
            "memory": _memory_view(m),
            "fallback": False,
            "fallback_reason": None,
        }

    # ============================================================
    # Phase C.1 P1-1: 扩展 memory 视图(类型统计 / 质量状态)
    # 严格只读,不修改 memory 数据
    # ============================================================

    # 类型分类规则
    _VALID_USER_TYPES = {
        "user_message", "user_fact", "user_event", "user_experience",
        "user_preference", "user_milestone", "user_emotion",
        "user_goal", "user_relationship", "user_shared",
    }
    _SYSTEM_TYPES = {"system", "system_prompt", "system_reminder", "system_meta", "init"}
    _AI_INTERNAL_TYPES = {
        "ai_thought", "ai_reflection", "ai_internal", "ai_self_talk",
        "ai_prompt", "ai_scratchpad", "ai_planning",
    }
    _INVALID_TYPES = {"", "unknown", "null", "none", "undefined"}

    def get_type_stats(self) -> Dict[str, Any]:
        """
        按 memory_type 聚合统计。

        分类:
        - normal_user: 正常用户记忆(_VALID_USER_TYPES)
        - system_pollution: 系统消息污染(_SYSTEM_TYPES)
        - ai_internal_pollution: AI 内部提示污染(_AI_INTERNAL_TYPES)
        - invalid: 无效记录(_INVALID_TYPES 或缺少 content/role)

        Returns:
            {
                "available": bool,
                "total": int,
                "by_type": Dict[str, int],       # 所有类型 -> 数量
                "by_category": Dict[str, int],   # 4 大类 -> 数量
                "pollution_count": int,          # 污染总数(system+ai_internal)
                "invalid_count": int,            # 无效记录数
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        ok, memories = self._load_all()
        if not ok:
            return {
                "available": False,
                "total": 0,
                "by_type": {},
                "by_category": {
                    "normal_user": 0,
                    "system_pollution": 0,
                    "ai_internal_pollution": 0,
                    "invalid": 0,
                },
                "pollution_count": 0,
                "invalid_count": 0,
                "fallback": True,
                "fallback_reason": "memory_store_unavailable",
            }
        try:
            by_type: Dict[str, int] = {}
            by_category = {
                "normal_user": 0,
                "system_pollution": 0,
                "ai_internal_pollution": 0,
                "invalid": 0,
            }
            pollution_count = 0
            invalid_count = 0
            for m in memories:
                t = str(
                    (m.get("metadata") or {}).get("memory_type")
                    or m.get("memory_type")
                    or (m.get("metadata") or {}).get("type")
                    or m.get("type")
                    or ""
                ).lower().strip()
                by_type[t] = by_type.get(t, 0) + 1
                if t in self._SYSTEM_TYPES:
                    by_category["system_pollution"] += 1
                    pollution_count += 1
                elif t in self._AI_INTERNAL_TYPES:
                    by_category["ai_internal_pollution"] += 1
                    pollution_count += 1
                elif t in self._INVALID_TYPES:
                    by_category["invalid"] += 1
                    invalid_count += 1
                elif t in self._VALID_USER_TYPES:
                    by_category["normal_user"] += 1
                else:
                    # 未知类型: 视作 invalid(可能需要 review)
                    by_category["invalid"] += 1
                    invalid_count += 1
            return {
                "available": True,
                "total": len(memories),
                "by_type": by_type,
                "by_category": by_category,
                "pollution_count": pollution_count,
                "invalid_count": invalid_count,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("MemoryDashboardProvider.get_type_stats 异常: %s", exc)
            return {
                "available": False,
                "total": 0,
                "by_type": {},
                "by_category": {
                    "normal_user": 0,
                    "system_pollution": 0,
                    "ai_internal_pollution": 0,
                    "invalid": 0,
                },
                "pollution_count": 0,
                "invalid_count": 0,
                "fallback": True,
                "fallback_reason": f"type_stats_error:{type(exc).__name__}",
            }

    def get_quality(self) -> Dict[str, Any]:
        """
        质量状态(对当前 memory 数据做健康评估)。

        评估维度:
        - total: 总数
        - normal_ratio: 正常记忆比例
        - pollution_ratio: 污染比例
        - invalid_ratio: 无效记录比例
        - oldest / newest timestamp
        - has_role: 是否有 role 字段
        - has_content: 是否有 content 字段
        - status: healthy | warning | critical

        Returns:
            {
                "available": bool,
                "status": "healthy" | "warning" | "critical" | "unknown",
                "total": int,
                "normal_count": int,
                "pollution_count": int,
                "invalid_count": int,
                "normal_ratio": float,
                "pollution_ratio": float,
                "invalid_ratio": float,
                "missing_role_count": int,
                "missing_content_count": int,
                "oldest_timestamp": str|None,
                "newest_timestamp": str|None,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        ok, memories = self._load_all()
        if not ok:
            return {
                "available": False,
                "status": "unknown",
                "total": 0,
                "normal_count": 0,
                "pollution_count": 0,
                "invalid_count": 0,
                "normal_ratio": 0.0,
                "pollution_ratio": 0.0,
                "invalid_ratio": 0.0,
                "missing_role_count": 0,
                "missing_content_count": 0,
                "oldest_timestamp": None,
                "newest_timestamp": None,
                "fallback": True,
                "fallback_reason": "memory_store_unavailable",
            }
        try:
            type_stats = self.get_type_stats()
            normal = int(type_stats.get("by_category", {}).get("normal_user", 0))
            pollution = int(type_stats.get("pollution_count", 0))
            invalid = int(type_stats.get("invalid_count", 0))
            total = len(memories)

            # 字段完整性
            missing_role = 0
            missing_content = 0
            ts_list: list = []
            for m in memories:
                if not m.get("role"):
                    missing_role += 1
                content = m.get("content")
                if not content or (isinstance(content, str) and not content.strip()):
                    missing_content += 1
                ts = str(m.get("timestamp", "") or "")
                if ts:
                    ts_list.append(ts)

            ts_list.sort()
            oldest = ts_list[0] if ts_list else None
            newest = ts_list[-1] if ts_list else None

            # 比例
            if total > 0:
                normal_ratio = round(normal / total, 4)
                pollution_ratio = round(pollution / total, 4)
                invalid_ratio = round(invalid / total, 4)
            else:
                normal_ratio = pollution_ratio = invalid_ratio = 0.0

            # 状态评估
            if total == 0:
                status = "unknown"
            elif pollution_ratio >= 0.3 or invalid_ratio >= 0.3:
                status = "critical"
            elif pollution_ratio >= 0.1 or invalid_ratio >= 0.1 or missing_content > 0:
                status = "warning"
            else:
                status = "healthy"

            return {
                "available": True,
                "status": status,
                "total": total,
                "normal_count": normal,
                "pollution_count": pollution,
                "invalid_count": invalid,
                "normal_ratio": normal_ratio,
                "pollution_ratio": pollution_ratio,
                "invalid_ratio": invalid_ratio,
                "missing_role_count": missing_role,
                "missing_content_count": missing_content,
                "oldest_timestamp": oldest,
                "newest_timestamp": newest,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("MemoryDashboardProvider.get_quality 异常: %s", exc)
            return {
                "available": False,
                "status": "unknown",
                "total": 0,
                "normal_count": 0,
                "pollution_count": 0,
                "invalid_count": 0,
                "normal_ratio": 0.0,
                "pollution_ratio": 0.0,
                "invalid_ratio": 0.0,
                "missing_role_count": 0,
                "missing_content_count": 0,
                "oldest_timestamp": None,
                "newest_timestamp": None,
                "fallback": True,
                "fallback_reason": f"quality_error:{type(exc).__name__}",
            }

    def get_combined(self, recent_limit: int = 20) -> Dict[str, Any]:
        """
        组合视图: summary + type_stats + quality + recent
        便于 Dashboard 一次拉取。
        """
        summary = self.get_summary()
        type_stats = self.get_type_stats()
        quality = self.get_quality()
        recent = self.list_recent(limit=recent_limit)
        return {
            "available": True,
            "summary": summary,
            "type_stats": type_stats,
            "quality": quality,
            "recent": recent,
        }


# ============================================================
# 模块级单例
# ============================================================
_provider_instance: Optional[MemoryDashboardProvider] = None
_provider_lock = threading.Lock()


def get_memory_dashboard_provider() -> MemoryDashboardProvider:
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = MemoryDashboardProvider()
    return _provider_instance


def reset_memory_dashboard_provider_for_testing() -> None:
    global _provider_instance
    with _provider_lock:
        _provider_instance = None


__all__ = [
    "MemoryDashboardProvider",
    "get_memory_dashboard_provider",
    "reset_memory_dashboard_provider_for_testing",
]
