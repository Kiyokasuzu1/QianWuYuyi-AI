"""
Phase B.1.2 — ProposalStore 完整实现

替换原 skeleton。

设计目标：
- JSONL append-only 存储，文件路径默认 data/proposals/proposals.jsonl
- 内存索引（id -> 最新版本 dict）
- save() 追加 + 索引更新
- update() 追加新版本（latest-wins by id）
- load(id) 返回最新版本
- list(status, limit, offset) 支持按 status 过滤
- exists_similar() 用于重复检测（同 source_event_id + fingerprint）
- 任何 I/O 异常必须被隔离，不影响调用方

关键约束：
- status 字段必须属于 {pending, accepted, rejected, applied}
- 同一 id 的多次写入以最后一次为准（latest-wins）
- 解析失败行必须被跳过，不能让单条脏数据污染整个 store
"""
from __future__ import annotations

import json
import os
import hashlib
import logging
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from src.contracts import growth_schema

logger = logging.getLogger(__name__)

# V1.0: per-path RLock 表（多实例同文件写串行化）
_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path) -> threading.RLock:
    _key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        _lock = _PATH_LOCKS.get(_key)
        if _lock is None:
            _lock = threading.RLock()
            _PATH_LOCKS[_key] = _lock
    return _lock


def _backup_corrupt(path: Path) -> None:
    """损坏文件复制备份（不覆盖不删除旧文件）。"""
    try:
        if path.exists():
            _backup = f"{path}.corrupt.{datetime.now():%Y%m%dT%H%M%S%f}"
            shutil.copy2(str(path), _backup)
            logger.warning("ProposalStore: 损坏文件已备份为 %s", _backup)
    except Exception:
        pass


# 合法 status 集合（与 Phase B.1.2 文档一致）
# P5.0-E #0b: 仅追加 "approved"（R2.5.3 治理状态）。
# 否则 ApprovalManager 写入的 approved 会被 save() 静默改写回 pending，
# 导致下游 EvolutionPipeline 永远拿不到 approved 提案。
# 治理结论（A/B 验证）：deferred / under_review 不进入白名单——
# 旧行为是把它们归一化为 pending，若放行会改变 4.3/4.4 链测试的 pending 语义。
VALID_STATUSES = frozenset({
    "pending", "accepted", "rejected", "applied",
    "approved",
})


class ProposalStore:
    """Append-only JSONL store for GrowthProposal objects.

    Data file (default): data/proposals/proposals.jsonl
    Each line is a JSON-serialized GrowthProposal dict.

    Implementation:
    - Append-only: every save() appends a new line
    - Latest-wins: in-memory index (id -> last written dict)
    - Crash-safe: writes are flushed + fsynced to disk before return
    """

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path or "data/proposals/proposals.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # ensure file exists
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")
        # 写锁（V1.0: 升级为 per-path 跨实例锁）
        self._lock = _path_lock(self.path)
        # 内存索引：id -> latest dict
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load_index()

    # ============================================================
    # 索引加载 / 重建
    # ============================================================
    def _load_index(self) -> None:
        """从文件重建内存索引（latest-wins）。"""
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        # 跳过脏行（不污染整体索引）
                        continue
                    pid = d.get("id")
                    if not pid:
                        continue
                    self._index[pid] = d
        except Exception:
            # I/O 异常：损坏备份后保持空索引（不覆盖旧文件）
            _backup_corrupt(self.path)
            self._index = {}

    # ============================================================
    # save / update
    # ============================================================
    def save(self, proposal: growth_schema.GrowthProposal) -> None:
        """持久化新 proposal（追加一行 + 更新内存索引）。"""
        try:
            d = proposal.to_dict()
        except Exception as e:
            # to_dict 失败（非 dataclass 输入等），降级为最小 dict
            d = {
                "id": getattr(proposal, "id", f"prop_unknown_{id(proposal)}"),
                "status": "pending",
                "_to_dict_error": str(e),
            }
        # 状态合法性校验
        status = d.get("status", "pending")
        if status not in VALID_STATUSES:
            d["status"] = "pending"
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        # fsync 失败不致命（不同平台行为不同）
                        pass
            except Exception:
                # 落盘失败：仍更新内存索引（保证单进程内一致性）
                pass
            self._index[d["id"]] = d

    def update(self, proposal: growth_schema.GrowthProposal) -> None:
        """更新现有 proposal（追加新版本，索引指向最新版）。"""
        # 复用 save：append-only 模型下 update 与 save 行为一致
        self.save(proposal)

    # ============================================================
    # load / list
    # ============================================================
    def load(self, proposal_id: str) -> Optional[growth_schema.GrowthProposal]:
        """加载最新版本 proposal（按 id）。"""
        with self._lock:
            d = self._index.get(proposal_id)
        if d is None:
            return None
        try:
            return growth_schema.GrowthProposal.from_dict(d)
        except Exception:
            return None

    def list(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[growth_schema.GrowthProposal]:
        """列出 proposal，可选 status 过滤。

        返回顺序：按 timestamp 降序（最新在前）。
        """
        with self._lock:
            all_items = list(self._index.values())

        if status is not None:
            all_items = [d for d in all_items if d.get("status") == status]

        # 按 timestamp 降序
        all_items.sort(key=lambda d: d.get("timestamp", ""), reverse=True)

        sliced = all_items[offset : offset + limit]
        result: List[growth_schema.GrowthProposal] = []
        for d in sliced:
            try:
                result.append(growth_schema.GrowthProposal.from_dict(d))
            except Exception:
                continue
        return result

    def count(self, status: Optional[str] = None) -> int:
        """返回总数（可选 status 过滤）。"""
        with self._lock:
            items = list(self._index.values())
        if status is not None:
            return sum(1 for d in items if d.get("status") == status)
        return len(items)

    # ============================================================
    # exists_similar — 重复检测
    # ============================================================
    def exists_similar(
        self,
        source_event_id: str,
        fingerprint: str,
    ) -> Optional[str]:
        """检测重复 proposal（同 source_event_id + fingerprint）。

        Returns:
            已存在 proposal 的 id，若无重复则返回 None。
        """
        with self._lock:
            items = list(self._index.values())
        for d in items:
            if d.get("source_event_id") != source_event_id:
                continue
            # 重建 fingerprint 与历史对比
            existing_fp = compute_fingerprint(d)
            if existing_fp == fingerprint:
                return d.get("id")
        return None


def compute_fingerprint(proposal_dict: Dict[str, Any]) -> str:
    """计算 proposal 的 fingerprint（用于重复检测）。

    基于 source_event_id + proposed_changes 的 path+delta 序列。
    """
    parts = [str(proposal_dict.get("source_event_id") or "")]
    changes = proposal_dict.get("proposed_changes") or []
    for ci in changes:
        if not isinstance(ci, dict):
            continue
        path = ci.get("path", "")
        before = ci.get("before")
        after = ci.get("after")
        try:
            delta = round(float(after) - float(before), 4) if (after is not None and before is not None) else 0.0
        except Exception:
            delta = 0.0
        parts.append(f"{path}:{delta}")
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
