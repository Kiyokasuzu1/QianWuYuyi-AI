"""
Live2D Adapter — 情绪/事件 → 雪芽2.0 模型参数映射

将羽依的 EmotionState + 最近事件类型 (event_type)
转换为 Live2D 模型可执行的动作指令 (l2d_action)。

雪芽2.0 关键参数（来自 .cdi3.json）：
  - Param207       爱心眼
  - Param211       星星眼
  - open_Cheek     脸红 (shy)
  - open_06        哭
  - open_09        生气
  - open_10        疑问
  - open_03        宕机
  - Param17        脸黑
  - ParamCheek     脸颊 (通用)
  - ParamBrowRForm 右眉变形
  - ParamBrowLForm 左眉变形
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# ============================================================
# 雪芽2.0 参数ID（来自 A.雪芽2.0.cdi3.json）
# ============================================================
class L2DParam:
    HEART_EYE = "Param207"        # 爱心眼
    STAR_EYE = "Param211"         # 星星眼
    CHEEK_SHY = "open_Cheek"      # 脸红
    CRY = "open_06"               # 哭
    ANGRY = "open_09"             # 生气
    QUESTION = "open_10"          # 疑问
    CRASH = "open_03"             # 宕机
    BLACK_FACE = "Param17"        # 脸黑
    CHEEK = "ParamCheek"          # 脸颊（通用红润）
    BROW_R_FORM = "ParamBrowRForm"
    BROW_L_FORM = "ParamBrowLForm"
    BROW_R_ANGLE = "ParamBrowRAngle2"  # 右眉 委屈生气
    BROW_L_ANGLE = "ParamBrowLAngle2"   # 左眉 生气


def _params(*items) -> List[Dict[str, Any]]:
    """构造参数列表，每项 (id, value)。"""
    return [{"id": pid, "value": float(val)} for pid, val in items]


# ============================================================
# 场景映射表：event_type → L2D 动作
# ============================================================
# 每项包含：expression_name（中文名）、params（参数列表）、duration（秒）
SCENE_ACTION_MAP: Dict[str, Dict[str, Any]] = {
    # 用户成就（如打游戏通关）→ 哇塞！星星眼
    "achievement": {
        "expression_name": "哇塞",
        "params": _params(
            (L2DParam.STAR_EYE, 1.0),     # 星星眼
            (L2DParam.CHEEK, 0.6),        # 脸颊微红（激动）
        ),
        "duration": 8.0,
        "message": "哇塞！好厉害！",
    },
    # 用户夸赞羽依 → 害羞脸红
    "user_praise": {
        "expression_name": "害羞",
        "params": _params(
            (L2DParam.CHEEK_SHY, 1.0),    # 脸红
            (L2DParam.CHEEK, 0.8),        # 脸颊红润
        ),
        "duration": 6.0,
        "message": "哼哼...人家会害羞的啦",
    },
    # 用户冲突/生气 → 羽依委屈
    "user_conflict": {
        "expression_name": "委屈",
        "params": _params(
            (L2DParam.BROW_R_ANGLE, 0.8),  # 右眉委屈生气
            (L2DParam.CHEEK, 0.3),
        ),
        "duration": 5.0,
        "message": "唔...不要这样嘛",
    },
    # 失望
    "disappointment": {
        "expression_name": "难过",
        "params": _params(
            (L2DParam.CRY, 0.6),
        ),
        "duration": 5.0,
        "message": "哎...",
    },
    # 新话题 → 好奇
    "new_topic": {
        "expression_name": "好奇",
        "params": _params(
            (L2DParam.QUESTION, 0.7),
        ),
        "duration": 4.0,
        "message": "嗯？说说看~",
    },
}


# ============================================================
# 情绪维度兜底映射：当没有事件触发时，按情绪维度推断
# ============================================================
def _map_by_valence_arousal(valence: float, arousal: float,
                             anxiety: float, curiosity: float) -> Dict[str, Any]:
    """根据情绪维度（愉悦度/激活度）推断 L2D 动作。"""
    # 高愉悦 + 高激活 → 爱心眼（开心）
    if valence > 0.4 and arousal > 0.7:
        return {
            "expression_name": "开心",
            "params": _params(
                (L2DParam.HEART_EYE, 1.0),    # 爱心眼
                (L2DParam.CHEEK, 0.5),
            ),
            "duration": 5.0,
            "message": "今天心情很好呢 ♡",
        }
    # 高愉悦 + 低激活 → 平静微笑
    if valence > 0.3 and arousal < 0.3:
        return {
            "expression_name": "平静",
            "params": _params(
                (L2DParam.CHEEK, 0.3),
            ),
            "duration": 3.0,
            "message": "嗯~安静地陪着你就好",
        }
    # 高焦虑 → 不安
    if anxiety > 0.6:
        return {
            "expression_name": "不安",
            "params": _params(
                (L2DParam.QUESTION, 0.5),
            ),
            "duration": 4.0,
            "message": "有点不安呢...",
        }
    # 高好奇 → 好奇
    if curiosity > 0.6:
        return {
            "expression_name": "好奇",
            "params": _params(
                (L2DParam.QUESTION, 0.6),
            ),
            "duration": 4.0,
            "message": "咦？让我看看~",
        }
    # 低愉悦 + 高激活 → 生气
    if valence < -0.3 and arousal > 0.6:
        return {
            "expression_name": "生气",
            "params": _params(
                (L2DParam.ANGRY, 0.8),
            ),
            "duration": 5.0,
            "message": "哼，不太开心",
        }
    # 低愉悦 + 低激活 → 难过
    if valence < -0.3 and arousal < 0.3:
        return {
            "expression_name": "难过",
            "params": _params(
                (L2DParam.CRY, 0.5),
            ),
            "duration": 5.0,
            "message": "有点低落...",
        }
    # 默认：平静
    return {
        "expression_name": "平静",
        "params": [],
        "duration": 0.0,
        "message": "正在为您待命~",
    }


class Live2DAdapter:
    """
    情绪/事件 → Live2D 动作映射器。

    使用方式：
        adapter = Live2DAdapter()
        action = adapter.map_to_l2d_action(
            emotion_state={"valence": 0.5, "arousal": 0.7, ...},
            last_event_type="achievement",
            event_age_seconds=3.0,
        )
    """

    # 事件有效期（秒）：超过此时间则忽略事件，回退到情绪维度推断
    EVENT_FRESH_WINDOW: float = 30.0

    def map_to_l2d_action(
        self,
        emotion_state: Optional[Dict[str, float]] = None,
        last_event_type: str = "",
        event_age_seconds: float = 999.0,
    ) -> Dict[str, Any]:
        """
        根据情绪状态 + 最近事件类型，生成 L2D 动作指令。

        Args:
            emotion_state: 情绪状态字典，包含 valence/arousal/anxiety/curiosity/confidence/energy
            last_event_type: 最近的事件类型（如 user_praise、achievement）
            event_age_seconds: 事件发生距今秒数；>EVENT_FRESH_WINDOW 时忽略

        Returns:
            l2d_action 字典，字段：
              - expression_name: 表情名（中文）
              - params: [{id, value}, ...]  Live2D 参数列表
              - duration: float  持续秒数（0 表示无临时表情，保持默认）
              - message: str  对应的台词
              - reason: str  触发原因（debug 用）
        """
        emotion_state = emotion_state or {}
        valence = float(emotion_state.get("valence", 0.0))
        arousal = float(emotion_state.get("arousal", 0.5))
        anxiety = float(emotion_state.get("anxiety", 0.0))
        curiosity = float(emotion_state.get("curiosity", 0.5))

        # 1. 优先：事件驱动（如用户夸赞、用户成就）
        if last_event_type and event_age_seconds <= self.EVENT_FRESH_WINDOW:
            scene = SCENE_ACTION_MAP.get(last_event_type)
            if scene:
                return {
                    "expression_name": scene["expression_name"],
                    "params": scene["params"],
                    "duration": scene["duration"],
                    "message": scene["message"],
                    "reason": f"event:{last_event_type}",
                }

        # 2. 兜底：情绪维度推断
        action = _map_by_valence_arousal(valence, arousal, anxiety, curiosity)
        action["reason"] = "emotion:dimension"
        return action

    @staticmethod
    def get_default_action() -> Dict[str, Any]:
        """默认动作（平静待命）。"""
        return {
            "expression_name": "平静",
            "params": [],
            "duration": 0.0,
            "message": "正在为您待命~",
            "reason": "default",
        }
