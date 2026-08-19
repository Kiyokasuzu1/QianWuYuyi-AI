import sys, os
print('exec:', sys.executable)
print('cwd:', os.getcwd())
print('path0:', sys.path[0])
try:
    import pydantic_core
    print('pydantic_core at:', pydantic_core.__file__)
except Exception as e:
    print('ERR:', e)
try:
    from src.orchestrator import Orchestrator
    print('orchestrator OK')
except Exception as e:
    print('orch ERR:', e)
