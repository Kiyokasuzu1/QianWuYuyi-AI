# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/tasks/growth_cycle.py

P2.8 Phase D-2.0: Growth Cycle 周期任务(Thin Trigger)。

职责(任务书 Step 2):
    时间触发
        ↓
    复用 GrowthPipeline 组件链(extract/normalize/validate/track/evaluate)
        ↓
    对 growth_allowed 事件构建 GrowthProposal(canonical schema)
        ↓
    保存 status=pending 提案(ProposalStore, 治理链等待审批)
        ↓
    写 LifecycleEvent 审计(proposal ids 进 produced_events)

红线(与任务书一致):
- 禁止 apply / approve / accept: 不调用 GrowthEngine.apply/apply_proposal、
  ProposalManager.accept/apply、PersonalityAdapter 任何写入方法
- 禁止修改人格: pipeline 组件只读使用(extract/normalize/validate/track/evaluate),
  绝不调用 apply_relationship / mark_processed_batch / event_memory.refresh
- 提案仅落盘, 零业务状态变更
- 不消费记忆(不 mark_processed), 不影响聊天链路的事件供给; 重复提案由
  ProposalStore.exists_similar + compute_fingerprint 去重

注入设计(测试/实验注入 fake, 生产默认惰性构造):
- pipeline: GrowthPipeline 实例(组件宿主; 仅在 execute 时惰性构造)
- proposal_store: ProposalStore 实例(默认 data/proposals/proposals.jsonl,
  与治理链 _park_proposal_for_review 同一落盘位置)
- proposal_builder: 默认绑定 pipeline._build_proposal_from_evaluated——
  复用与聊天路径完全一致的提案语义(ChangeItem 权重/证据/评估元数据),
  避免在周期任务中复制一份可能漂移的治理语义
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from src.contracts.lifecycle_event_schema import TASK_TYPE_GROWTH_CYCLE
from src.runtime.lifecycle.lifecycle_decision import AfterInterval
from src.runtime.lifecycle.lifecycle_task import BaseLifecycleTask

DEFAULT_INTERVAL_SECONDS = 86400.0  # 默认每日一次
DEFAULT_BUDGET_MS = 15000  # extract 可能含 LLM 调用, 预算放宽
DEFAULT_BATCH_LIMIT = 50
DEFAULT_PROPOSAL_STORE_PATH = "data/proposals/proposals.jsonl"
OWNER = "p2.8.d2"
TASK_ID = "growth.cycle"

# 提案来源标记(canonical schema 无顶层 source 字段, 来源写入 evaluator_meta)
GOVERNANCE_ORIGIN = "growth_cycle_task"


def _build_default_pipeline():
    """惰性构造 GrowthPipeline(组件宿主; 不触发任何业务写入)。"""
    from src.growth.pipeline import GrowthPipeline

    return GrowthPipeline()


def _build_default_store(proposal_store_path: str):
    """惰性构造 ProposalStore(与治理链 parking 同一 append-only JSONL)。"""
    from src.growth.proposal_store import ProposalStore

    return ProposalStore(proposal_store_path)


class GrowthCycleTask(BaseLifecycleTask):
    """成长周期任务: 事件评估 → pending 提案落盘 → 审计。

    绝不调用 apply/approve/accept 链路。
    """

    TASK_TYPE = TASK_TYPE_GROWTH_CYCLE
    IDEMPOTENCY_BUCKET_SECONDS = 86400  # 幂等键按自然日分桶

    def __init__(
        self,
        pipeline: Any = None,
        proposal_store: Any = None,
        proposal_builder: Optional[Callable[[dict, dict], Any]] = None,
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
            display_name="Growth Proposal Cycle",
            condition=AfterInterval(interval),
            budget_ms=budget_ms,
            interval_seconds=interval,
            max_concurrent=1,
        )
        self._pipeline = pipeline
        self._store = proposal_store
        self._proposal_builder = proposal_builder
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
        """事件评估 → pending 提案落盘(零 apply)。

        返回 dict 由 BaseLifecycleTask.run_safely 包装为 SUCCESS LifecycleResult;
        "events" 键进入 LifecycleResult.produced_events → LifecycleEvent 审计的
        produced_events(proposal ids 可追踪)。
        """
        pipeline = self._pipeline
        if pipeline is None:
            pipeline = _build_default_pipeline()
        store = self._store
        if store is None:
            store = _build_default_store(self._proposal_store_path)

        # 1. 提取 + 标准化 + 验证(全部只读)
        events = list(pipeline.extractor.extract(self._batch_limit) or [])
        extracted_raw = len(events)
        if not events:
            return {
                "metrics": {
                    "events_extracted": 0,
                    "events_evaluated": 0,
                    "growth_allowed": 0,
                    "proposals_created": 0,
                    "proposals_deduped": 0,
                },
            }
        events = pipeline.normalizer.normalize(events)
        events = pipeline.validator.validate(events)
        if not events:
            return {
                "metrics": {
                    "events_extracted": extracted_raw,
                    "events_evaluated": 0,
                    "growth_allowed": 0,
                    "proposals_created": 0,
                    "proposals_deduped": 0,
                },
            }

        from src.growth.event_identity_resolver import resolve_event_identity

        for e in events:
            resolve_event_identity(e)
        events = pipeline.matcher.track(events, False)

        # 2. 逐事件评估 → 构建提案 → pending 落盘
        from src.growth.mutation_adapter import attach_governance_linkage
        from src.growth.proposal_store import compute_fingerprint

        builder = self._proposal_builder
        if builder is None:
            builder = getattr(pipeline, "_build_proposal_from_evaluated", None)
        if not callable(builder):
            raise RuntimeError("proposal_builder 不可用: pipeline 未提供 _build_proposal_from_evaluated")

        created: List[str] = []
        evaluated_count = 0
        allowed_count = 0
        deduped = 0

        for event in events:
            if not event.get("is_first_occurrence", True):
                continue
            apply_flag = event.get("metadata", {}).get("validator_apply", True)
            if not apply_flag:
                continue

            canonical_topic = event.get("canonical_topic", event.get("topic", ""))
            event_type = event.get("event_type", "")
            history_events = pipeline.matcher.get_history(canonical_topic, event_type)

            # 不传 experience_meaning: 周期任务走规则映射, 零 LLM 语义依赖
            evaluated = pipeline.evaluator.evaluate(event, history_events)
            evaluated_count += 1
            if not evaluated.get("growth_allowed", False):
                continue
            allowed_count += 1

            proposal = builder(evaluated, event)
            if proposal is None:
                continue

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
                pass  # 去重失败不阻断提案落盘(治理语义: 重复 pending 无害)

            store.save(proposal)
            created.append(str(getattr(proposal, "id", "") or ""))

        return {
            "events": created,
            "metrics": {
                "events_extracted": extracted_raw,
                "events_evaluated": evaluated_count,
                "growth_allowed": allowed_count,
                "proposals_created": len(created),
                "proposals_deduped": deduped,
                "proposal_ids": created,
            },
        }


__all__ = [
    "GrowthCycleTask",
    "GOVERNANCE_ORIGIN",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_BUDGET_MS",
    "DEFAULT_BATCH_LIMIT",
    "DEFAULT_PROPOSAL_STORE_PATH",
]
