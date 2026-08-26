# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段5:RelationshipProposalStore —— 关系核心提案的 append-only 存储。

路径约定:data/relationship_core/relationship_proposals.jsonl

约束(镜像 src/growth/proposal_store.py 治理模式,不复制其结构):
- append-only:save() 追加新行,同一 proposal_id 以最新一次为准(latest-wins);
- 坏行跳过;整体损坏备份 *.corrupt.* 后降级;
- 文件不存在 → 空列表;
- 任何 I/O 异常隔离,不向调用方抛出;
- 本 store 只承载「候选→审核」的治理记录,绝不自动改变 proposal 状态。

SYSTEM_C_RELATIONSHIP_PROPOSALS = LEGACY_FROZEN（2026-08-27 P0 冻结）
  - 历史数据保留（111 条 append-only，不删除、不迁移、不清空）
  - 不再新增（handlers.py 已停止候选桥订阅）
  - 不自动激活（routes.py activate 端点 410；canonical 唯一写路径 = gov confirm）
  - 不作为 canonical source（RelationshipCore 7 条 confirmed 为唯一真源）
  - 重新启用需重新经过治理架构设计
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.relationship.relationship_proposal import RelationshipProposal

logger = logging.getLogger(__name__)

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: str) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
    return lock


def _backup_corrupt(path: str) -> None:
    try:
        if os.path.exists(path):
            backup = f"{path}.corrupt.{datetime.now():%Y%m%dT%H%M%S%f}"
            shutil.copy2(path, backup)
            logger.warning("RelationshipProposalStore: 损坏文件已备份为 %s", backup)
    except Exception:  # noqa: BLE001
        pass


class RelationshipProposalStore:
    """RelationshipProposal 的 append-only JSONL 存储(latest-wins by proposal_id)。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or "data/relationship_core/relationship_proposals.jsonl"
        self._lock = _path_lock(self.path)
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load_index()

    def _load_index(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw_lines = f.readlines()
        except Exception:  # noqa: BLE001
            _backup_corrupt(self.path)
            self._index = {}
            return
        valid = 0
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(parsed, dict) and parsed.get("proposal_id"):
                self._index[str(parsed["proposal_id"])] = parsed
                valid += 1
        if raw_lines and valid == 0:
            _backup_corrupt(self.path)

    def save(self, proposal: Any) -> bool:
        """追加保存(latest-wins)。输入为 RelationshipProposal 或 dict。"""
        if isinstance(proposal, RelationshipProposal):
            data = proposal.to_dict()
        elif isinstance(proposal, dict):
            data = dict(proposal)
        else:
            return False
        proposal_id = str(data.get("proposal_id") or "")
        if not proposal_id:
            return False
        with self._lock:
            try:
                parent = os.path.dirname(self.path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:  # noqa: BLE001
                        pass
                self._index[proposal_id] = data
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("RelationshipProposalStore.save 失败(已隔离): %s", exc)
                return False

    def get(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._index.get(str(proposal_id))
        return dict(data) if data else None

    def list(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._index.values())
        if status is not None:
            items = [d for d in items if d.get("status") == status]
        items.sort(key=lambda d: str(d.get("updated_at") or ""), reverse=True)
        return [dict(d) for d in items[offset:offset + limit]]


__all__ = ["RelationshipProposalStore"]
