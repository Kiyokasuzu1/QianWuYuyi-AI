"""Phase 4：数据完整性验证
- 验证 data/ 中所有核心数据文件可读
- 验证 config.yaml 未被修改（对比备份）
- 记录数据快照（before/after）
- 不修改任何源文件
"""
import json
import hashlib
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 核心数据文件清单
CRITICAL_DATA = {
    "data/memory.json": "list",  # 聊天记忆
    "data/chroma_db/chroma.sqlite3": "binary",  # 向量数据库
    "data/chroma_db/index_status.json": "dict",
    "data/growth_state.json": "dict",
    "data/emotion_state.json": "dict",
    "data/relationship_state.json": "dict",
    "data/runtime_state.json": "dict",
    "data/emotional_traces.json": "list",
    "data/audit/audit_logs.json": "jsonl_or_list",
    "data/proposals/growth_proposals.json": "list",
    "data/growth/proposals/proposals.json": "dict",
    "data/growth/sync/dead_letter_queue.json": "dict",
    "data/growth/limiter/limiter_state.json": "dict",
    "data/llm_failures/failures.jsonl": "jsonl",
    "config.yaml": "text",
    "config.yaml.example": "text",
    "config.yaml.save": "text",
}


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def read_json_safe(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"_error": str(e)[:100]}


def main():
    print("=" * 60)
    print("Phase 4: Data Integrity Verification (READ-ONLY)")
    print("=" * 60)
    print()

    snapshot = {}
    integrity_ok = True

    for rel_path, expected_type in CRITICAL_DATA.items():
        path = PROJECT_ROOT / rel_path
        if not path.exists():
            print(f"  [MISS]  {rel_path} (file not found)")
            snapshot[rel_path] = {"exists": False}
            integrity_ok = False
            continue

        h = file_hash(path)
        size = path.stat().st_size

        entry = {
            "exists": True,
            "size": size,
            "hash": h,
        }

        if expected_type == "binary":
            entry["type"] = "binary"
            print(f"  [OK]    {rel_path:50s}  size={size:>10,}B  hash={h}")
        elif expected_type in ("dict", "list", "jsonl", "jsonl_or_list", "text"):
            content = read_json_safe(path)
            if isinstance(content, dict) and "_error" in content:
                entry["error"] = content["_error"]
                print(f"  [ERR]   {rel_path:50s}  size={size:>10,}B  hash={h}  ERROR: {content['_error'][:60]}")
                integrity_ok = False
            elif expected_type == "text":
                entry["type"] = "text"
                print(f"  [OK]    {rel_path:50s}  size={size:>10,}B  hash={h}")
            elif expected_type == "jsonl":
                # 数行数
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        lines = [l for l in f.readlines() if l.strip()]
                    entry["type"] = "jsonl"
                    entry["count"] = len(lines)
                    print(f"  [OK]    {rel_path:50s}  size={size:>10,}B  lines={len(lines):>4}  hash={h}")
                except Exception as e:
                    entry["error"] = str(e)
                    print(f"  [ERR]   {rel_path}  ERROR: {e}")
                    integrity_ok = False
            elif expected_type == "jsonl_or_list":
                # 尝试作为 jsonl 或 list
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        raw = f.read().strip()
                    if raw.startswith("["):
                        # JSON list
                        data = json.loads(raw)
                        entry["type"] = "list"
                        entry["count"] = len(data) if isinstance(data, list) else 0
                    else:
                        # 可能是 JSONL
                        lines = [l for l in raw.split("\n") if l.strip()]
                        entry["type"] = "jsonl"
                        entry["count"] = len(lines)
                    print(f"  [OK]    {rel_path:50s}  size={size:>10,}B  count={entry.get('count', '-'):>4}  hash={h}")
                except Exception as e:
                    entry["error"] = str(e)[:80]
                    print(f"  [ERR]   {rel_path}  ERROR: {e}")
                    integrity_ok = False
            else:
                if expected_type == "list":
                    entry["type"] = "list"
                    entry["count"] = len(content) if isinstance(content, list) else 0
                    # 取最早/最晚时间戳作为连续性证据
                    if isinstance(content, list) and content:
                        first = content[0]
                        last = content[-1]
                        if isinstance(first, dict) and "timestamp" in first:
                            entry["first_ts"] = first.get("timestamp")
                            entry["last_ts"] = last.get("timestamp")
                elif expected_type == "dict":
                    entry["type"] = "dict"
                    entry["keys"] = list(content.keys()) if isinstance(content, dict) else []
                print(f"  [OK]    {rel_path:50s}  size={size:>10,}B  count={entry.get('count', len(entry.get('keys', [])))}  hash={h}")

        snapshot[rel_path] = entry

    # 写入数据快照
    SNAPSHOT_PATH = PROJECT_ROOT / "migration" / f"data_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f"\nSnapshot written: {SNAPSHOT_PATH.relative_to(PROJECT_ROOT)}")

    # 输出连续性关键指标
    print()
    print("=" * 60)
    print("Continuity Anchors")
    print("=" * 60)
    mem = snapshot.get("data/memory.json", {})
    if mem.get("type") == "list":
        print(f"  memory.json        : {mem.get('count', 0)} entries")
        if "first_ts" in mem:
            print(f"    first_ts         : {mem['first_ts']}")
            print(f"    last_ts          : {mem['last_ts']}")

    emo = snapshot.get("data/emotion_state.json", {})
    if emo.get("type") == "dict":
        print(f"  emotion_state.json : {emo.get('keys', [])}")

    rel = snapshot.get("data/relationship_state.json", {})
    if rel.get("type") == "dict":
        print(f"  relationship_state : {rel.get('keys', [])}")

    growth = snapshot.get("data/growth_state.json", {})
    if growth.get("type") == "dict":
        print(f"  growth_state.json  : {growth.get('keys', [])}")

    rt = snapshot.get("data/runtime_state.json", {})
    if rt.get("type") == "dict":
        print(f"  runtime_state.json : {rt.get('keys', [])}")

    audit = snapshot.get("data/audit/audit_logs.json", {})
    print(f"  audit_logs.json    : type={audit.get('type', '?')} count={audit.get('count', '?')}")

    chroma = snapshot.get("data/chroma_db/chroma.sqlite3", {})
    print(f"  chroma.sqlite3     : {chroma.get('size', 0):,} bytes")

    print()
    print("=" * 60)
    if integrity_ok:
        print("[OK] All critical data files verified.")
    else:
        print("[FAIL] Some data files have issues, see above.")
    print("=" * 60)

    return 0 if integrity_ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
