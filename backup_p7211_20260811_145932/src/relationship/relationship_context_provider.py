"""
关系上下文提供器 (RelationshipContextProvider)
将关系状态和认知档案转换为 Prompt 可用的自然语言描述。
不暴露内部数值，空状态安全，并经过关系边界检查（任何违规均拒绝）。
"""
from typing import Optional
from src.relationship.relationship_state import RelationshipState
from src.relationship.relationship_cognitive_profile import RelationshipCognitiveProfile
from src.relationship.relationship_boundary import RelationshipBoundary, BoundaryLevel


class RelationshipContextProvider:
    def __init__(self):
        self.boundary = RelationshipBoundary()

    def get_context(
        self,
        state: Optional[RelationshipState] = None,
        profile: Optional[RelationshipCognitiveProfile] = None,
    ) -> str:
        """
        生成关系认知上下文字符串。
        若 state 为 None 或无有效关系数据，返回空字符串。
        若生成的上下文未通过边界检查（任何违规），返回空字符串。
        """
        if state is None:
            return ""
        if state.familiarity == 0.0 and state.trust == 0.0 and state.collaboration == 0.0:
            return ""

        lines = []
        lines.append("【关系认知参考】")
        lines.append("以下是你基于长期互动对当前用户形成的理解。")

        familiarity_text = self._describe_familiarity(state.familiarity)
        if familiarity_text:
            lines.append(f"熟悉程度：{familiarity_text}。")

        trust_text = self._describe_trust(state.trust)
        if trust_text:
            lines.append(f"信任程度：{trust_text}。")

        collaboration_text = self._describe_collaboration(state.collaboration)
        if collaboration_text:
            lines.append(f"协作情况：{collaboration_text}。")

        if state.communication_style:
            style_text = "、".join(state.communication_style)
            lines.append(f"沟通风格：{style_text}。")

        if profile and profile.confirmed_patterns:
            patterns_text = "；".join(profile.confirmed_patterns)
            lines.append(f"已观察到的互动模式：{patterns_text}。")

        stage_text = self._describe_stage(state.relationship_stage)
        if stage_text:
            lines.append(f"关系阶段：{stage_text}。")

        context = "\n".join(lines)

        # 安全审核：任何违规（WARNING 或 BLOCK）都应拒绝
        boundary_result = self.boundary.check_expression(context)
        if boundary_result.level != BoundaryLevel.SAFE:
            return ""

        return context

    def _describe_familiarity(self, value: float) -> str:
        if value >= 0.8:
            return "你们非常熟悉彼此的习惯和偏好"
        elif value >= 0.6:
            return "你们比较熟悉，有一定默契"
        elif value >= 0.4:
            return "你们开始逐渐了解对方"
        elif value > 0.0:
            return "你们还在初步认识阶段"
        return ""

    def _describe_trust(self, value: float) -> str:
        if value >= 0.8:
            return "建立在长期稳定互动上的深厚信任"
        elif value >= 0.6:
            return "较高的信任感"
        elif value >= 0.4:
            return "一定的信任基础"
        elif value > 0.0:
            return "信任正在建立中"
        return ""

    def _describe_collaboration(self, value: float) -> str:
        if value >= 0.8:
            return "多次深度合作，配合默契"
        elif value >= 0.6:
            return "有过不少有效协作经历"
        elif value >= 0.4:
            return "开始尝试协作"
        elif value > 0.0:
            return "协作尚浅"
        return ""

    def _describe_stage(self, stage: str) -> str:
        stage_map = {
            "initial": "初步接触",
            "developing": "正在发展",
            "stable": "稳定互动",
            "deep_collaboration": "深度协作",
        }
        return stage_map.get(stage, "")