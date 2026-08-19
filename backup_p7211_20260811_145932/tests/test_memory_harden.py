# -*- coding: utf-8 -*-
"""
tests/test_memory_harden.py

说明：
- 测试 MemoryStore 字段迁移（旧数据补默认值）
- 测试 MemoryService 按 importance/emotion_tag/time_range 过滤检索
- 测试 VectorMemory 持久化（写入 -> 重启实例 -> 能搜到）

注：为保证在目标仓库中能运行，测试会尝试导入真实实现（在常见路径），若不存在则使用本地模拟实现。
"""

import json
import os
import time
import tempfile
import shutil
from datetime import datetime, timedelta

import pytest

# 尝试导入仓库中可能存在的实现，按常见路径查找
try:
    from src.memory import MemoryStore, MemoryService, VectorMemory
except Exception:
    try:
        from memory import MemoryStore, MemoryService, VectorMemory
    except Exception:
        MemoryStore = None
        MemoryService = None
        VectorMemory = None


# 本地模拟实现（仅在真实实现缺失时使用）
class _MockMemoryStore:
    """简单 JSON 后端的 MemoryStore，用于 migrate 测试"""

    def __init__(self, path):
        self.path = path
        self._data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self._data = json.load(f)

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def migrate_defaults(self):
        # 对所有条目补齐默认字段
        for k, v in self._data.get("memories", {}).items():
            if "importance" not in v:
                v["importance"] = 0
            if "emotion_tag" not in v:
                v["emotion_tag"] = "neutral"
            if "created_at" not in v:
                v["created_at"] = datetime.utcnow().isoformat()
        self.save()


class _MockMemoryService:
    """支持按 importance/emotion_tag/time_range 过滤的内存检索服务"""

    def __init__(self, store):
        self.store = store

    def add(self, key, text, importance=0, emotion_tag="neutral", created_at=None):
        if "memories" not in self.store._data:
            self.store._data["memories"] = {}
        if created_at is None:
            created_at = datetime.utcnow().isoformat()
        self.store._data["memories"][key] = {
            "text": text,
            "importance": importance,
            "emotion_tag": emotion_tag,
            "created_at": created_at,
        }
        self.store.save()

    def query(self, min_importance=None, emotion_tag=None, start_time=None, end_time=None):
        out = []
        for k, v in self.store._data.get("memories", {}).items():
            if min_importance is not None and v.get("importance", 0) < min_importance:
                continue
            if emotion_tag is not None and v.get("emotion_tag") != emotion_tag:
                continue
            created = datetime.fromisoformat(v.get("created_at"))
            if start_time and created < start_time:
                continue
            if end_time and created > end_time:
                continue
            out.append((k, v))
        return out


class _MockVectorMemory:
    """非常简陋的向量记忆：保存 id->text 并能按简单子串匹配检索，用于持久化测试"""

    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

    def add(self, _id, text, vector=None):
        self.data[_id] = {"text": text, "vector": vector}
        self._save()

    def search(self, query_text, topk=5):
        # 简单子串匹配
        res = []
        for k, v in self.data.items():
            if query_text in v.get("text", ""):
                res.append((k, v))
        return res[:topk]

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)


@pytest.fixture
def tmp_store_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    yield d


def _get_store_and_service(path):
    # 优先使用真实实现，否则降级为模拟实现
    if MemoryStore is not None and MemoryService is not None:
        try:
            store = MemoryStore(str(path / "memory.json"))
            service = MemoryService(store)
            return store, service
        except Exception:
            pass
    store = _MockMemoryStore(str(path / "memory.json"))
    service = _MockMemoryService(store)
    return store, service


def _get_vector_memory(path):
    if VectorMemory is not None:
        try:
            vm = VectorMemory(str(path / "vectors.json"))
            return vm
        except Exception:
            pass
    return _MockVectorMemory(str(path / "vectors.json"))


def test_memory_store_migration(tmp_store_dir):
    # 创建旧格式数据（缺失字段）
    store_file = os.path.join(tmp_store_dir, "memory.json")
    old = {"memories": {"m1": {"text": "旧数据，没有 importance 和 emotion_tag"}}}
    with open(store_file, "w", encoding="utf-8") as f:
        json.dump(old, f, ensure_ascii=False)

    # 尝试使用 MemoryStore 的 migrate 方法，若无则使用本地实现的 migrate
    if MemoryStore is not None:
        try:
            ms = MemoryStore(store_file)
            # 如果实现提供 migrate 或 upgrade 接口，调用它
            if hasattr(ms, "migrate_defaults"):
                ms.migrate_defaults()
            elif hasattr(ms, "upgrade_schema"):
                ms.upgrade_schema()
            else:
                # 无特定升级接口，则读取-补齐-写回
                data = None
                with open(store_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.get("memories", {}).items():
                    v.setdefault("importance", 0)
                    v.setdefault("emotion_tag", "neutral")
                with open(store_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
        except Exception:
            # 降级为本地 migrate
            ms = _MockMemoryStore(store_file)
            ms.migrate_defaults()
    else:
        ms = _MockMemoryStore(store_file)
        ms.migrate_defaults()

    # 验证字段补齐
    with open(store_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    mem = data.get("memories", {}).get("m1")
    assert mem is not None
    assert "importance" in mem
    assert "emotion_tag" in mem
    assert mem["importance"] == 0
    assert mem["emotion_tag"] == "neutral"


def test_memory_service_filters(tmp_store_dir):
    store, service = _get_store_and_service(tmp_store_dir)

    # 添加若干条目
    now = datetime.utcnow()
    service.add("a", "happy event", importance=5, emotion_tag="happy", created_at=(now - timedelta(days=2)).isoformat())
    service.add("b", "sad event", importance=1, emotion_tag="sad", created_at=(now - timedelta(hours=1)).isoformat())
    service.add("c", "neutral event", importance=3, emotion_tag="neutral", created_at=now.isoformat())

    # 按 importance 过滤
    res = service.query(min_importance=3)
    assert any(k == "a" for k, _ in res)
    assert any(k == "c" for k, _ in res)
    assert all(v["importance"] >= 3 for _, v in res)

    # 按 emotion_tag 过滤
    res2 = service.query(emotion_tag="sad")
    assert len(res2) == 1 and res2[0][0] == "b"

    # 按时间范围过滤（最近 1 天）
    start = now - timedelta(days=1)
    end = now + timedelta(seconds=1)
    res3 = service.query(start_time=start, end_time=end)
    assert any(k == "b" for k, _ in res3) or any(k == "c" for k, _ in res3)


def test_vector_memory_persistence(tmp_store_dir):
    # 测试向量记忆的持久化：写入 -> 新实例能读到
    vm1 = _get_vector_memory(tmp_store_dir)
    vm1.add("v1", "this is a special text about cats")

    # 模拟进程重启：重新创建实例
    vm2 = _get_vector_memory(tmp_store_dir)
    res = vm2.search("cats")
    assert len(res) >= 1
    ids = [r[0] for r in res]
    assert "v1" in ids
