"""
MemoryGate —— 记忆入口过滤器

职责：
用户消息 → Extractor → Verifier → 过滤可长期保存的记忆
只有通过审核的记忆才能进入长期记忆系统
"""

from src.memory.memory_extractor import MemoryExtractor
from src.memory.memory_verifier import MemoryVerifier


class MemoryGate:
    def __init__(self):
        self.extractor = MemoryExtractor()
        self.verifier = MemoryVerifier()

    def process(self, user_message: str):
        raw = {"role": "user", "content": user_message}
        candidates = self.extractor.extract([raw])
        result = []

        for candidate in candidates:
            if not hasattr(candidate, "to_dict"):
                continue
            try:
                memory = self.verifier.verify(candidate.to_dict())
                if (
                    memory.get("truth", 0) > 0
                    and any(
                        usage in memory.get("usage", [])
                        for usage in ["conversation", "persona", "growth"]
                    )
                ):
                    print(
                        f"[MemoryGate] accepted: "
                        f"{memory.get('memory_class')} | "
                        f"{memory.get('content')}"
                    )
                    result.append(memory)
            except Exception as e:
                print(f"[MemoryGate] verify failed: {e}")

        return result