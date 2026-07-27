from typing import List, Optional
from datetime import datetime, timedelta

from src.audit.record import AuditRecord
from src.audit.storage import get_audit_storage


class AuditQuery:
    def __init__(self):
        self.storage = get_audit_storage()

    def get_recent(self, limit: int = 50) -> List[AuditRecord]:
        return self.storage.load(limit=limit)

    def get_by_user(self, user_id: str, limit: int = 50) -> List[AuditRecord]:
        return self.storage.load_by_user(user_id, limit=limit)

    def get_by_type(self, operation_type: str, limit: int = 50) -> List[AuditRecord]:
        return self.storage.load_by_type(operation_type, limit=limit)

    def get_by_time_range(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 50
    ) -> List[AuditRecord]:
        return self.storage.load_by_time_range(start_time, end_time, limit=limit)

    def get_today(self) -> List[AuditRecord]:
        today = datetime.now().strftime("%Y-%m-%d")
        start_time = f"{today}T00:00:00"
        end_time = f"{today}T23:59:59"
        return self.storage.load_by_time_range(start_time, end_time)

    def get_summary(self, days: int = 7) -> dict:
        end_time = datetime.now().isoformat()
        start_time = (datetime.now() - timedelta(days=days)).isoformat()

        records = self.storage.load_by_time_range(start_time, end_time)

        type_counts = {}
        for record in records:
            type_counts[record.operation_type] = type_counts.get(record.operation_type, 0) + 1

        user_counts = {}
        for record in records:
            user_counts[record.user_id] = user_counts.get(record.user_id, 0) + 1

        return {
            "total_records": len(records),
            "time_range": {"start": start_time, "end": end_time},
            "operation_type_distribution": type_counts,
            "user_distribution": user_counts,
            "daily_counts": self._get_daily_counts(records, days),
        }

    def _get_daily_counts(self, records: List[AuditRecord], days: int) -> dict:
        counts = {}
        for i in range(days):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            counts[date] = 0

        for record in records:
            date = record.timestamp[:10]
            if date in counts:
                counts[date] += 1

        return counts


_global_query = None


def get_audit_query() -> AuditQuery:
    global _global_query
    if _global_query is None:
        _global_query = AuditQuery()
    return _global_query


def get_recent_audit(limit: int = 50) -> List[AuditRecord]:
    return get_audit_query().get_recent(limit)


def get_audit_summary(days: int = 7) -> dict:
    return get_audit_query().get_summary(days)