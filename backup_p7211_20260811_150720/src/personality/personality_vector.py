"""
人格向量（PersonalityVector）
职责：封装人格数据，提供统一访问接口
支持：属性访问（p.warmth）、字典访问（p['warmth']）、原有方法
"""

from typing import Dict, Optional, List, Any


class PersonalityVector:
    """
    人格向量
    封装人格参数，支持统一访问和描述生成
    """

    def __init__(self, data: Dict):
        self._data = data

    # ========== 原有数值型专用方法（保持兼容） ==========
    def get(self, key: str, default: float = 0.0) -> float:
        """获取数值型参数，默认返回 0.0（供旧代码使用）"""
        val = self._data.get(key, default)
        return val

    def get_label(self, key: str, default: str = "未知") -> str:
        """获取标签型参数"""
        return self._data.get(key, default)

    def get_behaviors(self) -> Dict:
        """获取行为倾向"""
        return self._data.get("behaviors", {})

    def get_identities(self) -> List[str]:
        """获取自我认知列表"""
        return self._data.get("identities", [])

    def get_all(self) -> Dict:
        """获取全部数据"""
        return self._data

    def summary(self) -> Dict:
        """返回关键参数摘要"""
        return {
            "warmth": self.get("warmth"),
            "gentleness": self.get("gentleness"),
            "shyness": self.get("shyness"),
            "sensitivity": self.get("sensitivity"),
            "dependence": self.get("dependence"),
            "attachment_level": self.get_label("attachment_level", "未知"),
            "interaction_familiarity_level": self.get_label("interaction_familiarity_level", "未知"),
        }

    # ========== 新增：通用取值方法 ==========
    def value(self, key: str, default: Any = None) -> Any:
        """通用取值，返回原始类型（字符串、数值、字典等）"""
        return self._data.get(key, default)

    # ========== 字典式访问支持 ==========
    def __getitem__(self, key: str) -> Any:
        if key not in self._data:
            raise KeyError(f"PersonalityVector 不包含键 '{key}'")
        return self._data[key]

    def __contains__(self, key: str) -> bool:
        return key in self._data

    # ========== 属性访问支持 ==========
    def __getattr__(self, key: str) -> Any:
        # 保护内部属性
        if key.startswith('_'):
            raise AttributeError(key)
        try:
            return self._data[key]
        except KeyError:
            raise AttributeError(
                f"'PersonalityVector' 没有属性 '{key}'，"
                f"可用键: {list(self._data.keys())}"
            )

    # ========== 调试与展示 ==========
    def __repr__(self) -> str:
        return f"PersonalityVector({self._data})"

    def __str__(self) -> str:
        s = self.summary()
        return (
            f"温暖:{s['warmth']:.2f}, "
            f"害羞:{s['shyness']:.2f}, "
            f"依恋:{s['attachment_level']}, "
            f"交流熟悉度:{s['interaction_familiarity_level']}"
        )