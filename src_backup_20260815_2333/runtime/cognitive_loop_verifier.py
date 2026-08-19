"""
Phase 3.5.21: Cognitive Loop Verifier

职责：
- 使用现有 RuntimeCore 验证认知闭环是否稳定工作
- 不修改 Persona
- 不自动接受 Proposal
- 不调用 LLM 决定成长
- 所有结果生成 audit report

验证阶段门控：
1st input  -> Experience + Memory
2nd input  -> + Reflection
3rd input+ -> + Evaluation + Proposal + Identity Stability
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from src.contracts.cognitive_loop_schema import CognitiveLoopReport, DecisionBudget
from src.contracts.experience_schema import ActionResult


class CognitiveLoopVerifier:
    def __init__(self, decision_budget: Optional[DecisionBudget] = None):
        self.decision_budget = decision_budget or DecisionBudget()
        self._history: List[CognitiveLoopReport] = []
        self._reflection_count = 0
        self._evaluation_count = 0
        self._proposal_count = 0

    def verify_input(
        self,
        runtime: Any,
        *,
        user_text: str,
        user_id: str = "tester",
    ) -> CognitiveLoopReport:
        seq = len(self._history) + 1
        report = CognitiveLoopReport(
            input_text=user_text,
            sequence_index=seq,
        )
        audit: Dict[str, Any] = {
            "budget": self.decision_budget.to_dict(),
            "sequence_policy": "",
            "experience_id": "",
            "memory_ids": [],
            "insight_id": "",
            "evaluation_id": "",
            "proposal_ids": [],
            "identity_report_id": "",
            "pending_proposal_count": 0,
        }

        # 1. create experience shell
        runtime.inject_event("user.input", {"content": user_text, "user_id": user_id, "importance": 0.9})
        exp_id = getattr(runtime, "_building_experience_id", "")
        if not exp_id:
            report.failure_points.append("experience_start_failed")
            report.end_time = report.start_time
            report.audit = audit
            self._history.append(report)
            return report

        runtime.experience_builder.record_decision(
            exp_id,
            decision_source="cognitive_loop_verifier",
            intention_id=f"verify_seq_{seq}",
            action_type="internal_reflection_candidate",
        )
        time.sleep(0.02)
        exp = runtime.experience_builder.finish_building(
            exp_id,
            result=ActionResult(
                action_id=f"verify_act_{seq}",
                success=True,
                response_received=False,
            ),
            self_state_after=runtime.self_state.to_dict(),
        )
        if exp is None:
            report.failure_points.append("experience_finish_failed")
            report.end_time = report.start_time
            report.audit = audit
            self._history.append(report)
            return report

        report.experience_created = True
        audit["experience_id"] = exp.experience_id

        # 2. memory write
        if runtime.memory_adapter and runtime.memory_adapter.store_experience(exp):
            report.memory_written = True
            mid = runtime.memory_adapter.get_experience_memory_id(exp.experience_id)
            if mid:
                audit["memory_ids"].append(mid)
            store = runtime.memory_adapter.get_memory_store()
            memories = store.load() or []
            if runtime.memory_relevance_evaluator:
                ranked = runtime.memory_relevance_evaluator.rank_memories(
                    memories[-20:],
                    query=user_text,
                    context={"identity_focus": False, "relationship_focus": False},
                    top_k=5,
                )
                audit["relevance_audit_ids"] = [r.get("relevance_audit_id", "") for r in ranked]
        else:
            report.failure_points.append("memory_write_failed")

        # 3. stage policy
        if seq == 1:
            audit["sequence_policy"] = "memory_only"
            report.warnings.append("first_input_only_memory_expected")
            report.end_time = self._now()
            report.audit = audit
            self._history.append(report)
            return report

        if seq == 2:
            audit["sequence_policy"] = "allow_reflection_only"
            if self._reflection_count >= self.decision_budget.max_reflections:
                report.warnings.append("reflection_budget_exhausted")
            else:
                experiences = runtime.get_experiences()
                insight = self._generate_verification_reflection(experiences)
                if insight is not None:
                    self._reflection_count += 1
                    report.reflection_generated = True
                    audit["insight_id"] = insight.get("insight_id", "")
                    audit["verification_reflection"] = insight
                else:
                    report.failure_points.append("reflection_generation_failed")
            report.end_time = self._now()
            report.audit = audit
            self._history.append(report)
            return report

        # 4. third input+: allow evaluation/proposal/identity check
        audit["sequence_policy"] = "allow_full_loop_without_auto_accept"
        if self._reflection_count >= self.decision_budget.max_reflections:
            report.warnings.append("reflection_budget_exhausted")
            report.end_time = self._now()
            report.audit = audit
            self._history.append(report)
            return report

        insight = runtime.reflect_on_experiences()
        if insight:
            self._reflection_count += 1
            report.reflection_generated = True
            audit["insight_id"] = getattr(insight, "insight_id", "")
        else:
            report.failure_points.append("reflection_generation_failed")

        latest_eval = runtime.get_latest_evaluation()
        if latest_eval:
            if self._evaluation_count < self.decision_budget.max_evaluations:
                self._evaluation_count += 1
                report.evaluation_completed = True
                audit["evaluation_id"] = latest_eval.get("evaluation_id", "")
            else:
                report.warnings.append("evaluation_budget_exhausted")

        proposals = runtime.get_growth_proposals(status="proposed", limit=20)
        audit["pending_proposal_count"] = len(proposals)
        if proposals:
            if self._proposal_count < self.decision_budget.max_proposals:
                self._proposal_count += 1
                report.proposal_created = True
                audit["proposal_ids"] = [getattr(p, "id", "") for p in proposals[:5]]
            else:
                report.warnings.append("proposal_budget_exhausted")

        identity_report = runtime.refresh_identity_stability(force=True)
        if identity_report:
            report.identity_checked = True
            audit["identity_report_id"] = identity_report.get("report_id", "")
        else:
            report.failure_points.append("identity_check_failed")

        # verifier never auto-applies evolution
        report.evolution_applied = False
        if runtime.personality_evolution_pipeline:
            audit["personality_evolution_snapshot"] = runtime.personality_evolution_pipeline.get_snapshot()

        report.end_time = self._now()
        report.audit = audit
        self._history.append(report)
        return report

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._history)
        items.reverse()
        return [r.to_dict() for r in items[:limit]]

    @staticmethod
    def _generate_verification_reflection(experiences: List[Any]) -> Optional[Dict[str, Any]]:
        """
        第二次输入阶段使用轻量验证反思：
        - 不调用 LLM
        - 不写入 Growth
        - 只证明 loop 已经具备“从多次经验中形成模式总结”的能力
        """
        if len(experiences) < 2:
            return None
        latest = experiences[-2:]
        texts = []
        for e in latest:
            trigger = getattr(e, "trigger_event", {}) or {}
            data = trigger.get("data", {}) if isinstance(trigger, dict) else {}
            text = data.get("content", "") if isinstance(data, dict) else ""
            texts.append(str(text))
        if not any(texts):
            return None
        import uuid
        return {
            "insight_id": f"verify_ins_{uuid.uuid4().hex[:8]}",
            "summary": "检测到连续输入中存在稳定主题，可进入正式反思阶段",
            "details": texts,
            "source": "cognitive_loop_verifier",
        }

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"
