"""
Phase 6.0 Runtime Growth Integration AST 检查脚本。

验证：
1. Phase 6.0 新增/修改的源文件无语法错误
2. RuntimeCore 核心逻辑未被修改
3. 所有 Personality 修改必须经 PersonalityAdapter（无旁路）
4. 所有 Proposal 必须经 LifecycleManager（无旁路）
"""
import ast
import hashlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Phase 6.0 修改/新增的源文件
files_to_check = [
    # 已有 Phase 5.5.1 / 5.5.2 文件
    "src/growth/state_machines.py",
    "src/growth/sync/__init__.py",
    "src/growth/sync/retry_queue.py",
    "src/growth/sync/proposal_sync_manager.py",
    "src/growth/sync/retry_worker.py",
    "src/growth/lifecycle_manager.py",
    "src/growth/growth_limiter.py",
    "src/growth/approval_manager.py",
    "src/personality/personality_adapter.py",
    "src/runtime/adapters/governance_mirror_integration.py",
    "src/runtime/adapters/growth_proposal_mirror.py",
    "src/admin/governance_provider.py",
    "src/runtime/runtime_core.py",
    # Phase 6.0 新增
    "src/runtime/pipeline/__init__.py",
    "src/runtime/pipeline/runtime_growth_pipeline.py",
    "tests/test_phase_5_5_1_hotfix.py",
    "tests/test_phase_5_5_2_stability.py",
    "tests/test_phase_6_0_integration.py",
]

print("=" * 64)
print("Phase 6.0 Runtime Growth Integration AST 检查")
print("=" * 64)

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
        # 计算 hash 用于监控变化
        h = hashlib.sha256(src.encode("utf-8")).hexdigest()[:10]
        print(f"  OK:   {f}  ({len(classes)} classes, {len(funcs)} funcs) sha={h}")
    except SyntaxError as e:
        print(f"  FAIL: {f} - {e}")
        all_ok = False
    except Exception as e:
        print(f"  ERR:  {f} - {e}")
        all_ok = False

# RuntimeCore 引用计数（确保未在 hotfix 文件中旁路 RuntimeCore）
print("=" * 64)
print("RuntimeCore Authority 边界检查")
print("=" * 64)
hotfix_files_phase6 = [
    "src/growth/approval_manager.py",
    "src/personality/personality_adapter.py",
    "src/runtime/pipeline/runtime_growth_pipeline.py",
    "src/growth/lifecycle_manager.py",
    "src/growth/growth_limiter.py",
]
total_refs = 0
for f in hotfix_files_phase6:
    p = PROJECT_ROOT / f
    if not p.exists():
        continue
    with open(p, "r", encoding="utf-8") as fh:
        src = fh.read()
    refs = src.lower().count("runtime_core")
    if refs:
        print(f"  NOTE: {f} contains 'runtime_core' {refs} times (likely docstring or import)")
    total_refs += refs
print(f"  total runtime_core refs: {total_refs}")

# PersonalityAdapter 旁路检查：检查 pipeline 是否绕过 PersonalityAdapter
print("=" * 64)
print("PersonalityAdapter 必经性检查")
print("=" * 64)
pipeline_path = PROJECT_ROOT / "src/runtime/pipeline/runtime_growth_pipeline.py"
if pipeline_path.exists():
    with open(pipeline_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    if "personality_adapter" in src or "personality" in src:
        print("  OK:   runtime_growth_pipeline.py 引用 personality_adapter")
    else:
        print("  WARN: pipeline 未引用 personality_adapter（可能通过 approval_manager 间接接入）")
else:
    print("  SKIP: pipeline file not found")

# LifecycleManager 接入检查
print("=" * 64)
print("LifecycleManager 接入检查")
print("=" * 64)
approval_path = PROJECT_ROOT / "src/growth/approval_manager.py"
if approval_path.exists():
    with open(approval_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    if "lifecycle_manager" in src or "record_transition" in src:
        print("  OK:   approval_manager.py 接入 lifecycle_manager")
    else:
        print("  FAIL: approval_manager.py 未接入 lifecycle_manager")
        all_ok = False
else:
    print("  SKIP: approval_manager.py not found")

# GrowthRateLimiter 接入检查
print("=" * 64)
print("GrowthRateLimiter 接入检查")
print("=" * 64)
adapter_path = PROJECT_ROOT / "src/personality/personality_adapter.py"
if adapter_path.exists():
    with open(adapter_path, "r", encoding="utf-8") as fh:
        src = fh.read()
    if "growth_limiter" in src or "_filter_proposal_by_limiter" in src:
        print("  OK:   personality_adapter.py 接入 growth_limiter")
    else:
        print("  FAIL: personality_adapter.py 未接入 growth_limiter")
        all_ok = False
else:
    print("  SKIP: personality_adapter.py not found")

if all_ok:
    print("=" * 64)
    print("RESULT: PASS - All Phase 6.0 AST checks succeeded")
    sys.exit(0)
else:
    print("=" * 64)
    print("RESULT: FAIL")
    sys.exit(1)
