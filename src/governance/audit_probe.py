# -*- coding: utf-8 -*-
"""
P2.6 Phase A — Governance Audit Probe（治理审计探针，只读）

职责（只观察，不干预）：
- 观察旧链人格变化路径（legacy growth apply / legacy self-model policy 决策）
- 将观察结果以 JSONL append-only 写入 data/governance_audit/

硬约束：
- 只增加记录，不改变任何返回值、不修改任何已有写入逻辑
- 写失败必须静默吞掉（audit failure = swallow exception），绝不影响主流程
- 零业务依赖（仅标准库），业务模块通过函数内延迟导入接入，避免循环导入
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from typing import Any, Dict, Optional

_DEFAULT_AUDIT_DIR = os.path.join("data", "governance_audit")
_AUDIT_FILE_NAME = "governance_audit_probe.jsonl"


class GovernanceAuditProbe:
    """旧链写入观察探针。

    记录字段（P2.6 Phase A 任务书 §2）：
        timestamp / source_path / domain / mutation_type / decision
        / payload_summary / triggered_by / request_id
    """

    def __init__(self, audit_dir: Optional[str] = None) -> None:
        self._audit_dir = audit_dir or _DEFAULT_AUDIT_DIR
        self._lock = threading.Lock()

    @property
    def audit_dir(self) -> str:
        return self._audit_dir

    def record(
        self,
        *,
        source_path: str,
        domain: str,
        mutation_type: str,
        decision: str,
        payload_summary: Optional[Dict[str, Any]] = None,
        triggered_by: str = "",
        request_id: str = "",
    ) -> bool:
        """记录一条审计事件。任何失败静默吞掉，返回是否成功写入。"""
        try:
            entry = {
                "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                "source_path": source_path,
                "domain": domain,
                "mutation_type": mutation_type,
                "decision": decision,
                "payload_summary": dict(payload_summary or {}),
                "triggered_by": triggered_by,
                "request_id": request_id,
            }
            with self._lock:
                os.makedirs(self._audit_dir, exist_ok=True)
                path = os.path.join(self._audit_dir, _AUDIT_FILE_NAME)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return True
        except Exception:
            return False


_probe: Optional[GovernanceAuditProbe] = None


def get_governance_audit_probe() -> GovernanceAuditProbe:
    """进程级单例探针（惰性创建）。"""
    global _probe
    if _probe is None:
        _probe = GovernanceAuditProbe()
    return _probe


def set_governance_audit_probe(probe: Optional[GovernanceAuditProbe]) -> None:
    """注入探针实例（测试用）；传 None 时下次 get 重建默认实例。"""
    global _probe
    _probe = probe


def record_governance_audit(
    *,
    source_path: str,
    domain: str,
    mutation_type: str,
    decision: str,
    payload_summary: Optional[Dict[str, Any]] = None,
    triggered_by: str = "",
    request_id: str = "",
) -> bool:
    """业务侧唯一入口：记录 + 全量吞异常（含导入失败）。"""
    try:
        return get_governance_audit_probe().record(
            source_path=source_path,
            domain=domain,
            mutation_type=mutation_type,
            decision=decision,
            payload_summary=payload_summary,
            triggered_by=triggered_by,
            request_id=request_id,
        )
    except Exception:
        return False
