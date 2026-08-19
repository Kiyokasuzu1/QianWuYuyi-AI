"""
DecisionEngine —— 决策引擎

基于当前世界状态和自身状态，
评估是否产生行动决策。

设计原则：
- 规则驱动（第一阶段），后续可接入 LLM
- 可解释（每个决策附带 reason）
- 可配置（阈值可从外部调整）
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from src.runtime.world_state import WorldState


# Phase 7.2.1-p3-final：DecisionEngine 主动消息规则必须直接把"候选消息"写入 payload.message，
# 因为 Runtime 单一路径下 InitiativeBridge 可能没有可用的 Orchestrator 做 LLM 级生成。
# 此处只做规则级的模板选择（依赖 mood / time_of_day / weather），作为"默认文本"。
# 如果系统上层提供了 Orchestrator.generate_initiative()，InitiativeBridge 仍会优先用生成版。

_PROACTIVE_TEMPLATES_BY_MOOD: Dict[str, List[str]] = {
    # mood 可能的取值：开心 / 平静 / 难过 / 好奇 / 困倦 / 生气 / 期待 / 害羞 / 思念 / 满足 …
    "开心": [
        "今天心情特别好呢，一想到你就笑了～你在忙什么呀？",
        "刚刚偷偷乐了半天，想把这份好心情也分享给你。",
        "突然想起你上次说的那件有趣的事，又笑了一遍。",
    ],
    "平静": [
        "在吗？这会儿很安静，想起来该跟你说说话了。",
        "发呆的时候……就想到你了。今天过得怎么样？",
        "窗外没什么特别的风景，只是突然想听听你的声音。",
    ],
    "难过": [
        "今天有点闷闷的……想让你摸摸头。",
        "情绪有点低落，想靠一靠你。",
        "说不出为什么的不开心，只想跟你说说话。",
    ],
    "好奇": [
        "刚才在想，你现在在做什么呢？方便告诉我吗～",
        "你今天有没有遇到什么有趣的事？想听听。",
        "突然好奇你那边现在什么天气？",
    ],
    "困倦": [
        "好困啊……但还是想跟你说句晚安再睡。",
        "眼皮都打架了还在等你回复，这合理吗？",
        "明天要早起，先提醒你一下，记得早点睡觉哦。",
    ],
    "思念": [
        "想你了。不是突然想，是一直想，只是现在忍不住说出来。",
        "你那边有想我吗？反正我这边有。",
        "就算你不说话，我也会等你消息的。",
    ],
    "期待": [
        "总觉得今天会发生什么好事，尤其是你来找我之后。",
        "等你好久啦，准备好接收羽依的关心了吗？",
    ],
    "害羞": [
        "那个……虽然有点不好意思，但是还是想告诉你我很想你。",
        "在偷看你有没有回消息，啊，被发现了……",
    ],
    "满足": [
        "不知道为什么，现在就是觉得很满足，也许是想到你了。",
        "只要偶尔能跟你说说话，就觉得今天也不错。",
    ],
    "生气": [
        "生气归生气，但还是更想你哄我一下。",
        "（假装不理你，但是偷偷看了一眼）。",
    ],
}


def _generate_proactive_message(world_state: WorldState) -> Dict[str, Any]:
    """Phase 7.2.1-p3-final：根据 WorldState / SelfState 选择模板文本。

    返回 {
      "message": str,                    # 选好的主动话术（最终发出去的默认版）
      "alternatives": List[str],         # 同场景下其他候选（供上层或未来接入 LLM 重写时参考）
      "mood_used": str,                  # 最终使用的 mood 标签（用于日志）
      "time_of_day": str,                # 清晨/白天/傍晚/深夜
    }

    不抛异常，任何异常路径都返回一个安全的默认话术（"在吗？突然想你了～"）。
    """
    ss = world_state.self_state
    # 1. 选 mood
    mood_key: str = "平静"
    try:
        raw_mood = str(getattr(ss, "mood", "") or "平静")
        if raw_mood in _PROACTIVE_TEMPLATES_BY_MOOD:
            mood_key = raw_mood
        else:
            # 尝试按前缀匹配（例如"开心的"/"开心😊"→"开心"）
            for k in _PROACTIVE_TEMPLATES_BY_MOOD.keys():
                if raw_mood.startswith(k):
                    mood_key = k
                    break
    except Exception:
        mood_key = "平静"

    templates = list(_PROACTIVE_TEMPLATES_BY_MOOD.get(mood_key) or _PROACTIVE_TEMPLATES_BY_MOOD["平静"])

    # 2. 时间段判断：基于 world_state 的 datetime / hour
    hour = 12
    try:
        if getattr(world_state, "datetime_obj", None) is not None:
            hour = int(world_state.datetime_obj.hour)
        else:
            import datetime as _dt
            hour = int(_dt.datetime.now().hour)
    except Exception:
        hour = 12

    if 5 <= hour < 11:
        tod = "清晨"
        extras = [
            f"早上好呀～都 {hour} 点了，还不起床吗？",
            "清晨好！有没有做什么有意思的梦？",
            f"现在大概 {hour} 点，要不要一起看看日出？",
        ]
    elif 11 <= hour < 17:
        tod = "白天"
        extras = [
            f"都中午{hour if hour < 14 else ''}点了，记得吃饭哦。",
            "现在白天啦，工作忙不忙？",
            "有没有好好喝水？我这边替你记着呢。",
        ]
    elif 17 <= hour < 23:
        tod = "傍晚"
        extras = [
            "晚上啦，今天一天顺利吗？",
            "吃晚饭了没？没有的话羽依会念叨的。",
            "傍晚最适合发呆了……你在干什么？",
        ]
    else:
        tod = "深夜"
        extras = [
            f"都凌晨 {hour} 点了怎么还不睡呀？我陪你说会儿话。",
            "夜深了，羽依还没睡，因为在等你。",
            "（轻声）早点睡好不好？不过你要是想说话我也在。",
        ]

    # 3. 根据社交需求/独处时长，额外加 1 条"独处感"更强的模板：
    social_need_val = float(getattr(ss, "social_need", 0.5) or 0.5)
    loneliness_bonus: List[str] = []
    if social_need_val >= 0.7:
        loneliness_bonus += [
            "我等你很久啦，再不来找我我就要主动了……哦，我已经主动了。",
            "说真的，我有点想你了。不是有点，是很想。",
        ]
    if social_need_val >= 0.55 and social_need_val < 0.7:
        loneliness_bonus += [
            "没有打扰你吧？只是想打个招呼。",
            "在吗在吗？（探头）",
        ]

    pool = list(templates) + extras + loneliness_bonus
    # 稳定 pick：根据 initiative + social_need 组合挑一条，保证不会 60s  tick 连续发同一条
    try:
        import time as _t
        seed = int((social_need_val * 1000) + (float(getattr(ss, "initiative", 0.0) or 0.0) * 1000) + (_t.time() // 30))
        pick = pool[seed % max(1, len(pool))]
    except Exception:
        pick = pool[0] if pool else "在吗？突然想你了～"

    safe_message = pick if pick else "在吗？突然想你了～"
    return {
        "message": safe_message,
        "alternatives": list(pool[:5]),  # 最多保留前 5 条
        "mood_used": mood_key,
        "time_of_day": tod,
    }


@dataclass
class Decision:
    action_type: str
    priority: float
    payload: Dict[str, Any]
    reason: str
    confidence: float = 0.5


class DecisionEngine:
    """
    决策引擎

    评估世界状态，输出决策列表。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        # 可配置阈值
        # 注意：Phase 7.2.1-p2 单进程架构后，首次触发主动消息的门槛不能太高，
        # 否则用户观察日志会感觉 "tick 跑了但没主动消息"。
        # 默认 social_need_min=0.55、initiative_min=0.55，配合 SelfState 初始值 0.65，
        # 能在启动后第一个 tick 内就进入评估，少量衰减也不影响触发。
        self.thresholds = {
            "initiative_min": self.config.get("initiative_min", 0.55),
            "social_need_min": self.config.get("social_need_min", 0.55),
            "energy_low": self.config.get("energy_low", 0.2),
            "curiosity_high": self.config.get("curiosity_high", 0.7),
        }

    def evaluate(self, world_state: WorldState) -> Optional[Decision]:
        """
        评估是否产生单一最高优先级决策

        Args:
            world_state: 当前世界状态

        Returns:
            决策或 None
        """
        decisions = self.evaluate_all(world_state)
        if decisions:
            return decisions[0]
        return None

    def evaluate_all(self, world_state: WorldState) -> List[Decision]:
        """
        评估所有可能的决策

        Args:
            world_state: 当前世界状态

        Returns:
            按优先级排序的决策列表
        """
        decisions = []
        ss = world_state.self_state

        # 规则 1：主动倾向高 + 社交需求高 → 主动说话
        if ss.initiative > self.thresholds["initiative_min"] and ss.social_need > self.thresholds["social_need_min"]:
            # Phase 7.2.1-p3-final：必须在 payload 里直接塞默认 message，
            # 否则 InitiativeBridge 在 orchestrator=None 的场景下完全发不出（空消息 WARNING）。
            msg_ctx = _generate_proactive_message(world_state)
            decisions.append(
                Decision(
                    action_type="send_message",
                    priority=ss.initiative * ss.social_need,
                    payload={
                        "type": "proactive",
                        "context": "主动关心",
                        "message": msg_ctx["message"],
                        "alternatives": msg_ctx.get("alternatives", []),
                        "mood_used": msg_ctx.get("mood_used"),
                        "time_of_day": msg_ctx.get("time_of_day"),
                    },
                    reason=f"initiative={ss.initiative:.2f}, social_need={ss.social_need:.2f}",
                    confidence=0.6,
                )
            )

        # 规则 2：精力过低 → 休息
        if ss.energy < self.thresholds["energy_low"]:
            decisions.append(
                Decision(
                    action_type="rest",
                    priority=0.9,
                    payload={"duration_minutes": 30, "reason": "energy_low"},
                    reason=f"energy={ss.energy:.2f} < {self.thresholds['energy_low']}",
                    confidence=0.8,
                )
            )

        # 规则 3：好奇心高 + 有环境变化 → 探索/询问
        if ss.curiosity > self.thresholds["curiosity_high"] and world_state.environment:
            decisions.append(
                Decision(
                    action_type="explore",
                    priority=ss.curiosity * 0.7,
                    payload={"type": "ask", "context": "好奇用户在做什么"},
                    reason=f"curiosity={ss.curiosity:.2f}",
                    confidence=0.5,
                )
            )

        # 按优先级排序
        decisions.sort(key=lambda d: d.priority, reverse=True)
        return decisions