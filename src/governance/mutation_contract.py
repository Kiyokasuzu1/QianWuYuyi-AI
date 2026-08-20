# -*- coding: utf-8 -*-
"""
P2.3-B.3 — Mutation Contract（变更治理数据契约）

依据 docs/governance/mutation_gateway_design.md §3.2/§3.3 落地：

- MutationRequest  ：Gateway 输入（9 字段，冻结）
- MutationDecision ：Gateway 输出（四态 verdict）
- CheckResult      ：单道检查的结果（passed/reason/metadata）
- DecisionVerdict  ：ACCEPT / REJECT / NEED_REVIEW / DEFER

硬约束（B.3 任务书）：
- frozen dataclass，创建后不可变（派生一律走 dataclasses.replace）
- 无业务依赖：只依赖 stdlib + RuntimeContext v2 契约常量（MUTATION_TARGETS）
- 可 JSON 序列化：容器字段强制 JSON 安全校验，
  任何 manager / repository / client 等非标量实例都会被 __post_init__ 拒绝
  （TypeError），从类型层面落实"禁止携带写句柄"。
- 本模块不导入、不持有任何 memory/emotion/growth/personality/relationship
  模块对象；target_domain 枚举复用 request_context.MUTATION_TARGETS
  （单一事实来源，六域：memory/emotion/relationship/growth/personality/self_model）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from src.runtime.request_context import MUTATION_TARGETS

# ============================================================
# 基础工具
# ============================================================

JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def ensure_json_safe(value: Any, where: str) -> Any:
    """递归校验值可 JSON 序列化；非法（对象实例等）抛 TypeError。

    这是"禁止包含 manager/repository/client 实例"的契约级执行点：
    dataclass 实例、函数、模块等一律拒绝；允许 dict/list/标量。
    """
    if isinstance(value, JSON_SCALAR_TYPES):
        return value
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"{where}: dict 键必须是 str，得到 {type(k).__name__}")
            ensure_json_safe(v, f"{where}.{k}")
        return value
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            ensure_json_safe(v, f"{where}[{i}]")
        return value
    raise TypeError(
        f"{where}: 不允许非 JSON 安全的类型 {type(value).__module__}."
        f"{type(value).__name__}（契约禁止携带 manager/repository/client 实例）"
    )


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{where} 必须是非空 str，得到 {value!r}")
    return value


def _require_dict(value: Any, where: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{where} 必须是 dict，得到 {type(value).__name__}")
    ensure_json_safe(value, where)
    return dict(value)


def _require_list(value: Any, where: str) -> List[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{where} 必须是 list，得到 {type(value).__name__}")
    ensure_json_safe(value, where)
    return list(value)


# ============================================================
# DecisionVerdict —— 四态决策
# ============================================================
class DecisionVerdict(str, Enum):
    """Gateway 输出四态（B.2 §3.3 冻结表）。

    - ACCEPT     ：五道检查全过，允许进入现有执行组件
    - REJECT     ：硬性违规（身份越权 / 身份锚点 / 域红线 / 重复落账），终止
    - NEED_REVIEW：证据不足 / 倒转冲突 / 高风险路径，进入复核队列
    - DEFER      ：重复提案 / 年度上限 / 审计链路缺失，暂缓可重提
    """

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    NEED_REVIEW = "NEED_REVIEW"
    DEFER = "DEFER"


# ============================================================
# MutationRequest —— Gateway 输入（9 字段冻结）
# ============================================================
RISK_LEVELS = ("low", "medium", "high")


@dataclass(frozen=True)
class MutationRequest:
    """变更请求。字段与 B.2 §3.2 表一一对应，禁止增删。"""

    mutation_id: str = field(default_factory=lambda: _gen_id("mut"))
    source_event: Dict[str, Any] = field(default_factory=dict)
    actor_identity: str = ""
    target_domain: str = ""
    target_path: str = ""
    proposed_change: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Any] = field(default_factory=list)
    context_snapshot: Dict[str, Any] = field(default_factory=dict)
    risk_level: str = "medium"

    def __post_init__(self) -> None:
        mutation_id = _require_str(self.mutation_id, "mutation_id")
        actor = _require_str(self.actor_identity, "actor_identity")
        domain = _require_str(self.target_domain, "target_domain")
        path = _require_str(self.target_path, "target_path")

        if domain not in MUTATION_TARGETS:
            raise ValueError(
                f"target_domain 必须是 {sorted(MUTATION_TARGETS)} 之一，得到 {domain!r}"
            )
        if self.risk_level not in RISK_LEVELS:
            raise ValueError(
                f"risk_level 必须是 {RISK_LEVELS} 之一，得到 {self.risk_level!r}"
            )

        object.__setattr__(self, "mutation_id", mutation_id)
        object.__setattr__(self, "actor_identity", actor)
        object.__setattr__(self, "target_domain", domain)
        object.__setattr__(self, "target_path", path)
        # 容器字段：拷贝 + JSON 安全校验（拒绝写句柄实例）
        object.__setattr__(
            self, "source_event", _require_dict(self.source_event, "source_event"),
        )
        object.__setattr__(
            self, "proposed_change",
            _require_dict(self.proposed_change, "proposed_change"),
        )
        object.__setattr__(
            self, "context_snapshot",
            _require_dict(self.context_snapshot, "context_snapshot"),
        )
        object.__setattr__(self, "evidence", _require_list(self.evidence, "evidence"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mutation_id": self.mutation_id,
            "source_event": dict(self.source_event),
            "actor_identity": self.actor_identity,
            "target_domain": self.target_domain,
            "target_path": self.target_path,
            "proposed_change": dict(self.proposed_change),
            "evidence": list(self.evidence),
            "context_snapshot": dict(self.context_snapshot),
            "risk_level": self.risk_level,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MutationRequest":
        if not isinstance(data, dict):
            raise TypeError(f"from_dict 需要 dict，得到 {type(data).__name__}")
        return cls(
            mutation_id=data.get("mutation_id") or _gen_id("mut"),
            source_event=data.get("source_event") or {},
            actor_identity=data.get("actor_identity") or "",
            target_domain=data.get("target_domain") or "",
            target_path=data.get("target_path") or "",
            proposed_change=data.get("proposed_change") or {},
            evidence=data.get("evidence") if isinstance(data.get("evidence"), list) else [],
            context_snapshot=data.get("context_snapshot") or {},
            risk_level=data.get("risk_level") or "medium",
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ============================================================
# CheckResult —— 单道检查结果
# ============================================================
@dataclass(frozen=True)
class CheckResult:
    """单道检查输出（B.3 Phase 3 契约）。

    metadata 中可携带 verdict 建议（"REJECT" / "NEED_REVIEW" / "DEFER"），
    Gateway 以首个未通过检查的 metadata["verdict"] 为最终裁决；
    缺失时按 DEFAULT_FAIL_VERDICT（mutation_gateway.py）兜底。
    """

    passed: bool
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _require_str(self.reason, "reason") if self.reason else "")
        object.__setattr__(self, "metadata", _require_dict(self.metadata, "metadata"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


# ============================================================
# MutationDecision —— Gateway 输出
# ============================================================
@dataclass(frozen=True)
class MutationDecision:
    """Gateway 输出（B.3 Phase 2 字段冻结：6 字段）。

    注意与 B.2 §3.3 的差异：B.3 任务书字段集不含 prescription，
    执行分派指引留待 B.4 迁移阶段再引入（见 implementation_report §3）。
    """

    mutation_id: str = ""
    decision: DecisionVerdict = DecisionVerdict.DEFER
    reason: str = ""
    checks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    audit_reference: str = ""
    timestamp: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mutation_id", _require_str(self.mutation_id, "mutation_id"))
        if not isinstance(self.decision, DecisionVerdict):
            raise TypeError(f"decision 必须是 DecisionVerdict，得到 {self.decision!r}")
        object.__setattr__(self, "reason", _require_str(self.reason, "reason") if self.reason else "")
        object.__setattr__(self, "checks", _require_dict(self.checks, "checks"))
        object.__setattr__(
            self, "audit_reference",
            _require_str(self.audit_reference, "audit_reference") if self.audit_reference else "",
        )
        object.__setattr__(self, "timestamp", _require_str(self.timestamp, "timestamp"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mutation_id": self.mutation_id,
            "decision": self.decision.value,
            "reason": self.reason,
            "checks": {k: dict(v) for k, v in self.checks.items()},
            "audit_reference": self.audit_reference,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MutationDecision":
        if not isinstance(data, dict):
            raise TypeError(f"from_dict 需要 dict，得到 {type(data).__name__}")
        raw_checks = data.get("checks")
        return cls(
            mutation_id=data.get("mutation_id") or "",
            decision=DecisionVerdict(data.get("decision") or "DEFER"),
            reason=data.get("reason") or "",
            checks=raw_checks if isinstance(raw_checks, dict) else {},
            audit_reference=data.get("audit_reference") or "",
            timestamp=data.get("timestamp") or _now_iso(),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


__all__ = [
    "DecisionVerdict",
    "MutationRequest",
    "MutationDecision",
    "CheckResult",
    "RISK_LEVELS",
    "ensure_json_safe",
]
