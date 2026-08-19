"""
羽依统一记忆系统 V2

负责：

- 核心身份记忆
- 用户身份
- 长期记忆
- 人生事件
- 语义检索
- 对话历史
- Context生成

"""


from typing import Dict, Optional, List
from datetime import datetime


from src.memory.identity_memory import IdentityMemory
from src.memory.memory_store import MemoryStore
from src.memory.vector import VectorMemory
from src.memory.event_memory import EventMemory



class MemorySystem:


    def __init__(self):

        self.store = MemoryStore()

        self.vector = VectorMemory()

        self.event = EventMemory()

        self.identity = IdentityMemory()



    # ===============================
    # 身份
    # ===============================


    def get_identity(self):

        try:

            return {
                "type":"identity",
                "content":
                self.identity.get_identity_prompt(),
                "score":1000
            }

        except Exception:

            return None



    def _need_identity(self,query):

        keys=[
            "我是谁",
            "你是谁",
            "清清是谁",
            "羽依是谁",
            "名字",
            "关系",
            "记得我",
            "我们的事情"
        ]

        return any(
            k in query
            for k in keys
        )



    # ===============================
    # 用户ID统一
    # ===============================


    def _users(self,user_id):

        table={

            "terminal_user":"366648462",

            "366648462":"terminal_user"

        }


        return [
            user_id,
            table.get(user_id)
        ]



    # ===============================
    # 写入
    # ===============================


    def add(
        self,
        user_id:str,
        role:str,
        content:str,
        metadata:Optional[Dict]=None
    ):


        try:

            self.store.add(

                user_id=user_id,

                role=role,

                content=content,

                metadata=metadata or {}

            )


        except Exception as e:

            print(
                "Memory add error:",
                e
            )



    # ===============================
    # 搜索
    # ===============================


    def search(
        self,
        user_id:str,
        query:str,
        top_k:int=5
    ):


        pool=[]



        # ----------
        # 身份
        # ----------

        if self._need_identity(query):

            identity=self.get_identity()

            if identity:

                pool.append(identity)



        # ----------
        # 人生事件
        # ----------

        try:

            for e in self.event.search(
                query,
                limit=5
            ):

                pool.append({

                    "type":"event",

                    "content":e,

                    "score":300

                })


        except Exception:

            pass



        # ----------
        # 向量
        # ----------

        try:

            for v in self.vector.search(
                query,
                top_k
            ):

                pool.append({

                    "type":"semantic",

                    "content":v,

                    "score":100

                })


        except Exception:

            pass




        # ----------
        # 普通记忆
        # ----------


        try:

            users=self._users(user_id)

            memories=self.store.load()


            for mem in memories:


                if mem.get(
                    "user_id"
                ) not in users:

                    continue


                text=mem.get(
                    "content",
                    ""
                )


                if not text:

                    continue



                score=0



                # 中文关键词

                if query in text:

                    score+=50



                for word in query:

                    if word.strip() and word in text:

                        score+=3



                # 最近记忆加权

                try:

                    t=datetime.fromisoformat(
                        mem.get(
                            "timestamp"
                        )
                    )

                    days=(
                        datetime.now()-t
                    ).days


                    score += max(
                        0,
                        30-days/30
                    )


                except:

                    pass



                if score>5:

                    pool.append({

                        "type":"chat",

                        "content":mem,

                        "score":score

                    })


        except Exception as e:

            print(
                "search error:",
                e
            )




        # ===============================
        # 排序
        # ===============================


        pool.sort(
            key=lambda x:x["score"],
            reverse=True
        )



        result=[]

        seen=set()



        for item in pool:


            content=item["content"]


            key=str(content)



            if key in seen:

                continue



            seen.add(key)


            result.append(content)



            if len(result)>=top_k:

                break



        return result




    # ===============================
    # 最近聊天
    # ===============================


    def get_recent(
        self,
        user_id,
        limit=5
    ):

        try:

            return self.store.get_recent(
                user_id,
                limit
            )

        except:

            return []




    # ===============================
    # 给LLM生成上下文
    # ===============================


    def build_context(
        self,
        user_id,
        query
    ):


        memories=self.search(
            user_id,
            query,
            8
        )


        text=""

        if memories:

            text+="【羽依记得】\n"


            for m in memories:

                if isinstance(
                    m,
                    dict
                ):

                    text+=(
                        "- "
                        +
                        m.get(
                            "content",
                            ""
                        )
                        +
                        "\n"
                    )

                else:

                    text+=(
                        "- "
                        +
                        str(m)
                        +
                        "\n"
                    )


        return text



    # ===============================
    # 事件刷新
    # ===============================


    def refresh_events(self):

        try:

            self.event.refresh()

        except:

            pass