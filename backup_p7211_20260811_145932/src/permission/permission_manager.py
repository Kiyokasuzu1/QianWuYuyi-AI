import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from src.permission.constants import (
    PERMISSION_LEVEL,
    PERMISSION_STAGE,
    PERMISSION_TYPE,
    STAGE_PERMISSIONS,
)


class PermissionManager:
    def __init__(self, data_dir: str = "data/permission"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.permission_file = self.data_dir / "permissions.json"

        if not self.permission_file.exists():
            self._init_permission_file()

        self._permissions: Dict[str, Dict] = {}
        self._load_permissions()

    def _init_permission_file(self):
        initial = {
            "version": "1.0",
            "stage": PERMISSION_STAGE["STAGE_1"],
            "permissions": {},
            "granted_at": {},
            "revoked_at": {},
        }
        with open(self.permission_file, "w", encoding="utf-8") as f:
            json.dump(initial, f, ensure_ascii=False, indent=2)

    def _load_permissions(self):
        try:
            with open(self.permission_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._stage = data.get("stage", PERMISSION_STAGE["STAGE_1"])
            self._permissions = data.get("permissions", {})
            self._granted_at = data.get("granted_at", {})
            self._revoked_at = data.get("revoked_at", {})
        except Exception:
            self._stage = PERMISSION_STAGE["STAGE_1"]
            self._permissions = {}
            self._granted_at = {}
            self._revoked_at = {}

    def _save_permissions(self):
        data = {
            "version": "1.0",
            "stage": self._stage,
            "permissions": self._permissions,
            "granted_at": self._granted_at,
            "revoked_at": self._revoked_at,
        }
        try:
            with open(self.permission_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PermissionManager] 保存失败: {e}")

    def get_current_stage(self) -> str:
        return self._stage

    def set_stage(self, stage: str) -> bool:
        if stage not in PERMISSION_STAGE.values():
            return False

        self._stage = stage
        self._save_permissions()

        for perm_type in STAGE_PERMISSIONS[stage]:
            self.grant(perm_type)

        return True

    def promote_stage(self) -> bool:
        stages = [
            PERMISSION_STAGE["STAGE_1"],
            PERMISSION_STAGE["STAGE_2"],
            PERMISSION_STAGE["STAGE_3"],
        ]
        current_idx = stages.index(self._stage) if self._stage in stages else 0

        if current_idx < len(stages) - 1:
            self.set_stage(stages[current_idx + 1])
            return True
        return False

    def demote_stage(self) -> bool:
        stages = [
            PERMISSION_STAGE["STAGE_1"],
            PERMISSION_STAGE["STAGE_2"],
            PERMISSION_STAGE["STAGE_3"],
        ]
        current_idx = stages.index(self._stage) if self._stage in stages else 0

        if current_idx > 0:
            self.set_stage(stages[current_idx - 1])
            return True
        return False

    def check(self, permission_type: str) -> bool:
        if permission_type not in PERMISSION_TYPE.values():
            return False

        if self._stage == PERMISSION_STAGE["STAGE_3"]:
            return True

        return self._permissions.get(permission_type, PERMISSION_LEVEL["NONE"]) != PERMISSION_LEVEL["NONE"]

    def grant(self, permission_type: str) -> bool:
        if permission_type not in PERMISSION_TYPE.values():
            return False

        self._permissions[permission_type] = PERMISSION_LEVEL["CONTROL"]
        self._granted_at[permission_type] = datetime.now().isoformat()

        if permission_type in self._revoked_at:
            del self._revoked_at[permission_type]

        self._save_permissions()
        return True

    def revoke(self, permission_type: str) -> bool:
        if permission_type not in PERMISSION_TYPE.values():
            return False

        self._permissions[permission_type] = PERMISSION_LEVEL["NONE"]
        self._revoked_at[permission_type] = datetime.now().isoformat()

        if permission_type in self._granted_at:
            del self._granted_at[permission_type]

        self._save_permissions()
        return True

    def get_permission_info(self, permission_type: str) -> Dict:
        return {
            "type": permission_type,
            "level": self._permissions.get(permission_type, PERMISSION_LEVEL["NONE"]),
            "granted_at": self._granted_at.get(permission_type, ""),
            "revoked_at": self._revoked_at.get(permission_type, ""),
            "current_stage": self._stage,
        }

    def get_all_permissions(self) -> Dict:
        return {
            "stage": self._stage,
            "permissions": {
                perm_type: self.get_permission_info(perm_type)
                for perm_type in PERMISSION_TYPE.values()
            },
        }

    def reset_all(self):
        self._stage = PERMISSION_STAGE["STAGE_1"]
        self._permissions = {}
        self._granted_at = {}
        self._revoked_at = {}

        for perm_type in STAGE_PERMISSIONS[PERMISSION_STAGE["STAGE_1"]]:
            self.grant(perm_type)

        self._save_permissions()


_global_manager = None


def get_permission_manager() -> PermissionManager:
    global _global_manager
    if _global_manager is None:
        _global_manager = PermissionManager()
    return _global_manager


def check_permission(permission_type: str) -> bool:
    return get_permission_manager().check(permission_type)


def require_permission(permission_type: str):
    def decorator(func):
        def wrapper(*args, **kwargs):
            if not check_permission(permission_type):
                raise PermissionError(f"缺少权限: {permission_type}")
            return func(*args, **kwargs)
        return wrapper
    return decorator


def request_permission(permission_type: str) -> bool:
    return check_permission(permission_type)


def grant_permission(permission_type: str) -> bool:
    return get_permission_manager().grant(permission_type)


def revoke_permission(permission_type: str) -> bool:
    return get_permission_manager().revoke(permission_type)