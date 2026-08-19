import os
import logging
import yaml
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

_logger = logging.getLogger(__name__)
_config = None


def load_config():
    """加载 config.yaml。R2.7.6-AUDIT: 缺失/损坏时返回 {} 而非抛异常。"""
    global _config
    if _config is None:
        config_path = Path(__file__).parent.parent / "config.yaml"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                _config = yaml.safe_load(f) or {}
        except FileNotFoundError:
            _logger.warning("config.yaml 不存在 (%s)，使用空配置降级", config_path)
            _config = {}
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
            _logger.error("config.yaml 解析失败: %s，使用空配置降级", e)
            _config = {}
    return _config


def get(path: str, default=None):
    """获取配置值，支持点号分隔，如 'llm.model'"""
    config = load_config()
    keys = path.split(".")
    value = config
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return default
    return value if value is not None else default


def get_api_key() -> str:
    """优先从环境变量获取，其次从 config.yaml。

    R2.7.6-AUDIT: 检测 ${...} 字面占位符。yaml.safe_load 不会展开 ${VAR}，
    如果环境变量未设置，config.yaml 中的 "${DEEPSEEK_API_KEY}" 会被当作
    真实 key 发送 → 401 静默失败。此处检测并视为空。
    """
    env_key = os.getenv("DEEPSEEK_API_KEY")
    if env_key and not env_key.startswith("${"):
        return env_key
    cfg_key = get("llm.api_key", "")
    if cfg_key and not str(cfg_key).startswith("${"):
        return str(cfg_key)
    return ""


def get_memory_config() -> dict:
    """获取记忆系统配置"""
    return {
        "json_path": get("memory.json_path", "data/memories.json"),
        "chroma_path": get("memory.chroma_path", "data/chroma_db"),
        "embedding_model": get("memory.embedding_model", "all-MiniLM-L6-v2"),
        "search_top_k": get("memory.search_top_k", 8),
        "min_relevance": get("memory.min_relevance", 0.3),
        "target_user_id": get("memory.target_user_id", "366648462")
    }