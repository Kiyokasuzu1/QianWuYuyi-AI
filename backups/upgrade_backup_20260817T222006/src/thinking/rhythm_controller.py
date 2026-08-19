"""
思考节奏控制器。

负责：
- 响应前的确认间隔
- 控制回复节奏
- 管理思考深度
"""

import time
from typing import Dict, List, Optional


class RhythmController:
    """
    思考节奏控制器。

    根据置信度和错误率动态调整响应前的思考间隔。
    不修改现有模块，只在回复前增加一个可选的等待。
    """

    def __init__(self):
        self._base_delay_ms = 500       # 基础延迟
        self._current_delay_ms = 500    # 当前延迟
        self._min_delay_ms = 200        # 最小延迟
        self._max_delay_ms = 2000       # 最大延迟
        self._thinking_depth = 1        # 思考深度（1=轻量, 2=标准, 3=深度）
        self._error_count = 0
        self._total_count = 0
        self._recent_results: List[bool] = []  # 最近的检查结果

    def wait_before_response(self, confidence: float = 1.0):
        """
        响应前的等待。

        根据置信度调整等待时间：
        - 置信度高 → 短等待
        - 置信度低 → 长等待

        Args:
            confidence: 置信度 0~1
        """
        # 置信度越低，等待越长
        adjusted = self._current_delay_ms / max(confidence, 0.1)
        delay_ms = min(adjusted, self._max_delay_ms)
        delay_ms = max(delay_ms, self._min_delay_ms)

        time.sleep(delay_ms / 1000.0)

    def adjust_delay(self, error_rate: float):
        """
        根据错误率调整延迟。

        Args:
            error_rate: 错误率 0~1
        """
        if error_rate > 0.3:
            # 错误率高，增加延迟
            self._current_delay_ms = min(
                int(self._current_delay_ms * 1.5),
                self._max_delay_ms
            )
        elif error_rate < 0.1:
            # 错误率低，减少延迟
            self._current_delay_ms = max(
                int(self._current_delay_ms * 0.8),
                self._min_delay_ms
            )

    def record_result(self, success: bool):
        """记录一次检查结果"""
        self._total_count += 1
        if not success:
            self._error_count += 1

        self._recent_results.append(success)
        if len(self._recent_results) > 20:
            self._recent_results = self._recent_results[-20:]

        # 自动调整延迟
        if len(self._recent_results) >= 5:
            recent_errors = sum(1 for r in self._recent_results[-5:] if not r)
            error_rate = recent_errors / 5
            self.adjust_delay(error_rate)

    def set_thinking_depth(self, depth: int):
        """
        设置思考深度。

        Args:
            depth: 1=轻量, 2=标准, 3=深度
        """
        self._thinking_depth = max(1, min(3, depth))

        # 深度影响基础延迟
        if depth == 1:
            self._base_delay_ms = 200
        elif depth == 2:
            self._base_delay_ms = 500
        else:
            self._base_delay_ms = 1000

        self._current_delay_ms = self._base_delay_ms

    def get_delay(self) -> int:
        """获取当前延迟（毫秒）"""
        return self._current_delay_ms

    def get_stats(self) -> Dict:
        """获取统计数据"""
        error_rate = (
            self._error_count / self._total_count
            if self._total_count > 0
            else 0.0
        )
        return {
            "current_delay_ms": self._current_delay_ms,
            "base_delay_ms": self._base_delay_ms,
            "thinking_depth": self._thinking_depth,
            "total_count": self._total_count,
            "error_count": self._error_count,
            "error_rate": round(error_rate, 4),
        }

    def reset(self):
        """重置状态"""
        self._current_delay_ms = self._base_delay_ms
        self._error_count = 0
        self._total_count = 0
        self._recent_results = []


# 全局实例
_global_rhythm: Optional[RhythmController] = None


def get_rhythm_controller() -> RhythmController:
    """获取全局节奏控制器实例"""
    global _global_rhythm
    if _global_rhythm is None:
        _global_rhythm = RhythmController()
    return _global_rhythm