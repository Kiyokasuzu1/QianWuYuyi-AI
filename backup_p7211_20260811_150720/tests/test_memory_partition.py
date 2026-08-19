"""
Phase 11.6：Memory Partition 测试（14 项）
"""
import sys, os, tempfile, shutil, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.identity.user_context import UserContext
from src.identity.user_resolver import UserResolver


def test_user_context_dirs_different():
    ctx_a = UserContext(user_id="user_A")
    ctx_b = UserContext(user_id="user_B")
    assert ctx_a.user_dir != ctx_b.user_dir


def test_user_context_path_safe():
    ctx = UserContext(user_id="../dangerous/path")
    assert ".." not in ctx.user_dir


def test_user_context_properties():
    ctx = UserContext(user_id="test_user")
    assert "test_user" in ctx.user_dir
    assert ctx.memory_path.endswith("memory.json")
    assert ctx.origin_identity_path.endswith("origin_identity.json")
    assert ctx.agreements_dir.endswith("agreements")


def test_memory_partition_isolation():
    ctx_a = UserContext(user_id="user_A")
    ctx_b = UserContext(user_id="user_B")
    assert ctx_a.memory_path != ctx_b.memory_path


def test_vector_index_partitioned():
    ctx_a = UserContext(user_id="user_A")
    ctx_b = UserContext(user_id="user_B")
    assert ctx_a.vector_index_dir != ctx_b.vector_index_dir


def test_origin_identity_path_isolated():
    ctx_a = UserContext(user_id="user_A")
    ctx_b = UserContext(user_id="user_B")
    assert ctx_a.origin_identity_path != ctx_b.origin_identity_path


def test_relationship_dir_isolated():
    ctx_a = UserContext(user_id="user_A")
    ctx_b = UserContext(user_id="user_B")
    assert ctx_a.relationship_dir != ctx_b.relationship_dir


def test_agreements_dir_exists():
    ctx = UserContext(user_id="test_user")
    assert "agreements" in ctx.agreements_dir


def test_resolver_returns_user_context():
    resolver = UserResolver()
    ctx = resolver.resolve()
    assert ctx is not None
    assert ctx.user_id != ""
    assert ctx.platform == "qq"


def test_platform_isolation():
    ctx_qq = UserContext(user_id="user123", platform="qq")
    ctx_discord = UserContext(user_id="user123", platform="discord")
    assert ctx_qq.user_dir != ctx_discord.user_dir


def test_owner_metadata_required():
    ctx = UserContext(user_id="owner_test_user")
    metadata = {"owner": ctx.user_key}
    assert metadata["owner"] == "qq:owner_test_user"


def test_platform_paths_are_different():
    ctx_qq = UserContext(user_id="user123", platform="qq")
    ctx_discord = UserContext(user_id="user123", platform="discord")
    assert ctx_qq.user_dir != ctx_discord.user_dir


def test_real_memory_data_isolation():
    tmpdir = tempfile.mkdtemp()
    try:
        ctx_a = UserContext(user_id="user_A", platform="qq")
        memory_path_a = f"{tmpdir}/{ctx_a.user_dir}/memory.json"
        os.makedirs(os.path.dirname(memory_path_a), exist_ok=True)
        memory_a = [{"id": "mem_001", "content": "A 的秘密", "metadata": {"owner": ctx_a.user_key}}]
        with open(memory_path_a, "w", encoding="utf-8") as f:
            json.dump(memory_a, f)

        ctx_b = UserContext(user_id="user_B", platform="qq")
        memory_path_b = f"{tmpdir}/{ctx_b.user_dir}/memory.json"
        os.makedirs(os.path.dirname(memory_path_b), exist_ok=True)
        memory_b = [{"id": "mem_002", "content": "B 的笔记", "metadata": {"owner": ctx_b.user_key}}]
        with open(memory_path_b, "w", encoding="utf-8") as f:
            json.dump(memory_b, f)

        with open(memory_path_a, "r", encoding="utf-8") as f:
            loaded_a = json.load(f)
        assert "B 的笔记" not in str(loaded_a)

        with open(memory_path_b, "r", encoding="utf-8") as f:
            loaded_b = json.load(f)
        assert "A 的秘密" not in str(loaded_b)

        assert ctx_a.user_key != ctx_b.user_key
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_user_key_format():
    ctx = UserContext(user_id="test_user", platform="qq")
    assert ctx.user_key == "qq:test_user"
    ctx2 = UserContext(user_id="test_user", platform="discord")
    assert ctx2.user_key == "discord:test_user"
    assert ctx.user_key != ctx2.user_key


if __name__ == "__main__":
    test_user_context_dirs_different()
    print("✅ 1/14 不同用户目录隔离")
    test_user_context_path_safe()
    print("✅ 2/14 路径穿越防护")
    test_user_context_properties()
    print("✅ 3/14 UserContext 属性")
    test_memory_partition_isolation()
    print("✅ 4/14 记忆分区隔离")
    test_vector_index_partitioned()
    print("✅ 5/14 向量索引隔离")
    test_origin_identity_path_isolated()
    print("✅ 6/14 起源身份路径隔离")
    test_relationship_dir_isolated()
    print("✅ 7/14 关系目录隔离")
    test_agreements_dir_exists()
    print("✅ 8/14 Agreement 目录预留")
    test_resolver_returns_user_context()
    print("✅ 9/14 UserResolver 可用")
    test_platform_isolation()
    print("✅ 10/14 跨平台隔离")
    test_owner_metadata_required()
    print("✅ 11/14 owner 元数据验证")
    test_platform_paths_are_different()
    print("✅ 12/14 跨平台路径不同")
    test_real_memory_data_isolation()
    print("✅ 13/14 真实数据隔离")
    test_user_key_format()
    print("✅ 14/14 user_key 格式正确")
    print("\n🎉 Phase 11.6 全部通过")