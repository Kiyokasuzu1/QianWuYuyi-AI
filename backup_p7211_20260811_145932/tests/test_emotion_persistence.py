"""
Phase 9.2.1：情绪持久化与更新链路测试
"""
import sys, os, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_manager import EmotionManager
from src.emotion.emotion_repository import EmotionRepository
from src.emotion.emotion_event import EmotionEvent


def test_save_and_load_preserves_state():
    """保存后重新加载，状态应一致"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = f"{tmpdir}/emotion.json"
        repo = EmotionRepository(path)

        # 第一次创建并保存
        mgr1 = EmotionManager(repo)
        mgr1.process_event(EmotionEvent("user_praise", intensity=1.0))
        val1 = mgr1.state.valence

        # 第二次从文件加载
        mgr2 = EmotionManager(EmotionRepository(path))
        assert mgr2.state.valence == val1


def test_update_applies_decay():
    """update() 应应用时间衰减"""
    mgr = EmotionManager()
    mgr.process_event(EmotionEvent("user_praise", intensity=1.0))

    before = abs(mgr.state.valence)

    # 手动快进时间
    from datetime import datetime, timedelta
    past = datetime.now() - timedelta(hours=2)
    mgr.state.updated_at = past.isoformat()

    mgr.update()
    after = abs(mgr.state.valence)

    assert after < before


def test_get_context_is_read_only():
    """get_context() 不应修改状态"""
    mgr = EmotionManager()
    mgr.process_event(EmotionEvent("user_praise", intensity=1.0))

    ctx1 = mgr.get_context()
    ctx2 = mgr.get_context()

    assert ctx1.summary == ctx2.summary
    assert ctx1.mood == ctx2.mood


if __name__ == "__main__":
    test_save_and_load_preserves_state()
    print("✅ 1/3 持久化保存与加载")
    test_update_applies_decay()
    print("✅ 2/3 衰减更新")
    test_get_context_is_read_only()
    print("✅ 3/3 get_context 只读")
    print("\n🎉 Phase 9.2.1 全部通过")