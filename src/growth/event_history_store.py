"""
事件历史存储（EventHistoryStore）
职责：保存和查询事件历史记录
抽象接口，未来可换 SQLite
"""

import json
import time
from pathlib import Path
from typing import Dict, Optional


class EventHistoryStore:
    def __init__(self, file_path: str = "data/event_history.json"):
        self.file_path = Path(file_path)
        self._data: Dict = {}
        self._load()

    def _load(self):
        if not self.file_path.exists():
            self._data = {}
            return
        # 部署加固: 损坏 JSON 不再抛异常阻断构造——备份后降级为空字典。
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._data = data if isinstance(data, dict) else {}
        except Exception:
            try:
                backup = str(self.file_path) + f".corrupt.{int(time.time())}"
                self.file_path.rename(backup)
            except Exception:
                pass
            self._data = {}

    def _save(self):
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        # 部署加固: 截断写 → 原子写（临时文件 + fsync + os.replace），
        # 进程崩溃不会留下半写文件。
        from src.memory.atomic_write import atomic_write_json

        atomic_write_json(str(self.file_path), self._data)

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