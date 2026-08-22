# -*- coding: utf-8 -*-
"""
src/governance/state_mutation_audit.py

Phase G-0 Governance Freeze: 统一 state mutation 审计。

record_state_mutation(...) — append-only JSONL 审计:
- 原子追加 (per-path RLock + flush + fsync);
- 失败不阻断业务 (永不抛异常, 返回 False);
- 记录风格复用 src/contracts/audit_schema.py 的 AuditEntry 语义
  (id / component / actor / before / after / timestamp / version),
  并追加治理要素 target / proposal_id / approval_id;
- 读取: 逐行解析, 损坏行隔离 (跳过 + 保留其余)。

并发惯例与 src/audit/storage.py、src/runtime/lifecycle/audit_writer.py 一致。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.contracts.audit_schema import now_iso

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_PATH = "data/audit/state_mutations.jsonl"
SCHEMA_VERSION = "governance.1.0"

# 按路径写锁(进程内); 跨进程追加不做强保证, 审计属尽力而为日志
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


def _resolve_path(path: Optional[Union[str, Path]]) -> Path:
    """审计落点解析: 显式 path > 环境变量(测试隔离) > 默认路径。"""
    if path:
        return Path(path)
    env_path = os.environ.get("YUYI_STATE_MUTATION_AUDIT_PATH")
    if env_path:
        return Path(env_path)
    return Path(DEFAULT_AUDIT_PATH)


def record_state_mutation(
    component: str,
    target: str,
    before: Any,
    after: Any,
    proposal_id: str,
    approval_id: str,
    actor: str,
    *,
    path: Optional[Union[str, Path]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """记录一条 state mutation 审计。成功返回 True; 任何失败返回 False(不抛)。

    G-1.3.4: extra 承载附加审批上下文（reviewer_id / decision 等），
    合并进条目；核心字段（proposal_id/approval_id/actor 等）不可被覆盖。

    供 G-1/G-2 的合法写入口在 apply 前后调用;
    审计不可用绝不能拖垮业务(调用方可忽略返回值)。
    """
    entry: Dict[str, Any] = {
        "id": f"sm_{uuid.uuid4().hex[:12]}",
        "component": component,
        "target": target,
        "before": before,
        "after": after,
        "proposal_id": proposal_id,
        "approval_id": approval_id,
        "actor": actor,
        "timestamp": now_iso(),
        "version": SCHEMA_VERSION,
    }
    if isinstance(extra, dict):
        for _key, _value in extra.items():
            if _key not in entry:
                entry[_key] = _value
    return append_entry(entry, path=path)


def append_entry(entry: Dict[str, Any], *, path: Optional[Union[str, Path]] = None) -> bool:
    """追加一条 dict 形态的审计条目(便利入口)。成功 True / 失败 False, 不抛。"""
    target_path = _resolve_path(path)
    if not isinstance(entry, dict):
        return False
    try:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _path_lock(target_path):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[StateMutationAudit] 审计写入失败(已隔离): %s", exc)
        return False


def read_entries(
    limit: int = 100,
    *,
    path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """从尾部读取最近 N 条有效审计(损坏行隔离, 最新在前)。"""
    target_path = _resolve_path(path)
    out: List[Dict[str, Any]] = []
    try:
        cap = max(1, int(limit or 100))
    except Exception:  # noqa: BLE001
        cap = 100
    try:
        with _path_lock(target_path):
            if not target_path.exists():
                return out
            with open(target_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
    except Exception:  # noqa: BLE001
        return out
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(item, dict):
            out.append(item)
            if len(out) >= cap:
                break
    return out


__all__ = [
    "DEFAULT_AUDIT_PATH",
    "SCHEMA_VERSION",
    "record_state_mutation",
    "append_entry",
    "read_entries",
]
