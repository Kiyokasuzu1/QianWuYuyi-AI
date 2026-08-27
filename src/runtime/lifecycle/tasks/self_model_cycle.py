# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/tasks/self_model_cycle.py

P2.8 Phase D-4.0: SelfModel Cycle 周期任务(Thin Trigger)。

职责(任务书 Step 2):
    时间触发
        ↓
    读取历史上下文(成长历史, 只读)
        ↓
    SelfModelUpdater.create_proposal_from_growth(纯计算, 不写 store)
        ↓
    映射为 canonical GrowthProposal(SelfModelProposal 形态)
        ↓
    status="pending" + 治理链接(_governance_origin=self_model_cycle,
    _governance.decision=pending_review)
        ↓
    ProposalStore.save()(append-only, 与 D-2.0/D-3.0 同一落盘位置)
        ↓
    写 LifecycleEvent 审计(proposal ids 进 produced_events)

红线(任务书 + Step 1 审查结论):
- 禁止 apply / approve / accept / self_model.save:
  绝不调用 SelfModelUpdater.update_from_growth(其 AUTO_APPLY 分支直写 store)、
  SelfModelUpdater.apply_proposal、_apply_to_store、SelfModelStore 任何写方法
- SelfModelGovernancePolicy 的 context/preference 级规则是 AUTO_APPLY——
  周期任务必须只用 create_proposal_from_growth(纯计算), 绝不走
  update_from_growth 便捷入口
- 提案仅落盘, 零业务状态变更; self_model.json 只读甚至不读
- 不消费记忆(不 mark_processed), 不影响聊天链路; 重复提案由
  compute_fingerprint + ProposalStore.exists_similar 去重

注入设计(测试/实验注入 fake, 生产默认惰性构造):
- updater: SelfModelUpdater 实例(默认无 store —— 提案生成不需要 store)
- proposal_store: ProposalStore 实例(默认 data/proposals/proposals.jsonl)
- growth_records_provider: Callable[[], List[dict]] —— 生产默认只读加载
  PersonalityGrowthHistory 最近 N 条并转换为 GrowthRecord 兼容形态;
  测试/实验注入固定记录
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from src.contracts.growth_schema import ChangeItem, GrowthProposal
from src.contracts.lifecycle_event_schema import TASK_TYPE_SELF_MODEL_CYCLE
from src.runtime.lifecycle.lifecycle_decision import AfterInterval
from src.runtime.lifecycle.lifecycle_task import BaseLifecycleTask

DEFAULT_INTERVAL_SECONDS = 86400.0  # 默认每日一次
DEFAULT_BUDGET_MS = 15000  # narrative 生成可能含 LLM 调用, 预算放宽
DEFAULT_BATCH_LIMIT = 20
DEFAULT_PROPOSAL_STORE_PATH = "data/proposals/proposals.jsonl"
DEFAULT_GROWTH_HISTORY_PATH = "data/personality_growth_history.json"
OWNER = "p2.8.d4"
TASK_ID = "self_model.cycle"

# 提案来源标记(任务书 Step 3: _governance_origin 必须等于 self_model_cycle)
GOVERNANCE_ORIGIN = "self_model_cycle"


def _build_default_updater():
    """惰性构造 SelfModelUpdater(无 store —— 仅提案生成, 无写入口可达)。"""
    from src.personality.self_model_updater import SelfModelUpdater

    return SelfModelUpdater()


def _build_default_store(proposal_store_path: str):
    """惰性构造 ProposalStore(与 D-2.0/D-3.0 治理待审区同一 append-only JSONL)。"""
    from src.growth.proposal_store import ProposalStore

    return ProposalStore(proposal_store_path)


def _to_growth_record_shape(history_record: dict) -> dict:
    """PersonalityGrowthRecord → SelfModelUpdater 期望的 GrowthRecord 形态。

    纯转换, 无副作用:
    - affected_dimensions: List[str] → {dim: delta}(从 changes 取 delta)
    - meaning/narrative → reason
    - trigger_events[0] → source_event_id
    """
    dims = history_record.get("affected_dimensions") or []
    changes = history_record.get("changes") or {}
    dim_map: dict = {}
    for dim in dims:
        if not isinstance(dim, str):
            continue
        change = changes.get(dim) or {}
        try:
            dim_map[dim] = float(change.get("delta", 0.0))
        except Exception:
            dim_map[dim] = 0.0
    trigger_events = history_record.get("trigger_events") or []
    source_event_id = trigger_events[0] if trigger_events else None
    evidence_ids = history_record.get("evidence_ids") or []
    return {
        "record_id": history_record.get("record_id", ""),
        "source_event_id": source_event_id,
        "evidence_ids": list(evidence_ids),
        "growth_signal": "personality_growth_history",
        "source_type": "preference",
        "growth_level": history_record.get("growth_level", "context"),
        "affected_dimensions": dim_map,
        "confidence": history_record.get("confidence", 0.5),
        "reason": history_record.get("meaning")
        or history_record.get("narrative", ""),
    }


