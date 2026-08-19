import sys, os
sys.path.insert(0, os.getcwd())
print('DEBUG_START', flush=True)
print('exec:', sys.executable, flush=True)
print('cwd:', os.getcwd(), flush=True)
print('path0:', sys.path[0], flush=True)
try:
    from src.orchestrator import Orchestrator
    print('orchestrator: OK', flush=True)
except Exception as e:
    import traceback
    print('orchestrator FAIL:', e, flush=True)
    traceback.print_exc(file=sys.stdout)
    sys.stdout.flush()
