"""
本地代理 (Local Agent)

运行在用户电脑上的小工具，负责：
- 连接到羽依服务器
- 发送认证信息
- 定时心跳保活
- 接收服务器下发的指令（截图已实现，控制功能待实现）

启动方式：
    python local_agent/agent.py
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import platform
import sys
import uuid
from pathlib import Path
from typing import Optional

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("local_agent")


# ============================================================
# 协议常量（与服务器 src/remote/protocol.py 保持一致）
# ============================================================

class CommandType:
    AUTH_CHALLENGE = "auth.challenge"
    HEARTBEAT = "system.heartbeat"
    PING = "system.ping"
    SCREEN_CAPTURE = "screen.capture"
    SCREEN_REGION = "screen.region"
    KEY_PRESS = "input.key_press"
    KEY_TYPE = "input.key_type"
    MOUSE_CLICK = "input.mouse_click"
    MOUSE_MOVE = "input.mouse_move"
    MOUSE_SCROLL = "input.mouse_scroll"


class ResponseType:
    AUTH_RESPONSE = "auth.response"
    HEARTBEAT_ACK = "system.heartbeat_ack"
    PONG = "system.pong"
    AGENT_STATUS = "agent.status"
    SCREEN_DATA = "screen.data"
    ACTION_RESULT = "action.result"
    ERROR = "error"


def build_response(resp_type: str, request_id: str = "",
                   payload: Optional[dict] = None,
                   success: bool = True,
                   error: str = "") -> dict:
    return {
        "type": resp_type,
        "request_id": request_id,
        "success": success,
        "error": error,
        "payload": payload or {},
    }


def get_message_type(message: dict) -> str:
    return message.get("type", "")


def get_payload(message: dict) -> dict:
    return message.get("payload", {})


def get_request_id(message: dict) -> str:
    return message.get("request_id", "")


# ============================================================
# 配置加载
# ============================================================

def load_config(config_path: str = "local_agent/config.yaml") -> dict:
    """加载配置文件"""
    path = Path(config_path)
    if not path.exists():
        logger.error(f"配置文件不存在: {config_path}")
        logger.error("请先复制 local_agent/config.yaml.example 为 config.yaml 并填写配置")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config.get("agent", {})


# ============================================================
# 本地代理主类
# ============================================================

class LocalAgent:
    """
    浅雾羽依本地代理

    负责与服务器建立 WebSocket 连接，处理认证和心跳。
    当前阶段只接收并打印指令，不执行实际的屏幕/控制操作。
    """

    def __init__(self, config: dict):
        self._config = config
        self.server_url = config.get("server_url", "ws://localhost:8765")
        # 安全修复 2026-08-11：token 支持环境变量覆盖（优先级高于配置文件），
        # 避免把共享密钥硬编码在配置文件中。与服务端 YUYI_REMOTE_TOKEN 使用同一值。
        self.token = (
            os.getenv("YUYI_AGENT_TOKEN", "").strip()
            or os.getenv("YUYI_REMOTE_TOKEN", "").strip()
            or str(config.get("token", "") or "").strip()
        )
        self.agent_id = config.get("agent_id", "")
        self.user_id = config.get("user_id", "default")
        self.heartbeat_interval = config.get("heartbeat_interval", 10)
        self.reconnect_delay = config.get("reconnect_delay", 5)
        self.max_reconnect_attempts = config.get("max_reconnect_attempts", 0)  # 0 = 无限重连

        # 如果没配 agent_id，自动生成一个
        if not self.agent_id:
            self.agent_id = f"agent_{platform.node()}_{uuid.uuid4().hex[:6]}"

        self.hostname = platform.node()
        self.os_name = f"{platform.system()} {platform.release()}"

        self._ws = None
        self._running = False
        self._heartbeat_task = None
        self._reconnect_count = 0

        # 截图模块（延迟初始化，按需创建）
        self._screen_capture = None

        # 键鼠控制模块（延迟初始化，按需创建）
        self._input_control = None

    def _get_screen_capture(self):
        """延迟初始化截图模块"""
        if self._screen_capture is None:
            try:
                import sys
                from pathlib import Path
                # 把项目根目录加到 sys.path
                project_root = str(Path(__file__).parent.parent)
                if project_root not in sys.path:
                    sys.path.insert(0, project_root)
                from local_agent.screen_capture import ScreenCapture
                screen_config = self._config.get("screen", {})
                self._screen_capture = ScreenCapture(
                    max_width=screen_config.get("max_width", 1920),
                    quality=screen_config.get("capture_quality", 85),
                )
                logger.info("截图模块已初始化")
            except Exception as e:
                logger.error(f"截图模块初始化失败: {e}")
                return None
        return self._screen_capture

    def _get_input_control(self):
        """延迟初始化键鼠控制模块"""
        if self._input_control is None:
            try:
                import sys
                from pathlib import Path
                project_root = str(Path(__file__).parent.parent)
                if project_root not in sys.path:
                    sys.path.insert(0, project_root)
                from local_agent.input_control import InputControl
                self._input_control = InputControl()
                logger.info("键鼠控制模块已初始化")
            except Exception as e:
                logger.error(f"键鼠控制模块初始化失败: {e}")
                return None
        return self._input_control

    async def connect(self) -> bool:
        """连接到服务器并认证"""
        try:
            import websockets
        except ImportError:
            logger.error("websockets 库未安装，请运行: pip install -r local_agent/requirements.txt")
            return False

        try:
            logger.info(f"正在连接服务器: {self.server_url}")
            self._ws = await websockets.connect(self.server_url)
            logger.info("WebSocket 连接已建立")

            # 发送认证
            auth_msg = build_response(
                ResponseType.AUTH_RESPONSE,
                payload={
                    "agent_id": self.agent_id,
                    "user_id": self.user_id,
                    "token": self.token,
                    "hostname": self.hostname,
                    "os": self.os_name,
                },
            )
            await self._ws.send(json.dumps(auth_msg, ensure_ascii=False))
            logger.info(f"已发送认证请求，agent_id={self.agent_id}")

            # 等待认证结果（读取第一条消息）
            first_msg = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
            data = json.loads(first_msg)
            msg_type = get_message_type(data)

            if msg_type == ResponseType.ERROR:
                error = data.get("error", "unknown")
                logger.error(f"认证失败: {error}")
                await self._ws.close()
                self._ws = None
                return False

            # 收到 PING 或其他消息，说明认证通过
            logger.info("认证成功")
            self._reconnect_count = 0
            return True

        except asyncio.TimeoutError:
            logger.error("认证超时")
        except Exception as e:
            logger.error(f"连接失败: {e}")

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        return False

    async def _heartbeat_loop(self):
        """定时发送心跳"""
        while self._running and self._ws:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                if not self._ws or not self._running:
                    break

                heartbeat_msg = build_response(
                    ResponseType.HEARTBEAT_ACK,
                    payload={"timestamp": __import__("datetime").datetime.now().isoformat()},
                )
                await self._ws.send(json.dumps(heartbeat_msg, ensure_ascii=False))
                logger.debug("心跳已发送")

            except Exception as e:
                logger.warning(f"心跳发送失败: {e}")
                break

    async def _message_loop(self):
        """接收并处理服务器消息"""
        try:
            async for message in self._ws:
                if not self._running:
                    break

                await self._handle_message(message)

        except Exception as e:
            logger.error(f"消息循环异常: {e}")

    async def _handle_message(self, message: str):
        """处理一条服务器消息"""
        try:
            data = json.loads(message)
            msg_type = get_message_type(data)
            payload = get_payload(data)
            request_id = get_request_id(data)

            logger.info(f"收到指令: {msg_type}")

            # ---- 系统类指令 ----
            if msg_type == CommandType.PING:
                # PING - 回复 PONG
                pong = build_response(
                    ResponseType.PONG,
                    request_id=request_id,
                    payload={"msg": "pong"},
                )
                await self._ws.send(json.dumps(pong, ensure_ascii=False))
                logger.debug("已回复 PONG")

            elif msg_type == CommandType.HEARTBEAT:
                # 服务器主动发心跳 - 回复确认
                ack = build_response(
                    ResponseType.HEARTBEAT_ACK,
                    request_id=request_id,
                )
                await self._ws.send(json.dumps(ack, ensure_ascii=False))

            # ---- 屏幕类指令 ----
            elif msg_type == CommandType.SCREEN_CAPTURE:
                # 全屏截图
                logger.info("收到截图请求")
                sc = self._get_screen_capture()
                if sc is None:
                    response = build_response(
                        ResponseType.ERROR,
                        request_id=request_id,
                        success=False,
                        error="screen capture module unavailable",
                    )
                    await self._ws.send(json.dumps(response, ensure_ascii=False))
                else:
                    image_base64 = sc.capture_full()
                    if image_base64:
                        response = build_response(
                            ResponseType.SCREEN_DATA,
                            request_id=request_id,
                            payload={
                                "image": image_base64,
                                "format": "jpeg",
                                "timestamp": __import__("datetime").datetime.now().isoformat(),
                            },
                        )
                        logger.info(f"截图完成，返回 {len(image_base64)} bytes (base64)")
                    else:
                        response = build_response(
                            ResponseType.ERROR,
                            request_id=request_id,
                            success=False,
                            error="capture failed",
                        )
                    await self._ws.send(json.dumps(response, ensure_ascii=False))

            elif msg_type == CommandType.SCREEN_REGION:
                # 区域截图
                x = payload.get("x", 0)
                y = payload.get("y", 0)
                width = payload.get("width", 800)
                height = payload.get("height", 600)
                logger.info(f"收到区域截图请求: ({x}, {y}, {width}x{height})")
                sc = self._get_screen_capture()
                if sc is None:
                    response = build_response(
                        ResponseType.ERROR,
                        request_id=request_id,
                        success=False,
                        error="screen capture module unavailable",
                    )
                else:
                    image_base64 = sc.capture_region(x, y, width, height)
                    if image_base64:
                        response = build_response(
                            ResponseType.SCREEN_DATA,
                            request_id=request_id,
                            payload={
                                "image": image_base64,
                                "format": "jpeg",
                                "timestamp": __import__("datetime").datetime.now().isoformat(),
                            },
                        )
                        logger.info(f"区域截图完成，返回 {len(image_base64)} bytes (base64)")
                    else:
                        response = build_response(
                            ResponseType.ERROR,
                            request_id=request_id,
                            success=False,
                            error="capture failed",
                        )
                await self._ws.send(json.dumps(response, ensure_ascii=False))

            # ---- 控制类指令 ----
            elif msg_type == CommandType.KEY_PRESS:
                # 按键
                key = payload.get("key", "")
                logger.info(f"收到按键请求: {key}")
                ic = self._get_input_control()
                if ic is None:
                    result = {"success": False, "error": "input control module unavailable"}
                else:
                    result = ic.press_key(key)
                response = build_response(
                    ResponseType.ACTION_RESULT,
                    request_id=request_id,
                    success=result.get("success", False),
                    error=result.get("error", ""),
                    payload={"action": "key_press", "key": key},
                )
                await self._ws.send(json.dumps(response, ensure_ascii=False))

            elif msg_type == CommandType.KEY_TYPE:
                # 输入文本
                text = payload.get("text", "")
                logger.info(f"收到文本输入请求: {len(text)} 字符")
                ic = self._get_input_control()
                if ic is None:
                    result = {"success": False, "error": "input control module unavailable"}
                else:
                    result = ic.type_text(text)
                response = build_response(
                    ResponseType.ACTION_RESULT,
                    request_id=request_id,
                    success=result.get("success", False),
                    error=result.get("error", ""),
                    payload={"action": "key_type", "chars": result.get("chars", 0)},
                )
                await self._ws.send(json.dumps(response, ensure_ascii=False))

            elif msg_type == CommandType.MOUSE_CLICK:
                # 鼠标点击
                x = payload.get("x")
                y = payload.get("y")
                button = payload.get("button", "left")
                logger.info(f"收到鼠标点击请求: ({x}, {y}), {button}")
                ic = self._get_input_control()
                if ic is None:
                    result = {"success": False, "error": "input control module unavailable"}
                else:
                    result = ic.click(x, y, button)
                response = build_response(
                    ResponseType.ACTION_RESULT,
                    request_id=request_id,
                    success=result.get("success", False),
                    error=result.get("error", ""),
                    payload={
                        "action": "mouse_click",
                        "x": result.get("x", x),
                        "y": result.get("y", y),
                        "button": button,
                    },
                )
                await self._ws.send(json.dumps(response, ensure_ascii=False))

            elif msg_type == CommandType.MOUSE_MOVE:
                # 鼠标移动
                x = payload.get("x", 0)
                y = payload.get("y", 0)
                logger.info(f"收到鼠标移动请求: ({x}, {y})")
                ic = self._get_input_control()
                if ic is None:
                    result = {"success": False, "error": "input control module unavailable"}
                else:
                    result = ic.move_mouse(x, y)
                response = build_response(
                    ResponseType.ACTION_RESULT,
                    request_id=request_id,
                    success=result.get("success", False),
                    error=result.get("error", ""),
                    payload={
                        "action": "mouse_move",
                        "x": result.get("x", x),
                        "y": result.get("y", y),
                    },
                )
                await self._ws.send(json.dumps(response, ensure_ascii=False))

            elif msg_type == CommandType.MOUSE_SCROLL:
                # 鼠标滚轮
                dx = payload.get("dx", 0)
                dy = payload.get("dy", 0)
                logger.info(f"收到鼠标滚轮请求: dx={dx}, dy={dy}")
                ic = self._get_input_control()
                if ic is None:
                    result = {"success": False, "error": "input control module unavailable"}
                else:
                    result = ic.scroll(dx, dy)
                response = build_response(
                    ResponseType.ACTION_RESULT,
                    request_id=request_id,
                    success=result.get("success", False),
                    error=result.get("error", ""),
                    payload={"action": "mouse_scroll", "dx": dx, "dy": dy},
                )
                await self._ws.send(json.dumps(response, ensure_ascii=False))

            # ---- 未知指令 ----
            else:
                logger.warning(f"收到未知指令: {msg_type}")

        except json.JSONDecodeError:
            logger.warning("收到无效 JSON 消息")
        except Exception as e:
            logger.error(f"处理消息异常: {e}")

    async def run(self):
        """运行代理（含自动重连）"""
        self._running = True
        logger.info(f"本地代理启动 | agent_id={self.agent_id} | user={self.user_id}")
        logger.info(f"主机名: {self.hostname}")
        logger.info(f"系统: {self.os_name}")

        while self._running:
            # 尝试连接
            connected = await self.connect()

            if connected:
                # 启动心跳任务
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                # 进入消息循环
                await self._message_loop()

                # 消息循环退出，说明连接断开
                logger.warning("连接已断开")

            if not self._running:
                break

            # 重连逻辑
            self._reconnect_count += 1
            if self.max_reconnect_attempts > 0 and self._reconnect_count >= self.max_reconnect_attempts:
                logger.error(f"已达到最大重连次数 ({self.max_reconnect_attempts})，退出")
                break

            logger.info(f"等待 {self.reconnect_delay} 秒后重连... (第 {self._reconnect_count} 次)")
            await asyncio.sleep(self.reconnect_delay)

        logger.info("本地代理已停止")

    async def stop(self):
        """停止代理"""
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None


async def main():
    """主入口"""
    # 确定配置文件路径
    config_path = os.environ.get(
        "YUYI_AGENT_CONFIG",
        str(Path(__file__).parent / "config.yaml"),
    )

    config = load_config(config_path)
    agent = LocalAgent(config)

    try:
        await agent.run()
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止...")
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
