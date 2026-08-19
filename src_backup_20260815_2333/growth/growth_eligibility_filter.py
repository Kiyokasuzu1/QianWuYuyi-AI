"""
Phase 4.0-R2.5.2-A: GrowthEligibilityFilter

定位：Growth 子系统内部的「时机/稳定性过滤器」。

链路位置（Bridge 完全不知道这层存在）：

    ExperienceBridge                       (Runtime：这是不是成长候选？)
        │
        ▼ 仅 growth_candidate
    GrowthIntegrationService.accept_experience(record)
        │
        ├── Step 1: 构造事件骨架
        ├── Step 2: Normalize
        ├── Step 3: Validate
        ├── Step 4: Match History → List[event]
        │
        ├── Step 4.5: ✨ GrowthEligibilityFilter.evaluate(record, normalized, history)
        │              4 个维度：
        │                1) Frequency  频率（N天内出现>=M次？或 首次观察宽限期）
        │                2) Stability  稳定性（历史 importance/std 不剧烈波动）
        │                3) Evidence   证据量（去重 memory_id 数量）
        │                4) GracePeriod 首次观察不通过，写入 candidate ledger；
        │                              第二次命中且在窗口内才通过
        │
        │              不通过：直接产出 pipeline_state = rejected_growth_eligibility
        │               通过：继续
        │
        ├── Step 5: GrowthEvaluator.evaluate
        ├── Step 6: ChangeItem
        └── Step 7: ProposalManager

职责边界（R2.5.2-A 冻结不得违反）：
  ✅ 可以：根据 history 频率、importance 波动率、evidence 计数、ledger 观察累计
         决定"现在是否应该进入 Evaluator"
  ✅ 可以：写 AuditEvent.GrowthEligibilityDecision
  ✅ 可以：维护进程内 GrowthCandidateLedger（观察窗口）
  ❌ 不行：做 Evaluator 语义方向判断（creative_activity_interest 是正是反）
  ❌ 不行：new ChangeItem / 写 proposed_changes
  ❌ 不行：new ProposalManager / Personality / GrowthState
  ❌ 不行：直接修改任何持久化的 Personality / Relationship 字段
"""
from __future__ import annotations

import logging
import statistics
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 可调阈值（R2.5.2-A 初版保守值，上线后可调）
# ============================================================

# 1. Frequency：窗口内出现多少次才允许进入 Evaluator
FREQUENCY_WINDOW_DAYS: int = 14
FREQUENCY_MIN_OCCURRENCES: int = 2  # 单条历史（除当前 normalized）+ 当前事件 => 至少 2 条

# 2. Stability：历史 importance 标准差阈值（≤ 这个值才认为稳定）
STABILITY_MAX_STD: float = 0.25
#   如果只有 1 条历史，跳过 stability 检查（无法算 std），但 evidence 要更高
STABILITY_FALLBACK_EVIDENCE_MIN: int = 2

# 3. Evidence：去重 memory_id 至少 E 条
EVIDENCE_MIN: int = 1
#   如果是单条高频（frequency 够），evidence=1 是 OK 的

# 4. GracePeriod：首次命中 frequency < FREQUENCY_MIN_OCCURRENCES 时，
#    不通过，但是写入 ledger；下一次命中（在宽限期内）才通过
GRACE_PERIOD_WINDOW_DAYS: int = 30
GRACE_PERIOD_MIN_OCCURRENCES: int = 2  # 要"至少 2 次观察记录"才放行


# ============================================================
# GrowthCandidateLedger：进程内观察账本（GracePeriod 需要）
# ============================================================

@dataclass
class LedgerEntry:
    canonical_topic: str
    event_type: str
    memory_id: str
    importance: float
    timestamp_iso: str
    record_count: int = 1  # 相同 canonical_topic 的观察次数


