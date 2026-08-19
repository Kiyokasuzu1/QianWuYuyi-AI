"""只读健康检查：Runtime / Core 导入验证
不修改任何文件，不读写 data/ 与 config.yaml
"""
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CHECKS = [
    ("RuntimeCore", "from src.runtime.runtime_core import RuntimeCore"),
    ("YuyiCore", "from src.core.yuyi_core import YuyiCore"),
    ("YuyiCognitiveCore", "from src.core.yuyi_cognitive_core import YuyiCognitiveCore"),
    ("Orchestrator", "from src.orchestrator import Orchestrator"),
    ("Engine", "from src.engine import Engine"),
    ("EventBus", "from src.core.event_bus import EventBus"),
    ("Heartbeat", "from src.core.heartbeat import Heartbeat"),
    ("ModuleLoader", "from src.core.module_loader import ModuleLoader"),
    ("ModuleInterface", "from src.core.module_interface import ModuleInterface"),
    ("Persona", "from src.core.persona import Persona"),
    ("RuntimeBridge", "from src.runtime.runtime_bridge import RuntimeBridge"),
    ("RuntimeIntegrationManager", "from src.runtime.runtime_integration_manager import RuntimeIntegrationManager"),
    ("LifecycleOrchestrator", "from src.runtime.lifecycle_orchestrator import LifecycleOrchestrator"),
    ("SelfState", "from src.runtime.self_state import SelfState"),
    ("WorldState", "from src.runtime.world_state import WorldState"),
    ("ActionDispatcher", "from src.runtime.action_dispatcher import ActionDispatcher"),
    ("CognitiveEngine", "from src.runtime.cognitive_engine import CognitiveEngine"),
    ("DecisionEngine", "from src.runtime.decision_engine import DecisionEngine"),
    ("Scheduler", "from src.runtime.scheduler import Scheduler"),
    ("CuriosityEngine", "from src.runtime.curiosity_engine import CuriosityEngine"),
    ("CreativeEngine", "from src.runtime.creative_engine import CreativeEngine"),
    ("AutonomousScheduler", "from src.runtime.autonomous_scheduler import AutonomousScheduler"),
    ("ExperienceBuilder", "from src.runtime.experience_builder import ExperienceBuilder"),
    ("SelfReflectionEngine", "from src.runtime.self_reflection_engine import SelfReflectionEngine"),
    ("ReflectionEngine", "from src.runtime.reflection_engine import ReflectionEngine"),
    ("ReflectionScheduler", "from src.runtime.reflection_scheduler import ReflectionScheduler"),
    ("ReflectionGrowthBridge", "from src.runtime.reflection_growth_bridge import ReflectionGrowthBridge"),
    ("CognitiveLoopVerifier", "from src.runtime.cognitive_loop_verifier import CognitiveLoopVerifier"),
    ("LongTermPatternAnalyzer", "from src.runtime.long_term_pattern_analyzer import LongTermPatternAnalyzer"),
    ("PersonalityEventBus", "from src.runtime.personality_event_bus import PersonalityEventBus"),
    ("SelfModelBootstrap", "from src.runtime.self_model_bootstrap import SelfModelBootstrap"),
    ("YuyiRuntimeIntegration", "from src.runtime.yuyi_runtime_integration import YuyiRuntimeIntegration"),
    ("InitiativeBridge", "from src.runtime.initiative_bridge import InitiativeBridge"),
    ("EventBus_Runtime", "from src.runtime.event_bus import EventBus as _REB"),
    ("RuntimeEventBus", "from src.runtime.runtime_event_bus import RuntimeEventBus"),
    ("RuntimeContext", "from src.runtime.runtime_context import RuntimeContext"),
    ("AutonomousDecisionLayer", "from src.runtime.autonomous_decision_layer import AutonomousDecisionLayer"),
    ("ContradictionAnalyzer", "from src.runtime.contradiction_analyzer import ContradictionAnalyzer"),
    ("LifecycleManager", "from src.runtime.lifecycle_manager import LifecycleManager"),
]


def main():
    results = []
    for name, stmt in CHECKS:
        try:
            exec(stmt)
            results.append((name, "OK", ""))
        except Exception as e:
            err = str(e).split("\n")[0][:140]
            results.append((name, "FAIL", err))
    ok = sum(1 for r in results if r[1] == "OK")
    fail = sum(1 for r in results if r[1] == "FAIL")
    print(f"== Runtime / Core Imports == {ok}/{len(results)} OK | {fail} FAIL")
    for n, s, e in results:
        if s == "OK":
            print(f"  [OK]   {n}")
        else:
            print(f"  [FAIL] {n} -> {e}")
    return ok, fail, results


if __name__ == "__main__":
    ok, fail, _ = main()
    sys.exit(0 if fail == 0 else 1)
