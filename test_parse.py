code = '''if self._decision_observer is not None:
    try:
        last_at = None
        snap_count = 0
        try:
            last_at = 1
        except Exception:
            pass
        decision_observability["snapshot_count"] = snap_count
    decision_observability["last_snapshot"] = last_at
'''
import ast
try:
    ast.parse(code)
    print("OK")
except SyntaxError as e:
    print(f"Error: {e}")
