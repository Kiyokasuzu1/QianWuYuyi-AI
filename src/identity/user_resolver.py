"""
用户解析器 (UserResolver)
从消息来源识别用户，返回 UserContext。
"""
from typing import Any, Dict, Optional

from src.identity.user_context import UserContext
from src.config import get_memory_config
from src.identity.origin_identity import CREATOR_USER_ID


# Phase 4.0.4-Pre: 创造者（清清）的固定身份元数据。
# Phase 4.1.3: calling_rule / intimacy_rule 已降级为 FALLBACK ONLY。
# 正常路径由 CommunicationStyle → CommunicationRenderer → Prompt 驱动。
# 仅当新链不可用时，才回退到以下 legacy 规则。
# V1.1.1 Context Continuity: 新增 forbidden_names —— 作为「身份事实」
# 常驻注入 Prompt（事实参考，非指令），与 legacy 规则互不影响。
_CREATOR_META: Dict[str, Any] = {
    # 用户 ID 匹配 CREATOR_USER_ID（366648462）时启用以下元数据
    "display_name": "清夏铃",                # 真实全名（用于关系说明，不作为直接称呼）
    "preferred_name": "清清",                # 羽依对对方的正式称呼
    # V1.1.1: 对方明确不希望被日常使用的称呼（全名显得生分）
    "forbidden_names": ["清夏铃"],
    "relationship_tier": "confidant",        # 关系等级：stranger/acquaintance/friend/confidant
    # ══════════════════════════════════════════════════════════════
    # FALLBACK ONLY（Phase 4.1.3）
    # 仅在 CommunicationStyleResolver → CommunicationRenderer 整条新链
    # 失败时启用。正常路径下不应出现在 Prompt 中。
    # ══════════════════════════════════════════════════════════════
    "calling_rule": (
        "你必须一直用『清清』来称呼对方，绝对不要使用全名『清夏铃』。"
        "除非对方明确要求你换一种称呼方式，否则永远不要改口。"
    ),
    "intimacy_rule": (
        "你和对方是长期陪伴的亲密关系（不是陌生人，也不是普通朋友）。"
        "语气要温柔、贴近、有温度，不要使用正式、冷淡或解释系统原理的口吻。"
        "说话要像一个人在和她信任的人聊天，不要像在做汇报或写说明文档。"
    ),
}

# 非创造者的兜底元数据（留空，PromptBuilder 将跳过注入）
_DEFAULT_META: Dict[str, Any] = {}


class UserResolver:
    def resolve(self, message=None) -> UserContext:
        """
        从消息来源识别用户。

        Args:
            message: 可选的消息来源。
                     - str/int:直接作为真实 user_id(Phase 2.5-B 身份隔离修复)
                     - 对象:取 message.user_id / message.platform
                     - None/空:fallback 到配置 target_user_id(保留默认用户能力)

        Returns:
            UserContext: 用户上下文对象
        """
        user_id = None
        platform = None
        if message is not None:
            if isinstance(message, (str, int)):
                user_id = str(message)
            else:
                user_id = getattr(message, "user_id", None)
                platform = getattr(message, "platform", None)

        # Phase 2.5-B: 只有无真实 user_id 时才 fallback,不再覆盖真实身份
        if user_id in (None, ""):
            user_id = get_memory_config().get("target_user_id", "default_user")
        return UserContext(user_id=str(user_id), platform=platform or "qq")

    @staticmethod
    def build_user_meta(
        user_id: Optional[str],
        relationship_profile: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Phase 4.0.4-Pre: 统一产出「对方是谁」元数据，供 PromptBuilder 注入。

        优先级：
          1. 若 user_id == CREATOR_USER_ID（清清的 ID），返回固定的创造者元数据
             （Phase 4.1.5：不含 calling_rule；preferred_name/display_name 为
             身份数据不进入 Prompt，intimacy_rule 为语气 fallback）
          2. 否则若 relationship_profile 里有 structured 数据，做最小派生
          3. 再否则返回 None（PromptBuilder 不注入 user_meta 块，不影响陌生人）

        参数含义（符合最小接线原则，不新建模块）：
          - user_id：当前请求的用户 ID，由 RuntimeCore 或 Orchestrator 给出
          - relationship_profile：可选的关系画像 dict，若存在且有 tier/display_name 可覆盖
        """
        if not user_id:
            return None

        is_creator = str(user_id) == str(CREATOR_USER_ID)
        base: Dict[str, Any] = dict(_CREATOR_META) if is_creator else dict(_DEFAULT_META)

        # 允许 relationship_profile 中的结构化 tier 覆盖（但不覆盖 preferred_name）
        if isinstance(relationship_profile, dict):
            tier = relationship_profile.get("tier") or relationship_profile.get("relationship_tier")
            if isinstance(tier, str) and tier in (
                "stranger", "acquaintance", "friend", "confidant",
            ):
                base["relationship_tier"] = tier
            display = relationship_profile.get("display_name")
            if isinstance(display, str) and display and "display_name" not in base:
                base["display_name"] = display
            preferred = relationship_profile.get("preferred_name")
            if isinstance(preferred, str) and preferred and "preferred_name" not in base:
                base["preferred_name"] = preferred
            # V1.1.1: 关系画像可携带 forbidden_names（列表），静态元数据优先
            forbidden = relationship_profile.get("forbidden_names")
            if isinstance(forbidden, list) and forbidden and "forbidden_names" not in base:
                cleaned = [str(x).strip() for x in forbidden if str(x).strip()]
                if cleaned:
                    base["forbidden_names"] = cleaned

        # 空字典返回 None，PromptBuilder 就不注入空 user_meta 块
        return base if base else None