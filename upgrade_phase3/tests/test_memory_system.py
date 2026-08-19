"""
羽依记忆系统验收测试（最终冻结版）

覆盖:
- Verifier: 分类准确性、信任度/用途分离、自我认知、输入防御
- Context: 分桶、渲染、安全隔离
- 安全: user_statement 不进人格/成长、assistant 零信任且不进入 persona、source_document 不直出
"""

from src.memory.memory_verifier import MemoryVerifier
from src.memory.memory_context import MemoryContext


# ========================
#  Verifier 单元测试
# ========================

def test_preference_detection():
    """用户偏好识别"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "我喜欢AI绘画"})
    assert res["memory_class"] == "preference"
    assert res["truth"] == 0.85
    assert "growth" in res["usage"]


def test_user_statement_is_not_fact():
    """用户陈述 ≠ 事实，不可用于成长"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "我小时候住在海边"})
    assert res["memory_class"] == "user_statement"
    assert res["truth"] == 0.6
    assert "growth" not in res["usage"]


def test_instruction_detection():
    """指令识别（主语+动作）"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "羽依以后回答的时候要温柔一点"})
    assert res["memory_class"] == "instruction"
    assert "persona" in res["usage"]


def test_assistant_zero_trust():
    """AI 回复零信任，不进 growth 和 persona"""
    v = MemoryVerifier()
    res = v.verify({"role": "assistant", "content": "你以前说过你喜欢猫"})
    assert res["memory_class"] == "assistant_output"
    assert res["truth"] == 0.0
    assert "growth" not in res["usage"]
    assert "persona" not in res["usage"]


def test_growth_memory():
    """成长引擎自我认知"""
    v = MemoryVerifier()
    res = v.verify({
        "origin": "growth_engine",
        "content": "我发现自己喜欢帮助用户整理想法"
    })
    assert res["memory_class"] == "growth_memory"
    assert res["self_confidence"] == 0.5
    assert res["truth"] == 0.3


def test_string_input_defense():
    """纯文本输入被安全兜底为 unknown（输入防御）"""
    v = MemoryVerifier()
    res = v.verify("我是一段纯文本")
    assert res["memory_class"] == "unknown"
    assert res["truth"] == 0.1


# ========================
#  Context 单元测试
# ========================

def test_context_buckets():
    """分桶统计正确"""
    v = MemoryVerifier()
    ctx = MemoryContext()
    msgs = [
        {"role": "user", "content": "我喜欢AI绘画"},
        {"role": "assistant", "content": "历史回复"},
        {"origin": "growth_engine", "content": "我的自我理解"}
    ]
    ctx.add_batch(v.verify_all(msgs))
    s = ctx.summary()
    assert s["preferences"] == 1
    assert s["assistant_output"] == 1
    assert s["growth_memories"] == 1


def test_identity_rendered():
    """身份信息正常渲染"""
    ctx = MemoryContext()
    ctx.add({
        "memory_class": "identity",
        "truth": 1.0,
        "content": "用户叫清夏铃"
    })
    prompt = ctx.build_prompt()
    assert "核心身份" in prompt
    assert "清夏铃" in prompt


def test_growth_memory_rendered_with_label():
    """成长记忆渲染并带有标签"""
    ctx = MemoryContext()
    ctx.add({
        "memory_class": "growth_memory",
        "truth": 0.3,
        "content": "我觉得自己喜欢帮助用户"
    })
    prompt = ctx.build_prompt()
    assert "羽依自我认知" in prompt
    assert "我觉得自己喜欢帮助用户" in prompt


def test_assistant_content_not_rendered():
    """AI 回复内容绝不展示"""
    v = MemoryVerifier()
    ctx = MemoryContext()
    ctx.add(v.verify({"role": "assistant", "content": "用户喜欢猫"}))
    prompt = ctx.build_prompt()
    assert "用户喜欢猫" not in prompt
    assert "AI 过去回复" in prompt


def test_empty_context():
    """空记忆安全返回"""
    ctx = MemoryContext()
    prompt = ctx.build_prompt()
    assert "当前没有相关记忆" in prompt


def test_source_document_isolated():
    """source_document 内容不直接进入 prompt，但提示存在"""
    ctx = MemoryContext()
    ctx.add({
        "memory_class": "source_document",
        "truth": 0.4,
        "content": "秘密档案中的敏感信息"
    })
    prompt = ctx.build_prompt()
    assert "敏感信息" not in prompt
    assert "待解析的参考资料" in prompt


# ========================
#  安全集成测试
# ========================

def test_user_statement_no_persona_no_growth():
    """用户玩笑话不能进入人格或成长（Verifier 权限检查）"""
    v = MemoryVerifier()
    res = v.verify({"role": "user", "content": "我其实是一只猫"})
    assert res["memory_class"] == "user_statement"
    assert "persona" not in res["usage"]
    assert "growth" not in res["usage"]


def test_assistant_cannot_growth_or_persona():
    """AI 回复绝不能用于成长或人格（Verifier）"""
    v = MemoryVerifier()
    res = v.verify({"role": "assistant", "content": "你是一个孤独的人"})
    assert "growth" not in res["usage"]
    assert "persona" not in res["usage"]


def test_usage_controls_access():
    """
    权限隔离：user_statement 的 usage 不含 growth，
    因此 get_growth_context 不应包含 user_statements 字段
    """
    ctx = MemoryContext()
    ctx.add({
        "memory_class": "user_statement",
        "truth": 0.6,
        "usage": ["conversation"],
        "content": "我是外星人"
    })
    growth_ctx = ctx.get_growth_context()
    assert "user_statements" not in growth_ctx
    assert "我是外星人" not in str(growth_ctx)


def test_user_statement_not_in_persona_context():
    """
    人格隔离：user_statement 不应进入人格解析上下文，
    防止用户随口说的话影响羽依人格
    """
    ctx = MemoryContext()
    ctx.add({
        "memory_class": "user_statement",
        "truth": 0.6,
        "usage": ["conversation"],
        "content": "我是外星人"
    })
    persona_ctx = ctx.get_persona_context()
    assert "我是外星人" not in str(persona_ctx)
    # 更加稳健的遍历检查，兼容未来结构变化
    for memories in persona_ctx.values():
        for mem in memories:
            assert mem.get("memory_class") != "user_statement"


def test_assistant_not_in_persona_context():
    """
    AI 历史回复不能污染羽依人格
    """
    ctx = MemoryContext()
    ctx.add({
        "memory_class": "assistant_output",
        "truth": 0.0,
        "content": "羽依是一个孤独的人"
    })
    persona_ctx = ctx.get_persona_context()
    assert "羽依是一个孤独的人" not in str(persona_ctx)


# ========================
#  指令识别增强测试
# ========================

def test_soft_instruction_detection():
    """软指令识别（羽依以后回答的时候温柔一点）"""
    v = MemoryVerifier()
    res = v.verify({
        "role": "user",
        "content": "羽依以后回答的时候温柔一点"
    })
    assert res["memory_class"] == "instruction"


def test_preference_not_instruction():
    """用户偏好不应被误判为指令"""
    v = MemoryVerifier()
    res = v.verify({
        "role": "user",
        "content": "我喜欢羽依温柔一点"
    })
    assert res["memory_class"] != "instruction"