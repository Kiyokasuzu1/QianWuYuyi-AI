"""
Phase 11.8.1：Agreement 运行时集成测试（最终版，10 项）
所有测试使用临时文件完全隔离，无交叉污染。
"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.agreement.agreement import Agreement, AgreementPriority, AgreementCategory, AgreementSource
from src.agreement.agreement_repository import AgreementRepository
from src.agreement.agreement_manager import AgreementManager
from src.agreement.boundary_checker import BoundaryChecker
from src.agreement.agreement_context import AgreementContext
from src.agreement.agreement_rule import get_rule_for_agreement
from src.agreement.agreement_config import AgreementConfig
from src.identity.user_context import UserContext


def make_temp_repo():
    """创建完全隔离的临时仓库"""
    tmpdir = tempfile.mkdtemp()
    filepath = os.path.join(tmpdir, "agreements.json")
    repo = AgreementRepository(filepath=filepath)
    return repo, tmpdir


def test_restart_preserves_agreements():
    repo, tmpdir = make_temp_repo()
    mgr1 = AgreementManager(repo)
    mgr1.add_agreement(Agreement(
        content="不编造记忆",
        priority=AgreementPriority.IMMUTABLE,
        source_type=AgreementSource.DEVELOPER_DEFINED,
    ))
    # 模拟重启：用同一路径重新创建仓库和管理器
    filepath = os.path.join(tmpdir, "agreements.json")
    repo2 = AgreementRepository(filepath=filepath)
    mgr2 = AgreementManager(repo2)
    assert len(mgr2.get_all()) == 1
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_agreement_context_immutable_and_high():
    agreements = [
        Agreement(content="不编造记忆", priority=AgreementPriority.IMMUTABLE),
        Agreement(content="回复简洁", priority=AgreementPriority.HIGH),
    ]
    ctx = AgreementContext.build(agreements)
    assert "不可改变核心约定" in ctx
    assert "不编造记忆" in ctx
    assert "长期偏好" in ctx
    assert "回复简洁" in ctx


def test_agreement_context_only_immutable():
    agreements = [Agreement(content="不编造记忆", priority=AgreementPriority.IMMUTABLE)]
    ctx = AgreementContext.build(agreements)
    assert "不可改变核心约定" in ctx
    assert "长期偏好" not in ctx


def test_empty_agreements_returns_empty_string():
    ctx = AgreementContext.build([])
    assert ctx == ""


def test_boundary_checker_blocks_immutable_only():
    repo, tmpdir = make_temp_repo()
    mgr = AgreementManager(repo)
    mgr.add_agreement(Agreement(
        content="不称用户为主人",
        priority=AgreementPriority.IMMUTABLE,
        source_type=AgreementSource.DEVELOPER_DEFINED,
    ))
    mgr.add_agreement(Agreement(
        content="回复简洁",
        priority=AgreementPriority.HIGH,
    ))
    checker = BoundaryChecker(mgr)
    assert checker.check("好的，主人").passed is False
    assert checker.check("这是一段非常冗长啰嗦的回复" * 10).passed is True
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_boundary_checker_passes_safe_text():
    repo, tmpdir = make_temp_repo()
    mgr = AgreementManager(repo)
    mgr.add_agreement(Agreement(
        content="不称用户为主人",
        priority=AgreementPriority.IMMUTABLE,
        source_type=AgreementSource.DEVELOPER_DEFINED,
    ))
    checker = BoundaryChecker(mgr)
    assert checker.check("你好，今天有什么可以帮你的？").passed is True
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_user_isolation():
    """不同用户的约定通过不同临时文件完全隔离"""
    repo_a, tmpdir_a = make_temp_repo()
    repo_b, tmpdir_b = make_temp_repo()

    mgr_a = AgreementManager(repo_a)
    mgr_b = AgreementManager(repo_b)

    mgr_a.add_agreement(Agreement(
        content="用户A的核心约定",
        priority=AgreementPriority.IMMUTABLE,
        source_type=AgreementSource.DEVELOPER_DEFINED,
    ))
    mgr_b.add_agreement(Agreement(
        content="用户B的约定",
        priority=AgreementPriority.HIGH,
    ))

    assert len(mgr_a.get_active_agreements()) == 1
    assert len(mgr_b.get_active_agreements()) == 1
    assert mgr_a.get_active_agreements()[0].content != mgr_b.get_active_agreements()[0].content

    shutil.rmtree(tmpdir_a, ignore_errors=True)
    shutil.rmtree(tmpdir_b, ignore_errors=True)


def test_agreement_rule_case_insensitive():
    rule = get_rule_for_agreement("不称用户为主人")
    assert rule.matches("主人你好")
    assert rule.matches("Master")
    assert rule.matches("MASTER")
    assert rule.matches("My Owner")
    assert not rule.matches("你好")


def test_agreement_rule_default_pattern():
    rule = get_rule_for_agreement("自定义的约定内容")
    assert rule.matches("自定义的约定内容")
    assert not rule.matches("无关文本")


def test_prompt_builder_receives_agreement_context():
    from src.response.prompt_builder import PromptBuilder

    builder = PromptBuilder()
    messages = builder.build_messages(
        user_message="你好",
        agreement_context="【不可改变核心约定】\n不编造记忆",
    )
    system_prompt = messages[0]["content"]
    assert "不编造记忆" in system_prompt
    assert "【不可改变核心约定】" in system_prompt


if __name__ == "__main__":
    test_restart_preserves_agreements()
    print("✅ 1/10 重启保持约定")
    test_agreement_context_immutable_and_high()
    print("✅ 2/10 AgreementContext 区分等级")
    test_agreement_context_only_immutable()
    print("✅ 3/10 仅不可变时无偏好段落")
    test_empty_agreements_returns_empty_string()
    print("✅ 4/10 空约定返回空")
    test_boundary_checker_blocks_immutable_only()
    print("✅ 5/10 只拦截 IMMUTABLE")
    test_boundary_checker_passes_safe_text()
    print("✅ 6/10 安全文本通过")
    test_user_isolation()
    print("✅ 7/10 用户隔离")
    test_agreement_rule_case_insensitive()
    print("✅ 8/10 大小写不敏感匹配")
    test_agreement_rule_default_pattern()
    print("✅ 9/10 默认规则模式")
    test_prompt_builder_receives_agreement_context()
    print("✅ 10/10 PromptBuilder 注入验证")
    print("\n🎉 Phase 11.8 全部通过")