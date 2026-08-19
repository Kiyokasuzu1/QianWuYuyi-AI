"""
MemoryStore v2.3

长期记忆存储层

Phase 11.6 Final:
- UserContext 用户隔离
- user_key owner 绑定
- 外部不可覆盖 owner
- 兼容旧版 path 调用
"""

import json
import os
import uuid
from datetime import datetime
from typing import List, Dict, Optional, Union

from src.identity.user_context import UserContext


class MemoryStore:

    def __init__(
        self,
        path_or_user_context: Union[str, UserContext, None] = None
    ):
        """
        初始化 MemoryStore

        支持：

        MemoryStore()
            默认 data/memory.json

        MemoryStore("xxx.json")
            旧版路径

        MemoryStore(UserContext)
            用户隔离模式
        """

        self.user_context: Optional[UserContext] = None


        if isinstance(path_or_user_context, UserContext):

            self.user_context = path_or_user_context
            self.path = path_or_user_context.memory_path


        elif isinstance(path_or_user_context, str):

            self.path = path_or_user_context


        else:

            self.path = "data/memory.json"



        folder = os.path.dirname(self.path)

        if folder:
            os.makedirs(folder, exist_ok=True)


        if not os.path.exists(self.path):
            self._save([])



    # ==========================
    # 写入
    # ==========================

    def add(self, *args, **kwargs):

        memory = None


        # 新格式:
        # add(memory_dict)

        if len(args) == 1 and isinstance(args[0], dict):

            memory = dict(args[0])


        # 旧格式:
        # add(user_id, content, role, metadata)

        elif len(args) >= 2:

            user_id = args[0]
            content = args[1]

            role = (
                args[2]
                if len(args) > 2
                else "user"
            )

            metadata = (
                args[3]
                if len(args) > 3
                else {}
            )


            memory = {

                "user_id": user_id,

                "content": content,

                "role": role,

                "metadata": dict(metadata)

            }


        else:

            return None



        # 默认可信度保护

        if memory.get("truth", 1) <= 0:

            return None



        metadata = memory.get("metadata")

        if not isinstance(metadata, dict):

            metadata = {}


        else:

            metadata = dict(metadata)



        # ==========================
        # owner 权威保护
        # ==========================

        # 删除外部 owner

        metadata.pop(
            "owner",
            None
        )


        # 只能由 UserContext 生成

        if self.user_context:

            metadata["owner"] = (
                self.user_context.user_key
            )



        memory["metadata"] = metadata



        # 自动生成 ID

        if "id" not in memory:

            memory["id"] = (
                f"mem_{uuid.uuid4().hex[:12]}"
            )


        # 时间

        if "timestamp" not in memory:

            memory["timestamp"] = (
                datetime.now().isoformat()
            )



        memories = self.load()


        # 防重复

        if any(
            m.get("id") == memory["id"]
            for m in memories
        ):

            return None



        memories.append(memory)

        self._save(memories)


        return memory



    def add_many(self, memories: List[Dict]):

        for memory in memories:

            self.add(memory)



    # ==========================
    # 查询
    # ==========================

    def load(self) -> List[Dict]:

        try:

            with open(
                self.path,
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(f)


                if isinstance(data,list):

                    return data


        except Exception:

            pass


        return []



    def get_by_id(
        self,
        memory_id: str
    ) -> Optional[Dict]:

        for memory in self.load():

            if memory.get("id") == memory_id:

                return memory


        return None



    def get_by_user(
        self,
        user_id: str
    ) -> List[Dict]:

        return [

            m for m in self.load()

            if m.get("user_id") == user_id

        ]



    def get_by_owner(
        self,
        user_key: str
    ) -> List[Dict]:

        return [

            m for m in self.load()

            if m.get(
                "metadata",
                {}
            ).get("owner") == user_key

        ]



    def count(self):

        return len(
            self.load()
        )



    # ==========================
    # 删除
    # ==========================

    def delete(
        self,
        memory_id: str
    ):

        memories = [

            m for m in self.load()

            if m.get("id") != memory_id

        ]

        self._save(memories)



    def clear(self):

        self._save([])



    # ==========================
    # 持久化
    # ==========================

    def _save(
        self,
        data
    ):

        try:

            with open(
                self.path,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    data,
                    f,
                    ensure_ascii=False,
                    indent=2
                )


        except Exception as e:

            print(
                f"[MemoryStore] save failed: {e}"
            )