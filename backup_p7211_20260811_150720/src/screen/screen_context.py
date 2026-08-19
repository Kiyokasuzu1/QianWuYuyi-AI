"""
屏幕上下文管理器

通过 agent_server 向本地代理请求截图，调用 OCR 分析后生成描述，供 orchestrator 注入 prompt。
代理离线、截图超时、OCR 失败时优雅降级，不影响主流程。
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional

from src.remote.agent_server import AgentServer, get_agent_server
from src.remote.protocol import CommandType, ResponseType
from src.screen.screen_analyzer import ScreenAnalyzer

logger = logging.getLogger(__name__)


class ScreenContextManager:
    """屏幕上下文管理器 —— 集成到 orchestrator"""

    def __init__(self, agent_server: Optional[AgentServer] = None,
                 analyzer: Optional[ScreenAnalyzer] = None,
                 capture_timeout: int = 10,
                 ocr_language: str = "chi_sim+eng"):
        """
        Args:
            agent_server: AgentServer 实例（如果为 None，尝试获取全局实例）
            analyzer: ScreenAnalyzer 实例（如果为 None，自动创建）
            capture_timeout: 截图超时（秒）
            ocr_language: OCR 语言
        """
        self.agent_server = agent_server or get_agent_server()
        self.analyzer = analyzer or ScreenAnalyzer(lang=ocr_language)
        self.capture_timeout = capture_timeout

    async def get_screen_description(self, user_id: str,
                                     timeout: Optional[int] = None) -> Dict:
        """
        异步获取当前屏幕描述

        Args:
            user_id: 用户 ID
            timeout: 截图超时（秒），None 则使用默认值

        Returns:
            {
                "available": True/False,      # 是否成功获取
                "text": "屏幕文字",            # OCR 文字
                "description": "描述文本",      # 供 prompt 注入
                "error": "",                   # 错误信息
                "timestamp": "...",
            }
        """
        ts = datetime.now().isoformat()
        effective_timeout = timeout or self.capture_timeout

        # 1. 检查 agent_server 是否可用
        if not self.agent_server:
            return self._unavailable("远程代理服务未启动", ts)

        # 2. 检查代理是否在线
        if not self.agent_server.is_user_online(user_id):
            return self._unavailable("本地代理离线", ts)

        # 3. 找到 agent_id
        info = self.agent_server.registry.get_agent_by_user(user_id)
        if not info:
            return self._unavailable("未找到代理信息", ts)

        # 4. 请求截图并等待响应
        try:
            result = await self.agent_server.send_command_and_wait(
                agent_id=info.agent_id,
                cmd_type=CommandType.SCREEN_CAPTURE,
                timeout=effective_timeout,
            )
        except Exception as e:
            logger.error(f"请求截图异常: {e}")
            return self._unavailable(f"请求异常: {e}", ts)

        # 5. 检查响应
        if not result or not result.get("success", False):
            error = result.get("error", "unknown") if result else "no response"
            logger.warning(f"截图失败: {error}")
            return self._unavailable(f"截图失败: {error}", ts)

        # 6. 提取图片数据
        payload = result.get("payload", {})
        image_base64 = payload.get("image", "")

        if not image_base64:
            return self._unavailable("截图数据为空", ts)

        # 7. OCR 分析
        try:
            analysis = self.analyzer.analyze(image_base64)
        except Exception as e:
            logger.error(f"屏幕分析异常: {e}")
            return self._unavailable(f"分析失败: {e}", ts)

        return {
            "available": True,
            "text": analysis.get("text", ""),
            "description": analysis.get("description", ""),
            "ocr_success": analysis.get("ocr_success", False),
            "error": "",
            "timestamp": ts,
        }

    def get_context_for_prompt(self, user_id: str) -> Dict:
        """
        同步接口：获取屏幕上下文，供 orchestrator 调用

        内部将异步调用转为同步。如果当前没有事件循环，创建临时循环。
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在已有事件循环中，创建任务但不 await（返回降级结果）
                # 这种情况下应该用异步接口 get_screen_description
                logger.warning("当前已在事件循环中，建议使用异步接口 get_screen_description")
                return self._unavailable("请在异步上下文中调用", datetime.now().isoformat())
            else:
                return loop.run_until_complete(
                    self.get_screen_description(user_id)
                )
        except RuntimeError:
            # 没有事件循环，创建一个新的
            return asyncio.run(self.get_screen_description(user_id))
        except Exception as e:
            logger.error(f"获取屏幕上下文异常: {e}")
            return self._unavailable(str(e), datetime.now().isoformat())

    def _unavailable(self, reason: str, timestamp: str) -> Dict:
        """生成不可用的降级结果"""
        return {
            "available": False,
            "text": "",
            "description": "",
            "ocr_success": False,
            "error": reason,
            "timestamp": timestamp,
        }


# 全局实例
_global_manager: Optional[ScreenContextManager] = None


def get_screen_context_manager() -> Optional[ScreenContextManager]:
    """获取全局屏幕上下文管理器（可能为 None，如果未启用）"""
    return _global_manager


def init_screen_context_manager(agent_server=None, capture_timeout=10,
                                ocr_language="chi_sim+eng") -> ScreenContextManager:
    """初始化全局屏幕上下文管理器"""
    global _global_manager
    _global_manager = ScreenContextManager(
        agent_server=agent_server,
        capture_timeout=capture_timeout,
        ocr_language=ocr_language,
    )
    return _global_manager
