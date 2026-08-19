"""
Phase 11.5：Origin Persistence 测试（12 项）
"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.identity.origin_identity import OriginIdentity, OriginContributor, OriginRole
from src.identity.origin_storage import OriginStorage
from src.identity.origin_manager import OriginManager
from src.identity.origin_event import OriginEvent, OriginEventStatus


def make_temp_storage():
    """创建使用临时文件的存储"""
    tmpdir = tempfile.mkdtemp()
    return OriginStorage(f"{tmpdir}/origin_identity.json"), tmpdir


def test_save_and_load_identity():
    """保存后加载，数据应一致"""
    storage, tmpdir = make_temp_storage()
    try:
        identity = OriginIdentity()
        identity.add_contributor(OriginContributor(
            user_id="user_001",
            roles=[OriginRole.CREATOR],
            evidence_ids=["mem_001"],
            description="创建者",
        ))
        assert storage.save(identity)

        loaded = storage.load()
        assert len(loaded.contributors) == 1
        assert loaded.contributors[0].user_id == "user_001"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_load_returns_empty_when_no_file():
    """文件不存在时返回空 OriginIdentity"""
    storage = OriginStorage("/nonexistent/path/origin.json")
    identity = storage.load()
    assert identity.contributors == []
    assert identity.role_claims == {}


def test_corrupted_file_returns_empty():
    """损坏文件返回空身份，不崩溃"""
    tmpdir = tempfile.mkdtemp()
    try:
        filepath = f"{tmpdir}/origin.json"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("这不是有效的 JSON {{{")

        storage = OriginStorage(filepath)
        identity = storage.load()
        assert identity.contributors == []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_restart_recovery():
    """模拟重启后能恢复身份"""
    tmpdir = tempfile.mkdtemp()
    try:
        storage1 = OriginStorage(f"{tmpdir}/origin.json")
        manager1 = OriginManager(storage1)
        identity1 = manager1.identity
        identity1.add_contributor(OriginContributor(
            user_id="user_A", roles=[OriginRole.CREATOR], evidence_ids=["m1"]
        ))
        storage1.save(identity1)

        storage2 = OriginStorage(f"{tmpdir}/origin.json")
        manager2 = OriginManager(storage2)
        identity2 = manager2.identity

        assert len(identity2.contributors) == 1
        assert identity2.contributors[0].user_id == "user_A"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_candidate_event_not_written():
    """Candidate 状态的事件不应被写入身份"""
    tmpdir = tempfile.mkdtemp()
    try:
        storage = OriginStorage(f"{tmpdir}/origin.json")
        manager = OriginManager(storage)

        event = OriginEvent(
            event_id="test_001",
            event_type="origin_signal",
            status=OriginEventStatus.CANDIDATE,
            user_id="user_X",
            potential_roles=[OriginRole.CREATOR],
            evidence_ids=["m1"],
            confidence=0.3,
        )
        result = manager.process_verified_event(event)
        assert result is False
        assert len(manager.identity.contributors) == 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_verified_event_written():
    """Verified 事件应被写入身份并持久化"""
    tmpdir = tempfile.mkdtemp()
    try:
        storage = OriginStorage(f"{tmpdir}/origin.json")
        manager = OriginManager(storage)

        event = OriginEvent(
            event_id="test_002",
            event_type="origin_signal",
            status=OriginEventStatus.VERIFIED,
            user_id="user_Y",
            potential_roles=[OriginRole.SYSTEM_BUILDER],
            evidence_ids=["m1", "m2"],
            confidence=0.7,
            description="开发了核心模块",
        )
        result = manager.process_verified_event(event)
        assert result is True
        assert len(manager.identity.contributors) == 1
        assert manager.identity.contributors[0].user_id == "user_Y"

        loaded = storage.load()
        assert len(loaded.contributors) == 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_same_user_appends_evidence():
    """同一用户再次出现时，追加 evidence 而不是重复创建 contributor"""
    tmpdir = tempfile.mkdtemp()
    try:
        storage = OriginStorage(f"{tmpdir}/origin.json")
        manager = OriginManager(storage)

        event1 = OriginEvent(
            event_id="e1", event_type="origin_signal",
            status=OriginEventStatus.VERIFIED,
            user_id="user_Z",
            potential_roles=[OriginRole.GROWTH_PARTICIPANT],
            evidence_ids=["m1"],
            confidence=0.6,
        )
        manager.process_verified_event(event1)

        event2 = OriginEvent(
            event_id="e2", event_type="origin_signal",
            status=OriginEventStatus.VERIFIED,
            user_id="user_Z",
            potential_roles=[OriginRole.GROWTH_PARTICIPANT],
            evidence_ids=["m2", "m3"],
            confidence=0.7,
        )
        manager.process_verified_event(event2)

        assert len(manager.identity.contributors) == 1
        assert len(manager.identity.contributors[0].evidence_ids) == 3
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_same_user_adds_new_role():
    """同一用户后来获得新角色时，应追加 role 而不丢失旧 role"""
    tmpdir = tempfile.mkdtemp()
    try:
        storage = OriginStorage(f"{tmpdir}/origin.json")
        manager = OriginManager(storage)

        event1 = OriginEvent(
            event_id="e1", event_type="origin_signal",
            status=OriginEventStatus.VERIFIED,
            user_id="user_Z",
            potential_roles=[OriginRole.SYSTEM_BUILDER],
            evidence_ids=["m1", "m2"],
            confidence=0.7,
        )
        manager.process_verified_event(event1)

        event2 = OriginEvent(
            event_id="e2", event_type="origin_signal",
            status=OriginEventStatus.VERIFIED,
            user_id="user_Z",
            potential_roles=[OriginRole.GROWTH_PARTICIPANT],
            evidence_ids=["m3"],
            confidence=0.6,
        )
        manager.process_verified_event(event2)

        assert len(manager.identity.contributors) == 1
        contributor = manager.identity.contributors[0]
        assert OriginRole.SYSTEM_BUILDER in contributor.roles
        assert OriginRole.GROWTH_PARTICIPANT in contributor.roles
        assert len(contributor.evidence_ids) == 3
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_origin_does_not_enter_traits():
    """起源身份不应包含 traits 字段"""
    tmpdir = tempfile.mkdtemp()
    try:
        storage = OriginStorage(f"{tmpdir}/origin.json")
        manager = OriginManager(storage)
        data = manager.identity.to_dict()
        assert "traits" not in data
        assert "personality" not in data
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_full_pipeline_detect_to_persist():
    """完整链路：检测 → 验证 → 持久化"""
    tmpdir = tempfile.mkdtemp()
    try:
        storage = OriginStorage(f"{tmpdir}/origin.json")
        manager = OriginManager(storage)

        event = manager.detect("我参与了羽依人格系统的设计", evidence_id="mem_001")
        assert event is not None
        assert event.status == OriginEventStatus.CANDIDATE

        event.evidence_ids = ["mem_001", "mem_002", "mem_003"]
        verified = manager.verify(event, existing_evidence_count=0)
        assert verified.status == OriginEventStatus.VERIFIED

        result = manager.process_verified_event(verified)
        assert result is True
        assert len(manager.identity.contributors) > 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_reload_clears_unsaved_changes():
    """reload 应从存储重新加载，丢弃未保存的变更"""
    tmpdir = tempfile.mkdtemp()
    try:
        storage = OriginStorage(f"{tmpdir}/origin.json")
        manager = OriginManager(storage)

        event = OriginEvent(
            event_id="e_saved", event_type="origin_signal",
            status=OriginEventStatus.VERIFIED,
            user_id="saved_user",
            potential_roles=[OriginRole.CREATOR],
            evidence_ids=["m1", "m2", "m3"],
            confidence=0.8,
        )
        manager.process_verified_event(event)

        manager.identity.add_contributor(OriginContributor(
            user_id="unsaved_user",
            roles=[OriginRole.SYSTEM_BUILDER],
            evidence_ids=["m_unsaved"],
        ))

        manager.reload()
        assert len(manager.identity.contributors) == 1
        assert manager.identity.contributors[0].user_id == "saved_user"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_invalid_role_ignored():
    """非法角色应被忽略，不影响合法角色写入"""
    tmpdir = tempfile.mkdtemp()
    try:
        storage = OriginStorage(f"{tmpdir}/origin.json")
        manager = OriginManager(storage)

        event = OriginEvent(
            event_id="e_mixed",
            event_type="origin_signal",
            status=OriginEventStatus.VERIFIED,
            user_id="user_M",
            potential_roles=[OriginRole.CREATOR, "unknown_role"],
            evidence_ids=["m1", "m2", "m3"],
            confidence=0.8,
        )
        result = manager.process_verified_event(event)
        assert result is True
        assert len(manager.identity.contributors) == 1
        contributor = manager.identity.contributors[0]
        assert OriginRole.CREATOR in contributor.roles
        assert "unknown_role" not in contributor.roles
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_save_and_load_identity()
    print("✅ 1/12 保存加载一致")
    test_load_returns_empty_when_no_file()
    print("✅ 2/12 无文件返回空")
    test_corrupted_file_returns_empty()
    print("✅ 3/12 损坏文件不崩溃")
    test_restart_recovery()
    print("✅ 4/12 重启恢复")
    test_candidate_event_not_written()
    print("✅ 5/12 Candidate 不写入")
    test_verified_event_written()
    print("✅ 6/12 Verified 写入")
    test_same_user_appends_evidence()
    print("✅ 7/12 同用户追加证据")
    test_same_user_adds_new_role()
    print("✅ 8/12 同用户追加新角色")
    test_origin_does_not_enter_traits()
    print("✅ 9/12 不污染 traits")
    test_full_pipeline_detect_to_persist()
    print("✅ 10/12 完整链路")
    test_reload_clears_unsaved_changes()
    print("✅ 11/12 reload 丢弃未保存")
    test_invalid_role_ignored()
    print("✅ 12/12 非法角色被忽略")
    print("\n🎉 Phase 11.5 全部通过")