def _build_default_provider(batch_limit: int):
    """生产默认输入源: 只读加载成长历史最近 N 条, 转换形态后返回。"""

    def provider() -> List[dict]:
        from src.personality.personality_growth_record import (
            PersonalityGrowthHistory,
        )

        try:
            history = PersonalityGrowthHistory(
                storage_path=DEFAULT_GROWTH_HISTORY_PATH
            )
            records = history.all()
        except Exception:
            return []
        converted = [_to_growth_record_shape(r) for r in records[-batch_limit:]]
        return [r for r in converted if r.get("record_id")]

    return provider


def build_self_model_proposal(
    sm_proposal: Any,
    growth_record: dict,
    *,
    proposal_id: Optional[str] = None,
) -> GrowthProposal:
    """SelfModelChangeProposal → canonical GrowthProposal(SelfModelProposal 形态)。

    任务书 Step 3 字段映射:
    - id                    → GrowthProposal.id
    - source_events         → evaluator_meta["source_events"]
      (canonical schema 冻结, 顶层不可加字段)
    - evidence_ids          → GrowthProposal.evidence_ids
    - proposed_changes      → ChangeItem 摘要(path=self_model.<target>)
    - confidence            → GrowthProposal.confidence
    - evaluator_meta        → 含 proposal_type=self_model + self_model_proposal
      (1:1 映射 C-1 drain 的 _SELF_MODEL_PROPOSAL_FIELDS, 审批执行端可无损消费)
    - status="pending"      → 由 execute 统一固化

    纯构建, 零副作用; 不写入任何 store。
    """
    source = dict(getattr(sm_proposal, "source", None) or {})
    change = dict(getattr(sm_proposal, "change", None) or {})
    growth_id = str(source.get("growth_id", "") or "")
    source_event_id = source.get("source_event_id") or growth_id or None

    evidence_ids: List[str] = []
    for eid in list(source.get("evidence_ids") or []):
        evidence_ids.append(str(eid))
    for eid in list(growth_record.get("evidence_ids") or []):
        if str(eid) not in evidence_ids:
            evidence_ids.append(str(eid))

    reason = (
        change.get("narrative", "")
        or change.get("meaning", "")
        or growth_record.get("reason", "")
        or f"self_model proposal for {growth_id}"
    )

    changes = [
        ChangeItem(
            path=f"self_model.{getattr(sm_proposal, 'target', '') or 'self_understanding'}",
            before=None,
            after=None,
            reason=reason,
        )
    ]

    try:
        confidence = float(
            source.get("confidence", growth_record.get("confidence", 0.5)) or 0.5
        )
    except Exception:
        confidence = 0.5

    meta: dict = {
        "proposal_type": "self_model",
        "growth_level": growth_record.get("growth_level", ""),
        "growth_signal": growth_record.get("growth_signal", ""),
        "source_events": [
            str(x) for x in [source_event_id, growth_id] if x
        ],
        # SelfModelChangeProposal 序列化载荷(与 C-1 drain 1:1 兼容):
        # 未来审批执行端(人工 approve 后经 drain/updater.apply_proposal)
        # 可直接从此键重建, 无需改 schema
        "self_model_proposal": sm_proposal.to_dict(),
        "_source_schema": "self_model_change_proposal",
    }

    proposal = GrowthProposal(
        source_event_id=source_event_id,
        proposed_changes=changes,
        confidence=confidence,
        evidence_ids=evidence_ids,
        evaluator_meta=meta,
        status="pending",
    )
    if proposal_id:
        proposal.id = str(proposal_id)
    return proposal


