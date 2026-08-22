# -*- coding: utf-8 -*-
"""
src/governance/write_path_registry.py

Phase G-0 Governance Freeze: 合法写入口注册表 + 废弃写路径清单。

目的:
- LEGAL_WRITE_PATHS: 声明每个状态突变入口的治理要素
  (proposal_source / approval_requirement / audit_requirement)。
- DEPRECATED_WRITE_PATHS: 旧直写路径清单——仅标记 deprecated,
  G-1/G-2 迁移, 本阶段不删除、不改变行为。
- warn_deprecated_once(): legacy 路径一次性 DeprecationWarning
  (进程内每 key 只告警一次, 不改变任何执行行为)。

约束(红线):
- 本模块是纯数据/工具模块, 不 import 任何域模块(避免循环依赖);
- 只声明, 不执行任何状态修改;
- 注册表条目必须与源码真实写入口一一对应(Phase G 前置审查取证)。
"""

from __future__ import annotations

import threading
import warnings
from dataclasses import dataclass
from typing import Set, Tuple


@dataclass(frozen=True)
class LegalWritePath:
    """合法写入口: 必须声明 proposal / approval / audit 三要素。"""

    target: str
    writer: str
    file: str
    proposal_source: str
    approval_requirement: str
    audit_requirement: str


@dataclass(frozen=True)
class DeprecatedWritePath:
    """废弃写路径: 仅标记, 待迁移, 不删除。"""

    target: str
    writer: str
    file: str
    reason: str
    migration_phase: str


LEGAL_WRITE_PATHS: Tuple[LegalWritePath, ...] = (
    # ---------------- personality ----------------
    LegalWritePath(
        target="personality_state.traits",
        writer="PersonalityState.apply_evolution",
        file="src/personality/personality_state.py",
        proposal_source="GrowthProposal (approved 后经 drain 链)",
        approval_requirement="ApprovalRecord 凭证 (EP-3 非空 + EP-4 identity 前缀硬拒)",
        audit_requirement="evolution_record + state_mutations.jsonl",
    ),
    LegalWritePath(
        target="personality_state.traits",
        writer="PersonalityEvolutionPipeline.apply_approved_to_state",
        file="src/personality/personality_evolution_pipeline.py",
        proposal_source="GrowthProposal (status=approved, B-store)",
        approval_requirement="approval_id 参数 (真实审批记录, G-1 硬化)",
        audit_requirement="PersonalityGrowthRecord + state_mutations.jsonl",
    ),
    LegalWritePath(
        target="personality.trait_states (内存执行件)",
        writer="TraitStateUpdater.apply",
        file="src/personality/trait_state_updater.py",
        proposal_source="GrowthProposal (经 PersonalityAdapter.apply_proposal)",
        approval_requirement="approval_record 参数 (G-1 硬化后强制, 禁止自证)",
        audit_requirement="evolution_record",
    ),
    LegalWritePath(
        target="personality.trait_states (启动恢复)",
        writer="TraitRebuilder.inject_trait_states",
        file="src/runtime/trait_rebuilder.py",
        proposal_source="无 (启动恢复既有历史, 非事件驱动)",
        approval_requirement="无新增变更 (仅恢复已生效状态)",
        audit_requirement="restore trace",
    ),
    # ---------------- emotion ----------------
    LegalWritePath(
        target="emotion_state.dimensions",
        writer="EmotionUpdater.apply",
        file="src/emotion/emotion_updater.py",
        proposal_source="EmotionChangeProposal (E-2 提案链)",
        approval_requirement="维度白名单 / 置信度 / 最大 delta 门 (E-3)",
        audit_requirement="AuditEntry (before/after/evidence) + state_mutations.jsonl",
    ),
    LegalWritePath(
        target="emotion_state.dimensions",
        writer="EmotionManager._apply_accepted_emotion",
        file="src/emotion/emotion_manager.py",
        proposal_source="MutationRequest (EmotionMutationAdapter, B.7)",
        approval_requirement="MutationGateway ACCEPT 裁决",
        audit_requirement="MutationGateway audit writer",
    ),
    # ---------------- self_model ----------------
    LegalWritePath(
        target="self_model",
        writer="SelfModelStore.apply_change_proposal",
        file="src/personality/self_model_store.py",
        proposal_source="SelfModelChangeProposal",
        approval_requirement="SelfModelGovernancePolicy 分档 (trait 档需审批) 或 approved drain",
        audit_requirement="state_mutations.jsonl",
    ),
    LegalWritePath(
        target="self_model",
        writer="SelfModelApprovedDrain 应用",
        file="src/growth/self_model_approved_drain.py",
        proposal_source="B-store self_model 提案 (status=approved)",
        approval_requirement="四字段审批证明 (reviewer/reviewed_at/decision/record_id)",
        audit_requirement="governance JSONL 审计",
    ),
    LegalWritePath(
        target="self_model",
        writer="SelfModelUpdater.apply_proposal",
        file="src/personality/self_model_updater.py",
        proposal_source="SelfModelChangeProposal",
        approval_requirement="policy AUTO_APPLY 档 (context/preference) 或 approved",
        audit_requirement="state_mutations.jsonl",
    ),
    # ---------------- memory ----------------
    LegalWritePath(
        target="memory.importance",
        writer="EmotionMemoryWeightBridge.apply_to_memory",
        file="src/emotion/emotion_memory_weight.py",
        proposal_source="情绪派生权重 (E-4 compute_weight)",
        approval_requirement="clamp [0,1] + MUTABLE_FIELDS 白名单",
        audit_requirement="state_mutations.jsonl",
    ),
    # ---------------- goal (v1.3 Phase 1) ----------------
    LegalWritePath(
        target="goal_state",
        writer="GoalApprovedDrain 应用",
        file="src/goal/goal_approved_drain.py",
        proposal_source="B-store goal 提案 (status=approved, proposal_type=goal)",
        approval_requirement="B-store approved 状态 + reviewer_id/reviewed_at 审批凭证",
        audit_requirement="state_mutations.jsonl (component=goal)",
    ),
)


