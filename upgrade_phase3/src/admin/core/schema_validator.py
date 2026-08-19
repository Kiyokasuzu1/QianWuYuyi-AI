"""
配置 Schema 验证

每个模块可以自带 schema.yaml 定义自己的配置字段规则，
SchemaValidator 自动发现并合并，用于配置修改前的深度验证。

Schema 格式（简化版 JSON Schema 风格）：
    schema.yaml:
        enabled:
            type: boolean
            required: true
        host:
            type: string
            default: "0.0.0.0"
        port:
            type: integer
            min: 1
            max: 65535
            required: true
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SchemaField:
    """单个配置字段的 Schema 定义"""
    name: str
    type: str = "string"             # string / integer / float / boolean / dict / list
    required: bool = False
    default: Any = None
    min: Optional[float] = None      # 数值最小值或字符串最小长度
    max: Optional[float] = None      # 数值最大值或字符串最大长度
    choices: List[Any] = field(default_factory=list)  # 枚举值
    description: str = ""


@dataclass
class ModuleSchema:
    """一个模块的完整 Schema"""
    module_name: str
    fields: Dict[str, SchemaField] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            name: {
                "type": f.type,
                "required": f.required,
                "default": f.default,
                "min": f.min,
                "max": f.max,
                "choices": f.choices,
                "description": f.description,
            }
            for name, f in self.fields.items()
        }


class SchemaValidator:
    """
    配置 Schema 验证器

    自动发现 src/*/schema.yaml 或 src/config/schema/*.yaml 中的 schema 定义，
    提供配置验证功能。
    """

    _TYPE_MAP = {
        "string": str,
        "integer": int,
        "float": (int, float),
        "boolean": bool,
        "dict": dict,
        "list": list,
    }

    def __init__(self, src_root: Optional[str] = None,
                 schema_dir: Optional[str] = None):
        """
        Args:
            src_root: src/ 目录路径，用于扫描模块自带 schema.yaml
            schema_dir: 集中式 schema 目录路径
        """
        self._src_root = Path(src_root) if src_root else Path(__file__).parent.parent.parent
        self._schema_dir = Path(schema_dir) if schema_dir else Path(__file__).parent / "schema"
        self._schemas: Dict[str, ModuleSchema] = {}
        self._loaded = False

    def load(self) -> Dict[str, ModuleSchema]:
        """加载所有 Schema"""
        if self._loaded:
            return self._schemas

        # 1. 从模块目录扫描 schema.yaml
        for item in sorted(self._src_root.iterdir()):
            if not item.is_dir():
                continue
            if item.name.startswith("_") or item.name == "core":
                continue
            schema_file = item / "schema.yaml"
            if schema_file.exists():
                self._load_schema_file(item.name, schema_file)

        # 2. 从集中式 schema 目录加载
        if self._schema_dir and self._schema_dir.exists():
            for schema_file in sorted(self._schema_dir.glob("*.yaml")):
                module_name = schema_file.stem
                if module_name not in self._schemas:
                    self._load_schema_file(module_name, schema_file)

        self._loaded = True
        logger.info(f"Schema 加载完成，共 {len(self._schemas)} 个模块")
        return self._schemas

    def _load_schema_file(self, module_name: str, schema_file: Path):
        """从 yaml 文件加载单个模块的 Schema"""
        try:
            with open(schema_file, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

            fields: Dict[str, SchemaField] = {}
            for field_name, field_def in raw.items():
                if not isinstance(field_def, dict):
                    continue
                fields[field_name] = SchemaField(
                    name=field_name,
                    type=field_def.get("type", "string"),
                    required=field_def.get("required", False),
                    default=field_def.get("default"),
                    min=field_def.get("min"),
                    max=field_def.get("max"),
                    choices=field_def.get("choices", []),
                    description=field_def.get("description", ""),
                )

            self._schemas[module_name] = ModuleSchema(
                module_name=module_name,
                fields=fields,
            )
            logger.debug(f"加载 Schema: {module_name} ({len(fields)} 个字段)")

        except Exception as e:
            logger.warning(f"加载 Schema 失败 {module_name}: {e}")

    def validate_section(self, section_name: str,
                         config: Dict[str, Any]) -> List[str]:
        """
        验证单个配置区段

        Args:
            section_name: 区段名（如 "remote"）
            config: 该区段的配置字典

        Returns:
            错误信息列表
        """
        self.load()
        errors: List[str] = []

        schema = self._schemas.get(section_name)
        if schema is None:
            # 没有 schema 的区段不做验证
            return errors

        for field_name, field in schema.fields.items():
            value = config.get(field_name)

            # 必填检查
            if field.required and value is None:
                errors.append(
                    f"'{section_name}.{field_name}' 是必填字段"
                )
                continue

            if value is None and not field.required:
                continue

            # 类型检查
            expected_type = self._TYPE_MAP.get(field.type)
            if expected_type and not isinstance(value, expected_type):
                # 特殊处理：boolean 不接受 0/1（int 是 bool 的父类，isinstance(True, int) == True）
                if field.type == "boolean":
                    # bool 是 int 子类，isinstance(bool_val, int) 为 True，
                    # 所以需要单独判断
                    errors.append(
                        f"'{section_name}.{field_name}' 类型错误: "
                        f"期望 boolean，实际 {type(value).__name__}"
                    )
                    continue
                else:
                    errors.append(
                        f"'{section_name}.{field_name}' 类型错误: "
                        f"期望 {field.type}，实际 {type(value).__name__}"
                    )
                    continue

            # 范围检查
            if field.min is not None and isinstance(value, (int, float)):
                if value < field.min:
                    errors.append(
                        f"'{section_name}.{field_name}' 小于最小值 {field.min}: {value}"
                    )
            if field.max is not None and isinstance(value, (int, float)):
                if value > field.max:
                    errors.append(
                        f"'{section_name}.{field_name}' 大于最大值 {field.max}: {value}"
                    )

            # 字符串长度检查
            if field.type == "string" and isinstance(value, str):
                if field.min is not None and len(value) < field.min:
                    errors.append(
                        f"'{section_name}.{field_name}' 长度不足 {field.min}: {len(value)}"
                    )
                if field.max is not None and len(value) > field.max:
                    errors.append(
                        f"'{section_name}.{field_name}' 超过最大长度 {field.max}: {len(value)}"
                    )

            # 枚举检查
            if field.choices and value not in field.choices:
                errors.append(
                    f"'{section_name}.{field_name}' 值不在允许范围内: "
                    f"{value}，可选值: {field.choices}"
                )

        return errors

    def validate_all(self, config: Dict[str, Any]) -> Dict[str, List[str]]:
        """
        验证整个配置

        Returns:
            {section_name: [errors]} 字典
        """
        self.load()
        all_errors: Dict[str, List[str]] = {}

        for section_name in self._schemas:
            section_config = config.get(section_name, {})
            if not isinstance(section_config, dict):
                all_errors[section_name] = [f"'{section_name}' 必须是字典"]
                continue

            errors = self.validate_section(section_name, section_config)
            if errors:
                all_errors[section_name] = errors

        return all_errors

    def get_schema(self, module_name: str) -> Optional[ModuleSchema]:
        """获取指定模块的 Schema"""
        self.load()
        return self._schemas.get(module_name)

    def get_all_schemas(self) -> Dict[str, ModuleSchema]:
        """获取所有 Schema"""
        self.load()
        return dict(self._schemas)

    def get_defaults(self, module_name: str) -> Dict[str, Any]:
        """获取模块的默认配置"""
        self.load()
        schema = self._schemas.get(module_name)
        if not schema:
            return {}
        return {
            name: field.default
            for name, field in schema.fields.items()
            if field.default is not None
        }
