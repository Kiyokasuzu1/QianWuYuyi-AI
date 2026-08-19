"""
Phase 7.2.1.1-identity-stabilization: OriginFacade —— 只读查询层。

设计原则（严格遵守，不要在此文件加任何写逻辑）：
  1. 只从已冻结的 OriginIdentity（R2.7.6-YUYI）中"读"当前 user_id 的角色。
  2. 不修改身份、不创建 contributor、不写存储、不判断关系升级。
  3. 写数据库 / 关系升级 永远走 OriginManager，不走 OriginFacade。
  4. 所有返回值使用 Python 原生类型（dict/list/str/None），方便 Prompt 直接渲染。

用户可感知内容（中文直接返回）：
  - 366648462 = 清清，四角色冻结。
  - 其他任何 user_id → 返回"陌生用户"（空角色，空显示名，is_creator=False）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.identity.origin_identity import (
    CREATOR_USER_ID,
    FROZEN_ROLES,
    OriginContributor,
    OriginIdentity,
    OriginRole,
)

# 角色英文 key → 中文自然语言标签（Phase 7.2.1.1 首次加入；未来改文案只需改这里）
_ROLE_LABELS: Dict[str, str] = {
    OriginRole.CREATOR: "创造者",
    OriginRole.PERSONALITY_DESIGNER: "人格设计者",
    OriginRole.SYSTEM_BUILDER: "系统构建者",
    OriginRole.GROWTH_PARTICIPANT: "成长陪伴者",
}

# 已知贡献者的显示名（只存 Origin 冻结角色对应的显示名，runtime 用户昵称走 relationship/user_name）
# 注：不要把 runtime 可变昵称（比如 relationship 里用户自己改的）塞进来。
#     这里只放起源身份层面"永远不会忘"的称呼。
_KNOWN_DISPLAY_NAMES: Dict[str, str] = {
    CREATOR_USER_ID: "清清",
}


class OriginFacade:
    """OriginIdentity 的只读 Facade。

    典型用法：
        facade = OriginFacade.default()
        facade.get_display_name("366648462")  # "清清"
        facade.get_label("366648462")          # "创造者 / 人格设计者 / 系统构建者 / 成长陪伴者（清清）"
        facade.get_roles("123456789")          # []
    """

    _singleton: Optional["OriginFacade"] = None

    # ------------------------------------------------------------
    # 构造（尽量懒，不要在 import 时就触发大量 IO）
    # ------------------------------------------------------------
    def __init__(self, identity: Optional[OriginIdentity] = None) -> None:
        """接受显式注入的 OriginIdentity（测试可传）；生产默认使用冻结版本。"""
        if identity is not None:
            self._identity = identity
        else:
            self._identity = OriginIdentity.create_frozen(CREATOR_USER_ID)

    @classmethod
    def default(cls) -> "OriginFacade":
        """单例入口：所有用户共享一个 Facade（因为 OriginIdentity 是全局冻结的）。"""
        if cls._singleton is None:
            cls._singleton = cls()
        return cls._singleton

    # ------------------------------------------------------------
    # 内部 helper（私有，不要暴露写能力）
    # ------------------------------------------------------------
    def _find_contributor(self, user_id: str) -> Optional[OriginContributor]:
        if not user_id:
            return None
        uid = str(user_id)
        for c in self._identity.contributors:
            if str(c.user_id) == uid:
                return c
        return None

    # ------------------------------------------------------------
    # 对外只读接口（三选一即可 / 或按需组合调用）
    # ------------------------------------------------------------
    def get_roles(self, user_id: str) -> List[str]:
        """返回该用户的起源角色列表（英文常量）。陌生用户返回空列表。"""
        c = self._find_contributor(user_id)
        if c is None:
            return []
        return [r for r in list(c.roles or []) if r in FROZEN_ROLES]

    def get_display_name(self, user_id: str) -> Optional[str]:
        """返回该用户的起源身份显示名（只存起源层永不遗忘的称呼）。陌生人返回 None。

        优先级：
          1. 如果贡献者本身有 description 里的 nickname（未来扩展），可覆盖。
          2. 否则查 KNOWN_DISPLAY_NAMES 硬编码表。
        """
        if not user_id:
            return None
        # 未来：如果 contributor.description 含 "name:xxx"，可从那儿提取。
        # 目前保持极简硬编码。
        name = _KNOWN_DISPLAY_NAMES.get(str(user_id))
        if name:
            return name
        c = self._find_contributor(user_id)
        if c is not None and c.description:
            return c.description.strip() or None
        return None

    def is_owner(self, user_id: str) -> bool:
        """是否是创造者本人（即清清）。用于 Prompt 里"主人级"信任决策。"""
        return str(user_id) == CREATOR_USER_ID

    def get_label(self, user_id: str) -> str:
        """返回自然语言关系标签，供 Prompt 直接拼入 system_message。

        示例：
          "创造者 / 人格设计者 / 系统构建者 / 成长陪伴者（清清）"
          "陌生用户（user_id=123456789）"
        """
        roles = self.get_roles(user_id)
        display = self.get_display_name(user_id)
        if roles:
            labels = [_ROLE_LABELS[r] for r in roles if r in _ROLE_LABELS]
            role_str = " / ".join(labels) if labels else "起源贡献者"
            if display:
                return f"{role_str}（{display}）"
            return role_str
        # 陌生人（防御性打印 uid 缩略，防止 Prompt 里出现超长内容）
        uid_str = str(user_id) if user_id else "unknown"
        return f"陌生用户（user_id={uid_str[:16]}）"

    # ------------------------------------------------------------
    # 统一 dict 输出：方便 RuntimeController.turn_cfg 一次性塞
    # ------------------------------------------------------------
    def get_user_identity(self, user_id: str) -> Dict[str, Any]:
        """统一字典输出：返回 user_id 的完整 Origin 身份摘要。

        输出字段（全部只读，供 Prompt / debug 使用）：
            user_id           str  归一化后的 user_id
            is_owner          bool 是否是创造者本人（清清）
            roles             List[str]  角色英文常量列表
            role_labels       List[str]  角色中文标签列表（与 roles 同序）
            display_name      Optional[str]  起源层显示名（无则 None）
            label             str  可直接拼入 Prompt 的自然语言描述
        """
        uid = str(user_id) if user_id else ""
        roles = self.get_roles(uid)
        role_labels = [_ROLE_LABELS[r] for r in roles if r in _ROLE_LABELS]
        return {
            "user_id": uid,
            "is_owner": self.is_owner(uid),
            "roles": roles,
            "role_labels": role_labels,
            "display_name": self.get_display_name(uid),
            "label": self.get_label(uid),
        }
