# -*- coding: utf-8 -*-
"""Phase C.4.6.5 — 触发 GrowthPipeline 评估最近的真实 memory 记录。

设计目标:
- 不修改 Orchestrator / GrowthPipeline / GrowthEvaluator
- 读取 data/memory.json 中最新 user_shared 记录
- 通过 GrowthPipeline 处理触发 GrowthEvaluator + proposal 生成
- 输出到 data/proposals/proposals.jsonl
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("d:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI")
sys.path.insert(0, str(ROOT))

from src.growth.pipeline import GrowthPipeline
from src.growth.proposal_store import ProposalStore


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    print(f"[{now_iso()}] === Phase C.4.6.5 GrowthPipeline trigger ===")

    # 读取 memory.json 最新记录
    mem_path = ROOT / "data" / "memory.json"
    with open(mem_path, "r", encoding="utf-8") as f:
        memories = json.load(f)

    # 找到最新 user_shared 记录(必须是 Phase C.4.6.5 这次)
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
        print(f"[{now_iso()}] ERROR: 找不到 Phase C.4.6.5 的 target memory record")
        return 1

    print(f"[{now_iso()}] TARGET MEMORY:")
    print(json.dumps(target, ensure_ascii=False, indent=2))

    # 记录 proposal store 初始 size
    pstore = ProposalStore()
    initial_count = pstore.count()
    print(f"[{now_iso()}] Initial proposal count: {initial_count}")

    # 通过 GrowthPipeline 增量处理
    gp = GrowthPipeline()
    msg = target["content"]
    print(f"[{now_iso()}] Calling GrowthPipeline.incremental_update()...")
    result = gp.incremental_update(msg)
    print(f"[{now_iso()}] incremental_update result keys: {list(result.keys()) if isinstance(result, dict) else 'non-dict'}")
    print(f"[{now_iso()}] incremental_update result:")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str)[:2000])

    # 检查新 proposal
    pstore_reload = ProposalStore()
    new_count = pstore_reload.count()
    print(f"[{now_iso()}] New proposal count: {new_count} (delta={new_count - initial_count})")

    # 列出 pending proposal
    pending = pstore_reload.list(status="pending", limit=10)
    print(f"[{now_iso()}] Pending proposals ({len(pending)}):")
    for p in pending:
        try:
            d = p.to_dict() if hasattr(p, "to_dict") else (p.__dict__ if hasattr(p, "__dict__") else {})
        except Exception as e:
            d = {"_err": str(e), "id": getattr(p, "id", "?")}
        print(json.dumps(d, ensure_ascii=False, indent=2, default=str)[:1500])
        print("---")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
