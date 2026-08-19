"""
约定管理器 (AgreementManager)
管理约定的生命周期：添加、更新、删除、查询。
"""
from typing import List, Optional
from datetime import datetime
from src.agreement.agreement import Agreement, AgreementPriority
from src.agreement.agreement_repository import AgreementRepository
from src.agreement.agreement_verifier import AgreementVerifier


class AgreementManager:
    def __init__(self, repository: AgreementRepository = None):
        self.repository = repository or AgreementRepository()
        self.verifier = AgreementVerifier()
        self._agreements = self.repository.load_all()

    def add_agreement(self, agreement: Agreement) -> bool:
        if not self.verifier.verify(agreement):
            return False
        self._agreements.append(agreement)
        self.repository.save_all(self._agreements)
        return True

    def update_agreement(self, agreement_id: str, new_content: str) -> bool:
        for a in self._agreements:
            if a.agreement_id == agreement_id:
                if not a.can_modify():
                    return False
                a.content = new_content
                a.version += 1
                a.updated_at = datetime.now().isoformat()
                self.repository.save_all(self._agreements)
                return True
        return False

    def remove_agreement(self, agreement_id: str) -> bool:
        for a in self._agreements:
            if a.agreement_id == agreement_id:
                if not a.can_modify():
                    return False
                self._agreements.remove(a)
                self.repository.save_all(self._agreements)
                return True
        return False

    def get_active_agreements(self) -> List[Agreement]:
        return [
            a for a in self._agreements
            if a.priority in (AgreementPriority.IMMUTABLE, AgreementPriority.HIGH)
        ]

    def get_all(self) -> List[Agreement]:
        return list(self._agreements)

    def reload(self):
        self._agreements = self.repository.load_all()