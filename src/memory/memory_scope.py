# -*- coding: utf-8 -*-
"""
Memory Scope 模型(Phase 2.5-B)

纯函数模块,无 DB、无状态 —— 记忆权限的唯一权威。

四层模型:
- yui_core                羽依自身核心(身份/起源/核心关系事实),任何会话可见
- relationship_core       羽依与特定人的长期关系约定,按 relationship_key/visibility 授权
- private_user            某人的私人经历,仅 owner 所在会话可见
- other_user_relationship 羽依与其他用户的关系经历,必须标注主体
- public                  公共知识,任何会话可见

向后兼容:
- 所有新字段可选、写入 metadata;
- 旧记录(无 memory_scope)靠 derive_scope 读时推导,不迁移、不改内容;
- 未提供 user_id 的调用方维持旧行为(不过滤)。
"""
from typing import Any, Dict, List, Optional

from src.identity.origin_identity import CREATOR_USER_ID

SCOPE_YUI_CORE = "yui_core"
SCOPE_RELATIONSHIP_CORE = "relationship_core"
SCOPE_PRIVATE_USER = "private_user"
SCOPE_OTHER_USER_RELATIONSHIP = "other_user_relationship"
SCOPE_PUBLIC = "public"

SCOPES = (
    SCOPE_YUI_CORE,
    SCOPE_RELATIONSHIP_CORE,
    SCOPE_PRIVATE_USER,
    SCOPE_OTHER_USER_RELATIONSHIP,
    SCOPE_PUBLIC,
)

# Prompt 展示名(身份事实,非称呼指令)
CREATOR_LABEL = "清夏铃"


def _is_anchor(record: Dict[str, Any]) -> bool:
    """是否命中关系核心锚点白名单(治理审核后加入)。"""
    try:
        from src.identity.yui_core_profile import CORE_RELATIONSHIP_MEMORY_IDS
        return record.get("id") in CORE_RELATIONSHIP_MEMORY_IDS
    except Exception:  # noqa: BLE001
        return False


def _effective_relationship_fields(record: Dict[str, Any]):
    """返回 (relationship_key, visibility)。

    锚点白名单记录缺省:relationship_key=yuyi:creator、visibility=global。
    """
    metadata = record.get("metadata") or {}
    rel_key = metadata.get("relationship_key")
    visibility = metadata.get("visibility")
    if _is_anchor(record):
        if rel_key is None:
            rel_key = f"yuyi:{CREATOR_USER_ID}"
        if visibility is None:
            visibility = "global"
    return rel_key, visibility


def derive_scope(record: Dict[str, Any]) -> str:
    """读时推导记录的记忆层。

    优先级:
    1. metadata.memory_scope(显式声明)
    2. 关系核心锚点白名单(审核过的历史记忆)
    3. role != "user" → yui_core(羽依自己的记录)
    4. 其余(含无 user_id)→ private_user(owner 缺省时不可召回)
    """
    if not isinstance(record, dict):
        return SCOPE_PRIVATE_USER
    metadata = record.get("metadata") or {}
    scope = metadata.get("memory_scope")
    if scope:
        return str(scope)
    if _is_anchor(record):
        return SCOPE_RELATIONSHIP_CORE
    role = record.get("role")
    if role and str(role) != "user":
        return SCOPE_YUI_CORE
    return SCOPE_PRIVATE_USER


def resolve_allowed(record: Dict[str, Any], current_user_id: Optional[str]) -> bool:
    """硬过滤:当前会话是否允许访问该记录。

    规则(见 phase25_relationship_memory_architecture.md §4.2):
    - yui_core / public:总是允许
    - private_user:owner == current_user
    - relationship_core:relationship_key == yuyi:current_user,或 visibility == global
    - other_user_relationship:subject == current_user 或 relationship_key 匹配,
      或 visibility == global
    - 无归属(owner 缺失)的 private_user:不允许(仅可渲染为「未知来源记录」)
    """
    if not isinstance(record, dict):
        return False
    uid = None if current_user_id is None else str(current_user_id)
    scope = derive_scope(record)
    metadata = record.get("metadata") or {}

    if scope in (SCOPE_YUI_CORE, SCOPE_PUBLIC):
        return True

    if scope == SCOPE_PRIVATE_USER:
        owner = metadata.get("owner_user_id") or record.get("user_id")
        return owner is not None and uid is not None and str(owner) == uid

    if scope == SCOPE_RELATIONSHIP_CORE:
        rel_key, visibility = _effective_relationship_fields(record)
        if rel_key and uid is not None and str(rel_key) == f"yuyi:{uid}":
            return True
        return visibility == "global"

    if scope == SCOPE_OTHER_USER_RELATIONSHIP:
        subject = metadata.get("subject_user_id")
        if subject is not None and uid is not None and str(subject) == uid:
            return True
        rel_key = metadata.get("relationship_key")
        if rel_key and uid is not None and str(rel_key) == f"yuyi:{uid}":
            return True
        return metadata.get("visibility") == "global"

    return False


