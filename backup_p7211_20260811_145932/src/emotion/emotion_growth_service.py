"""
情绪成长服务 (EmotionGrowthService)
负责后台的情绪模式分析、信念提取和 SelfModel 更新，并自动持久化。
Phase 9.7 v2 新增：注入 SelfModelStore，分析后自动保存模型。
Phase 6.2: 增加可选 self_model_adapter 参数；注入时通过 Adapter 写入（Authority Closure）。
"""
from typing import Any, Optional
from src.emotion.emotion_manager import EmotionManager
from src.emotion.emotion_pattern_analyzer import EmotionPatternAnalyzer
from src.emotion.emotion_belief_extractor import EmotionBeliefExtractor
from src.emotion.emotion_self_model_bridge import EmotionSelfModelBridge
from src.personality.self_model_v3 import SelfModelV3
from src.personality.self_model_store import SelfModelStore


class EmotionGrowthService:
    def __init__(
        self,
        manager: EmotionManager,
        self_model_store: SelfModelStore,
        analysis_interval: int = 10,  # 默认每10次对话分析一次，未来可根据负载调整
        self_model_adapter: Optional[Any] = None,
    ):
        self.manager = manager
        self.self_model_store = self_model_store
        self.analysis_interval = analysis_interval
        self.pattern_analyzer = EmotionPatternAnalyzer()
        self.belief_extractor = EmotionBeliefExtractor()
        self.bridge = EmotionSelfModelBridge()
        # Phase 6.2: 可选 SelfModelAdapter 注入；为 None 时走 legacy 路径
        self._self_model_adapter = self_model_adapter

    def set_self_model_adapter(self, adapter: Any) -> None:
        """Phase 6.2: 运行时注入 Adapter（替代 self_model_store 直接写入）"""
        self._self_model_adapter = adapter

    def should_analyze(self) -> bool:
        """判断是否应该触发情绪模式分析"""
        return self.manager.analysis_counter >= self.analysis_interval

    def analyze_and_merge(self) -> None:
        """
        执行完整的情绪成长流程：
        1. 获取最近轨迹
        2. 模式分析
        3. 信念提取
        4. 合并到 SelfModel
        5. 保存模型
        注意：只要分析执行过，无论是否产生信念，都重置计数器。
        Phase 6.2: 若 self_model_adapter 已注入，信念写入走 Adapter（唯一写入口）。
        """
        traces = self.manager.get_recent_traces(limit=200)
        if not traces:
            self.manager.reset_analysis_counter()
            return

        patterns = self.pattern_analyzer.analyze(traces)
        beliefs = self.belief_extractor.extract(patterns)

        if beliefs:
            # 获取当前激活的自我模型
            model = self.self_model_store.get_active_self_model()
            if model is None:
                model = SelfModelV3()

            # 合并信念
            self.bridge.merge(model, beliefs)

            # Phase 6.2: Authority Closure — 优先走 Adapter
            if self._self_model_adapter is not None:
                try:
                    from src.personality.self_belief import SelfBelief
                    belief_objs: list = []
                    for b in beliefs:
                        try:
                            content = getattr(b, "text", None) or str(b)
                            belief_objs.append(SelfBelief(
                                domain="preference",
                                content=f"emotion_growth:{content}",
                                confidence=0.5,
                                sources=["emotion_growth_service"],
                            ))
                        except Exception:
                            continue
                    self._self_model_adapter.apply_external_change(
                        change_type="emotion",
                        reason="emotion_pattern_analysis",
                        source="emotion_growth_service",
                        confidence=0.5,
                        beliefs_to_add=belief_objs,
                    )
                except Exception:
                    # 降级：legacy save
                    self.self_model_store.save(model)
            else:
                # 兼容性：保持原行为
                self.self_model_store.save(model)

        # 无论是否产生信念，分析完成后都重置计数器
        self.manager.reset_analysis_counter()