DEPRECATED_WRITE_PATHS: Tuple[DeprecatedWritePath, ...] = (
    # ---------------- personality (G-1) ----------------
    DeprecatedWritePath(
        target="personality.traits",
        writer="PersonalityResolver.resolve 直写分支",
        file="src/personality/personality_resolver.py",
        reason="累积漂移直接写 _trait_states, 无提案/审批 (personality_resolver.py:183-191)",
        migration_phase="G-1",
    ),
    DeprecatedWritePath(
        target="personality.trait_states",
        writer="PersonalityAdapter.apply_proposal mark_approved 自证",
        file="src/personality/personality_adapter.py",
        reason="mark_approved=True 自证批准, 无外部审批凭证 (personality_adapter.py:556-559)",
        migration_phase="G-1",
    ),
    # ---------------- self_model (G-1) ----------------
    DeprecatedWritePath(
        target="self_model",
        writer="GrowthIntegrationService._refresh_self_model",
        file="src/growth/growth_integration.py",
        reason="growth_history 变化直接重建 self_model, 无审批 (growth_integration.py:449)",
        migration_phase="G-1",
    ),
    DeprecatedWritePath(
        target="self_model",
        writer="Orchestrator 治理 auto_apply 分支",
        file="src/orchestrator.py",
        reason="auto_apply 直写, 绕过 policy 审批档 (orchestrator.py:2246)",
        migration_phase="G-1",
    ),
    DeprecatedWritePath(
        target="self_model",
        writer="SelfModelConsumer.process",
        file="src/admin/selfmodel_consumer.py",
        reason="admin 旁路 apply_pcr + save_state, 无审批证明 (selfmodel_consumer.py:650-690)",
        migration_phase="G-1",
    ),
    # ---------------- emotion (G-2) ----------------
    DeprecatedWritePath(
        target="emotion_state.dimensions",
        writer="EmotionManager.process_event legacy 分支",
        file="src/emotion/emotion_manager.py",
        reason="apply_delta + repository.save 直写, 无提案无审计 (emotion_manager.py:69-83)",
        migration_phase="G-2",
    ),
    DeprecatedWritePath(
        target="emotion_state.dimensions",
        writer="EmotionManager.update decay 直写",
        file="src/emotion/emotion_manager.py",
        reason="decay 直写 save, 无提案无审计 (emotion_manager.py:198-209)",
        migration_phase="G-2",
    ),
    DeprecatedWritePath(
        target="emotion_state (per-user 副本)",
        writer="Orchestrator per-user 二次持久化",
        file="src/orchestrator.py",
        reason="未经治理的第二条持久化通道 (orchestrator.py:2057-2072)",
        migration_phase="G-2",
    ),
    # ---------------- memory (G-2) ----------------
    DeprecatedWritePath(
        target="memory.importance",
        writer="新记忆 importance=0.5 硬编码 (Orchestrator)",
        file="src/orchestrator.py",
        reason="新记忆 importance 固定 0.5, 未接情绪权重 (orchestrator.py:1237)",
        migration_phase="G-2",
    ),
    DeprecatedWritePath(
        target="memory.importance",
        writer="经历投影 importance=0.5 硬编码 (RuntimeCore)",
        file="src/runtime/runtime_core.py",
        reason="经历投影 importance 固定 0.5, 域归属待 G-2 复核 (runtime_core.py:5944)",
        migration_phase="G-2",
    ),
)


def is_legal_write(file_path: str, writer: str) -> bool:
    """查询某 (文件, writer) 是否为已登记合法写入口。"""
    for entry in LEGAL_WRITE_PATHS:
        if entry.file == file_path and entry.writer == writer:
            return True
    return False


def is_deprecated_write(file_path: str, writer: str) -> bool:
    """查询某 (文件, writer) 是否为已标记 deprecated 的写路径。"""
    for entry in DEPRECATED_WRITE_PATHS:
        if entry.file == file_path and entry.writer == writer:
            return True
    return False


# ============================================================
# legacy 一次性告警 (不改变行为)
# ============================================================

_WARNED_KEYS: Set[str] = set()
_WARN_LOCK = threading.Lock()


def warn_deprecated_once(key: str, message: str) -> None:
    """对 legacy 路径发出 DeprecationWarning; 同一 key 进程内只告警一次。

    DeprecationWarning 默认被 Python/pytest 忽略, 不影响任何执行行为;
    仅在显式打开 -Wd 时可见, 供治理观测与迁移审计。
    """
    with _WARN_LOCK:
        if key in _WARNED_KEYS:
            return
        _WARNED_KEYS.add(key)
    warnings.warn(message, DeprecationWarning, stacklevel=3)


__all__ = [
    "LegalWritePath",
    "DeprecatedWritePath",
    "LEGAL_WRITE_PATHS",
    "DEPRECATED_WRITE_PATHS",
    "is_legal_write",
    "is_deprecated_write",
    "warn_deprecated_once",
]
