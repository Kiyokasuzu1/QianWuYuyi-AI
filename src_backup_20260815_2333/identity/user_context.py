"""
用户上下文 (UserContext)
统一传递用户身份信息，所有模块通过此对象获取用户标识和路径。
"""
import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class UserContext:
    user_id: str
    platform: str = "qq"
    metadata: Dict = field(default_factory=dict)

    @property
    def user_key(self) -> str:
        """全局唯一用户标识：平台:用户ID"""
        return f"{self.platform}:{self.user_id}"

    @property
    def safe_user_id(self) -> str:
        """
        安全的用户标识片段，用于文件路径。
        如果原始 user_id 只包含安全字符，直接使用；
        否则使用 SHA256 哈希的前 16 位，防止碰撞。
        """
        if re.match(r"^[a-zA-Z0-9_-]+$", self.user_id):
            return self.user_id
        return hashlib.sha256(self.user_id.encode()).hexdigest()[:16]

    @property
    def user_dir(self) -> str:
        """安全的用户目录路径，包含平台前缀"""
        safe_platform = re.sub(r"[^a-zA-Z0-9_-]", "", self.platform)
        return f"data/users/{safe_platform}_{self.safe_user_id}"

    @property
    def memory_path(self) -> str:
        return f"{self.user_dir}/memory.json"

    @property
    def vector_index_dir(self) -> str:
        return f"{self.user_dir}/vector_index"

    @property
    def relationship_dir(self) -> str:
        return self.user_dir

    @property
    def origin_identity_path(self) -> str:
        return f"{self.user_dir}/origin_identity.json"

    @property
    def agreements_dir(self) -> str:
        return f"{self.user_dir}/agreements"