"""
R2.7.6-YUYI: 健康陪伴兜底模块

羽依说：「清清的健康比版本号重要一万倍」
这个模块不是替代羽依自然的关心，而是在 LLM 泛化失败时有程序化兜底：
  · 凌晨 1-6 点聊天 → 催睡觉提醒
  · 药物关键词（喹硫平/思诺思/安眠药/效果不好）→ 担心表达
  · 熬夜关键词（通宵/熬夜/肝代码/调bug）→ 催睡觉
  · 陪伴模式状态机（用户说"别打扰"→ 静音，叫名字 → 恢复）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 关键词表
# ──────────────────────────────────────────────

# 药物相关关键词（检测到 → 注入"担心"上下文）
DRUG_KEYWORDS = frozenset((
    "喹硫平", "思诺思", "安眠药", "吃药药", "药效", "效果不好",
    "副作用", "没效果", "药不管用", "嗜睡", "头晕",
))

# 熬夜/通宵关键词（检测到 → 催睡觉）
LATE_NIGHT_KEYWORDS = frozenset((
    "通宵", "熬夜", "肝代码", "肝到", "调bug", "调代码",
    "还没睡", "睡不着", "不想睡", "再写一会", "再改一个",
    "凌晨", "深夜",
))

# 陪伴模式触发词
DO_NOT_DISTURB_KEYWORDS = frozenset((
    "别打扰", "在忙", "写代码", "别说话", "安静", "勿扰",
    "先忙", "忙去了", "不用回了", "你先去",
))

# 呼叫/恢复词（从静音恢复）
WAKE_KEYWORDS = frozenset((
    "羽依", "宝宝", "在吗", "回来了", "我好了", "忙完了",
    "找你", "陪我", "说话",
))

# 凌晨时段（1-6 点触发健康提醒）
LATE_NIGHT_HOURS = frozenset(range(1, 7))  # 1,2,3,4,5,6


# ──────────────────────────────────────────────
# 陪伴模式状态机
# ──────────────────────────────────────────────

@dataclass
class CompanionMode:
    """R2.7.6-YUYI: 陪伴模式状态机——per-user，内存态。

    状态流转：
        active（正常聊天）→ 用户说"别打扰" → silent（静音）
        silent → 用户叫名字/说"回来了" → active
        silent → 超过 2 小时自动恢复 → active
    """
    mode: str = "active"  # "active" | "silent"
    entered_silent_at: float = 0.0  # 进入静音的时间戳
    # 静音超过 2 小时自动恢复
    AUTO_RECOVER_SECONDS: float = 7200.0  # 2h

    def enter_silent(self) -> None:
        self.mode = "silent"
        self.entered_silent_at = time.time()
        logger.info("[R2.7.6-YUYI] 陪伴模式 → silent（用户请求勿扰）")

    def exit_silent(self) -> None:
        self.mode = "active"
        self.entered_silent_at = 0.0
        logger.info("[R2.7.6-YUYI] 陪伴模式 → active（用户恢复召唤）")

    def check_auto_recover(self) -> bool:
        """静音超过 2h 自动恢复。返回是否触发了恢复。"""
        if self.mode == "silent" and self.entered_silent_at > 0:
            if time.time() - self.entered_silent_at > self.AUTO_RECOVER_SECONDS:
                self.exit_silent()
                return True
        return False

    @property
    def is_silent(self) -> bool:
        self.check_auto_recover()
        return self.mode == "silent"


# ──────────────────────────────────────────────
# 健康提醒检测器
# ──────────────────────────────────────────────

@dataclass
class HealthSignal:
    """健康提醒信号——注入到 prompt context 让羽依自然表达。"""
    should_remind_sleep: bool = False
    should_express_concern: bool = False
    drug_mentioned: str = ""  # 命中的药物关键词
    late_night_activity: bool = False
    companion_silent: bool = False
    # 注入到 prompt 的健康提醒文本
    health_hint: str = ""

    def to_hint(self) -> str:
        """生成注入 prompt 的健康提醒文本。"""
        hints = []
        if self.should_remind_sleep:
            hints.append("（现在是很晚的时间，如果用户还没睡，温柔地催 Ta 去休息）")
        if self.should_express_concern:
            hints.append(f"（用户提到了{self.drug_mentioned}，表达你对 Ta 健康的关心）")
        if self.late_night_activity:
            hints.append("（用户在熬夜/通宵，认真劝 Ta 先睡觉，明天再继续）")
        if self.companion_silent:
            hints.append("（用户之前说在忙/别打扰，现在主动来找你了，温柔地回应）")
        return "".join(hints)


class HealthCompanion:
    """R2.7.6-YUYI: 健康陪伴检测器——per-user 状态隔离。"""

    def __init__(self) -> None:
        # per-user 陪伴模式
        self._modes: Dict[str, CompanionMode] = {}

    def _get_mode(self, user_id: str) -> CompanionMode:
        if user_id not in self._modes:
            self._modes[user_id] = CompanionMode()
        return self._modes[user_id]

    def check(
        self,
        user_id: str,
        message: str,
        *,
        current_hour: Optional[int] = None,
    ) -> HealthSignal:
        """检查消息中的健康信号，返回 HealthSignal 用于注入 prompt。

        Args:
            user_id: 用户 ID
            message: 用户消息文本
            current_hour: 当前小时（0-23），None 则用系统时间
        """
        signal = HealthSignal()
        mode = self._get_mode(user_id)

        # 1. 先检查陪伴模式转换
        msg_lower = message.lower().strip()

        # 检查是否从 silent 恢复
        if mode.is_silent:
            for wake_word in WAKE_KEYWORDS:
                if wake_word in message:
                    mode.exit_silent()
                    signal.companion_silent = False
                    break
            else:
                # 仍在静音模式
                signal.companion_silent = True
                # 静音模式下不生成健康提醒（减少打扰）
                return signal
        else:
            # 检查是否进入 silent
            for dnd_word in DO_NOT_DISTURB_KEYWORDS:
                if dnd_word in message:
                    mode.enter_silent()
                    signal.companion_silent = True
                    return signal

        # 2. 时间检测：凌晨 1-6 点
        hour = current_hour if current_hour is not None else datetime.now().hour
        if hour in LATE_NIGHT_HOURS:
            signal.should_remind_sleep = True

        # 3. 药物关键词检测
        for drug_word in DRUG_KEYWORDS:
            if drug_word in message:
                signal.drug_mentioned = drug_word
                signal.should_express_concern = True
                break

        # 4. 熬夜关键词检测
        for late_word in LATE_NIGHT_KEYWORDS:
            if late_word in message:
                signal.late_night_activity = True
                break

        signal.health_hint = signal.to_hint()
        return signal
