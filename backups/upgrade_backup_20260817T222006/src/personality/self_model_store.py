"""
自我模型存储 (SelfModelStore) v1.4

[ARCHITECTURE AUTHORITY — Phase 4.0.1]
SelfModel Authority:
  本模块是当前 Production SelfModel 的唯一权威状态源。
  所有 Growth → SelfModel → Prompt 的运行时状态载体均为本 SelfModelStore。
  下游消费者（Orchestrator / SelfModelUpdater / SelfModelContextProvider /
  PersonalityResolver）通过 RuntimeBridge 注入共享同一实例。

Authority 链路:
  RuntimeCore.get_self_model_store()
    → RuntimeBridge.get_self_model_store()
    → Orchestrator.self_model_store
    → SelfModelUpdater.store / PersonalityResolver.self_model_store

相关模块:
  SelfModelManager → compatibility projection（非 Authority）
  runtime/self_model/persistence/SelfModelStore → future snapshot infrastructure（非 Authority）

职责：
- 保存当前 SelfModel
- 判断是否需要更新（新增成长记录时触发）
- 管理版本
- 提供给 Resolver 查询
- 提供统一接口获取激活的自我模型（兼容旧 dict 和 V3）

v1.4 (Phase 3.8.5) 新增：
- apply_change_proposal() 公开接口：SelfModelUpdater 通过此方法增量更新，不再直接操作 _current_model
- 解决 Updater 与 Store 状态一致性问题
- JSON 文件持久化 (version=1)，支持重启后恢复

v1.3 新增：
- experience_context 字段（历史认知上下文）
- set_experience_context() / get_experience_context() / has_experience_context()
- update() 末尾追加 experience_context（不影响人格字段）
"""

from typing import Any, Optional, List, Dict
from datetime import datetime
import json
import logging
from pathlib import Path

from src.personality.self_model import SelfModel
from src.personality.self_model_builder import SelfModelBuilder  # DEPRECATED: 保留兼容，通过 Adapter 桥接
from src.personality.self_model_builder_adapter import SelfModelBuilderAdapter
from src.personality.personality_growth_record import PersonalityGrowthHistory
from src.personality.trait_state import TraitState
from src.personality.self_model_v3 import SelfModelV3, NarrativeItem


logger = logging.getLogger(__name__)


