from pathlib import Path
from src.config import get


class Persona:
    def __init__(self):
        self.docs_dir = Path(get("persona.docs_dir", "docs"))
        self.max_length = get("persona.max_length", 12000)
        self._cache = None

    @staticmethod
    def _default_identity_text() -> str:
        """兜底身份文案 —— Phase 4.0.2-P1：由 IDENTITY_CORE 驱动（章程阶段一）。
        局部导入避免循环依赖。"""
        from src.personality.identity_core import IDENTITY_CORE
        return f"你是{IDENTITY_CORE.get('name', '浅雾羽依')}。{IDENTITY_CORE.get('essence', '')}"

    def load(self) -> str:
        if self._cache:
            return self._cache

        if not self.docs_dir.exists():
            self._cache = self._default_identity_text()
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
            self._cache = self._default_identity_text()
            return self._cache

        self._cache = content[:self.max_length]
        return self._cache

    def get_core_identity(self) -> str:
        return self._default_identity_text()