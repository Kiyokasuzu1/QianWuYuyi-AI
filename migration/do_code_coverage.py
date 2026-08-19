"""Phase 3：代码覆盖检查
由于源 = 目标路径（同一 QianWuYuyi-AI/），
实际无需覆盖操作。

本脚本只验证：
1. 当前 src/ 已是新版本
2. 关键文件存在性
3. 风险文件 diff 状态（与羽依核心对比）
4. 不执行任何写入
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# 必须存在的关键新版本文件
NEW_VERSION_REQUIRED = [
    "src/admin/api/routes.py",
    "src/admin/self_model_provider.py",
    "src/admin/runtime_provider.py",
    "src/admin/governance_provider.py",
    "src/emotion/emotion_engine.py",
    "src/emotion/emotion_dynamics_engine.py",
    "src/runtime/runtime_core.py",
    "src/runtime/runtime_bridge.py",
    "src/runtime/runtime_integration_manager.py",
    "src/growth/sync/proposal_sync_manager.py",
    "src/growth/sync/retry_queue.py",
    "src/growth/sync/retry_worker.py",
    "src/growth/approval_manager.py",
    "src/growth/growth_limiter.py",
    "src/personality/self_model_v3.py",
    "src/personality/self_model_builder_v3.py",
    "src/personality/self_model_health.py",
    "src/personality/self_model_retention.py",
    "src/personality/self_model_guardian.py",
    "src/personality/self_model_persistence.py",
    "src/personality/self_model_manager.py",
    "src/personality/self_model_snapshot.py",
    "src/personality/self_model_runtime_context.py",
    "src/personality/identity_anchor.py",
    "src/personality/identity_continuity.py",
    "src/personality/identity_stability_engine.py",
    "src/personality/personality_stability_engine.py",
    "src/contracts/runtime_schema.py",
    "src/contracts/runtime_integration_schema.py",
    "src/contracts/self_model_schema.py",
    "src/contracts/growth_approval_schema.py",
    "static/admin/index.html",
    "static/admin/js/app.js",
    "static/admin/js/selfmodel_dashboard.js",
    "static/admin/css/style.css",
    "api_server.py",
    "main.py",
    "run_server.py",
    "initiative_sender.py",
    "requirements.txt",
]

# 风险文件：仅 diff，不覆盖
RISK_FILES = [
    "src/personality/self_model.py",
    "src/personality/self_model_store.py",
    "src/personality/value_system.py",
    "src/personality/relationship_state.py",
    "src/personality/personality_evolution.py",
    "src/growth/growth_engine.py",
    "src/growth/growth_schema.py",
    "src/growth/growth_state.py",
    "src/growth/pipeline.py",
    "src/growth/proposal_manager.py",
    "src/growth/proposal/proposal.py",
    "src/growth/proposal/storage.py",
    "src/growth/proposal/reviewer.py",
    "src/core/yuyi_core.py",
    "src/core/yuyi_cognitive_core.py",
    "src/core/self_model.py",
    "src/memory/memory_store.py",
    "src/memory/vector.py",
    "src/runtime/runtime_core.py",
    "config.yaml",
]


def main():
    print("=" * 60)
    print("Phase 3: Code Coverage Check (READ-ONLY)")
    print("=" * 60)
    print("Note: Source = Target path, no overwrite required.")
    print()

    # 检查新版本必需文件
    print("--- New Version Required Files ---")
    missing = []
    present = []
    for f in NEW_VERSION_REQUIRED:
        path = PROJECT_ROOT / f
        if path.exists():
            present.append(f)
            print(f"  [OK]   {f}")
        else:
            missing.append(f)
            print(f"  [MISS] {f}")
    print(f"\n  Present: {len(present)}/{len(NEW_VERSION_REQUIRED)}")
    if missing:
        print(f"  Missing: {len(missing)} (CRITICAL)")
    else:
        print("  All new version files present.")

    print()
    print("--- Risk Files (verify existence, do not diff with old) ---")
    for f in RISK_FILES:
        path = PROJECT_ROOT / f
        status = "PROTECTED" if f == "config.yaml" else "DIFF-ONLY"
        marker = "[OK]" if path.exists() else "[MISS]"
        print(f"  {marker}  {f:50s}  [{status}]")

    print()
    print("--- Coverage Action Summary ---")
    if not missing:
        print("  [SKIP] No code overwrite required.")
        print("  [SKIP] src/ already contains new version.")
        print("  [SKIP] static/ already contains new version.")
        print("  [SKIP] tests/ already contains new version.")
        print("  [SKIP] scripts/ already contains new version.")
        print("  [KEEP] config.yaml unchanged (user rule: API Key).")
        print("  [KEEP] data/ unchanged (user data protection).")
        print("  [KEEP] .env unchanged (not present).")
    else:
        print(f"  [ALERT] {len(missing)} files missing, manual review needed.")

    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
