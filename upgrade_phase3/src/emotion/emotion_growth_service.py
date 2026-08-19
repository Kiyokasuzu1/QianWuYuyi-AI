"""
情绪成长服务 (EmotionGrowthService)
负责后台的情绪模式分析、信念提取和 SelfModel 更新，并自动持久化。
Phase 9.7 v2 新增：注入 SelfModelStore，分析后自动保存模型。
"""
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
    ):
        self.manager = manager
        self.self_model_store = self_model_store
        self.analysis_interval = analysis_interval
        self.pattern_analyzer = EmotionPatternAnalyzer()
        self.belief_extractor = EmotionBeliefExtractor()
        self.bridge = EmotionSelfModelBridge()

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

            # 保存模型
            self.self_model_store.save(model)

        # 无论是否产生信念，分析完成后都重置计数器
        self.manager.reset_analysis_counter()