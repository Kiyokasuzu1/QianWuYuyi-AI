"""
代理注册表

记录所有在线的本地代理，管理它们的状态和心跳。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timedelta


@dataclass
class AgentInfo:
    """代理信息"""
    agent_id: str
    user_id: str = ""
    connected_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_heartbeat: str = field(default_factory=lambda: datetime.now().isoformat())
    ip_address: str = ""
    hostname: str = ""
    os_name: str = ""
    status: str = "connecting"  # connecting | authenticated | disconnected

    def update_heartbeat(self):
        """更新心跳时间"""
        self.last_heartbeat = datetime.now().isoformat()

    def is_heartbeat_expired(self, timeout_seconds: int = 30) -> bool:
        """检查心跳是否超时"""
        try:
            last = datetime.fromisoformat(self.last_heartbeat)
            return datetime.now() - last > timedelta(seconds=timeout_seconds)
        except Exception:
            return True

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "user_id": self.user_id,
            "connected_at": self.connected_at,
            "last_heartbeat": self.last_heartbeat,
            "ip_address": self.ip_address,
            "hostname": self.hostname,
            "os_name": self.os_name,
            "status": self.status,
        }


class AgentRegistry:
    """代理注册表 —— 管理所有在线代理"""

    def __init__(self, heartbeat_timeout: int = 30):
        self._agents: Dict[str, AgentInfo] = {}
        self._user_to_agent: Dict[str, str] = {}  # user_id -> agent_id
        self.heartbeat_timeout = heartbeat_timeout

    def register(self, agent_id: str, user_id: str = "",
                 ip_address: str = "", hostname: str = "",
                 os_name: str = "") -> AgentInfo:
        """注册一个新代理"""
        info = AgentInfo(
            agent_id=agent_id,
            user_id=user_id,
            ip_address=ip_address,
            hostname=hostname,
            os_name=os_name,
            status="connecting",
        )
        self._agents[agent_id] = info
        if user_id:
            self._user_to_agent[user_id] = agent_id
        return info

    def authenticate(self, agent_id: str, user_id: str) -> bool:
        """标记代理为已认证"""
        if agent_id not in self._agents:
            return False
        info = self._agents[agent_id]
        info.status = "authenticated"
        info.user_id = user_id
        if user_id:
            self._user_to_agent[user_id] = agent_id
        return True

    def unregister(self, agent_id: str):
        """注销代理"""
        if agent_id in self._agents:
            info = self._agents[agent_id]
            info.status = "disconnected"
            if info.user_id and self._user_to_agent.get(info.user_id) == agent_id:
                del self._user_to_agent[info.user_id]
            del self._agents[agent_id]

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        """根据 agent_id 获取代理信息"""
        return self._agents.get(agent_id)

    def get_agent_by_user(self, user_id: str) -> Optional[AgentInfo]:
        """根据 user_id 获取代理信息"""
        agent_id = self._user_to_agent.get(user_id)
        if agent_id:
            return self._agents.get(agent_id)
        return None

    def update_heartbeat(self, agent_id: str) -> bool:
        """更新代理心跳"""
        info = self._agents.get(agent_id)
        if info:
            info.update_heartbeat()
            return True
        return False

    def check_expired(self) -> List[AgentInfo]:
        """检查并返回所有心跳超时的代理"""
        expired = []
        for agent_id, info in list(self._agents.items()):
            if info.is_heartbeat_expired(self.heartbeat_timeout):
                expired.append(info)
        return expired

    def cleanup_expired(self) -> List[AgentInfo]:
        """清理所有心跳超时的代理，返回被清理的列表"""
        expired = self.check_expired()
        for info in expired:
            self.unregister(info.agent_id)
        return expired

    def list_all(self) -> List[AgentInfo]:
        """列出所有代理"""
        return list(self._agents.values())

    def list_authenticated(self) -> List[AgentInfo]:
        """列出所有已认证的代理"""
        return [a for a in self._agents.values() if a.status == "authenticated"]

    def count(self) -> int:
        """返回代理总数"""
        return len(self._agents)

    def count_authenticated(self) -> int:
        """返回已认证代理数"""
        return len(self.list_authenticated())

    def is_online(self, user_id: str) -> bool:
        """检查指定用户的代理是否在线"""
        info = self.get_agent_by_user(user_id)
        if info and info.status == "authenticated":
            return not info.is_heartbeat_expired(self.heartbeat_timeout)
        return False


_global_registry = None


def get_agent_registry() -> AgentRegistry:
    """获取全局代理注册表实例"""
    global _global_registry
    if _global_registry is None:
        _global_registry = AgentRegistry()
    return _global_registry
