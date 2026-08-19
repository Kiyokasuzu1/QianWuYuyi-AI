"""
用户解析器 (UserResolver)
从消息来源识别用户，返回 UserContext。
"""
from src.identity.user_context import UserContext
from src.config import get_memory_config


class UserResolver:
    def resolve(self, message=None) -> UserContext:
        """
        从消息来源识别用户。
        
        Args:
            message: 可选的消息对象（未来可包含 user_id、platform 等字段）
                     当前阶段从配置读取默认用户。
        
        Returns:
            UserContext: 用户上下文对象
        """
        # 未来：从 message 对象中提取真实用户信息
        # if message is not None:
        #     return UserContext(
        #         user_id=message.user_id,
        #         platform=message.platform
        #     )
        
        user_id = get_memory_config().get("target_user_id", "default_user")
        return UserContext(user_id=user_id, platform="qq")