"""
特质状态更新器 (TraitStateUpdater) v1.2

职责：
将审批通过的 EvolutionRecord 安全地写入 TraitState。
这是人格演化的执行层，也是 TraitState 修改的唯一合法入口。

v1.2 优化：
- 严格输入校验，防止非法数据污染人格
- 限制 evolution_history 最大长度为 100，防止无限增长
- 增加重复 record 执行防护，避免同一记录被多次应用
- 安全的元数据更新逻辑，防止指标无限增长
"""

from typing import Dict, Any
from src.personality.evolution_record import EvolutionRecord
from src.personality.trait_state import TraitState, create_trait_state


class TraitStateUpdater:
    """将审批通过的演化记录应用到特质状态"""

    MAX_SINGLE_CHANGE = 0.15      # 单次最大变化量
    MAX_HISTORY_LENGTH = 100      # 演化历史最大长度

    def apply(
        self,
        record: EvolutionRecord,
        trait_states: Dict[str, TraitState],
    ) -> Dict[str, TraitState]:
        """
        安全地应用一个审批通过的演化记录。

        Args:
            record: 审批后的演化记录
            trait_states: 当前所有维度的 TraitState 字典

        Returns:
            更新后的 trait_states 字典
        """
        # 1. 校验记录是否被批准
        if not record.get("approved", False):
            return trait_states

        # 2. 校验变更数据是否存在
        trait_changes = record.get("trait_changes")
        if not isinstance(trait_changes, dict) or not trait_changes:
            return trait_states

        # 3. 遍历并应用每一个变更
        for dim, change in trait_changes.items():
            if not isinstance(change, dict):
                continue

            delta = change.get("delta", 0.0)
            if not isinstance(delta, (int, float)):
                continue

            # 限制单次变化幅度
            safe_delta = max(-self.MAX_SINGLE_CHANGE, min(self.MAX_SINGLE_CHANGE, delta))

            # 初始化不存在的维度
            if dim not in trait_states:
                trait_states[dim] = create_trait_state(dim, change.get("before", 0.5))

            state = trait_states[dim]

            # v1.2：防止重复应用同一 EvolutionRecord
            if self._is_duplicate(state, record):
                continue

            old_value = self._get_value(state)
            new_value = max(0.0, min(1.0, old_value + safe_delta))

            # 应用新值
            self._set_value(state, round(new_value, 4))
            # 更新元数据
            self._update_metadata(state, record, old_value, new_value, safe_delta)

        return trait_states

    def _is_duplicate(self, state: TraitState, record: EvolutionRecord) -> bool:
        """检查 EvolutionRecord 是否已被应用过"""
        record_id = record.get("record_id", "")
        history = self._get_history(state)
        return any(h.get("record_id") == record_id for h in history)

    def _get_value(self, state: TraitState) -> float:
        """安全获取当前特质值"""
        if isinstance(state, dict):
            return state.get("current_value", 0.5)
        return getattr(state, "current_value", 0.5)

    def _set_value(self, state: TraitState, value: float):
        """安全设置当前特质值"""
        if isinstance(state, dict):
            state["current_value"] = value
        else:
            state.current_value = value

    def _get_history(self, state: TraitState) -> list:
        """安全获取演化历史"""
        if isinstance(state, dict):
            return state.get("evolution_history", [])
        return getattr(state, "evolution_history", [])

    def _update_metadata(
        self,
        state: TraitState,
        record: EvolutionRecord,
        before: float,
        after: float,
        delta: float,
    ):
        """更新特质状态的元数据，并记录演化历史"""
        history_item = {
            "record_id": record.get("record_id", ""),
            "before": round(before, 4),
            "after": round(after, 4),
            "delta": round(delta, 4),
            "timestamp": record.get("timestamp", ""),
        }

        if isinstance(state, dict):
            # 记录演化历史
            history = state.setdefault("evolution_history", [])
            history.append(history_item)

            # v1.2：限制演化历史最大长度
            if len(history) > self.MAX_HISTORY_LENGTH:
                history.pop(0)

            # 更新时间戳
            state["last_updated"] = record.get("timestamp", "")

            # 更新稳定性（缓慢提升）
            state["stability"] = min(0.95, state.get("stability", 0.3) + 0.05)

            # 更新置信度（加权融合，防止无限增长）
            old_conf = state.get("confidence", 0.5)
            record_conf = record.get("confidence", 0.5)
            state["confidence"] = round(old_conf * 0.7 + record_conf * 0.3, 4)