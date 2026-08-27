# -*- coding: utf-8 -*-
"""
src/goal/goal_pattern_detector.py

v1.3 Agency Phase 3: Evidence Pattern Detector(最小确定性检测器)。

职责(只做这一件事):
    Evidence(Memory / ExperienceJournal 记录)
        ↓ 过滤禁止源 + 来源校验
    Pattern(同主题聚类, 跨时间窗口合并)
        ↓ ≥ min_evidence 个独立来源
    GoalCandidate(append-only 持久化)

不做(红线):
- 不创建 Proposal / 不审批 / 不激活 Goal(由 bridge + admin + drain 负责);
- 不调用 LLM(纯确定性字符相似度, 无任何 LLM import);
- 不读取 personality / self_model / emotion / relationship 状态;
- 不接受 SelfNarrative / Reflection 文本作为证据(fail-closed);
- 不写 GoalState / 不修改任何其他域状态。

数据源约束(Phase 3 审计):
- Memory(第一手): content / id / timestamp / importance
- ExperienceJournal(第一手行为): content / id / timestamp
- GrowthHistory(辅助, 治理来源): 本阶段未接入, 后续在 detector 外扩展
  且仅允许白名单字段(record_id/evidence_ids/affected_dimensions/meaning/
  confidence/source_growth_record_id), 禁止 narrative 字段。

Candidate 生命周期:
    GoalCandidate(append-only) → bridge → GoalProposal(PENDING)
    → Admin Review → GoalDrain → GoalState(active)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

DETECTOR_VERSION = "goal_pattern_detector.1.0"
DEFAULT_CANDIDATE_PATH = "data/goal/goal_candidates.jsonl"
MAX_CANDIDATE_BYTES = 32 * 1024 * 1024  # 容量保护(与 journal 同惯例)

MIN_EVIDENCE = 2
DEFAULT_SIMILARITY_THRESHOLD = 0.25
# v1.3 RC 7.1: 候选证据引用上限(不改变聚类算法, 仅限制存储进候选的引用数)
MAX_EVIDENCE_REFS_PER_CANDIDATE = 32

# fail-closed: 这些来源类型一律不作为证据(叙事/反思文本)
FORBIDDEN_SOURCE_TYPES = frozenset({
    "self_narrative",
    "narrative",
    "reflection",
    "self_reflection",
})

# 记录侧类型名 → 证据 source_type 归一(ExperienceJournal 用 metadata.type)
_SOURCE_TYPE_ALIASES = {
    "runtime_experience": "experience",
}

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Union[str, Path]) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# GoalCandidateStore(append-only JSONL)
# ============================================================
class GoalCandidateStore:
    """GoalCandidate append-only 存储(与 GoalStateStore / journal 同惯例)。

    - append-only: 只追加不覆盖不删除;
    - fingerprint 幂等: 同一组证据不重复落盘;
    - fail-soft: 任何失败不抛出; 损坏行读取隔离;
    - 容量保护: 超限单轮转(.1 后缀)。
    """

    def __init__(self, path: str = DEFAULT_CANDIDATE_PATH) -> None:
        self.path = Path(path).resolve()

    def append_candidate(self, candidate: Dict[str, Any]) -> bool:
        """追加一条 candidate; 已存在同 fingerprint → False(不重复写)。"""
        if not isinstance(candidate, dict):
            return False
        _fp = str(candidate.get("fingerprint", "") or "")
        try:
            with _path_lock(self.path):
                if _fp and self.fingerprint_exists(_fp):
                    return False
                line = json.dumps(candidate, ensure_ascii=False, default=str)
                self._rotate_if_needed()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.write("\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GoalCandidateStore] append 失败(已隔离): %s", exc)
            return False

    def _rotate_if_needed(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_CANDIDATE_BYTES:
                _rotated = str(self.path) + ".1"
                if os.path.exists(_rotated):
                    os.remove(_rotated)
                os.replace(str(self.path), _rotated)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GoalCandidateStore] rotate 失败(已隔离): %s", exc)

    def list_all(self) -> List[Dict[str, Any]]:
        """读取全部有效 candidate(append 顺序); 损坏行跳过; 异常返回空。"""
        out: List[Dict[str, Any]] = []
        try:
            with _path_lock(self.path):
                if not self.path.exists():
                    return out
                with open(self.path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GoalCandidateStore] 读取失败(已隔离): %s", exc)
            return out
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def fingerprint_exists(self, fingerprint: str) -> bool:
        if not fingerprint:
            return False
        try:
            return any(
                str(c.get("fingerprint", "") or "") == fingerprint
                for c in self.list_all()
            )
        except Exception:  # noqa: BLE001
            return False


# ============================================================
# 确定性相似度(无 LLM)
# ============================================================
def _char_set(text: str) -> set:
    return set(text)


def _char_bigrams(text: str) -> set:
    if len(text) < 2:
        return set(text)
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _similarity(a: str, b: str) -> float:
    """字符集 Jaccard 与字符 bigram Jaccard 的均值(确定性主题近似)。

    中文短文本的主题词主导字符重叠; 随机句对重叠极低, 误合并风险可控。
    """
    _a = str(a or "").strip()
    _b = str(b or "").strip()
    if not _a or not _b:
        return 0.0
    _sa, _sb = _char_set(_a), _char_set(_b)
    _ga, _gb = _char_bigrams(_a), _char_bigrams(_b)

    def _jaccard(x: set, y: set) -> float:
        _inter = len(x & y)
        _denom = max(1, min(len(x), len(y)))
        return _inter / _denom

    return 0.5 * _jaccard(_sa, _sb) + 0.5 * _jaccard(_ga, _gb)


# ============================================================
# Evidence 归一化(fail-closed)
# ============================================================
def _source_type_of(record: Dict[str, Any]) -> str:
    _st = str(record.get("source_type", "") or "").strip().lower()
    if _st:
        return _st
    _meta = record.get("metadata")
    if isinstance(_meta, dict):
        return str(_meta.get("type", "") or "").strip().lower()
    return ""


def _normalize_evidence(
    record: Any,
    default_source_type: str,
) -> Optional[Dict[str, Any]]:
    """把一条输入记录归一化为证据; 禁止源/缺来源 → None(fail-closed)。"""
    if not isinstance(record, dict):
        return None
    _st = _source_type_of(record) or default_source_type
    _st = _SOURCE_TYPE_ALIASES.get(_st, _st)
    if _st in FORBIDDEN_SOURCE_TYPES:
        logger.warning(
            "[GoalPatternDetector] 丢弃禁止来源证据: %s", _st,
        )
        return None
    _sid = str(record.get("source_id", "") or record.get("id", "") or "").strip()
    _content = str(record.get("content", "") or "").strip()
    if not _sid or not _content:
        return None
    try:
        _importance = round(float(record.get("importance", 0.5) or 0.5), 6)
    except (TypeError, ValueError):
        _importance = 0.5
    return {
        "source_type": _st,
        "source_id": _sid,
        "content": _content,
        "timestamp": str(record.get("timestamp", "") or ""),
        "importance": _importance,
    }


# ============================================================
# 证据质量统计(供 runner 指标, 只分类不写)
# ============================================================
def analyze_evidence(
    memories: Optional[List[Any]] = None,
    experience_records: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """按与 detect 相同的规则分类证据, 返回统计(用于指标观察)。"""
    out: Dict[str, Any] = {
        "valid_count": 0,
        "forbidden_count": 0,
        "no_source_count": 0,
        "by_type": {},
    }

    def _classify(record: Any, default_type: str) -> None:
        if not isinstance(record, dict):
            out["no_source_count"] += 1
            return
        _st = _source_type_of(record) or default_type
        _st = _SOURCE_TYPE_ALIASES.get(_st, _st)
        if _st in FORBIDDEN_SOURCE_TYPES:
            out["forbidden_count"] += 1
            return
        _sid = str(record.get("source_id", "") or record.get("id", "") or "").strip()
        _content = str(record.get("content", "") or "").strip()
        if not _sid or not _content:
            out["no_source_count"] += 1
            return
        out["valid_count"] += 1
        out["by_type"][_st] = int(out["by_type"].get(_st, 0)) + 1

    for _m in memories or []:
        _classify(_m, "memory")
    for _r in experience_records or []:
        _classify(_r, "experience")
    return out


# ============================================================
# detect_candidates
# ============================================================
def detect_candidates(
    memories: Optional[List[Any]] = None,
    experience_records: Optional[List[Any]] = None,
    *,
    candidate_store: Optional[GoalCandidateStore] = None,
    min_evidence: int = MIN_EVIDENCE,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    detector_version: str = DETECTOR_VERSION,
) -> List[Dict[str, Any]]:
    """Evidence → Pattern → Candidate(纯确定性, 无 LLM, 无状态写入)。

    - memories: Memory 记录列表(第一手证据)
    - experience_records: ExperienceJournal 记录列表(第一手行为证据)
    - candidate_store: 提供时 append-only 持久化(fingerprint 幂等, 不覆盖历史)
    - 返回: 本轮流出的 candidate dict 列表(与 store 内容同构)
    """
    try:
        _min = max(2, int(min_evidence or MIN_EVIDENCE))
    except (TypeError, ValueError):
        _min = MIN_EVIDENCE
    try:
        _threshold = min(1.0, max(0.0, float(similarity_threshold)))
    except (TypeError, ValueError):
        _threshold = DEFAULT_SIMILARITY_THRESHOLD

    evidence: List[Dict[str, Any]] = []
    for _m in memories or []:
        _e = _normalize_evidence(_m, "memory")
        if _e is not None:
            evidence.append(_e)
    for _r in experience_records or []:
        _e = _normalize_evidence(_r, "experience")
        if _e is not None:
            evidence.append(_e)

    # 同 (source_type, source_id) 只计一次
    _seen: set = set()
    _deduped: List[Dict[str, Any]] = []
    for _e in evidence:
        _key = (_e["source_type"], _e["source_id"])
        if _key in _seen:
            continue
        _seen.add(_key)
        _deduped.append(_e)

    # 贪婪聚类(连接分量): 与簇内任一证据相似度达标 → 同主题合并
    clusters: List[List[Dict[str, Any]]] = []
    for _e in _deduped:
        _placed = False
        for _c in clusters:
            if any(
                _similarity(_e["content"], _x["content"]) >= _threshold
                for _x in _c
            ):
                _c.append(_e)
                _placed = True
                break
        if not _placed:
            clusters.append([_e])

    candidates: List[Dict[str, Any]] = []
    for _cluster in clusters:
        _distinct = {(e["source_type"], e["source_id"]) for e in _cluster}
        if len(_distinct) < _min:
            continue  # Test A: 单条(或不足)证据不产生 Candidate

        _refs = sorted(
            _cluster, key=lambda e: (str(e.get("timestamp", "") or ""), str(e["source_id"]))
        )
        _total_refs = len(_refs)
        _stored_refs = _refs[:MAX_EVIDENCE_REFS_PER_CANDIDATE]
        if _total_refs > MAX_EVIDENCE_REFS_PER_CANDIDATE:
            logger.warning(
                "[GoalPatternDetector] 候选证据超上限(%d -> %d 截断, 总数保留, 聚类算法不变)",
                _total_refs, MAX_EVIDENCE_REFS_PER_CANDIDATE,
            )
        _description = _pick_description(_refs)
        _confidence = _compute_confidence(_refs, _min)
        _fp = _fingerprint_of(_refs)
        candidate = {
            "id": f"gc_{_fp[:12]}",
            "description": _description,
            "evidence_refs": [
                {
                    "source_type": e["source_type"],
                    "source_id": e["source_id"],
                    "timestamp": e["timestamp"],
                }
                for e in _stored_refs
            ],
            "evidence_total_count": _total_refs,
            "confidence": _confidence,
            "created_at": _utc_now_iso(),
            "detector_version": str(detector_version or DETECTOR_VERSION),
            "fingerprint": _fp,
        }
        if candidate_store is not None:
            try:
                candidate_store.append_candidate(candidate)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[GoalPatternDetector] 持久化失败(已隔离): %s", exc)
        candidates.append(candidate)

    return candidates


def _pick_description(refs: List[Dict[str, Any]]) -> str:
    """确定性描述: importance 最高(平手取更长)的证据内容, 截断 160 字符。"""
    _best = max(
        refs,
        key=lambda e: (
            float(e.get("importance", 0.0) or 0.0),
            len(str(e.get("content", "") or "")),
        ),
    )
    _text = str(_best.get("content", "") or "").strip()
    if len(_text) > 160:
        _text = _text[:160]
    return _text


def _compute_confidence(refs: List[Dict[str, Any]], min_evidence: int) -> float:
    """确定性置信度(0.5 起, 封顶 0.8): 证据数 / 跨时间窗口 / 含行为证据加分。"""
    _distinct = {(e["source_type"], e["source_id"]) for e in refs}
    _extra = max(0, len(_distinct) - int(min_evidence))
    _days = {
        str(e.get("timestamp", "") or "")[:10]
        for e in refs
        if str(e.get("timestamp", "") or "")[:10]
    }
    _has_experience = any(e["source_type"] == "experience" for e in refs)
    _score = 0.5 + 0.05 * min(_extra, 3)
    if len(_days) >= 2:
        _score += 0.05
    if _has_experience:
        _score += 0.05
    return round(min(0.8, max(0.5, _score)), 6)


def _fingerprint_of(refs: List[Dict[str, Any]]) -> str:
    """证据组指纹(幂等键): 排序后的 type:id 列表 sha1。"""
    _keys = sorted(f"{e['source_type']}:{e['source_id']}" for e in refs)
    return hashlib.sha1("|".join(_keys).encode("utf-8")).hexdigest()[:16]


__all__ = [
    "DETECTOR_VERSION",
    "DEFAULT_CANDIDATE_PATH",
    "MIN_EVIDENCE",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "FORBIDDEN_SOURCE_TYPES",
    "GoalCandidateStore",
    "analyze_evidence",
    "detect_candidates",
]
