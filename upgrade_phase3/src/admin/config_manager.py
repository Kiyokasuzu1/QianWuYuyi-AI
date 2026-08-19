"""
兼容层：从 src.admin.core.config_manager 重新导出

旧代码 import src.admin.config_manager 仍可正常工作。
"""

from .core.config_manager import ConfigManager, ConfigValidationError

__all__ = ["ConfigManager", "ConfigValidationError"]
