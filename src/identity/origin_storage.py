"""
起源身份存储 (OriginStorage)
负责 OriginIdentity 的 JSON 持久化。
只负责读写，不负责判断或修改身份。
"""
import json
import shutil
import time
from pathlib import Path

from src.identity.origin_identity import OriginIdentity


class OriginStorage:
    def __init__(self, filepath: str = "data/identity/origin_identity.json"):
        self.filepath = Path(filepath)

    def save(self, identity: OriginIdentity) -> bool:
        """保存起源身份到文件，返回是否成功。

        V1.0-OPT: 原子写（临时文件 + os.replace），避免截断窗口毁掉身份来源数据。
        数据格式不变。
        """
        try:
            from src.memory.atomic_write import atomic_write_json
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(str(self.filepath), identity.to_dict())
            return True
        except Exception as e:
            print(f"⚠️ 保存起源身份失败: {e}")
            return False

    def load(self) -> OriginIdentity:
        """
        从文件加载起源身份。
        文件不存在时返回空 OriginIdentity。
        文件损坏时返回空 OriginIdentity 并记录错误。
        V1.0-OPT: 损坏时先备份原文件（copy2，不删除），供人工恢复。
        """
        if not self.filepath.exists():
            return OriginIdentity()

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return OriginIdentity.from_dict(data)
        except Exception as e:
            print(f"⚠️ 加载起源身份失败，返回空身份: {e}")
            try:
                _backup = f"{self.filepath}.corrupt.{time.strftime('%Y%m%dT%H%M%S')}"
                shutil.copy2(str(self.filepath), _backup)
                print(f"⚠️ 损坏的起源身份文件已备份为 {_backup}")
            except Exception:
                pass
            return OriginIdentity()