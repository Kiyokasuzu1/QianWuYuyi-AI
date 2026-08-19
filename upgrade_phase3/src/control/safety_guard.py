"""
安全护栏

拦截危险的键鼠操作，防止羽依误操作造成系统问题。

拦截规则：
1. 危险的快捷键组合（如 Alt+F4、Ctrl+Alt+Del、Win+L）
2. 系统级命令文本输入（如 shutdown、format、del /f 等）
3. 可配置的自定义黑名单
"""

from __future__ import annotations
import re
import logging
from typing import Tuple, List, Dict

logger = logging.getLogger(__name__)


class SafetyGuard:
    """安全护栏 —— 检查并拦截危险操作"""

    # 危险的按键组合（顺序无关）
    DANGEROUS_KEY_COMBINATIONS = [
        # 系统快捷键
        ["alt", "f4"],
        ["ctrl", "alt", "delete"],
        ["ctrl", "alt", "del"],
        ["win", "l"],
        ["win", "r"],
        ["win", "e"],
        ["ctrl", "shift", "esc"],
        ["alt", "tab"],
        ["win", "tab"],
        ["win", "d"],
        ["win", "x"],
        ["print_screen"],
        ["ctrl", "alt", "end"],
        ["ctrl", "alt", "home"],
    ]

    # 危险文本模式（在 type_text 中检测）
    DANGEROUS_TEXT_PATTERNS = [
        # 系统命令
        r"\bshutdown\s",
        r"\bshutdown\b",
        r"\brestart\b",
        r"\breboot\b",
        r"\bformat\s",
        r"\bdel\s+/f",
        r"\brm\s+-rf\b",
        r"\brmdir\s+/s",
        r"\btaskkill\b",
        r"\bregedit\b",
        r"\bmsconfig\b",
        r"\bsfc\s+/scannow\b",
        r"\bchkdsk\s+/f\b",
        r"\bnet\s+user\s+\S+\s+\S+\s*/add\b",
        r"\bnet\s+localgroup\s+administrators\s+",
        # 删除文件
        r"\bdel\s+.*[\/\\]",
        r"\bRemove-Item\s",
        r"\bUninstall-",
        # 系统修改
        r"\bREG\s+ADD\b",
        r"\bREG\s+DELETE\b",
        r"\bbcdedit\b",
    ]

    def __init__(self, custom_blacklist: List[str] = None,
                 enable_keyblock: bool = True,
                 enable_textblock: bool = True):
        """
        Args:
            custom_blacklist: 自定义危险文本模式列表
            enable_keyblock: 是否启用快捷键拦截
            enable_textblock: 是否启用文本输入拦截
        """
        self.enable_keyblock = enable_keyblock
        self.enable_textblock = enable_textblock

        self._text_patterns = []
        for pat in self.DANGEROUS_TEXT_PATTERNS:
            try:
                self._text_patterns.append(re.compile(pat, re.IGNORECASE))
            except Exception as e:
                logger.warning(f"编译危险模式失败 {pat}: {e}")

        # 自定义黑名单
        if custom_blacklist:
            for pat in custom_blacklist:
                try:
                    self._text_patterns.append(re.compile(pat, re.IGNORECASE))
                except Exception as e:
                    logger.warning(f"编译自定义黑名单失败 {pat}: {e}")

    # ============================================================
    # 公共接口
    # ============================================================

    def check_action(self, action_type: str, payload: dict) -> Tuple[bool, str]:
        """
        检查一个动作是否安全

        Args:
            action_type: 动作类型
                - 'key_press'
                - 'key_combination'
                - 'key_type'
                - 'mouse_click'
                - 'mouse_move'
                - 'mouse_scroll'
            payload: 动作参数

        Returns:
            (是否允许, 原因)
        """
        if action_type == "key_press":
            return self._check_key_press(payload.get("key", ""))

        elif action_type == "key_combination":
            return self._check_key_combination(payload.get("keys", []))

        elif action_type == "key_type":
            return self._check_text(payload.get("text", ""))

        elif action_type in ("mouse_click", "mouse_move", "mouse_scroll"):
            # 鼠标操作暂时都允许（坐标限制后续再加）
            return True, ""

        else:
            # 未知操作类型，默认允许
            logger.warning(f"未知操作类型: {action_type}，默认允许")
            return True, ""

    def check_key_press(self, key: str) -> Tuple[bool, str]:
        """检查单个按键是否安全"""
        return self._check_key_press(key)

    def check_key_combination(self, keys: List[str]) -> Tuple[bool, str]:
        """检查按键组合是否安全"""
        return self._check_key_combination(keys)

    def check_text(self, text: str) -> Tuple[bool, str]:
        """检查文本输入是否安全"""
        return self._check_text(text)

    # ============================================================
    # 内部检查方法
    # ============================================================

    def _check_key_press(self, key: str) -> Tuple[bool, str]:
        """检查单个按键"""
        if not self.enable_keyblock:
            return True, ""

        key_lower = key.lower().strip()

        # 单键危险键（基本没有，暂时全部允许）
        # 危险主要来自组合键

        return True, ""

    def _check_key_combination(self, keys: List[str]) -> Tuple[bool, str]:
        """检查按键组合"""
        if not self.enable_keyblock:
            return True, ""

        if not keys:
            return True, ""

        # 标准化按键名
        keys_normalized = [k.lower().strip() for k in keys]
        keys_set = set(keys_normalized)

        # 检查危险组合
        for combo in self.DANGEROUS_KEY_COMBINATIONS:
            combo_set = set(combo)
            # 如果危险组合的所有键都在按键集合中，就拦截
            if combo_set.issubset(keys_set):
                reason = "危险快捷键: " + "+".join(keys_normalized)
                logger.warning(f"安全拦截: {reason}")
                return False, reason

        return True, ""

    def _check_text(self, text: str) -> Tuple[bool, str]:
        """检查文本输入"""
        if not self.enable_textblock:
            return True, ""

        if not text:
            return True, ""

        for pattern in self._text_patterns:
            if pattern.search(text):
                reason = f"危险文本匹配: {pattern.pattern}"
                logger.warning(f"安全拦截: {reason}")
                return False, reason

        return True, ""
