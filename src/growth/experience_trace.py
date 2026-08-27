# -*- coding: utf-8 -*-
"""Experience Trace（T1-A）—— 经历事实账本（append-only）。

架构约束（主架构师审核）：
1. Trace 必须早于 Proposal：Memory → Experience 生成 → Trace 固化 → Proposal 引用
   （Proposal 是推论，Experience 是事实，事实必须先存在）
2. Trace 只保存事实证据（memory_id/时间/内容摘要），禁止任何人格解释——
   意义属于 Growth 层，不属事实层
3. MC 与 QQ 同池：source_type 区分（mc_events / runtime_pipeline），同权进入成长链

结构（schema_version=1）：
  experience_id / source_memory_ids / source_type / created_at / summary /
  detector / provenance
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_TRACE_PATH = os.path.join("data", "growth", "experience_trace.jsonl")

# 禁止字段（约束 2：事实层无解释）
FORBIDDEN_FIELDS = ("meaning", "interpretation", "explanation", "significance")

_TRACE_ID_RE = re.compile(r"^exp_[A-Za-z0-9]+$")


def new_experience_id() -> str:
    return f"exp_{uuid.uuid4().hex[:12]}"


def make_trace(experience_id: str, source_memory_ids: List[str], source_type: str,
               summary: str, detector: str = "", provenance: str = "system_rule") -> Optional[dict]:
    """构造 trace 记录（事实层：无任何人格解释字段）。

    返回 None 表示输入非法（不抛）。
    """
    if not experience_id or not _TRACE_ID_RE.match(experience_id):
        logger.warning("[ExpTrace] experience_id 非法: %r", experience_id)
        return None
    mem_ids = [str(m) for m in (source_memory_ids or []) if str(m)]
    if not mem_ids:
        logger.warning("[ExpTrace] 无 source_memory_ids（空证据不得固化）")
        return None
    if not summary or len(summary) > 200:
        logger.warning("[ExpTrace] summary 缺失或超长")
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "experience_id": experience_id,
        "source_memory_ids": mem_ids,
        "source_type": source_type or "runtime_pipeline",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "detector": detector,
        "provenance": provenance,
    }


def append_trace(trace: dict, path: str = DEFAULT_TRACE_PATH) -> bool:
    """append-only 固化。同 experience_id 已存在 → 幂等拒绝（不重复写）。"""
    if not trace:
        return False
    try:
        if exists(trace["experience_id"], path):
            return False
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(trace, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ExpTrace] append 失败（已隔离）: %s", exc)
        return False


def exists(experience_id: str, path: str = DEFAULT_TRACE_PATH) -> bool:
    """同 experience_id 是否已固化（幂等）。"""
    try:
        if not os.path.exists(path):
            return False
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    if json.loads(line).get("experience_id") == experience_id:
                        return True
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        pass
    return False


def load_traces(path: str = DEFAULT_TRACE_PATH) -> List[dict]:
    """读取全部 trace（只读）。"""
    out = []
    try:
        if not os.path.exists(path):
            return out
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ExpTrace] load 失败（已隔离）: %s", exc)
    return out


def resolve_trace_ids(ids: List[str], path: str = DEFAULT_TRACE_PATH) -> dict:
    """解析 evidence_trace_ids → 存在的集合。返回 {id: trace 或 None}。"""
    traces = {t.get("experience_id"): t for t in load_traces(path)}
    return {i: traces.get(i) for i in (ids or [])}
