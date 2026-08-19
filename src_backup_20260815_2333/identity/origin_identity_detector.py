"""
起源身份检测器 (OriginIdentityDetector) — Phase 11 最终版
从用户消息中检测可能形成起源身份的信号。
只生成候选事件（status=candidate），不直接写入 OriginIdentity。
"""
import hashlib
from typing import Optional
from src.identity.origin_event import OriginEvent, OriginEventStatus
from src.identity.origin_identity import OriginRole


class OriginIdentityDetector:
    # 角色信号关键词，不同角色初始置信度（信号强度）不同
    ROLE_SIGNALS = {
        OriginRole.CREATOR: (
            ["创建了", "诞生了", "第一个版本", "最初的想法", "一开始就",
             "从零开始", "项目启动", "概念的提出"],
            0.35
        ),
        OriginRole.PERSONALITY_DESIGNER: (
            ["人格设计", "设计人格", "人格架构", "人格系统",
             "性格设计", "价值观", "交流风格", "成长理念",
             "设定", "身份定义", "浅雾羽依"],
            0.30
        ),
        OriginRole.SYSTEM_BUILDER: (
            ["开发了", "架构设计", "系统设计", "系统架构", "Memory模块",
             "Growth系统", "Emotion模块", "代码实现", "模块搭建", "实现了"],
            0.30
        ),
        OriginRole.GROWTH_PARTICIPANT: (
            ["陪伴", "长期反馈", "一直参与", "迭代优化", "改进建议",
             "成长陪伴", "演化测试", "优化了", "测试反馈"],
            0.20
        ),
    }

    # 明确的完成/参与时态的贡献短语
    CONTRIBUTION_PHRASES = [
        "设计了", "开发了", "创建了", "提出了", "实现了",
        "搭建了", "参与了", "编写了", "定义了", "贡献了",
        "完成了", "建立了", "给出了", "提供了",
    ]

    # 学习/研究/阅读行为关键词，直接拒绝（避免兴趣被误判为贡献）
    LEARNING_KEYWORDS = [
        "学习", "研究", "了解", "阅读", "参考", "看了一篇", "看了一本",
        "正在学", "想学", "准备学", "了解下", "看看", "查资料",
    ]

    # 兴趣表达词，如果无明确的贡献短语则拒绝
    INTEREST_PATTERNS = [
        "觉得", "感觉", "有意思", "感兴趣", "挺有趣", "不错",
    ]

    # 否定表述，直接拒绝
    NEGATION_PATTERNS = [
        "没有参与", "不是我", "只是看看", "不了解", "没做过",
        "不是我做的", "不是我写的", "我只是看", "没参与",
    ]

    FORBIDDEN_TRIGGERS = [
        "你是我", "最爱", "离不开", "唯一", "只有你",
    ]

    def detect(self, user_message: str, evidence_id: str = "") -> Optional[OriginEvent]:
        if not user_message or len(user_message.strip()) < 5:
            return None

        # 1. 拦截情感化表达
        for trigger in self.FORBIDDEN_TRIGGERS:
            if trigger in user_message:
                return None

        # 2. 否定表述拦截
        for neg in self.NEGATION_PATTERNS:
            if neg in user_message:
                return None

        # 3. 学习/研究行为直接忽略
        if any(kw in user_message for kw in self.LEARNING_KEYWORDS):
            return None

        # 4. 检查是否有明确的贡献动作
        has_contribution = any(phrase in user_message for phrase in self.CONTRIBUTION_PHRASES)
        
        # 5. 如果存在兴趣表达且无明确贡献动作，拒绝
        has_interest = any(pattern in user_message for pattern in self.INTEREST_PATTERNS)
        if has_interest and not has_contribution:
            return None

        # 6. 必须有明确的贡献动作才会继续
        if not has_contribution:
            return None

        # 7. 匹配角色关键词
        detected_roles = []
        max_confidence = 0.0
        for role, (keywords, base_conf) in self.ROLE_SIGNALS.items():
            if any(kw in user_message for kw in keywords):
                detected_roles.append(role)
                if base_conf > max_confidence:
                    max_confidence = base_conf

        if not detected_roles:
            return None

        # 生成稳定 event_id
        event_id = "origin_evt_" + hashlib.sha256(
            user_message.encode("utf-8")
        ).hexdigest()[:12]

        return OriginEvent(
            event_id=event_id,
            event_type="origin_signal",
            description=user_message[:200],
            evidence_ids=[evidence_id] if evidence_id else [],
            potential_roles=detected_roles,
            confidence=max_confidence,
            status=OriginEventStatus.CANDIDATE,
        )