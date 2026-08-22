# -*- coding: utf-8 -*-
"""
src/goal/goal_production_runner.py

v1.3 Agency Phase 4: GoalProductionRunner(生产受控接线, OFF → SHADOW → ACTIVE)。

职责:
- mode=off: 零触碰(不读数据源、不写任何文件) — 与 v1.2.1 完全等价;
- mode=shadow: 调 detector → Candidate append-only 持久化(不桥接 Proposal);
- mode=active: shadow + 桥接新 Candidate → GoalProposal(PENDING, 不自动审批)。

红线:
- 不自动审批 / 不写 GoalState / 不触碰人格四域;
- 不新建调度器/线程(由既有后台宿主 tick 调用);
- 不调用 LLM;
- 幂等三层: ① 证据集 fingerprint(store 幂等, 不重复落盘)
  ② 提案库已有 candidate_id 去重(不重复桥接)
  ③ 单轮桥接上限。

指标(metrics, append-only JSONL, fail-soft):
  mode / ran / memory_count / experience_count / valid_evidence_count /
  dropped_forbidden / dropped_no_source / evidence_distribution /
  detected_candidate_count / new_candidate_count / duplicate_count /
  duplicate_rate / bridged_proposal_count / skipped_existing_count /
  detector_version / timestamp
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

GOAL_DETECTION_MODES = ("off", "shadow", "active")
DEFAULT_MODE = "off"
DEFAULT_METRICS_PATH = "data/goal/goal_detection_metrics.jsonl"
DEFAULT_CANDIDATE_PATH = "data/goal/goal_candidates.jsonl"
MAX_CANDIDATES_PER_RUN = 10

_METRICS_LOCKS: Dict[str, threading.RLock] = {}
_METRICS_LOCKS_GUARD = threading.Lock()


def _metrics_lock(path: Union[str, Path]) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _METRICS_LOCKS_GUARD:
        lock = _METRICS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _METRICS_LOCKS[key] = lock
        return lock


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_memory_loader() -> Callable[[], List[Dict[str, Any]]]:
    def _load() -> List[Dict[str, Any]]:
        try:
            from src.memory.memory_store import MemoryStore

            return list(MemoryStore("data/memory.json").load() or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GoalProductionRunner] 默认记忆加载失败(已隔离): %s", exc)
            return []

    return _load


def _default_experience_loader() -> Callable[[], List[Dict[str, Any]]]:
    def _load() -> List[Dict[str, Any]]:
        try:
            from src.runtime.experience_journal import ExperienceJournal

            return list(ExperienceJournal().get_recent(200) or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GoalProductionRunner] 默认经验加载失败(已隔离): %s", exc)
            return []

    return _load


class GoalProductionRunner:
    """Goal 模式检测生产执行器(受控接线; 默认 off = 零触碰)。"""

    def __init__(
        self,
        *,
        mode: str = DEFAULT_MODE,
        candidate_store: Optional[Any] = None,
        proposal_storage: Optional[Any] = None,
        memory_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        experience_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        metrics_path: Optional[str] = None,
        detector_version: Optional[str] = None,
        max_candidates_per_run: int = MAX_CANDIDATES_PER_RUN,
    ) -> None:
        self.mode = str(mode or DEFAULT_MODE)
        if self.mode not in GOAL_DETECTION_MODES:
            logger.warning(
                "[GoalProductionRunner] 未知 mode=%r, 降级为 off", self.mode,
            )
            self.mode = DEFAULT_MODE
        self._candidate_store = candidate_store
        self._proposal_storage = proposal_storage
        self._memory_loader = memory_loader
        self._experience_loader = experience_loader
        self._detector_version = detector_version
        self._metrics_path = Path(metrics_path or DEFAULT_METRICS_PATH).resolve()
        try:
            self._max_per_run = max(0, int(max_candidates_per_run))
        except (TypeError, ValueError):
            self._max_per_run = MAX_CANDIDATES_PER_RUN
        self.last_metrics: Dict[str, Any] = {"mode": self.mode, "ran": False}

    # --------------------------------------------------------
    # 执行
    # --------------------------------------------------------
    def run_once(self) -> Dict[str, Any]:
        """执行一轮检测(由后台宿主 tick 调用; 无 LLM; fail-soft)。

        off → 零触碰返回; 任何异常 → 记录并返回 ran=False(不抛出)。
        """
        if self.mode == "off":
            _metrics: Dict[str, Any] = {"mode": "off", "ran": False}
            self.last_metrics = _metrics
            return _metrics

        try:
            from src.goal.goal_pattern_detector import (
                analyze_evidence,
                detect_candidates,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GoalProductionRunner] detector 不可用(已隔离): %s", exc)
            return self._finalize({
                "mode": self.mode,
                "ran": False,
                "error": f"detector_unavailable:{type(exc).__name__}",
            })

        try:
            _mem_loader = self._memory_loader or _default_memory_loader()
            _exp_loader = self._experience_loader or _default_experience_loader()
            memories = list(_mem_loader() or [])
            experiences = list(_exp_loader() or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GoalProductionRunner] 数据源加载失败(已隔离): %s", exc)
            return self._finalize({
                "mode": self.mode,
                "ran": False,
                "error": f"loader_failed:{type(exc).__name__}",
            })

        _stats = analyze_evidence(
            memories=memories, experience_records=experiences,
        )

        # 检测前快照(用于 new/duplicate 切分)
        _pre_fingerprints: set = set()
        if self._candidate_store is not None:
            try:
                _pre_fingerprints = {
                    str(c.get("fingerprint", "") or "")
                    for c in self._candidate_store.list_all()
                }
            except Exception:  # noqa: BLE001
                _pre_fingerprints = set()

        try:
            candidates = detect_candidates(
                memories=memories,
                experience_records=experiences,
                candidate_store=self._candidate_store,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GoalProductionRunner] 检测失败(已隔离): %s", exc)
            return self._finalize({
                "mode": self.mode,
                "ran": True,
                "error": f"detect_failed:{type(exc).__name__}",
                **self._base_metrics(memories, experiences, _stats),
            })

        _new = [
            c for c in candidates
            if str(c.get("fingerprint", "") or "") not in _pre_fingerprints
        ]
        _duplicate = len(candidates) - len(_new)

        bridged = 0
        skipped_existing = 0
        if self.mode == "active":
            # RC 3.6 (F1): 候选去重与提案去重分离。
            # - 候选去重 = 候选库 fingerprint(上面 _new/_duplicate, 保持不变);
            # - 提案去重 = 提案库 candidate_id(下方 _existing_candidate_ids);
            # 桥接源 = detect 返回的全部候选(含 shadow 期已持久化候选)——
            #   "Candidate 已存在" ≠ "已生成 Proposal"。
            _existing_candidate_ids = self._existing_bridged_candidate_ids()
            for _c in candidates:
                if bridged >= self._max_per_run:
                    break
                _cid = str(_c.get("id", "") or "")
                if not _cid:
                    continue
                if _cid in _existing_candidate_ids:
                    skipped_existing += 1
                    continue
                try:
                    from src.goal.goal_candidate_bridge import (
                        bridge_candidate_to_proposal,
                    )

                    _proposal = bridge_candidate_to_proposal(
                        _c, storage=self._proposal_storage,
                    )
                    if _proposal is not None:
                        bridged += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[GoalProductionRunner] 桥接失败(已隔离) candidate=%s: %s",
                        _cid, exc,
                    )

        _total_detected = len(candidates)
        _rate = 0.0
        if _total_detected > 0:
            _rate = round(_duplicate / _total_detected, 6)

        return self._finalize({
            "mode": self.mode,
            "ran": True,
            **self._base_metrics(memories, experiences, _stats),
            "detected_candidate_count": _total_detected,
            "new_candidate_count": len(_new),
            "duplicate_count": _duplicate,
            "duplicate_rate": _rate,
            "bridged_proposal_count": bridged,
            "skipped_existing_count": skipped_existing,
            "detector_version": str(self._detector_version or ""),
        })

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    @staticmethod
    def _base_metrics(
        memories: List[Any],
        experiences: List[Any],
        stats: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "memory_count": len(memories),
            "experience_count": len(experiences),
            "valid_evidence_count": int(stats.get("valid_count", 0) or 0),
            "dropped_forbidden": int(stats.get("forbidden_count", 0) or 0),
            "dropped_no_source": int(stats.get("no_source_count", 0) or 0),
            "evidence_distribution": dict(stats.get("by_type", {}) or {}),
        }

    def _existing_bridged_candidate_ids(self) -> set:
        """提案库中已桥接的 candidate_id 集合(幂等②: 不重复桥接)。"""
        _ids: set = set()
        try:
            if self._proposal_storage is None:
                return _ids
            from src.growth.proposal.constants import PROPOSAL_TYPE

            for _p in self._proposal_storage.list_by_type(
                PROPOSAL_TYPE["GOAL"], limit=1000,
            ) or []:
                _meta = getattr(_p, "metadata", None) or {}
                if isinstance(_meta, dict):
                    _cid = str(_meta.get("candidate_id", "") or "")
                    if _cid:
                        _ids.add(_cid)
        except Exception:  # noqa: BLE001
            pass
        return _ids

    def _finalize(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        metrics.setdefault("timestamp", _utc_now_iso())
        metrics["metrics_path"] = str(self._metrics_path)
        self.last_metrics = metrics
        if metrics.get("ran"):
            self._append_metrics(metrics)
        return metrics

    def _append_metrics(self, metrics: Dict[str, Any]) -> None:
        """指标 append-only 落盘(fail-soft, 永不抛出)。"""
        try:
            line = json.dumps(metrics, ensure_ascii=False, default=str)
            with _metrics_lock(self._metrics_path):
                self._metrics_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._metrics_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.write("\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GoalProductionRunner] 指标落盘失败(已隔离): %s", exc)


__all__ = [
    "GOAL_DETECTION_MODES",
    "DEFAULT_MODE",
    "DEFAULT_METRICS_PATH",
    "MAX_CANDIDATES_PER_RUN",
    "GoalProductionRunner",
]
