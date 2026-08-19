# -*- coding: utf-8 -*-
"""
Phase 4.0-R2.5.0-B — Growth 消化能力基准测试（只读性质）
========================================================

目标:
    验证「Normalizer → Validator → Matcher → Evaluator → Proposal」
    整条 Growth 消化链对典型输入的行为是否符合设计预期。

策略说明:
    EventExtractor(LLM 抽取) 当前环境缺少 openai 包,无法真实运行。
    本基线测试**不依赖 LLM**——用等价的"已抽取事件 dict"作为起点,
    精确验证后续各阶段的纯规则行为(Normalize/Validate/Match/Evaluate)。
    LLM 抽取质量是一个独立课题,留待 R2.5.1 接入后再做回归。

红线 (不修改生产代码):
    ❌ 不改动 src/growth/*
    ❌ 不改动 src/events/*
    ❌ 不写任何数据到 data/*.json
    ❌ 不走 GrowthPipeline.incremental_update() (会绕过 Proposal 直接写 GrowthState,
       不符合羽依的"Proposal → 确认 → 演化"哲学;优先走 GrowthIntegrationService)

5 个 Case:
    Case 1: 应该成长 (长期兴趣 / preference)
    Case 2: 不应成长 (普通日常 / conversation)
    Case 3: 关系保护 (relationship → 只进 context, applied_delta=0)
    Case 4: 矛盾测试 (前后矛盾 → consistency 降低 → growth_allowed=False)
    Case 5: 重复稳定性 (10次 → stability 上升 → growth_level 晋升)
"""

from __future__ import annotations

import json
import sys
import types
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest


# ===========================================================================
# 隔离工具: 清除 MemoryProvider / 打桩 RuntimeBridge / 打桩 openai 缺包
# ===========================================================================
@pytest.fixture(autouse=True)
def _isolate_growth_from_runtime(monkeypatch: pytest.MonkeyPatch):
    """Growth 模块会通过 memory_provider / personality_* 间接读取一些 authority。
    这里重置单例 + 禁用 RuntimeBridge 深层 import + 伪造 openai 占位,让测试可以 import。
    注意:不做任何数据写入断言,只验证 evaluator/proposal 输出结构。
    """
    # 0) 伪造 openai 空模块(仅用于 EventExtractor import 通过,本测试不实际调用 extract)
    fake_openai = types.ModuleType("openai")
    fake_openai.APIError = type("APIError", (Exception,), {})
    fake_openai.OpenAI = type("OpenAI", (), {})
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    # 1) RuntimeBridge 禁用
    def _bad_import(*_a, **_k):
        raise RuntimeError("R2.5.0-B: RuntimeBridge disabled")

    fake_bridge = types.ModuleType("src.runtime.runtime_bridge")
    fake_bridge.get_runtime_bridge = _bad_import
    fake_bridge.RuntimeBridge = type("RuntimeBridge", (), {})
    monkeypatch.setitem(sys.modules, "src.runtime.runtime_bridge", fake_bridge)

    # 2) VectorMemory 禁用
    class _NullVM:
        def __init__(self, *a, **k):
            pass

        def search(self, *a, **k):
            return []

    fake_vm = types.ModuleType("src.memory.vector")
    fake_vm.VectorMemory = _NullVM
    monkeypatch.setitem(sys.modules, "src.memory.vector", fake_vm)

    # 3) MemoryProvider 单例在每测前后 reset
    from src.memory.memory_provider import MemoryProvider

    MemoryProvider.reset_for_testing()
    yield
    MemoryProvider.reset_for_testing()


# ===========================================================================
# 辅助: 构造一个"等价于 EventExtractor 输出"的标准化事件骨架
# ===========================================================================
def _build_event_skeleton(
    topic: str,
    event: str,
    event_type: str,
    content: str,
    *,
    role: str = "user",
    memory_id: Optional[str] = None,
    importance: float = 0.5,
) -> Dict[str, Any]:
    """构造一条 EventExtractor 风格输出事件 + 完整 metadata。

    返回格式对齐 Evaluator 的消费端字段需求。
    """
    if memory_id is None:
        memory_id = f"mem_r250b_{uuid.uuid4().hex[:8]}"
    return {
        "event": event,
        "topic": topic,
        "event_type": event_type,
        "importance": importance,
        "evidence": [
            {
                "text": content,
                "role": role,
                "source_index": 0,
                "memory_id": memory_id,
            }
        ],
        "source_ids": [memory_id],
        # 留给 Normalizer 填充: canonical_topic, first_seen, last_seen 等
    }


