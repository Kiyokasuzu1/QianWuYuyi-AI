"""
模块自动发现与注册机制

启动时扫描 src/ 下所有子目录，查找 MODULE_INFO 声明，
将模块信息注册到管理中心，供管理面板使用。

新增模块只需在目录下创建 module.py 并声明 MODULE_INFO，
无需手动修改注册表。
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ReloadMode(Enum):
    """模块重载模式"""
    HOT = "hot"                 # 立即生效，无需重启
    SOFT_RESTART = "soft_restart"  # 需要软重启（重新初始化模块实例）
    RESTART_REQUIRED = "restart_required"  # 需要重启整个服务


@dataclass
class ModuleInfo:
    """模块元信息"""
    name: str                           # 模块标识（如 "screen"）
    display: str                        # 显示名称（如 "屏幕感知"）
    version: str = "0.1.0"             # 模块版本
    description: str = ""              # 模块描述
    reload_mode: ReloadMode = ReloadMode.RESTART_REQUIRED
    dependencies: List[str] = field(default_factory=list)
    config_key: str = ""               # 对应 config.yaml 中的键名（如 "screen"）
    enabled: bool = False              # 当前是否启用
    loaded: bool = False               # 模块代码是否已加载
    error: str = ""                    # 加载错误信息

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display": self.display,
            "version": self.version,
            "description": self.description,
            "reload_mode": self.reload_mode.value,
            "dependencies": self.dependencies,
            "config_key": self.config_key,
            "enabled": self.enabled,
            "loaded": self.loaded,
            "error": self.error,
        }


class ModuleLoader:
    """
    模块自动发现与注册中心

    启动时扫描 src/ 子目录，发现所有声明了 MODULE_INFO 的模块，
    并结合 config.yaml 判断各模块是否启用。
    """

    # 已知的 config.yaml 模块键名映射（兜底：模块目录名 → config 键名）
    _CONFIG_KEY_ALIASES = {
        "token_opt": "token_opt",
        "initiative": "initiative",
    }

    def __init__(self, src_root: Optional[str] = None, config: Optional[Dict] = None):
        """
        Args:
            src_root: src/ 目录的绝对路径，默认为本文件所在目录
            config: config.yaml 解析后的字典
        """
        self._src_root = Path(src_root) if src_root else Path(__file__).parent.parent
        self._config = config or {}
        self._modules: Dict[str, ModuleInfo] = {}
        self._discovered = False

    def discover(self) -> Dict[str, ModuleInfo]:
        """
        扫描 src/ 下所有子目录，查找 MODULE_INFO 声明

        每个 src/<module_name>/module.py 文件可以声明 MODULE_INFO 字典，
        loader 会自动发现并注册。

        Returns:
            模块名 → ModuleInfo 的字典
        """
        if self._discovered:
            return self._modules

        for item in sorted(self._src_root.iterdir()):
            if not item.is_dir():
                continue
            if item.name.startswith("_") or item.name == "core":
                continue

            module_file = item / "module.py"
            if module_file.exists():
                self._load_module_info(item.name, module_file)

        self._discovered = True
        logger.info(f"模块发现完成，共注册 {len(self._modules)} 个模块")
        return self._modules

    def _load_module_info(self, module_name: str, module_file: Path):
        """从 module.py 中加载 MODULE_INFO"""
        try:
            # 用 importlib 动态导入
            spec = importlib.util.spec_from_file_location(
                f"src.{module_name}.module",
                str(module_file),
            )
            if spec is None or spec.loader is None:
                logger.warning(f"无法加载模块定义: {module_file}")
                return

            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            raw_info = getattr(mod, "MODULE_INFO", None)
            if raw_info is None:
                logger.debug(f"{module_name}/module.py 没有 MODULE_INFO，跳过")
                return

            info = self._parse_module_info(module_name, raw_info)
            self._modules[module_name] = info
            logger.info(f"注册模块: {info.display} ({info.name}) v{info.version}")

        except Exception as e:
            logger.warning(f"加载模块 {module_name} 失败: {e}")
            # 即使加载失败也注册，标记错误
            self._modules[module_name] = ModuleInfo(
                name=module_name,
                display=module_name,
                loaded=False,
                error=str(e),
            )

    def _parse_module_info(self, module_name: str, raw: Dict) -> ModuleInfo:
        """将原始 MODULE_INFO 字典解析为 ModuleInfo"""
        reload_raw = raw.get("reload_mode", "restart_required")
        try:
            reload_mode = ReloadMode(reload_raw)
        except ValueError:
            reload_mode = ReloadMode.RESTART_REQUIRED

        config_key = raw.get("config_key", self._CONFIG_KEY_ALIASES.get(module_name, module_name))

        # 从 config 中读取 enabled 状态
        enabled = False
        if config_key and self._config:
            section = self._config.get(config_key, {})
            if isinstance(section, dict):
                enabled = section.get("enabled", False)

        return ModuleInfo(
            name=raw.get("name", module_name),
            display=raw.get("display", module_name),
            version=raw.get("version", "0.1.0"),
            description=raw.get("description", ""),
            reload_mode=reload_mode,
            dependencies=raw.get("dependencies", []),
            config_key=config_key,
            enabled=enabled,
            loaded=True,
            error="",
        )

    def get_module(self, name: str) -> Optional[ModuleInfo]:
        """获取指定模块信息"""
        return self._modules.get(name)

    def get_all_modules(self) -> Dict[str, ModuleInfo]:
        """获取所有已注册模块"""
        return dict(self._modules)

    def update_enabled_state(self, name: str, enabled: bool):
        """更新模块启用状态（仅内存，不写配置）"""
        info = self._modules.get(name)
        if info:
            info.enabled = enabled

    def sync_config(self, config: Dict):
        """
        同步配置状态

        当 config.yaml 被外部修改后，调用此方法刷新所有模块的 enabled 状态。
        """
        self._config = config
        for name, info in self._modules.items():
            if info.config_key and config:
                section = config.get(info.config_key, {})
                if isinstance(section, dict):
                    info.enabled = section.get("enabled", False)

    def get_dependencies(self, name: str) -> List[str]:
        """获取模块的依赖列表"""
        info = self._modules.get(name)
        return info.dependencies if info else []