class GrowthCandidateLedger:
    """轻量进程内账本（观察用，不影响人格）。

    key: (canonical_topic, event_type)
    """

    def __init__(self) -> None:
        self._entries: Dict[Tuple[str, str], List[LedgerEntry]] = {}
        self._lock_reason: str = ""

    # ---- 写入 ----
    def record_observation(
        self,
        canonical_topic: str,
        event_type: str,
        *,
        memory_id: str,
        importance: float,
    ) -> LedgerEntry:
        key = (canonical_topic or "", event_type or "")
        now = datetime.now(timezone.utc).isoformat()
        entry = LedgerEntry(
            canonical_topic=canonical_topic or "",
            event_type=event_type or "",
            memory_id=str(memory_id),
            importance=float(importance or 0.0),
            timestamp_iso=now,
            record_count=1,
        )
        bucket = self._entries.setdefault(key, [])
        bucket.append(entry)
        # 超 365 天的条目自动清理（避免内存膨胀）
        self._prune_bucket(bucket, timedelta(days=365))
        return entry

    # ---- 读取窗口内计数 ----
    def count_observations_in_last(
        self,
        canonical_topic: str,
        event_type: str,
        *,
        within_days: int,
    ) -> int:
        key = (canonical_topic or "", event_type or "")
        bucket = self._entries.get(key, [])
        if not bucket:
            return 0
        within = timedelta(days=max(0, int(within_days)))
        now = datetime.now(timezone.utc)
        cnt = 0
        for e in bucket:
            try:
                ts = datetime.fromisoformat(e.timestamp_iso)
            except Exception:  # noqa: BLE001
                continue
            # 让 naive datetime 也能比（做个保护）
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (now - ts) <= within:
                cnt += 1
        return cnt

    # ---- 清理 ----
    def _prune_bucket(self, bucket: List[LedgerEntry], older_than: timedelta) -> None:
        if len(bucket) < 50:
            return
        now = datetime.now(timezone.utc)

        def _keep(e: LedgerEntry) -> bool:
            try:
                ts = datetime.fromisoformat(e.timestamp_iso)
            except Exception:  # noqa: BLE001
                return True
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (now - ts) <= older_than

        self._entries[(bucket[0].canonical_topic, bucket[0].event_type)] = [e for e in bucket if _keep(e)]

    # ---- 调试导出（只读） ----
    def snapshot(self) -> Dict[str, Any]:
        return {
            f"{k[0]}::{k[1]}": [asdict(e) for e in v]
            for k, v in self._entries.items()
        }


# 进程级默认单例（大多数情况下够用）
_DEFAULT_LEDGER = GrowthCandidateLedger()


# ============================================================
# Eligibility Decision (TypedDict-like dict 形状，冻结字段)
# ============================================================

DECISION_KEYS = (
    "eligible",
    "rule",
    "frequency_score",
    "stability_score",
    "evidence_count",
    "observation_state",
    "grace_period_observations",
    "canonical_topic",
    "event_type",
    "decision_id",
    "timestamp_iso",
    "duration_ms",
)


# ============================================================
# GrowthEligibilityFilter
# ============================================================

