import json
import os
from pathlib import Path
from typing import List, Optional

from src.growth.proposal.proposal import GrowthProposal
from src.growth.proposal.constants import PROPOSAL_STATUS, MAX_PROPOSALS


class ProposalStorage:
    def __init__(self, data_dir: str = "data/growth/proposals"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.json_file = self.data_dir / "proposals.json"

        if not self.json_file.exists():
            self._init_json_file()

    def _init_json_file(self):
        with open(self.json_file, "w", encoding="utf-8") as f:
            json.dump({"version": "1.0", "proposals": []}, f, ensure_ascii=False, indent=2)

    def save(self, proposal: GrowthProposal):
        try:
            with open(self.json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            proposals = data.get("proposals", [])
            found = False
            for i, p in enumerate(proposals):
                if p["proposal_id"] == proposal.proposal_id:
                    proposals[i] = proposal.to_dict()
                    found = True
                    break

            if not found:
                proposals.append(proposal.to_dict())

            if len(proposals) > MAX_PROPOSALS:
                proposals = proposals[-MAX_PROPOSALS:]

            data["proposals"] = proposals

            with open(self.json_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ProposalStorage] 保存失败: {e}")

    def load(self, proposal_id: str) -> Optional[GrowthProposal]:
        try:
            with open(self.json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for p in data.get("proposals", []):
                if p["proposal_id"] == proposal_id:
                    return GrowthProposal.from_dict(p)
            return None
        except Exception as e:
            print(f"[ProposalStorage] 加载失败: {e}")
            return None

    def list_all(self, limit: int = 50, offset: int = 0) -> List[GrowthProposal]:
        try:
            with open(self.json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            proposals = data.get("proposals", [])
            proposals.sort(key=lambda x: x["timestamp"], reverse=True)

            start = offset
            end = start + limit
            return [GrowthProposal.from_dict(p) for p in proposals[start:end]]
        except Exception as e:
            print(f"[ProposalStorage] 列表加载失败: {e}")
            return []

    def list_by_status(self, status: str, limit: int = 50) -> List[GrowthProposal]:
        all_proposals = self.list_all(limit=1000)
        return [p for p in all_proposals if p.status == status][:limit]

    def list_by_type(self, proposal_type: str, limit: int = 50) -> List[GrowthProposal]:
        all_proposals = self.list_all(limit=1000)
        return [p for p in all_proposals if p.proposal_type == proposal_type][:limit]

    def list_pending(self, limit: int = 50) -> List[GrowthProposal]:
        return self.list_by_status(PROPOSAL_STATUS["PENDING"], limit=limit)

    def delete(self, proposal_id: str):
        try:
            with open(self.json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            proposals = data.get("proposals", [])
            proposals = [p for p in proposals if p["proposal_id"] != proposal_id]
            data["proposals"] = proposals

            with open(self.json_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ProposalStorage] 删除失败: {e}")

    def count(self) -> int:
        try:
            with open(self.json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return len(data.get("proposals", []))
        except Exception:
            return 0


_global_storage = None


def get_proposal_storage() -> ProposalStorage:
    global _global_storage
    if _global_storage is None:
        _global_storage = ProposalStorage()
    return _global_storage


def save_proposal(proposal: GrowthProposal):
    get_proposal_storage().save(proposal)


def load_proposal(proposal_id: str) -> Optional[GrowthProposal]:
    return get_proposal_storage().load(proposal_id)


def list_pending_proposals(limit: int = 50) -> List[GrowthProposal]:
    return get_proposal_storage().list_pending(limit=limit)


def list_all_proposals(limit: int = 50) -> List[GrowthProposal]:
    return get_proposal_storage().list_all(limit=limit)