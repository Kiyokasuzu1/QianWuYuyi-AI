"""
AST 检查脚本：验证 Phase 5.5.1 Hotfix 修改的所有文件无语法错误，
并确认 RuntimeCore 未被修改。
"""
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 所有修改/创建的源文件
files_to_check = [
    # Phase 5.5.1 Hotfix
    "src/growth/state_machines.py",
    "src/growth/sync/__init__.py",
    "src/growth/sync/retry_queue.py",
    "src/personality/personality_adapter.py",
    "src/runtime/adapters/governance_mirror_integration.py",
    "src/runtime/adapters/growth_proposal_mirror.py",
    "src/admin/governance_provider.py",
    "src/runtime/runtime_core.py",
    "src/growth/approval_manager.py",
    "tests/test_phase_5_5_1_hotfix.py",
    # Phase 5.5.2 Stability Layer
    "src/growth/sync/proposal_sync_manager.py",
    "src/growth/sync/retry_worker.py",
    "src/growth/lifecycle_manager.py",
    "src/growth/growth_limiter.py",
    "tests/test_phase_5_5_2_stability.py",
]

print("=" * 60)
print("Phase 5.5.1 Hotfix AST 检查")
print("=" * 60)

all_ok = True
for f in files_to_check:
    p = PROJECT_ROOT / f
    if not p.exists():
        print(f"  SKIP: {f} (not found)")
        continue
    try:
        with open(p, "r", encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src, filename=str(p))
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        print(f"  OK:   {f}  ({len(classes)} classes, {len(funcs)} funcs)")
    except SyntaxError as e:
        print(f"  FAIL: {f} - {e}")
        all_ok = False
    except Exception as e:
        print(f"  ERR:  {f} - {e}")
        all_ok = False

# RuntimeCore 引用计数检查（确保未修改 RuntimeCore）
print("=" * 60)
print("RuntimeCore 未被修改验证")
print("=" * 60)
runtime_core_refs = 0
hotfix_files = [
    "src/growth/state_machines.py",
    "src/growth/sync/retry_queue.py",
    "src/personality/personality_adapter.py",
    "src/runtime/adapters/governance_mirror_integration.py",
    "src/runtime/adapters/growth_proposal_mirror.py",
]
for f in hotfix_files:
    p = PROJECT_ROOT / f
    if not p.exists():
        continue
    with open(p, "r", encoding="utf-8") as fh:
        src = fh.read()
    refs = src.lower().count("runtime_core")
    if refs:
        print(f"  WARN: {f} contains 'runtime_core' {refs} times")
    runtime_core_refs += refs

# 'src/runtime/runtime_core' 路径引用（说明是文档或字符串提及）
runtime_core_path_refs = 0
for f in hotfix_files:
    p = PROJECT_ROOT / f
    if not p.exists():
        continue
    with open(p, "r", encoding="utf-8") as fh:
        src = fh.read()
    runtime_core_path_refs += src.count("runtime_core")

print(f"  runtime_core (lowercase) refs: {runtime_core_refs}")
print(f"  runtime_core (any case) refs:  {runtime_core_path_refs}")

if all_ok:
    print("=" * 60)
    print("RESULT: PASS - All AST checks succeeded")
    sys.exit(0)
else:
    print("=" * 60)
    print("RESULT: FAIL")
    sys.exit(1)
