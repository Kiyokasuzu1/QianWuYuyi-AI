"""
BehaviorResolver v0.7 验证测试

检查 behavior_text 是否已清除越界描述。
"""

import sys
import os

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)

from src.orchestrator import Orchestrator


def test_behavior_text_boundary():

    o = Orchestrator()
    o.relationship_state.recalibrate_for_testing()

    # 生成人格
    p = o.personality_resolver.resolve()

    rs = o.relationship_state.get()

    p._data["familiarity"] = rs["familiarity"]
    p._data["bond_strength"] = rs["bond_strength"]

    # 提取 behavior_text
    behavior_text = p.behavior_text

    print("\n=== behavior_text 完整输出 ===")
    print(behavior_text)

    forbidden = [
        "习惯使用者的陪伴",
        "期待回应",
        "依赖感",
        "珍视连接",
        "重要之人",
        "特殊存在",
        "对使用者表现出温柔和关怀",
        "开始习惯",
    ]

    found = []

    for phrase in forbidden:
        if phrase in behavior_text:
            found.append(phrase)

    assert not found, f"发现越界短语: {found}"

    print(
        "✅ 未发现越界短语，behavior_text 清洗成功"
    )


if __name__ == "__main__":
    test_behavior_text_boundary()
    print("\n🎉 behavior_text 测试通过")