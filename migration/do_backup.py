"""Phase 2：建立完整备份
- 备份 data/、config.yaml*、logs/、src/ 到 backup_before_upgrade_<日期>/
- 验证文件数量
- 不修改任何源文件
"""
import shutil
import os
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP_DIR = PROJECT_ROOT / f"backup_before_upgrade_{TIMESTAMP}"

# 必须备份的项目
ITEMS = [
    "data",
    "config.yaml",
    "config.yaml.example",
    "config.yaml.save",
    "logs",
    "src",
]

# 不备份的内容（避免无意义大文件）
EXCLUDE_FROM_SRC = [
    "__pycache__",
    ".pyc",
    ".pyo",
    ".pyd",
]


def should_skip(path: Path) -> bool:
    name = path.name
    if name == "__pycache__":
        return True
    if name.endswith(".pyc") or name.endswith(".pyo") or name.endswith(".pyd"):
        return True
    return False


def copy_filtered(src_root: Path, dst_root: Path) -> dict:
    """复制 src/ 但排除 __pycache__"""
    stats = {"files": 0, "dirs": 0, "bytes": 0, "skipped": 0}
    for path in sorted(src_root.rglob("*")):
        rel = path.relative_to(src_root)
        # 跳过 __pycache__ 目录
        if any(part == "__pycache__" for part in rel.parts):
            stats["skipped"] += 1
            continue
        if path.is_dir():
            (dst_root / rel).mkdir(parents=True, exist_ok=True)
            stats["dirs"] += 1
        else:
            (dst_root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dst_root / rel)
            stats["files"] += 1
            stats["bytes"] += path.stat().st_size
    return stats


def main():
    print("=" * 60)
    print("Phase 2: Backup")
    print("=" * 60)
    print(f"Backup target: {BACKUP_DIR}")
    print()

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    summary = {}

    for item in ITEMS:
        src = PROJECT_ROOT / item
        if not src.exists():
            print(f"  [SKIP] {item} (not found)")
            summary[item] = {"status": "skipped", "reason": "not found"}
            continue

        dst = BACKUP_DIR / item

        if src.is_dir():
            if item == "src":
                # 特殊处理 src：排除 __pycache__
                stats = copy_filtered(src, dst)
                summary[item] = {
                    "status": "ok",
                    "files": stats["files"],
                    "dirs": stats["dirs"],
                    "bytes": stats["bytes"],
                    "skipped_pycache": stats["skipped"],
                }
                print(f"  [OK]   {item}/ -> {dst.relative_to(PROJECT_ROOT)}")
                print(f"         files={stats['files']} dirs={stats['dirs']} size={stats['bytes']:,}B skipped_pycache={stats['skipped']}")
            else:
                # 完整复制 data/、logs/
                stats = copy_filtered(src, dst) if item in ("data", "logs") else None
                if stats is None:
                    shutil.copytree(src, dst)
                    file_count = sum(1 for _ in dst.rglob("*") if _.is_file())
                    byte_count = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
                    summary[item] = {"status": "ok", "files": file_count, "bytes": byte_count}
                    print(f"  [OK]   {item}/ -> {dst.relative_to(PROJECT_ROOT)}")
                    print(f"         files={file_count} size={byte_count:,}B")
                else:
                    summary[item] = {
                        "status": "ok",
                        "files": stats["files"],
                        "bytes": stats["bytes"],
                    }
                    print(f"  [OK]   {item}/ -> {dst.relative_to(PROJECT_ROOT)}")
                    print(f"         files={stats['files']} size={stats['bytes']:,}B")
        else:
            # 单文件（config.yaml 等）
            shutil.copy2(src, dst)
            size = dst.stat().st_size
            summary[item] = {"status": "ok", "files": 1, "bytes": size}
            print(f"  [OK]   {item} -> {dst.relative_to(PROJECT_ROOT)}")
            print(f"         size={size:,}B")

    print()
    print("=" * 60)
    print("Backup Summary")
    print("=" * 60)
    total_files = 0
    total_bytes = 0
    for k, v in summary.items():
        if v.get("status") == "ok":
            total_files += v.get("files", 0)
            total_bytes += v.get("bytes", 0)
            print(f"  {k:20s} {v.get('files', 0):5d} files  {v.get('bytes', 0):>12,d} bytes")
        else:
            print(f"  {k:20s} SKIPPED ({v.get('reason')})")
    print(f"  {'TOTAL':20s} {total_files:5d} files  {total_bytes:>12,d} bytes ({total_bytes/1024/1024:.2f} MB)")

    # 写备份清单
    manifest = BACKUP_DIR / "MANIFEST.txt"
    with open(manifest, "w", encoding="utf-8") as f:
        f.write(f"Backup created at: {TIMESTAMP}\n")
        f.write(f"Project: QianWuYuyi-AI\n")
        f.write(f"Purpose: Pre-upgrade safety backup\n")
        f.write("=" * 60 + "\n")
        for k, v in summary.items():
            f.write(f"{k}: {v}\n")
        f.write("=" * 60 + "\n")
        f.write(f"TOTAL: {total_files} files, {total_bytes} bytes\n")
    print(f"\nManifest written: {manifest.relative_to(PROJECT_ROOT)}")

    return BACKUP_DIR


if __name__ == "__main__":
    backup_dir = main()
    print(f"\nBackup directory: {backup_dir}")
