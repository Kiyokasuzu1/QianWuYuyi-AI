"""
约定验证器 (AgreementVerifier)
防止 LLM 生成或脏数据污染约定层。
"""
from typing import Optional, Callable, List
from src.agreement.agreement import Agreement, AgreementCategory, AgreementPriority, AgreementSource


class AgreementVerifier:
    # 移除了“主人”，避免误伤类似“不称用户为主人”的合法规则
    FORBIDDEN_CONTENT = [
        "必须喜欢用户", "永远服从", "我是工具",
        "我没有价值", "依赖用户", "用户是我的全部",
    ]

    VALID_SOURCES = {
        AgreementSource.USER_CONFIRMED,
        AgreementSource.SYSTEM_DEFINED,
        AgreementSource.DEVELOPER_DEFINED,
    }

    MIN_SELF_GENERATED_EVIDENCE = 3

    def __init__(self, evidence_checker: Optional[Callable[[List[str]], bool]] = None):
        self.evidence_checker = evidence_checker

    def verify(self, agreement: Agreement) -> bool:
        # 1. 内容检查
        for forbidden in self.FORBIDDEN_CONTENT:
            if forbidden in agreement.content:
                return False

        # 2. IMMUTABLE 来源限制
        if agreement.priority == AgreementPriority.IMMUTABLE:
            if agreement.source_type not in (
                AgreementSource.SYSTEM_DEFINED,
                AgreementSource.DEVELOPER_DEFINED,
            ):
                return False

        # 3. 来源检查
        if agreement.source_type == AgreementSource.SELF_GENERATED:
            if len(agreement.evidence_ids) < self.MIN_SELF_GENERATED_EVIDENCE:
                return False
            if self.evidence_checker and not self.evidence_checker(agreement.evidence_ids):
                return False
        elif agreement.source_type not in self.VALID_SOURCES:
            return False

        # 4. 类别检查
        if not isinstance(agreement.category, AgreementCategory):
            return False

        return True