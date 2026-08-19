# -*- coding: utf-8 -*-
"""Phase 2.4-C:T2a 精选记忆受控导入脚本。

与 phase24a_import.py 同源,安全标准一致:
- 只通过现有 MemoryStore.add() 写入流程,不直接替换 memory.json
- 每条记录保留 metadata.recovery(原 provenance 完整保留,新增 batch_id=phase24c-t2a / source=historical_recovery)
- 不删除任何已有记录,不修改 identity/growth/emotion 任何文件
- 导入前在内存中先做 PollutionGuard 预检 + 内容指纹去重
- 全程只 import src.memory.*

用法:
  python phase24c_import.py --payload phase24c/t2a_payload.jsonl \
      --memory-path data/memory.json --report phase24c/import_report.json
  python phase24c_import.py --rollback phase24c/memory.pre_t2a_import.json \
      --memory-path data/memory.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.memory.memory_store import MemoryStore  # noqa: E402
from src.memory.pollution_guard import check as pollution_check  # noqa: E402
from src.memory.atomic_write import atomic_write_json  # noqa: E402

BATCH_ID = "phase24c-t2a"


def normalize(text: Any) -> str:
    t = text if isinstance(text, str) else str(text or "")
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"\s+", " ", t)
    return t.casefold().strip()


def fingerprint(text: Any) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def build_memory(rec: Dict[str, Any]) -> Dict[str, Any]:
    md = dict(rec.get("metadata") or {})
    md["source"] = "recovery_import_phase24c"
    recovery = dict(md.get("recovery") or {})
    recovery["batch_id"] = BATCH_ID
    recovery["source"] = "historical_recovery"
    md["recovery"] = recovery
    return {
        "id": rec.get("id") or "",
        "content": rec.get("content", ""),
        "timestamp": str(rec.get("timestamp") or ""),
        "user_id": str(rec.get("user_id") or "366648462"),
        "role": str(rec.get("role") or "user"),
        "importance": float(rec.get("importance") or 0.0),
        "source_event_id": str(rec.get("source_event_id") or ""),
        "emotion_tag": str(rec.get("emotion_tag") or ""),
        "relationship_id": str(rec.get("relationship_id") or ""),
        "metadata": md,
    }


def do_rollback(memory_path: Path, backup_path: Path) -> Dict[str, Any]:
    if not backup_path.exists():
        return {"success": False, "error": f"备份不存在: {backup_path}"}
    try:
        data = json.loads(backup_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return {"success": False, "error": "备份格式不是 list,拒绝回滚"}
    except Exception as exc:
        return {"success": False, "error": f"备份读取失败: {exc}"}
    atomic_write_json(memory_path, data)
    return {
        "success": True,
        "restored_count": len(data),
        "post_sha256": sha256_file(memory_path),
        "at": iso_now(),
    }


def do_import(memory_path: Path, payload_path: Path, report_path: Path) -> Dict[str, Any]:
    store = MemoryStore(str(memory_path))
    existing = store.load()
    pre_count = len(existing)
    pre_sha = sha256_file(memory_path)

    seen_fp = {fingerprint(m.get("content", "")) for m in existing}
    seen_ids = {m.get("id", "") for m in existing}

    added: List[str] = []
    skipped: List[Dict[str, str]] = []
    payload: List[Dict[str, Any]] = []
    for line in payload_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload.append(json.loads(line))
        except Exception as exc:
            skipped.append({"id": "<unparseable>", "reason": f"payload_json_error:{exc}"})

    start = time.time()
    for rec in payload:
        rec_id = str(rec.get("id") or "")
        content = rec.get("content", "")
        fp = fingerprint(content)
        if rec_id in seen_ids:
            skipped.append({"id": rec_id, "reason": "duplicate_id"})
            continue
        if fp in seen_fp:
            skipped.append({"id": rec_id, "reason": "duplicate_content_fingerprint"})
            continue

        memory = build_memory(rec)
        allowed, reason = pollution_check(memory)
        if not allowed:
            skipped.append({"id": rec_id, "reason": f"pollution_guard:{reason}"})
            continue

        result = store.add(memory)
        if result is None:
            skipped.append({"id": rec_id, "reason": "store_add_returned_none"})
            continue

        added.append(rec_id)
        seen_ids.add(rec_id)
        seen_fp.add(fp)

    post = store.load()
    post_count = len(post)
    post_sha = sha256_file(memory_path)
    expected = pre_count + len(added)
    report = {
        "phase": "2.4-C",
        "batch_id": BATCH_ID,
        "mode": "import",
        "at": iso_now(),
        "memory_path": str(memory_path),
        "pre_count": pre_count,
        "pre_sha256": pre_sha,
        "payload_total": len(payload),
        "added": len(added),
        "added_ids": added,
        "skipped": skipped,
        "post_count": post_count,
        "post_sha256": post_sha,
        "expected_post_count": expected,
        "concurrent_clobber_warning": post_count != expected,
        "elapsed_seconds": round(time.time() - start, 2),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", type=Path, default=Path("phase24c/t2a_payload.jsonl"))
    ap.add_argument("--memory-path", type=Path, default=Path("data/memory.json"))
    ap.add_argument("--report", type=Path, default=Path("phase24c/import_report.json"))
    ap.add_argument("--rollback", type=Path, default=None,
                    help="回滚模式:用备份文件整体恢复 memory.json(不导入)")
    args = ap.parse_args()

    if args.rollback:
        result = do_rollback(args.memory_path, args.rollback)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0 if result.get("success") else 1

    report = do_import(args.memory_path, args.payload, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
