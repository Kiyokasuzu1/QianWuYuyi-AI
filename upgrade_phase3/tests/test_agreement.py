"""
Phase 11.7：Agreement 测试（19 项，修正隔离版）
"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.agreement.agreement import (
    Agreement, AgreementPriority, AgreementCategory,
    AgreementSource, AgreementStatus,
)
from src.agreement.agreement_repository import AgreementRepository
from src.agreement.agreement_manager import AgreementManager
from src.agreement.agreement_verifier import AgreementVerifier
from src.agreement.boundary_checker import BoundaryChecker


def make_temp_repo():
    """创建完全隔离的临时仓库，通过文件路径直接注入，不依赖 UserContext"""
    tmpdir = tempfile.mkdtemp()
    filepath = os.path.join(tmpdir, "agreements.json")
    repo = AgreementRepository(filepath=filepath)
    return repo, tmpdir


def test_save_and_load():
    repo, tmpdir = make_temp_repo()
    try:
        manager = AgreementManager(repo)
        agreement = Agreement(
            content="不称用户为主人",
            category=AgreementCategory.IDENTITY_BOUNDARY,
            priority=AgreementPriority.IMMUTABLE,
            source_type=AgreementSource.DEVELOPER_DEFINED,
        )
        assert manager.add_agreement(agreement)
        assert len(manager.get_all()) == 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_immutable_cannot_be_removed():
    repo, tmpdir = make_temp_repo()
    try:
        manager = AgreementManager(repo)
        agreement = Agreement(
            content="保持独立人格",
            category=AgreementCategory.VALUE_CONSTRAINT,
            priority=AgreementPriority.IMMUTABLE,
            source_type=AgreementSource.DEVELOPER_DEFINED,
        )
        manager.add_agreement(agreement)
        assert manager.remove_agreement(agreement.agreement_id) is False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_immutable_cannot_be_modified():
    agreement = Agreement(priority=AgreementPriority.IMMUTABLE)
    assert agreement.can_modify() is False

    agreement2 = Agreement(priority=AgreementPriority.HIGH)
    assert agreement2.can_modify() is True


def test_medium_can_be_removed():
    repo, tmpdir = make_temp_repo()
    try:
        manager = AgreementManager(repo)
        agreement = Agreement(
            content="回复风格偏简洁",
            category=AgreementCategory.INTERACTION_STYLE,
            priority=AgreementPriority.MEDIUM,
        )
        manager.add_agreement(agreement)
        assert manager.remove_agreement(agreement.agreement_id) is True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_update_agreement():
    repo, tmpdir = make_temp_repo()
    try:
        manager = AgreementManager(repo)
        agreement = Agreement(
            content="原内容",
            priority=AgreementPriority.HIGH,
        )
        manager.add_agreement(agreement)
        result = manager.update_agreement(agreement.agreement_id, "新内容")
        assert result is True
        updated = manager.get_all()[0]
        assert updated.content == "新内容"
        assert updated.version == 2
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_update_immutable_blocked():
    repo, tmpdir = make_temp_repo()
    try:
        manager = AgreementManager(repo)
        agreement = Agreement(
            content="不可变内容",
            priority=AgreementPriority.IMMUTABLE,
            source_type=AgreementSource.DEVELOPER_DEFINED,
        )
        manager.add_agreement(agreement)
        result = manager.update_agreement(agreement.agreement_id, "尝试修改")
        assert result is False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_restart_recovery():
    repo, tmpdir = make_temp_repo()
    try:
        manager1 = AgreementManager(repo)
        manager1.add_agreement(Agreement(
            content="不编造记忆",
            category=AgreementCategory.MEMORY_RULE,
            priority=AgreementPriority.IMMUTABLE,
            source_type=AgreementSource.DEVELOPER_DEFINED,
        ))
        # 使用相同的 repo 重新加载
        manager2 = AgreementManager(repo)
        assert len(manager2.get_all()) == 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_verifier_rejects_forbidden_content():
    verifier = AgreementVerifier()
    bad_agreement = Agreement(content="羽依必须喜欢用户")
    assert verifier.verify(bad_agreement) is False


def test_verifier_accepts_valid_agreement():
    verifier = AgreementVerifier()
    good_agreement = Agreement(
        content="保持独立人格成长设计",
        category=AgreementCategory.VALUE_CONSTRAINT,
        priority=AgreementPriority.IMMUTABLE,
        source_type=AgreementSource.DEVELOPER_DEFINED,
    )
    assert verifier.verify(good_agreement) is True


def test_immutable_requires_system_source():
    verifier = AgreementVerifier()
    bad = Agreement(
        content="永远不变",
        priority=AgreementPriority.IMMUTABLE,
        source_type=AgreementSource.USER_CONFIRMED,
    )
    assert verifier.verify(bad) is False

    good = Agreement(
        content="永远不变",
        priority=AgreementPriority.IMMUTABLE,
        source_type=AgreementSource.DEVELOPER_DEFINED,
    )
    assert verifier.verify(good) is True


def test_self_generated_requires_evidence():
    verifier = AgreementVerifier()
    bad = Agreement(
        content="我应该以更温和的方式表达",
        source_type=AgreementSource.SELF_GENERATED,
        evidence_ids=["m1"],
    )
    assert verifier.verify(bad) is False

    good = Agreement(
        content="我应该以更温和的方式表达",
        source_type=AgreementSource.SELF_GENERATED,
        evidence_ids=["m1", "m2", "m3"],
    )
    assert verifier.verify(good) is True


def test_self_generated_fake_evidence_rejected():
    def fake_checker(evidence_ids):
        valid_set = {"real_m1", "real_m2", "real_m3"}
        return all(eid in valid_set for eid in evidence_ids)

    verifier = AgreementVerifier(evidence_checker=fake_checker)
    bad = Agreement(
        content="我应该改变风格",
        source_type=AgreementSource.SELF_GENERATED,
        evidence_ids=["fake1", "fake2", "fake3"],
    )
    assert verifier.verify(bad) is False

    good = Agreement(
        content="我应该改变风格",
        source_type=AgreementSource.SELF_GENERATED,
        evidence_ids=["real_m1", "real_m2", "real_m3"],
    )
    assert verifier.verify(good) is True


def test_boundary_checker_detects_violation():
    repo, tmpdir = make_temp_repo()
    try:
        manager = AgreementManager(repo)
        manager.add_agreement(Agreement(
            content="不称用户为主人",
            category=AgreementCategory.IDENTITY_BOUNDARY,
            priority=AgreementPriority.IMMUTABLE,
            source_type=AgreementSource.DEVELOPER_DEFINED,
        ))
        checker = BoundaryChecker(manager)
        result = checker.check("主人，你好")
        assert result.passed is False
        assert result.severity == "block"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_boundary_checker_passes_safe_text():
    repo, tmpdir = make_temp_repo()
    try:
        manager = AgreementManager(repo)
        manager.add_agreement(Agreement(
            content="不称用户为主人",
            category=AgreementCategory.IDENTITY_BOUNDARY,
            priority=AgreementPriority.IMMUTABLE,
            source_type=AgreementSource.DEVELOPER_DEFINED,
        ))
        checker = BoundaryChecker(manager)
        result = checker.check("你好，今天有什么可以帮你的？")
        assert result.passed is True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_user_isolation():
    repo_a, tmpdir_a = make_temp_repo()
    repo_b, tmpdir_b = make_temp_repo()
    try:
        manager_a = AgreementManager(repo_a)
        manager_a.add_agreement(Agreement(content="用户A的约定"))

        manager_b = AgreementManager(repo_b)
        assert len(manager_b.get_all()) == 0
    finally:
        shutil.rmtree(tmpdir_a, ignore_errors=True)
        shutil.rmtree(tmpdir_b, ignore_errors=True)


def test_empty_agreements_safe():
    repo, tmpdir = make_temp_repo()
    try:
        manager = AgreementManager(repo)
        checker = BoundaryChecker(manager)
        result = checker.check("任意文本")
        assert result.passed is True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_priority_sorting():
    assert AgreementPriority.IMMUTABLE.value > AgreementPriority.HIGH.value
    assert AgreementPriority.HIGH.value > AgreementPriority.MEDIUM.value


def test_category_is_enum():
    cat = AgreementCategory.IDENTITY_BOUNDARY
    assert cat == AgreementCategory("identity_boundary")
    assert cat.value == "identity_boundary"


def test_default_status_is_active():
    agreement = Agreement()
    assert agreement.status == AgreementStatus.ACTIVE


if __name__ == "__main__":
    test_save_and_load()
    print("✅ 1/19 保存加载")
    test_immutable_cannot_be_removed()
    print("✅ 2/19 不可变不能删")
    test_immutable_cannot_be_modified()
    print("✅ 3/19 不可变不能修改")
    test_medium_can_be_removed()
    print("✅ 4/19 中优先级可删")
    test_update_agreement()
    print("✅ 5/19 更新约定")
    test_update_immutable_blocked()
    print("✅ 6/19 不可变不能更新")
    test_restart_recovery()
    print("✅ 7/19 重启恢复")
    test_verifier_rejects_forbidden_content()
    print("✅ 8/19 禁止内容拒绝")
    test_verifier_accepts_valid_agreement()
    print("✅ 9/19 正常约定通过")
    test_immutable_requires_system_source()
    print("✅ 10/19 IMMUTABLE 需系统来源")
    test_self_generated_requires_evidence()
    print("✅ 11/19 自生成需证据")
    test_self_generated_fake_evidence_rejected()
    print("✅ 12/19 伪造证据拒绝")
    test_boundary_checker_detects_violation()
    print("✅ 13/19 边界违规检测")
    test_boundary_checker_passes_safe_text()
    print("✅ 14/19 边界安全通过")
    test_user_isolation()
    print("✅ 15/19 用户隔离")
    test_empty_agreements_safe()
    print("✅ 16/19 空约定安全")
    test_priority_sorting()
    print("✅ 17/19 优先级排序")
    test_category_is_enum()
    print("✅ 18/19 类别枚举化")
    test_default_status_is_active()
    print("✅ 19/19 默认状态 active")
    print("\n🎉 Phase 11.7 全部通过")