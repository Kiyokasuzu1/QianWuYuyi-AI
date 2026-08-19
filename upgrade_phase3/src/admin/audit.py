"""
兼容层：从 src.admin.core.audit 重新导出

旧代码 import src.admin.audit 仍可正常工作。
"""

from .core.audit import AuditLogger, AuditEventType, AuditOperatorType

__all__ = ["AuditLogger", "AuditEventType", "AuditOperatorType"]
