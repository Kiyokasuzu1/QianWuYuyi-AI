"""
事件历史存储（EventHistoryStore）
职责：保存和查询事件历史记录
抽象接口，未来可换 SQLite
"""

import json
from pathlib import Path
from typing import Dict, Optional


class EventHistoryStore:
    def __init__(self, file_path: str = "data/event_history.json"):
        self.file_path = Path(file_path)
        self._data: Dict = {}
        self._load()

    def _load(self):
        if self.file_path.exists():
            with open(self.file_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        else:
            self._data = {}

    def _save(self):
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, event_id: str) -> Optional[Dict]:
        return self._data.get(event_id)

    def put(self, event_id: str, record: Dict):
        self._data[event_id] = record
        self._save()

    def update(self, event_id: str, updates: Dict):
        if event_id in self._data:
            self._data[event_id].update(updates)
            self._save()

    def exists(self, event_id: str) -> bool:
        return event_id in self._data

    def get_all(self) -> Dict:
        return self._data

    def get_by_category(self, category: str) -> list:
        return [v for v in self._data.values() if v.get("category") == category]