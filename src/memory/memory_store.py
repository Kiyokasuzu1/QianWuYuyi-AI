"""
MemoryStore v2.3

长期记忆存储层

Phase 11.6 Final:
- UserContext 用户隔离
- user_key owner 绑定
- 外部不可覆盖 owner
- 兼容旧版 path 调用

Phase C.2.3:
- 集成 PollutionGuard 防止污染数据进入 memory.json
"""

import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime
from typing import List, Dict, Optional, Union

from src.identity.user_context import UserContext
from src.memory.atomic_write import atomic_write_json

logger = logging.getLogger(__name__)

try:
    from src.memory.pollution_guard import check as _pollution_check
    _POLLUTION_GUARD_AVAILABLE = True
except Exception:  # pragma: no cover
    _POLLUTION_GUARD_AVAILABLE = False
    _pollution_check = None


# ==========================
# Phase 2.2: 按路径写锁
# ==========================
# 同一路径的所有 MemoryStore 实例共享一把可重入锁,
# 使 add/delete/clear 的「读-改-写」序列互斥,避免并发互相覆盖。
_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: str) -> threading.RLock:
    key = os.path.abspath(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


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
        """写入一条记忆。

        Phase 4.0-R2.4.2: 支持 3 种调用契约（向后兼容 + kwargs 修复）：

        1. add(memory_dict)                       → 推荐：直接传 dict record
        2. add(user_id, content, role, metadata)  → 旧位置参数格式
        3. add(user_id=..., content=..., ...)     → kwargs 格式（YuyiCore / MemorySystem 在用）

        不支持：args + kwargs 混用（直接返回 None，防止歧义）。
        """

        memory = None

        # Phase 4.0-R2.4.2: kwargs 格式（MemorySystem / YuyiCore 在用，之前静默失败）
        # 优先判断：只有 kwargs、没有 args 时，把 kwargs 本身当作 memory dict。
        # 这样 MemorySystem.add() 的 self.store.add(user_id=..., role=..., content=..., metadata=...)
        # 以及 YuyiCore.chat() 的 self.memory.add(user_id=..., ...) 都能正确写入。
        if len(args) == 0 and kwargs:
            memory = dict(kwargs)

        # 新格式:
        # add(memory_dict)
        elif len(args) == 1 and isinstance(args[0], dict) and not kwargs:
            memory = dict(args[0])

        # 旧格式:
        # add(user_id, content, role, metadata)
        elif len(args) >= 2 and not kwargs:
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
                "metadata": dict(metadata),
            }

        else:
            # 不支持的调用格式（如 args+kwargs 混用），防止歧义写入
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

        # ==========================
        # Phase C.2.3: PollutionGuard 入口校验
        # ==========================
        if _POLLUTION_GUARD_AVAILABLE and _pollution_check is not None:
            try:
                allowed, reason = _pollution_check(memory)
                if not allowed:
                    logger.info(
                        "[MemoryStore] PollutionGuard rejected memory: %s | id=%s",
                        reason,
                        memory.get("id", "<no-id>"),
                    )
                    return None
            except Exception as _pg_exc:  # noqa: BLE001
                # PollutionGuard 异常不应阻塞主流程,但要记录
                logger.warning("[MemoryStore] PollutionGuard 异常(已隔离): %s", _pg_exc)



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



        with _path_lock(self.path):

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

        with _path_lock(self.path):

            try:

                with open(
                    self.path,
                    "r",
                    encoding="utf-8"
                ) as f:

                    data = json.load(f)


                    if isinstance(data,list):

                        return data


                logger.error(
                    "[MemoryStore] load failed (corrupt format): %s: top-level is not a list",
                    self.path,
                )


            except FileNotFoundError:

                # 文件不存在 = 正常初始态(构造函数会主动创建),不视为损坏

                pass


            except Exception as exc:

                logger.error(
                    "[MemoryStore] load failed (corrupt file): %s: %s",
                    self.path,
                    exc,
                )

                self._backup_corrupt_file()


            else:

                self._backup_corrupt_file()


        return []


    def _backup_corrupt_file(self) -> None:
        """损坏文件留底:复制为 memory.json.corrupt.<timestamp>。

        用复制而非移动:原文件保持原地不动,避免干扰可能正在写入该文件的
        并发方;覆盖只发生在下一次合法的原子 _save,届时损坏现场已留底,
        历史数据不会静默丢失。
        """

        try:

            backup_path = (
                f"{self.path}.corrupt."
                f"{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
            )

            shutil.copy2(self.path, backup_path)

            logger.warning(
                "[MemoryStore] corrupt file backed up: %s -> %s",
                self.path,
                backup_path,
            )

        except Exception as exc:

            logger.error(
                "[MemoryStore] corrupt file backup failed: %s: %s",
                self.path,
                exc,
            )



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

        with _path_lock(self.path):

            memories = [

                m for m in self.load()

                if m.get("id") != memory_id

            ]

            self._save(memories)



    def clear(self):

        with _path_lock(self.path):

            self._save([])



    # ==========================
    # 持久化
    # ==========================

    def _save(
        self,
        data
    ):

        try:

            with _path_lock(self.path):

                atomic_write_json(
                    self.path,
                    data,
                )


        except Exception as e:

            logger.error(
                "[MemoryStore] save failed: %s",
                e
            )