import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

_config = None


def load_config():
    global _config
    if _config is None:
        config_path = Path(__file__).parent.parent / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f)
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
    """优先从环境变量获取，其次从 config.yaml"""
    env_key = os.getenv("DEEPSEEK_API_KEY")
    if env_key:
        return env_key
    return get("llm.api_key", "")


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