# -*- coding: utf-8 -*-
"""
src/runtime/observer/config.py

Phase 7.1 —— ObservationConfig 观察层配置。

设计要点:
- 持久化(JSONL)默认关闭,避免长期运行磁盘爆炸
- 所有字段有合理默认值,允许无 config.yaml 直接运行
- 支持从 load_config() 返回的 dict 懒加载(runtime_observation 段)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


# ============================================================
# 默认值常量
# ============================================================
DEFAULT_OBSERVATION_ENABLED = True
DEFAULT_PERSISTENCE_ENABLED = False   # 默认关闭 JSONL 永久保存
DEFAULT_MAX_HISTORY_QUEUE_LEN = 500   # 内存历史队列长度
DEFAULT_SUBSCRIBER_QUEUE_LEN = 100    # 单个订阅者队列长度
DEFAULT_REPLAY_HISTORY_LIMIT = 20     # SSE 首次连接回放历史条数
DEFAULT_POLLING_LIMIT_CAP = 200       # recent 轮询接口 limit 上限

DEFAULT_PERSISTENCE_DIR = "data/runtime_events"
DEFAULT_PERSISTENCE_MAX_DAYS = 7      # 持久化开启时,保留最近 N 天
DEFAULT_PERSISTENCE_MAX_FILE_BYTES = 50 * 1024 * 1024  # 单文件 50MB


# ============================================================
# 配置数据类
# ============================================================
@dataclass
class ObservationConfig:
    """
    观察层配置(可序列化,无行为)。

    Attributes:
        enabled:           观察层总开关,False 时 Sink 为 No-Op
        persistence:       JSONL 持久化开关,默认 False
        persistence_dir:   持久化目录(相对项目根)
        persistence_max_days:       保留天数(开启时)
        persistence_max_file_bytes: 单文件最大字节数(开启时)
        max_history_len:   内存历史队列最大长度
        subscriber_queue_len:       单个订阅者队列容量(满了丢事件,不阻塞)
        replay_history_limit:       SSE 首次连接回放条数
        polling_limit_cap:          recent 轮询接口 limit 上限
    """

    enabled: bool = DEFAULT_OBSERVATION_ENABLED
    persistence: bool = DEFAULT_PERSISTENCE_ENABLED
    persistence_dir: str = DEFAULT_PERSISTENCE_DIR
    persistence_max_days: int = DEFAULT_PERSISTENCE_MAX_DAYS
    persistence_max_file_bytes: int = DEFAULT_PERSISTENCE_MAX_FILE_BYTES
    max_history_len: int = DEFAULT_MAX_HISTORY_QUEUE_LEN
    subscriber_queue_len: int = DEFAULT_SUBSCRIBER_QUEUE_LEN
    replay_history_limit: int = DEFAULT_REPLAY_HISTORY_LIMIT
    polling_limit_cap: int = DEFAULT_POLLING_LIMIT_CAP

    # --------------------------------------------------------
    # 从 config.yaml 段加载
    # --------------------------------------------------------
    @classmethod
    def from_dict(cls, cfg: Optional[Dict[str, Any]]) -> "ObservationConfig":
        """
        从 load_config()['runtime_observation'] 段加载,未配置部分使用默认值。

        cfg 为 None 或空字典时,返回全默认配置。
        """
        if not isinstance(cfg, dict):
            return cls()
        result = cls()
        # 按字段读取,失败静默回退默认值
        # 注意: 只接受真 bool(True/False);避免 bool("x") / bool(1) 误判
        if "enabled" in cfg and type(cfg["enabled"]) is bool:
            result.enabled = cfg["enabled"]
        if "persistence" in cfg and type(cfg["persistence"]) is bool:
            result.persistence = cfg["persistence"]
        if isinstance(cfg.get("persistence_dir"), str) and cfg["persistence_dir"]:
            result.persistence_dir = str(cfg["persistence_dir"])
        if "persistence_max_days" in cfg:
            try:
                v = int(cfg["persistence_max_days"])
                if v >= 1:
                    result.persistence_max_days = v
            except (TypeError, ValueError):
                pass
        if "persistence_max_file_bytes" in cfg:
            try:
                v = int(cfg["persistence_max_file_bytes"])
                if v >= 1024:
                    result.persistence_max_file_bytes = v
            except (TypeError, ValueError):
                pass
        if "max_history_len" in cfg:
            try:
                v = int(cfg["max_history_len"])
                if v >= 10:
                    result.max_history_len = v
            except (TypeError, ValueError):
                pass
        if "subscriber_queue_len" in cfg:
            try:
                v = int(cfg["subscriber_queue_len"])
                if v >= 10:
                    result.subscriber_queue_len = v
            except (TypeError, ValueError):
                pass
        if "replay_history_limit" in cfg:
            try:
                v = int(cfg["replay_history_limit"])
                if v >= 1:
                    result.replay_history_limit = v
            except (TypeError, ValueError):
                pass
        if "polling_limit_cap" in cfg:
            try:
                v = int(cfg["polling_limit_cap"])
                if v >= 10:
                    result.polling_limit_cap = v
            except (TypeError, ValueError):
                pass
        return result

    # --------------------------------------------------------
    # 序列化(用于调试)
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


__all__ = [
    "DEFAULT_OBSERVATION_ENABLED",
    "DEFAULT_PERSISTENCE_ENABLED",
    "DEFAULT_PERSISTENCE_DIR",
    "DEFAULT_PERSISTENCE_MAX_DAYS",
    "DEFAULT_PERSISTENCE_MAX_FILE_BYTES",
    "DEFAULT_MAX_HISTORY_QUEUE_LEN",
    "DEFAULT_SUBSCRIBER_QUEUE_LEN",
    "DEFAULT_REPLAY_HISTORY_LIMIT",
    "DEFAULT_POLLING_LIMIT_CAP",
    "ObservationConfig",
]
