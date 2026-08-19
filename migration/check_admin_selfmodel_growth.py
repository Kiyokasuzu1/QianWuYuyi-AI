"""只读健康检查：Admin / SelfModel / Growth 模块导入验证
不修改任何文件，不读写 data/ 与 config.yaml
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def run(name, checks):
    results = []
    for n, stmt in checks:
        try:
            exec(stmt)
            results.append((n, "OK", ""))
        except Exception as e:
            err = str(e).split("\n")[0][:140]
            results.append((n, "FAIL", err))
    ok = sum(1 for r in results if r[1] == "OK")
    fail = sum(1 for r in results if r[1] == "FAIL")
    print(f"\n== {name} == {ok}/{len(results)} OK | {fail} FAIL")
    for n, s, e in results:
        if s == "OK":
            print(f"  [OK]   {n}")
        else:
            print(f"  [FAIL] {n} -> {e}")
    return ok, fail, results


ADMIN_CHECKS = [
    ("admin_bp", "from src.admin.api.routes import admin_bp"),
    ("init_admin", "from src.admin.api.routes import init_admin"),
    ("SelfModelProvider", "from src.admin.self_model_provider import SelfModelProvider"),
    ("RuntimeProvider", "from src.admin.runtime_provider import RuntimeProvider"),
    ("GovernanceProvider", "from src.admin.governance_provider import GovernanceProvider"),
    ("AdminConfigManager", "from src.admin.config_manager import AdminConfigManager"),
    ("AdminAudit", "from src.admin.audit import AdminAudit"),
    ("AdminSchemaValidator", "from src.admin.core.schema_validator import SchemaValidator"),
    ("AdminL2DAdapter", "from src.admin.core.l2d_adapter import L2DAdapter"),
    ("AdminAuditHooks", "from src.admin.core.audit_hooks import AuditHooks"),
    ("AdminCoreConfigManager", "from src.admin.core.config_manager import CoreConfigManager"),
    ("api_server", "import api_server"),
]

SELFMODEL_CHECKS = [
    ("SelfModel", "from src.personality.self_model import SelfModel"),
    ("SelfModelV3", "from src.personality.self_model_v3 import SelfModelV3"),
    ("SelfModelBuilder", "from src.personality.self_model_builder import SelfModelBuilder"),
    ("SelfModelBuilderV3", "from src.personality.self_model_builder_v3 import SelfModelBuilderV3"),
    ("SelfModelStore", "from src.personality.self_model_store import SelfModelStore"),
    ("SelfModelContextProvider", "from src.personality.self_model_context_provider import SelfModelContextProvider"),
    ("SelfModelCore", "from src.personality.self_model_core import SelfModelCore"),
    ("SelfModelManager", "from src.personality.self_model_manager import SelfModelManager"),
    ("SelfModelGuardian", "from src.personality.self_model_guardian import SelfModelGuardian"),
    ("SelfModelHealth", "from src.personality.self_model_health import SelfModelHealth"),
    ("SelfModelRetention", "from src.personality.self_model_retention import SelfModelRetention"),
    ("SelfModelPersistence", "from src.personality.self_model_persistence import SelfModelPersistence"),
    ("SelfModelUpdater", "from src.personality.self_model_updater import SelfModelUpdater"),
    ("SelfModelAdapter", "from src.personality.self_model_adapter import SelfModelAdapter"),
    ("SelfModelSyncAdapter", "from src.personality.self_model_sync_adapter import SelfModelSyncAdapter"),
    ("SelfModelSnapshot", "from src.personality.self_model_snapshot import SelfModelSnapshot"),
    ("SelfModelRuntimeContext", "from src.personality.self_model_runtime_context import SelfModelRuntimeContext"),
    ("SelfModelAudit", "from src.audit.self_model_audit import SelfModelAudit"),
    ("SelfModelSchema", "from src.contracts.self_model_schema import SelfModelSchema"),
]

GROWTH_CHECKS = [
    ("GrowthEngine", "from src.growth.growth_engine import GrowthEngine"),
    ("GrowthEvaluator", "from src.growth.growth_evaluator import GrowthEvaluator"),
    ("GrowthLoop", "from src.growth.growth_loop import GrowthLoop"),
    ("GrowthRecord", "from src.growth.growth_record import GrowthRecord"),
    ("GrowthSchema", "from src.growth.growth_schema import GrowthSchema"),
    ("GrowthState", "from src.growth.growth_state import GrowthState"),
    ("GrowthPipeline", "from src.growth.pipeline import GrowthPipeline"),
    ("ProposalManager", "from src.growth.proposal_manager import ProposalManager"),
    ("ProposalStore", "from src.growth.proposal_store import ProposalStore"),
    ("ProposalEvents", "from src.growth.proposal_events import ProposalEvents"),
    ("Proposal_Subpackage", "import src.growth.proposal"),
    ("ProposalConstants", "from src.growth.proposal.constants import *"),
    ("Proposal_Proposal", "from src.growth.proposal.proposal import *"),
    ("Proposal_Reviewer", "from src.growth.proposal.reviewer import *"),
    ("Proposal_Storage", "from src.growth.proposal.storage import *"),
    ("GrowthLimiter", "from src.growth.growth_limiter import GrowthLimiter"),
    ("LifecycleManager", "from src.growth.lifecycle_manager import LifecycleManager"),
    ("StateMachines", "from src.growth.state_machines import *"),
    ("ApprovalManager", "from src.growth.approval_manager import ApprovalManager"),
    ("MeaningResolver", "from src.growth.meaning_resolver import MeaningResolver"),
    ("MemoryFormer", "from src.growth.memory_former import MemoryFormer"),
    ("TopicTracker", "from src.growth.topic_tracker import TopicTracker"),
    ("EventHistoryStore", "from src.growth.event_history_store import EventHistoryStore"),
    ("EventHistoryMatcher", "from src.growth.event_history_matcher import EventHistoryMatcher"),
    ("Sync_Subpackage", "import src.growth.sync"),
    ("ProposalSyncManager", "from src.growth.sync.proposal_sync_manager import ProposalSyncManager"),
    ("RetryQueue", "from src.growth.sync.retry_queue import RetryQueue"),
    ("RetryWorker", "from src.growth.sync.retry_worker import RetryWorker"),
    ("GrowthSchema_Contracts", "from src.contracts.growth_schema import *"),
    ("GrowthApprovalSchema", "from src.contracts.growth_approval_schema import *"),
]


def main():
    total_ok = 0
    total_fail = 0
    for name, checks in [
        ("Admin API", ADMIN_CHECKS),
        ("SelfModel", SELFMODEL_CHECKS),
        ("Growth", GROWTH_CHECKS),
    ]:
        ok, fail, _ = run(name, checks)
        total_ok += ok
        total_fail += fail
    print(f"\n== Total == {total_ok}/{total_ok + total_fail} OK | {total_fail} FAIL")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
