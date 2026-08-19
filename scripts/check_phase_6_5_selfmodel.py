"""Step 4c: 验证 routes.py 中的 selfmodel endpoint 已正确注册。"""
import re
import sys
from pathlib import Path

src = Path("src/admin/api/routes.py").read_text(encoding="utf-8")

new_eps = re.findall(r"@admin_bp\.route\(\"([^\"]+)\"", src)
selfmodel_eps = sorted(set(ep for ep in new_eps if "selfmodel" in ep))

print("SelfModel endpoints count:", len(selfmodel_eps))
for e in selfmodel_eps:
    print(" ", e)

# 验证 import 不报错
try:
    from src.admin.api.routes import admin_bp  # noqa
    print("routes.py import OK")
except Exception as e:
    print(f"routes.py import FAIL: {e}")
    sys.exit(1)

# 验证 provider 可被实例化
try:
    from src.admin.self_model_provider import SelfModelProvider
    p = SelfModelProvider()
    print("SelfModelProvider init OK")
    status = p.get_status()
    print("get_status:", status)
except Exception as e:
    print(f"SelfModelProvider init FAIL: {e}")
    sys.exit(1)
