# -*- coding: utf-8 -*-
"""Phase C.4.6.5 — 通过 GrowthIntegrationService 触发真实 proposal 生成。

设计:
- 不修改 Orchestrator / GrowthEvaluator / ProposalManager / PersonalityAdapter
- 基于真实 memory record(mem_20260802222601_1b27)构造 source_event + evaluator_output
- 通过 GrowthIntegrationService.process_event() 触发完整 Growth 评估链路
- 写入 data/proposals/proposals.jsonl(append-only,latest-wins)

约束:
- 不自动 approve(由人工 review)
- 不直接写 SelfModel 数据
- 严格使用现有模块接口
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("d:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI")
sys.path.insert(0, str(ROOT))

from src.growth.proposal_store import ProposalStore
from src.growth.growth_integration import GrowthIntegrationService
from src.contracts import growth_schema


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    print(f"[{now_iso()}] === Phase C.4.6.5 GrowthIntegration trigger ===")

    # 1) 读取真实 memory 记录
    mem_path = ROOT / "data" / "memory.json"
    with open(mem_path, "r", encoding="utf-8") as f:
        memories = json.load(f)

    target = None
    for m in reversed(memories):
        if (
            m.get("role") == "user"
            and m.get("metadata", {}).get("memory_type") == "user_shared"
            and "我今天完成了一个重要的项目" in m.get("content", "")
        ):
            target = m
            break

    if target is None:
        print(f"[{now_iso()}] ERROR: target memory not found")
        return 1

    memory_id = target["id"]
    print(f"[{now_iso()}] TARGET MEMORY: {memory_id}")
    print(json.dumps(target, ensure_ascii=False, indent=2))

    # 2) 构造 source_event
    source_event = {
        "id": f"evt_{memory_id}",
        "type": "user_preference",
        "event_type": "preference",
        "canonical_topic": "项目成就感",
        "topic": "项目成就感",
        "user_id": target.get("user_id", "366648462"),
        "importance": 0.85,  # 用户明确表达重要情感
        "first_seen": target.get("timestamp"),
        "last_seen": now_iso(),
        "metadata": {
            "memory_id": memory_id,
            "memory_type": "user_shared",
            "source": "orchestrator_real_interaction",
        },
        "evidence": [
            {
                "text": target["content"],
                "role": "user",
                "memory_id": memory_id,
            }
        ],
        "source_ids": [memory_id],
    }
    print(f"[{now_iso()}] SOURCE_EVENT:")
    print(json.dumps(source_event, ensure_ascii=False, indent=2))

    # 3) 构造 evaluator_output
    # 注意:proposed_changes 必须在 ALLOWED_PERSONALITY_PATHS 白名单内
    # 见 src/personality/personality_adapter.py
    evaluator_output = {
        "proposed_changes": [
            growth_schema.ChangeItem(
                path="warmth",
                before=0.7,
                after=0.72,  # 微量增加,符合 MAX_SINGLE_EVENT_DELTA = 0.05
                reason=f"用户在真实交互中表达成就感,关联记忆 {memory_id},显示情感投入",
            )
        ],
        "confidence": 0.85,  # ≥ threshold (0.8)
        "evidence_ids": [memory_id],  # 必填,防止 rejected_no_evidence
        "growth_level": "context",
        "action_scope": "personality",
        "reason": f"用户完成重要项目,表达成就感 — 来自真实 memory {memory_id}",
        "narrative": f"用户在 memory {memory_id} 中表达重要项目完成的成就感,体现对自我成长话题的兴趣",
    }
    print(f"[{now_iso()}] EVALUATOR_OUTPUT:")
    def _change_to_dict(c):
        try:
            from dataclasses import asdict
            return asdict(c)
        except Exception:
            return {
                "path": getattr(c, "path", ""),
                "before": getattr(c, "before", None),
                "after": getattr(c, "after", None),
                "reason": getattr(c, "reason", None),
            }
    print(json.dumps(
        {k: v if not isinstance(v, list) else [_change_to_dict(c) for c in v] for k, v in evaluator_output.items()},
        ensure_ascii=False, indent=2
    ))

    # 4) 记录初始 proposal 状态
    pstore = ProposalStore()
    initial_total = pstore.count()
    initial_pending = pstore.count(status="pending")
    print(f"[{now_iso()}] Initial: total={initial_total} pending={initial_pending}")

    # 5) 调用 GrowthIntegrationService.process_event()
    service = GrowthIntegrationService(
        config={
            "auto_accept_enabled": False,  # 强制关闭 auto_accept
            "confidence_threshold": 0.8,
        }
    )

    print(f"[{now_iso()}] Calling GrowthIntegrationService.process_event()...")
    result = service.process_event(
        source_event=source_event,
        evaluator_output=evaluator_output,
    )
    print(f"[{now_iso()}] process_event result:")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    # 6) 检查新 proposal
    pstore_after = ProposalStore()
    after_total = pstore_after.count()
    after_pending = pstore_after.count(status="pending")
    print(f"[{now_iso()}] After: total={after_total} pending={after_pending} (delta: +{after_total - initial_total})")

    # 7) 找出新创建的 proposal
    if result.get("proposal_id"):
        new_proposal_id = result["proposal_id"]
        print(f"[{now_iso()}] NEW PROPOSAL ID: {new_proposal_id}")
        proposal = pstore_after.load(new_proposal_id)
        if proposal is not None:
            print(f"[{now_iso()}] NEW PROPOSAL DETAIL:")
            print(json.dumps(proposal.to_dict(), ensure_ascii=False, indent=2, default=str))
        else:
            print(f"[{now_iso()}] WARNING: proposal {new_proposal_id} not loaded by ProposalStore")

    # 8) 输出:最近的 pending proposal(可能有多个)
    print(f"[{now_iso()}] === Recent pending proposals (last 5) ===")
    pending_list = pstore_after.list(status="pending", limit=5)
    for p in pending_list:
        d = p.to_dict()
        print(f"[{now_iso()}] - id={d.get('id')} status={d.get('status')} source_event_id={d.get('source_event_id')} confidence={d.get('confidence')} evidence_count={len(d.get('evidence_ids') or [])}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
