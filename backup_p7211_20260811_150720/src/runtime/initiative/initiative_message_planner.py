# -*- coding: utf-8 -*-
"""
src/runtime/initiative/initiative_message_planner.py

Phase C.9.0 Initiative Message Planner.

只负责:
  - 根据 trigger / context / style 生成消息请求
  - 不调用任何聊天接口
  - 不发送消息
  - 不修改任何系统

输出:
  {
    "type": "continue_topic" | "relationship_check" | "reflection" | "support" | "interest" | "none",
    "context": { ... },
    "style": "personality_style" | "default",
    "template": "可选,给消息渠道的语义模板"
  }
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, FrozenSet, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema + constants
# ============================================================

INITIATIVE_MESSAGE_PLANNER_SCHEMA_VERSION = "1.0"
INITIATIVE_MESSAGE_PLANNER_NAME = "initiative_message_planner"
INITIATIVE_MESSAGE_PLANNER_VERSION = "1.0.0"

# Message types
MSG_TYPE_CONTINUE_TOPIC = "continue_topic"
MSG_TYPE_RELATIONSHIP_CHECK = "relationship_check"
MSG_TYPE_REFLECTION = "reflection"
MSG_TYPE_SUPPORT = "support"
MSG_TYPE_INTEREST = "interest"
MSG_TYPE_NONE = "none"

ALL_MESSAGE_TYPES: FrozenSet[str] = frozenset({
    MSG_TYPE_CONTINUE_TOPIC,
    MSG_TYPE_RELATIONSHIP_CHECK,
    MSG_TYPE_REFLECTION,
    MSG_TYPE_SUPPORT,
    MSG_TYPE_INTEREST,
    MSG_TYPE_NONE,
})

# Style
STYLE_DEFAULT = "default"
STYLE_PERSONALITY = "personality_style"
STYLE_GENTLE = "gentle"
STYLE_CARING = "caring"

ALL_STYLES: FrozenSet[str] = frozenset({
    STYLE_DEFAULT,
    STYLE_PERSONALITY,
    STYLE_GENTLE,
    STYLE_CARING,
})

# Trigger -> Message type mapping
TRIGGER_TO_MESSAGE_TYPE = {
    "unfinished_topic": MSG_TYPE_CONTINUE_TOPIC,
    "relationship_check": MSG_TYPE_RELATIONSHIP_CHECK,
    "user_interest": MSG_TYPE_INTEREST,
    "emotional_support": MSG_TYPE_SUPPORT,
    "growth_reflection": MSG_TYPE_REFLECTION,
    "none": MSG_TYPE_NONE,
}


# ============================================================
# Utilities
# ============================================================


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


def _safe_dict(v: Any) -> Dict[str, Any]:
    try:
        if isinstance(v, dict):
            return dict(v)
        return {}
    except Exception:
        return {}


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        return []
    except Exception:
        return []


def _safe_deepcopy(v: Any) -> Any:
    try:
        return copy.deepcopy(v)
    except Exception:
        if isinstance(v, dict):
            return {}
        if isinstance(v, list):
            return []
        return v


# ============================================================
# Message Planner
# ============================================================


class InitiativeMessagePlanner:
    """
    Initiative Message Planner (Phase C.9.0 / v1.0).

    接收:
      - trigger: 触发器类型
      - trigger_evidence: 触发器评估的证据
      - personality_snapshot: Personality snapshot(只读)

    输出:InitiativeMessageRequest
    """
    SCHEMA_VERSION = INITIATIVE_MESSAGE_PLANNER_SCHEMA_VERSION
    NAME = INITIATIVE_MESSAGE_PLANNER_NAME
    VERSION = INITIATIVE_MESSAGE_PLANNER_VERSION

    def __init__(self, default_style: str = STYLE_GENTLE) -> None:
        self._default_style = _safe_str(default_style, STYLE_GENTLE) or STYLE_GENTLE

    def plan(
        self,
        trigger: str,
        trigger_evidence: Optional[Dict[str, Any]] = None,
        personality_snapshot: Optional[Dict[str, Any]] = None,
        user_id: str = "",
    ) -> Dict[str, Any]:
        """
        生成一条 InitiativeMessageRequest。
        """
        try:
            t = _safe_str(trigger, "none")
            msg_type = TRIGGER_TO_MESSAGE_TYPE.get(t, MSG_TYPE_NONE)
            evidence = _safe_dict(trigger_evidence)
            personality = _safe_dict(personality_snapshot)
            style = self._resolve_style(personality)
            template = self._build_template(t, evidence, personality)
            request = {
                "schema_version": self.SCHEMA_VERSION,
                "type": msg_type,
                "trigger": t,
                "context": {
                    "user_id": _safe_str(user_id, ""),
                    "evidence": _safe_deepcopy(evidence),
                    "personality_present": bool(personality),
                },
                "style": style,
                "template": template,
                "channel_ready": False,  # C.9.0 不发送
            }
            return request
        except Exception as exc:
            logger.warning("[InitiativeMessagePlanner] plan failed: %s", exc)
            return {
                "schema_version": self.SCHEMA_VERSION,
                "type": MSG_TYPE_NONE,
                "trigger": _safe_str(trigger, "none"),
                "context": {"user_id": _safe_str(user_id, ""), "error": str(exc)},
                "style": self._default_style,
                "template": "",
                "channel_ready": False,
                "error": str(exc),
            }

    def _resolve_style(self, personality: Dict[str, Any]) -> str:
        try:
            # 从 personality snapshot 中读取 style hint(只读)
            style_hint = _safe_str(personality.get("style_hint"), "")
            if style_hint and style_hint in ALL_STYLES:
                return style_hint
            comm_style = _safe_dict(personality.get("communication_style"))
            if comm_style:
                if _safe_str(comm_style.get("tone")) in ("gentle", "warm"):
                    return STYLE_GENTLE
                if _safe_str(comm_style.get("tone")) in ("caring", "nurturing"):
                    return STYLE_CARING
            return self._default_style
        except Exception:
            return self._default_style

    def _build_template(
        self,
        trigger: str,
        evidence: Dict[str, Any],
        personality: Dict[str, Any],
    ) -> str:
        try:
            if trigger == "unfinished_topic":
                topic = _safe_str(evidence.get("topic"), "之前聊过的话题")
                return f"想起你之前提到过:{topic}。最近有新的进展吗?"
            if trigger == "relationship_check":
                days = evidence.get("days_since", "?")
                return f"好久没聊了,想问问你最近怎么样?({days} 天没联系)"
            if trigger == "user_interest":
                interest = _safe_str(evidence.get("interest"), "")
                return f"你之前对 {interest} 挺感兴趣的,最近怎么样?"
            if trigger == "emotional_support":
                return "感觉你最近情绪有些变化,想陪你聊聊。"
            if trigger == "growth_reflection":
                name = _safe_str(evidence.get("name"), "成长")
                return f"我最近有些 {name} 方面的变化,想和你分享一下。"
            return ""
        except Exception:
            return ""


def create_initiative_message_planner(
    default_style: str = STYLE_GENTLE,
) -> InitiativeMessagePlanner:
    return InitiativeMessagePlanner(default_style=default_style)


# ============================================================
# Safe wrapper
# ============================================================


def safe_plan_message(
    planner: Any,
    trigger: str,
    trigger_evidence: Optional[Dict[str, Any]] = None,
    personality_snapshot: Optional[Dict[str, Any]] = None,
    user_id: str = "",
) -> Dict[str, Any]:
    if planner is None or not hasattr(planner, "plan"):
        return {
            "schema_version": INITIATIVE_MESSAGE_PLANNER_SCHEMA_VERSION,
            "type": MSG_TYPE_NONE,
            "trigger": _safe_str(trigger, "none"),
            "context": {"user_id": _safe_str(user_id, ""), "error": "planner unavailable"},
            "style": STYLE_DEFAULT,
            "template": "",
            "channel_ready": False,
        }
    try:
        return planner.plan(
            trigger=trigger,
            trigger_evidence=trigger_evidence,
            personality_snapshot=personality_snapshot,
            user_id=user_id,
        )
    except Exception as exc:
        return {
            "schema_version": INITIATIVE_MESSAGE_PLANNER_SCHEMA_VERSION,
            "type": MSG_TYPE_NONE,
            "trigger": _safe_str(trigger, "none"),
            "context": {"user_id": _safe_str(user_id, ""), "error": str(exc)},
            "style": STYLE_DEFAULT,
            "template": "",
            "channel_ready": False,
        }
