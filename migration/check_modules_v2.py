"""只读健康检查 v2：模块级导入验证（更宽松、更准确）
不修改任何文件，不读写 data/ 与 config.yaml
只验证模块能否被 import，不验证具体类名
"""
import sys
import importlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def check_module(name):
    try:
        importlib.import_module(name)
        return "OK", ""
    except Exception as e:
        return "FAIL", str(e).split("\n")[0][:140]


ADMIN_MODULES = [
    "src.admin.api.routes",
    "src.admin.self_model_provider",
    "src.admin.runtime_provider",
    "src.admin.governance_provider",
    "src.admin.config_manager",
    "src.admin.audit",
    "src.admin.core.schema_validator",
    "src.admin.core.l2d_adapter",
    "src.admin.core.audit_hooks",
    "src.admin.core.config_manager",
    "src.admin.core",
    "src.admin",
    "api_server",
]

SELFMODEL_MODULES = [
    "src.personality.self_model",
    "src.personality.self_model_v3",
    "src.personality.self_model_builder",
    "src.personality.self_model_builder_v3",
    "src.personality.self_model_store",
    "src.personality.self_model_context_provider",
    "src.personality.self_model_core",
    "src.personality.self_model_manager",
    "src.personality.self_model_guardian",
    "src.personality.self_model_health",
    "src.personality.self_model_retention",
    "src.personality.self_model_persistence",
    "src.personality.self_model_updater",
    "src.personality.self_model_adapter",
    "src.personality.self_model_sync_adapter",
    "src.personality.self_model_snapshot",
    "src.personality.self_model_runtime_context",
    "src.personality",
    "src.audit.self_model_audit",
    "src.contracts.self_model_schema",
]

GROWTH_MODULES = [
    "src.growth.growth_engine",
    "src.growth.growth_evaluator",
    "src.growth.growth_loop",
    "src.growth.growth_record",
    "src.growth.growth_schema",
    "src.growth.growth_state",
    "src.growth.growth_limiter",
    "src.growth.lifecycle_manager",
    "src.growth.state_machines",
    "src.growth.approval_manager",
    "src.growth.meaning_resolver",
    "src.growth.memory_former",
    "src.growth.topic_tracker",
    "src.growth.event_history_store",
    "src.growth.event_history_matcher",
    "src.growth.pipeline",
    "src.growth.proposal_manager",
    "src.growth.proposal_store",
    "src.growth.proposal_events",
    "src.growth.schemas",
    "src.growth.proposal",
    "src.growth.proposal.constants",
    "src.growth.proposal.proposal",
    "src.growth.proposal.reviewer",
    "src.growth.proposal.storage",
    "src.growth.sync",
    "src.growth.sync.proposal_sync_manager",
    "src.growth.sync.retry_queue",
    "src.growth.sync.retry_worker",
    "src.growth",
    "src.contracts.growth_schema",
    "src.contracts.growth_approval_schema",
]

RUNTIME_MODULES = [
    "src.runtime.runtime_core",
    "src.runtime.runtime_bridge",
    "src.runtime.runtime_integration_manager",
    "src.runtime.runtime_event_bus",
    "src.runtime.runtime_context",
    "src.runtime.self_state",
    "src.runtime.world_state",
    "src.runtime.action_dispatcher",
    "src.runtime.cognitive_engine",
    "src.runtime.decision_engine",
    "src.runtime.scheduler",
    "src.runtime.curiosity_engine",
    "src.runtime.creative_engine",
    "src.runtime.autonomous_scheduler",
    "src.runtime.experience_builder",
    "src.runtime.self_reflection_engine",
    "src.runtime.reflection_engine",
    "src.runtime.reflection_scheduler",
    "src.runtime.reflection_growth_bridge",
    "src.runtime.cognitive_loop_verifier",
    "src.runtime.long_term_pattern_analyzer",
    "src.runtime.personality_event_bus",
    "src.runtime.self_model_bootstrap",
    "src.runtime.yuyi_runtime_integration",
    "src.runtime.initiative_bridge",
    "src.runtime.autonomous_decision_layer",
    "src.runtime.contradiction_analyzer",
    "src.runtime.lifecycle_manager",
    "src.runtime",
    "src.core",
    "src.core.yuyi_core",
    "src.core.yuyi_cognitive_core",
    "src.core.event_bus",
    "src.core.module_loader",
    "src.core.persona",
    "src.orchestrator",
    "src.engine",
]


def run(name, modules):
    results = []
    for m in modules:
        s, e = check_module(m)
        results.append((m, s, e))
    ok = sum(1 for r in results if r[1] == "OK")
    fail = sum(1 for r in results if r[1] == "FAIL")
    print(f"\n== {name} Modules == {ok}/{len(results)} OK | {fail} FAIL")
    for n, s, e in results:
        marker = "[OK]  " if s == "OK" else "[FAIL]"
        print(f"  {marker} {n}{(': ' + e) if s == 'FAIL' else ''}")
    return ok, fail, results


def main():
    total_ok = 0
    total_fail = 0
    for name, modules in [
        ("Admin", ADMIN_MODULES),
        ("SelfModel", SELFMODEL_MODULES),
        ("Growth", GROWTH_MODULES),
        ("Runtime/Core", RUNTIME_MODULES),
    ]:
        ok, fail, _ = run(name, modules)
        total_ok += ok
        total_fail += fail
    print(f"\n========= TOTAL =========")
    print(f"OK   : {total_ok}")
    print(f"FAIL : {total_fail}")
    print(f"RATE : {total_ok / (total_ok + total_fail) * 100:.1f}%")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
