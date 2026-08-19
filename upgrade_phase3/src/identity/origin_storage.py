"""
起源身份存储 (OriginStorage)
负责 OriginIdentity 的 JSON 持久化。
只负责读写，不负责判断或修改身份。
"""
import json
from pathlib import Path
from src.identity.origin_identity import OriginIdentity


class OriginStorage:
    def __init__(self, filepath: str = "data/identity/origin_identity.json"):
        self.filepath = Path(filepath)

    def save(self, identity: OriginIdentity) -> bool:
        """保存起源身份到文件，返回是否成功"""
        try:
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(identity.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"⚠️ 保存起源身份失败: {e}")
            return False

    def load(self) -> OriginIdentity:
        """
        从文件加载起源身份。
        文件不存在时返回空 OriginIdentity。
        文件损坏时返回空 OriginIdentity 并记录错误。
        """
        if not self.filepath.exists():
            return OriginIdentity()

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return OriginIdentity.from_dict(data)
        except Exception as e:
            print(f"⚠️ 加载起源身份失败，返回空身份: {e}")
            return OriginIdentity()