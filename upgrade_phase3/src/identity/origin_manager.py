"""
起源身份管理器 (OriginManager) — Phase 11.6 最终版
整合 Detector、Verifier、Storage 和 MemoryStore，
管理从事件检测到身份持久化的完整生命周期。
"""
from typing import Optional
from src.identity.origin_event import OriginEvent, OriginEventStatus
from src.identity.origin_identity import OriginIdentity, OriginContributor, OriginRole
from src.identity.origin_identity_detector import OriginIdentityDetector
from src.identity.origin_verifier import OriginVerifier
from src.identity.origin_storage import OriginStorage


class OriginManager:
    VALID_ROLES = {
        OriginRole.CREATOR,
        OriginRole.PERSONALITY_DESIGNER,
        OriginRole.SYSTEM_BUILDER,
        OriginRole.GROWTH_PARTICIPANT,
    }

    def __init__(
        self,
        storage: OriginStorage = None,
        memory_store=None,  # 注入 MemoryStore，统一记忆查询入口
    ):
        self.detector = OriginIdentityDetector()
        self.verifier = OriginVerifier()
        self.storage = storage or OriginStorage()
        self.memory_store = memory_store
        self._identity = self.storage.load()

    @property
    def identity(self) -> OriginIdentity:
        return self._identity

    def detect(self, user_message: str, evidence_id: str = "") -> Optional[OriginEvent]:
        return self.detector.detect(user_message, evidence_id)

    def verify(self, event: OriginEvent, existing_evidence_count: int = 0) -> OriginEvent:
        return self.verifier.verify(event, existing_evidence_count)

    def process_verified_event(self, event: OriginEvent, user_context=None) -> bool:
        if event.status != OriginEventStatus.VERIFIED:
            return False

        # 验证证据归属（通过 MemoryStore 统一查询）
        if user_context and self.memory_store:
            for eid in event.evidence_ids:
                if not self._evidence_belongs_to_user(eid, user_context.user_key):
                    return False

        valid_roles = [r for r in event.potential_roles if r in self.VALID_ROLES]
        if not valid_roles:
            return False

        for existing in self._identity.contributors:
            if existing.user_id == event.user_id:
                for eid in event.evidence_ids:
                    if eid not in existing.evidence_ids:
                        existing.evidence_ids.append(eid)
                for role in valid_roles:
                    if role not in existing.roles:
                        existing.roles.append(role)
                self.storage.save(self._identity)
                return True

        contributor = OriginContributor(
            user_id=event.user_id,
            roles=valid_roles,
            evidence_ids=event.evidence_ids,
            description=event.description,
        )
        self._identity.add_contributor(contributor)
        self.storage.save(self._identity)
        return True

    def reload(self):
        self._identity = self.storage.load()

    def _evidence_belongs_to_user(self, memory_id: str, user_key: str) -> bool:
        if self.memory_store is None:
            return True
        memory = self.memory_store.get_by_id(memory_id)
        if memory is None:
            return False
        owner = memory.get("metadata", {}).get("owner", "")
        return owner == user_key