"""
Phase 4.0 — R2.5.2-C: TransitionAnalyzer（兴趣迁移分析器）

职责边界（严格遵循用户要求）：
  ✅ 回答：「这条经历，结合历史同类信号，是属于 增强 / 新兴趣浮现 / 兴趣迁移？」
  ❌ 不回答：「羽依以后喜欢什么。」
  ❌ 不修改 Personality / GrowthState / trait before-after。
  ❌ 不创建 ChangeItem / Proposal。只生成 InterestTransitionProposal 交给上游。

输入：
  - normalized_event:  EventNormalizer 产物（含 canonical_topic / growth_signal? 可选）
  - history_events:    EventHistoryMatcher 产出的同类主题历史事件（可能带 growth_signal）
  - evaluator_output:  GrowthEvaluator.evaluate() 产出（必须有 growth_signal 字段）

分析规则（简单稳定，R2.5.2-C 不做 ML；Gate C-4/5/6/7/8/9 逐个覆盖）：
  1. 如果当前信号为空 → analyzer 不产出 proposal（返回 None），留给 Evaluator 正常跑就行
  2. previous_signals = 历史中出现过的 unique 非空 growth_signal
  3. 若 当前信号 ∈ previous_signals  →  reinforce（增强）
  4. 若 当前信号 ∉ previous_signals
        且 previous_signals 为空     → new_interest_emerging（全新兴趣域）
  5. 若 当前信号 ∉ previous_signals
        且 previous_signals 非空
        且 旧信号（占比 ≥ decay_threshold 且 importance 均值明显低于当前）→ gradual_transition
        否则 → new_interest_emerging（多兴趣并行，不视为迁移）
  6. 任何异常 → 返回 None，由调用方继续走原 Evaluator 链路（失败隔离不阻塞成长）

估算强度（仅用于 transition from/to 描述，不直接当 Personlity 真值写）：
  strength_estimate(sig, relevant_events) =
      0.2 + min(0.6, count * 0.08) + min(0.2, avg_importance)
"""
from __future__ import annotations

import logging
import statistics
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.growth.interest_transition import (
    InterestTransitionProposal,
    build_interest_transition_proposal,
)

logger = logging.getLogger(__name__)

# 信号占比阈值：旧信号在 previous_signals 里占历史条目 >= 该比例才视为"有基础兴趣"
PREV_SIGNAL_DOMINANCE_RATIO = 0.5

# 判定为迁移的"强度差阈值"：旧 signal 平均 importance 低于当前 至少 这么多
TRANSITION_IMPORTANCE_GAP = 0.1

# 若 history 不够多（总条目 <=1），直接降为 emerging（不判 transition，避免误触发）
MIN_HISTORY_COUNT_FOR_TRANSITION = 2


