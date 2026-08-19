"""
GrowthPipeline 成长记录生成测试
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.growth.pipeline import GrowthPipeline


def test_ordinary_chat_no_growth():
    """场景1：普通聊天 → 无成长记录"""
    p = GrowthPipeline()
    r = p.incremental_update("今天有点累")
    assert r["growth_records"] == [], f"Expected empty, got {r['growth_records']}"
    print("✅ 场景1通过: 普通聊天不产生成长记录")


def test_repeated_creation_produces_record():
    """场景2：重复创作 → 有成长记录"""
    p = GrowthPipeline()
    for _ in range(5):
        p.incremental_update("我今天用AI画了一幅作品")
    r = p.incremental_update("刚刚完成了一张新作品")
    assert len(r["growth_records"]) > 0, "Expected growth_records > 0"
    print("✅ 场景2通过: 重复创作产生成长记录")


def test_relationship_event_no_personality_record():
    """场景3：关系事件 → 无成长记录"""
    p = GrowthPipeline()
    r = p.incremental_update("我每天都会找你聊天")
    assert r["growth_records"] == [], f"Expected empty, got {r['growth_records']}"
    print("✅ 场景3通过: 关系事件不产生成长记录")


if __name__ == "__main__":
    test_ordinary_chat_no_growth()
    test_repeated_creation_produces_record()
    test_relationship_event_no_personality_record()
    print("\n🎉 全部通过")