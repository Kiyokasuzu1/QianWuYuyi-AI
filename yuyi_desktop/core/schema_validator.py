# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/schema_validator.py

Phase C.10.4.3 —— Schema Version Validation

功能:
- 检查 API Response 的 schema_version
- 兼容:继续
- 不兼容:返回 SchemaError(阻断进入 Service)

目的:
- 防止未来 Server API 修改导致 Desktop 静默错误
- 早期发现协议不匹配

规则:
- 严格兼容列表:[EXPECTED_SCHEMA_VERSION, ...]
- 缺失 schema_version 视为不兼容(强制要求)
- 解析失败的 schema_version 视为不兼容
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

# 统一从 errors 模块导出 SchemaError(DesktopError 子类),
# 避免在不同模块出现同名 Exception 类。
from yuyi_desktop.core.errors import SchemaError  # noqa: F401  (re-export)

logger = logging.getLogger(__name__)


# ============================================================
# Validation Result
# ============================================================
@dataclass
class SchemaValidationResult:
    """Schema 校验结果。"""

    ok: bool
    expected: str
    actual: str
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "expected": str(self.expected),
            "actual": str(self.actual),
            "reason": str(self.reason or ""),
        }


# ============================================================
# SchemaValidator
# ============================================================
class SchemaValidator:
    """
    API Response schema_version 校验器。

    行为:
        validate(envelope) -> SchemaValidationResult
        assert_compatible(envelope) -> None / raise SchemaError
    """

    # 已知兼容的 schema_version 列表
    COMPATIBLE_VERSIONS = ("1.0",)

    # 简单 semver-like 模式:数字.数字(.数字)*
    _VERSION_RE = re.compile(r"^\d+(\.\d+)*$")

    def __init__(
        self,
        expected_versions: Optional[Tuple[str, ...]] = None,
    ) -> None:
        if expected_versions is None:
            expected_versions = self.COMPATIBLE_VERSIONS
        # 校验 expected_versions 本身合法
        cleaned: list = []
        for v in expected_versions:
            if not isinstance(v, str) or not self._VERSION_RE.match(v.strip()):
                raise ValueError(f"invalid expected version: {v!r}")
            cleaned.append(v.strip())
        if not cleaned:
            cleaned = list(self.COMPATIBLE_VERSIONS)
        self._expected_versions = tuple(cleaned)

    @property
    def expected_versions(self) -> Tuple[str, ...]:
        return self._expected_versions

    # --------------------------------------------------------
    # 校验
    # --------------------------------------------------------
    def validate(
        self,
        envelope: Any,
    ) -> SchemaValidationResult:
        """
        校验 envelope 的 schema_version。

        Returns:
            SchemaValidationResult(ok / expected / actual / reason)
        """
        expected = self._expected_versions[0]
        if not isinstance(envelope, dict):
            return SchemaValidationResult(
                ok=False,
                expected=expected,
                actual="",
                reason="envelope_not_dict",
            )
        actual = envelope.get("schema_version", "")
        if not actual:
            return SchemaValidationResult(
                ok=False,
                expected=expected,
                actual="",
                reason="missing_schema_version",
            )
        actual = str(actual).strip()
        if not self._VERSION_RE.match(actual):
            return SchemaValidationResult(
                ok=False,
                expected=expected,
                actual=actual,
                reason="malformed_schema_version",
            )
        if actual in self._expected_versions:
            return SchemaValidationResult(
                ok=True,
                expected=expected,
                actual=actual,
            )
        return SchemaValidationResult(
            ok=False,
            expected=expected,
            actual=actual,
            reason="version_mismatch",
        )

    def is_compatible(
        self,
        envelope: Any,
    ) -> bool:
        return self.validate(envelope).ok

    def assert_compatible(
        self,
        envelope: Any,
    ) -> SchemaValidationResult:
        """
        校验 envelope,不兼容时抛 SchemaError。
        """
        result = self.validate(envelope)
        if not result.ok:
            msg = (
                f"schema_mismatch: expected={result.expected} "
                f"actual={result.actual!r} reason={result.reason}"
            )
            raise SchemaError(
                message=msg,
                expected=result.expected,
                actual=result.actual,
                reason=result.reason,
            )
        return result


# ============================================================
# 模块级单例
# ============================================================
_default_validator: Optional[SchemaValidator] = None


def get_schema_validator() -> SchemaValidator:
    """获取默认 SchemaValidator 单例。"""
    global _default_validator
    if _default_validator is None:
        _default_validator = SchemaValidator()
    return _default_validator


def reset_schema_validator_for_testing() -> SchemaValidator:
    """测试用:重置默认 validator。"""
    global _default_validator
    _default_validator = SchemaValidator()
    return _default_validator


__all__ = [
    "SchemaError",
    "SchemaValidationResult",
    "SchemaValidator",
    "get_schema_validator",
    "reset_schema_validator_for_testing",
]
