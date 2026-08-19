# -*- coding: utf-8 -*-
"""Phase C.4.6.5 — Apply approved proposal to SelfModel.

设计:
- 不修改 SelfModelAdapter / SelfModelPersistence / PersonalityAdapter
- 使用 SelfModelConsumer 走标准流程
- data_dir 设为 data/self_model,产出 beliefs.jsonl / history.jsonl
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("d:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI")
sys.path.insert(0, str(ROOT))

from src.growth.proposal_store import ProposalStore


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(proposal_id: str) -> int:
    print(f"[{now_iso()}] === Phase C.4.6.5 Apply proposal {proposal_id} ===")

    # 1) 加载 approved proposal
    pstore = ProposalStore()
    proposal = pstore.load(proposal_id)
    if proposal is None:
        print(f"[{now_iso()}] ERROR: proposal {proposal_id} not found")
        return 1

    proposal_dict = proposal.to_dict()
    print(f"[{now_iso()}] proposal status={proposal_dict.get('status')} source_event_id={proposal_dict.get('source_event_id')}")
    print(json.dumps(proposal_dict, ensure_ascii=False, indent=2, default=str))

    # 2) 注入 review metadata into evaluator_meta (保留 review 审计)
    em = dict(proposal_dict.get("evaluator_meta") or {})
    em["reviewer_id"] = "yuyi_operator"
    em["review_comment"] = "Approved: real memory event mem_20260802222601_1b27, evidence verified"
    em["reviewed_at"] = "2026-08-02T14:34:49.010416Z"
    em["phase"] = "C.4.6.5"
    em["human_reviewed"] = True
    proposal_dict["evaluator_meta"] = em

    # 3) 使用 SelfModelConsumer 处理
    from src.admin.selfmodel_consumer import SelfModelConsumer
    consumer = SelfModelConsumer(
        data_dir="data/self_model",
        actor="yuyi_operator",
        auto_save=True,
        bootstrap=True,
    )
    print(f"[{now_iso()}] SelfModelConsumer data_dir={consumer.data_dir}")

    result = consumer.process(proposal_dict)
    print(f"[{now_iso()}] process result:")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    # 4) 验证产物
    beliefs_path = ROOT / "data" / "self_model" / "beliefs.jsonl"
    history_path = ROOT / "data" / "self_model" / "history.jsonl"
    print(f"[{now_iso()}] Output files:")
    print(f"  beliefs.jsonl: exists={beliefs_path.exists()} size={beliefs_path.stat().st_size if beliefs_path.exists() else 0}")
    print(f"  history.jsonl: exists={history_path.exists()} size={history_path.stat().st_size if history_path.exists() else 0}")

    if beliefs_path.exists():
        with open(beliefs_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    print(f"[beliefs] line {i}: {json.dumps(d, ensure_ascii=False, default=str)[:300]}")
                except Exception as e:
                    print(f"[beliefs] line {i}: parse error {e}")

    if history_path.exists():
        with open(history_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    print(f"[history] line {i}: {json.dumps(d, ensure_ascii=False, default=str)[:300]}")
                except Exception as e:
                    print(f"[history] line {i}: parse error {e}")

    # 5) 标记 proposal 为 applied
    proposal.status = "applied"
    try:
        pstore.update(proposal)
        print(f"[{now_iso()}] proposal status updated to applied")
    except Exception as e:
        print(f"[{now_iso()}] WARN: update failed: {e}")

    return 0


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "prop_9785d8035aab"
    raise SystemExit(main(pid))
