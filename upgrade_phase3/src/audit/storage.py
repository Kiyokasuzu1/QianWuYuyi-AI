import json
import os
from pathlib import Path
from typing import List, Optional

from src.audit.record import AuditRecord


class AuditStorage:
    def __init__(self, data_dir: str = "data/audit"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.json_file = self.data_dir / "audit_logs.json"

        if not self.json_file.exists():
            self._init_json_file()

    def _init_json_file(self):
        with open(self.json_file, "w", encoding="utf-8") as f:
            json.dump({"version": "1.0", "records": []}, f, ensure_ascii=False, indent=2)

    def save(self, record: AuditRecord):
        self._save_to_json(record)

    def _save_to_json(self, record: AuditRecord):
        try:
            with open(self.json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            data["records"].append(record.to_dict())

            if len(data["records"]) > 10000:
                data["records"] = data["records"][-10000:]

            with open(self.json_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[AuditStorage] JSON 保存失败: {e}")

    def load(self, limit: int = 100, offset: int = 0) -> List[AuditRecord]:
        try:
            with open(self.json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            records = data.get("records", [])
            records.sort(key=lambda x: x["timestamp"], reverse=True)

            start = offset
            end = start + limit
            return [AuditRecord(**r) for r in records[start:end]]
        except Exception as e:
            print(f"[AuditStorage] 加载失败: {e}")
            return []

    def load_by_user(self, user_id: str, limit: int = 50) -> List[AuditRecord]:
        all_records = self.load(limit=1000)
        return [r for r in all_records if r.user_id == user_id][:limit]

    def load_by_type(self, operation_type: str, limit: int = 50) -> List[AuditRecord]:
        all_records = self.load(limit=1000)
        return [r for r in all_records if r.operation_type == operation_type][:limit]

    def load_by_time_range(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 50
    ) -> List[AuditRecord]:
        all_records = self.load(limit=1000)

        def in_range(record):
            ts = record.timestamp
            if start_time and ts < start_time:
                return False
            if end_time and ts > end_time:
                return False
            return True

        return [r for r in all_records if in_range(r)][:limit]

    def count(self) -> int:
        try:
            with open(self.json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return len(data.get("records", []))
        except Exception:
            return 0


_global_storage = None


def get_audit_storage() -> AuditStorage:
    global _global_storage
    if _global_storage is None:
        _global_storage = AuditStorage()
    return _global_storage


def save_audit_record(record: AuditRecord):
    get_audit_storage().save(record)


def load_audit_records(limit: int = 100, offset: int = 0) -> List[AuditRecord]:
    return get_audit_storage().load(limit=limit, offset=offset)