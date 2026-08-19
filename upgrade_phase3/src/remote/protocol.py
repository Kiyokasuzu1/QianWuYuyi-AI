"""
通信协议定义

服务器（羽依） ←→ 本地代理 之间的消息格式。

所有消息都是 JSON 格式，结构统一为：
{
    "type": "...",           # 消息类型
    "request_id": "...",     # 请求 ID（用于配对请求-响应）
    "payload": {...}         # 消息内容
}
"""

from __future__ import annotations
from typing import Dict, Any, Optional
import uuid


class CommandType:
    """服务器 → 代理 的指令类型"""

    AUTH_CHALLENGE = "auth.challenge"       # 认证挑战
    HEARTBEAT = "system.heartbeat"          # 心跳
    PING = "system.ping"                    # 连通性测试

    SCREEN_CAPTURE = "screen.capture"       # 请求截图
    SCREEN_REGION = "screen.region"         # 请求指定区域截图

    KEY_PRESS = "input.key_press"           # 按键
    KEY_TYPE = "input.key_type"             # 输入文本
    MOUSE_CLICK = "input.mouse_click"       # 鼠标点击
    MOUSE_MOVE = "input.mouse_move"         # 鼠标移动
    MOUSE_SCROLL = "input.mouse_scroll"     # 滚轮


class ResponseType:
    """代理 → 服务器 的响应类型"""

    AUTH_RESPONSE = "auth.response"         # 认证响应
    HEARTBEAT_ACK = "system.heartbeat_ack"  # 心跳确认
    PONG = "system.pong"                    # PING 响应

    AGENT_STATUS = "agent.status"           # 代理状态上报

    SCREEN_DATA = "screen.data"             # 截图数据
    ACTION_RESULT = "action.result"         # 动作执行结果

    ERROR = "error"                         # 错误信息


class AuthStatus:
    PENDING = "pending"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"


def build_command(cmd_type: str, payload: Optional[Dict[str, Any]] = None,
                  request_id: Optional[str] = None) -> Dict[str, Any]:
    """构建服务器下发的指令消息"""
    return {
        "type": cmd_type,
        "request_id": request_id or f"req_{uuid.uuid4().hex[:8]}",
        "payload": payload or {},
    }


def build_response(resp_type: str, request_id: str = "",
                   payload: Optional[Dict[str, Any]] = None,
                   success: bool = True,
                   error: str = "") -> Dict[str, Any]:
    """构建代理回复的响应消息"""
    return {
        "type": resp_type,
        "request_id": request_id,
        "success": success,
        "error": error,
        "payload": payload or {},
    }


def get_request_id(message: Dict[str, Any]) -> str:
    """从消息中提取 request_id"""
    return message.get("request_id", "")


def get_message_type(message: Dict[str, Any]) -> str:
    """从消息中提取类型"""
    return message.get("type", "")


def get_payload(message: Dict[str, Any]) -> Dict[str, Any]:
    """从消息中提取 payload"""
    return message.get("payload", {})