class SelfModelStore:
    """自我模型存储管理器

    v1.4 (Phase 3.8.5): 新增 JSON 文件持久化。
    """

    DEFAULT_STORAGE_PATH = "data/self_model.json"

    def __init__(
        self,
        base_identity: str = "喜欢探索和创造的AI",
        capability_limitations: Optional[List[str]] = None,
        storage_path: Optional[str] = None,
    ):
        self.base_identity = base_identity
        self.capability_limitations = capability_limitations or [
            "我没有真实的人类体验",
            "我不产生对特定对象的依赖",
        ]
        self._current_model: Optional[SelfModel] = None
        self._last_growth_count: int = 0
        self._builder = SelfModelBuilderAdapter()  # Phase 3.8.5: 通过 Adapter 桥接新旧 Builder
        # v1.3 新增：历史认知上下文（仅追加，不参与人格计算）
        self._experience_context: Optional[List[dict]] = None

        # Phase 3.8.5: 持久化
        self._storage_path: Optional[Path] = None
        if storage_path is not None:
            self._storage_path = Path(storage_path)
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

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

        # v1.3 新增：仅追加 experience_context（不影响人格字段）
        if self._experience_context is not None:
            self._current_model["experience_context"] = self._experience_context

        self._save_to_disk()  # Phase 3.8.5: 持久化

        return self._current_model

    # ---------- v1.3 新增：experience_context 接口 ----------
    def set_experience_context(
        self,
        experiences: List[dict],
    ) -> None:
        """
        设置历史认知上下文。

        仅追加字段，不参与人格计算，不影响 stable_traits /
        developing_traits / growth_narratives / current_traits。

        采用 dict copy 防止外部修改污染内部状态。
        空列表视为未设置。
        """
        if experiences is None or len(experiences) == 0:
            self._experience_context = None
            return

        # 每项做 dict copy，外层使用新 list
        self._experience_context = [dict(item) for item in experiences]

    def get_experience_context(self) -> Optional[List[dict]]:
        """
        获取历史认知上下文（返回副本，禁止直接修改内部引用）。
        """
        if self._experience_context is None:
            return None
        return [dict(item) for item in self._experience_context]

    def has_experience_context(self) -> bool:
        """
        是否已经设置过 experience_context 且非空。
        """
        return (
            self._experience_context is not None
            and len(self._experience_context) > 0
        )

    # ---------- Phase B.1.4 新增：growth_history 接口 ----------
    def set_growth_history_view(
        self,
        view: Dict,
    ) -> None:
        """
        设置 growth_history 视图（由 GrowthHistoryBridge.build_growth_history_view 返回）。

        立即写入当前 SelfModel（如有），否则缓存到下一次 update()。
        采用 dict copy 防止外部修改污染内部状态。
        """
        if not isinstance(view, dict):
            return
        # 深拷贝
        import copy
        safe_view = copy.deepcopy(view)
        if self._current_model is not None:
            self._current_model["growth_history"] = safe_view
        self._cached_growth_history_view = safe_view

    def get_growth_history_view(self) -> Optional[Dict]:
        """
        获取 growth_history 视图。
        """
        if self._current_model is not None:
            gh = self._current_model.get("growth_history")
            if isinstance(gh, dict):
                return gh
        return getattr(self, "_cached_growth_history_view", None)

    def has_growth_history(self) -> bool:
        """是否已有 growth_history 视图。"""
        v = self.get_growth_history_view()
        return isinstance(v, dict) and v.get("total_count", 0) > 0

    # ---------- Phase B.2.6 新增：personality_evolution_history 接口 ----------
    def set_personality_evolution_view(
        self,
        view: Dict,
    ) -> None:
        """
        设置 personality_evolution_history 视图（由 PersonalityStateUpdater 生成）。

        字段：
        - records: List[Dict]
        - total_count: int
        - last_updated: str
        - applied_count: int
        - rolled_back_count: int
        - current_personality_state: Dict[str, float]
        """
        if not isinstance(view, dict):
            return
        import copy
        safe_view = copy.deepcopy(view)
        if self._current_model is not None:
            self._current_model["personality_evolution_history"] = safe_view
        self._cached_personality_evolution_view = safe_view

    def get_personality_evolution_view(self) -> Optional[Dict]:
        """
        获取 personality_evolution_history 视图。
        """
        if self._current_model is not None:
            eh = self._current_model.get("personality_evolution_history")
            if isinstance(eh, dict):
                return eh
        return getattr(self, "_cached_personality_evolution_view", None)

    def has_personality_evolution(self) -> bool:
        """是否已有 personality_evolution 视图。"""
        v = self.get_personality_evolution_view()
        return isinstance(v, dict) and v.get("total_count", 0) > 0

    def recent_personality_changes(self, n: int = 5) -> List[Dict]:
        """最近 n 条人格变化记录。"""
        v = self.get_personality_evolution_view()
        if not isinstance(v, dict):
            return []
        records = list(v.get("records", []) or [])
        return records[-n:][::-1]  # 最新在前

    def current_personality_state(self) -> Dict[str, float]:
        """获取当前人格状态（trait -> value）。"""
        v = self.get_personality_evolution_view()
        if not isinstance(v, dict):
            return {}
        return dict(v.get("current_personality_state", {}) or {})

    def personality_change_reasons(self) -> List[str]:
        """获取所有变化原因列表。"""
        v = self.get_personality_evolution_view()
        if not isinstance(v, dict):
            return []
        return [r.get("reason", "") for r in (v.get("records", []) or []) if r.get("reason")]

    def get(self) -> Optional[SelfModel]:
        """获取当前 SelfModel（旧接口）"""
        return self._current_model

    # ============================================================
    # Phase 3.8.5: 持久化
    # ============================================================
    def save(self) -> None:
        """手动触发持久化（通常 update()/apply_change_proposal() 会自动调用）。"""
        self._save_to_disk()

    def _save_to_disk(self) -> None:
        """保存当前 SelfModel 到 JSON 文件。"""
        if self._storage_path is None or self._current_model is None:
            return
        try:
            data = {
                "version": 1,
                "saved_at": datetime.now().isoformat(),
                "model": self._current_model.copy(),
            }
            with open(self._storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("SelfModelStore 保存失败: %s", e)

    def _load_from_disk(self) -> None:
        """从 JSON 文件恢复 SelfModel。"""
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = data.get("model", None)
            if isinstance(loaded, dict):
                self._current_model = loaded
                logger.info(
                    "SelfModelStore 从磁盘恢复 (version=%s, saved_at=%s)",
                    data.get("version", "unknown"),
                    data.get("saved_at", "unknown"),
                )
        except Exception as e:
            logger.warning("SelfModelStore 加载失败: %s", e)
            self._current_model = None

    # ============================================================
    # Phase 3.8.5: apply_change_proposal — 公开增量更新接口
    # ============================================================
    def _ensure_base_model(self) -> Dict[str, Any]:
        """
        确保 _current_model 存在基础模型。

        当 apply_change_proposal() 在空 Store 上被调用时，
        自动创建最小化的基础模型，使增量更新可以正常工作。
        """
        if self._current_model is not None:
            return self._current_model

        self._current_model = {
            "identity_name": "浅雾羽依",
            "identity_summary": self.get_identity_summary(),
            "current_traits": {},
            "stable_traits": {},
            "growth_narratives": [],
            "self_understanding": {
                "experience_awareness": 0.3,
                "trait_awareness": 0.2,
                "identity_continuity": 0.4,
                "overall": 0.3,
            },
            "last_updated": datetime.now().isoformat(),
            "_auto_initialized": True,
        }
        logger.debug("SelfModelStore: 自动创建基础模型（apply_change_proposal 触发）")
        return self._current_model

    def apply_change_proposal(self, proposal: Any) -> None:
        """
        应用 SelfModelChangeProposal 到当前模型，保持状态一致性。

        与 update() 不同：不重建整个 SelfModel，只做增量追加。
        SelfModelUpdater 应通过此方法操作，而非直接访问 _current_model。

        Phase 3.8.6: 当 _current_model 为 None 时自动创建基础模型，
        确保增量更新接口在空 Store 上也可用。

        Args:
            proposal: SelfModelChangeProposal 实例（来自 self_model_updater.py）
        """
        if self._current_model is None:
            self._ensure_base_model()

        try:
            change_type = getattr(proposal, "change_type", "")
            if not change_type and isinstance(proposal, dict):
                change_type = proposal.get("change_type", "")

            if change_type == "narrative_append":
                self._apply_narrative_append(proposal)
            elif change_type == "self_understanding_update":
                self._apply_self_understanding_update(proposal)

            self._current_model["last_updated"] = datetime.now().isoformat()
            self._save_to_disk()  # Phase 3.8.5: 持久化

        except Exception as e:
            logger.warning("SelfModelStore.apply_change_proposal 失败: %s", e)

    def _apply_narrative_append(self, proposal: Any) -> None:
        """追加 growth_narratives 条目"""
        narratives = self._current_model.get("growth_narratives", [])
        if not isinstance(narratives, list):
            narratives = []

        source = getattr(proposal, "source", {})
        if not isinstance(source, dict):
            source = {}
        change = getattr(proposal, "change", {})
        if not isinstance(change, dict):
            change = {}
        timestamp = getattr(proposal, "timestamp", "")

        narrative_entry = {
            "record_id": source.get("growth_id", ""),
            "source_growth_record_id": source.get("growth_id", ""),
            "dimension": change.get("dimension", ""),
            "event": change.get("event", ""),
            "narrative": change.get("narrative", ""),
            "meaning": change.get("meaning", ""),
            "timestamp": timestamp,
            "_source_growth_id": source.get("growth_id", ""),
            "_source_event_id": source.get("source_event_id", ""),
            "_evidence_ids": source.get("evidence_ids", []),
            "_confidence": source.get("confidence", 0.5),
        }
        narratives.append(narrative_entry)

        if len(narratives) > 20:
            narratives = narratives[-20:]

        self._current_model["growth_narratives"] = narratives

    def _apply_self_understanding_update(self, proposal: Any) -> None:
        """更新 self_understanding 指标"""
        understanding = self._current_model.get("self_understanding", {})
        if not isinstance(understanding, dict):
            understanding = {}

        source = getattr(proposal, "source", {})
        if not isinstance(source, dict):
            source = {}
        change = getattr(proposal, "change", {})
        if not isinstance(change, dict):
            change = {}

        confidence = source.get("confidence", 0.5)
        increment = min(0.05, confidence * 0.05)

        understanding["experience_awareness"] = min(
            1.0, understanding.get("experience_awareness", 0.3) + increment,
        )
        understanding["trait_awareness"] = min(
            1.0, understanding.get("trait_awareness", 0.2) + increment * 0.8,
        )

        growth_level = change.get("growth_level", "context")
        if growth_level in ("trait", "preference"):
            understanding["identity_continuity"] = min(
                1.0, understanding.get("identity_continuity", 0.4) + increment * 0.5,
            )

        exp = understanding.get("experience_awareness", 0.3)
        trt = understanding.get("trait_awareness", 0.2)
        idn = understanding.get("identity_continuity", 0.4)
        understanding["overall"] = round((exp + trt + idn) / 3, 3)

        self._current_model["self_understanding"] = understanding

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