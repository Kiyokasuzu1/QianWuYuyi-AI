import sys
print('exec:', sys.executable, flush=True)
print('python version:', sys.version, flush=True)
print('PYTHONHOME:', sys.prefix, flush=True)
print('--- sys.path ---', flush=True)
for p in sys.path:
    print(' ', p, flush=True)
