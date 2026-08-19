import sys
sys.path.insert(0, r'd:\Yuyi Project\QianWuYuyi-AI_自动驾驶版\QianWuYuyi-AI')
from src.runtime.decision_intelligence import DecisionIntelligence
from src.runtime.action_persistence import ActionPersistenceManager

mgr = ActionPersistenceManager(path=r'd:\Yuyi Project\QianWuYuyi-AI_自动驾驶版\QianWuYuyi-AI\test_b11.jsonl')
intel = DecisionIntelligence(runtime_bridge=None, persistence=mgr)
print(f"Enabled: {intel.enabled}")
print(f"Analysis count before: {intel.analysis_count}")
result = intel.compute_health_score()
print(f"Result: {result.overall_score}")
print(f"Analysis count after: {intel.analysis_count}")
print(f"Last error: {intel.last_error}")
import os
print(f"File exists: {os.path.exists(r'd:\Yuyi Project\QianWuYuyi-AI_自动驾驶版\QianWuYuyi-AI\test_b11.jsonl')}")
