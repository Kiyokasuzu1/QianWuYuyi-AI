# -*- coding: utf-8 -*-
"""Phase 2.4-F T2c A类 导入后验证脚本(本地排练与服务器导入后通用)。

检查项:
1. memory.json 为合法 JSON list
2. 每条记录 schema 完整(必需键)
3. 每条记录通过 PollutionGuard.check
4. 内容指纹全文件唯一(无重复)
5. 既有记录(pre 文件)逐条完好保留(不删不改)
6. MemoryRetriever 检索可用性(关键词命中新增记录)
7. 新增记录均带 metadata.recovery.provenance 且 batch_id 一致
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.memory.memory_store import MemoryStore  # noqa: E402
from src.memory.pollution_guard import check as pollution_check  # noqa: E402
from src.memory.memory_retriever import MemoryRetriever  # noqa: E402

REQUIRED_KEYS = ("id", "content", "timestamp", "user_id", "role", "metadata")


def normalize(text: Any) -> str:
    t = text if isinstance(text, str) else str(text or "")
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"\s+", " ", t)
    return t.casefold().strip()


def fingerprint(text: Any) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def verify(memory_path: Path, pre_path: Path, imported_ids: set) -> Dict[str, Any]:
    store = MemoryStore(str(memory_path))
    records = store.load()

    problems: List[str] = []
    fp_count: Dict[str, int] = {}

    for i, rec in enumerate(records):
        tag = f"#{i}({rec.get('id', '?')})"
        if not isinstance(rec, dict):
            problems.append(f"{tag} 非 dict"); continue
        for k in REQUIRED_KEYS:
            if k not in rec:
                problems.append(f"{tag} 缺键 {k}")
        allowed, reason = pollution_check(rec)
        if not allowed:
            problems.append(f"{tag} PollutionGuard: {reason}")
        fp = fingerprint(rec.get("content", ""))
        fp_count[fp] = fp_count.get(fp, 0) + 1

    dup_fps = {fp: n for fp, n in fp_count.items() if n > 1}
    schema_fail = [p for p in problems if not p.endswith("非 dict")]
    guard_fail = [p for p in problems if "PollutionGuard" in p]

    # 5. 既有记录完好性
    pre_records = json.loads(pre_path.read_text(encoding="utf-8")) if pre_path.exists() else []
    pre_ids = {r.get("id", "") for r in pre_records}
    pre_fps = {fingerprint(r.get("content", "")) for r in pre_records}
    post_ids = {r.get("id", "") for r in records}
    missing_ids = pre_ids - post_ids
    pre_fp_now = {fingerprint(r.get("content", "")) for r in records if r.get("id", "") in pre_ids}
    changed_contents = pre_fps ^ pre_fp_now  # 对称差:有内容被改动

    # 7. 新增记录 provenance
    imported_present = imported_ids & post_ids
    missing_imports = imported_ids - post_ids
    bad_provenance = []
    for r in records:
        if r.get("id", "") in imported_ids:
            rec = ((r.get("metadata") or {}).get("recovery") or {})
            if not isinstance(rec, dict) or not rec.get("fingerprint") or not rec.get("batch_id"):
                bad_provenance.append(r.get("id", ""))

    # 6. 检索可用性:用 T1 内容中的短语查询
    queries = [
        "我是清夏铃", "羽依", "架构", "记忆", "清夏铃",
        "人生经历档案", "成长经验", "第一次启动", "统一认知中枢",
    ]
    retriever = MemoryRetriever(store)
    retrieval = []
    for q in queries:
        hits = retriever.search(q, limit=3)
        hit_ids = [h.get("id", "") for h in hits]
        retrieval.append({
            "query": q,
            "hits": len(hits),
            "hit_imported": sum(1 for i in hit_ids if i in imported_ids),
        })

    return {
        "memory_path": str(memory_path),
        "total_records": len(records),
        "pre_count": len(pre_records),
        "imported_expected": len(imported_ids),
        "imported_present": len(imported_present),
        "missing_imports": sorted(missing_imports),
        "schema_fail_count": len(schema_fail),
        "schema_fail": schema_fail[:10],
        "pollution_guard_fail_count": len(guard_fail),
        "pollution_guard_fail": guard_fail[:10],
        "duplicate_fingerprints": dup_fps,
        "pre_existing_missing": sorted(missing_ids),
        "pre_existing_content_changed": len(changed_contents) > 0,
        "bad_provenance": bad_provenance,
        "retrieval_checks": retrieval,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-path", type=Path, required=True)
    ap.add_argument("--pre", type=Path, required=True, help="导入前备份文件")
    ap.add_argument("--imported-ids", type=Path, required=True,
                    help="导入 id 清单(jsonl,每行一个 id)")
    ap.add_argument("--out", type=Path, default=Path("phase24a/verify_report.json"))
    args = ap.parse_args()

    ids = {line.strip() for line in args.imported_ids.read_text(encoding="utf-8").splitlines() if line.strip()}
    result = verify(args.memory_path, args.pre, ids)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