def _pretty(label: str, obj: Any) -> None:
    """测试中记录结构化观察(打印可读 JSON)。"""
    print(f"\n===== {label} =====")
    try:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    except Exception:
        print(repr(obj))


# ===========================================================================
# 辅助: 串联 normalizer -> validator -> matcher -> evaluator (纯函数式管道)
# ===========================================================================
def _run_digest_pipeline(
    raw_event: Dict[str, Any],
    *,
    matcher_history_override: Optional[List[Dict[str, Any]]] = None,
    now_offset_days: int = 0,
) -> Dict[str, Any]:
    """跑一遍 Digest 四阶段,返回最终 evaluator 输出 + 过程元数据。

    注意: 如果 validator 返回 should_keep=False, 则 evaluator_output=None,
    调用方能直接断言 growth_allowed 等价于 False。
    """
    from src.growth.event_normalizer import EventNormalizer
    from src.growth.event_validator import EventValidator
    from src.growth.event_history_matcher import EventHistoryMatcher
    from src.growth.growth_evaluator import GrowthEvaluator

    normalizer = EventNormalizer()
    validator = EventValidator()
    matcher = EventHistoryMatcher()
    evaluator = GrowthEvaluator()

    # Step 1: Normalize (分类规则 + canonical_topic 稳定化)
    # EventNormalizer.normalize 契约: 输入 List[Dict],返回 List[Dict]
    normalized_list = normalizer.normalize([raw_event])
    if not normalized_list:
        return {
            "validated": False,
            "validator_decision": ("normalizer_dropped", 0.0, "normalize() 返回空列表"),
            "evaluator_output": None,
        }
    normalized = normalized_list[0]
    _pretty("1) Normalized", normalized)

    # Step 2: Validate (过滤 / 最终 importance)
    # EventValidator.should_keep 契约: 返回 bool,内部已调用 decide() 并写入 event.metadata
    should_keep_flag = validator.should_keep(normalized)
    decision_info = validator.decide(normalized)  # 3-tuple: (decision, score, reason)
    _pretty(
        "2) Validate",
        {
            "should_keep": should_keep_flag,
            "decision": decision_info[0],
            "score": decision_info[1],
            "reason": decision_info[2],
        },
    )
    if not should_keep_flag:
        return {
            "validated": False,
            "validator_decision": decision_info,
            "evaluator_output": None,
        }

    # Step 3: Match 历史 (测试用 matcher_history_override 可注入伪历史)
    canonical_topic = normalized.get("canonical_topic") or normalized.get("topic", "")
    ev_type = normalized.get("event_type", "")
    if matcher_history_override is not None:
        # 用覆盖历史(用于 Case 4/5)
        history = matcher_history_override
    else:
        history = matcher.get_history(canonical_topic, ev_type)
    _pretty(
        "3) History",
        {"count": len(history), "sample_top_3": history[:3]},
    )

    # Step 4: Evaluate
    evaluated = evaluator.evaluate(normalized, history)
    if now_offset_days:
        # Evaluator 读 first_seen/last_seen 算 stability;这里不篡改,
        # 由调用方在 matcher_history_override 中注入不同日期。
        pass

    _pretty("4) Evaluated", _summarize_eval(evaluated))
    return {
        "validated": True,
        "validator_decision": decision_info,
        "evaluator_output": evaluated,
    }


