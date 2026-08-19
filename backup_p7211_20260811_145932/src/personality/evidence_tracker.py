"""
Phase B.2.3 — Evidence Accumulation Tracker

职责：
累计同类成长事件的 evidence，生成 trust 渐进式增量。
防止单次大变化直接冲击长期人格。

调用：
    GrowthRecord × N
        ↓
    EvidenceTracker.absorb()
        ↓
    EvidenceTracker.summary(trait)
        ↓
    EvolutionEngine

设计：
- 每个 trait 维护累计窗口（默认 7 天）
- 时间衰减：超过半衰期的 evidence 权重减半
- 累计 delta 而非单次 delta
- 异常隔离

示例：
    第一次：trust +0.01
    第二次：trust +0.01
    长期累计：trust +0.03（而非 +0.02 之后的 +0.5）
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _parse_ts(ts: str) -> Optional[datetime]:
    """安全解析 ISO 时间戳"""
    if not ts:
        return None
    try:
        # 处理 "Z" 结尾
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


class EvidenceTracker:
    """
    累计 trait → evidence records，支持时间衰减。
    """

    # 默认半衰期（天）
    DEFAULT_HALF_LIFE_DAYS = 14.0

    # 默认累计窗口（天）
    DEFAULT_WINDOW_DAYS = 30

    def __init__(
        self,
        half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
        window_days: float = DEFAULT_WINDOW_DAYS,
    ):
        self.half_life_days = float(half_life_days)
        self.window_days = float(window_days)
        # trait -> list of evidence dicts
        self._store: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    # ============================================================
    # absorb: 吸收一条 GrowthRecord 的 evidence
    # ============================================================
    def absorb(self, record: Dict[str, Any]) -> int:
        """
        吸收一条 GrowthRecord。
        返回吸收的 evidence 数量。
        """
        try:
            record_id = record.get("record_id", "")
            record_ts = record.get("timestamp", "")
            confidence = float(record.get("confidence", 0.0) or 0.0)
            changes = record.get("changes", {}) or {}
            meaning = record.get("meaning", "")
            affected = record.get("affected_dimensions", []) or []

            absorbed = 0
            for dim in affected:
                change = changes.get(dim, {}) if isinstance(changes, dict) else {}
                delta = change.get("delta", 0.0) if isinstance(change, dict) else 0.0
                try:
                    delta = float(delta)
                except Exception:
                    delta = 0.0

                evidence = {
                    "record_id": record_id,
                    "timestamp": record_ts,
                    "confidence": confidence,
                    "delta": delta,
                    "meaning": meaning,
                }
                self._store[dim].append(evidence)
                absorbed += 1
            return absorbed
        except Exception as e:
            logger.warning("[evidence_absorb_failed] %s", e)
            return 0

    # ============================================================
    # summary: 获取 trait 的累计 evidence
    # ============================================================
    def summary(self, trait: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        返回 trait 的累计 evidence 摘要：
        - count: 有效 evidence 数量
        - cumulative_delta: 时间加权累计 delta
        - avg_confidence: 时间加权平均 confidence
        - earliest_ts / latest_ts: 时间跨度
        - reason: 累计 reason
        - source_record_ids: 来源 record id 列表
        """
        now = now or datetime.now()
        items = self._store.get(trait, []) or []

        if not items:
            return {
                "trait": trait,
                "count": 0,
                "cumulative_delta": 0.0,
                "avg_confidence": 0.0,
                "earliest_ts": "",
                "latest_ts": "",
                "reason": "",
                "source_record_ids": [],
            }

        weighted_delta_sum = 0.0
        weighted_conf_sum = 0.0
        weight_sum = 0.0
        source_ids: List[str] = []
        reasons: List[str] = []
        earliest_ts = ""
        latest_ts = ""
        valid_count = 0

        for it in items:
            ts = _parse_ts(it.get("timestamp", ""))
            if ts is None:
                continue
            # 窗口过滤
            age_days = (now - ts).total_seconds() / 86400.0
            if age_days > self.window_days:
                continue
            # 时间衰减权重
            if self.half_life_days > 0:
                weight = math.pow(0.5, age_days / self.half_life_days)
            else:
                weight = 1.0

            try:
                d = float(it.get("delta", 0.0))
                c = float(it.get("confidence", 0.0))
            except Exception:
                continue

            weighted_delta_sum += d * weight
            weighted_conf_sum += c * weight
            weight_sum += weight
            valid_count += 1
            rid = it.get("record_id", "")
            if rid:
                source_ids.append(rid)
            meaning = it.get("meaning", "")
            if meaning:
                reasons.append(meaning)

            if not earliest_ts or it["timestamp"] < earliest_ts:
                earliest_ts = it["timestamp"]
            if not latest_ts or it["timestamp"] > latest_ts:
                latest_ts = it["timestamp"]

        if weight_sum <= 0 or valid_count == 0:
            return {
                "trait": trait,
                "count": 0,
                "cumulative_delta": 0.0,
                "avg_confidence": 0.0,
                "earliest_ts": "",
                "latest_ts": "",
                "reason": "",
                "source_record_ids": [],
            }

        return {
            "trait": trait,
            "count": valid_count,
            "cumulative_delta": round(weighted_delta_sum, 4),
            "avg_confidence": round(weighted_conf_sum / weight_sum, 4),
            "earliest_ts": earliest_ts,
            "latest_ts": latest_ts,
            "reason": " | ".join(reasons[:5]),
            "source_record_ids": source_ids,
        }

    # ============================================================
    # 测试辅助
    # ============================================================
    def reset(self) -> None:
        self._store.clear()

    def total_records(self) -> int:
        return sum(len(v) for v in self._store.values())
