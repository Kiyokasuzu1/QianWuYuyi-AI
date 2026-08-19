"""
时间窗口工具。

用于上下文管理器按时间段筛选记忆。
"""

from datetime import datetime, timedelta
from typing import Tuple, Optional


class TimeWindow:
    """
    时间窗口工具类。

    定义一天中的时间段，并提供时间范围查询和判断功能。
    """

    MORNING = "morning"       # 06:00 - 12:00
    AFTERNOON = "afternoon"   # 12:00 - 18:00
    EVENING = "evening"       # 18:00 - 22:00
    NIGHT = "night"           # 22:00 - 06:00

    _WINDOW_RANGES = {
        MORNING: (6, 12),
        AFTERNOON: (12, 18),
        EVENING: (18, 22),
        NIGHT: (22, 6),
    }

    @staticmethod
    def get_time_range(window_type: str, date: Optional[datetime] = None) -> Tuple[str, str]:
        """
        获取指定时间窗口的起止时间（ISO 格式）。

        Args:
            window_type: 时间窗口类型
            date: 目标日期，默认为今天

        Returns:
            (start_iso, end_iso) 起止时间字符串
        """
        if date is None:
            date = datetime.now()

        start_hour, end_hour = TimeWindow._WINDOW_RANGES.get(
            window_type, (0, 24)
        )

        if window_type == TimeWindow.NIGHT:
            # 夜间跨越午夜：22:00 当天 ~ 06:00 次日
            start = date.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            end = (date + timedelta(days=1)).replace(hour=end_hour, minute=0, second=0, microsecond=0)
        else:
            start = date.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            end = date.replace(hour=end_hour, minute=0, second=0, microsecond=0)

        return start.isoformat(), end.isoformat()

    @staticmethod
    def is_in_window(timestamp: str, window_type: str) -> bool:
        """
        判断时间戳是否在指定时间窗口内。

        Args:
            timestamp: ISO 格式时间戳
            window_type: 时间窗口类型

        Returns:
            是否在窗口内
        """
        try:
            ts = datetime.fromisoformat(timestamp)
        except (ValueError, TypeError):
            return False

        start_iso, end_iso = TimeWindow.get_time_range(window_type, ts)
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)

        return start <= ts < end

    @staticmethod
    def get_past_window(days_ago: int, window_type: str = None) -> Tuple[str, str]:
        """
        获取 N 天前的时间窗口。

        Args:
            days_ago: 几天前
            window_type: 时间窗口类型，None 表示全天

        Returns:
            (start_iso, end_iso) 起止时间字符串
        """
        target_date = datetime.now() - timedelta(days=days_ago)

        if window_type is None:
            # 全天
            start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end = (target_date + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            return start.isoformat(), end.isoformat()

        return TimeWindow.get_time_range(window_type, target_date)

    @staticmethod
    def get_recent_hours(hours: int) -> Tuple[str, str]:
        """
        获取最近 N 小时的时间范围。

        Args:
            hours: 小时数

        Returns:
            (start_iso, end_iso) 起止时间字符串
        """
        end = datetime.now()
        start = end - timedelta(hours=hours)
        return start.isoformat(), end.isoformat()

    @staticmethod
    def get_current_window() -> str:
        """获取当前所处的时间窗口类型"""
        now = datetime.now()
        hour = now.hour

        for window_type, (start_h, end_h) in TimeWindow._WINDOW_RANGES.items():
            if window_type == TimeWindow.NIGHT:
                if hour >= start_h or hour < end_h:
                    return window_type
            else:
                if start_h <= hour < end_h:
                    return window_type

        return TimeWindow.AFTERNOON