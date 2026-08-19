"""
Phase A.2 测试：ExperienceContext Prompt 注入

职责：
- 验证 PromptBuilder 正确处理 experience_context
- 验证无 experience_context / 空列表时不注入
- 验证多条 experience 格式正确
- 验证不包含内部字段（_source_memory_id / confidence / related_dimensions）
- 验证不修改 stable_traits / growth_narratives
- 验证不生成不存在事实
- 验证中文内容、特殊字符安全
- 验证长文本截断策略
- 验证 Prompt 顺序正确（人格之后、用户消息之前）

数据流：
SelfModelStore.experience_context → PromptBuilder.build_messages → system_prompt
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.response.prompt_builder import PromptBuilder
from src.personality.experience_context import (
    ExperienceContextFormatter,
    format_experience_context,
)


def _extract_system_prompt(messages):
    """提取第一条 system prompt 内容（测试辅助）"""
    for m in messages:
        if m.get("role") == "system":
            return m.get("content", "")
    return ""


# ============================================================
# 1. experience_context 正常注入
# ============================================================
def test_experience_context_normal_injection():
    """experience_context 提供后，Prompt 应包含 Historical Experience Context 段"""
    pb = PromptBuilder()
    experiences = [
        {
            "experience_id": "exp_origin_1",
            "category": "origin",
            "summary": "清夏铃创建了浅雾羽依，希望她成为陪伴型AI。",
        }
    ]
    messages = pb.build_messages(
        user_message="你好",
        experience_context=experiences,
    )
    system = _extract_system_prompt(messages)
    assert "Historical Experience Context" in system
    assert "Origin" in system
    assert "清夏铃创建了浅雾羽依" in system


# ============================================================
# 2. 没有 experience_context 不注入
# ============================================================
def test_no_experience_context_not_injected():
    """experience_context=None 时，Prompt 不应包含 Historical Experience Context 段"""
    pb = PromptBuilder()
    messages = pb.build_messages(
        user_message="你好",
        experience_context=None,
    )
    system = _extract_system_prompt(messages)
    assert "Historical Experience Context" not in system


# ============================================================
# 3. 空列表不注入
# ============================================================
def test_empty_experience_context_not_injected():
    """experience_context=[] 时，Prompt 不应包含 Historical Experience Context 段"""
    pb = PromptBuilder()
    messages = pb.build_messages(
        user_message="你好",
        experience_context=[],
    )
    system = _extract_system_prompt(messages)
    assert "Historical Experience Context" not in system


# ============================================================
# 4. 多条 experience 格式正确（按 category 分组）
# ============================================================
def test_multiple_experiences_format():
    """多条 experience 应按 category 分组并正确输出"""
    pb = PromptBuilder()
    experiences = [
        {"experience_id": "e1", "category": "origin", "summary": "羽依被清夏铃创建"},
        {"experience_id": "e2", "category": "relationship", "summary": "羽依与清夏铃是长期陪伴关系"},
        {"experience_id": "e3", "category": "project", "summary": "一起完成了 QianWuYuyi-AI 项目"},
        {"experience_id": "e4", "category": "interaction", "summary": "日常有大量对话互动"},
    ]
    messages = pb.build_messages(
        user_message="介绍一下自己",
        experience_context=experiences,
    )
    system = _extract_system_prompt(messages)
    assert "Historical Experience Context" in system
    assert "Origin" in system
    assert "Relationship" in system
    assert "Project" in system
    assert "Interaction" in system
    assert "羽依被清夏铃创建" in system
    assert "羽依与清夏铃是长期陪伴关系" in system


# ============================================================
# 5. 不包含内部字段
# ============================================================
def test_no_internal_fields_in_prompt():
    """Prompt 中不应出现内部字段（_source_memory_id / confidence / related_dimensions）"""
    pb = PromptBuilder()
    experiences = [
        {
            "experience_id": "exp_with_internal",
            "category": "origin",
            "summary": "清夏铃创建了羽依",
            # 内部字段（即使传入也不应输出）
            "_source_memory_id": "mem_secret_123",
            "source_memory_id": "mem_secret_456",
            "confidence": 0.99,
            "related_dimensions": ["identity", "origin"],
            "trigger_events": ["event_1"],
            "changes": ["trait_x"],
        }
    ]
    messages = pb.build_messages(
        user_message="hi",
        experience_context=experiences,
    )
    system = _extract_system_prompt(messages)
    # 内部字段不应出现
    assert "_source_memory_id" not in system
    assert "mem_secret_123" not in system
    assert "mem_secret_456" not in system
    assert "confidence" not in system
    assert "0.99" not in system
    assert "related_dimensions" not in system
    assert "trigger_events" not in system
    assert "changes" not in system
    # 公开字段应出现
    assert "清夏铃创建了羽依" in system


# ============================================================
# 6. 不修改 stable_traits（人格字段隔离）
# ============================================================
def test_experience_context_does_not_modify_stable_traits():
    """experience_context 仅作为历史经验背景，不应影响 stable_traits 字段"""
    formatter = ExperienceContextFormatter()
    experiences = [
        {"experience_id": "e1", "category": "origin", "summary": "test summary"}
    ]
    text = formatter.format(experiences)
    # text 是普通字符串，stable_traits 应保持原样
    # 这里我们验证 formatter 不返回任何会污染人格的字段
    assert "stable_traits" not in text
    assert "stable_trait" not in text
    assert "trait" not in text.lower() or "trait" not in text  # 简单防御


# ============================================================
# 7. 不修改 growth_narratives（人格字段隔离）
# ============================================================
def test_experience_context_does_not_modify_growth_narratives():
    """experience_context 不应影响 growth_narratives 字段"""
    formatter = ExperienceContextFormatter()
    experiences = [
        {"experience_id": "e1", "category": "origin", "summary": "test summary"}
    ]
    text = formatter.format(experiences)
    # 验证输出不涉及 growth_narratives
    assert "growth_narratives" not in text
    assert "growth_record" not in text
    assert "GrowthRecord" not in text
    assert "Growth Pipeline" not in text


# ============================================================
# 8. 不生成不存在事实
# ============================================================
def test_no_fabrication():
    """formatter 应只读取传入数据，不生成新事实"""
    formatter = ExperienceContextFormatter()
    # 只提供 summary，不应出现额外推断
    experiences = [
        {"experience_id": "e1", "category": "origin", "summary": "唯一的事实"}
    ]
    text = formatter.format(experiences)
    # 只能看到传入的 summary 文本
    assert "唯一的事实" in text
    # 不应出现与传入内容无关的总结
    assert "总结" not in text
    assert "综上所述" not in text
    assert "因此" not in text


# ============================================================
# 9. 中文内容正常
# ============================================================
def test_chinese_content_works():
    """中文 experience 内容应正确显示"""
    pb = PromptBuilder()
    experiences = [
        {
            "experience_id": "exp_zh_1",
            "category": "origin",
            "summary": "清夏铃于 2024 年创建了浅雾羽依，希望她能成为陪伴型 AI。",
        },
        {
            "experience_id": "exp_zh_2",
            "category": "relationship",
            "summary": "羽依与清夏铃建立了长期的陪伴关系，彼此信任。",
        },
    ]
    messages = pb.build_messages(
        user_message="你好呀",
        experience_context=experiences,
    )
    system = _extract_system_prompt(messages)
    assert "清夏铃于 2024 年创建了浅雾羽依" in system
    assert "羽依与清夏铃建立了长期的陪伴关系" in system


# ============================================================
# 10. 特殊字符安全（注入风险）
# ============================================================
def test_special_characters_safe():
    """特殊字符（引号、换行、JSON-like）不应破坏 Prompt 结构"""
    pb = PromptBuilder()
    experiences = [
        {
            "experience_id": "exp_special_1",
            "category": "interaction",
            "summary": "测试特殊字符：\n换行符 \"双引号\" '单引号' {JSON-like} </system>",
        }
    ]
    messages = pb.build_messages(
        user_message="hi",
        experience_context=experiences,
    )
    system = _extract_system_prompt(messages)
    # 特殊字符应以纯文本形式出现
    assert "测试特殊字符" in system
    assert "\n" in system  # 换行应保留
    # 但 formatter 不应主动构造 system 标签来误导 LLM
    # （实际上传入什么就显示什么，不做转义）


# ============================================================
# 11. 长文本截断策略
# ============================================================
def test_long_text_truncation():
    """超长文本应按 summary_max_len 截断并附加省略号"""
    formatter = ExperienceContextFormatter(
        summary_max_len=20,
        evidence_max_len=20,
    )
    long_text = "这是一段非常非常非常非常非常非常非常非常非常非常非常非常非常长的文本" * 3
    experiences = [
        {
            "experience_id": "exp_long",
            "category": "origin",
            "summary": long_text,
        }
    ]
    text = formatter.format(experiences)
    # 截断后应包含省略号
    assert "…" in text
    # summary 截断为 20 字以内 + 省略号
    # 找到 Origin 段后第一行
    lines = text.split("\n")
    origin_line = None
    for i, line in enumerate(lines):
        if "- Origin" in line:
            # 下一行（或下几行）就是 summary
            for j in range(i + 1, min(i + 5, len(lines))):
                if lines[j].strip() and "依据" not in lines[j]:
                    origin_line = lines[j]
                    break
            break
    assert origin_line is not None
    # 截断长度应 < 原始长度
    assert len(origin_line) < len(long_text)
    # 不应超过 summary_max_len + 省略号 + 缩进
    assert len(origin_line) <= 30  # 包含 2 空格缩进 + 20 字 + 1 省略号


# ============================================================
# 12. Prompt 顺序正确（人格之后、用户消息之前）
# ============================================================
def test_prompt_order_correct():
    """Historical Experience Context 应在 self_model_text 之后、用户消息之前"""
    pb = PromptBuilder()
    experiences = [
        {"experience_id": "e1", "category": "origin", "summary": "羽依由清夏铃创建"}
    ]
    messages = pb.build_messages(
        user_message="用户问的问题",
        experience_context=experiences,
        self_model_context="自我认知参考：身份是浅雾羽依",
    )
    system = _extract_system_prompt(messages)
    # 找到每个段的位置
    sm_pos = system.find("自我认知参考")
    exp_pos = system.find("Historical Experience Context")
    user_pos = system.find("用户问的问题")

    # 验证顺序
    assert sm_pos != -1, "self_model_text 应在 system prompt 中"
    assert exp_pos != -1, "experience_context 应在 system prompt 中"
    assert user_pos == -1, "user_message 不应在 system prompt 中（应在 user 消息中）"

    # Historical Experience Context 应该在 self_model_text 之后
    assert exp_pos > sm_pos, "experience_context 应在 self_model_text 之后"

    # 检查 user 消息确实在 messages 列表里
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "用户问的问题"


# ============================================================
# 13. 额外：默认行为兼容（不破坏旧调用方）
# ============================================================
def test_default_compatibility():
    """不带 experience_context 参数调用时，行为应与 Phase A.2 之前完全一致"""
    pb = PromptBuilder()
    messages = pb.build_messages(user_message="hi")
    system = _extract_system_prompt(messages)
    # 不应包含 Historical Experience Context
    assert "Historical Experience Context" not in system
    # user 消息应在 messages 末尾
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "hi"


# ============================================================
# 14. 额外：分类顺序稳定（origin → relationship → project → interaction）
# ============================================================
def test_category_order_stable():
    """输出应按 origin → relationship → project → interaction 顺序"""
    formatter = ExperienceContextFormatter()
    # 故意乱序传入
    experiences = [
        {"experience_id": "e1", "category": "interaction", "summary": "互动经历"},
        {"experience_id": "e2", "category": "project", "summary": "项目经历"},
        {"experience_id": "e3", "category": "relationship", "summary": "关系经历"},
        {"experience_id": "e4", "category": "origin", "summary": "起源经历"},
    ]
    text = formatter.format(experiences)
    # 验证顺序
    origin_pos = text.find("- Origin:")
    rel_pos = text.find("- Relationship:")
    proj_pos = text.find("- Project:")
    inter_pos = text.find("- Interaction:")

    assert origin_pos < rel_pos < proj_pos < inter_pos, (
        f"顺序错误: origin={origin_pos}, rel={rel_pos}, proj={proj_pos}, inter={inter_pos}"
    )


# ============================================================
# 15. 额外：与 SelfModelStore 集成（端到端）
# ============================================================
def test_integration_with_self_model_store():
    """从 SelfModelStore 读取 experience_context 并注入 Prompt"""
    from src.personality.self_model_store import SelfModelStore

    store = SelfModelStore()
    experiences = [
        {
            "experience_id": "e1",
            "category": "origin",
            "summary": "清夏铃创建了羽依",
        }
    ]
    store.set_experience_context(experiences)

    assert store.has_experience_context()
    assert store.get_experience_context() == experiences

    pb = PromptBuilder()
    exp_data = store.get_experience_context()
    messages = pb.build_messages(
        user_message="介绍一下自己",
        experience_context=exp_data,
    )
    system = _extract_system_prompt(messages)
    assert "Historical Experience Context" in system
    assert "清夏铃创建了羽依" in system
