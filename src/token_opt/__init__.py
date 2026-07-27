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
    """
    try:
        from src.utils.config import get_config
        config = get_config()
        if config and hasattr(config, "get"):
            token_opt = config.get("token_opt", {})
            return token_opt.get("enabled", False)
    except Exception:
        pass

    # 兜底：检查环境变量
    import os
    return os.getenv("YUYI_TOKEN_OPT", "false").lower() == "true"


__all__ = [
    "HistoryCompressor",
    "MemorySummarizer",
    "is_token_opt_enabled",
]