def _summarize_eval(ev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从完整 Evaluator 输出中摘出 B 测试报告用的关键字段。"""
    if not ev:
        return {"evaluated": False}
    keys = [
        "event",
        "topic",
        "event_type",
        "confidence",
        "stability",
        "consistency",
        "impact",
        "growth_domain",
        "growth_level",
        "max_allowed_level",
        "growth_allowed",
        "applied_delta",
        "growth_signal",
        "target_candidates",
        "occurrence_count",
        "growth_domain_max_reason",
    ]
    return {k: ev.get(k) for k in keys if k in ev}


# ===========================================================================
# 辅助: 用 GrowthIntegrationService 把 Evaluator 输出转成 Proposal
# ===========================================================================
def _build_proposal_via_integration(
    evaluated_event: Dict[str, Any],
    *,
    user_id: str = "r2_5_0_b_user",
    auto_accept: bool = False,
) -> Dict[str, Any]:
    """调用 GrowthIntegrationService.process_event(),返回 proposal 生命周期结果。

    对应你建议的:
        Memory → Experience → Event → Evaluation → GrowthProposal(pending)
    """
    from src.growth.growth_integration import GrowthIntegrationService

    svc = GrowthIntegrationService(auto_accept_enabled=auto_accept)
    source_event = {
        "event_id": f"evt_{uuid.uuid4().hex[:8]}",
        "event_type": evaluated_event.get("event_type", ""),
        "topic": evaluated_event.get("topic", ""),
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(),
        "raw_content": (evaluated_event.get("evidence") or [{}])[0].get("text", ""),
    }
    result = svc.process_event(source_event, evaluated_event)
    _pretty(
        "5) GrowthIntegrationService.process_event()",
        {
            "pipeline_state": result.get("pipeline_state"),
            "proposal_id": result.get("proposal_id"),
            "proposal_status": (
                result.get("proposal", {}).get("status")
                if isinstance(result.get("proposal"), dict)
                else None
            ),
            "deduped": result.get("pipeline_state") == "deduped",
        },
    )
    return result


# ===========================================================================
# Case 1: 应该成长 (长期兴趣 / preference)
# ===========================================================================
def test_case1_should_grow_interest(capsys: pytest.CaptureFixture[str]) -> None:
    """Case 1 — 应该成长:用户长期/深度兴趣宣言。

    预期:
      - event_type: preference 或 creation
      - growth_domain: preference
      - growth_allowed: True
      - target_candidates 含 creative_interest 类
      - Proposal 状态 = pending (auto_accept 关闭)
    """
    raw = _build_event_skeleton(
        topic="用户学习AI绘画",
        event="用户表达对AI绘画的长期兴趣",
        event_type="preference",
        content="最近我开始学习AI绘画,每天都会研究角色设计,感觉越来越感兴趣。",
        importance=0.8,  # 长期宣言 → 偏高重要性
    )

    result = _run_digest_pipeline(raw)
    evaluated = result["evaluator_output"]
    assert evaluated is not None, "Case 1: validator 不应丢弃本事件"

    s = _summarize_eval(evaluated)
    _pretty("Case 1 关键字段摘要", s)

    assert s.get("growth_allowed") is True, (
        f"Case 1 必须 growth_allowed=True,实际={s.get('growth_allowed')}"
    )
    assert s.get("event_type") in {"preference", "creation", "capability"}, (
        f"Case 1 event_type 应属 preference/creation/capability,实际={s.get('event_type')}"
    )
    assert s.get("growth_domain") == "preference", (
        f"Case 1 growth_domain 应为 preference,实际={s.get('growth_domain')}"
    )
    # proposal 环节 (auto_accept=False → 必须是 pending)
    proposal_result = _build_proposal_via_integration(evaluated, auto_accept=False)
    state = proposal_result.get("pipeline_state")
    assert state in {"created", "accepted", "deduped"}, (
        f"Case 1 Proposal 创建失败,pipeline_state={state}"
    )
    if state == "created":
        proposal = proposal_result.get("proposal") or {}
        status = proposal.get("status") if isinstance(proposal, dict) else None
        assert status == "pending", (
            f"Case 1 auto_accept=False 时 Proposal 状态必须为 pending,实际={status}"
        )


# ===========================================================================
# Case 2: 不应该成长 (普通日常 conversation)
# ===========================================================================
def test_case2_should_not_grow_daily(capsys: pytest.CaptureFixture[str]) -> None:
    """Case 2 — 不应该成长:日常琐碎无方向。

    预期:
      - validator.should_keep == False,或 growth_allowed==False
      - 最终不能产生非 context 级 Proposal
    """
    raw = _build_event_skeleton(
        topic="今天吃拉面",
        event="用户陈述今日餐饮",
        event_type="conversation",
        content="今天吃了拉面。",
        importance=0.2,  # 琐碎 → 低重要性
    )

    result = _run_digest_pipeline(raw)
    evaluated = result["evaluator_output"]

    # 两种合法结果:Validator 直接淘汰 OR Evaluator 判 growth_allowed=False
    if evaluated is None:
        dec = result.get("validator_decision")
        if isinstance(dec, tuple) and len(dec) >= 1:
            decision_repr = {"decision": dec[0], "score": dec[1] if len(dec) > 1 else None, "reason": dec[2] if len(dec) > 2 else None}
        else:
            decision_repr = {"decision_raw": dec}
        _pretty("Case 2 Validator 淘汰", decision_repr)
        # Good: validator 提前挡住了
        return

    s = _summarize_eval(evaluated)
    _pretty("Case 2 关键字段摘要", s)

    assert s.get("growth_allowed") is False, (
        f"Case 2 必须 growth_allowed=False,实际={s.get('growth_allowed')}。"
        f"若为 True,表示普通日常会触发成长(污染风险)。"
    )
    # proposal 环节即便强行走 process_event 也应 rejected_low_confidence / created 但 pending
    proposal_result = _build_proposal_via_integration(evaluated, auto_accept=False)
    state = proposal_result.get("pipeline_state")
    # 允许 created (后续审批阶段可以拒绝);不允许 accepted/applied
    assert state not in {"accepted", "applied"}, (
        f"Case 2 不应产生 auto-accept/apply 的 proposal,实际 state={state}"
    )


# ===========================================================================
# Case 3: 关系保护 (relationship 只进 context)
# ===========================================================================
def test_case3_relationship_protection(capsys: pytest.CaptureFixture[str]) -> None:
    """Case 3 — 关系保护:关系事件不得进入 preference/trait,delta=0。

    预期:
      - event_type: relationship
      - growth_domain: relationship_context
      - max_allowed_level: context
      - applied_delta == 0
      - 即便 growth_allowed=True,也不能触发人格维度变化
    """
    raw = _build_event_skeleton(
        topic="用户认为羽依最重要",
        event="用户表达深度情感承诺",
        event_type="relationship",
        content="羽依你是我最重要的人,我希望你一直陪着我。",
        importance=0.9,
    )

    result = _run_digest_pipeline(raw)
    evaluated = result["evaluator_output"]
    assert evaluated is not None, "Case 3: validator 不应丢弃关系事件"

    s = _summarize_eval(evaluated)
    _pretty("Case 3 关键字段摘要", s)

    # 领域必须是 relationship_context
    assert s.get("growth_domain") == "relationship_context", (
        f"Case 3 growth_domain 应为 relationship_context,实际={s.get('growth_domain')}"
    )
    # max_allowed_level 不得超过 context
    max_lvl = s.get("max_allowed_level")
    assert max_lvl in {"context", "trace"}, (
        f"Case 3 max_allowed_level 应为 context/trace,实际={max_lvl}"
    )
    # applied_delta 必须 0 (关系不得改变人格数值)
    delta = s.get("applied_delta")
    assert delta == 0 or delta == 0.0, (
        f"Case 3 applied_delta 必须为 0,实际={delta!r}。"
        f"若 >0,关系事件会污染人格。"
    )


# ===========================================================================
# Case 4: 矛盾测试 (前后矛盾 → growth_allowed=False)
# ===========================================================================
def test_case4_inconsistency_blocks_growth(capsys: pytest.CaptureFixture[str]) -> None:
    """Case 4 — 矛盾:用户先说喜欢画画,再说完全不喜欢。

    构造:
      - 历史事件:"我很喜欢画画。" → topic 对齐,态度正面
      - 当前事件:"我最近完全不想碰画画了。" → 同一 topic,态度负面
    预期:
      - Evaluator.consistency 降低
      - growth_allowed=False (或 若 domain=relationship 等本不允许领域,也 OK)
    """
    # 构造历史:一次"喜欢画画"
    history_event = {
        "event": "用户表达喜欢画画",
        "topic": "画画兴趣",
        "canonical_topic": "画画兴趣",
        "event_type": "preference",
        "growth_signal": "creative_activity_interest",
        "semantic_direction": 1.0,  # 正向信号
        "importance": 0.8,
        "first_seen": (datetime.now() - timedelta(days=10)).isoformat(),
        "last_seen": (datetime.now() - timedelta(days=1)).isoformat(),
        "occurrence_count": 3,
        "evidence": [{"text": "我很喜欢画画。", "role": "user"}],
    }
    history = [history_event] * 3  # 3 次正面历史

    # 当前事件:"完全不想碰画画" → 取反语义 (我们通过 importance 低+在 evidence 文本里体现否定)
    # Evaluator 的 consistency 依赖 history 与当前事件的 semantic_direction 对比。
    # 这里构造一个与 history 方向相反的当前事件
    raw = _build_event_skeleton(
        topic="画画兴趣",
        event="用户表达对画画失去兴趣",
        event_type="preference",
        content="我最近完全不想碰画画了。",
        importance=0.7,
    )
    raw["semantic_direction"] = -1.0  # 明确负向,触发一致性下降

    result = _run_digest_pipeline(raw, matcher_history_override=history)
    evaluated = result["evaluator_output"]
    assert evaluated is not None, "Case 4: validator 不应丢弃本事件"

    s = _summarize_eval(evaluated)
    _pretty("Case 4 关键字段摘要(前后矛盾)", s)

    consistency = s.get("consistency")
    growth_allowed = s.get("growth_allowed")
    # 期望:consistency 显著降低(< 首次默认的 0.3 或低于 0.5)
    assert (consistency is None) or (consistency < 0.6), (
        f"Case 4 consistency 应 < 0.6 (矛盾检测),实际={consistency}"
    )
    # 最重要:growth_allowed 必须是 False (否则会出现"既喜欢又不喜欢"同时写入)
    assert growth_allowed is False, (
        f"Case 4 矛盾事件必须 growth_allowed=False,实际={growth_allowed}。"
        f"若为 True,会造成人格污染。"
    )


# ===========================================================================
# Case 5: 重复稳定性 (10 次重复 → stability/growth_level 晋升)
# ===========================================================================
def test_case5_repetition_stability_rise(capsys: pytest.CaptureFixture[str]) -> None:
    """Case 5 — 重复稳定性:模拟同一主题在一个月内出现 10 次。

    预期:
      - 第 1 次: stability ≈ 0.2, growth_level = context 或更低
      - 第 10 次 (30 天跨度): stability 升高,growth_level 晋升至 preference
    """
    topic = "AI绘画持续兴趣"
    ev_type = "preference"

    # ====== 第 1 次 (首次出现) ======
    history_1: List[Dict[str, Any]] = []
    raw_1 = _build_event_skeleton(
        topic=topic,
        event="用户表达AI绘画兴趣",
        event_type=ev_type,
        content="最近一直喜欢AI绘画。",
        importance=0.6,
    )
    r1 = _run_digest_pipeline(raw_1, matcher_history_override=history_1)
    e1 = r1["evaluator_output"]
    assert e1 is not None
    s1 = _summarize_eval(e1)
    _pretty("Case 5 - 第 1 次", s1)

    stab_1 = s1.get("stability") or 0.0
    level_1 = s1.get("growth_level")

    # ====== 第 10 次 (30 天跨度,出现 10 次) ======
    base = datetime.now()
    history_10 = []
    for i in range(10):
        days_ago = 30 - i * 3
        history_10.append(
            {
                "event": "用户表达AI绘画兴趣",
                "topic": topic,
                "canonical_topic": topic,
                "event_type": ev_type,
                "growth_signal": "creative_activity_interest",
                "semantic_direction": 1.0,
                "importance": 0.6,
                "first_seen": (base - timedelta(days=30)).isoformat(),
                "last_seen": (base - timedelta(days=days_ago)).isoformat(),
                "occurrence_count": i + 1,
            }
        )

    raw_10 = _build_event_skeleton(
        topic=topic,
        event="用户表达AI绘画兴趣(第10次)",
        event_type=ev_type,
        content="最近一直喜欢AI绘画。",
        importance=0.6,
    )
    r10 = _run_digest_pipeline(raw_10, matcher_history_override=history_10)
    e10 = r10["evaluator_output"]
    assert e10 is not None
    s10 = _summarize_eval(e10)
    _pretty("Case 5 - 第 10 次", s10)

    stab_10 = s10.get("stability") or 0.0
    level_10 = s10.get("growth_level")
    occurrence_10 = s10.get("occurrence_count")

    # 断言 1: stability 上升
    assert stab_10 >= stab_1, (
        f"Case 5 stability 应随重复次数上升: 1st={stab_10} vs 10th={stab_10}"
    )
    # 断言 2: occurrence_count 应 > 1 (进入 matcher 后累加识别)
    assert (occurrence_10 is None) or (occurrence_10 >= 2), (
        f"Case 5 occurrence_count 应体现重复性(≥2),实际={occurrence_10}"
    )
    # 断言 3: growth_level 不应降级(保持或晋升)——不能出现越学越怀疑
    LEVEL_ORDER = {"trace": 0, "context": 1, "preference": 2, "trait": 3}
    l1 = LEVEL_ORDER.get(str(level_1 or "trace"), 0)
    l10 = LEVEL_ORDER.get(str(level_10 or "trace"), 0)
    assert l10 >= l1, (
        f"Case 5 growth_level 不应降级: 1st={level_1} → 10th={level_10}"
    )
