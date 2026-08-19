"""
Phase 4.0 — R2.5.2-C: InterestTransitionProposal（兴趣迁移提案冻结形状）

定位：只描述「这是一次什么样的兴趣转变」，绝不直接改 Personality。
本模块不读取 PersonalityState；不产生 change items；不输出任何 prompt。

三种 proposal_mode（Gate 里冻结枚举，R2.5.2-C 不允许扩）：
  1) reinforce            — 重复出现已知 growth_signal 类别 → 该兴趣被增强
  2) new_interest_emerging — 出现历史中从未见过的 growth_signal 类别 → 视为新兴趣浮现
  3) gradual_transition    — （旧信号下降 or 历史长期缺失）+（新信号上升）→ 判定为兴趣迁移，
                             永远是 gradual，不会 abrupt。

冻结字段（不多不少，Gate C-1 强制 exact check）：
  id                          interest_tr_{hex12}
  proposal_mode               Literal["reinforce", "new_interest_emerging", "gradual_transition"]
  current_growth_signal       当前 evaluator 给出的信号（如 creative_activity_interest）
  previous_growth_signals     历史中出现过的 unique growth_signal 列表
  from_interest               mode=gradual_transition 时填写 {signal: estimated_strength_estimate}，否则 {}
  to_interest                 mode=gradual_transition 时填写 {signal: estimated_strength_estimate}，
                              mode=new_interest_emerging 时填写 {new_signal: estimate}，否则 {}
  transition_mode             Literal["gradual"] （R2.5.2-C 唯一允许值；红线：不允许 abrupt）
  confidence                  0~1（analyzer 自身的判断置信度，不是 evaluator.confidence）
  evidence_memory_ids         支撑本次判断的 memory_id 列表（当前 + 历史）
  created_at_iso              ISO 时间戳
  status                      Literal["pending"]（R2.5.2-C 永远不 applied / approved）
  reason_text                 简短解释（供 audit / Approval UI 看）

红线 R2.5.2-C 冻结不得违反：
  1) 不读取 Personality，不写 interest.trait.before/after（ProposalManager 才做 ChangeItem）
  2) gradual_transition 时，永远 mode="gradual"，不允许把旧兴趣直接清零
  3) status 唯一允许 "pending"
  4) 字段集合固定，不偷偷加 extra
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Literal, TypedDict, List, Optional
import uuid


InterestTransitionMode = Literal["reinforce", "new_interest_emerging", "gradual_transition"]
TransitionExecutionMode = Literal["gradual"]
InterestTransitionStatus = Literal["pending"]


class InterestTransitionProposal(TypedDict, total=False):
    id: str
    proposal_mode: InterestTransitionMode
    current_growth_signal: str
    previous_growth_signals: List[str]
    from_interest: Dict[str, float]
    to_interest: Dict[str, float]
    transition_mode: TransitionExecutionMode
    confidence: float
    evidence_memory_ids: List[str]
    created_at_iso: str
    status: InterestTransitionStatus
    reason_text: str


FROZEN_KEYS: tuple = (
    "id",
    "proposal_mode",
    "current_growth_signal",
    "previous_growth_signals",
    "from_interest",
    "to_interest",
    "transition_mode",
    "confidence",
    "evidence_memory_ids",
    "created_at_iso",
    "status",
    "reason_text",
)

ALLOWED_PROPOSAL_MODES: tuple = ("reinforce", "new_interest_emerging", "gradual_transition")
ALLOWED_TRANSITION_MODES: tuple = ("gradual",)
ALLOWED_STATUS: tuple = ("pending",)


def build_interest_transition_proposal(
    *,
    proposal_mode: str,
    current_growth_signal: str,
    previous_growth_signals: List[str],
    from_interest: Optional[Dict[str, float]] = None,
    to_interest: Optional[Dict[str, float]] = None,
    confidence: float,
    evidence_memory_ids: List[str],
    reason_text: str,
) -> InterestTransitionProposal:
    """构建 InterestTransitionProposal。只做形状校验 + 红线防御。"""

    mode_ok = proposal_mode in ALLOWED_PROPOSAL_MODES
    if not mode_ok:
        raise ValueError(
            f"proposal_mode={proposal_mode!r} 非法，允许={ALLOWED_PROPOSAL_MODES}"
        )

    # gradual_transition 必须同时给出 from_interest 和 to_interest
    if proposal_mode == "gradual_transition":
        if not from_interest:
            raise ValueError("gradual_transition 需要 from_interest 非空")
        if not to_interest:
            raise ValueError("gradual_transition 需要 to_interest 非空")

    # new_interest_emerging 需要 to_interest 非空；from_interest 允许空
    if proposal_mode == "new_interest_emerging":
        if not to_interest:
            raise ValueError("new_interest_emerging 需要 to_interest 非空（新兴趣是什么）")

    # 红线：transition_mode 永远只有 gradual
    transition_mode: TransitionExecutionMode = "gradual"

    # 归一化 from/to（保留两位小数，拒绝负值或超 1）
    def _norm(d: Optional[Dict[str, Any]]) -> Dict[str, float]:
        result: Dict[str, float] = {}
        for k, v in (d or {}).items():
            try:
                val = float(v)
            except Exception:  # noqa: BLE001
                raise ValueError(f"{k}={v!r} 不是数值")
            if val < 0.0:
                val = 0.0
            if val > 1.0:
                val = 1.0
            result[str(k)] = round(val, 4)
        return result

    prop: InterestTransitionProposal = {
        "id": f"interest_tr_{uuid.uuid4().hex[:12]}",
        "proposal_mode": proposal_mode,  # type: ignore[typeddict-item]
        "current_growth_signal": str(current_growth_signal or ""),
        "previous_growth_signals": [str(x) for x in (previous_growth_signals or [])],
        "from_interest": _norm(from_interest or {}),
        "to_interest": _norm(to_interest or {}),
        "transition_mode": transition_mode,
        "confidence": min(max(float(confidence or 0.0), 0.0), 1.0),
        "evidence_memory_ids": [str(x) for x in (evidence_memory_ids or []) if x],
        "created_at_iso": datetime.now(timezone.utc).isoformat(),
        "status": "pending",  # type: ignore[typeddict-item]
        "reason_text": str(reason_text or ""),
    }

    # 二次形状 + 红线断言式检查
    missing = [k for k in FROZEN_KEYS if k not in prop]
    if missing:
        raise ValueError(f"InterestTransitionProposal 缺少冻结字段: {missing}")
    extra = [k for k in prop.keys() if k not in FROZEN_KEYS]
    if extra:
        raise ValueError(f"InterestTransitionProposal 有未登记字段: {extra}")
    if prop["proposal_mode"] == "gradual_transition" and prop["transition_mode"] != "gradual":
        raise ValueError("红线：gradual_transition 必须 transition_mode=gradual")
    if prop["status"] != "pending":
        raise ValueError("红线：InterestTransitionProposal 只能是 pending 状态")
    return prop
