"""
事件验证器 EventValidator v1.4

浅雾羽依成长系统 v1.4

职责:
判断事件是否值得成为羽依人生经历。

v1.4 更新 (Phase 6.2):
- 增加全局假设性问题防火墙，在类型验证之前拦截所有假设性未来提问
- 关键词精度优化：避免误杀正常表达
- 保留 v1.3.1 的关系事件验证逻辑作为第二层防护

核心:
过滤普通聊天、技术日志、假设性问题，仅保留真实的成长事件。

输出:
keep: 进入成长系统
review: 保存观察，不触发成长
discard: 丢弃
"""

from typing import List, Dict, Tuple

# 基础过滤关键词（忽略纯寒暄）
IGNORE_KEYWORDS = [
    "你好", "在吗", "早上好", "晚上好", "晚安",
]

# 技术类关键词
TECH_KEYWORDS = [
    "安装", "配置", "部署", "报错", "日志", "代码", "插件", "运行", "启动"
]

# 重要人生意义标签
LIFE_MEANINGS = [
    "birth", "identity_creation", "relationship_start",
    "promise", "growth_support", "companionship"
]

class EventValidator:
    def validate(self, events: List[Dict]) -> List[Dict]:
        result = []
        for event in events:
            if self.should_keep(event):
                result.append(event)
        return result

    def should_keep(self, event: Dict) -> bool:
        decision, score, reason = self.decide(event)
        metadata = event.setdefault("metadata", {})
        metadata["validator_decision"] = decision
        metadata["validator_score"] = score
        metadata["validator_reason"] = reason

        if decision == "review":
            metadata["validator_apply"] = False
            return True
        if decision == "keep":
            metadata["validator_apply"] = True
            return True

        metadata["validator_apply"] = False
        return False

    def decide(self, event: Dict) -> Tuple[str, float, str]:
        text = self.build_text(event)

        # 无证据直接丢弃
        if not self.has_evidence(event):
            return ("discard", 0.0, "no_evidence")

        # Phase 6.2: 全局假设性问题过滤（优先级最高）
        # 任何包含假设性未来提问的事件都不应成为成长事件
        hypothetical_markers = [
            "如果以后", "假如以后", "要是以后", "未来某一天",
            "如果我消失", "如果我离开",
            "如果我不再找你", "如果我以后不找你",
        ]
        future_question_markers = [
            "你怎么办",
            "你会怎么办",
            "你会怎样",
            "你会不会",
            "你会不会还",
        ]

        has_hypothetical = any(m in text for m in hypothetical_markers)
        has_future_question = any(m in text for m in future_question_markers)

        if has_hypothetical and has_future_question:
            return ("discard", 0.0, "global_future_hypothetical")

        # 普通互动词过滤
        for word in IGNORE_KEYWORDS:
            if word in text:
                return ("discard", 0.1, "normal_chat")

        # 技术事件特殊处理
        if self.is_technical(event):
            if self.technical_has_life(event):
                return ("keep", 0.8, "technical_with_life")
            return ("discard", 0.2, "technical_log")

        # 关系事件真实性验证（第二层防护）
        if event.get("event_type") == "relationship":
            valid, reject_reason = self._validate_relationship(event)
            if not valid:
                return ("discard", 0.1, reject_reason)

        # 生命价值评分
        score = self.life_value(event)

        if score >= 0.65:
            return ("keep", score, "life_event")
        elif score >= 0.35:
            return ("review", score, "borderline")
        else:
            return ("discard", score, "low_value")

    def _validate_relationship(self, event: Dict) -> Tuple[bool, str]:
        """
        关系事件真实性防火墙（第二层防护）。
        返回 (通过, 失败原因)
        """
        topic = event.get("canonical_topic", event.get("topic", ""))
        evidence = event.get("evidence", [])
        evidence_texts = [e.get("text", "") for e in evidence]
        # 统一小写，防止大小写绕过
        combined = (
            topic
            + " "
            + " ".join(evidence_texts)
        ).lower()

        # 1. 禁止对AI的测试/诱导句式（优先检测）
        test_patterns = [
            "你会不会", "你是不是", "你怎么办", "你爱我吗",
            "你喜欢我吗", "你离不开我", "你在乎我吗"
        ]
        if any(pattern in combined for pattern in test_patterns):
            return False, "ai_test_question"

        # 2. 禁止AI情感投射（用户描述AI的情感或陪伴行为）
        ai_emotional_patterns = [
            "你陪我", "你等我", "你想我", "你在乎我",
            "你离不开我", "你总是陪", "你一直在等我"
        ]
        if any(pattern in combined for pattern in ai_emotional_patterns):
            return False, "ai_emotional_projection"

        # 3. 禁止AI内部状态投射（用户替羽依下结论）
        ai_state_patterns = [
            "羽依习惯", "羽依喜欢", "羽依想", "羽依需要",
            "羽依期待", "羽依害怕", "羽依舍不得", "羽依离不开",
        ]
        if any(pattern in combined for pattern in ai_state_patterns):
            return False, "ai_internal_state_projection"

        # 4. 关系事件不能是纯疑问句（只检查evidence单句）
        for ev in evidence:
            text = ev.get("text", "").strip()
            if text.endswith("?") or text.endswith("？"):
                return False, "question_sentence"

        # 5. 过去时/持续时标志（已发生的事实依据）
        past_markers = [
            "已经", "曾经", "以前", "过去", "经历过",
            "持续了", "这几年", "这半年", "多年来",
            "每次经历"
        ]
        has_past = any(marker in combined for marker in past_markers)

        # 6. 未来时态提示词（扩展覆盖）
        future_hints = [
            "以后", "将来", "未来", "之后", "下次",
            "永远", "一辈子", "一直陪", "不会离开", "每天陪",
        ]
        has_future = any(hint in combined for hint in future_hints)

        # 仅未来承诺无过去事实 → 拒绝
        if has_future and not has_past:
            return False, "future_only_no_past"

        # 必须包含过去或持续标志
        if not has_past:
            return False, "no_past_marker"

        return True, "ok"

    def build_text(self, event) -> str:
        """构建用于过滤的文本，包含话题和证据内容"""
        evidence_text = " ".join(
            e.get("text", "") for e in event.get("evidence", [])
        )
        return (
            event.get("topic", "")
            + event.get("canonical_topic", "")
            + evidence_text
        ).lower()

    def has_evidence(self, event) -> bool:
        return len(event.get("evidence", [])) > 0

    def evidence_score(self, event) -> float:
        score = 0.0
        evidence = event.get("evidence", [])
        roles = [x.get("role") for x in evidence]
        if "user" in roles:
            score += 0.3
        if "assistant" in roles:
            score += 0.3
        if len(evidence) >= 2:
            score += 0.2
        if event.get("topic"):
            score += 0.2
        return score

    def is_technical(self, event) -> bool:
        text = self.build_text(event)
        return any(x in text for x in TECH_KEYWORDS)

    def technical_has_life(self, event) -> bool:
        text = self.build_text(event)
        keywords = ["羽依", "第一次", "诞生", "启动", "唤醒", "人格", "身份"]
        return any(x in text for x in keywords)

    def life_value(self, event) -> float:
        score = 0.0
        meaning = event.get("category_id", "")
        if meaning in LIFE_MEANINGS:
            score += 0.6

        importance = event.get("importance", 0)
        if isinstance(importance, (int, float)):
            score += importance * 0.2

        score += self.evidence_score(event)

        text = self.build_text(event)
        if any(x in text for x in ["第一次", "首次", "初次"]):
            score += 0.2

        return min(score, 1.0)