class TransitionAnalyzer:
    """只判断"这属于什么兴趣转变"；不产生 ChangeItem。"""

    def __init__(
        self,
        *,
        dominance_ratio: float = PREV_SIGNAL_DOMINANCE_RATIO,
        importance_gap: float = TRANSITION_IMPORTANCE_GAP,
        min_history_for_transition: int = MIN_HISTORY_COUNT_FOR_TRANSITION,
    ) -> None:
        self.dominance_ratio = max(0.0, min(1.0, float(dominance_ratio)))
        self.importance_gap = max(0.0, min(1.0, float(importance_gap)))
        self.min_history_for_transition = int(max(1, min_history_for_transition))

    # ============================================================
    # 对外主 API
    # ============================================================
    def analyze(
        self,
        normalized_event: Dict[str, Any],
        history_events: List[Dict[str, Any]],
        evaluator_output: Dict[str, Any],
    ) -> Optional[InterestTransitionProposal]:
        try:
            return self._analyze_safe(normalized_event, history_events, evaluator_output)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[TransitionAnalyzer] analyze 失败(已隔离, 返回 None): %s", exc,
            )
            return None

    # ============================================================
    # 内部实现
    # ============================================================
    def _analyze_safe(
        self,
        normalized_event: Dict[str, Any],
        history_events: List[Dict[str, Any]],
        evaluator_output: Dict[str, Any],
    ) -> Optional[InterestTransitionProposal]:
        current_signal: str = str(evaluator_output.get("growth_signal") or "").strip()
        if not current_signal:
            return None  # 无信号，不做 transition 判断

        # 收集 history 里的非空 growth_signal（可能存在于 normalized 字段或 evaluator_meta 里）
        signal_list: List[str] = []
        importance_list: List[Tuple[str, float]] = []
        for ev in (history_events or []):
            sig = self._read_growth_signal(ev)
            if sig:
                signal_list.append(sig)
                imp = self._read_importance(ev)
                importance_list.append((sig, imp))

        previous_unique = sorted(set(signal_list))
        total_hist = len(signal_list)

        # 当前事件的证据（memory ids）
        cur_evids = self._collect_memory_ids(normalized_event)
        # 历史证据（memory ids）
        hist_evids: List[str] = []
        for ev in (history_events or []):
            hist_evids.extend(self._collect_memory_ids(ev))
        all_evids = list(dict.fromkeys(cur_evids + hist_evids))

        current_importance = self._read_importance(dict(normalized_event, **(evaluator_output or {})))

        # Case 1: reinforce（已经在 previous 里出现过）
        if current_signal in previous_unique:
            strength_current = self._estimate_strength(
                current_signal,
                relevant=[
                    imp for sig, imp in importance_list if sig == current_signal
                ] + [current_importance],
                occurrence_count=sum(1 for s in signal_list if s == current_signal) + 1,
            )
            return build_interest_transition_proposal(
                proposal_mode="reinforce",
                current_growth_signal=current_signal,
                previous_growth_signals=previous_unique,
                from_interest={current_signal: strength_current},  # reinforce 可把 from/to 都写当前
                to_interest={current_signal: strength_current},
                confidence=0.75,
                evidence_memory_ids=all_evids,
                reason_text=(
                    f"reinforce: signal={current_signal} 已在历史出现"
                    f" {sum(1 for s in signal_list if s == current_signal)} 次"
                ),
            )

        # 当前信号不在 previous → emerging 或 transition
        # Case 2: previous_signals 为空 → 全新兴趣 emerging
        if not previous_unique:
            strength_new = self._estimate_strength(
                current_signal, relevant=[current_importance], occurrence_count=1,
            )
            return build_interest_transition_proposal(
                proposal_mode="new_interest_emerging",
                current_growth_signal=current_signal,
                previous_growth_signals=previous_unique,
                to_interest={current_signal: strength_new},
                confidence=0.7,
                evidence_memory_ids=all_evids,
                reason_text=(
                    f"new_interest_emerging: signal={current_signal} 历史无同类成长信号"
                ),
            )

        # Case 3: previous_signals 非空，判 transition 或 emerging
        # — history 条目太少 -> 不判 transition，保守 emerging
        if total_hist < self.min_history_for_transition:
            strength_new = self._estimate_strength(
                current_signal, relevant=[current_importance], occurrence_count=1,
            )
            return build_interest_transition_proposal(
                proposal_mode="new_interest_emerging",
                current_growth_signal=current_signal,
                previous_growth_signals=previous_unique,
                to_interest={current_signal: strength_new},
                confidence=0.65,
                evidence_memory_ids=all_evids,
                reason_text=(
                    f"new_interest_emerging: history={total_hist}<{self.min_history_for_transition}，"
                    f"不判 transition；signal={current_signal}"
                ),
            )

        # — 计算旧信号（主导 previous_unique 中占比最高的一个）的 importance 均值
        dominant_old_signal, old_avg_imp = self._find_dominant_old_signal(
            importance_list, total_hist, signal_list, previous_unique,
        )
        # 判定为 gradual_transition 的条件：
        #   a) 旧 dominant 信号占比 >= dominance_ratio
        #   b) 旧 importance 均值 比 当前 importance 小至少 importance_gap
        old_ratio = (
            sum(1 for s in signal_list if s == dominant_old_signal) / total_hist
        )
        if (
            old_ratio >= self.dominance_ratio
            and current_importance - old_avg_imp >= self.importance_gap
        ):
            strength_old = self._estimate_strength(
                dominant_old_signal,
                relevant=[imp for sig, imp in importance_list if sig == dominant_old_signal],
                occurrence_count=sum(1 for s in signal_list if s == dominant_old_signal),
            )
            strength_new = self._estimate_strength(
                current_signal,
                relevant=[current_importance],
                occurrence_count=1,
            )
            return build_interest_transition_proposal(
                proposal_mode="gradual_transition",
                current_growth_signal=current_signal,
                previous_growth_signals=previous_unique,
                from_interest={dominant_old_signal: strength_old},
                to_interest={current_signal: strength_new},
                confidence=round(min(0.85, 0.6 + (current_importance - old_avg_imp) * 0.5), 3),
                evidence_memory_ids=all_evids,
                reason_text=(
                    f"gradual_transition: {dominant_old_signal}(~{strength_old:.2f})"
                    f" → {current_signal}(~{strength_new:.2f});"
                    f" old_ratio={old_ratio:.2f}, imp_gap={current_importance-old_avg_imp:.2f}"
                ),
            )

        # — 否则：多兴趣并行（不视为迁移）→ new_interest_emerging
        strength_new = self._estimate_strength(
            current_signal, relevant=[current_importance], occurrence_count=1,
        )
        return build_interest_transition_proposal(
            proposal_mode="new_interest_emerging",
            current_growth_signal=current_signal,
            previous_growth_signals=previous_unique,
            to_interest={current_signal: strength_new},
            confidence=0.6,
            evidence_memory_ids=all_evids,
            reason_text=(
                f"new_interest_emerging: previous_signals={previous_unique!r}"
                f" 与 new signal={current_signal} 并存，不视为迁移"
            ),
        )

    # ============================================================
    # 辅助工具
    # ============================================================
    @staticmethod
    def _read_growth_signal(ev: Dict[str, Any]) -> str:
        for key in ("growth_signal", "signal", "category_id"):
            v = ev.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        md = ev.get("evaluator_meta") if isinstance(ev.get("evaluator_meta"), dict) else {}
        for key in ("growth_signal",):
            v = md.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    @staticmethod
    def _read_importance(ev: Dict[str, Any]) -> float:
        for key in ("importance", "avg_importance"):
            v = ev.get(key)
            if isinstance(v, (int, float)):
                return float(v)
        md = ev.get("evaluator_meta") if isinstance(ev.get("evaluator_meta"), dict) else {}
        v = md.get("importance")
        if isinstance(v, (int, float)):
            return float(v)
        return 0.5

    @staticmethod
    def _collect_memory_ids(ev: Dict[str, Any]) -> List[str]:
        out = []
        for k in ("memory_id", "source_memory_id"):
            v = ev.get(k)
            if v:
                out.append(str(v))
        src_ids = ev.get("source_ids")
        if isinstance(src_ids, Iterable):
            for s in src_ids:
                if s:
                    out.append(str(s))
        evidence = ev.get("evidence")
        if isinstance(evidence, list):
            for e in evidence:
                if isinstance(e, dict):
                    m = e.get("memory_id")
                    if m:
                        out.append(str(m))
        return list(dict.fromkeys(out))

    @staticmethod
    def _estimate_strength(
        signal: str,
        *,
        relevant: List[float],
        occurrence_count: int,
    ) -> float:
        avg = float(statistics.mean(relevant)) if relevant else 0.0
        cnt = int(max(0, occurrence_count))
        # 20% 基础 + 8%*count(上限60%) + min(avg*0.2系数, 20%)
        raw = 0.2 + min(0.6, cnt * 0.08) + min(0.2, avg)
        return round(min(max(raw, 0.0), 1.0), 4)

    def _find_dominant_old_signal(
        self,
        importance_list: List[Tuple[str, float]],
        total_hist: int,
        signal_list: List[str],
        previous_unique: List[str],
    ) -> Tuple[str, float]:
        # 1) previous_unique 里出现次数最多的 signal
        counts: Dict[str, int] = {s: sum(1 for x in signal_list if x == s) for s in previous_unique}
        # 2) 选最大的；若并列，取 importance 均值更高的
        best_sig = previous_unique[0]
        best_count = -1
        best_avg = -1.0
        for s in previous_unique:
            c = counts.get(s, 0)
            imps = [imp for sig, imp in importance_list if sig == s]
            try:
                avg = float(statistics.mean(imps)) if imps else 0.0
            except Exception:  # noqa: BLE001
                avg = 0.0
            if c > best_count or (c == best_count and avg > best_avg):
                best_sig = s
                best_count = c
                best_avg = avg
        return best_sig, best_avg
