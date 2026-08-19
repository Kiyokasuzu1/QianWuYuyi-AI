from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional
import uuid


@dataclass
class AuditRecord:
    record_id: str = field(default_factory=lambda: f"audit_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    user_id: str = ""
    user_name: str = ""

    operation_type: str = ""
    source: str = ""
    action: str = ""

    detail: Dict[str, Any] = field(default_factory=dict)
    before_state: Dict[str, Any] = field(default_factory=dict)
    after_state: Dict[str, Any] = field(default_factory=dict)

    result: str = "success"
    error_message: str = ""

    correlation_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "operation_type": self.operation_type,
            "source": self.source,
            "action": self.action,
            "detail": self.detail,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "result": self.result,
            "error_message": self.error_message,
            "correlation_id": self.correlation_id,
            "metadata": self.metadata,
        }


def record_audit_log(
    operation_type: str,
    source: str,
    action: str = "",
    user_id: str = "",
    user_name: str = "",
    detail: Dict[str, Any] = None,
    before_state: Dict[str, Any] = None,
    after_state: Dict[str, Any] = None,
    result: str = "success",
    error_message: str = "",
    correlation_id: str = "",
) -> AuditRecord:
    from src.audit.storage import save_audit_record

    record = AuditRecord(
        operation_type=operation_type,
        source=source,
        action=action,
        user_id=user_id,
        user_name=user_name,
        detail=detail or {},
        before_state=before_state or {},
        after_state=after_state or {},
        result=result,
        error_message=error_message,
        correlation_id=correlation_id,
    )

    try:
        save_audit_record(record)
    except Exception as e:
        print(f"[Audit] 保存审计记录失败: {e}")

    return record


def audit_event_handler(event):
    return record_audit_log(
        operation_type=event.event_type,
        source=event.source,
        detail=event.data,
        user_id=event.data.get("user_id", ""),
    )