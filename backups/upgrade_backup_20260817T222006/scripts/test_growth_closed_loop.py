"""
Growth System 闭环测试脚本
==========================

目标：验证 Growth System 是否可以完成一次真实的成长闭环测试
测试事件：test_growth_runtime_001

约束：
- 不修改真实 self_model.json
- 不修改真实 growth_state.json
- 不修改真实 proposals 存储
- 全部使用临时目录

运行：python scripts/test_growth_closed_loop.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import shutil
import traceback
from pathlib import Path
from datetime import datetime
from typing import Any, Dict

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ============================================================
# 测试配置
# ============================================================
TEST_EVENT = {
    "event_id": "test_growth_runtime_001",
    "content": "用户长期反馈：羽依有时会重复提醒用户休息，希望羽依学习调整表达节奏，在关心用户的同时减少机械重复。",
    "source": "runtime_test",
}

REPORT: Dict[str, Any] = {
    "test_time": datetime.now().isoformat(),
    "test_event": TEST_EVENT,
    "path_a_pipeline": {},
    "path_b_integration": {},
    "chain_status": {},
}


def section(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def subsection(title: str):
    print(f"\n--- {title} ---")


def safe_get(obj, *keys, default=None):
    """安全获取嵌套属性"""
    cur = obj
    for k in keys:
        try:
            if isinstance(cur, dict):
                cur = cur.get(k, default)
            else:
                cur = getattr(cur, k, default)
        except Exception:
            return default
    return cur


# ============================================================
# 路径 A: GrowthPipeline 快速路径测试
# ============================================================
def test_path_a_pipeline(tmpdir: Path) -> Dict[str, Any]:
    """
    路径 A: GrowthPipeline（快速路径）
    EventExtractor → EventNormalizer → EventValidator → EventHistoryMatcher
        → GrowthEvaluator → GrowthEngine → GrowthState + GrowthRecord
    """
    section("路径 A: GrowthPipeline 快速路径测试")
    result: Dict[str, Any] = {
        "started": True,
        "components": {},
        "outputs": {},
        "data_changes": {},
        "errors": [],
    }

    try:
        # 1. 准备临时 GrowthState 路径
        tmp_growth_state_path = tmpdir / "growth_state.json"
        print(f"[准备] 临时 GrowthState 路径: {tmp_growth_state_path}")

        # 2. 初始化组件
        subsection("2.1 组件初始化")

        from src.growth.growth_state import GrowthState
        from src.growth.growth_evaluator import GrowthEvaluator
        from src.growth.growth_engine import GrowthEngine
        from src.personality.personality_resolver import PersonalityResolver
        from src.personality.relationship_state import RelationshipState
        from src.personality.personality_growth_record import PersonalityGrowthHistory

        growth_state = GrowthState(state_path=str(tmp_growth_state_path))
        print(f"  ✅ GrowthState 初始化成功")
        print(f"     初始 metrics: {growth_state.get()['metrics']}")
        result["components"]["growth_state_initial"] = dict(growth_state.get()["metrics"])

        evaluator = GrowthEvaluator()
        print(f"  ✅ GrowthEvaluator 初始化成功")

        growth_engine = GrowthEngine(state=growth_state)
        print(f"  ✅ GrowthEngine 初始化成功")

        growth_records = PersonalityGrowthHistory()
        print(f"  ✅ PersonalityGrowthHistory 初始化成功")

        relationship_state = RelationshipState()
        resolver = PersonalityResolver(
            state=growth_state,
            relationship_state=relationship_state,
        )
        print(f"  ✅ PersonalityResolver 初始化成功")
        result["components"]["all_init"] = True

        # 3. 记录 GrowthState 初始快照
        state_before = json.loads(json.dumps(growth_state.get()))

        # 4. 手动构造标准化事件（绕过 LLM EventExtractor，避免 API key 依赖）
        subsection("2.2 构造标准化事件（绕过 LLM）")
        # 构造多个重复发生的 preference 事件，模拟长期反馈
        test_events = []
        for i in range(5):
            evt = {
                "event_id": f"{TEST_EVENT['event_id']}_occurrence_{i+1}",
                "event_type": "preference",
                "topic": "表达节奏优化",
                "canonical_topic": "表达节奏优化",
                "content": TEST_EVENT["content"],
                "source": TEST_EVENT["source"],
                "importance": 0.75 if i < 4 else 0.85,  # 最后一次更重要
                "evidence": [
                    {"role": "user", "text": TEST_EVENT["content"][:50]},
                ],
                "timestamp": f"2026-08-0{i+1}T10:00:00",
            }
            test_events.append(evt)
        print(f"  构造了 {len(test_events)} 个重复 preference 事件")
        print(f"  类型: preference, 主题: 表达节奏优化, importance: 递增")

        # 5. 逐个处理：Evaluator → apply_evaluated → apply
        subsection("2.3 Evaluator + Engine 链路执行")
        history_events = []
        total_growth_records = 0
        for idx, event in enumerate(test_events):
            # Evaluator 评估
            evaluated = evaluator.evaluate(event, history_events)
            history_events.append(evaluated)

            print(f"\n  事件 {idx+1}/{len(test_events)}:")
            print(f"    confidence={evaluated.get('confidence'):.3f}, "
                  f"stability={evaluated.get('stability'):.3f}, "
                  f"consistency={evaluated.get('consistency'):.3f}")
            print(f"    growth_level={evaluated.get('growth_level')}, "
                  f"growth_allowed={evaluated.get('growth_allowed')}, "
                  f"growth_domain={evaluated.get('growth_domain')}")
            print(f"    growth_signal={evaluated.get('growth_signal')}, "
                  f"target_candidates={evaluated.get('target_candidates')}")
            print(f"    applied_delta={evaluated.get('applied_delta')}")

            # 生成 GrowthRecord
            if evaluated.get("growth_allowed"):
                record = growth_engine.apply_evaluated(evaluated)
                if record:
                    growth_records.add(record)
                    total_growth_records += 1
                    print(f"    ✅ GrowthRecord 生成: {getattr(record, 'record_id', '?')}, "
                          f"dims={getattr(record, 'affected_dimensions', {})}")

            # 更新 GrowthState 统计
            gs_result = growth_engine.apply(event)
            print(f"    GrowthState.apply: status={gs_result.get('status')}, "
                  f"mode={gs_result.get('mode')}, "
                  f"meaning={gs_result.get('meaning')}")
            delta = gs_result.get("delta", {})
            if delta:
                print(f"      delta={ {k: round(v, 5) for k, v in delta.items()} }")

        # 最后一次运行获取汇总（注意：GrowthRecord 是 TypedDict/dict，用 .get()）
        all_records = list(growth_records.all())
        output_final = {
            "growth_records": all_records,
        }
        result["components"]["evaluator_engine_direct_used"] = True
        result["outputs"]["run_final"] = {
            "event_count": len(test_events),
            "growth_record_count": len(all_records),
            "growth_records": [
                {
                    "record_id": r.get("record_id"),
                    "growth_level": r.get("growth_level"),
                    "affected_dimensions": r.get("affected_dimensions"),
                }
                for r in all_records
            ],
        }
        print(f"\n  最终成长记录: {len(all_records)} 条")
        for r in all_records:
            print(f"    - {r.get('record_id', '?')}: "
                  f"level={r.get('growth_level', '?')}, "
                  f"dims={r.get('affected_dimensions', {})}")

        # 标记链路：直接 Evaluator 测试
        result["outputs"]["evaluator_direct_success"] = True

        # 5. 检查 GrowthState 变化
        subsection("2.4 GrowthState 数据变化检查")
        state_after = json.loads(json.dumps(growth_state.get()))

        metrics_before = state_before.get("metrics", {})
        metrics_after = state_after.get("metrics", {})
        metric_changes = {}
        for k in set(list(metrics_before.keys()) + list(metrics_after.keys())):
            b = metrics_before.get(k, 0)
            a = metrics_after.get(k, 0)
            if abs(b - a) > 0.0001:
                metric_changes[k] = {"before": b, "after": a, "delta": round(a - b, 5)}

        print(f"  metrics 变化: {len(metric_changes)} 个维度")
        for k, v in metric_changes.items():
            print(f"    - {k}: {v['before']:.4f} → {v['after']:.4f} (Δ {v['delta']:+.5f})")

        milestones_before = len(state_before.get("milestones", []))
        milestones_after = len(state_after.get("milestones", []))
        processed_before = len(state_before.get("processed_events", []))
        processed_after = len(state_after.get("processed_events", []))

        print(f"  milestones: {milestones_before} → {milestones_after}")
        print(f"  processed_events: {processed_before} → {processed_after}")

        result["data_changes"] = {
            "temporary_growth_state_used": True,
            "metric_changes": metric_changes,
            "milestones_added": milestones_after - milestones_before,
            "processed_events_added": processed_after - processed_before,
            "growth_state_file_created": tmp_growth_state_path.exists(),
        }

        # 6. Personlity 解析结果
        subsection("2.5 PersonalityResolver 输出")
        personality = resolver.resolve()
        # PersonalityVector 不是 dict，是自定义 vector 类
        if personality is not None:
            # 取已知属性（不用 .keys()）
            known_attrs = ['warmth', 'gentleness', 'shyness', 'sensitivity', 'initiative',
                           'self_expression', 'care_level', 'playfulness', 'self_confidence', 'curiosity']
            personality_sample = {}
            for attr in known_attrs:
                try:
                    val = getattr(personality, attr, None)
                    if val is not None and isinstance(val, (int, float, str)):
                        personality_sample[attr] = val
                except Exception:
                    pass
            result["outputs"]["personality_sample"] = personality_sample
            print(f"  personality 属性样本: {personality_sample}")

        result["success"] = True

        # 补充 run1（与 chain_status 兼容，event_count 用 test_events 数）
        result["outputs"]["run1"] = {
            "event_count": len(test_events),
            "growth_record_count": total_growth_records,
            "has_personality": personality is not None,
        }

    except Exception as e:
        result["success"] = False
        result["errors"].append({
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        })
        print(f"  ❌ 错误: {type(e).__name__}: {e}")
        traceback.print_exc()

    return result


# ============================================================
# 路径 B: GrowthIntegrationService 完整 Proposal 链路
# ============================================================
def test_path_b_integration(tmpdir: Path) -> Dict[str, Any]:
    """
    路径 B: GrowthIntegrationService（完整 Proposal 链路）
    source_event → Evaluator → ProposalManager → ProposalStore
        → accept_proposal → apply_proposal → PersonalityAdapter
    """
    section("路径 B: GrowthIntegrationService 完整 Proposal 链路")
    result: Dict[str, Any] = {
        "started": True,
        "components": {},
        "outputs": {},
        "data_changes": {},
        "errors": [],
    }

    try:
        # 1. 准备临时存储路径
        subsection("3.1 临时存储准备")
        tmp_proposals_path = tmpdir / "proposals.jsonl"
        tmp_growth_history_dir = tmpdir / "growth_history"
        tmp_growth_history_dir.mkdir(exist_ok=True)

        print(f"  临时 ProposalStore 路径: {tmp_proposals_path}")

        # 2. 初始化组件
        subsection("3.2 组件初始化")
        from src.growth.proposal_store import ProposalStore
        from src.growth.proposal_manager import ProposalManager
        from src.growth.growth_integration import GrowthIntegrationService
        from src.personality.personality_growth_record import PersonalityGrowthHistory
        from src.personality.personality_adapter import PersonalityAdapter
        from src.contracts import growth_schema

        store = ProposalStore(path=str(tmp_proposals_path))
        print(f"  ✅ ProposalStore 初始化 (空)")

        growth_history = PersonalityGrowthHistory()
        print(f"  ✅ PersonalityGrowthHistory 初始化")

        personality_adapter = PersonalityAdapter()
        print(f"  ✅ PersonalityAdapter 初始化")

        manager = ProposalManager(
            store=store,
            personality_adapter=personality_adapter,
            growth_history=growth_history,
            config={
                "auto_accept_enabled": False,
                "confidence_threshold": 0.8,
            },
        )
        print(f"  ✅ ProposalManager 初始化 (auto_accept=False, threshold=0.8)")

        integration = GrowthIntegrationService(
            proposal_manager=manager,
            growth_history=growth_history,
            config={
                "auto_accept_enabled": False,
                "confidence_threshold": 0.8,
            },
        )
        print(f"  ✅ GrowthIntegrationService 初始化")

        result["components"]["all_initialized"] = True

        # 3. 准备 source_event 和 evaluator_output
        subsection("3.3 构造输入数据")
        source_event = {
            "id": TEST_EVENT["event_id"],
            "type": "preference",
            "topic": "表达节奏优化",
            "content": TEST_EVENT["content"],
            "source": TEST_EVENT["source"],
            "importance": 0.85,
            "event_type": "preference",
        }

        # 构造 ChangeItem：表达节奏 → 影响 warmth （使用合法白名单 path）
        # 白名单参考：ALLOWED_PERSONALITY_PATHS in personality_adapter.py
        proposed_changes = [
            growth_schema.ChangeItem(
                path="personality.traits.warmth",
                before=0.30,
                after=0.33,  # +0.03 ≤ 0.05 MAX_SINGLE_EVENT_DELTA
                reason="用户希望关心时更自然，减少机械重复 → 微调 warmth 表达模式",
            ),
            growth_schema.ChangeItem(
                path="personality.traits.curiosity",
                before=0.25,
                after=0.28,  # +0.03 ≤ 0.05
                reason="学习调整表达节奏 → 好奇心驱动探索更好的表达方式",
            ),
        ]

        evaluator_output = {
            "proposed_changes": proposed_changes,
            "confidence": 0.88,  # > 0.8 门槛
            "evidence_ids": [
                "evt_evidence_001:重复提醒休息",
                "evt_evidence_002:用户反馈机械感",
            ],
            "growth_level": "preference",
            "reason": "用户长期反馈重复提醒问题，经多次出现形成稳定偏好",
            "narrative": "羽依注意到自己的休息提醒有时会重复，用户希望表达更自然。经过多次反馈验证，决定微调表达模式。",
            "action_scope": "personality",
            "reason_summary": "表达节奏优化偏好（preference level）",
            "pattern_detected": "mechanical_repetition_feedback",
        }

        print(f"  source_event.id: {source_event['id']}")
        print(f"  proposed_changes: {len(proposed_changes)} 项")
        for ci in proposed_changes:
            print(f"    - {ci.path}: {ci.before} → {ci.after} ({ci.reason[:30]}...)")
        print(f"  confidence: {evaluator_output['confidence']}")
        print(f"  evidence_ids: {evaluator_output['evidence_ids']}")

        # 4. Step 1: process_event（创建 proposal）
        subsection("3.4 Step 1: process_event → 创建 Proposal")
        before_count = len(store.list())
        process_result = integration.process_event(source_event, evaluator_output)
        after_count = len(store.list())

        print(f"  pipeline_state: {process_result.get('pipeline_state')}")
        print(f"  proposal_id: {process_result.get('proposal_id')}")
        print(f"  proposal 数量: {before_count} → {after_count}")
        print(f"  reasons: {process_result.get('reasons', [])}")

        result["outputs"]["step1_process_event"] = {
            "pipeline_state": process_result.get("pipeline_state"),
            "proposal_id": process_result.get("proposal_id"),
            "proposals_created": after_count - before_count,
        }

        proposal_id = process_result.get("proposal_id")
        if not proposal_id:
            raise RuntimeError("process_event 未生成 proposal_id")

        # 4b. 检查 ProposalStore 中存储的内容
        stored_proposal = store.load(proposal_id)
        if stored_proposal:
            print(f"\n  存储的 proposal 详情:")
            print(f"    id: {stored_proposal.id}")
            print(f"    status: {stored_proposal.status}")
            print(f"    source_event_id: {stored_proposal.source_event_id}")
            print(f"    confidence: {stored_proposal.confidence}")
            print(f"    proposed_changes count: {len(stored_proposal.proposed_changes)}")
            print(f"    evidence_ids: {stored_proposal.evidence_ids}")
            result["outputs"]["stored_proposal"] = {
                "id": stored_proposal.id,
                "status": stored_proposal.status,
                "confidence": stored_proposal.confidence,
                "change_count": len(stored_proposal.proposed_changes),
            }

        # 5. Step 2: accept_proposal（审核通过）
        subsection("3.5 Step 2: accept_proposal → 审核通过")
        accept_result = integration.accept_proposal(proposal_id, actor="test_runner")
        print(f"  accept status: {accept_result.get('status')}")
        print(f"  reason: {accept_result.get('reason', '')}")

        stored_after_accept = store.load(proposal_id)
        if stored_after_accept:
            print(f"  存储 proposal 新状态: {stored_after_accept.status}")
            print(f"  accepted_at: {stored_after_accept.accepted_at}")

        result["outputs"]["step2_accept"] = {
            "status": accept_result.get("status"),
            "proposal_status_after": stored_after_accept.status if stored_after_accept else None,
            "has_accepted_at": bool(stored_after_accept.accepted_at) if stored_after_accept else False,
        }

        # 5b. 检查 PersonalityGrowthHistory 是否有记录
        gh_view = integration.get_growth_history_view()
        print(f"\n  PersonalityGrowthHistory view:")
        print(f"    total_records: {gh_view.get('total_records', '?')}")
        recent = gh_view.get("recent_records", [])
        if recent:
            first = recent[0]
            print(f"    最新 record: {safe_get(first, 'record_id')}")
            print(f"      dims: {safe_get(first, 'affected_dimensions')}")

        result["outputs"]["growth_history_after_accept"] = gh_view

        # 6. Step 3: apply_proposal（应用到 TraitState）
        subsection("3.6 Step 3: apply_proposal → 应用到人格")
        apply_result = integration.apply_proposal(proposal_id, actor="test_runner")
        print(f"  apply status: {apply_result.get('status')}")

        apply_detail = apply_result.get("apply_result", {})
        print(f"  apply_result.applied: {apply_detail.get('applied')}")
        print(f"  apply_result.note: {apply_detail.get('note')}")
        print(f"  apply_result.before: {apply_detail.get('before')}")
        print(f"  apply_result.after: {apply_detail.get('after')}")
        print(f"  apply_result.evolution_record_id: {apply_detail.get('evolution_record_id')}")

        stored_after_apply = store.load(proposal_id)
        if stored_after_apply:
            print(f"\n  存储 proposal 最终状态: {stored_after_apply.status}")

        result["outputs"]["step3_apply"] = {
            "status": apply_result.get("status"),
            "apply_applied": apply_detail.get("applied"),
            "apply_note": apply_detail.get("note"),
            "apply_before": apply_detail.get("before"),
            "apply_after": apply_detail.get("after"),
            "trait_delta": {
                k: round(apply_detail.get("after", {}).get(k, 0) - apply_detail.get("before", {}).get(k, 0), 5)
                for k in set(list(apply_detail.get("before", {}).keys()) + list(apply_detail.get("after", {}).keys()))
            },
            "evolution_record_id": apply_detail.get("evolution_record_id"),
            "proposal_status_after": stored_after_apply.status if stored_after_apply else None,
            "rate_limit": apply_detail.get("rate_limit"),
            "skipped_traits": apply_detail.get("skipped_traits", []),
            "denied_traits": apply_detail.get("denied_traits", []),
        }

        # 7. 数据变化汇总
        subsection("3.7 数据变化汇总")
        # 检查 proposals.jsonl 是否有内容
        if tmp_proposals_path.exists():
            lines = [l for l in tmp_proposals_path.read_text(encoding="utf-8").split("\n") if l.strip()]
            print(f"  proposals.jsonl 行数: {len(lines)}")
            if len(lines) >= 1:
                first_obj = json.loads(lines[0])
                print(f"    第 1 行 id: {first_obj.get('id')}, status: {first_obj.get('status')}")
            if len(lines) >= 2:
                second_obj = json.loads(lines[1])
                print(f"    第 2 行 id: {second_obj.get('id')}, status: {second_obj.get('status')}")
            if len(lines) >= 3:
                third_obj = json.loads(lines[2])
                print(f"    第 3 行 id: {third_obj.get('id')}, status: {third_obj.get('status')}")

        result["data_changes"] = {
            "temporary_storage_used": True,
            "proposal_jsonl_lines": len(lines) if tmp_proposals_path.exists() else 0,
            "proposal_created": True,
            "proposal_accepted": accept_result.get("status") in ("accepted", "already_accepted"),
            "proposal_applied": apply_result.get("status") == "applied",
            "trait_changes_in_memory": apply_detail.get("applied", False),
        }

        # 8. 验证 path 白名单（尝试非法 path）
        subsection("3.8 安全验证：非法 path 拦截")
        bad_proposal = growth_schema.GrowthProposal(
            source_event_id="evt_bad_path",
            proposed_changes=[
                growth_schema.ChangeItem(
                    path="personality.core_identity.name",  # 非白名单
                    before="羽依",
                    after="测试修改",
                    reason="should be blocked",
                ),
            ],
            confidence=0.9,
            evidence_ids=["mem_x"],
        )
        bad_apply = personality_adapter.apply_proposal(bad_proposal, actor="security_test")
        print(f"  非法 path apply 结果: applied={bad_apply.get('applied')}, note={bad_apply.get('note')}")
        result["outputs"]["security_path_blocked"] = {
            "blocked": not bad_apply.get("applied"),
            "note": bad_apply.get("note"),
        }

        result["success"] = True

    except Exception as e:
        result["success"] = False
        result["errors"].append({
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        })
        print(f"  ❌ 错误: {type(e).__name__}: {e}")
        traceback.print_exc()

    return result


# ============================================================
# 主流程
# ============================================================
def main():
    section("QianWuYuyi-AI Growth System 闭环测试")
    print(f"测试时间: {datetime.now().isoformat()}")
    print(f"测试事件: {TEST_EVENT['event_id']}")
    print(f"事件内容: {TEST_EVENT['content'][:60]}...")

    with tempfile.TemporaryDirectory(prefix="yuyi_growth_test_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        print(f"\n临时目录: {tmpdir}")

        # 路径 A
        REPORT["path_a_pipeline"] = test_path_a_pipeline(tmpdir)

        # 路径 B
        REPORT["path_b_integration"] = test_path_b_integration(tmpdir)

        # 报告输出
        section("链路状态汇总")

        # 判断各链路状态
        a = REPORT["path_a_pipeline"]
        b = REPORT["path_b_integration"]

        chain_status = {
            "event_to_evaluator": {
                # 路径 A 直接构造标准事件调用 Evaluator，成功运行
                "connected": a.get("success", False) and a.get("outputs", {}).get("evaluator_direct_success", False),
                "note": "（直接调用）Standardized Event → GrowthEvaluator.evaluate()\n                         (EventExtractor 需要 LLM API，本次使用构造事件绕过)",
            },
            "evaluator_to_proposal": {
                "connected": b.get("success", False) and b.get("outputs", {}).get("step1_process_event", {}).get("proposal_id") is not None,
                "note": "Evaluator Output → ProposalManager.create_proposal → ProposalStore",
            },
            "proposal_to_storage": {
                "connected": b.get("success", False) and b.get("data_changes", {}).get("proposal_jsonl_lines", 0) > 0,
                "note": "Proposal 保存为 JSONL（append-only，3 版本 pending/accepted/applied）",
            },
            "proposal_to_accept": {
                "connected": b.get("success", False) and b.get("outputs", {}).get("step2_accept", {}).get("proposal_status_after") == "accepted",
                "note": "accept_proposal → PersonalityAdapter.build_change_request → GrowthHistory.add",
            },
            "accept_to_personality_traits": {
                "connected": b.get("success", False) and b.get("outputs", {}).get("step3_apply", {}).get("apply_applied", False),
                "note": "apply_proposal → TraitStateUpdater → EvolutionRecord → TraitState in-memory 更新",
            },
            "evaluator_to_growth_state": {
                # 注意：本次 preference 事件不在 GrowthEngine.GROWTH_MAP 中，
                # 所以 metrics 没有变化。链路本身是通的（GrowthEngine.apply 成功调用只是没匹配到规则）
                "connected": False,
                "note": "（本次未验证）GrowthEngine.apply → GrowthState.metrics 仅对 GROWTH_MAP 中事件类型生效\n                         （birth/creation/relationship/emotional_expression 等，本次 preference 不触发）",
            },
            "evaluator_to_growth_record": {
                "connected": a.get("success", False) and a.get("outputs", {}).get("run_final", {}).get("growth_record_count", 0) > 0,
                "note": "GrowthEngine.apply_evaluated → GrowthRecord → PersonalityGrowthHistory.add",
            },
            "proposal_to_selfmodel_persist": {
                # Level 4 需要的：SelfModel 持久化
                "connected": False,
                "note": "（本次未验证）TraitState 变更 → SelfModelAdapter.apply_pcr → self_model.json 落盘\n                         （本次为隔离测试环境，未验证真实持久化）",
            },
        }

        REPORT["chain_status"] = chain_status

        print("\n已连接链路：")
        connected_count = 0
        for name, info in chain_status.items():
            symbol = "✅" if info["connected"] else "❌"
            if info["connected"]:
                connected_count += 1
            print(f"  {symbol} {name:30s} → {info['note']}")

        print(f"\n总计: {connected_count}/{len(chain_status)} 链路已连接")

        # 输出最终判断
        section("最终等级判断")

        # 保存报告
        report_path = PROJECT_ROOT / "data" / "growth" / "test_growth_closed_loop_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\n详细报告已保存至: {report_path}")

        # 等级判断
        determine_level()


def determine_level():
    a = REPORT["path_a_pipeline"]
    b = REPORT["path_b_integration"]
    cs = REPORT["chain_status"]

    level = "Level 0: 只有数据结构"
    reasons = []

    # Level 1: 可以生成成长提案
    l1_ok = (
        cs.get("event_to_evaluator", {}).get("connected")
        and cs.get("evaluator_to_proposal", {}).get("connected")
    )
    if l1_ok:
        level = "Level 1: 可以生成成长提案"
        reasons.append("Event → Evaluator → Proposal 链路可运行")

    # Level 2: 可以保存成长结果
    l2_ok = l1_ok and (
        cs.get("proposal_to_storage", {}).get("connected")
        or cs.get("evaluator_to_growth_state", {}).get("connected")
        or cs.get("evaluator_to_growth_record", {}).get("connected")
    )
    if l2_ok:
        level = "Level 2: 可以保存成长结果"
        if cs.get("proposal_to_storage", {}).get("connected"):
            reasons.append("Proposal 可持久化到 JSONL")
        if cs.get("evaluator_to_growth_state", {}).get("connected"):
            reasons.append("GrowthState metrics 可更新")
        if cs.get("evaluator_to_growth_record", {}).get("connected"):
            reasons.append("GrowthRecord 可生成并写入 PersonalityGrowthHistory")

    # Level 3: 可以影响人格状态
    l3_ok = l2_ok and (
        cs.get("proposal_to_accept", {}).get("connected")
        and cs.get("accept_to_personality_traits", {}).get("connected")
    )
    if l3_ok:
        level = "Level 3: 可以影响人格状态"
        reasons.append("accept_proposal 可写入 PersonalityGrowthHistory")
        apply_detail = b.get("outputs", {}).get("step3_apply", {})
        if apply_detail.get("apply_applied"):
            reasons.append("apply_proposal 可更新 TraitState (in-memory)，有 before/after/delta")

    # Level 4: 完整闭环运行
    # 条件：不仅 in-memory，还需要持久化到 SelfModel.json（当前测试不修改真实文件，
    # 需检查是否通过 SelfModelAdapter 真正落盘 — 本测试为隔离环境未验证持久化）
    # 因此不自动判定 Level 4

    print(f"\n🎯 当前成长系统等级：{level}")
    print(f"\n判定依据：")
    for r in reasons:
        print(f"  ✅ {r}")

    # 未连接的链路
    not_connected = [k for k, v in cs.items() if not v.get("connected")]
    if not_connected:
        print(f"\n未连接/未验证链路：")
        for k in not_connected:
            print(f"  ⚠️  {k}: {cs[k]['note']}")

    # 报告错误
    all_errors = []
    if a.get("errors"):
        all_errors.extend([("路径A", e) for e in a["errors"]])
    if b.get("errors"):
        all_errors.extend([("路径B", e) for e in b["errors"]])
    if all_errors:
        print(f"\n测试过程中的错误：")
        for path, e in all_errors:
            print(f"  ❌ [{path}] {e['type']}: {e['message']}")


if __name__ == "__main__":
    main()
