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
from src.memory.memory_provider import MemoryProvider
from src.memory.event_memory import EventMemory
from src.memory.memory_relevance_evaluator import MemoryRelevanceEvaluator



class MemorySystem:


    def __init__(self):

        # Phase 4.4.2 Memory Authority 收口：优先通过 RuntimeBridge 获取共享实例
        self.store = None
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge
            _bridge = get_runtime_bridge()
            _shared_store = _bridge.get_memory_store()
            if _shared_store is not None:
                self.store = _shared_store
        except Exception:
            pass

        # Fallback：RuntimeBridge 不可用时 → MemoryProvider 共享单例
        if self.store is None:
            # Phase 4.0-R2.3.1: 不再 MemoryStore() 自建，改为 Authority Provider 单例
            self.store = MemoryProvider.get_store()

        # Phase 4.4.2 VectorMemory Authority 收口：优先通过 RuntimeBridge 获取共享实例
        self.vector = None
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge
            _bridge = get_runtime_bridge()
            _shared_vm = _bridge.get_vector_memory()
            if _shared_vm is not None:
                self.vector = _shared_vm
        except Exception:
            pass

        # Fallback：RuntimeBridge 不可用时自建
        if self.vector is None:
            try:
                from src.memory.vector import VectorMemory
                self.vector = VectorMemory()
            except Exception:
                class _NullVectorMemory:
                    def search(self, query, top_k=5):
                        return []
                self.vector = _NullVectorMemory()

        self.event = EventMemory()

        self.identity = IdentityMemory()

        self.relevance_evaluator = MemoryRelevanceEvaluator()



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


        ranked_pool = self._rank_pool(
            pool,
            query=query,
            top_k=max(top_k * 3, top_k),
        )



        result=[]

        seen=set()



        for item in ranked_pool:


            content=item["content"]


            key=str(content)



            if key in seen:

                continue



            seen.add(key)


            result.append(content)



            if len(result)>=top_k:

                break



        # ── Phase 7.2: Cognitive Trace hook（只读, 不改 result）──
        try:
            from src.runtime.observer.cognitive_hooks import emit_memory_retrieved

            # 汇总来源统计
            sources_count = {"identity": 0, "event": 0, "semantic": 0, "chat": 0}
            scores = []
            for item in ranked_pool:
                t = item.get("type", "")
                s = item.get("score", 0)
                if t == "identity":
                    sources_count["identity"] += 1
                elif t == "event":
                    sources_count["event"] += 1
                elif t == "semantic":
                    sources_count["semantic"] += 1
                elif t == "chat":
                    sources_count["chat"] += 1
                if isinstance(s, (int, float)) and s > 0:
                    scores.append(float(s))

            max_score = max(scores) if scores else 0.0
            min_score = min(scores) if scores else 0.0
            score_range = [min_score, max_score] if scores else []

            emit_memory_retrieved(
                query=str(query),
                top_k=int(top_k),
                result_count=len(result),
                memory_ids=[str(r.get("id", "")) if isinstance(r, dict) else "" for r in result],
                sources=sources_count,
                score_range=score_range,
                max_score=max_score,
            )
        except Exception:  # noqa: BLE001
            pass

        return result

    def _rank_pool(
        self,
        pool: List[Dict],
        query: str,
        top_k: int,
    ) -> List[Dict]:
        """将旧检索池映射到统一 relevance evaluator。"""
        candidates: List[Dict] = []
        for item in pool:
            original = item.get("content")
            score = float(item.get("score", 0) or 0)
            normalized = {
                "_original_content": original,
                "_legacy_type": item.get("type", ""),
                "_vector_relevance": min(1.0, score / 1000.0),
                "score": score,
            }

            if isinstance(original, dict):
                normalized.update(dict(original))
                if "content" not in normalized:
                    for key in ("text", "memory_summary", "event", "topic", "canonical_topic"):
                        if original.get(key):
                            normalized["content"] = original.get(key)
                            break
            else:
                normalized["content"] = str(original)

            if "memory_class" not in normalized:
                legacy_type = str(item.get("type", "")).strip().lower()
                mapped = {
                    "identity": "identity",
                    "event": "event",
                    "semantic": "semantic",
                    "chat": "event",
                }.get(legacy_type, legacy_type or "unknown")
                normalized["memory_class"] = mapped

            if "importance" not in normalized:
                normalized["importance"] = min(1.0, max(0.0, score / 100.0))

            candidates.append(normalized)

        ranked = self.relevance_evaluator.rank_memories(
            candidates,
            query=query,
            context={
                "identity_focus": self._need_identity(query),
                "relationship_focus": "关系" in query or "我们" in query,
                "current_emotion": "",
            },
            top_k=top_k,
        )

        out: List[Dict] = []
        for item in ranked:
            out.append({
                "type": item.get("_legacy_type", item.get("memory_class", "unknown")),
                "content": item.get("_original_content", item.get("content")),
                "score": item.get("memory_relevance", item.get("score", 0)),
                "retrieval_priority": item.get("retrieval_priority", "low"),
                "relevance_audit_id": item.get("relevance_audit_id", ""),
            })
        return out

    def get_relevance_history(self, limit: int = 50):
        return [item.to_dict() for item in self.relevance_evaluator.get_history(limit=limit)]

    def get_relevance_snapshot(self):
        return self.relevance_evaluator.get_snapshot().to_dict()




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
