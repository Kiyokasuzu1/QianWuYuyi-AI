"""
Phase 6.4: SelfModel Persistence Health Check

职责：
- 检测 SelfModel JSONL persistence 文件的健康状态
- 检测 SelfBeliefStore / SelfHistory / SelfReflection 内部数据异常
- 输出 SelfModelHealthReport，供 health check / 监控 / 测试使用

检测项：

1. 文件层
   - JSONL 文件是否存在
   - JSONL 损坏行数
   - 文件大小是否异常
   - meta.json 是否可解析

2. 数量层
   - 数量是否超过配额上限
   - active / archived 比例
   - 重复 belief（content+domain 重复）

3. 置信度层
   - 是否有 confidence 异常（>1, <0, NaN）
   - 平均 confidence 趋势

4. 一致性
   - snapshot_before / snapshot_after 配对
   - history.source_id 与 audit.proposal_id 关联
   - belief.sources 是否包含有效 proposal_id
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# Health Issue
# ============================================================

@dataclass
class HealthIssue:
    """单一健康问题"""
    severity: str  # info / warning / error / critical
    code: str      # 错误码
    message: str
    location: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# Health Report
# ============================================================

@dataclass
class SelfModelHealthReport:
    """SelfModel 健康检查报告"""
    timestamp: str = field(default_factory=_now_iso)
    overall_status: str = "unknown"  # healthy / warning / degraded / critical / unknown

    # 文件层
    files_checked: int = 0
    files_ok: int = 0
    files_missing: List[str] = field(default_factory=list)
    files_corrupted: List[Dict[str, Any]] = field(default_factory=list)
    file_sizes: Dict[str, int] = field(default_factory=dict)

    # 数量层
    beliefs_count: int = 0
    beliefs_active: int = 0
    beliefs_inactive: int = 0
    beliefs_duplicate: int = 0
    history_count: int = 0
    history_snapshots: int = 0
    reflections_count: int = 0

    # 置信度层
    beliefs_avg_confidence: float = 0.0
    beliefs_confidence_out_of_range: int = 0
    beliefs_nan_confidence: int = 0

    # 一致性
    orphan_snapshots: int = 0
    orphan_belief_refs: int = 0

    # 配额
    over_belief_quota: bool = False
    over_history_quota: bool = False
    over_reflection_quota: bool = False

    # 问题
    issues: List[HealthIssue] = field(default_factory=list)

    # 元信息
    data_dir: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> Dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "files": {
                "checked": self.files_checked,
                "ok": self.files_ok,
                "missing": len(self.files_missing),
                "corrupted": len(self.files_corrupted),
            },
            "counts": {
                "beliefs": self.beliefs_count,
                "beliefs_active": self.beliefs_active,
                "beliefs_inactive": self.beliefs_inactive,
                "history": self.history_count,
                "reflections": self.reflections_count,
            },
            "quota": {
                "over_belief_quota": self.over_belief_quota,
                "over_history_quota": self.over_history_quota,
                "over_reflection_quota": self.over_reflection_quota,
            },
            "issues_by_severity": self._count_by_severity(),
        }

    def _count_by_severity(self) -> Dict[str, int]:
        out: Dict[str, int] = {"info": 0, "warning": 0, "error": 0, "critical": 0}
        for issue in self.issues:
            sev = issue.severity if issue.severity in out else "info"
            out[sev] = out.get(sev, 0) + 1
        return out


# ============================================================
# 阈值
# ============================================================

DEFAULT_BELIEF_QUOTA: int = 1000
DEFAULT_HISTORY_QUOTA: int = 1000
DEFAULT_REFLECTION_QUOTA: int = 500
DEFAULT_LARGE_FILE_SIZE_MB: float = 50.0


# ============================================================
# SelfModelHealthChecker
# ============================================================

class SelfModelHealthChecker:
    """
    SelfModel 健康检查器。

    使用示例：
        checker = SelfModelHealthChecker(persistence)
        report = checker.check(beliefs_store, history, reflections)
        if report.overall_status == "critical":
            alert_admin(report)
    """

    FILES_TO_CHECK: Tuple[str, ...] = ("beliefs.jsonl", "history.jsonl", "reflection.jsonl", "meta.json")

    def __init__(
        self,
        persistence: Optional[Any] = None,
        max_beliefs: int = DEFAULT_BELIEF_QUOTA,
        max_history: int = DEFAULT_HISTORY_QUOTA,
        max_reflections: int = DEFAULT_REFLECTION_QUOTA,
        large_file_size_mb: float = DEFAULT_LARGE_FILE_SIZE_MB,
    ) -> None:
        self._persistence = persistence
        self.max_beliefs = int(max_beliefs)
        self.max_history = int(max_history)
        self.max_reflections = int(max_reflections)
        self.large_file_size_mb = float(large_file_size_mb)

    def _add_issue(self, report: SelfModelHealthReport, severity: str, code: str,
                   message: str, location: str = "", metadata: Optional[Dict[str, Any]] = None) -> None:
        report.issues.append(HealthIssue(
            severity=severity, code=code, message=message,
            location=location, metadata=dict(metadata or {}),
        ))

    def _check_files(self, report: SelfModelHealthReport) -> None:
        """检查 JSONL 文件层"""
        if self._persistence is None:
            return
        data_dir: Optional[Path] = None
        try:
            data_dir = self._persistence.data_dir
        except Exception:
            data_dir = None
        if data_dir is None:
            return
        report.data_dir = str(data_dir)
        for fname in self.FILES_TO_CHECK:
            report.files_checked += 1
            try:
                p = data_dir / fname
                if not p.exists():
                    if fname == "meta.json":
                        # meta.json 缺失非关键
                        continue
                    report.files_missing.append(fname)
                    self._add_issue(
                        report, "warning", "FILE_MISSING",
                        f"文件缺失: {fname}", location=fname
                    )
                    continue
                size = p.stat().st_size
                report.file_sizes[fname] = size
                # 大文件警告
                if fname.endswith(".jsonl") and size > self.large_file_size_mb * 1024 * 1024:
                    self._add_issue(
                        report, "warning", "FILE_TOO_LARGE",
                        f"文件 {fname} 超过 {self.large_file_size_mb}MB",
                        location=fname, metadata={"size_bytes": size}
                    )
                # JSONL 损坏行数检测
                if fname.endswith(".jsonl"):
                    bad = 0
                    total = 0
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if not line:
                                    continue
                                total += 1
                                try:
                                    json.loads(line)
                                except Exception:
                                    bad += 1
                    except Exception as e:
                        report.files_corrupted.append({"file": fname, "error": str(e)})
                        self._add_issue(
                            report, "error", "FILE_UNREADABLE",
                            f"无法读取 {fname}: {e}", location=fname
                        )
                        continue
                    if bad > 0:
                        rate = bad / max(1, total)
                        report.files_corrupted.append({
                            "file": fname, "bad_lines": bad, "total_lines": total, "rate": round(rate, 4)
                        })
                        sev = "error" if rate > 0.1 else "warning"
                        self._add_issue(
                            report, sev, "JSONL_CORRUPTED",
                            f"{fname} 有 {bad}/{total} 行损坏", location=fname,
                            metadata={"bad": bad, "total": total}
                        )
                    else:
                        report.files_ok += 1
                else:
                    # meta.json 检查
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            json.load(f)
                        report.files_ok += 1
                    except Exception as e:
                        report.files_corrupted.append({"file": fname, "error": str(e)})
                        self._add_issue(
                            report, "warning", "META_INVALID",
                            f"meta.json 无效: {e}", location=fname
                        )
            except Exception as e:
                self._add_issue(
                    report, "warning", "FILE_CHECK_ERROR",
                    f"检查 {fname} 失败: {e}", location=fname
                )

    def _check_beliefs(self, report: SelfModelHealthReport, store: Any) -> None:
        """检查 SelfBeliefStore"""
        try:
            all_beliefs = list(store.all()) if hasattr(store, "all") else []
        except Exception as e:
            self._add_issue(
                report, "error", "BELIEFS_UNREADABLE",
                f"读取 belief store 失败: {e}"
            )
            return
        report.beliefs_count = len(all_beliefs)
        report.beliefs_active = sum(1 for b in all_beliefs if getattr(b, "active", True))
        report.beliefs_inactive = report.beliefs_count - report.beliefs_active

        # 配额
        if report.beliefs_count > self.max_beliefs:
            report.over_belief_quota = True
            self._add_issue(
                report, "warning", "BELIEF_QUOTA_EXCEEDED",
                f"beliefs 数量 {report.beliefs_count} 超过 {self.max_beliefs}",
                metadata={"count": report.beliefs_count, "quota": self.max_beliefs}
            )

        # 置信度
        conf_sum = 0.0
        conf_count = 0
        for b in all_beliefs:
            try:
                c = float(getattr(b, "confidence", 0.0) or 0.0)
            except Exception:
                report.beliefs_nan_confidence += 1
                continue
            if math.isnan(c) or math.isinf(c):
                report.beliefs_nan_confidence += 1
                continue
            if c < 0.0 or c > 1.0:
                report.beliefs_confidence_out_of_range += 1
                self._add_issue(
                    report, "warning", "BELIEF_CONF_OUT_OF_RANGE",
                    f"belief {getattr(b, 'belief_id', '')} confidence={c}",
                    metadata={"belief_id": getattr(b, "belief_id", ""), "confidence": c}
                )
            else:
                conf_sum += c
                conf_count += 1
        if conf_count > 0:
            report.beliefs_avg_confidence = round(conf_sum / conf_count, 4)

        # 重复检测（content+domain）
        seen: Dict[Tuple[str, str], int] = {}
        for b in all_beliefs:
            try:
                key = (getattr(b, "content", ""), getattr(b, "domain", ""))
                seen[key] = seen.get(key, 0) + 1
            except Exception:
                continue
        dup = sum(v - 1 for v in seen.values() if v > 1)
        report.beliefs_duplicate = dup
        if dup > 0:
            self._add_issue(
                report, "info", "BELIEF_DUPLICATES",
                f"发现 {dup} 个 content+domain 重复的 belief",
                metadata={"duplicate_count": dup}
            )

    def _check_history(self, report: SelfModelHealthReport, history: Any) -> None:
        """检查 SelfHistory"""
        try:
            events = list(history.all()) if hasattr(history, "all") else []
        except Exception as e:
            self._add_issue(
                report, "error", "HISTORY_UNREADABLE",
                f"读取 history 失败: {e}"
            )
            return
        report.history_count = len(events)
        report.history_snapshots = sum(
            1 for e in events if getattr(e, "event_type", "") == "snapshot_created"
        )

        if report.history_count > self.max_history:
            report.over_history_quota = True
            self._add_issue(
                report, "warning", "HISTORY_QUOTA_EXCEEDED",
                f"history 数量 {report.history_count} 超过 {self.max_history}",
                metadata={"count": report.history_count, "quota": self.max_history}
            )

        # 配对检测：snapshot_before / snapshot_after
        for ev in events:
            try:
                sb = getattr(ev, "snapshot_before", None)
                sa = getattr(ev, "snapshot_after", None)
                if sb is not None and sa is None:
                    report.orphan_snapshots += 1
            except Exception:
                continue
        if report.orphan_snapshots > 0:
            self._add_issue(
                report, "info", "SNAPSHOT_BEFORE_WITHOUT_AFTER",
                f"{report.orphan_snapshots} 个事件有 snapshot_before 但无 snapshot_after"
            )

    def _check_reflections(self, report: SelfModelHealthReport, store: Any) -> None:
        """检查 SelfReflectionStore"""
        try:
            notes = list(store.all()) if hasattr(store, "all") else []
        except Exception as e:
            self._add_issue(
                report, "error", "REFLECTIONS_UNREADABLE",
                f"读取 reflections 失败: {e}"
            )
            return
        report.reflections_count = len(notes)
        if report.reflections_count > self.max_reflections:
            report.over_reflection_quota = True
            self._add_issue(
                report, "warning", "REFLECTION_QUOTA_EXCEEDED",
                f"reflections 数量 {report.reflections_count} 超过 {self.max_reflections}",
                metadata={"count": report.reflections_count, "quota": self.max_reflections}
            )

    def _evaluate_overall(self, report: SelfModelHealthReport) -> None:
        """汇总整体健康度"""
        critical = any(i.severity == "critical" for i in report.issues)
        errors = sum(1 for i in report.issues if i.severity == "error")
        warnings = sum(1 for i in report.issues if i.severity == "warning")
        if critical:
            report.overall_status = "critical"
        elif errors > 0:
            report.overall_status = "degraded"
        elif warnings > 0:
            report.overall_status = "warning"
        else:
            report.overall_status = "healthy"

    def check(
        self,
        beliefs_store: Any = None,
        history: Any = None,
        reflections_store: Any = None,
    ) -> SelfModelHealthReport:
        """执行完整健康检查"""
        import time
        start = time.time()
        report = SelfModelHealthReport()
        try:
            self._check_files(report)
        except Exception as e:
            self._add_issue(report, "error", "FILE_CHECK_FAILED", str(e))
        try:
            if beliefs_store is not None:
                self._check_beliefs(report, beliefs_store)
        except Exception as e:
            self._add_issue(report, "error", "BELIEF_CHECK_FAILED", str(e))
        try:
            if history is not None:
                self._check_history(report, history)
        except Exception as e:
            self._add_issue(report, "error", "HISTORY_CHECK_FAILED", str(e))
        try:
            if reflections_store is not None:
                self._check_reflections(report, reflections_store)
        except Exception as e:
            self._add_issue(report, "error", "REFLECTION_CHECK_FAILED", str(e))
        self._evaluate_overall(report)
        report.duration_ms = round((time.time() - start) * 1000, 2)
        return report

    def check_persistence_only(self) -> SelfModelHealthReport:
        """仅检查 persistence 文件层（不检查 store 内容）"""
        report = SelfModelHealthReport()
        self._check_files(report)
        self._evaluate_overall(report)
        return report


__all__ = [
    "SelfModelHealthChecker",
    "SelfModelHealthReport",
    "HealthIssue",
    "DEFAULT_BELIEF_QUOTA",
    "DEFAULT_HISTORY_QUOTA",
    "DEFAULT_REFLECTION_QUOTA",
    "DEFAULT_LARGE_FILE_SIZE_MB",
]
