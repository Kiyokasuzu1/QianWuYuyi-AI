# -*- coding: utf-8 -*-
"""
src/growth/growth_limiter.py

Phase 5.5.2 Stability Layer: GrowthRateLimiter

限制 Growth 速率，防止人格过度变化：
- 单次 personality delta（单次变更量）
- 每日变化次数（每日配额）
- trait drift 速度（每小时最大变化量）
- 置信度门槛（低置信度不通过）

职责：
- 提供 RateLimitDecision
- 跟踪 trait drift 历史（内存 + 可选持久化）
- 不修改 Authority 数据
- 失败不 silent fail

设计原则：
- 不持有 Authority 引用
- 不引入 EventBus
- 阈值可配置
- 提供 dry_run 模式
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 决策
# ============================================================

from enum import Enum


class DecisionType(str, Enum):
    """限流决策类型"""
    ALLOW = "allow"
    DENY = "deny"
    WARN = "warn"  # 通过但记录警告


@dataclass
class RateLimitDecision:
    """单次限流决策"""
    decision_id: str = field(default_factory=lambda: f"rld_{uuid.uuid4().hex[:10]}")
    decision: str = DecisionType.ALLOW.value
    reason: str = ""
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    proposal_id: str = ""
    trait: str = ""
    proposed_delta: float = 0.0
    timestamp: str = field(default_factory=_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_allowed(self) -> bool:
        return self.decision in (DecisionType.ALLOW.value, DecisionType.WARN.value)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_allowed"] = self.is_allowed
        return d


# ============================================================
# 默认阈值
# ============================================================

DEFAULT_THRESHOLDS = {
    # 单次 personality delta 上限（绝对值）
    "max_single_delta": 0.15,
    # 每日变化次数上限
    "max_daily_changes": 20,
    # 每小时 trait drift 速度（绝对值）
    "max_trait_drift_per_hour": 0.30,
    # 置信度门槛
    "min_confidence": 0.30,
    # trait drift 窗口（小时）
    "drift_window_hours": 1,
    # 警告阈值（达到该比例时 WARN）
    "warn_ratio": 0.8,
}


# ============================================================
# Growth Rate Limiter
# ============================================================

class GrowthRateLimiter:
    """
    Growth 速率限制器。

    跟踪每个 trait 的 drift 历史，决策是否允许新变更。

    使用示例：
        limiter = GrowthRateLimiter()
        decision = limiter.check(
            trait="openness",
            proposed_delta=0.05,
            confidence=0.7,
            proposal_id="prop_001",
        )
        if decision.is_allowed:
            limiter.record(trait="openness", delta=0.05, proposal_id="prop_001")
    """

    def __init__(
        self,
        max_single_delta: float = DEFAULT_THRESHOLDS["max_single_delta"],
        max_daily_changes: int = DEFAULT_THRESHOLDS["max_daily_changes"],
        max_trait_drift_per_hour: float = DEFAULT_THRESHOLDS["max_trait_drift_per_hour"],
        min_confidence: float = DEFAULT_THRESHOLDS["min_confidence"],
        drift_window_hours: int = DEFAULT_THRESHOLDS["drift_window_hours"],
        warn_ratio: float = DEFAULT_THRESHOLDS["warn_ratio"],
        storage_path: Optional[str] = None,
    ):
        self._max_single_delta = abs(float(max_single_delta))
        self._max_daily_changes = int(max_daily_changes)
        self._max_trait_drift_per_hour = abs(float(max_trait_drift_per_hour))
        self._min_confidence = float(min_confidence)
        self._drift_window = timedelta(hours=int(drift_window_hours))
        self._warn_ratio = float(warn_ratio)

        # trait drift 历史（in-memory deque，按时间排序）
        self._drift_history: Dict[str, Deque[Dict[str, Any]]] = {}

        # 每日变化计数（按日期分桶）
        self._daily_counts: Dict[str, Dict[str, int]] = {}  # {date: {trait: count}}

        # 持久化路径
        self._path = Path(storage_path or "data/growth/limiter/limiter_state.json")
        if storage_path is not None or True:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not self._path.exists():
                self._init_file()
            else:
                self._load()

    def _init_file(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": "1.0",
                    "drift_history": {},
                    "daily_counts": {},
                },
                f, ensure_ascii=False, indent=2,
            )

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 仅加载最近 24h 数据（避免冷启动数据过多）
            cutoff = datetime.utcnow() - timedelta(hours=24)
            for trait, records in data.get("drift_history", {}).items():
                dq: Deque[Dict[str, Any]] = deque()
                for r in records:
                    try:
                        ts = datetime.fromisoformat(r["timestamp"].rstrip("Z"))
                        if ts >= cutoff:
                            dq.append(r)
                    except Exception:
                        continue
                self._drift_history[trait] = dq
            self._daily_counts = data.get("daily_counts", {})
        except Exception as e:
            logger.error(f"GrowthRateLimiter 加载失败: {e}")

    def _save(self) -> None:
        try:
            data = {
                "version": "1.0",
                "drift_history": {
                    t: list(dq) for t, dq in self._drift_history.items()
                },
                "daily_counts": self._daily_counts,
            }
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"GrowthRateLimiter 保存失败: {e}")

    # ============================================================
    # 决策
    # ============================================================

    def check(
        self,
        trait: str,
        proposed_delta: float,
        confidence: float,
        proposal_id: str = "",
        dry_run: bool = False,
    ) -> RateLimitDecision:
        """
        检查是否允许指定变更。

        Args:
            trait: 变更的 trait 名称
            proposed_delta: 提议的 delta
            confidence: 置信度
            proposal_id: Proposal ID（追踪用）
            dry_run: True=不实际记录（仅决策）

        Returns:
            RateLimitDecision
        """
        decision = RateLimitDecision(
            proposal_id=proposal_id,
            trait=trait,
            proposed_delta=proposed_delta,
        )

        # 1. 检查单次 delta
        if abs(proposed_delta) > self._max_single_delta:
            decision.decision = DecisionType.DENY.value
            decision.violations.append(
                f"单次 delta {abs(proposed_delta):.4f} 超过上限 {self._max_single_delta}"
            )

        # 2. 检查置信度
        if confidence < self._min_confidence:
            decision.decision = DecisionType.DENY.value
            decision.violations.append(
                f"confidence {confidence:.2f} 低于门槛 {self._min_confidence}"
            )

        # 3. 检查每日配额
        today = datetime.utcnow().strftime("%Y-%m-%d")
        daily_count = self._daily_counts.get(today, {}).get(trait, 0)
        if daily_count >= self._max_daily_changes:
            decision.decision = DecisionType.DENY.value
            decision.violations.append(
                f"今日 {trait} 变化次数 {daily_count} 达到上限 {self._max_daily_changes}"
            )

        # 4. 检查 trait drift 速度
        drift_sum = self._compute_drift_in_window(trait)
        if abs(drift_sum + proposed_delta) > self._max_trait_drift_per_hour:
            decision.decision = DecisionType.DENY.value
            decision.violations.append(
                f"trait {trait} 在 {self._drift_window.total_seconds()/3600:.0f}h 窗口内"
                f" drift {abs(drift_sum):.4f} + delta {abs(proposed_delta):.4f}"
                f" 超过上限 {self._max_trait_drift_per_hour}"
            )

        # 5. 警告阈值
        if decision.decision == DecisionType.ALLOW.value:
            single_ratio = abs(proposed_delta) / self._max_single_delta
            daily_ratio = daily_count / self._max_daily_changes if self._max_daily_changes > 0 else 0
            if single_ratio >= self._warn_ratio or daily_ratio >= self._warn_ratio:
                decision.decision = DecisionType.WARN.value
                decision.warnings.append(
                    f"接近上限：single_ratio={single_ratio:.2f}, daily_ratio={daily_ratio:.2f}"
                )

        # 6. 决策原因
        if decision.decision == DecisionType.ALLOW.value:
            decision.reason = "all_checks_passed"
        elif decision.decision == DecisionType.WARN.value:
            decision.reason = "passed_with_warnings"
        else:
            decision.reason = "; ".join(decision.violations)

        return decision

    def _compute_drift_in_window(self, trait: str) -> float:
        """计算 trait 在窗口内的 drift 总量"""
        records = self._drift_history.get(trait)
        if not records:
            return 0.0
        cutoff = datetime.utcnow() - self._drift_window
        total = 0.0
        for r in records:
            try:
                ts = datetime.fromisoformat(r["timestamp"].rstrip("Z"))
                if ts >= cutoff:
                    total += float(r.get("delta", 0) or 0)
            except Exception:
                continue
        return total

    # ============================================================
    # 记录
    # ============================================================

    def record(
        self,
        trait: str,
        delta: float,
        proposal_id: str = "",
        actor: str = "system",
    ) -> bool:
        """
        记录一次实际发生的变更（通过决策后调用）。

        Returns:
            是否成功记录
        """
        try:
            now = _now_iso()
            record = {
                "timestamp": now,
                "delta": float(delta),
                "proposal_id": proposal_id,
                "actor": actor,
            }
            dq = self._drift_history.setdefault(trait, deque())
            dq.append(record)
            # 清理窗口外数据
            cutoff = datetime.utcnow() - self._drift_window
            while dq:
                try:
                    first_ts = datetime.fromisoformat(dq[0]["timestamp"].rstrip("Z"))
                    if first_ts < cutoff:
                        dq.popleft()
                    else:
                        break
                except Exception:
                    dq.popleft()

            # 每日计数
            today = datetime.utcnow().strftime("%Y-%m-%d")
            self._daily_counts.setdefault(today, {})
            self._daily_counts[today][trait] = self._daily_counts[today].get(trait, 0) + 1
            # 清理 7 天前数据
            cutoff_date = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
            self._daily_counts = {
                d: c for d, c in self._daily_counts.items() if d >= cutoff_date
            }

            self._save()
            return True
        except Exception as e:
            logger.error(f"GrowthRateLimiter.record 失败: {e}")
            return False

    # ============================================================
    # 查询
    # ============================================================

    def get_trait_drift(self, trait: str) -> float:
        """获取 trait 在窗口内的 drift"""
        return self._compute_drift_in_window(trait)

    def get_daily_count(self, trait: str) -> int:
        """获取今日 trait 变化次数"""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        return self._daily_counts.get(today, {}).get(trait, 0)

    def get_thresholds(self) -> Dict[str, Any]:
        """获取当前阈值"""
        return {
            "max_single_delta": self._max_single_delta,
            "max_daily_changes": self._max_daily_changes,
            "max_trait_drift_per_hour": self._max_trait_drift_per_hour,
            "min_confidence": self._min_confidence,
            "drift_window_hours": self._drift_window.total_seconds() / 3600,
            "warn_ratio": self._warn_ratio,
        }

    def reset(self) -> None:
        """重置所有状态（仅供测试）"""
        self._drift_history.clear()
        self._daily_counts.clear()
        if self._path.exists():
            self._save()
