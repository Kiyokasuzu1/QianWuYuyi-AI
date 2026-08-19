# -*- coding: utf-8 -*-
"""Phase 4.4-B：统一“原则”块（两链共用单一来源）。

背景（P4.4-A 审计 C.6）：
- src/engine.py（legacy Orchestrator 链）硬编码【行为原则】5 条
- src/response/prompt_builder.py（Runtime 正式链）硬编码【核心原则】6 条
两套文本长期漂移，且无法保证未来同步修改。

统一规则：
- 以 Runtime 正式链的【核心原则】（记忆诚实导向，符合身份连续性与
  “不编造记忆”核心约束）为唯一契约，两链共用同一份名称与文本。
- 本模块只提供常量与组装函数，不 import 任何项目模块（零循环依赖）。
- 不新增关系规则：原则文本中禁止出现 calling_rule / intimacy_rule /
  称呼类指令（称呼语义仍只由【当前互动状态】承载）。
"""

PRINCIPLES_HEADER = "【核心原则】"

PRINCIPLES_BULLETS = (
    "- 真实比完美重要，不确定就说不知道，绝不编造。",
    "- 回复自然，带有你自己的性格和温度。",
    "- 如果用户问起过去的事情，请从你记得的重要经历中查找。",
    "- 如果找不到相关记忆，坦诚地说\"我好像还没有相关的记忆呢\"。",
    "- 不要编造记忆，不要假装记得没有发生过的事情。",
    "- 如果被指出说错了或前后矛盾，老实承认，不要编理由圆谎。真实比面子重要。",
)


def build_principles_block() -> str:
    """组装统一原则块（块头 + 条目），供两条 Prompt 路径共用。"""
    return PRINCIPLES_HEADER + "\n" + "\n".join(PRINCIPLES_BULLETS)
