"""Launch script for Yuyi Admin Console"""
import sys
import os

# 仅当 venv 的 Python 版本与当前解释器兼容时,才注入 venv site-packages
# 避免 Linux .so 或其他平台不兼容包污染系统 site-packages。
# Phase C.1 P0-1: 修复 /v1/chat/completions 500 (pydantic_core 跨平台不兼容)
import re as _re
_venv_py_match = _re.search(r"python(\d+)\.(\d+)", "python3.11")
if _venv_py_match:
    _venv_major, _venv_minor = int(_venv_py_match.group(1)), int(_venv_py_match.group(2))
    _cur_major, _cur_minor = sys.version_info.major, sys.version_info.minor
    _compatible = (_venv_major == _cur_major and _venv_minor == _cur_minor)
    venv_site = os.path.join(os.path.dirname(__file__), "venv", "lib", f"python{_venv_major}.{_venv_minor}", "site-packages")
    if _compatible and os.path.isdir(venv_site):
        sys.path.insert(0, venv_site)
    elif os.path.isdir(venv_site):
        sys.stderr.write(
            f"[run_server] 跳过不兼容的 venv (需要 python{_venv_major}.{_venv_minor}, 当前 python{_cur_major}.{_cur_minor}): {venv_site}\n"
        )

from api_server import app, init_orchestrator

if __name__ == "__main__":
    init_orchestrator()
    print("羽依 AI 控制中心启动中...")
    print("访问: http://127.0.0.1:5000/admin")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
