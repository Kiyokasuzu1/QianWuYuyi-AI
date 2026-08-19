"""Phase 1 辅助：data/ 详细数据审计"""
import json
import os
from pathlib import Path

DATA = Path("data")
results = {}

# 检查 data/ 下所有 JSON 文件
for json_file in sorted(DATA.rglob("*.json")):
    rel = str(json_file.relative_to(DATA))
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        size = json_file.stat().st_size
        if isinstance(data, list):
            count = len(data)
            kind = "list"
            sample = data[:1] if data else None
        elif isinstance(data, dict):
            count = len(data)
            kind = "dict"
            sample = list(data.keys())[:8]
        else:
            count = 1
            kind = type(data).__name__
            sample = None
        results[rel] = {
            "kind": kind,
            "size": size,
            "count": count,
            "keys_or_sample": sample,
        }
    except Exception as e:
        results[rel] = {"error": str(e)[:80]}

# 检查 chroma_db
chroma = DATA / "chroma_db"
if chroma.exists():
    files = [str(f.relative_to(DATA)) for f in chroma.rglob("*") if f.is_file()]
    results["chroma_db/"] = {
        "kind": "binary",
        "size": sum(f.stat().st_size for f in chroma.rglob("*") if f.is_file()),
        "files": files,
    }

# 检查 jsonl 文件
for jsonl_file in sorted(DATA.rglob("*.jsonl")):
    rel = str(jsonl_file.relative_to(DATA))
    try:
        size = jsonl_file.stat().st_size
        with open(jsonl_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        results[rel] = {
            "kind": "jsonl",
            "size": size,
            "count": len(lines),
        }
    except Exception as e:
        results[rel] = {"error": str(e)[:80]}

# 输出
print("=" * 60)
print("data/ Detailed Inventory")
print("=" * 60)
for k, v in sorted(results.items()):
    if "error" in v:
        print("  %-50s ERROR: %s" % (k, v["error"]))
    elif v.get("kind") == "binary":
        print("  %-50s %s  size=%d  files=%d" % (k, v["kind"], v["size"], len(v.get("files", []))))
        for f in v.get("files", []):
            print("    - %s" % f)
    else:
        sample = v.get("keys_or_sample")
        sample_str = (" keys=" + str(sample)) if sample else ""
        print("  %-50s %s  count=%d  size=%d%s" % (k, v["kind"], v["count"], v["size"], sample_str))

# 汇总
print("=" * 60)
total_size = sum(v.get("size", 0) for v in results.values() if isinstance(v, dict) and "error" not in v)
print("TOTAL data size: %d bytes (%.2f KB)" % (total_size, total_size / 1024))
print("TOTAL files: %d" % len(results))
