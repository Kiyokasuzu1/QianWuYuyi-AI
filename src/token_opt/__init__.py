"""
Token 优化模块。

提供历史消息压缩和记忆摘要功能，在不影响核心能力的前提下节省 token。
默认关闭，通过配置启用。
"""

from src.token_opt.history_compressor import HistoryCompressor
from src.token_opt.memory_summarizer import MemorySummarizer


def is_token_opt_enabled() -> bool:
    """
    检查是否启用了 token 优化。

    读取 config.yaml 中的 token_opt.enabled 配置，
    默认关闭（保持向后兼容）。

    优先级：环境变量 > config.yaml > 默认 False
    环境变量 YUYI_TOKEN_OPT=true 时强制启用，覆盖配置文件。
    """
    # 1. 环境变量优先（允许运维强制启用/禁用）
    import os
    env_val = os.getenv("YUYI_TOKEN_OPT", "").lower()
    if env_val == "true":
        return True
    if env_val == "false":
        return False

    # 2. 读取 config.yaml（复用 src.config.load_config，带缓存）
    try:
        from src.config import load_config
        config = load_config()
        if config and isinstance(config, dict):
            token_opt = config.get("token_opt", {})
            return token_opt.get("enabled", False)
    except Exception:
        pass

    # 3. 兜底：默认关闭
    return False


__all__ = [
    "HistoryCompressor",
    "MemorySummarizer",
    "is_token_opt_enabled",
]