def scope_weight(record: Dict[str, Any], current_user_id: Optional[str]) -> float:
    """软权重(只影响排序/截断,不影响硬过滤)。

    非当前对象的 global 关系核心(如清清约定出现在用户B会话)降权,
    避免喧宾夺主;主体标注仍由 label_for_prompt 保证。
    """
    scope = derive_scope(record)
    if scope == SCOPE_RELATIONSHIP_CORE:
        rel_key, _ = _effective_relationship_fields(record)
        if rel_key and current_user_id is not None and str(rel_key) != f"yuyi:{current_user_id}":
            return 0.5
    return 1.0


def _user_display(uid: Optional[str]) -> str:
    if uid in (None, ""):
        return "未知用户"
    if str(uid) == str(CREATOR_USER_ID):
        return CREATOR_LABEL
    return f"用户{uid}"


def _relationship_partner(record: Dict[str, Any]) -> Optional[str]:
    metadata = record.get("metadata") or {}
    rel_key = metadata.get("relationship_key") or record.get("_relationship_key")
    if rel_key and str(rel_key).startswith("yuyi:"):
        return str(rel_key).split("yuyi:", 1)[1]
    if _is_anchor(record):
        return CREATOR_USER_ID
    return record.get("user_id")


def label_for_prompt(record: Dict[str, Any], current_user_id: Optional[str] = None) -> str:
    """Prompt 主体标注:谁说的/关于谁/谁的约束。

    禁止 LLM 自行推断「所有 user 消息都是清清说的」——每条记忆必须带主体标签。
    """
    if not isinstance(record, dict):
        return "未知来源记录"
    metadata = record.get("metadata") or {}
    scope = record.get("_scope") or metadata.get("memory_scope") or derive_scope(record)

    if scope == SCOPE_YUI_CORE:
        return "羽依自身"

    if scope == SCOPE_RELATIONSHIP_CORE:
        partner = _relationship_partner(record)
        label = f"与{_user_display(partner)}的长期约定"
        if current_user_id is None or (partner is not None and str(partner) != str(current_user_id)):
            label += "(对羽依的约束)"
        return label

    if scope == SCOPE_PRIVATE_USER:
        owner = (
            metadata.get("owner_user_id")
            or record.get("_owner_user_id")
            or record.get("user_id")
        )
        if owner in (None, ""):
            return "未知来源记录"
        return f"{_user_display(owner)}曾说"

    if scope == SCOPE_OTHER_USER_RELATIONSHIP:
        subject = metadata.get("subject_user_id") or record.get("_subject_user_id") or record.get("user_id")
        return f"关于{_user_display(subject)}"

    if scope == SCOPE_PUBLIC:
        return "公共知识"

    return "未知来源记录"


def collect_core_records(store: Any) -> List[Dict[str, Any]]:
    """从 store 收集 YUI_CORE 与 global 关系核心记录(任何会话可用的核心层)。"""
    core: List[Dict[str, Any]] = []
    seen = set()
    try:
        for record in store.load() or []:
            if not isinstance(record, dict):
                continue
            scope = derive_scope(record)
            is_core = False
            if scope == SCOPE_YUI_CORE:
                is_core = True
            elif scope == SCOPE_RELATIONSHIP_CORE:
                _, visibility = _effective_relationship_fields(record)
                is_core = visibility == "global"
            if is_core:
                mid = record.get("id")
                if mid is not None and mid not in seen:
                    seen.add(mid)
                    core.append(record)
    except Exception:  # noqa: BLE001
        pass
    return core


def collect_allowed_records(
    store: Any,
    user_id: Optional[str],
    owner_limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """会话可用记忆装配:核心层(优先) + owner 私人层。

    - 核心层排前面,保证 prompt 截断时不被挤出;
    - owner 私人层 = store.get_by_user(user_id) 的最近 owner_limit 条;
    - 不做任何内容修改,只读。
    """
    if user_id is None:
        return []
    uid = str(user_id)
    records = collect_core_records(store)
    core_count = len(records)
    seen = {r.get("id") for r in records if isinstance(r, dict) and r.get("id") is not None}
    try:
        owner_records = store.get_by_user(uid) or []
    except Exception:  # noqa: BLE001
        owner_records = []
    for record in owner_records:
        if not isinstance(record, dict):
            continue
        mid = record.get("id")
        if mid is not None and mid in seen:
            continue
        if mid is not None:
            seen.add(mid)
        records.append(record)
    if owner_limit is not None and owner_limit > 0:
        records = records[:core_count] + records[core_count:][-owner_limit:]
    return records


__all__ = [
    "SCOPE_YUI_CORE",
    "SCOPE_RELATIONSHIP_CORE",
    "SCOPE_PRIVATE_USER",
    "SCOPE_OTHER_USER_RELATIONSHIP",
    "SCOPE_PUBLIC",
    "SCOPES",
    "derive_scope",
    "resolve_allowed",
    "scope_weight",
    "label_for_prompt",
    "collect_core_records",
    "collect_allowed_records",
]
