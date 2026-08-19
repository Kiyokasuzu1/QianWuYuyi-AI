"""
控制管理器

整合权限检查、安全护栏、指令下发的统一入口。
所有电脑控制操作都通过这里，确保权限和安全检查不被绕过。
"""

from __future__ import annotations
import asyncio
import logging
from typing import Dict, Optional

from src.remote.agent_server import AgentServer, get_agent_server
from src.remote.protocol import CommandType
from src.control.safety_guard import SafetyGuard

logger = logging.getLogger(__name__)


class ControlManager:
    """
    控制管理器 —— 所有电脑控制操作的统一入口

    流程：
    1. 检查代理是否在线
    2. 检查用户是否有操作权限
    3. 安全护栏检查
    4. 下发指令到本地代理
    5. 等待执行结果
    6. 记录审计日志（调用方负责）
    """

    def __init__(self, agent_server: Optional[AgentServer] = None,
                 safety_guard: Optional[SafetyGuard] = None,
                 action_timeout: int = 10):
        """
        Args:
            agent_server: AgentServer 实例
            safety_guard: 安全护栏实例
            action_timeout: 动作执行超时（秒）
        """
        self.agent_server = agent_server or get_agent_server()
        self.safety_guard = safety_guard or SafetyGuard()
        self.action_timeout = action_timeout

    # ============================================================
    # 键盘操作
    # ============================================================

    async def press_key(self, user_id: str, key: str) -> Dict:
        """
        按下一个按键

        Returns:
            {"success": True/False, "error": "...", "detail": {...}}
        """
        return await self._execute_action(
            user_id=user_id,
            action_type="key_press",
            cmd_type=CommandType.KEY_PRESS,
            payload={"key": key},
            permission_check=lambda pm: pm.has_permission(user_id, "keyboard"),
        )

    async def type_text(self, user_id: str, text: str) -> Dict:
        """
        输入一段文本

        Returns:
            {"success": True/False, "error": "...", "detail": {...}}
        """
        return await self._execute_action(
            user_id=user_id,
            action_type="key_type",
            cmd_type=CommandType.KEY_TYPE,
            payload={"text": text},
            permission_check=lambda pm: pm.has_permission(user_id, "keyboard"),
        )

    async def key_combination(self, user_id: str, keys: list) -> Dict:
        """
        按键组合

        Returns:
            {"success": True/False, "error": "...", "detail": {...}}
        """
        # 先做安全检查
        allowed, reason = self.safety_guard.check_key_combination(keys)
        if not allowed:
            return self._blocked_result(reason)

        # 注意：组合键通过多次 press_key 实现比较复杂，
        # 这里先做安全检查，具体执行暂时不实现（本地代理也不支持组合键指令）
        # 后续可以新增一个 key_combination 指令类型
        return {
            "success": False,
            "error": "组合键暂未实现",
            "detail": {},
        }

    # ============================================================
    # 鼠标操作
    # ============================================================

    async def click(self, user_id: str, x: int = None, y: int = None,
                    button: str = "left") -> Dict:
        """
        鼠标点击

        Returns:
            {"success": True/False, "error": "...", "detail": {...}}
        """
        payload = {"button": button}
        if x is not None:
            payload["x"] = x
        if y is not None:
            payload["y"] = y

        return await self._execute_action(
            user_id=user_id,
            action_type="mouse_click",
            cmd_type=CommandType.MOUSE_CLICK,
            payload=payload,
            permission_check=lambda pm: pm.has_permission(user_id, "mouse"),
        )

    async def move_mouse(self, user_id: str, x: int, y: int) -> Dict:
        """
        移动鼠标

        Returns:
            {"success": True/False, "error": "...", "detail": {...}}
        """
        return await self._execute_action(
            user_id=user_id,
            action_type="mouse_move",
            cmd_type=CommandType.MOUSE_MOVE,
            payload={"x": x, "y": y},
            permission_check=lambda pm: pm.has_permission(user_id, "mouse"),
        )

    async def scroll(self, user_id: str, dx: int = 0, dy: int = 3) -> Dict:
        """
        鼠标滚轮

        Returns:
            {"success": True/False, "error": "...", "detail": {...}}
        """
        return await self._execute_action(
            user_id=user_id,
            action_type="mouse_scroll",
            cmd_type=CommandType.MOUSE_SCROLL,
            payload={"dx": dx, "dy": dy},
            permission_check=lambda pm: pm.has_permission(user_id, "mouse"),
        )

    # ============================================================
    # 内部方法
    # ============================================================

    async def _execute_action(self, user_id: str, action_type: str,
                              cmd_type: str, payload: dict,
                              permission_check=None) -> Dict:
        """
        执行控制动作的统一流程

        Args:
            user_id: 用户 ID
            action_type: 动作类型（用于安全检查和日志）
            cmd_type: 指令类型（发给代理）
            payload: 指令参数
            permission_check: 权限检查函数，接受 permission_manager，返回 bool

        Returns:
            {"success": True/False, "error": "...", "detail": {...}}
        """
        # 1. 检查 agent_server
        if not self.agent_server:
            return self._error_result("远程代理服务未启动")

        if not self.agent_server.is_user_online(user_id):
            return self._error_result("本地代理离线")

        # 2. 权限检查（如果提供了权限管理器）
        # 注意：permission_manager 是可选的，没有就跳过权限检查
        # 实际使用时应该从外部注入
        if permission_check:
            pm = getattr(self, "_permission_manager", None)
            if pm is not None:
                if not permission_check(pm):
                    return self._error_result("权限不足")

        # 3. 安全护栏检查
        allowed, reason = self.safety_guard.check_action(action_type, payload)
        if not allowed:
            return self._blocked_result(reason)

        # 4. 找到 agent_id
        info = self.agent_server.registry.get_agent_by_user(user_id)
        if not info:
            return self._error_result("未找到代理信息")

        # 5. 下发指令并等待结果
        try:
            result = await self.agent_server.send_command_and_wait(
                agent_id=info.agent_id,
                cmd_type=cmd_type,
                payload=payload,
                timeout=self.action_timeout,
            )
        except Exception as e:
            logger.error(f"执行动作异常 {action_type}: {e}")
            return self._error_result(f"执行异常: {e}")

        # 6. 处理结果
        if not result:
            return self._error_result("无响应")

        if not result.get("success", False):
            error = result.get("error", "unknown")
            return self._error_result(f"执行失败: {error}")

        return {
            "success": True,
            "error": "",
            "detail": result.get("payload", {}),
        }

    def _error_result(self, error: str) -> Dict:
        return {
            "success": False,
            "error": error,
            "detail": {},
        }

    def _blocked_result(self, reason: str) -> Dict:
        logger.warning(f"安全护栏拦截: {reason}")
        return {
            "success": False,
            "error": f"安全拦截: {reason}",
            "blocked": True,
            "detail": {},
        }

    def set_permission_manager(self, pm):
        """设置权限管理器（可选）"""
        self._permission_manager = pm


# 全局实例
_global_manager: Optional[ControlManager] = None


def get_control_manager() -> Optional[ControlManager]:
    """获取全局控制管理器（可能为 None，如果未启用）"""
    return _global_manager


def init_control_manager(agent_server=None, action_timeout=10) -> ControlManager:
    """初始化全局控制管理器"""
    global _global_manager
    _global_manager = ControlManager(
        agent_server=agent_server,
        action_timeout=action_timeout,
    )
    return _global_manager
