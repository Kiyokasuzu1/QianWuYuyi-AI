# -*- coding: utf-8 -*-
"""
P2.3-B.10 Phase 2 — GovernanceProposalStore（治理提案落账存储）

定位：
    NEED_REVIEW 提案的 append-only 持久化层。三域 MutationAdapter 与
    MutationGateway 均可注入使用；默认只写 governance 数据目录
    （<repo>/data/governance/proposals.jsonl）。

与 src/growth/proposal_store.py 的关系：
    两者并存、互不替代。growth 版是 growth 域提案库（ConflictCheck 的
    exists_similar 协作件）；本 Store 是治理域横切落账（B.10 Phase 2）。

硬边界（B.10 任务书）：
    - append-only：只追加 JSONL 行，不修改 / 重写已有记录；
      重复 proposal_id 幂等跳过（不重写旧行）。
    - 禁止写业务数据：data/users/*（路径守卫）、data/relationship_state.json、
      data/emotion_state.json（本 Store 只写固定文件名 proposals.jsonl）。
    - 不修改任何域状态；本模块只依赖 mutation_contract 冻结契约，
      不导入 memory/emotion/growth/personality/relationship 模块对象。
    - 本模块不创建默认目录之外的任何文件；文件只在 save() 时惰性创建。

记录 schema（B.10 Phase 2 冻结，10 字段）：
    proposal_id / mutation_id / request_id / trace_id / domain /
    target_path / actor_identity / decision / created_at / evidence_refs
    可选扩展键：audit_reference（来自 MutationDecision.audit_reference）。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.governance.mutation_contract import MutationDecision, MutationRequest

logger = logging.getLogger(__name__)

# 记录必填键（B.10 Phase 2 冻结，10 字段）
RECORD_REQUIRED_KEYS: tuple = (
    "proposal_id",
    "mutation_id",
    "request_id",
    "trace_id",
    "domain",
    "target_path",
    "actor_identity",
    "decision",
    "created_at",
    "evidence_refs",
)

# 本 Store 唯一允许写入的文件名（守卫：禁止触碰业务状态文件）
FILE_NAME: str = "proposals.jsonl"

# 禁止落账的业务文件名（双保险：本 Store 永远不会写这些名字）
# v1.5-T7: 追加 relationship_states_v06（v0.6 分桶目录下的状态文件）
FORBIDDEN_FILE_NAMES: frozenset = frozenset({
    "relationship_state.json",
    "relationship_states_v06",
    "emotion_state.json",
})

# 禁止落账的目录子树（data/users/* 用户业务数据、data/relationship_states_v06/* 分桶状态）。
# 用连续路径段匹配，避免误伤系统路径（如 Windows 的 C:\Users）。
# v1.5-T7: 追加 relationship_states_v06 目录段
FORBIDDEN_DIR_SEQUENCE: tuple = ("data", "users")
FORBIDDEN_DIR_SEQUENCES: tuple = (
    ("data", "users"),
    ("data", "relationship_states_v06"),
)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _evidence_refs(evidence: List[Any]) -> List[str]:
    """MutationRequest.evidence → ref 字符串列表（非 dict 项忽略）。"""
    refs: List[str] = []
    for item in evidence or []:
        if isinstance(item, dict) and item.get("ref"):
            refs.append(str(item["ref"]))
    return refs


def build_proposal_record(
    request: MutationRequest,
    decision: MutationDecision,
    *,
    proposal_id: Optional[str] = None,
) -> Dict[str, Any]:
    """MutationRequest + MutationDecision → 10 字段落账记录（纯函数）。

    proposal_id 缺省时沿用三域 adapter 约定：f"prop_{mutation_id[4:]}"。
    audit_reference 作为可选扩展键附带（不破坏 10 字段契约）。
    """
    snapshot = request.context_snapshot or {}
    record: Dict[str, Any] = {
        "proposal_id": proposal_id or f"prop_{request.mutation_id[4:]}",
        "mutation_id": request.mutation_id,
        "request_id": str(
            snapshot.get("request_id") or request.mutation_id
        ),
        "trace_id": str(snapshot.get("trace_id") or ""),
        "domain": request.target_domain,
        "target_path": request.target_path,
        "actor_identity": request.actor_identity,
        "decision": str(decision.decision.value),
        "created_at": _now_iso(),
        "evidence_refs": _evidence_refs(list(request.evidence)),
    }
    audit_reference = getattr(decision, "audit_reference", None) or ""
    if audit_reference:
        record["audit_reference"] = str(audit_reference)
    return record


class GovernanceProposalStore:
    """append-only 治理提案落账存储（JSONL，一行一条记录）。"""

    def __init__(self, data_dir: Optional[Any] = None) -> None:
        self.data_dir = self._resolve_data_dir(data_dir)
        self._lock = threading.Lock()
        # 本进程内已见 proposal_id 集合（首次 save 时从磁盘惰性加载）
        self._known_ids: Optional[Set[str]] = None

    # ============================================================
    # 目录解析与守卫
    # ============================================================
    @staticmethod
    def default_data_dir() -> Path:
        """默认落账目录：<repo>/data/governance（仅 governance 数据目录）。"""
        return Path(__file__).resolve().parents[2] / "data" / "governance"

    def _resolve_data_dir(self, data_dir: Optional[Any]) -> Path:
        base = Path(data_dir) if data_dir is not None else self.default_data_dir()
        resolved = base.resolve()
        parts = [part.lower() for part in resolved.parts]
        # v1.5-T7: 遍历多组禁止目录序列（兼容旧 FORBIDDEN_DIR_SEQUENCE）
        for forbidden in FORBIDDEN_DIR_SEQUENCES:
            sequence = tuple(p.lower() for p in forbidden)
            for idx in range(len(parts) - len(sequence) + 1):
                if tuple(parts[idx:idx + len(sequence)]) == sequence:
                    raise ValueError(
                        f"GovernanceProposalStore 禁止落账到 {resolved}：命中 "
                        f"禁止目录子树 {forbidden}"
                        "（data/users/* 用户业务 / data/relationship_states_v06/* 分桶状态）"
                    )
        return resolved

    def _file_path(self) -> Path:
        return self.data_dir / FILE_NAME

    # ============================================================
    # 写（append-only）
    # ============================================================
    def _load_known_ids(self) -> Set[str]:
        known: Set[str] = set()
        for record in self._read_lines():
            pid = record.get("proposal_id")
            if pid:
                known.add(str(pid))
        return known

    def save(self, record: Dict[str, Any]) -> str:
        """追加一条落账记录，返回 proposal_id。

        - 必填键校验（10 字段冻结契约）
        - append-only：只追加行；proposal_id 已存在 → 幂等跳过（返回原 id）
        - 只写 proposals.jsonl；不创建任何其他文件
        """
        if not isinstance(record, dict):
            raise TypeError(
                f"save 需要 dict 记录，得到 {type(record).__name__}"
            )
        missing = [k for k in RECORD_REQUIRED_KEYS if not record.get(k)]
        if missing:
            raise ValueError(
                f"落账记录缺少必填键 {missing}（10 字段冻结契约）"
            )
        proposal_id = str(record["proposal_id"])

        with self._lock:
            if self._known_ids is None:
                self._known_ids = self._load_known_ids()
            if proposal_id in self._known_ids:
                logger.debug(
                    "[governance_proposal_store] proposal %s 已存在，幂等跳过",
                    proposal_id,
                )
                return proposal_id

            path = self._file_path()
            # 文件名守卫：本 Store 永不写业务状态文件
            if path.name in FORBIDDEN_FILE_NAMES or path.name != FILE_NAME:
                raise RuntimeError(
                    f"GovernanceProposalStore 拒绝写入 {path}（只允许 {FILE_NAME}）"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._known_ids.add(proposal_id)
        return proposal_id

    def record_pending(
        self,
        request: MutationRequest,
        decision: MutationDecision,
        *,
        proposal_id: Optional[str] = None,
    ) -> str:
        """MutationRequest + MutationDecision → 落账记录（MutationDecision 支持入口）。

        MutationDecision / AuditReference 支持语义：decision.decision 写入
        decision 字段，decision.audit_reference 写入可选扩展键 audit_reference。
        """
        record = build_proposal_record(
            request, decision, proposal_id=proposal_id,
        )
        return self.save(record)

    # ============================================================
    # 读（只读查询，供 GovernanceInspector 使用；不修改任何状态）
    # ============================================================
    def _read_lines(self) -> List[Dict[str, Any]]:
        path = self._file_path()
        if not path.exists():
            return []
        records: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        records.append(item)
                except json.JSONDecodeError:  # 损坏行跳过（不重写文件）
                    logger.warning(
                        "[governance_proposal_store] 跳过损坏行: %r", line[:80]
                    )
        return records

    def load_records(self) -> List[Dict[str, Any]]:
        """读取全部落账记录（按落账顺序）。"""
        with self._lock:
            return self._read_lines()

    def list_pending(self) -> List[Dict[str, Any]]:
        """NEED_REVIEW 待消费记录（decision 为 NEED_REVIEW）。"""
        return [
            r for r in self.load_records()
            if str(r.get("decision")) == "NEED_REVIEW"
        ]

    def get_by_domain(self, domain: str) -> List[Dict[str, Any]]:
        return [
            r for r in self.load_records()
            if str(r.get("domain")) == str(domain)
        ]

    def get_by_mutation_id(self, mutation_id: str) -> List[Dict[str, Any]]:
        return [
            r for r in self.load_records()
            if str(r.get("mutation_id")) == str(mutation_id)
        ]


__all__ = [
    "GovernanceProposalStore",
    "build_proposal_record",
    "RECORD_REQUIRED_KEYS",
    "FILE_NAME",
    "FORBIDDEN_FILE_NAMES",
    "FORBIDDEN_DIR_SEQUENCE",
    "FORBIDDEN_DIR_SEQUENCES",
]
