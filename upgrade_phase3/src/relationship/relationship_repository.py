"""
关系数据仓库 (RelationshipRepository) — Phase 10.2.1 重构版

职责：
- Phase 7 旧接口：管理 RelationshipInfluenceProfile 的持久化
- Phase 10 新接口：管理 RelationshipState 和 RelationshipCognitiveProfile
- 支持多用户隔离（通过 user_id 子目录）
"""

import json
import os
from typing import Optional, Tuple
from datetime import datetime

from src.relationship.relationship_influence_profile import RelationshipInfluenceProfile
from src.personality.personality_influence import PersonalityInfluence, InfluenceType

from src.relationship.relationship_state import RelationshipState
from src.relationship.relationship_cognitive_profile import RelationshipCognitiveProfile


class RelationshipRepository:
    """管理关系数据的持久化存储（向后兼容 Phase 7）"""

    def __init__(self, data_dir: str = "data", user_id: str = "default"):
        self.data_dir = data_dir
        self.user_id = user_id
        self.user_dir = os.path.join(self.data_dir, "users", self.user_id)
        os.makedirs(self.user_dir, exist_ok=True)

    # ============================================================
    # Phase 7 旧接口（保持签名不变）
    # ============================================================
    def _get_influence_filepath(self) -> str:
        return os.path.join(self.user_dir, "relationship_influence_profile.json")

    def load(self) -> Optional[RelationshipInfluenceProfile]:
        """加载关系影响画像（Phase 7）"""
        filepath = self._get_influence_filepath()
        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            profile = RelationshipInfluenceProfile(
                user_id=data.get("user_id", self.user_id),
                relationship_start=data.get("relationship_start", datetime.now().isoformat()),
            )

            for inf_data in data.get("influences", []):
                raw_type = inf_data.get("influence_type", "positive_growth")
                try:
                    influence_type = InfluenceType(raw_type)
                except ValueError:
                    influence_type = InfluenceType.POSITIVE_GROWTH

                influence = PersonalityInfluence(
                    influence_id=inf_data.get("influence_id", ""),
                    timestamp=inf_data.get("timestamp", ""),
                    source_event_id=inf_data.get("source_event_id", ""),
                    source_event_description=inf_data.get("source_event_description", ""),
                    affected_dimension=inf_data.get("affected_dimension", ""),
                    before_value=inf_data.get("before_value", 0.0),
                    after_value=inf_data.get("after_value", 0.0),
                    delta=inf_data.get("delta", 0.0),
                    influence_type=influence_type,
                    impact_weight=inf_data.get("impact_weight", 0.0),
                    confidence=inf_data.get("confidence", 0.5),
                    evidence=inf_data.get("evidence", []),
                )
                profile.add_influence(influence)

            return profile
        except Exception as e:
            print(f"⚠️ 加载关系影响画像失败: {e}")
            return None

    def save(self, profile: RelationshipInfluenceProfile):
        """保存关系影响画像（Phase 7）"""
        filepath = self._get_influence_filepath()
        try:
            data = profile.to_dict()
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ 保存关系影响画像失败: {e}")

    # ============================================================
    # Phase 10.2 新增接口
    # ============================================================
    def _get_state_path(self) -> str:
        return os.path.join(self.user_dir, "relationship_state.json")

    def _get_cognitive_profile_path(self) -> str:
        return os.path.join(self.user_dir, "relationship_cognitive_profile.json")

    # ---------- State ----------
    def load_state(self) -> RelationshipState:
        filepath = self._get_state_path()
        if not os.path.exists(filepath):
            return RelationshipState()
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return RelationshipState.from_dict(data)

    def save_state(self, state: RelationshipState):
        filepath = self._get_state_path()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)

    # ---------- Cognitive Profile ----------
    def load_cognitive_profile(self) -> RelationshipCognitiveProfile:
        filepath = self._get_cognitive_profile_path()
        if not os.path.exists(filepath):
            return RelationshipCognitiveProfile()
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return RelationshipCognitiveProfile.from_dict(data)

    def save_cognitive_profile(self, profile: RelationshipCognitiveProfile):
        filepath = self._get_cognitive_profile_path()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)

    # ---------- 便捷方法 ----------
    def load_all_v10(self) -> Tuple[RelationshipState, RelationshipCognitiveProfile]:
        return self.load_state(), self.load_cognitive_profile()

    def save_all_v10(self, state: RelationshipState, profile: RelationshipCognitiveProfile):
        self.save_state(state)
        self.save_cognitive_profile(profile)