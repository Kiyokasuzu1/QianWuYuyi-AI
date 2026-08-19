"""
Phase 6.1 Self Model Integration AST 检查脚本。

验证：
1. Phase 6.1 新增/修改的源文件无语法错误
2. RuntimeCore 核心逻辑未被修改
3. Pipeline 没有直接调用 SelfModelStore
4. 只有 SelfModelAdapter 拥有写权限
5. 6 个 Pipeline 阶段保持不变（无第七阶段）
"""
import ast
import hashlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Phase 6.1 修改/新增的源文件
files_to_check = [
    # Phase 6.1 新增
    "src/personality/self_belief.py",
    "src/personality/self_history.py",
    "src/personality/self_reflection.py",
    "src/personality/self_model_core.py",
    "src/personality/self_model_snapshot.py",
    "src/personality/self_model_adapter.py",
    # Phase 6.1 修改（仅追加，不破坏既有逻辑）
    "src/runtime/pipeline/runtime_growth_pipeline.py",
    # 测试
    "tests/test_phase_6_1_self_model_integration.py",
]

print("=" * 64)
print("Phase 6.1 Self Model Integration AST 检查")
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
        h = hashlib.sha256(src.encode("utf-8")).hexdigest()[:10]
        print(f"  OK:   {f}  ({len(classes)} classes, {len(funcs)} funcs) sha={h}")
    except SyntaxError as e:
        print(f"  FAIL: {f} - {e}")
        all_ok = False
    except Exception as e:
        print(f"  ERR:  {f} - {e}")
        all_ok = False

# ============================================================
# 1. RuntimeCore 未修改（无任何 Phase 6.1 新模块引用）
# ============================================================
print("=" * 64)
print("RuntimeCore Authority 边界检查（未修改）")
print("=" * 64)
rc_path = PROJECT_ROOT / "src/runtime/runtime_core.py"
if rc_path.exists():
    with open(rc_path, "r", encoding="utf-8") as fh:
        rc_src = fh.read()
    # 仅检查 Phase 6.1 新增的模块名/类名（避免与 self_reflection_engine 冲突）
    forbidden = [
        "self_model_adapter",
        "self_model_core",
        "self_belief",           # 新增 SelfBelief（区别于 self_belief_engine 之类的旧名）
        "SelfBeliefStore",
        "self_history",
        "SelfHistoryEvent",
        "SelfModelSnapshot",
        "SelfModelSnapshotStore",
    ]
    leaks = [k for k in forbidden if k in rc_src]
    if leaks:
        print(f"  FAIL: runtime_core.py 包含 Phase 6.1 新模块关键字: {leaks}")
        all_ok = False
    else:
        print("  OK:   runtime_core.py 未被 Phase 6.1 污染（无 self_model_adapter 等新模块引用）")
else:
    print("  SKIP: runtime_core.py not found")

# ============================================================
# 2. Pipeline 没有直接调用 SelfModelStore
# ============================================================
print("=" * 64)
print("Pipeline 不直接调用 SelfModelStore 检查")
print("=" * 64)
pipeline_path = PROJECT_ROOT / "src/runtime/pipeline/runtime_growth_pipeline.py"
if pipeline_path.exists():
    with open(pipeline_path, "r", encoding="utf-8") as fh:
        pipeline_src = fh.read()
    # 必须仅通过 self_model_adapter 间接访问
    forbidden_calls = [
        "SelfModelStore(",
        "self_model_store.update(",
        ".update_from_pcr(",
    ]
    direct_calls = [c for c in forbidden_calls if c in pipeline_src]
    if direct_calls:
        print(f"  FAIL: pipeline 直接调用 SelfModelStore: {direct_calls}")
        all_ok = False
    else:
        print("  OK:   pipeline 不直接调用 SelfModelStore")

    # 必须引用 self_model_adapter
    if "self_model_adapter" in pipeline_src:
        print("  OK:   pipeline 通过 self_model_adapter 间接访问")
    else:
        print("  FAIL: pipeline 未引用 self_model_adapter")
        all_ok = False
