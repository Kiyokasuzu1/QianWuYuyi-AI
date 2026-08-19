"""
羽依统一持久化管理器 (YuyiPersistence)
负责羽依核心数据的保存与恢复。
支持全局数据与用户数据分离存储，全部携带 schema_version。
"""
import json
from pathlib import Path
from typing import Optional, Dict, Any, List

from src.identity.user_context import UserContext


class YuyiPersistence:
    CURRENT_SCHEMA_VERSION = 1

    def __init__(self, base_dir: str = "data", user_context: Optional[UserContext] = None):
        self.base_dir = Path(base_dir)
        self.user_context = user_context
        self._ensure_dirs()

    def _ensure_dirs(self):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if self.user_context:
            self._user_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _user_dir(self) -> Optional[Path]:
        if not self.user_context:
            return None
        # 用户目录：base_dir/users/{platform}_{user_id}
        return self.base_dir / "users" / f"{self.user_context.platform}_{self.user_context.user_id}"

    def _save_json(self, filepath: Path, data: Dict[str, Any]):
        filepath.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self.CURRENT_SCHEMA_VERSION,
            "data": data,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _load_json(self, filepath: Path) -> Optional[Dict[str, Any]]:
        if not filepath.exists():
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            payload = json.load(f)
        version = payload.get("schema_version", 0)
        data = payload.get("data", {})
        if version < self.CURRENT_SCHEMA_VERSION:
            data = self._migrate(version, data, filepath.name)
        return data

    def _migrate(self, from_version: int, data: Dict[str, Any], filename: str) -> Dict[str, Any]:
        return data

    # ========== 全局数据 ==========

    def save_self_model(self, data: Dict[str, Any]):
        self._save_json(self.base_dir / "self_model.json", data)

    def load_self_model(self) -> Optional[Dict[str, Any]]:
        return self._load_json(self.base_dir / "self_model.json")

    def save_agreements(self, agreements: List[Dict[str, Any]]):
        self._save_json(self.base_dir / "agreements.json", {"agreements": agreements})

    def load_agreements(self) -> List[Dict[str, Any]]:
        result = self._load_json(self.base_dir / "agreements.json")
        if result is None:
            return []
        return result.get("agreements", [])

    def save_narrative_history(self, data: Dict[str, Any]):
        self._save_json(self.base_dir / "narrative_history.json", data)

    def load_narrative_history(self) -> Optional[Dict[str, Any]]:
        return self._load_json(self.base_dir / "narrative_history.json")

    # ========== 用户数据 ==========

    def save_relationship_state(self, data: Dict[str, Any]):
        if not self._user_dir:
            return
        self._save_json(self._user_dir / "relationship_state.json", data)

    def load_relationship_state(self) -> Optional[Dict[str, Any]]:
        if not self._user_dir:
            return None
        return self._load_json(self._user_dir / "relationship_state.json")

    def save_relationship_profile(self, data: Dict[str, Any]):
        if not self._user_dir:
            return
        self._save_json(self._user_dir / "relationship_profile.json", data)

    def load_relationship_profile(self) -> Optional[Dict[str, Any]]:
        if not self._user_dir:
            return None
        return self._load_json(self._user_dir / "relationship_profile.json")

    def save_memory(self, memories: List[Dict[str, Any]]):
        if not self._user_dir:
            return
        self._save_json(self._user_dir / "memory.json", {"memories": memories})

    def load_memory(self) -> List[Dict[str, Any]]:
        if not self._user_dir:
            return []
        result = self._load_json(self._user_dir / "memory.json")
        if result is None:
            return []
        return result.get("memories", [])