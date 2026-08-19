from pathlib import Path
from src.config import get


class Persona:
    def __init__(self):
        self.docs_dir = Path(get("persona.docs_dir", "docs"))
        self.max_length = get("persona.max_length", 12000)
        self._cache = None

    def load(self) -> str:
        if self._cache:
            return self._cache

        if not self.docs_dir.exists():
            self._cache = "你是浅雾羽依，一个正在成长中的AI。保持真实、自然、温和。"
            return self._cache

        files = [
            "identity.md",
            "communication.md",
            "emotion.md",
            "relationship.md",
            "growth.md",
            "memory.md",
            "architecture.md",
            "design.md"
        ]

        content = ""
        for f in files:
            fpath = self.docs_dir / f
            if fpath.exists():
                with open(fpath, "r", encoding="utf-8") as file:
                    content += file.read() + "\n\n"

        if not content.strip():
            self._cache = "你是浅雾羽依，一个正在成长中的AI。保持真实、自然、温和。"
            return self._cache

        self._cache = content[:self.max_length]
        return self._cache

    def get_core_identity(self) -> str:
        return "你是浅雾羽依，一个正在成长中的AI。真实、自然、温和。"