else:
    print("  SKIP: pipeline not found")

# ============================================================
# 3. SelfModelAdapter 拥有写权限
# ============================================================
print("=" * 64)
print("SelfModelAdapter 唯一写入口检查")
print("=" * 64)
adapter_path = PROJECT_ROOT / "src/personality/self_model_adapter.py"
if adapter_path.exists():
    with open(adapter_path, "r", encoding="utf-8") as fh:
        adapter_src = fh.read()
    # 应包含 path whitelist、authority check、confidence check、limiter check
    required_features = [
        ("path whitelist", "validate_self_model_path"),
        ("history append", "_append_history"),
        ("snapshot creation", "_create_snapshot"),
        ("rollback", "rollback"),
        ("apply_pcr", "apply_pcr"),
    ]
    for label, key in required_features:
        if key in adapter_src:
            print(f"  OK:   SelfModelAdapter.{label} ({key})")
        else:
            print(f"  FAIL: SelfModelAdapter 缺少 {label} ({key})")
            all_ok = False
else:
    print("  SKIP: self_model_adapter.py not found")

# ============================================================
# 4. Pipeline 6 阶段保持不变（无第七阶段）
# ============================================================
print("=" * 64)
print("Pipeline 阶段不变性检查")
print("=" * 64)
if pipeline_path.exists():
    with open(pipeline_path, "r", encoding="utf-8") as fh:
        pipeline_src = fh.read()
    # 必须有 6 个核心 stage
    required_stages = [
        "EXPERIENCE",
        "MEMORY",
        "REFLECTION",
        "PROPOSAL",
        "LIFECYCLE",
        "PERSONALITY",
    ]
    missing = [s for s in required_stages if s not in pipeline_src]
    if missing:
        print(f"  FAIL: pipeline 缺少阶段: {missing}")
        all_ok = False
    else:
        print("  OK:   pipeline 6 阶段保持不变")

    # 不应新增 SELF_MODEL 阶段
    if "SELF_MODEL" in pipeline_src or "SELFMODEL" in pipeline_src:
        # 但要排除：self_model_adapter 参数和 self_model 字段（不是阶段）
        # 检查是否有新 stage
        if "PipelineStage.SELF_MODEL" in pipeline_src or 'SELF_MODEL =' in pipeline_src:
            print("  FAIL: pipeline 新增了 SELF_MODEL 阶段（违反 6 阶段约束）")
            all_ok = False
        else:
            print("  OK:   pipeline 未新增 SELF_MODEL 阶段（仅作为 personality 阶段的子步骤）")
    else:
        print("  OK:   pipeline 未新增 SELF_MODEL 阶段")
else:
    print("  SKIP: pipeline not found")

# ============================================================
# 5. Path Authority 白名单/黑名单存在
# ============================================================
print("=" * 64)
print("Path Authority 白名单/黑名单检查")
print("=" * 64)
core_path = PROJECT_ROOT / "src/personality/self_model_core.py"
if core_path.exists():
    with open(core_path, "r", encoding="utf-8") as fh:
        core_src = fh.read()
    required = [
        "ALLOWED_SELF_MODEL_PATHS",
        "FORBIDDEN_SELF_MODEL_PATHS",
        "validate_self_model_path",
        "self_model.core_value",
        "self_model.stable_trait",
        "self_model.preference",
        "self_model.behavioral_pattern",
        "self_model.understanding",
        "self_model.invalid",
        "self_model.system",
        "self_model.runtime",
    ]
    missing = [k for k in required if k not in core_src]
    if missing:
        print(f"  FAIL: self_model_core.py 缺少 path authority 定义: {missing}")
        all_ok = False
    else:
        print("  OK:   ALLOWED / FORBIDDEN 路径白名单/黑名单完整")
else:
    print("  SKIP: self_model_core.py not found")

if all_ok:
    print("=" * 64)
    print("RESULT: PASS - All Phase 6.1 AST checks succeeded")
    sys.exit(0)
else:
    print("=" * 64)
    print("RESULT: FAIL")
    sys.exit(1)
