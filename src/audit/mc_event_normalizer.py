# -*- coding: utf-8 -*-
"""MC 事件归一化（v1.5.5-MC1）— 纯函数，无 IO、无 LLM、无业务依赖。

单一真源：游戏事件 → T4 四字段（origin/event_class/self_involvement/authored）
+ emotion_tag 静态映射。本模块不做任何语义推断；未知事件一律拒绝。
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

# 事件 → (event_class, self_involvement, authored, emotion_tag)
_EVENT_TABLE = {
    "death": ("world", "patient", False, "fear"),
    "join": ("interaction", "agent", True, "calm"),
    "leave": ("interaction", "agent", True, "calm"),
    "craft": ("interaction", "agent", True, "calm"),
    "collect": ("interaction", "agent", True, "calm"),
    "achievement": ("world", "patient", False, "calm"),
}

MC_USER_ID = "366648462"


def normalize_mc_event(event: str, detail: Optional[Dict[str, Any]], ts: str = "") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """把游戏事件归一化为 memory record dict。

    返回 (record, error)。record 为 None 时 error 为拒绝原因。
    detail 允许任意键（只取认识的），缺省容错。
    """
    if not isinstance(event, str):
        return None, "event 必须为字符串"
    event = event.strip().lower()
    spec = _EVENT_TABLE.get(event)
    if spec is None:
        return None, f"未知事件: {event}"

    event_class, self_involvement, authored, emotion_tag = spec
    detail = detail if isinstance(detail, dict) else {}

    # 时间：请求 ts 优先，缺省取服务器当前 UTC
    try:
        stamp = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
    except Exception:  # noqa: BLE001
        stamp = datetime.now(timezone.utc)

    content = _build_content(event, detail, stamp)

    record = {
        "content": content,
        "timestamp": stamp.isoformat(),
        "user_id": MC_USER_ID,
        # 注意：role 必须为 user（PollutionGuard 白名单）；来源区分靠 metadata.source
        "role": "user",
        "importance": 0.6,
        "source_event_id": "",
        "emotion_tag": emotion_tag,
        "relationship_id": MC_USER_ID,
        "metadata": {
            "memory_type": "user_experience",  # PollutionGuard 白名单类型
            "source": "mc_events",
            "origin": "system",
            "event_class": event_class,
            "self_involvement": self_involvement,
            "authored": authored,
        },
    }
    return record, None


def _round1(v: Any) -> str:
    try:
        return f"{float(v):.1f}"
    except Exception:  # noqa: BLE001
        return "?"


def _build_content(event: str, detail: Dict[str, Any], stamp) -> str:
    ts_str = stamp.strftime("%Y-%m-%d %H:%M")
    x, y, z = _round1(detail.get("x")), _round1(detail.get("y")), _round1(detail.get("z"))
    pos = f"({x},{y},{z})"
    cause = str(detail.get("cause") or "unknown")

    if event == "death":
        return f"{ts_str} 在 {pos} 被 {cause} 击杀。"
    if event == "join":
        player = str(detail.get("player") or "")
        if player:
            return f"{ts_str} {player} 上线了。"
        return f"{ts_str} 进入了这个世界。"
    if event == "leave":
        player = str(detail.get("player") or "")
        if player:
            return f"{ts_str} {player} 下线了。"
        return f"{ts_str} 离开了这个世界。"
    if event == "craft":
        item = str(detail.get("item") or "物品")
        count = int(detail.get("count") or 1)
        return f"{ts_str} 合成了 {count} 个 {item}。"
    if event == "collect":
        item = str(detail.get("item") or "物品")
        count = int(detail.get("count") or 1)
        return f"{ts_str} 拾取了 {count} 个 {item}。"
    if event == "achievement":
        name = str(detail.get("name") or detail.get("achievement") or "未知成就")
        return f"{ts_str} 达成了成就：{name}。"
    return f"{ts_str} 游戏事件：{event}。"