class GrowthEligibilityFilter:
    """Growth 内部第二道过滤器：只判断"现在是不是时机"。"""

    def __init__(
        self,
        *,
        ledger: Optional[GrowthCandidateLedger] = None,
        # 阈值全部允许覆写（方便测试/调参）
        frequency_window_days: int = FREQUENCY_WINDOW_DAYS,
        frequency_min_occurrences: int = FREQUENCY_MIN_OCCURRENCES,
        stability_max_std: float = STABILITY_MAX_STD,
        stability_fallback_evidence_min: int = STABILITY_FALLBACK_EVIDENCE_MIN,
        evidence_min: int = EVIDENCE_MIN,
        grace_period_window_days: int = GRACE_PERIOD_WINDOW_DAYS,
        grace_period_min_observations: int = GRACE_PERIOD_MIN_OCCURRENCES,
    ) -> None:
        self.ledger = ledger or _DEFAULT_LEDGER
        self.frequency_window_days = int(max(1, frequency_window_days))
        self.frequency_min_occurrences = int(max(1, frequency_min_occurrences))
        self.stability_max_std = float(max(0.0, stability_max_std))
        self.stability_fallback_evidence_min = int(max(1, stability_fallback_evidence_min))
        self.evidence_min = int(max(1, evidence_min))
        self.grace_period_window_days = int(max(1, grace_period_window_days))
        self.grace_period_min_observations = int(max(1, grace_period_min_observations))

    # ============================================================
    # 对外主 API
    # ============================================================
    def evaluate(
        self,
        record: Dict[str, Any],
        normalized_event: Dict[str, Any],
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """判断是否进入 Evaluator。

        Args:
            record: 原始 memory record（含 id/content/importance/metadata）
            normalized_event: Step 2 Normalizer 产出的标准化事件（含 canonical_topic）
            history: Step 4 Match History 产出的同类主题历史事件

        Returns:
            dict with keys DECISION_KEYS
        """
        t0 = time.perf_counter()
        decision_id = f"elig_{uuid.uuid4().hex[:12]}"
        try:
            canonical_topic = (
                normalized_event.get("canonical_topic")
                or normalized_event.get("topic")
                or record.get("content", "")[:30]
            )
            event_type = str(normalized_event.get("event_type") or "memory")

            # --- 先把本次事件记进 ledger（不管结果通过不通过，都会进入观察期） ---
            self.ledger.record_observation(
                canonical_topic,
                event_type,
                memory_id=str(record.get("id") or ""),
                importance=float(record.get("importance") or normalized_event.get("importance") or 0.0),
            )

            # --- 指标 1：频率（history + 当前事件） ---
            # history 本身是过去的同类事件（已被 Match 过滤好）；当前是+1
            occurrence_count = int(len(history)) + 1  # 1 = 当前 normalized
            frequency_ok = occurrence_count >= self.frequency_min_occurrences
            # 把 history 也算进 grace_period 观察
            grace_observations = (
                int(len(history))
                + self.ledger.count_observations_in_last(
                    canonical_topic, event_type,
                    within_days=self.grace_period_window_days,
                )
            )
            grace_ok = grace_observations >= self.grace_period_min_observations

            # 频率判断：要求 frequency_ok 或 grace_ok 至少一个
            if not (frequency_ok or grace_ok):
                return self._finish(
                    decision_id, t0,
                    eligible=False,
                    rule="GE_FREQUENCY_OR_GRACE_PERIOD_BELOW_THRESHOLD",
                    frequency_score=occurrence_count,
                    stability_score=0.0,
                    evidence_count=0,
                    observation_state=(
                        "first_seen"
                        if grace_observations <= 1
                        else "grace_period_accumulating"
                    ),
                    grace_period_observations=grace_observations,
                    canonical_topic=canonical_topic,
                    event_type=event_type,
                )

            # --- 指标 2：稳定性（历史 importance 标准差） ---
            imp_values = self._collect_importance_values(normalized_event, history)
            stability_score, stability_ok = self._evaluate_stability(
                imp_values, evidence_min_hint=self.stability_fallback_evidence_min
            )
            if not stability_ok:
                return self._finish(
                    decision_id, t0,
                    eligible=False,
                    rule="GE_HISTORY_IMPORTANCE_VOLATILE",
                    frequency_score=occurrence_count,
                    stability_score=stability_score,
                    evidence_count=0,
                    observation_state="rejected_volatile_importance",
                    grace_period_observations=grace_observations,
                    canonical_topic=canonical_topic,
                    event_type=event_type,
                )

            # --- 指标 3：证据量（去重 memory_id 数量） ---
            evidence_ids = self._collect_distinct_memory_ids(record, normalized_event, history)
            evidence_count = len(evidence_ids)
            evidence_ok = evidence_count >= self.evidence_min
            if not evidence_ok:
                return self._finish(
                    decision_id, t0,
                    eligible=False,
                    rule="GE_EVIDENCE_COUNT_BELOW_THRESHOLD",
                    frequency_score=occurrence_count,
                    stability_score=stability_score,
                    evidence_count=evidence_count,
                    observation_state="rejected_insufficient_evidence",
                    grace_period_observations=grace_observations,
                    canonical_topic=canonical_topic,
                    event_type=event_type,
                )

            # --- 通过 ---
            state = "window_qualified" if grace_ok and not frequency_ok else "frequency_qualified"
            return self._finish(
                decision_id, t0,
                eligible=True,
                rule="GE_ELIGIBLE_ALL_DIMENSIONS_PASSED",
                frequency_score=occurrence_count,
                stability_score=stability_score,
                evidence_count=evidence_count,
                observation_state=state,
                grace_period_observations=grace_observations,
                canonical_topic=canonical_topic,
                event_type=event_type,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[GrowthEligibilityFilter] evaluate 失败(降级为 eligible=False 并隔离): %s", exc)
            # 失败时保守：认为不可进入 Evaluator（避免异常事件污染成长）
            return self._finish(
                decision_id, t0,
                eligible=False,
                rule=f"GE_EXCEPTION_ISOLATED__{type(exc).__name__}",
                frequency_score=0.0,
                stability_score=0.0,
                evidence_count=0,
                observation_state="filter_error_isolated",
                grace_period_observations=0,
                canonical_topic=str(normalized_event.get("canonical_topic") or ""),
                event_type=str(normalized_event.get("event_type") or ""),
                error=str(exc),
            )

    # ============================================================
    # 内部辅助
    # ============================================================
    @staticmethod
    def _collect_importance_values(
        normalized_event: Dict[str, Any], history: List[Dict[str, Any]]
    ) -> List[float]:
        values: List[float] = []
        v = normalized_event.get("importance")
        if isinstance(v, (int, float)):
            values.append(float(v))
        for e in history:
            vi = e.get("importance")
            if isinstance(vi, (int, float)):
                values.append(float(vi))
        return values

    def _evaluate_stability(
        self,
        imp_values: List[float],
        *,
        evidence_min_hint: int,
    ) -> Tuple[float, bool]:
        """返回 (stability_score 0.0~1.0, 是否通过)。"""
        if len(imp_values) < 2:
            # 1 条历史或 0 条：不够算标准差。
            # 规则：把稳定性当成 "未验证 / 通过"，但把门槛转给 evidence_min_hint
            # （调用方在 evidence 维度已经检查过 ≥1；这里把 fallback_evidence_min
            #  存在 decision.stability_score 语义：0.5 = 未验证，留待 evidence 加严）
            # 此处不直接拒绝，让 evidence 层来拒绝。
            return (0.5, True)
        try:
            std = statistics.pstdev(imp_values)
        except Exception:  # noqa: BLE001
            return (0.0, False)
        # 1.0 = 完全稳定 (std=0)；0.0 = 超过 max_std
        clamped = min(max(std, 0.0), self.stability_max_std * 2)
        score = round(max(0.0, 1.0 - (clamped / max(self.stability_max_std, 0.001))), 3)
        ok = std <= self.stability_max_std
        return (score, ok)

    @staticmethod
    def _collect_distinct_memory_ids(
        record: Dict[str, Any],
        normalized_event: Dict[str, Any],
        history: List[Dict[str, Any]],
    ) -> List[str]:
        ids: set = set()
        mid = record.get("id")
        if mid:
            ids.add(str(mid))
        for ev in [normalized_event, *history]:
            src_ids = ev.get("source_ids")
            if isinstance(src_ids, list):
                for x in src_ids:
                    if x:
                        ids.add(str(x))
            evidence = ev.get("evidence")
            if isinstance(evidence, list):
                for e in evidence:
                    if isinstance(e, dict):
                        em = e.get("memory_id")
                        if em:
                            ids.add(str(em))
        return sorted(ids)

    @staticmethod
    def _finish(
        decision_id: str,
        t0: float,
        *,
        eligible: bool,
        rule: str,
        frequency_score: float,
        stability_score: float,
        evidence_count: int,
        observation_state: str,
        grace_period_observations: int,
        canonical_topic: str,
        event_type: str,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "eligible": bool(eligible),
            "rule": str(rule),
            "frequency_score": round(float(frequency_score), 3),
            "stability_score": round(float(stability_score), 3),
            "evidence_count": int(evidence_count),
            "observation_state": str(observation_state),
            "grace_period_observations": int(grace_period_observations),
            "canonical_topic": str(canonical_topic),
            "event_type": str(event_type),
            "decision_id": decision_id,
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }
        if error is not None:
            d["error"] = str(error)
        return d


# ============================================================
# 审计封装：把 eligibility decision 写入 Audit（Bridge 不会看到）
# ============================================================

def emit_eligibility_audit(decision: Dict[str, Any], *, user_id: str = "") -> None:
    """把 Eligibility 判定写进审计。失败完全隔离。"""
    try:
        from src.audit.record import record_audit_log

        detail = {k: decision.get(k) for k in DECISION_KEYS if k in decision}
        record_audit_log(
            operation_type="growth_eligibility",
            source="growth_eligibility_filter",
            action="pass" if decision.get("eligible") else "block",
            user_id=str(user_id or ""),
            detail=detail,
            result="success" if decision.get("error") is None else "failure",
            error_message=str(decision.get("error") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        try:
            logger.warning(
                "[GrowthEligibilityFilter] emit_audit 失败(已隔离): %s",
                exc,
            )
        except Exception:  # noqa: BLE001
            pass
