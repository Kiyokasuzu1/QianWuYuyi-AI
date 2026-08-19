"""
自我模型存储 (SelfModelStore) v1.2

职责：
- 保存当前 SelfModel
- 判断是否需要更新（新增成长记录时触发）
- 管理版本
- 提供给 Resolver 查询
- 提供统一接口获取激活的自我模型（兼容旧 dict 和 V3）

v1.2 新增：
- get_active_self_model() 统一返回 SelfModelV3，支持旧 dict 转换
"""

from typing import Optional, List, Dict
from datetime import datetime

from src.personality.self_model import SelfModel
from src.personality.self_model_builder import SelfModelBuilder
from src.personality.personality_growth_record import PersonalityGrowthHistory
from src.personality.trait_state import TraitState
from src.personality.self_model_v3 import SelfModelV3, NarrativeItem


class SelfModelStore:
    """自我模型存储管理器"""

    def __init__(
        self,
        base_identity: str = "喜欢探索和创造的AI",
        capability_limitations: Optional[List[str]] = None,
    ):
        self.base_identity = base_identity
        self.capability_limitations = capability_limitations or [
            "我没有真实的人类体验",
            "我不产生对特定对象的依赖",
        ]
        self._current_model: Optional[SelfModel] = None
        self._last_growth_count: int = 0
        self._builder = SelfModelBuilder()

    # ---------- 原有方法保持不变 ----------
    def should_update(
        self,
        history: PersonalityGrowthHistory,
    ) -> bool:
        """
        判断是否需要更新 SelfModel。
        条件：
        1. 首次构建（_current_model 为空）
        2. 成长记录数量增加
        """
        if self._current_model is None:
            return True

        return history.count() > self._last_growth_count

    def update(
        self,
        history: PersonalityGrowthHistory,
        trait_states: Dict[str, TraitState],
    ) -> SelfModel:
        """
        更新 SelfModel 并返回最新版本。
        """
        self._current_model = self._builder.build(
            history=history,
            trait_states=trait_states,
            base_identity=self.base_identity,
            capability_limitations=self.capability_limitations,
        )
        self._last_growth_count = history.count()
        return self._current_model

    def get(self) -> Optional[SelfModel]:
        """获取当前 SelfModel（旧接口）"""
        return self._current_model

    def get_identity_summary(self) -> str:
        """获取身份摘要"""
        if self._current_model:
            return self._current_model.get("identity_summary", "")
        return f"我是一个{self.base_identity}。"

    # ---------- 新增：统一激活模型接口 ----------
    def get_active_self_model(self) -> Optional[SelfModelV3]:
        """
        返回当前激活的自我模型，统一为 SelfModelV3 实例。
        内部处理旧 dict 的转换，不修改原始数据。
        """
        # 安全获取当前模型，避免空 dict 被误判为 None
        model = None
        if hasattr(self, '_current_model'):
            model = self._current_model
        if model is None and hasattr(self, 'current_model'):
            model = self.current_model
        if model is None:
            return None

        # 已经是新模型，直接返回
        if isinstance(model, SelfModelV3):
            return model

        # 旧版 dict → SelfModelV3 转换
        if isinstance(model, dict):
            return self._dict_to_v3(model)

        return None

    def _dict_to_v3(self, data: dict) -> SelfModelV3:
        """将旧版字典转换为 SelfModelV3（不污染信念）"""
        identity = data.get("identity_name", "浅雾羽依")
        traits = data.get("current_traits", {})
        # 旧版 stable_traits 不等于 beliefs，不转换，避免污染
        beliefs = []
        narratives = []
        for gn in data.get("growth_narratives", []):
            text = gn.get("narrative", "") or gn.get("meaning", "")
            if text:
                narratives.append(
                    NarrativeItem(text=text, source_ids=[gn.get("record_id", "")])
                )
        return SelfModelV3(
            identity=identity,
            traits=traits,
            beliefs=beliefs,
            narrative_items=narratives[-3:]  # 控制数量
        )