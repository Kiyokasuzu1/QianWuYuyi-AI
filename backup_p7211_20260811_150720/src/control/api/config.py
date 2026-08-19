# -*- coding: utf-8 -*-
"""
src/control/api/config.py

Phase C.10.3 — Yuyi Server API Gateway 配置加载

仅从 config.yaml 读取与 Gateway 相关的字段:
  control_api:
    enabled: bool
    host: str
    port: int
    auth:
      mode: "development" | "production" | "disabled"
      token: str       # 仅 development 模式使用
      required: bool   # production 模式是否强制 token

默认行为:
  enabled = True
  host = "0.0.0.0"
  port = 0           # 0 表示不绑定,需要显式启动
  auth.mode = "development"
  auth.required = True
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 仓库根路径与 config.yaml 定位
# ============================================================
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent.parent.parent


def find_repo_root() -> Path:
    """定位仓库根目录。"""
    cur = _THIS_FILE
    for _ in range(cur.parts.__len__()):
        if (cur / "config.yaml").exists():
            return cur
        if cur == cur.parent:
            break
        cur = cur.parent
    return _REPO_ROOT


def find_config_path() -> Path:
    """定位 config.yaml 路径。"""
    candidates = [
        find_repo_root() / "config.yaml",
        Path.cwd() / "config.yaml",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


# ============================================================
# 配置模型
# ============================================================
class GatewayAuthConfig:
    """认证配置。"""

    __slots__ = ("mode", "token", "required")

    def __init__(
        self,
        mode: str = "development",
        token: str = "",
        required: bool = True,
    ) -> None:
        self.mode = str(mode or "development")
        self.token = str(token or "")
        self.required = bool(required)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            # 不打印真实 token
            "has_token": bool(self.token),
            "required": self.required,
        }


class GatewayConfig:
    """Gateway 总配置。"""

    __slots__ = ("enabled", "host", "port", "auth", "schema_version")

    def __init__(
        self,
        enabled: bool = True,
        host: str = "0.0.0.0",
        port: int = 0,
        auth: Optional[GatewayAuthConfig] = None,
        schema_version: str = "1.0",
    ) -> None:
        self.enabled = bool(enabled)
        self.host = str(host or "0.0.0.0")
        self.port = int(port or 0)
        self.auth = auth if auth is not None else GatewayAuthConfig()
        self.schema_version = str(schema_version or "1.0")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "host": self.host,
            "port": self.port,
            "auth": self.auth.to_dict(),
            "schema_version": self.schema_version,
        }


# ============================================================
# 加载器
# ============================================================
def _load_yaml_safely(path: Path) -> Dict[str, Any]:
    """安全加载 YAML;失败返回空 dict。"""
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("GatewayConfig: 加载 %s 失败: %s", path, exc)
        return {}


def _extract_auth_section(config: Dict[str, Any]) -> GatewayAuthConfig:
    """从 config 字典中抽取 auth 段。"""
    # 优先级 1:control_api.auth.*
    ca = config.get("control_api") or {}
    if isinstance(ca, dict):
        auth_raw = ca.get("auth") or {}
    else:
        auth_raw = {}
    # 优先级 2:remote.auth_token(向后兼容)
    if not auth_raw:
        remote = config.get("remote") or {}
        if isinstance(remote, dict) and remote.get("auth_token"):
            auth_raw = {
                "mode": "development",
                "token": remote.get("auth_token", ""),
                "required": True,
            }
    # 优先级 3:环境变量
    if not auth_raw.get("token") if isinstance(auth_raw, dict) else True:
        env_token = os.environ.get("YUYI_GATEWAY_TOKEN", "").strip()
        if env_token:
            auth_raw = dict(auth_raw or {})
            auth_raw["token"] = env_token
    if not isinstance(auth_raw, dict):
        auth_raw = {}
    return GatewayAuthConfig(
        mode=str(auth_raw.get("mode", "development") or "development"),
        token=str(auth_raw.get("token", "") or ""),
        required=bool(auth_raw.get("required", True)),
    )


def _extract_gateway(config: Dict[str, Any]) -> GatewayConfig:
    """从 config 字典中抽取 gateway 段。"""
    ca = config.get("control_api") or {}
    if not isinstance(ca, dict):
        ca = {}
    return GatewayConfig(
        enabled=bool(ca.get("enabled", True)),
        host=str(ca.get("host", "0.0.0.0") or "0.0.0.0"),
        port=int(ca.get("port", 0) or 0),
        auth=_extract_auth_section(config),
        schema_version=str(ca.get("schema_version", "1.0") or "1.0"),
    )


def load_gateway_config(config_path: Optional[Path] = None) -> GatewayConfig:
    """
    加载 Gateway 配置。

    Args:
        config_path: 自定义 config.yaml 路径;None 时自动定位。

    Returns:
        GatewayConfig 实例。
    """
    path = config_path or find_config_path()
    config = _load_yaml_safely(path)
    return _extract_gateway(config)


# ============================================================
# 模块级单例(线程安全懒加载)
# ============================================================
_gateway_config: Optional[GatewayConfig] = None
_gateway_lock = threading.Lock()


def get_gateway_config() -> GatewayConfig:
    """获取 GatewayConfig 单例(懒加载)。"""
    global _gateway_config
    if _gateway_config is None:
        with _gateway_lock:
            if _gateway_config is None:
                _gateway_config = load_gateway_config()
    return _gateway_config


def reset_gateway_config_for_testing(
    cfg: Optional[GatewayConfig] = None,
) -> GatewayConfig:
    """测试用:重置/注入单例。"""
    global _gateway_config
    with _gateway_lock:
        _gateway_config = cfg if cfg is not None else load_gateway_config()
    return _gateway_config


__all__ = [
    "GatewayAuthConfig",
    "GatewayConfig",
    "find_config_path",
    "find_repo_root",
    "get_gateway_config",
    "load_gateway_config",
    "reset_gateway_config_for_testing",
]
