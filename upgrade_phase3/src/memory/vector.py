import json
from pathlib import Path
from typing import List, Dict, Optional

import chromadb
from chromadb.utils import embedding_functions

from src.config import get
from src.utils.text import clean_content

VECTOR_INDEX_VERSION = "0.3.0"


class VectorMemory:
    def __init__(self):
        self.embedding_model_name = get("memory.embedding_model", "BAAI/bge-small-zh-v1.5")
        self.chroma_path = Path(get("memory.chroma_path", "data/chroma_db"))
        self.target_user_id = get("memory.target_user_id", "366648462")
        self.search_top_k = get("memory.search_top_k", 8)
        self.min_relevance = get("memory.min_relevance", 0.3)

        self.chroma_path.mkdir(parents=True, exist_ok=True)

        self.chroma_client = chromadb.PersistentClient(path=str(self.chroma_path))
        self.status_file = self.chroma_path / "index_status.json"

        self._check_and_rebuild()

        self.collection = self._get_or_create_collection()
        self._indexed_count = self.collection.count()

    def _get_status(self) -> Dict:
        if self.status_file.exists():
            with open(self.status_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"version": None, "indexed": False, "count": 0}

    def _set_status(self, indexed: bool, count: int = 0):
        status = {
            "version": VECTOR_INDEX_VERSION,
            "indexed": indexed,
            "count": count,
            "updated": str(Path(__file__).stat().st_mtime)
        }
        with open(self.status_file, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2)

    def _check_and_rebuild(self):
        """检查版本和索引状态，决定是否重建"""
        status = self._get_status()
        need_rebuild = False

        if status.get("version") != VECTOR_INDEX_VERSION:
            need_rebuild = True
        elif not status.get("indexed", False):
            need_rebuild = True

        if need_rebuild:
            self._clear_all()
            self._set_status(indexed=False)

    def _clear_all(self):
        try:
            self.chroma_client.delete_collection("yuyi_memories")
        except:
            pass

    def _get_or_create_collection(self):
        try:
            return self.chroma_client.get_collection("yuyi_memories")
        except:
            return self.chroma_client.create_collection(
                name="yuyi_memories",
                embedding_function=embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=self.embedding_model_name
                )
            )

    def index_memories(self, memories: List[Dict], target_user_id: str = None):
        target = target_user_id or self.target_user_id

        new_count = 0
        for mem in memories:
            mem_id = mem.get("id")
            if not mem_id:
                continue
            if mem.get("user_id") != target:
                continue
            if mem.get("role") != "user":
                continue

            content = clean_content(mem.get("content", ""))
            if not content or len(content) < 5:
                continue

            try:
                self.collection.upsert(
                    documents=[content],
                    metadatas=[{
                        "id": mem_id,
                        "role": "user",
                        "timestamp": mem.get("timestamp", "")
                    }],
                    ids=[mem_id]
                )
                new_count += 1
            except Exception as e:
                print(f"Indexing failed (mem_{mem_id}): {e}")

        if new_count > 0:
            print(f"✅ 向量索引 {new_count} 条用户记忆")
            self._indexed_count = self.collection.count()

    def mark_index_complete(self):
        """✅ 标记索引完成（全量扫描后调用）"""
        self._set_status(indexed=True, count=self.collection.count())
        print(f"✅ 索引状态已标记为完成，共 {self.collection.count()} 条")

    def add_memory(self, memory: Dict):
        if memory.get("role") != "user":
            return

        mem_id = memory.get("id")
        if not mem_id:
            return

        content = clean_content(memory.get("content", ""))
        if not content or len(content) < 5:
            return

        try:
            self.collection.upsert(
                documents=[content],
                metadatas=[{
                    "id": mem_id,
                    "role": "user",
                    "timestamp": memory.get("timestamp", "")
                }],
                ids=[mem_id]
            )
            self._indexed_count = self.collection.count()
            self._set_status(indexed=True, count=self._indexed_count)
        except Exception as e:
            print(f"⚠️ 添加向量索引失败: {e}")

    def search(self, query: str, top_k: int = None) -> List[Dict]:
        if self.collection.count() == 0:
            return []

        query = clean_content(query)
        if not query:
            return []

        top_k = top_k or self.search_top_k

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=min(top_k * 2, self.collection.count())
            )
        except Exception as e:
            print(f"Search failed: {e}")
            return []

        if not results or not results['documents']:
            return []

        scored = []
        for i in range(len(results['documents'][0])):
            doc = results['documents'][0][i]
            meta = results['metadatas'][0][i]
            distance = results['distances'][0][i] if results.get('distances') else 1.0

            relevance = 1.0 - distance
            if relevance < self.min_relevance:
                continue
            scored.append({
                "content": doc,
                "role": meta.get("role", "user"),
                "timestamp": meta.get("timestamp", ""),
                "relevance": relevance,
                "mem_id": meta.get("id", -1)
            })

        scored.sort(key=lambda x: x['relevance'], reverse=True)
        return scored[:top_k]

    def count(self) -> int:
        return self.collection.count()

    def clear(self):
        self.chroma_client.delete_collection("yuyi_memories")
        self.collection = self._get_or_create_collection()
        self._indexed_count = 0
        self._set_status(indexed=False, count=0)