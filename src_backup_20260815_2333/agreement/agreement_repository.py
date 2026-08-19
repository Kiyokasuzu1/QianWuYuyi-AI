"""
约定仓库 (AgreementRepository)
负责 Agreement 列表的 JSON 持久化，支持多用户隔离与临时路径注入。
"""
import json
from pathlib import Path
from typing import List, Optional
from src.agreement.agreement import Agreement
from src.identity.user_context import UserContext


class AgreementRepository:
    def __init__(self, user_context: Optional[UserContext] = None, filepath: str = None):
        if filepath:
            self.filepath = Path(filepath)
        elif user_context:
            self.filepath = Path(user_context.agreements_dir) / "agreements.json"
        else:
            self.filepath = Path("data/agreements/agreements.json")

    def load_all(self) -> List[Agreement]:
        if not self.filepath.exists():
            return []
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [Agreement.from_dict(item) for item in data]
        except Exception:
            return []

    def save_all(self, agreements: List[Agreement]):
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump([a.to_dict() for a in agreements], f, ensure_ascii=False, indent=2)