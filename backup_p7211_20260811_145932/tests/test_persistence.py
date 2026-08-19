"""
Phase 13.1：持久化层测试（10 项）
"""
import sys, os, tempfile, shutil
from pathlib import Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.storage.yuyi_persistence import YuyiPersistence
from src.identity.user_context import UserContext


def make_persistence(base_dir: str, user_id: str):
    ctx = UserContext(user_id=user_id, platform="test")
    return YuyiPersistence(base_dir=base_dir, user_context=ctx)


def test_save_and_load_self_model():
    tmpdir = tempfile.mkdtemp()
    try:
        p = make_persistence(tmpdir, "user_A")
        data = {"identity": "浅雾羽依", "traits": {"openness": 0.8}}
        p.save_self_model(data)
        loaded = p.load_self_model()
        assert loaded is not None
        assert loaded["identity"] == "浅雾羽依"
        assert loaded["traits"]["openness"] == 0.8
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_load_self_model_returns_none_when_no_file():
    tmpdir = tempfile.mkdtemp()
    try:
        p = make_persistence(tmpdir, "user_A")
        assert p.load_self_model() is None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_save_and_load_agreements():
    tmpdir = tempfile.mkdtemp()
    try:
        p = make_persistence(tmpdir, "user_A")
        agreements = [
            {"content": "不编造记忆", "priority": "IMMUTABLE"},
            {"content": "保持独立人格", "priority": "HIGH"},
        ]
        p.save_agreements(agreements)
        loaded = p.load_agreements()
        assert len(loaded) == 2
        assert loaded[0]["content"] == "不编造记忆"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_load_agreements_empty_when_no_file():
    tmpdir = tempfile.mkdtemp()
    try:
        p = make_persistence(tmpdir, "user_A")
        assert p.load_agreements() == []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_save_and_load_narrative_history():
    tmpdir = tempfile.mkdtemp()
    try:
        p = make_persistence(tmpdir, "user_A")
        data = {"snapshots": [{"version": 1, "core_identity": "浅雾羽依"}]}
        p.save_narrative_history(data)
        loaded = p.load_narrative_history()
        assert loaded is not None
        assert loaded["snapshots"][0]["version"] == 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_user_isolation_with_shared_base_dir():
    tmpdir = tempfile.mkdtemp()
    try:
        p_a = make_persistence(tmpdir, "user_A")
        p_b = make_persistence(tmpdir, "user_B")

        p_a.save_relationship_state({"trust": 0.9})
        p_b.save_relationship_state({"trust": 0.1})

        loaded_a = p_a.load_relationship_state()
        loaded_b = p_b.load_relationship_state()

        assert loaded_a is not None
        assert loaded_b is not None
        assert loaded_a["trust"] == 0.9
        assert loaded_b["trust"] == 0.1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_relationship_state_returns_none_when_no_file():
    tmpdir = tempfile.mkdtemp()
    try:
        p = make_persistence(tmpdir, "user_X")
        assert p.load_relationship_state() is None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_save_and_load_memory():
    tmpdir = tempfile.mkdtemp()
    try:
        p = make_persistence(tmpdir, "user_A")
        memories = [
            {"id": "m1", "content": "用户喜欢猫"},
            {"id": "m2", "content": "用户偏好简洁回复"},
        ]
        p.save_memory(memories)
        loaded = p.load_memory()
        assert len(loaded) == 2
        assert loaded[0]["content"] == "用户喜欢猫"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_load_memory_empty_when_no_file():
    tmpdir = tempfile.mkdtemp()
    try:
        p = make_persistence(tmpdir, "user_X")
        assert p.load_memory() == []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_schema_version_included_and_migration_entry():
    tmpdir = tempfile.mkdtemp()
    try:
        p = make_persistence(tmpdir, "user_A")
        p.save_self_model({"identity": "测试"})

        with open(os.path.join(tmpdir, "self_model.json"), "r", encoding="utf-8") as f:
            import json
            payload = json.load(f)
        assert "schema_version" in payload
        assert payload["schema_version"] == 1

        old_data_path = os.path.join(tmpdir, "old_self_model.json")
        with open(old_data_path, "w", encoding="utf-8") as f:
            json.dump({"schema_version": 0, "data": {"identity": "旧版羽依"}}, f)

        loaded = p._load_json(Path(old_data_path))
        assert loaded is not None
        assert loaded["identity"] == "旧版羽依"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_save_and_load_self_model()
    print("✅ 1/10 保存加载 SelfModel")
    test_load_self_model_returns_none_when_no_file()
    print("✅ 2/10 无文件返回 None")
    test_save_and_load_agreements()
    print("✅ 3/10 保存加载 Agreements")
    test_load_agreements_empty_when_no_file()
    print("✅ 4/10 无文件返回空列表")
    test_save_and_load_narrative_history()
    print("✅ 5/10 保存加载 NarrativeHistory")
    test_user_isolation_with_shared_base_dir()
    print("✅ 6/10 共享 base_dir 用户隔离")
    test_relationship_state_returns_none_when_no_file()
    print("✅ 7/10 关系状态无文件返回 None")
    test_save_and_load_memory()
    print("✅ 8/10 保存加载 Memory")
    test_load_memory_empty_when_no_file()
    print("✅ 9/10 Memory 无文件返回空")
    test_schema_version_included_and_migration_entry()
    print("✅ 10/10 schema_version 及迁移入口")
    print("\n🎉 Phase 13.1 全部通过")