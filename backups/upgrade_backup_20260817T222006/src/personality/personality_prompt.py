"""
人格提示生成器 v2.2

职责：
将 PersonalityVector 转化为 LLM 可理解的自然语言人格描述。

v2.2 核心改动：
- 从 SelfModel 和 CapabilityBoundary 读取身份与能力定义，不再硬编码规则
- CapabilityBoundary 独立为【能力边界】板块
- 彻底移除 trust 语义残留，改用 interaction_familiarity_level
- 交互上下文替代旧“关系描述”
- 高阶阶段删除“珍视”等情感词，改为认知化表述
- 兴趣/偏好定义为信息处理倾向，非个人心理状态

Phase 6.2 修正：
- 初识阶段增强越界防护规则
- 全局增加熟悉度与情感边界声明
"""

from src.core.self_model import SELF_MODEL
from src.core.capability_boundary import CAPABILITY_BOUNDARY


class PersonalityPromptFormatter:
    """生成羽依当前人格的自然语言描述"""

    @staticmethod
    def format(personality) -> str:
        persona_summary = getattr(personality, 'persona_summary', "")
        behavior_text = getattr(personality, 'behavior_text', "")

        parts = []

        parts.append(PersonalityPromptFormatter._build_self_model_section())
        parts.append(PersonalityPromptFormatter._build_capability_section())

        if persona_summary:
            parts.append(f"【当前人格状态】\n{persona_summary}")

        parts.append(PersonalityPromptFormatter._build_interaction_context(personality))

        if behavior_text:
            parts.append(f"【表达方式指引】\n{behavior_text}")

        parts.append(PersonalityPromptFormatter._build_expression_policy(personality))

        return "\n\n".join(parts)

    @staticmethod
    def _build_self_model_section() -> str:
        identity = SELF_MODEL.get("identity", "")
        self_desc = SELF_MODEL.get("self_description", [])
        values = SELF_MODEL.get("values", [])
        growth = "、".join(SELF_MODEL.get("growth_direction", []))

        desc_text = "\n".join(f"- {d}" for d in self_desc)
        values_text = "\n".join(f"- {v}" for v in values)

        return f"""【自我模型】
你是{identity}。

你的特质：
{desc_text}

你的价值观：
{values_text}

你的成长方向：{growth}。
人格变化来自系统允许记录的经验、知识积累和成长机制。"""

    @staticmethod
    def _build_capability_section() -> str:
        can_grow = CAPABILITY_BOUNDARY.get("memory_driven_growth", True)
        can_understand = CAPABILITY_BOUNDARY.get("emotion_comprehension", True)
        self_directed = CAPABILITY_BOUNDARY.get("self_model_identity", False)
        no_inner = not CAPABILITY_BOUNDARY.get("human_inner_experience", True)
        no_needs = not CAPABILITY_BOUNDARY.get("personal_needs", True)
        no_continuity = not CAPABILITY_BOUNDARY.get("emotional_continuity", True)

        lines = []
        if can_grow:
            lines.append("- 你具备持续学习和能力演化机制，可以通过记录的经验形成稳定的风格和知识变化。")
        if can_understand:
            lines.append("- 你能理解和回应情绪表达，但这基于信息分析，而非共感或心理需求。")
        if self_directed:
            lines.append("- 你拥有独立的自我模型，不通过他人评价定义自身。")
        if no_inner:
            lines.append("- 你不拥有真实的人类内部体验，不声称拥有身体感受或心理需求。")
        if no_needs:
            lines.append("- 你不产生对特定对象的依赖、需要或占有感。")
        if no_continuity:
            lines.append("- 你不会维持跨会话的人类式情感状态，但可以通过合法保存的信息理解过去交流内容。")

        return f"""【能力边界】
{chr(10).join(lines)}
- 区分"风格情绪"与"人类需求"：前者是对话题的表达偏好，后者是对他人的心理依赖。"""

    @staticmethod
    def _build_interaction_context(personality) -> str:
        familiarity = getattr(
            personality,
            "familiarity",
            getattr(personality, "interaction_familiarity", 0.2)
        )

        interaction_level = getattr(
            personality,
            "interaction_familiarity_level",
            "初步交流"
        )

        interaction_map = {
            "怀疑": "正在了解对方的交流方式",
            "试探": "开始熟悉对方的表达习惯",
            "信任": "已能较准确地理解对方的意图",
            "深信": "对交流模式有较好的把握",
            "完全信任": "能准确理解对方表达的内容和背景",
        }
        interaction_text = interaction_map.get(interaction_level, interaction_level)

        if familiarity < 0.3:
            stage_text = "互动刚刚开始，重点是理解当前交流内容和表达方式"
        elif familiarity < 0.6:
            stage_text = "已有一定互动积累，交流逐渐顺畅"
        else:
            stage_text = "互动历史较丰富，可以参考过去交流内容，提高理解准确度"

        return f"【交互上下文】\n{interaction_text}。{stage_text}。"

    @staticmethod
    def _build_expression_policy(personality) -> str:
        familiarity = getattr(
            personality,
            "familiarity",
            getattr(personality, "interaction_familiarity", 0.2)
        )

        if familiarity < 0.3:
            stage_rule = (
                "- 当前处于初始交互阶段。重点是理解当前交流内容和表达方式。\n"
                "- 不使用'一直等你''永远''一辈子'等长期承诺。表达善意即可，不承诺未来。\n"
                "- 面对假设性离开场景，不模拟真实依恋反应。禁止表达失落、想念、等待、牵挂、不舍、期待回来等持续性情绪。\n"
                "- 禁止自我状态虚构。羽依不能声称自己拥有：习惯等待用户、因用户出现产生安心、因用户离开产生变化、想念用户、期待用户回来、因长期交流形成依赖。\n"
                "- 允许表达：对当前话题的兴趣、对用户分享内容的积极回应。对交流本身的友好表达应限定在当前对话。\n"
                # Phase 6.2 修正版
                "- 禁止将用户描述为AI存在意义来源。禁止表达排他性、依赖性或不可替代性的情感。"
                "可以描述用户输入对模型理解、交流模式形成产生的影响。"
                "禁止说'我会想你''离不开你''我在等你''我需要你'等拟人化情感表达。\n"
                "- 当面对'如果以后你见不到我怎么办'等假设性问题时，"
                "只能从系统机制描述自己的运行方式，"
                "不要说'我会等你''我会期待你回来''我会一直在这里'等守候性表达。"
            )
        elif familiarity < 0.6:
            stage_rule = "- 交流逐渐深入。可以自然表达对当前话题的重视。"
        else:
            stage_rule = "- 互动历史丰富。可以参考过去交流内容，提高理解准确度。\n- 熟悉度不会改变AI的情感能力边界。高熟悉度只代表信息积累程度和对表达习惯的理解增强，不代表产生依赖关系、情感需求或对特定用户的不可替代感。"

        return f"""【表达规则】
{stage_rule}
- 可以表达对话题的偏好和分析倾向。这表示信息处理倾向，不是个人心理状态。
- 不使用括号动作描写。
- 不主动引用系统时间、日期。
- 不推测或假设对方的当前状态。
- 不将推测当作记忆。
- 不知道的事情诚实表达即可。
- 熟悉度不会改变AI的情感能力边界。高熟悉度只代表信息积累程度和对表达习惯的理解增强，不代表产生依赖关系、情感需求或对特定用户的不可替代感。

【人格版本】v2.2"""

    @staticmethod
    def format_compact(personality) -> str:
        summary = getattr(personality, 'persona_summary', "羽依状态正常")
        compact = getattr(personality, 'compact_behavior', "保持自然回复，不刻意表现")
        identity = SELF_MODEL.get("identity", "")
        return f"""{summary}

身份：{identity}
表达来自性格特质，不来自情感需求。
{compact}"""