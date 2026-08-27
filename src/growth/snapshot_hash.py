# -*- coding: utf-8 -*-
"""Snapshot Hash 独立纯函数（T1-B，约束 2）。

- 确定性：同输入（path + old_value + source）→ 同 hash
- canonical JSON：sort_keys + 紧凑分隔符 + UTF-8，键序/空白无关
- captured_at / provenance / schema_version 不参与 hash（元数据 ≠ 状态内容）
- 供 T1-E Replay 复用（重冻结 → 同 hash = 确定性证明）
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def calculate_snapshot_hash(path: str, old_value: Any, source: str) -> str:
    """状态内容指纹：sha256:hex（64 位）。

    old_value 保持原类型（int/float/str/bool），不做类型转换——
    转换会破坏同一状态的 hash 稳定性。
    """
    payload = {
        "path": str(path),
        "old_value": old_value,
        "source": str(source),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def verify_snapshot_hash(path: str, old_value: Any, source: str, expected: str) -> bool:
    """校验快照 hash 未被篡改。"""
    return calculate_snapshot_hash(path, old_value, source) == expected
