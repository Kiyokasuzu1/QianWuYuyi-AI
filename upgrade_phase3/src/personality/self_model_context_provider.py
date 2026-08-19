"""
SelfModelContextProvider v8.4.2
从 SelfModelStore 获取当前自我模型，生成安全的 Prompt 参考片段。
不输出内部数值、不负责安全过滤。
"""

from typing import Optional
from src.personality.self_model_store import SelfModelStore
from src.personality.self_model_v3 import SelfModelV3


class SelfModelContextProvider:
    def __init__(self, store: SelfModelStore):
        self.store = store

    def get_context(self) -> str:
        """
        返回用于注入 Prompt 的自我认知上下文。
        若当前无模型，返回空字符串。
        """
        model: Optional[SelfModelV3] = self.store.get_active_self_model()
        if model is None:
            return ""

        lines = []

        # 标题强调"参考"，而非强制性设定
        lines.append("【自我认知参考】")
        lines.append("以下是基于你过去经历形成的自我理解，它会影响你的表达方式，但不是绝对事实。")

        # 身份
        lines.append(f"身份：{model.identity}")

        # 性格倾向（仅列出维度名称，不暴露数值）
        if model.traits:
            trait_names = list(model.traits.keys())
            lines.append(f"当前活跃的性格维度：{', '.join(trait_names)}")

        # 信念
        if model.beliefs:
            belief_text = "；".join(model.beliefs)
            lines.append(f"你目前形成的信念：{belief_text}")

        # 成长叙事（最多展示2条，每条截断到80字，防止 Prompt 过长）
        if model.narrative_items:
            lines.append("近期重要的自我成长认知：")
            for item in model.narrative_items[-2:]:
                text = item.text
                if len(text) > 80:
                    text = text[:80] + "…"
                lines.append(f"- {text}")

        return "\n".join(lines)