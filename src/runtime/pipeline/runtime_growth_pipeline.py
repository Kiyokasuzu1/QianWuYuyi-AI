# -*- coding: utf-8 -*-
"""
src/runtime/pipeline/runtime_growth_pipeline.py

Phase 6.0 Runtime Growth Integration: Runtime Growth Pipeline

端到端运行闭环：
    Experience
      ↓
    Memory
      ↓
    Reflection
      ↓
    GrowthProposal
      ↓
    Lifecycle
      ↓
    Personality

职责：
- 编排现有组件（不修改）
- 提供一站式 run_cycle()
- 提供 step-by-step 调试接口
- 不持有 Authority 引用
- 异常隔离，每步失败不影响后续

设计原则：
- 不修改 RuntimeCore 核心逻辑
- 不引入 EventBus
- 所有依赖通过参数注入（adapter/manager）
- 单步可独立运行
- 提供 pipeline snapshot 用于审计
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.memory.atomic_write import atomic_write_json, backup_corrupt_file, get_path_lock

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# Pipeline Stages
# ============================================================

class PipelineStage(str, Enum):
    """Pipeline 阶段标识"""
    EXPERIENCE = "experience"        # 原始经验
    MEMORY = "memory"                # 写入记忆
    REFLECTION = "reflection"        # 反思洞察
    PROPOSAL = "proposal"            # 生成 GrowthProposal
    LIFECYCLE = "lifecycle"          # 审批与生命周期
    PERSONALITY = "personality"      # 应用于人格
    COMPLETED = "completed"          # 闭环完成
    FAILED = "failed"                # 失败


class StageStatus(str, Enum):
    """单步状态"""
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class StageResult:
    """单步执行结果"""
    stage: str = PipelineStage.EXPERIENCE.value
    status: str = StageStatus.PENDING.value
    started_at: str = ""
    finished_at: str = ""
    duration_ms: float = 0.0
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineRun:
    """单次 Pipeline 运行的完整快照"""
    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex[:10]}")
    started_at: str = field(default_factory=_now_iso)
    finished_at: str = ""
    total_duration_ms: float = 0.0
    current_stage: str = PipelineStage.EXPERIENCE.value
    final_status: str = StageStatus.PENDING.value
    stages: List[StageResult] = field(default_factory=list)
    proposal_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_duration_ms": round(self.total_duration_ms, 3),
            "current_stage": self.current_stage,
            "final_status": self.final_status,
            "stages": [s.to_dict() for s in self.stages],
            "proposal_id": self.proposal_id,
            "metadata": self.metadata,
        }


# ============================================================
# Runtime Growth Pipeline
# ============================================================

class RuntimeGrowthPipeline:
    """
    Phase 6.0 Runtime Growth Pipeline

    用法：
        pipeline = RuntimeGrowthPipeline(
            memory_adapter=...,
            growth_adapter=...,
            approval_manager=...,
            personality_adapter=...,
        )
        run = pipeline.run_cycle(experience=exp, auto_approve=True)
        # 或分步
        run = pipeline.start_run(experience=exp)
        pipeline.step_memory(run)
        pipeline.step_reflection(run)
        pipeline.step_proposal(run)
        pipeline.step_lifecycle(run, auto_approve=True)
        pipeline.step_personality(run)

    所有步骤异常被隔离，不会破坏 Pipeline 状态。
    """

    def __init__(
        self,
        memory_adapter: Optional[Any] = None,
        growth_adapter: Optional[Any] = None,
        approval_manager: Optional[Any] = None,
        personality_adapter: Optional[Any] = None,
        lifecycle_manager: Optional[Any] = None,
        reflection_engine: Optional[Any] = None,
        self_model_adapter: Optional[Any] = None,
        history_path: Optional[str] = None,
        auto_record_lifecycle: bool = True,
    ):
        """
        Args:
            memory_adapter: MemoryAdapter
            growth_adapter: GrowthAdapter
            approval_manager: ApprovalManager
            personality_adapter: PersonalityAdapter
            lifecycle_manager: ProposalLifecycleManager（可选，approval_manager 内部已用）
            reflection_engine: 反思引擎（可选，缺省时跳过反思阶段）
            self_model_adapter: Phase 6.1 — SelfModelAdapter（可选，缺省时跳过 SelfModel 子步骤）
            history_path: 持久化路径
            auto_record_lifecycle: 是否在 Pipeline 内自动记录 lifecycle（一般 False，由 approval_manager 负责）
        """
        self._memory = memory_adapter
        self._growth = growth_adapter
        self._approval = approval_manager
        self._personality = personality_adapter
        self._lifecycle = lifecycle_manager
        self._reflection = reflection_engine
        self._self_model_adapter = self_model_adapter

        self._history_path = Path(history_path) if history_path else None
        if self._history_path:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)

        self._auto_record_lifecycle = auto_record_lifecycle

        # 内存中的运行历史
        self._runs: List[PipelineRun] = []
        self._max_runs = 200  # 与项目其他组件一致

    # ============================================================
    # 一站式运行
    # ============================================================

    def run_cycle(
        self,
        experience: Any = None,
        auto_approve: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PipelineRun:
        """
        一站式运行整个 Pipeline。

        Args:
            experience: RuntimeExperience（可选，None 时跳过 Experience/Memory 阶段）
            auto_approve: 是否自动批准（默认 False，需要外部调用 approval_manager.approve_proposal）
            metadata: 附加元数据
        """
        run = self.start_run(experience=experience, metadata=metadata)
        self.step_memory(run)
        self.step_reflection(run)
        self.step_proposal(run)
        self.step_lifecycle(run, auto_approve=auto_approve)
        self.step_personality(run)
        self.finish_run(run)
        return run

    # ============================================================
    # 逐步接口
    # ============================================================

    def start_run(
        self,
        experience: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PipelineRun:
        """启动一次 Pipeline 运行"""
        run = PipelineRun(metadata=metadata or {})
        if experience is not None:
            # 缓存 experience 用于后续步骤
            run.metadata["_experience"] = experience
            stage = StageResult(
                stage=PipelineStage.EXPERIENCE.value,
                status=StageStatus.OK.value,
                started_at=_now_iso(),
                finished_at=_now_iso(),
                inputs={"experience_id": getattr(experience, "experience_id", "")},
                outputs={
                    "experience_id": getattr(experience, "experience_id", ""),
                    "trigger_type": getattr(experience, "trigger_type", ""),
                    "action_type": getattr(experience, "action_type", ""),
                },
            )
            run.stages.append(stage)
            run.proposal_id = ""  # 稍后填充
        self._runs.append(run)
        # 限制 runs 大小
        if len(self._runs) > self._max_runs:
            self._runs = self._runs[-self._max_runs:]
        logger.info(f"Pipeline start: {run.run_id}")
        return run

    def step_memory(self, run: PipelineRun) -> StageResult:
        """Step 2: Experience → Memory"""
        stage = self._init_stage(PipelineStage.MEMORY.value)
        run.current_stage = PipelineStage.MEMORY.value
        try:
            experience = self._extract_experience(run)
            if experience is None:
                stage.status = StageStatus.SKIPPED.value
                stage.outputs["reason"] = "no_experience"
            elif self._memory is None:
                stage.status = StageStatus.SKIPPED.value
                stage.outputs["reason"] = "no_memory_adapter"
            else:
                ok = self._memory.store_experience(experience)
                stage.outputs["stored"] = bool(ok)
                stage.status = StageStatus.OK.value if ok else StageStatus.FAILED.value
        except Exception as e:
            stage.status = StageStatus.FAILED.value
            stage.error = str(e)
            logger.error(f"Pipeline {run.run_id} memory step failed: {e}")
        self._finalize_stage(run, stage)
        return stage

    def step_reflection(self, run: PipelineRun) -> StageResult:
        """Step 3: Memory → Reflection"""
        stage = self._init_stage(PipelineStage.REFLECTION.value)
        run.current_stage = PipelineStage.REFLECTION.value
        try:
            if self._reflection is None:
                # 退化为：直接从经验生成一个最小 ReflectionInsight
                experience = self._extract_experience(run)
                if experience is None:
                    stage.status = StageStatus.SKIPPED.value
                    stage.outputs["reason"] = "no_experience"
                else:
                    insight = self._minimal_reflection_from_experience(experience)
                    stage.outputs["insight_id"] = insight.insight_id
                    stage.outputs["pattern"] = insight.pattern_detected
                    stage.status = StageStatus.OK.value
                    # 把 insight 暂存到 run.metadata
                    run.metadata["reflection_insight"] = insight
            else:
                # 使用外部 reflection_engine
                result = self._reflection.reflect()
                if hasattr(result, "insight_id"):
                    stage.outputs["insight_id"] = result.insight_id
                    run.metadata["reflection_insight"] = result
                stage.status = StageStatus.OK.value
        except Exception as e:
            stage.status = StageStatus.FAILED.value
            stage.error = str(e)
            logger.error(f"Pipeline {run.run_id} reflection step failed: {e}")
        self._finalize_stage(run, stage)
        return stage

    def step_proposal(self, run: PipelineRun) -> StageResult:
        """Step 4: Reflection → GrowthProposal"""
        stage = self._init_stage(PipelineStage.PROPOSAL.value)
        run.current_stage = PipelineStage.PROPOSAL.value
        try:
            insight = run.metadata.get("reflection_insight")
            if insight is None:
                stage.status = StageStatus.SKIPPED.value
                stage.outputs["reason"] = "no_insight"
            elif self._growth is None:
                stage.status = StageStatus.SKIPPED.value
                stage.outputs["reason"] = "no_growth_adapter"
            else:
                ok = self._growth.store_insight(insight)
                proposals = self._growth.list_proposals(status="proposed", limit=1)
                if proposals:
                    run.proposal_id = proposals[0].id
                    stage.outputs["proposal_id"] = run.proposal_id
                    stage.status = StageStatus.OK.value
                else:
                    stage.status = StageStatus.FAILED.value
                    stage.outputs["reason"] = "no_proposal_created"
        except Exception as e:
            stage.status = StageStatus.FAILED.value
            stage.error = str(e)
            logger.error(f"Pipeline {run.run_id} proposal step failed: {e}")
        self._finalize_stage(run, stage)
        return stage

    def step_lifecycle(
        self,
        run: PipelineRun,
        auto_approve: bool = False,
    ) -> StageResult:
        """Step 5: Proposal → Lifecycle（审批 + 生命周期）"""
        stage = self._init_stage(PipelineStage.LIFECYCLE.value)
        run.current_stage = PipelineStage.LIFECYCLE.value
        try:
            if not run.proposal_id:
                stage.status = StageStatus.SKIPPED.value
                stage.outputs["reason"] = "no_proposal"
            elif self._approval is None:
                stage.status = StageStatus.SKIPPED.value
                stage.outputs["reason"] = "no_approval_manager"
            elif auto_approve:
                record = self._approval.approve_proposal(
                    proposal_id=run.proposal_id,
                    reason="pipeline_auto_approve",
                    actor="runtime_pipeline",
                )
                if record is None:
                    stage.status = StageStatus.FAILED.value
                    stage.outputs["reason"] = "approve_returned_none"
                else:
                    stage.outputs["record_id"] = record.record_id
                    stage.outputs["action"] = record.action
                    stage.status = StageStatus.OK.value
                # 记录 lifecycle state
                if self._lifecycle is not None:
                    try:
                        run.metadata["lifecycle_state"] = self._lifecycle.get_state(run.proposal_id)
                    except Exception:
                        pass
            else:
                # 不自动批准，但记录当前 lifecycle 状态
                if self._lifecycle is not None:
                    try:
                        run.metadata["lifecycle_state"] = self._lifecycle.get_state(run.proposal_id)
                    except Exception:
                        pass
                stage.status = StageStatus.OK.value
                stage.outputs["action"] = "awaiting_external_approval"
        except Exception as e:
            stage.status = StageStatus.FAILED.value
            stage.error = str(e)
            logger.error(f"Pipeline {run.run_id} lifecycle step failed: {e}")
        self._finalize_stage(run, stage)
        return stage

    def step_personality(self, run: PipelineRun) -> StageResult:
        """Step 6: Lifecycle → Personality（应用 proposal）→ SelfModel（Phase 6.1）
        
        Phase 6.1: 在 Personality 应用成功后，构造 PersonalityChangeRequest 并调用
        self_model_adapter.apply_pcr()。SelfModel 失败不会影响 Personality。
        """
        stage = self._init_stage(PipelineStage.PERSONALITY.value)
        run.current_stage = PipelineStage.PERSONALITY.value
        try:
            if not run.proposal_id:
                stage.status = StageStatus.SKIPPED.value
                stage.outputs["reason"] = "no_proposal"
            elif self._personality is None:
                stage.status = StageStatus.SKIPPED.value
                stage.outputs["reason"] = "no_personality_adapter"
            elif self._growth is None:
                stage.status = StageStatus.SKIPPED.value
                stage.outputs["reason"] = "no_growth_adapter"
            else:
                proposal = self._find_proposal(run.proposal_id)
                if proposal is None:
                    stage.status = StageStatus.FAILED.value
                    stage.outputs["reason"] = "proposal_not_found"
                else:
                    # 仅当 lifecycle 状态为 approved/accepted 时应用
                    state = run.metadata.get("lifecycle_state", "approved")
                    if state not in ("approved", "applied"):
                        stage.status = StageStatus.SKIPPED.value
                        stage.outputs["reason"] = f"lifecycle_state:{state}_not_ready"
                        stage.outputs["lifecycle_state"] = state
                    else:
                        envelope = self._personality.apply_proposal(
                            proposal,
                            actor="runtime_pipeline",
                            mark_approved=True,
                        )
                        stage.outputs["envelope"] = envelope
                        stage.status = (
                            StageStatus.OK.value
                            if envelope.get("applied")
                            else StageStatus.SKIPPED.value
                        )

                        # Phase 6.1: 在 personality 应用成功后调用 self_model_adapter
                        if envelope.get("applied") and self._self_model_adapter is not None:
                            self_model_result = self._invoke_self_model(proposal, envelope, run)
                            stage.outputs["self_model"] = self_model_result
        except Exception as e:
            stage.status = StageStatus.FAILED.value
            stage.error = str(e)
            logger.error(f"Pipeline {run.run_id} personality step failed: {e}")
        self._finalize_stage(run, stage)
        return stage

    def _invoke_self_model(
        self,
        proposal: Any,
        personality_envelope: Dict[str, Any],
        run: PipelineRun,
    ) -> Dict[str, Any]:
        """
        Phase 6.1: 构造 PersonalityChangeRequest 并调用 self_model_adapter.apply_pcr。
        
        异常隔离：SelfModel 失败不影响人格修改。
        """
        try:
            # 构造 PCR dict（供 self_model_adapter.apply_pcr 消费）
            source_proposal_id = getattr(proposal, "id", "")
            evaluator_meta = getattr(proposal, "evaluator_meta", None) or {}
            pcr = {
                "request_id": f"pcr_{run.run_id}",
                "source_proposal_id": source_proposal_id,
                "source_insight_id": run.metadata.get("reflection_insight", None) and getattr(
                    run.metadata.get("reflection_insight"), "insight_id", ""
                ) or "",
                "timestamp": _now_iso(),
                "evolution_record": personality_envelope,
                "growth_records": [],  # Personality 阶段已在 envelope 体现
                "confidence": float(getattr(proposal, "confidence", 0.5) or 0.5),
                "evidence_count": len(getattr(proposal, "evidence_ids", []) or []),
                "evaluator_meta": evaluator_meta,
                "requires_validation": False,
                "reason": "pipeline_step_personality",
            }
            # 注入 pcr 到 metadata，便于审计
            run.metadata["last_pcr"] = pcr
            # 调用 self_model_adapter
            result = self._self_model_adapter.apply_pcr(pcr, actor="runtime_pipeline")
            return result
        except Exception as e:
            # 异常隔离
            logger.warning(
                f"Pipeline {run.run_id} self_model 子步骤失败（已隔离，不影响人格）: {e}"
            )
            return {
                "applied": False,
                "self_model_updated": False,
                "note": "self_model_isolated_exception",
                "errors": [str(e)],
                "warnings": [],
            }

    def finish_run(self, run: PipelineRun) -> None:
        """完成一次 Pipeline 运行"""
        run.finished_at = _now_iso()
        # 计算总时长
        if run.stages:
            start = run.stages[0].started_at or run.started_at
            try:
                start_dt = datetime.fromisoformat(start.rstrip("Z"))
                end_dt = datetime.fromisoformat(run.finished_at.rstrip("Z"))
                run.total_duration_ms = (end_dt - start_dt).total_seconds() * 1000
            except Exception:
                pass

        # 综合状态
        statuses = [s.status for s in run.stages]
        if any(s == StageStatus.FAILED.value for s in statuses):
            run.final_status = StageStatus.FAILED.value
            run.current_stage = PipelineStage.FAILED.value
        else:
            run.final_status = StageStatus.OK.value
            run.current_stage = PipelineStage.COMPLETED.value

        # 持久化
        if self._history_path:
            try:
                self._save_runs()
            except Exception as e:
                logger.warning(f"Pipeline 持久化失败: {e}")

        logger.info(
            f"Pipeline finish: {run.run_id} status={run.final_status} "
            f"duration={run.total_duration_ms:.1f}ms proposal={run.proposal_id}"
        )

    # ============================================================
    # 查询 / 审计
    # ============================================================

    def get_recent_runs(self, limit: int = 20) -> List[PipelineRun]:
        return list(reversed(self._runs[-limit:]))

    def get_run(self, run_id: str) -> Optional[PipelineRun]:
        for r in self._runs:
            if r.run_id == run_id:
                return r
        return None

    def get_snapshot(self) -> Dict[str, Any]:
        """Pipeline 状态快照"""
        recent = self.get_recent_runs(limit=50)
        total = len(self._runs)
        ok_count = sum(1 for r in self._runs if r.final_status == StageStatus.OK.value)
        failed_count = sum(1 for r in self._runs if r.final_status == StageStatus.FAILED.value)
        # Phase 6.1: 统计 self_model 触发的 run
        self_model_triggered = sum(
            1 for r in self._runs
            if any(
                (s.stage == PipelineStage.PERSONALITY.value and "self_model" in (s.outputs or {}))
                for s in r.stages
            )
        )
        return {
            "enabled": True,
            "total_runs": total,
            "ok_runs": ok_count,
            "failed_runs": failed_count,
            "ok_rate": round(ok_count / total, 4) if total > 0 else 0.0,
            "memory_adapter": self._memory is not None,
            "growth_adapter": self._growth is not None,
            "approval_manager": self._approval is not None,
            "personality_adapter": self._personality is not None,
            "lifecycle_manager": self._lifecycle is not None,
            "reflection_engine": self._reflection is not None,
            "self_model_adapter": self._self_model_adapter is not None,  # Phase 6.1
            "self_model_triggered_runs": self_model_triggered,  # Phase 6.1
            "recent_runs": [r.to_dict() for r in recent[:5]],
        }

    def clear_runs(self) -> int:
        n = len(self._runs)
        self._runs = []
        if self._history_path:
            try:
                self._save_runs()
            except Exception:
                pass
        return n

    # ============================================================
    # 内部工具
    # ============================================================

    def _init_stage(self, stage_name: str) -> StageResult:
        return StageResult(
            stage=stage_name,
            status=StageStatus.RUNNING.value,
            started_at=_now_iso(),
        )

    def _finalize_stage(self, run: PipelineRun, stage: StageResult) -> None:
        stage.finished_at = _now_iso()
        try:
            s = datetime.fromisoformat(stage.started_at.rstrip("Z"))
            e = datetime.fromisoformat(stage.finished_at.rstrip("Z"))
            stage.duration_ms = round((e - s).total_seconds() * 1000, 3)
        except Exception:
            stage.duration_ms = 0.0
        run.stages.append(stage)

    def _extract_experience(self, run: PipelineRun) -> Optional[Any]:
        # 优先使用 start_run 缓存的 experience
        if "_experience" in run.metadata:
            return run.metadata["_experience"]
        if not run.stages:
            return None
        for s in run.stages:
            if s.stage == PipelineStage.EXPERIENCE.value:
                eid = s.outputs.get("experience_id")
                if eid and self._memory is not None:
                    try:
                        for exp in self._memory.get_recent_experiences(limit=50):
                            if exp.experience_id == eid:
                                return exp
                    except Exception:
                        pass
        return None

    def _minimal_reflection_from_experience(self, experience: Any) -> Any:
        """当无 reflection_engine 时，构造一个最简 ReflectionInsight"""
        from src.contracts.experience_schema import ReflectionInsight
        # 检测简单模式
        pattern = ""
        if hasattr(experience, "result") and experience.result is not None:
            if not experience.result.success:
                pattern = "low_success_rate"
            elif not experience.result.response_received:
                pattern = "low_user_response"
            else:
                pattern = "baseline"
        else:
            pattern = "no_result"

        return ReflectionInsight(
            experience_ids=[getattr(experience, "experience_id", "")],
            insight_type="pattern",
            summary=f"Pipeline auto-reflection for {pattern}",
            pattern_detected=pattern,
            pattern_frequency=1,
            confidence=0.4,
            used_llm=False,
        )

    def _find_proposal(self, proposal_id: str) -> Optional[Any]:
        try:
            for p in self._growth.list_proposals(limit=1000):
                if p.id == proposal_id:
                    return p
        except Exception:
            return None
        return None

    def _save_runs(self) -> None:
        if not self._history_path:
            return
        try:
            runs_data = []
            for r in self._runs[-self._max_runs:]:
                d = r.to_dict()
                # 过滤不可序列化的 metadata（_experience 等非 dict 对象）
                meta = d.get("metadata") or {}
                clean_meta: Dict[str, Any] = {}
                for k, v in meta.items():
                    try:
                        json.dumps(v, default=str)
                        clean_meta[k] = v
                    except (TypeError, ValueError):
                        # 非可序列化对象：仅保留 id / 字符串表示
                        if hasattr(v, "experience_id"):
                            clean_meta[k] = {"_id": getattr(v, "experience_id", ""), "_type": type(v).__name__}
                        elif hasattr(v, "id"):
                            clean_meta[k] = {"_id": getattr(v, "id", ""), "_type": type(v).__name__}
                        else:
                            clean_meta[k] = {"_repr": str(v)[:200], "_type": type(v).__name__}
                d["metadata"] = clean_meta
                # 同样处理 stages 内部 outputs
                for st in d.get("stages", []) or []:
                    st_meta = st.get("outputs") or {}
                    for sk, sv in list(st_meta.items()):
                        try:
                            json.dumps(sv, default=str)
                        except (TypeError, ValueError):
                            st[sk] = {"_repr": str(sv)[:200], "_type": type(sv).__name__}
                runs_data.append(d)
            data = {"version": "1.0", "runs": runs_data}
            # V1.1: 锁 + 原子写（替换裸 open("w") 截断写）
            with get_path_lock(str(self._history_path)):
                atomic_write_json(str(self._history_path), data, default=str)
        except Exception as e:
            logger.error(f"Pipeline 保存失败: {e}")

    def load_runs(self) -> int:
        """从文件加载历史"""
        if not self._history_path or not self._history_path.exists():
            return 0
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for r in data.get("runs", []):
                stages = [
                    StageResult(**s) for s in r.get("stages", [])
                ]
                run = PipelineRun(
                    run_id=r.get("run_id", ""),
                    started_at=r.get("started_at", _now_iso()),
                    finished_at=r.get("finished_at", ""),
                    total_duration_ms=r.get("total_duration_ms", 0.0),
                    current_stage=r.get("current_stage", PipelineStage.COMPLETED.value),
                    final_status=r.get("final_status", StageStatus.OK.value),
                    stages=stages,
                    proposal_id=r.get("proposal_id", ""),
                    metadata=r.get("metadata", {}),
                )
                self._runs.append(run)
            return len(self._runs)
        except Exception as e:
            logger.error(f"Pipeline 加载失败: {e}")
            backup_corrupt_file(self._history_path)
            return 0
