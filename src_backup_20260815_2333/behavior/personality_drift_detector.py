# -*- coding: utf-8 -*-
"""
src/behavior/personality_drift_detector.py

Phase 3.7.6 → 3.7.7：人格漂移检测器

职责：
  - 对比不同时间段的风格快照，检测人格漂移
  - 生成漂移报告（哪个指标变化最大、是否超出阈值）
  - 不自动修正，只做检测和告警

Phase 3.7.7 升级：
  - 新增 check_same_topic_drift()：同一话题域内的漂移检测（排除话题适配干扰）
  - 新增 check_core_personality_drift()：核心人格向量漂移检测
  - check_drift() 自动使用话题归一化（当快照含 topic_context 时）
  - 向后兼容：旧版无 topic_context 的快照仍正常检测

检测维度：
  - 单指标漂移：某个风格指标变化超过阈值
  - 整体漂移：多个指标同时朝同一方向移动
  - 角色崩塌：warmth 骤降 + formality 骤升
  - 同话题漂移：同一话题域内的风格变化（排除话题适配）
  - 核心人格漂移：深层人格特质的变化

约束：
  - 不调用 LLM
  - 不修改状态
  - 只读分析
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import logging

from .response_style_monitor import ResponseStyleMonitor

logger = logging.getLogger(__name__)

# ============================================================
# 漂移阈值
# ============================================================

# 单指标漂移阈值（绝对值变化）
DRIFT_THRESHOLDS = {
    "warmth": 0.25,         # 温暖度变化超过 0.25
    "curiosity": 0.25,      # 好奇心变化超过 0.25
    "initiative": 0.30,     # 主动性变化超过 0.30
    "formality": 0.30,      # 正式度变化超过 0.30
    "playfulness": 0.25,    # 活泼度变化超过 0.25
}

# 整体漂移阈值（多个指标同时变化）
MULTI_DRIFT_THRESHOLD = 0.20  # 至少 3 个指标变化超过此值

# 角色崩塌检测
ROLE_COLLAPSE_WARMTH_DROP = 0.30     # warmth 下降超过 0.30
ROLE_COLLAPSE_FORMALITY_RISE = 0.30  # formality 上升超过 0.30


# ============================================================
# 数据结构
# ============================================================

@dataclass
class DriftAlert:
    """漂移告警。"""

    alert_type: str  # "single_metric" / "multi_metric" / "role_collapse"
    severity: str  # "warning" / "critical"
    metric: str = ""  # 漂移指标名
    baseline_value: float = 0.0
    current_value: float = 0.0
    delta: float = 0.0
    message: str = ""
    timestamp: str = ""


@dataclass
class DriftReport:
    """漂移检测报告。"""

    report_id: str = ""
    timestamp: str = ""
    baseline_period: str = ""  # 基线时间段
    current_period: str = ""  # 当前时间段
    baseline_snapshot_count: int = 0
    current_snapshot_count: int = 0
    alerts: List[DriftAlert] = field(default_factory=list)
    metric_changes: Dict[str, float] = field(default_factory=dict)
    has_drift: bool = False
    summary: str = ""

    # Phase 3.7.7：话题感知字段
    topic_domain: str = ""  # 检测的话题域（空 = 全话题对比）
    topic_aware: bool = False  # 是否使用了话题归一化
    core_personality_changes: Dict[str, float] = field(default_factory=dict)  # 核心人格变化


# ============================================================
# 漂移检测器
# ============================================================

class PersonalityDriftDetector:
    """Phase 3.7.6 → 3.7.7：人格漂移检测器。

    用法：
        detector = PersonalityDriftDetector(monitor)

        # 全话题漂移检测（向后兼容）
        report = detector.check_drift(
            baseline_days=(7, 1),
            current_days=(1, 0),
        )

        # Phase 3.7.7：同话题漂移检测
        report = detector.check_same_topic_drift(
            topic_domain="emotional",
            baseline_days=(7, 3),
            current_days=(3, 0),
        )

        # Phase 3.7.7：核心人格漂移检测
        report = detector.check_core_personality_drift(
            baseline_days=(7, 3),
            current_days=(3, 0),
        )

        if report.has_drift:
            print(report.summary)
    """

    METRICS = ["warmth", "curiosity", "initiative", "formality", "playfulness"]
    CORE_METRICS = ["curiosity", "honesty", "initiative", "long_term_focus", "relationship_orientation"]

    def __init__(self, monitor: Optional[ResponseStyleMonitor] = None):
        self._monitor = monitor or ResponseStyleMonitor()

    def _get_period_averages(
        self, start_days_ago: int, end_days_ago: int
    ) -> Tuple[Dict[str, float], int]:
        """获取指定时间段内各指标的平均值。

        Args:
            start_days_ago: 起始偏移（距今天数，更大 = 更早）
            end_days_ago: 结束偏移（距今天数，更小 = 更近）

        Returns:
            (averages, count): 各指标平均值 + 快照数量
        """
        now = time.time()
        # start 是更早的时间点，end 是更近的时间点
        period_start = now - start_days_ago * 86400  # 更早
        period_end = now - end_days_ago * 86400      # 更近

        records = self._monitor.load_all()
        values: Dict[str, List[float]] = {m: [] for m in self.METRICS}

        for rec in records:
            ts = rec.get("timestamp", "")
            if not ts:
                continue
            try:
                rec_time = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                # period_start <= rec_time < period_end
                if not (period_start <= rec_time < period_end):
                    continue
            except (ValueError, TypeError):
                continue

            for metric in self.METRICS:
                val = rec.get(metric, 0.0)
                if isinstance(val, (int, float)):
                    values[metric].append(val)

        averages = {}
        for metric in self.METRICS:
            vals = values[metric]
            averages[metric] = round(sum(vals) / len(vals), 3) if vals else 0.0

        count = max(len(values[m]) for m in self.METRICS)
        return averages, count

    def check_drift(
        self,
        baseline_days: Tuple[int, int] = (7, 1),
        current_days: Tuple[int, int] = (1, 0),
    ) -> DriftReport:
        """检测两个时间段之间的人格漂移。

        Args:
            baseline_days: 基线时间段 (start_offset, end_offset)，默认前 7 天到前 1 天
            current_days: 当前时间段 (start_offset, end_offset)，默认前 1 天到现在

        Returns:
            DriftReport: 漂移报告
        """
        report = DriftReport(
            report_id=f"drift_{int(time.time())}",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            baseline_period=f"{-baseline_days[0]}d ~ {-baseline_days[1]}d",
            current_period=f"{-current_days[0]}d ~ {-current_days[1]}d",
        )

        baseline_avgs, baseline_count = self._get_period_averages(*baseline_days)
        current_avgs, current_count = self._get_period_averages(*current_days)

        report.baseline_snapshot_count = baseline_count
        report.current_snapshot_count = current_count

        if current_count < 5:
            report.summary = "当前时间段快照不足（< 5 条），无法进行可靠的漂移检测"
            return report

        if baseline_count < 5:
            report.summary = "基线时间段快照不足（< 5 条），使用当前数据建立基线"
            # 仍做检测，但降低置信度
            baseline_avgs = current_avgs

        alerts: List[DriftAlert] = []
        metric_changes: Dict[str, float] = {}

        # 1. 单指标漂移检测
        drifting_metrics = 0
        for metric in self.METRICS:
            baseline = baseline_avgs.get(metric, 0.0)
            current = current_avgs.get(metric, 0.0)
            delta = current - baseline
            metric_changes[metric] = round(delta, 3)

            threshold = DRIFT_THRESHOLDS.get(metric, 0.25)
            if abs(delta) >= threshold:
                direction = "上升" if delta > 0 else "下降"
                alerts.append(DriftAlert(
                    alert_type="single_metric",
                    severity="warning",
                    metric=metric,
                    baseline_value=baseline,
                    current_value=current,
                    delta=delta,
                    message=f"{metric} {direction} {abs(delta):.3f}（{baseline:.3f} → {current:.3f}），超出阈值 {threshold}",
                    timestamp=report.timestamp,
                ))
                drifting_metrics += 1

        # 2. 整体漂移检测
        if drifting_metrics >= 3:
            # 检查是否同方向
            positive_deltas = sum(1 for d in metric_changes.values() if d > MULTI_DRIFT_THRESHOLD)
            negative_deltas = sum(1 for d in metric_changes.values() if d < -MULTI_DRIFT_THRESHOLD)
            if positive_deltas >= 3:
                alerts.append(DriftAlert(
                    alert_type="multi_metric",
                    severity="critical",
                    message=f"整体人格漂移：{positive_deltas} 个指标同时上升超过 {MULTI_DRIFT_THRESHOLD}",
                    timestamp=report.timestamp,
                ))
            elif negative_deltas >= 3:
                alerts.append(DriftAlert(
                    alert_type="multi_metric",
                    severity="critical",
                    message=f"整体人格漂移：{negative_deltas} 个指标同时下降超过 {MULTI_DRIFT_THRESHOLD}",
                    timestamp=report.timestamp,
                ))

        # 3. 角色崩塌检测
        warmth_delta = metric_changes.get("warmth", 0.0)
        formality_delta = metric_changes.get("formality", 0.0)
        if warmth_delta <= -ROLE_COLLAPSE_WARMTH_DROP and formality_delta >= ROLE_COLLAPSE_FORMALITY_RISE:
            alerts.append(DriftAlert(
                alert_type="role_collapse",
                severity="critical",
                message=(
                    f"角色崩塌风险：warmth 下降 {abs(warmth_delta):.3f} "
                    f"+ formality 上升 {formality_delta:.3f}，"
                    f"可能表示身份丢失或记忆断裂"
                ),
                timestamp=report.timestamp,
            ))

        report.alerts = alerts
        report.metric_changes = metric_changes
        report.has_drift = len(alerts) > 0

        # 生成摘要
        if report.has_drift:
            parts = [f"检测到 {len(alerts)} 个漂移告警："]
            for alert in alerts:
                parts.append(f"  [{alert.severity.upper()}] {alert.message}")
            report.summary = "\n".join(parts)
        else:
            changes_str = ", ".join(
                f"{m}:{d:+.3f}" for m, d in metric_changes.items()
            )
            report.summary = f"未检测到人格漂移。指标变化：{changes_str}"

        return report

    # ============================================================
    # Phase 3.7.7：话题感知 + 核心人格漂移检测
    # ============================================================

    def _get_period_snapshots_by_topic(
        self, start_days_ago: int, end_days_ago: int, topic_domain: str
    ) -> List[Dict[str, Any]]:
        """获取指定时间段内、指定话题域的快照。

        Args:
            start_days_ago: 起始偏移
            end_days_ago: 结束偏移
            topic_domain: 话题域（如 "technology", "emotional"）

        Returns:
            List[Dict]: 快照记录
        """
        now = time.time()
        period_start = now - start_days_ago * 86400
        period_end = now - end_days_ago * 86400

        records = self._monitor.load_all()
        filtered = []

        for rec in records:
            ts = rec.get("timestamp", "")
            if not ts:
                continue
            try:
                rec_time = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                if not (period_start <= rec_time < period_end):
                    continue
            except (ValueError, TypeError):
                continue

            # 检查话题域
            topic_ctx = rec.get("topic_context", {})
            rec_domain = topic_ctx.get("domain", "") if isinstance(topic_ctx, dict) else ""
            if rec_domain == topic_domain:
                filtered.append(rec)

        return filtered

    def _get_period_core_personality_avgs(
        self, start_days_ago: int, end_days_ago: int
    ) -> Tuple[Dict[str, float], int]:
        """获取指定时间段内核心人格向量的平均值。

        Returns:
            (averages, count): 各核心指标平均值 + 快照数量
        """
        now = time.time()
        period_start = now - start_days_ago * 86400
        period_end = now - end_days_ago * 86400

        records = self._monitor.load_all()
        values: Dict[str, List[float]] = {m: [] for m in self.CORE_METRICS}

        for rec in records:
            ts = rec.get("timestamp", "")
            if not ts:
                continue
            try:
                rec_time = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                if not (period_start <= rec_time < period_end):
                    continue
            except (ValueError, TypeError):
                continue

            cpv = rec.get("core_personality_vector", {})
            if not isinstance(cpv, dict):
                continue

            for metric in self.CORE_METRICS:
                val = cpv.get(metric, 0.0)
                if isinstance(val, (int, float)):
                    values[metric].append(val)

        averages = {}
        for metric in self.CORE_METRICS:
            vals = values[metric]
            averages[metric] = round(sum(vals) / len(vals), 3) if vals else 0.0

        count = max(len(values[m]) for m in self.CORE_METRICS)
        return averages, count

    def check_same_topic_drift(
        self,
        topic_domain: str,
        baseline_days: Tuple[int, int] = (7, 3),
        current_days: Tuple[int, int] = (3, 0),
    ) -> DriftReport:
        """Phase 3.7.7：同一话题域内的漂移检测。

        排除话题适配干扰，只比较同一话题域内的风格变化。

        Args:
            topic_domain: 话题域（"technology", "emotional", "philosophy", "daily_life", "creative"）
            baseline_days: 基线时间段
            current_days: 当前时间段

        Returns:
            DriftReport: 同话题漂移报告
        """
        report = DriftReport(
            report_id=f"drift_topic_{int(time.time())}",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            baseline_period=f"{-baseline_days[0]}d ~ {-baseline_days[1]}d",
            current_period=f"{-current_days[0]}d ~ {-current_days[1]}d",
            topic_domain=topic_domain,
            topic_aware=True,
        )

        # 获取同话题快照
        baseline_snaps = self._get_period_snapshots_by_topic(
            baseline_days[0], baseline_days[1], topic_domain
        )
        current_snaps = self._get_period_snapshots_by_topic(
            current_days[0], current_days[1], topic_domain
        )

        report.baseline_snapshot_count = len(baseline_snaps)
        report.current_snapshot_count = len(current_snaps)

        if len(current_snaps) < 3:
            report.summary = f"当前时间段「{topic_domain}」话题快照不足（< 3 条），无法检测"
            return report

        if len(baseline_snaps) < 3:
            report.summary = f"基线时间段「{topic_domain}」话题快照不足（< 3 条），无法检测"
            return report

        # 计算同话题内的指标平均值
        def _avg_metrics(snaps: List[Dict]) -> Dict[str, float]:
            avgs = {}
            for m in self.METRICS:
                vals = [s.get(m, 0.0) for s in snaps if isinstance(s.get(m), (int, float))]
                avgs[m] = round(sum(vals) / len(vals), 3) if vals else 0.0
            return avgs

        baseline_avgs = _avg_metrics(baseline_snaps)
        current_avgs = _avg_metrics(current_snaps)

        alerts: List[DriftAlert] = []
        metric_changes: Dict[str, float] = {}
        drifting_metrics = 0

        for metric in self.METRICS:
            baseline = baseline_avgs.get(metric, 0.0)
            current = current_avgs.get(metric, 0.0)
            delta = current - baseline
            metric_changes[metric] = round(delta, 3)

            threshold = DRIFT_THRESHOLDS.get(metric, 0.25)
            if abs(delta) >= threshold:
                direction = "上升" if delta > 0 else "下降"
                alerts.append(DriftAlert(
                    alert_type="single_metric",
                    severity="warning",
                    metric=metric,
                    baseline_value=baseline,
                    current_value=current,
                    delta=delta,
                    message=(
                        f"[{topic_domain}] {metric} {direction} {abs(delta):.3f} "
                        f"（{baseline:.3f} → {current:.3f}），超出阈值 {threshold}"
                    ),
                    timestamp=report.timestamp,
                ))
                drifting_metrics += 1

        # 角色崩塌（同话题内）
        warmth_delta = metric_changes.get("warmth", 0.0)
        formality_delta = metric_changes.get("formality", 0.0)
        if warmth_delta <= -ROLE_COLLAPSE_WARMTH_DROP and formality_delta >= ROLE_COLLAPSE_FORMALITY_RISE:
            alerts.append(DriftAlert(
                alert_type="role_collapse",
                severity="critical",
                message=(
                    f"[{topic_domain}] 角色崩塌风险：warmth 下降 {abs(warmth_delta):.3f} "
                    f"+ formality 上升 {formality_delta:.3f}"
                ),
                timestamp=report.timestamp,
            ))

        report.alerts = alerts
        report.metric_changes = metric_changes
        report.has_drift = len(alerts) > 0

        if report.has_drift:
            parts = [f"同话题「{topic_domain}」检测到 {len(alerts)} 个漂移告警："]
            for alert in alerts:
                parts.append(f"  [{alert.severity.upper()}] {alert.message}")
            report.summary = "\n".join(parts)
        else:
            changes_str = ", ".join(f"{m}:{d:+.3f}" for m, d in metric_changes.items())
            report.summary = f"同话题「{topic_domain}」未检测到人格漂移。指标变化：{changes_str}"

        return report

    def check_core_personality_drift(
        self,
        baseline_days: Tuple[int, int] = (7, 3),
        current_days: Tuple[int, int] = (3, 0),
    ) -> DriftReport:
        """Phase 3.7.7：核心人格向量漂移检测。

        检测不受话题影响的深层人格特质变化：
        curiosity, honesty, initiative, long_term_focus, relationship_orientation

        核心人格的漂移阈值比表层风格更严格（0.20 vs 0.25），
        因为核心人格变化更有意义。

        Args:
            baseline_days: 基线时间段
            current_days: 当前时间段

        Returns:
            DriftReport: 核心人格漂移报告
        """
        report = DriftReport(
            report_id=f"drift_core_{int(time.time())}",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            baseline_period=f"{-baseline_days[0]}d ~ {-baseline_days[1]}d",
            current_period=f"{-current_days[0]}d ~ {-current_days[1]}d",
            topic_aware=True,
        )

        baseline_avgs, baseline_count = self._get_period_core_personality_avgs(
            baseline_days[0], baseline_days[1]
        )
        current_avgs, current_count = self._get_period_core_personality_avgs(
            current_days[0], current_days[1]
        )

        report.baseline_snapshot_count = baseline_count
        report.current_snapshot_count = current_count

        if current_count < 3:
            report.summary = "当前时间段核心人格快照不足（< 3 条），无法检测"
            return report

        if baseline_count < 3:
            report.summary = "基线时间段核心人格快照不足（< 3 条），使用当前数据建立基线"
            baseline_avgs = current_avgs

        # 核心人格漂移阈值（比表层更严格）
        CORE_DRIFT_THRESHOLDS = {
            "curiosity": 0.20,
            "honesty": 0.20,
            "initiative": 0.20,
            "long_term_focus": 0.20,
            "relationship_orientation": 0.20,
        }

        alerts: List[DriftAlert] = []
        core_changes: Dict[str, float] = {}
        drifting_count = 0

        for metric in self.CORE_METRICS:
            baseline = baseline_avgs.get(metric, 0.0)
            current = current_avgs.get(metric, 0.0)
            delta = current - baseline
            core_changes[metric] = round(delta, 3)

            threshold = CORE_DRIFT_THRESHOLDS.get(metric, 0.20)
            if abs(delta) >= threshold:
                direction = "上升" if delta > 0 else "下降"
                alerts.append(DriftAlert(
                    alert_type="core_personality",
                    severity="warning",
                    metric=metric,
                    baseline_value=baseline,
                    current_value=current,
                    delta=delta,
                    message=(
                        f"核心人格 {metric} {direction} {abs(delta):.3f} "
                        f"（{baseline:.3f} → {current:.3f}），超出阈值 {threshold}"
                    ),
                    timestamp=report.timestamp,
                ))
                drifting_count += 1

        # 核心人格多指标漂移（更严格：2 个指标即触发）
        if drifting_count >= 2:
            alerts.append(DriftAlert(
                alert_type="core_multi_metric",
                severity="critical",
                message=f"核心人格多指标漂移：{drifting_count} 个核心特质同时变化",
                timestamp=report.timestamp,
            ))

        report.alerts = alerts
        report.core_personality_changes = core_changes
        report.metric_changes = core_changes
        report.has_drift = len(alerts) > 0

        if report.has_drift:
            parts = [f"核心人格检测到 {len(alerts)} 个漂移告警："]
            for alert in alerts:
                parts.append(f"  [{alert.severity.upper()}] {alert.message}")
            report.summary = "\n".join(parts)
        else:
            changes_str = ", ".join(f"{m}:{d:+.3f}" for m, d in core_changes.items())
            report.summary = f"核心人格未检测到漂移。变化：{changes_str}"

        return report

    def quick_check(self, days: int = 7) -> DriftReport:
        """快速检查：对比最近 7 天 vs 前 7 天的漂移。

        Args:
            days: 窗口大小（天）

        Returns:
            DriftReport: 漂移报告
        """
        return self.check_drift(
            baseline_days=(days * 2, days),
            current_days=(days, 0),
        )

    def get_trend(self, metric: str, days: int = 7) -> List[Tuple[str, float]]:
        """获取某个指标的每日趋势。

        Args:
            metric: 指标名
            days: 回溯天数

        Returns:
            List[(date, value)]: 每天的指标值
        """
        daily = self._monitor.get_daily_averages(days=days)
        trend = []
        for date_key, metrics in sorted(daily.items()):
            trend.append((date_key, metrics.get(metric, 0.0)))
        return trend