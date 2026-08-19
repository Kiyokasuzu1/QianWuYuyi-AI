"""
意图推断器。

负责：
- 基于上下文推断用户意图
- 识别情感需求
- 预测潜在行为
"""

from typing import Dict, List, Optional


class IntentInferrer:
    """
    意图推断器。

    通过规则引擎分析用户文本，推断隐含意图和情感需求。
    不依赖机器学习模型，使用关键词和上下文规则。
    """

    # 情感需求规则库
    _EMOTION_RULES = [
        {
            "keywords": ["累了", "疲惫", "撑不住", "好累"],
            "emotion": "疲惫",
            "need": "需要鼓励或休息建议",
            "implied": "可能还想再撑一下",
        },
        {
            "keywords": ["无聊", "没意思", "空虚"],
            "emotion": "无聊",
            "need": "需要陪伴或新话题",
            "implied": "希望有人陪着",
        },
        {
            "keywords": ["难过", "伤心", "不开心", "想哭"],
            "emotion": "悲伤",
            "need": "需要安慰",
            "implied": "需要被倾听",
        },
        {
            "keywords": ["生气", "气死", "烦死", "讨厌"],
            "emotion": "愤怒",
            "need": "需要发泄和理解",
            "implied": "希望有人站在自己这边",
        },
        {
            "keywords": ["害怕", "担心", "焦虑", "紧张"],
            "emotion": "焦虑",
            "need": "需要安全感",
            "implied": "需要被安抚",
        },
        {
            "keywords": ["开心", "高兴", "嘿嘿", "哈哈"],
            "emotion": "快乐",
            "need": "分享喜悦",
            "implied": "希望对方也开心",
        },
        {
            "keywords": ["想你", "想见", "在吗"],
            "emotion": "思念",
            "need": "需要陪伴",
            "implied": "希望得到回应",
        },
        {
            "keywords": ["算了", "无所谓", "随便"],
            "emotion": "失望",
            "need": "需要被重视",
            "implied": "可能觉得不被在意",
        },
        {
            "keywords": ["对不起", "抱歉", "是我的错"],
            "emotion": "愧疚",
            "need": "需要被原谅",
            "implied": "希望关系不受影响",
        },
        {
            "keywords": ["谢谢", "感谢", "辛苦了"],
            "emotion": "感激",
            "need": "表达感谢",
            "implied": "认可对方的付出",
        },
    ]

    # 语境修饰规则
    _CONTEXT_MODIFIERS = [
        {
            "keywords": ["但是", "可是", "不过", "然而"],
            "modifier": "转折",
            "effect": "真实意图可能在转折之后",
        },
        {
            "keywords": ["其实", "说实话", "老实说"],
            "modifier": "坦白",
            "effect": "在表达真实想法",
        },
        {
            "keywords": ["可能", "也许", "大概"],
            "modifier": "不确定",
            "effect": "对表达的内容不确定",
        },
        {
            "keywords": ["一定", "必须", "肯定"],
            "modifier": "强调",
            "effect": "在强调自己的决心",
        },
    ]

    def __init__(self):
        pass

    def infer_intent(self, text: str, context: Dict = None) -> Dict:
        """
        推断用户意图。

        Args:
            text: 用户输入文本
            context: 上下文信息

        Returns:
            {
                "detected_emotions": [...],
                "primary_emotion": str,
                "inferred_need": str,
                "implied_meaning": str,
                "context_modifier": str,
                "modifier_effect": str,
                "confidence": float,
            }
        """
        detected = []

        # 情感检测
        for rule in self._EMOTION_RULES:
            matched_keywords = [kw for kw in rule["keywords"] if kw in text]
            if matched_keywords:
                detected.append({
                    "emotion": rule["emotion"],
                    "need": rule["need"],
                    "implied": rule["implied"],
                    "matched_keywords": matched_keywords,
                })

        # 语境修饰检测
        context_modifier = ""
        modifier_effect = ""
        for modifier_rule in self._CONTEXT_MODIFIERS:
            if any(kw in text for kw in modifier_rule["keywords"]):
                context_modifier = modifier_rule["modifier"]
                modifier_effect = modifier_rule["effect"]
                break

        # 确定主要情感
        primary_emotion = detected[0]["emotion"] if detected else "平静"
        inferred_need = detected[0]["need"] if detected else ""
        implied_meaning = detected[0]["implied"] if detected else ""

        # 置信度计算
        confidence = 0.0
        if detected:
            # 匹配关键词越多，置信度越高
            total_matches = sum(len(d["matched_keywords"]) for d in detected)
            confidence = min(1.0, 0.3 + total_matches * 0.2)

        # 上下文加成
        if context and context.get("immediate", {}).get("last_user_message"):
            # 如果和上一句话情感一致，置信度增加
            last_msg = context["immediate"]["last_user_message"]
            for rule in self._EMOTION_RULES:
                if any(kw in last_msg for kw in rule["keywords"]):
                    if rule["emotion"] == primary_emotion:
                        confidence = min(1.0, confidence + 0.15)
                    break

        return {
            "detected_emotions": detected,
            "primary_emotion": primary_emotion,
            "inferred_need": inferred_need,
            "implied_meaning": implied_meaning,
            "context_modifier": context_modifier,
            "modifier_effect": modifier_effect,
            "confidence": round(confidence, 2),
        }

    def detect_emotional_need(self, text: str, context: Dict = None) -> str:
        """
        检测情感需求。

        Args:
            text: 用户输入文本
            context: 上下文信息

        Returns:
            情感需求描述
        """
        result = self.infer_intent(text, context)
        return result["inferred_need"] or "无明显情感需求"

    def get_emotion_rules_count(self) -> int:
        """获取情感规则数量"""
        return len(self._EMOTION_RULES)