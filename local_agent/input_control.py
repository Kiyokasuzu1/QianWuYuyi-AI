"""
键鼠控制模块

使用 pynput 模拟键盘和鼠标操作。
pynput 不可用时降级返回错误，不影响代理主程序。
"""

from __future__ import annotations
import logging
import time

logger = logging.getLogger("local_agent.input")


class InputControl:
    """键鼠控制器"""

    def __init__(self):
        self._keyboard = None  # 延迟初始化
        self._mouse = None     # 延迟初始化

    def _ensure_keyboard(self):
        """延迟初始化键盘控制器"""
        if self._keyboard is None:
            try:
                from pynput.keyboard import Controller as KeyboardController
                self._keyboard = KeyboardController()
            except ImportError:
                logger.error("pynput 库未安装，请运行: pip install pynput")
                raise
            except Exception as e:
                logger.error(f"初始化键盘控制器失败: {e}")
                raise
        return self._keyboard

    def _ensure_mouse(self):
        """延迟初始化鼠标控制器"""
        if self._mouse is None:
            try:
                from pynput.mouse import Controller as MouseController
                self._mouse = MouseController()
            except ImportError:
                logger.error("pynput 库未安装，请运行: pip install pynput")
                raise
            except Exception as e:
                logger.error(f"初始化鼠标控制器失败: {e}")
                raise
        return self._mouse

    # ============================================================
    # 键盘操作
    # ============================================================

    def press_key(self, key: str) -> dict:
        """
        按下并释放一个按键

        Args:
            key: 按键名称，如 'a'、'enter'、'space'、'f5'、'ctrl'

        Returns:
            {"success": True/False, "error": "..."}
        """
        try:
            from pynput.keyboard import Key, KeyCode
            keyboard = self._ensure_keyboard()

            key_obj = self._parse_key(key)
            keyboard.press(key_obj)
            keyboard.release(key_obj)

            logger.info(f"按键: {key}")
            return {"success": True, "error": ""}

        except ImportError:
            return {"success": False, "error": "pynput not installed"}
        except Exception as e:
            logger.error(f"按键失败 {key}: {e}")
            return {"success": False, "error": str(e)}

    def type_text(self, text: str) -> dict:
        """
        输入一段文本

        Args:
            text: 要输入的文本

        Returns:
            {"success": True/False, "error": "...", "chars": N}
        """
        try:
            keyboard = self._ensure_keyboard()
            keyboard.type(text)

            logger.info(f"输入文本: {len(text)} 字符")
            return {"success": True, "error": "", "chars": len(text)}

        except ImportError:
            return {"success": False, "error": "pynput not installed"}
        except Exception as e:
            logger.error(f"输入文本失败: {e}")
            return {"success": False, "error": str(e)}

    def key_combination(self, keys: list) -> dict:
        """
        按键组合（如 ctrl+c、alt+tab）

        Args:
            keys: 按键列表，如 ["ctrl", "c"]

        Returns:
            {"success": True/False, "error": "..."}
        """
        try:
            from pynput.keyboard import Key, KeyCode
            keyboard = self._ensure_keyboard()

            key_objs = [self._parse_key(k) for k in keys]

            # 按下所有键（顺序）
            for k in key_objs:
                keyboard.press(k)

            # 释放所有键（逆序）
            for k in reversed(key_objs):
                keyboard.release(k)

            logger.info("组合键: " + "+".join(keys))
            return {"success": True, "error": ""}

        except ImportError:
            return {"success": False, "error": "pynput not installed"}
        except Exception as e:
            logger.error(f"组合键失败 {keys}: {e}")
            return {"success": False, "error": str(e)}

    def _parse_key(self, key: str):
        """
        解析按键名称为 pynput Key 对象

        支持：
        - 单个字符: 'a', '1', ' '
        - 特殊键: 'enter', 'space', 'backspace', 'tab', 'esc', 'ctrl',
                  'alt', 'shift', 'win', 'caps_lock', 'f1'~'f12',
                  'up', 'down', 'left', 'right', 'delete', 'home', 'end'
        """
        from pynput.keyboard import Key, KeyCode

        key_lower = key.lower().strip()

        # 特殊键映射
        special_keys = {
            "enter": Key.enter,
            "return": Key.enter,
            "space": Key.space,
            " ": Key.space,
            "backspace": Key.backspace,
            "tab": Key.tab,
            "esc": Key.esc,
            "escape": Key.esc,
            "ctrl": Key.ctrl,
            "control": Key.ctrl,
            "alt": Key.alt,
            "shift": Key.shift,
            "win": Key.cmd,
            "cmd": Key.cmd,
            "command": Key.cmd,
            "caps_lock": Key.caps_lock,
            "capslock": Key.caps_lock,
            "up": Key.up,
            "down": Key.down,
            "left": Key.left,
            "right": Key.right,
            "delete": Key.delete,
            "del": Key.delete,
            "home": Key.home,
            "end": Key.end,
            "page_up": Key.page_up,
            "pageup": Key.page_up,
            "page_down": Key.page_down,
            "pagedown": Key.page_down,
            "insert": Key.insert,
            "ins": Key.insert,
            "print_screen": Key.print_screen,
            "prtsc": Key.print_screen,
        }

        # F1 ~ F12
        for i in range(1, 13):
            special_keys[f"f{i}"] = getattr(Key, f"f{i}", None)

        if key_lower in special_keys and special_keys[key_lower] is not None:
            return special_keys[key_lower]

        # 普通字符
        if len(key) == 1:
            return KeyCode.from_char(key)

        raise ValueError(f"未知按键: {key}")

    # ============================================================
    # 鼠标操作
    # ============================================================

    def click(self, x: int = None, y: int = None, button: str = "left") -> dict:
        """
        鼠标点击

        Args:
            x, y: 点击坐标（None 则不移动，在当前位置点击）
            button: 'left' / 'right' / 'middle'

        Returns:
            {"success": True/False, "error": "..."}
        """
        try:
            from pynput.mouse import Button
            mouse = self._ensure_mouse()

            # 移动到指定位置
            if x is not None and y is not None:
                mouse.position = (x, y)

            # 映射按钮
            button_map = {
                "left": Button.left,
                "right": Button.right,
                "middle": Button.middle,
            }
            btn = button_map.get(button.lower(), Button.left)

            # 点击
            mouse.click(btn)

            pos = mouse.position
            logger.info(f"鼠标点击: ({pos[0]}, {pos[1]}), {button}")
            return {"success": True, "error": "", "x": pos[0], "y": pos[1]}

        except ImportError:
            return {"success": False, "error": "pynput not installed"}
        except Exception as e:
            logger.error(f"鼠标点击失败: {e}")
            return {"success": False, "error": str(e)}

    def double_click(self, x: int = None, y: int = None, button: str = "left") -> dict:
        """
        鼠标双击

        Args:
            x, y: 点击坐标（None 则不移动）
            button: 'left' / 'right' / 'middle'

        Returns:
            {"success": True/False, "error": "..."}
        """
        try:
            from pynput.mouse import Button
            mouse = self._ensure_mouse()

            if x is not None and y is not None:
                mouse.position = (x, y)

            button_map = {
                "left": Button.left,
                "right": Button.right,
                "middle": Button.middle,
            }
            btn = button_map.get(button.lower(), Button.left)

            mouse.click(btn, 2)

            pos = mouse.position
            logger.info(f"鼠标双击: ({pos[0]}, {pos[1]}), {button}")
            return {"success": True, "error": "", "x": pos[0], "y": pos[1]}

        except ImportError:
            return {"success": False, "error": "pynput not installed"}
        except Exception as e:
            logger.error(f"鼠标双击失败: {e}")
            return {"success": False, "error": str(e)}

    def move_mouse(self, x: int, y: int) -> dict:
        """
        移动鼠标到指定位置

        Returns:
            {"success": True/False, "error": "...", "x": ..., "y": ...}
        """
        try:
            mouse = self._ensure_mouse()
            mouse.position = (x, y)

            pos = mouse.position
            logger.info(f"鼠标移动: ({pos[0]}, {pos[1]})")
            return {"success": True, "error": "", "x": pos[0], "y": pos[1]}

        except ImportError:
            return {"success": False, "error": "pynput not installed"}
        except Exception as e:
            logger.error(f"鼠标移动失败: {e}")
            return {"success": False, "error": str(e)}

    def scroll(self, dx: int = 0, dy: int = 3) -> dict:
        """
        鼠标滚轮滚动

        Args:
            dx: 水平滚动量（正数向右）
            dy: 垂直滚动量（正数向上，负数向下）

        Returns:
            {"success": True/False, "error": "..."}
        """
        try:
            mouse = self._ensure_mouse()
            mouse.scroll(dx, dy)

            logger.info(f"鼠标滚轮: dx={dx}, dy={dy}")
            return {"success": True, "error": ""}

        except ImportError:
            return {"success": False, "error": "pynput not installed"}
        except Exception as e:
            logger.error(f"鼠标滚轮失败: {e}")
            return {"success": False, "error": str(e)}

    def press_mouse(self, button: str = "left") -> dict:
        """按住鼠标键"""
        try:
            from pynput.mouse import Button
            mouse = self._ensure_mouse()
            button_map = {"left": Button.left, "right": Button.right, "middle": Button.middle}
            btn = button_map.get(button.lower(), Button.left)
            mouse.press(btn)
            return {"success": True, "error": ""}
        except ImportError:
            return {"success": False, "error": "pynput not installed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def release_mouse(self, button: str = "left") -> dict:
        """释放鼠标键"""
        try:
            from pynput.mouse import Button
            mouse = self._ensure_mouse()
            button_map = {"left": Button.left, "right": Button.right, "middle": Button.middle}
            btn = button_map.get(button.lower(), Button.left)
            mouse.release(btn)
            return {"success": True, "error": ""}
        except ImportError:
            return {"success": False, "error": "pynput not installed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_mouse_position(self) -> dict:
        """获取当前鼠标位置"""
        try:
            mouse = self._ensure_mouse()
            pos = mouse.position
            return {"success": True, "error": "", "x": pos[0], "y": pos[1]}
        except ImportError:
            return {"success": False, "error": "pynput not installed", "x": 0, "y": 0}
        except Exception as e:
            return {"success": False, "error": str(e), "x": 0, "y": 0}
