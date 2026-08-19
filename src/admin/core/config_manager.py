"""
配置版本管理器

支持配置的读取、修改、验证、备份和回滚。
每次修改都会生成时间戳备份，防止误操作导致系统不可用。
"""

from __future__ import annotations

import copy
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """配置验证失败异常"""
    pass


class ConfigManager:
    """
    配置版本管理器

    流程：
        读取 → 验证 → 备份 → 写入 → 通知模块
    """

    # 已知的顶层配置键及其字段约束
    _SCHEMA = {
        "remote": {
            "enabled": bool,
            "host": str,
            "port": int,
            "auth_token": str,
            "heartbeat_timeout": (int, float),
        },
        "screen": {
            "enabled": bool,
            "capture_timeout": (int, float),
            "ocr_language": str,
            "max_image_width": int,
        },
        "control": {
            "enabled": bool,
            "action_timeout": (int, float),
        },
        "token_opt": {
            "enabled": bool,
        },
        "initiative": {
            "astrbot_url": str,
            "onebot_url": str,
            "api_type": str,
            "target_user_qq": str,
            "min_check_seconds": (int, float),
            "max_check_seconds": (int, float),
            "check_randomize": bool,
            "max_backoff_multiplier": (int, float),
            "request_timeout": (int, float),
        },
        "admin": {
            "enabled": bool,
            "host": str,
            "token_required": bool,
            "session_timeout": (int, float),
        },
    }

    def __init__(self, config_path: Optional[str] = None):
        """
        Args:
            config_path: config.yaml 的路径，默认为项目根目录下
        """
        if config_path:
            self._config_path = Path(config_path)
        else:
            self._config_path = Path(__file__).parent.parent.parent / "config.yaml"

        self._backup_dir = self._config_path.parent / "config" / "backup"
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Optional[Dict] = None

    def read(self, use_cache: bool = True) -> Dict:
        """
        读取当前配置

        Args:
            use_cache: 是否使用缓存（避免频繁读磁盘）

        Returns:
            配置字典
        """
        if use_cache and self._cache is not None:
            return copy.deepcopy(self._cache)

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            self._cache = config
            return copy.deepcopy(config)
        except FileNotFoundError:
            logger.warning(f"配置文件不存在: {self._config_path}")
            return {}
        except yaml.YAMLError as e:
            raise ConfigValidationError(f"YAML 格式错误: {e}")

    def write(self, config: Dict, operator: str = "system",
              reason: str = "") -> Dict[str, Any]:
        """
        写入配置（带验证和备份）

        流程：验证 → 备份当前 → 写入新配置 → 清缓存

        Args:
            config: 完整的配置字典
            operator: 操作者标识
            reason: 修改原因

        Returns:
            {"success": bool, "backup_file": str, "errors": list}
        """
        result = {"success": False, "backup_file": "", "errors": []}

        # 1. 验证
        errors = self.validate(config)
        if errors:
            result["errors"] = errors
            return result

        # 2. 备份当前配置
        backup_file = self._create_backup()
        result["backup_file"] = str(backup_file)

        # 3. 写入
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False,
                          allow_unicode=True, sort_keys=False)
            self._cache = config
            result["success"] = True
            logger.info(f"配置已更新（操作者: {operator}, 原因: {reason}）")

        except Exception as e:
            result["errors"].append(f"写入失败: {e}")
            logger.error(f"配置写入失败: {e}")

            # 尝试回滚
            self._rollback_file(backup_file)
            result["errors"].append("已自动回滚到备份版本")

        return result

    def update_section(self, section: str, values: Dict,
                       operator: str = "system",
                       reason: str = "") -> Dict[str, Any]:
        """
        更新配置中的某个区段

        Args:
            section: 区段名（如 "remote"）
            values: 要更新的键值对
            operator: 操作者
            reason: 修改原因

        Returns:
            同 write()
        """
        config = self.read(use_cache=False)
        if section not in config:
            config[section] = {}

        config[section].update(values)
        return self.write(config, operator=operator, reason=reason)

    def toggle_module(self, module_name: str, enabled: bool,
                      operator: str = "system") -> Dict[str, Any]:
        """
        切换模块的 enabled 状态

        Args:
            module_name: 模块名（对应 config.yaml 中的键名）
            enabled: 是否启用
            operator: 操作者

        Returns:
            同 write()
        """
        config = self.read(use_cache=False)
        if module_name not in config:
            config[module_name] = {}
        config[module_name]["enabled"] = enabled
        return self.write(config, operator=operator,
                         reason=f"切换模块 {module_name}: enabled={enabled}")

    def validate(self, config: Dict) -> List[str]:
        """
        验证配置合法性

        Returns:
            错误信息列表，空列表表示验证通过
        """
        errors = []

        if not isinstance(config, dict):
            return ["配置根节点必须是字典"]

        # 检查已知字段的类型
        for section_name, field_specs in self._SCHEMA.items():
            section = config.get(section_name)
            if section is None:
                continue
            if not isinstance(section, dict):
                errors.append(f"'{section_name}' 必须是字典")
                continue

            for field_name, expected_type in field_specs.items():
                value = section.get(field_name)
                if value is None:
                    continue  # 允许缺失
                if not isinstance(value, expected_type):
                    type_name = (expected_type.__name__
                                 if isinstance(expected_type, type)
                                 else str(expected_type))
                    errors.append(
                        f"'{section_name}.{field_name}' 类型错误: "
                        f"期望 {type_name}，实际 {type(value).__name__}"
                    )

        # 特殊值域检查
        remote = config.get("remote", {})
        port = remote.get("port")
        if port is not None and not (1 <= port <= 65535):
            errors.append(f"'remote.port' 超出范围 (1-65535): {port}")

        timeout = remote.get("heartbeat_timeout")
        if timeout is not None and timeout <= 0:
            errors.append(f"'remote.heartbeat_timeout' 必须大于 0: {timeout}")

        screen = config.get("screen", {})
        capture_timeout = screen.get("capture_timeout")
        if capture_timeout is not None and capture_timeout <= 0:
            errors.append(f"'screen.capture_timeout' 必须大于 0: {capture_timeout}")

        control = config.get("control", {})
        action_timeout = control.get("action_timeout")
        if action_timeout is not None and action_timeout <= 0:
            errors.append(f"'control.action_timeout' 必须大于 0: {action_timeout}")

        # 检查 API key 没有被硬编码
        llm = config.get("llm", {})
        api_key = llm.get("api_key", "")
        if api_key and not api_key.startswith("${") and len(api_key) > 10:
            errors.append("'llm.api_key' 不应在配置文件中硬编码，请使用环境变量")

        return errors

    def list_backups(self) -> List[Dict[str, Any]]:
        """
        列出所有配置备份

        Returns:
            备份信息列表，按时间倒序
        """
        backups = []
        for f in sorted(self._backup_dir.glob("config_*.yaml"), reverse=True):
            # 文件名格式: config_20260728_103000_123456.yaml
            name = f.stem  # config_20260728_103000_123456
            parts = name.split("_")
            if len(parts) >= 3:
                timestamp_str = f"{parts[1]}_{parts[2]}"
                try:
                    ts = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                    readable = ts.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    readable = timestamp_str
            else:
                readable = name

            backups.append({
                "filename": f.name,
                "path": str(f),
                "size": f.stat().st_size,
                "modified": readable,
            })

        return backups

    def rollback(self, backup_filename: str, operator: str = "system") -> Dict[str, Any]:
        """
        回滚到指定备份版本

        Args:
            backup_filename: 备份文件名（如 "config_20260728_103000.yaml"）
            operator: 操作者

        Returns:
            {"success": bool, "errors": list}
        """
        backup_path = self._backup_dir / backup_filename
        if not backup_path.exists():
            return {"success": False, "errors": [f"备份文件不存在: {backup_filename}"]}

        # 先验证备份文件的合法性
        try:
            with open(backup_path, "r", encoding="utf-8") as f:
                backup_config = yaml.safe_load(f)
            errors = self.validate(backup_config)
            if errors:
                return {"success": False, "errors": [f"备份文件验证失败: {e}" for e in errors]}
        except Exception as e:
            return {"success": False, "errors": [f"备份文件读取失败: {e}"]}

        # 备份当前版本（以便反悔）
        current_backup = self._create_backup()

        # 复制备份文件覆盖当前配置
        try:
            shutil.copy2(str(backup_path), str(self._config_path))
            self._cache = None  # 清缓存
            logger.info(f"配置已回滚到 {backup_filename}（操作者: {operator}）")
            return {"success": True, "errors": []}
        except Exception as e:
            return {"success": False, "errors": [f"回滚失败: {e}"]}

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        获取配置修改历史（基于备份文件）

        Args:
            limit: 最多返回条数

        Returns:
            备份信息列表
        """
        return self.list_backups()[:limit]

    def _create_backup(self) -> Path:
        """创建当前配置的时间戳备份"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_filename = f"config_{timestamp}.yaml"
        backup_path = self._backup_dir / backup_filename

        if self._config_path.exists():
            shutil.copy2(str(self._config_path), str(backup_path))

        # 清理旧备份（最多保留 30 个）
        self._cleanup_old_backups(max_backups=30)

        return backup_path

    def _rollback_file(self, backup_path: Path):
        """用备份文件覆盖当前配置（内部用）"""
        if backup_path.exists():
            shutil.copy2(str(backup_path), str(self._config_path))
            self._cache = None

    def _cleanup_old_backups(self, max_backups: int = 30):
        """清理旧备份，只保留最近的 N 个"""
        backups = sorted(self._backup_dir.glob("config_*.yaml"), reverse=True)
        for old_backup in backups[max_backups:]:
            old_backup.unlink(missing_ok=True)