class SelfModelCycleTask(BaseLifecycleTask):
    """自我模型周期任务: 反思历史 → pending 提案落盘 → 审计。

    绝不调用 apply/approve/accept 链路, 绝不写 self_model store。
    """

    TASK_TYPE = TASK_TYPE_SELF_MODEL_CYCLE
    IDEMPOTENCY_BUCKET_SECONDS = 86400  # 幂等键按自然日分桶

    def __init__(
        self,
        updater: Any = None,
        proposal_store: Any = None,
        growth_records_provider: Optional[Callable[[], List[dict]]] = None,
        *,
        task_id: str = TASK_ID,
        interval_seconds: Optional[float] = None,
        budget_ms: Optional[int] = DEFAULT_BUDGET_MS,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        proposal_store_path: str = DEFAULT_PROPOSAL_STORE_PATH,
    ) -> None:
        interval = float(
            interval_seconds if interval_seconds is not None else DEFAULT_INTERVAL_SECONDS
        )
        super().__init__(
            task_id,
            OWNER,
            display_name="SelfModel Proposal Cycle",
            condition=AfterInterval(interval),
            budget_ms=budget_ms,
            interval_seconds=interval,
            max_concurrent=1,
        )
        self._updater = updater
        self._store = proposal_store
        self._provider = growth_records_provider
        try:
            self._batch_limit = int(batch_limit or DEFAULT_BATCH_LIMIT)
        except Exception:
            self._batch_limit = DEFAULT_BATCH_LIMIT
        if self._batch_limit <= 0:
            self._batch_limit = DEFAULT_BATCH_LIMIT
        self._proposal_store_path = str(proposal_store_path)

    # --------------------------------------------------------
    # 执行
    # --------------------------------------------------------
    def execute(self, context) -> dict:
        """读历史 → 纯计算生成 SelfModel 提案 → pending 落盘(零 apply)。

        返回 dict 由 BaseLifecycleTask.run_safely 包装为 SUCCESS LifecycleResult;
        "events" 键进入 LifecycleResult.produced_events → 审计可追踪。
        """
        updater = self._updater
        if updater is None:
            updater = _build_default_updater()
        store = self._store
        if store is None:
            store = _build_default_store(self._proposal_store_path)
        provider = self._provider
        if provider is None:
            provider = _build_default_provider(self._batch_limit)

        # 1. 读取历史上下文(只读)
        records = list(provider() or [])[: self._batch_limit]
        extracted_raw = len(records)
        if not records:
            return {
                "metrics": {
                    "records_read": 0,
                    "records_evaluated": 0,
                    "proposals_created": 0,
                    "proposals_deduped": 0,
                },
            }

        from src.growth.mutation_adapter import attach_governance_linkage
        from src.growth.proposal_store import compute_fingerprint

        created: List[str] = []
        evaluated_count = 0
        deduped = 0

        # 2. 逐记录: 纯计算提案(create_proposal_from_growth, 绝不 update_from_growth)
        for record in records:
            sm_proposal = updater.create_proposal_from_growth(record)
            if sm_proposal is None:
                continue
            evaluated_count += 1

            proposal = build_self_model_proposal(sm_proposal, record)

            # 3. 落盘前治理化: pending + 治理链接键(不经过任何 approve/apply)
            proposal.status = "pending"
            em = dict(getattr(proposal, "evaluator_meta", None) or {})
            em["_governance_origin"] = GOVERNANCE_ORIGIN
            proposal.evaluator_meta = em
            attach_governance_linkage(proposal, decision="pending_review")

            # 4. 去重(同 source_event_id + fingerprint 已存在 → 跳过落盘)
            try:
                fp = compute_fingerprint(proposal.to_dict())
                if store.exists_similar(proposal.source_event_id or "", fp):
                    deduped += 1
                    continue
            except Exception:
                pass  # 去重失败不阻断提案落盘(重复 pending 无害)

            store.save(proposal)
            created.append(str(getattr(proposal, "id", "") or ""))

        return {
            "events": created,
            "metrics": {
                "records_read": extracted_raw,
                "records_evaluated": evaluated_count,
                "proposals_created": len(created),
                "proposals_deduped": deduped,
                "proposal_ids": created,
            },
        }


__all__ = [
    "SelfModelCycleTask",
    "build_self_model_proposal",
    "GOVERNANCE_ORIGIN",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_BUDGET_MS",
    "DEFAULT_BATCH_LIMIT",
    "DEFAULT_PROPOSAL_STORE_PATH",
    "DEFAULT_GROWTH_HISTORY_PATH",
]
