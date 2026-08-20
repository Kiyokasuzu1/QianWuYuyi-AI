# -*- coding: utf-8 -*-
"""
P2.3-B.3 — AuditWriter 接口（审计/落账接线准备）
P2.3-B.10 Phase 4 — 统一 MutationAuditRecord 投影

依据 B.1 结论：MutationJournal / AuditTrail 目前只有结构、无生产写入（B11）。
本阶段只定义写入接口 + 内存实现，为后续把 Gateway 决策接入
RuntimeContext v2 的 mutations journal / audit chain 预留挂接面。

边界（B.3 任务书）：
- 只增加接口：record_request() / record_decision()
- 允许 memory implementation（InMemoryAuditWriter）
- 禁止修改现有 Audit 数据结构（MutationJournal/AuditTrail/AuditLink
  一律不 import、不修改、不子类化）
- 无业务依赖：不导入 approval / growth / personality 任何模块

B.10 Phase 4 统一抽象：
    MutationDecision 自身不含 domain / actor（它们只存在于 MutationRequest），
    导致审计留痕无法按域/身份关联。新增 MutationAuditRecord 统一投影
    （6 字段冻结：mutation_id / domain / actor / decision / timestamp / reason），
    由 InMemoryAuditWriter 在 record_decision 时自动合并最近一次
    record_request 的对应请求生成。旧接口（record_request / record_decision /
    requests / decisions）全部保留不变。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

# ============================================================
# MutationAuditRecord —— 统一审计投影（B.10 Phase 4，6 字段冻结）
# ============================================================
@dataclass(frozen=True)
class MutationAuditRecord:
    """统一审计记录（B.10 Phase 4）。

    字段来源：
    - mutation_id ← decision.mutation_id
    - domain      ← request.target_domain
    - actor       ← request.actor_identity
    - decision    ← decision.decision.value（ACCEPT/REJECT/NEED_REVIEW/DEFER）
    - timestamp   ← decision.timestamp（ISO 字符串）
    - reason      ← decision.reason

    这是未来 MutationJournal 正式落账（B.10 债务 #2）的投影目标形状；
    本阶段不导入、不修改 MutationJournal / AuditTrail / AuditLink。
    """

    mutation_id: str
    domain: str
    actor: str
    decision: str
    timestamp: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mutation_id": self.mutation_id,
            "domain": self.domain,
            "actor": self.actor,
            "decision": self.decision,
            "timestamp": self.timestamp,
            "reason": self.reason,
        }


def build_audit_record(request: Any, decision: Any) -> MutationAuditRecord:
    """MutationRequest + MutationDecision → 统一 MutationAuditRecord（纯函数）。

    鸭子类型读取（兼容 dataclass 与 dict 输入）：request 需提供
    target_domain / actor_identity；decision 需提供 mutation_id /
    decision.value / timestamp / reason。缺字段时用空串兜底，不抛异常。
    """
    request_dict = getattr(request, "to_dict", None)
    req = (
        dict(request_dict())
        if callable(request_dict)
        else dict(getattr(request, "__dict__", {}) or {})
    )
    if isinstance(request, dict):
        req = dict(request)

    decision_value = getattr(decision, "decision", None)
    decision_str = str(
        getattr(decision_value, "value", decision_value) or ""
    )
    return MutationAuditRecord(
        mutation_id=str(getattr(decision, "mutation_id", "") or ""),
        domain=str(req.get("target_domain", "") or ""),
        actor=str(req.get("actor_identity", "") or ""),
        decision=decision_str,
        timestamp=str(
            getattr(decision, "timestamp", "")
            or datetime.utcnow().isoformat() + "Z"
        ),
        reason=str(getattr(decision, "reason", "") or ""),
    )


# ============================================================
# AuditWriter 接口（B.3 冻结，旧 API 不变）
# ============================================================
class AuditWriter(ABC):
    """Governance 审计写入接口。

    未来接线方向（本阶段不实施）：
    - record_request  → 投影为 MutationJournal.add(MutationRecord)
    - record_decision → 投影为 AuditTrail.chain 追加 AuditLink
    投影逻辑属于实施阶段的 adapter，不属于本接口。
    """

    @abstractmethod
    def record_request(self, request: Any) -> None:
        """记录进入 Gateway 的变更请求（幂等追加，不修改请求本体）。"""

    @abstractmethod
    def record_decision(self, decision: Any) -> Optional[str]:
        """记录 Gateway 决策；返回审计引用（audit_reference），无引用时返回 None。"""


class InMemoryAuditWriter(AuditWriter):
    """内存实现：请求与决策各存一份 JSON 化副本，供测试与单进程追溯。

    B.10 Phase 4：额外维护统一投影 records（MutationAuditRecord 列表），
    在 record_decision 时自动合并匹配的请求生成。旧字段 requests / decisions
    与返回引用格式保持不变。
    """

    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []
        self.decisions: List[Dict[str, Any]] = []
        # B.10 Phase 4：统一审计投影（mutation_id/domain/actor/decision/timestamp/reason）
        self.records: List[MutationAuditRecord] = []

    def record_request(self, request: Any) -> None:
        self.requests.append(_to_jsonable_dict(request))

    def record_decision(self, decision: Any) -> Optional[str]:
        self.decisions.append(_to_jsonable_dict(decision))
        self._append_unified_record(decision)
        return f"audit_{len(self.decisions)}_{getattr(decision, 'mutation_id', 'unknown')}"

    def _append_unified_record(self, decision: Any) -> None:
        """找最近一次同 mutation_id 的请求，生成统一审计投影（找不到则不追加）。"""
        mutation_id = str(getattr(decision, "mutation_id", "") or "")
        request_dict: Optional[Dict[str, Any]] = None
        for item in reversed(self.requests):
            if str(item.get("mutation_id") or "") == mutation_id:
                request_dict = item
                break
        if request_dict is not None:
            self.records.append(build_audit_record(request_dict, decision))

    def clear(self) -> None:
        self.requests.clear()
        self.decisions.clear()
        self.records.clear()


def _to_jsonable_dict(obj: Any) -> Dict[str, Any]:
    """把 to_dict() 能力对象转为 dict；无 to_dict 时兜底 vars 拷贝（仅标量安全场景）。"""
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return dict(to_dict())
        except Exception:  # noqa: BLE001
            pass
    return dict(getattr(obj, "__dict__", {}) or {})


__all__ = [
    "AuditWriter",
    "InMemoryAuditWriter",
    "MutationAuditRecord",
    "build_audit_record",
]
