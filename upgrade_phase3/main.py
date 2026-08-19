print("🔴 程序入口 1")

import sys
print("🔴 程序入口 2")
from pathlib import Path
print("🔴 程序入口 3")

sys.path.insert(0, str(Path(__file__).parent))
print("🔴 程序入口 4")

from src.orchestrator import Orchestrator
print("🔴 程序入口 5 - 导入完成")


def main():
    print("🔴 main() 开始执行")
    # ========== 测试 GrowthPipeline ==========
    print("🧪 测试 GrowthPipeline...")
    try:
        from src.growth.pipeline import GrowthPipeline
        import json

        pipeline = GrowthPipeline()
        print("🔴 Pipeline 实例创建成功，开始运行 full_consolidation...")
        result = pipeline.run_full_consolidation(20, force_first_run=True)
        print("🔴 full_consolidation 执行完毕")

        # 从结果中提取事件列表和人格信息
        events = result.get("events", [])
        personality = result.get("personality", {})

        print(f"\n📊 共处理 {len(events)} 个事件")

        for e in events:
            if isinstance(e, dict):
                print(f"\n  [{e.get('event_id')}]")
                print(f"    topic: {e.get('topic')}")
                print(f"    canonical_topic: {e.get('canonical_topic')}")
                print(f"    category: {e.get('category')} ({e.get('category_id')})")
                print(f"    importance: {e.get('importance')}")
                print(f"    is_first_occurrence: {e.get('is_first_occurrence')}")
                print(f"    source_ids: {len(e.get('source_ids', []))} 条")
            else:
                print(f"\n  [异常数据] {e}")

        # 打印人格摘要
        if personality:
            print(f"\n🧠 当前人格摘要:")
            print(f"  温暖度: {personality.get('warmth')}")
            print(f"  害羞度: {personality.get('shyness')}")
            print(f"  信任程度: {personality.get('trust_level')}")
            print(f"  依恋程度: {personality.get('attachment_level')}")

        # 保存规范化事件到文件
        with open("data/normalized_events.json", "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        print("\n💾 规范化事件已保存到 data/normalized_events.json")

    except Exception as e:
        print(f"⚠️ 测试失败: {e}")
        import traceback
        traceback.print_exc()
    print("--- 测试结束 ---\n")
    # ========== 测试代码结束 ==========

    print("=" * 50)
    print("  浅雾羽依 v0.3.0 终端模拟器")
    print("  输入 exit 或 quit 退出")
    print("  /clear 清空当前会话记忆")
    print("=" * 50 + "\n")

    orch = Orchestrator()

    while True:
        try:
            user_input = input("你: ").strip()
            if not user_input:
                continue

            if user_input.lower() in ["exit", "quit"]:
                print("羽依: 嗯，下次见。我会记得今天聊过的话。")
                break

            if user_input.lower() == "/clear":
                orch.clear_history()
                print("羽依: 好，我忘掉刚才的了。我们重新开始吧。\n")
                continue

            print("羽依: ", end="")
            reply = orch.process(user_input)
            print(reply + "\n")

        except KeyboardInterrupt:
            print("\n羽依: 突然安静了... 下次再聊。")
            break
        except Exception as e:
            print(f"\n⚠️ 系统异常: {e}\n")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    print("🔴 进入 __main__")
    main()