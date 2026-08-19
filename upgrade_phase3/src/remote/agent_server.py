"""
Agent Server —— 服务器端 WebSocket 服务

管理本地代理的连接、认证、心跳和指令下发。

设计原则：
- 默认关闭，通过 config.yaml 显式启用
- 与现有功能解耦，不影响 api_server.py 和 orchestrator.py
- token 认证，防止未授权连接
- 心跳超时检测，自动断开僵尸连接
"""

from __future__ import annotations
import asyncio
import json
import logging
import uuid
from typing import Dict, Optional, Any

from src.remote.protocol import (
    CommandType,
    ResponseType,
    AuthStatus,
    build_command,
    build_response,
    get_message_type,
    get_payload,
    get_request_id,
)
from src.remote.agent_registry import AgentRegistry, AgentInfo, get_agent_registry

logger = logging.getLogger(__name__)


class AgentServer:
    """
    羽依本地代理服务器

    负责：
    - 接收本地代理 WebSocket 连接
    - Token 认证
    - 心跳保活
    - 指令下发
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8765,
                 auth_token: str = "", heartbeat_timeout: int = 30,
                 registry: Optional[AgentRegistry] = None):
        self.host = host
        self.port = port
        self.auth_token = auth_token
        self.heartbeat_timeout = heartbeat_timeout

        self.registry = registry or get_agent_registry()
        self.registry.heartbeat_timeout = heartbeat_timeout

        # agent_id -> websocket 连接
        self._connections: Dict[str, object] = {}

        # websocket -> agent_id（反向映射）
        self._ws_to_agent: Dict[object, str] = {}

        # 请求-响应配对：request_id -> asyncio.Future
        self._pending_requests: Dict[str, asyncio.Future] = {}

        self._server = None
        self._heartbeat_task = None
        self._running = False

    async def start(self):
        """启动 WebSocket 服务"""
        if self._running:
            return

        try:
            import websockets
        except ImportError:
            logger.error("websockets 库未安装，请先 pip install websockets")
            return

        self._running = True
        logger.info(f"Agent Server 启动中，监听 {self.host}:{self.port}")

        self._server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
        )

        # 启动心跳检查任务
        self._heartbeat_task = asyncio.create_task(self._heartbeat_checker())

        logger.info(f"Agent Server 已启动，端口 {self.port}")

    async def stop(self):
        """停止 WebSocket 服务"""
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # 关闭所有连接
        for ws in list(self._ws_to_agent.keys()):
            try:
                await ws.close()
            except Exception:
                pass

        self._connections.clear()
        self._ws_to_agent.clear()
        logger.info("Agent Server 已停止")

    async def _handle_connection(self, websocket):
        """处理新的 WebSocket 连接"""
        agent_id = None
        try:
            remote_addr = websocket.remote_address
            ip = remote_addr[0] if remote_addr else ""
            logger.info(f"新连接来自 {ip}")

            # Step 1: 等待认证
            auth_result = await self._handle_auth(websocket, ip)
            if not auth_result:
                logger.warning(f"来自 {ip} 的连接认证失败")
                await websocket.close(code=1008, reason="auth failed")
                return

            agent_id, user_id, hostname, os_name = auth_result

            # Step 2: 注册代理
            self.registry.register(
                agent_id=agent_id,
                user_id=user_id,
                ip_address=ip,
                hostname=hostname,
                os_name=os_name,
            )
            self.registry.authenticate(agent_id, user_id)

            self._connections[agent_id] = websocket
            self._ws_to_agent[websocket] = agent_id

            logger.info(f"代理 {agent_id}（用户 {user_id}）已连接")

            # 发送欢迎消息
            await self._send_to_agent(
                agent_id,
                build_command(CommandType.PING, {"msg": "welcome"})
            )

            # Step 3: 消息循环
            async for message in websocket:
                await self._handle_message(agent_id, message)

        except Exception as e:
            logger.error(f"连接处理异常: {e}")
        finally:
            # 清理连接
            if agent_id:
                self._cleanup_connection(agent_id, websocket)
            elif websocket in self._ws_to_agent:
                agent_id = self._ws_to_agent[websocket]
                self._cleanup_connection(agent_id, websocket)

    async def _handle_auth(self, websocket, ip: str) -> Optional[tuple]:
        """
        处理认证流程
        返回 (agent_id, user_id, hostname, os_name) 或 None
        """
        try:
            # 等待客户端发送认证请求
            message = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            data = json.loads(message)

            msg_type = get_message_type(data)
            if msg_type != ResponseType.AUTH_RESPONSE:
                logger.warning(f"期望认证消息，收到 {msg_type}")
                return None

            payload = get_payload(data)
            agent_id = payload.get("agent_id", "")
            user_id = payload.get("user_id", "")
            token = payload.get("token", "")
            hostname = payload.get("hostname", "")
            os_name = payload.get("os", "")

            # 验证 token
            if self.auth_token and token != self.auth_token:
                logger.warning(f"代理 {agent_id} token 不匹配")
                await websocket.send(json.dumps(build_response(
                    ResponseType.ERROR,
                    request_id=get_request_id(data),
                    success=False,
                    error="invalid token",
                )))
                return None

            if not agent_id:
                logger.warning("agent_id 不能为空")
                return None

            return (agent_id, user_id, hostname, os_name)

        except asyncio.TimeoutError:
            logger.warning(f"来自 {ip} 的连接认证超时")
            return None
        except json.JSONDecodeError:
            logger.warning(f"来自 {ip} 的认证消息格式错误")
            return None
        except Exception as e:
            logger.error(f"认证处理异常: {e}")
            return None

    async def _handle_message(self, agent_id: str, message: str):
        """处理来自代理的消息"""
        try:
            data = json.loads(message)
            msg_type = get_message_type(data)
            payload = get_payload(data)

            if msg_type == ResponseType.HEARTBEAT_ACK:
                # 心跳响应
                self.registry.update_heartbeat(agent_id)

            elif msg_type == ResponseType.PONG:
                # PING 响应，也算心跳
                self.registry.update_heartbeat(agent_id)

            elif msg_type == ResponseType.AGENT_STATUS:
                # 代理状态上报
                self.registry.update_heartbeat(agent_id)
                info = self.registry.get_agent(agent_id)
                if info:
                    info.hostname = payload.get("hostname", info.hostname)
                    info.os_name = payload.get("os", info.os_name)

            elif msg_type == ResponseType.SCREEN_DATA:
                # 截图数据 —— 唤醒等待的请求
                self.registry.update_heartbeat(agent_id)
                self._resolve_pending(data)

            elif msg_type == ResponseType.ACTION_RESULT:
                # 动作执行结果 —— 唤醒等待的请求
                self.registry.update_heartbeat(agent_id)
                self._resolve_pending(data)

            elif msg_type == ResponseType.ERROR:
                # 错误信息 —— 也唤醒等待的请求（让调用方知道出错了）
                error = payload.get("error", data.get("error", "unknown"))
                logger.warning(f"代理 {agent_id} 报错: {error}")
                self.registry.update_heartbeat(agent_id)
                self._resolve_pending(data)

            else:
                logger.debug(f"收到未知消息类型: {msg_type}")
                self.registry.update_heartbeat(agent_id)

        except json.JSONDecodeError:
            logger.warning(f"代理 {agent_id} 发送了无效 JSON")
        except Exception as e:
            logger.error(f"处理代理 {agent_id} 消息异常: {e}")

    async def _heartbeat_checker(self):
        """后台任务：定期检查心跳超时的代理"""
        while self._running:
            try:
                await asyncio.sleep(5)  # 每 5 秒检查一次

                expired = self.registry.cleanup_expired()
                for info in expired:
                    logger.warning(f"代理 {info.agent_id} 心跳超时，断开连接")
                    ws = self._connections.get(info.agent_id)
                    if ws:
                        try:
                            await ws.close(code=1001, reason="heartbeat timeout")
                        except Exception:
                            pass
                        self._cleanup_connection(info.agent_id, ws)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳检查异常: {e}")

    def _cleanup_connection(self, agent_id: str, websocket):
        """清理连接资源"""
        if agent_id in self._connections:
            del self._connections[agent_id]
        if websocket in self._ws_to_agent:
            del self._ws_to_agent[websocket]

        # 取消该代理所有未完成的请求
        cancelled = []
        for req_id, future in list(self._pending_requests.items()):
            if not future.done():
                future.set_result({"success": False, "error": "agent disconnected",
                                   "payload": {}, "request_id": req_id})
                cancelled.append(req_id)
        for req_id in cancelled:
            del self._pending_requests[req_id]

        self.registry.unregister(agent_id)
        logger.info(f"代理 {agent_id} 已断开")

    async def _send_to_agent(self, agent_id: str, message: dict) -> bool:
        """向指定代理发送消息"""
        ws = self._connections.get(agent_id)
        if not ws:
            logger.warning(f"代理 {agent_id} 不在线")
            return False
        try:
            await ws.send(json.dumps(message, ensure_ascii=False))
            return True
        except Exception as e:
            logger.error(f"向代理 {agent_id} 发送消息失败: {e}")
            return False

    def _resolve_pending(self, data: dict):
        """收到代理响应后，唤醒对应的等待 Future"""
        request_id = data.get("request_id", "")
        if not request_id:
            return

        future = self._pending_requests.get(request_id)
        if future and not future.done():
            future.set_result(data)
            del self._pending_requests[request_id]

    async def send_command(self, agent_id: str, cmd_type: str,
                           payload: Optional[dict] = None) -> bool:
        """向代理发送指令（外部调用接口，不等待响应）"""
        if not self._running:
            logger.warning("Agent Server 未运行")
            return False

        info = self.registry.get_agent(agent_id)
        if not info or info.status != "authenticated":
            logger.warning(f"代理 {agent_id} 未认证或不存在")
            return False

        cmd = build_command(cmd_type, payload)
        return await self._send_to_agent(agent_id, cmd)

    async def send_command_and_wait(self, agent_id: str, cmd_type: str,
                                    payload: Optional[dict] = None,
                                    timeout: int = 10) -> Optional[dict]:
        """
        发送指令并等待响应

        用于截图、控制等需要返回结果的操作。
        内部用 asyncio.Future 实现请求-响应配对。
        超时或代理断开时返回 None 或带 error 的 dict。
        """
        if not self._running:
            return {"success": False, "error": "server not running", "payload": {}}

        info = self.registry.get_agent(agent_id)
        if not info or info.status != "authenticated":
            return {"success": False, "error": "agent not online", "payload": {}}

        # 生成 request_id 并创建 Future
        request_id = f"req_{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_requests[request_id] = future

        # 构建指令（带指定 request_id）
        cmd = build_command(cmd_type, payload, request_id=request_id)

        # 发送
        sent = await self._send_to_agent(agent_id, cmd)
        if not sent:
            del self._pending_requests[request_id]
            return {"success": False, "error": "send failed", "payload": {}}

        # 等待响应
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            del self._pending_requests[request_id]
            logger.warning(f"代理 {agent_id} 响应超时 ({timeout}s)")
            return {"success": False, "error": "timeout", "payload": {}}
        except Exception as e:
            if request_id in self._pending_requests:
                del self._pending_requests[request_id]
            logger.error(f"等待代理 {agent_id} 响应异常: {e}")
            return {"success": False, "error": str(e), "payload": {}}

    async def send_command_to_user(self, user_id: str, cmd_type: str,
                                   payload: Optional[dict] = None) -> bool:
        """向指定用户的代理发送指令"""
        info = self.registry.get_agent_by_user(user_id)
        if not info:
            return False
        return await self.send_command(info.agent_id, cmd_type, payload)

    def get_online_agents(self) -> list:
        """获取所有在线代理列表"""
        return [a.to_dict() for a in self.registry.list_authenticated()]

    def is_agent_online(self, agent_id: str) -> bool:
        """检查代理是否在线"""
        info = self.registry.get_agent(agent_id)
        if not info:
            return False
        return info.status == "authenticated" and agent_id in self._connections

    def is_user_online(self, user_id: str) -> bool:
        """检查指定用户的代理是否在线"""
        info = self.registry.get_agent_by_user(user_id)
        if not info:
            return False
        return self.is_agent_online(info.agent_id)


_global_server = None


def get_agent_server() -> Optional[AgentServer]:
    """获取全局 Agent Server 实例（可能为 None，如果未启用）"""
    return _global_server


async def start_agent_server(host: str = "0.0.0.0", port: int = 8765,
                             auth_token: str = "",
                             heartbeat_timeout: int = 30) -> AgentServer:
    """启动全局 Agent Server"""
    global _global_server
    if _global_server is None:
        _global_server = AgentServer(
            host=host,
            port=port,
            auth_token=auth_token,
            heartbeat_timeout=heartbeat_timeout,
        )
    if not _global_server._running:
        await _global_server.start()
    return _global_server
