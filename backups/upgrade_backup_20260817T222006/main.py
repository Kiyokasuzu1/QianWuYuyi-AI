print("🔴 程序入口 1")

import sys
print("🔴 程序入口 2")
from pathlib import Path
print("🔴 程序入口 3")

sys.path.insert(0, str(Path(__file__).parent))
print("🔴 程序入口 4")

from src.orchestrator import Orchestrator
print("🔴 程序入口 5 - 导入完成")

from src.orchestrator.long_loop import LongLoop


# Phase 4.0-R2.2: CLI 入口收敛适配器
class LongLoopOrchestratorAdapter:
    """LongLoop 入口统一到 RuntimePipeline。

    设计约束（R2.2 审查红线）：
    - 保留 LongLoop 期待的接口：``process(user_message: str) -> str``
    - 内部 100% 先走 RuntimePipeline.run()（entry=RuntimePipeline，审计合规）
    - R2.2 阶段只做"入口收敛"，不打开 Runtime 真实 17 阶段（runtime=None 传入，
      内部 fallback 到真正 Orchestrator，业务行为零差异）
    - 仅在 RuntimePipeline 自身抛错时（极端兜底），再调用一次真实 Orchestrator
      保证 CLI 不崩。
    """

    def __init__(self, canonical_orchestrator: Orchestrator) -> None:
        from src.runtime.runtime_pipeline import RuntimePipeline

        self._orch = canonical_orchestrator
        # R2.2 只收敛入口，暂不启用 RuntimeCore 17 阶段（runtime=None）；
        # orchestrator 参数 = 真正的羽依 Orchestrator，使得 pipeline 内 fallback
        # 路径与之前 CLI 直接调 orch.process() 行为完全等价。
        self._pipeline = RuntimePipeline(
            orchestrator=self._orch,
            runtime=None,
        )

    def process(self, user_input: str) -> str:
        try:
            ctx = self._pipeline.run({"user_message": user_input})
            outputs = getattr(ctx, "outputs", None) or {}
            snapshot = outputs.get("snapshot") or {}
            reply = snapshot.get("reply", "") or ""
            if isinstance(reply, str) and reply.strip():
                return reply
        except Exception:  # noqa: BLE001
            # 极端兜底：RuntimePipeline 本身异常，保证 CLI 不崩（不进常见路径）
            pass
        return self._orch.process(user_input)


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
    # Phase 4.0-R2.2: 入口收敛 —— LongLoop 不再直接注入 Orchestrator，
    # 而是通过 LongLoopOrchestratorAdapter 先 100% 进入 RuntimePipeline 生命周期。
    # Orchestrator 仍然是业务回复的实现者（通过 pipeline.run() 内部 fallback 调用），
    # 但所有调用现在都有 audit / persistence / event_sink（不再是 Orchestrator.direct）。
    orchestrator_adapter = LongLoopOrchestratorAdapter(orch)

    def _on_goodbye() -> None:
        print("羽依: 嗯，下次见。我会记得今天聊过的话。")

    def _on_reply(user_input: str, reply: str) -> None:
        print("羽依: " + reply + "\n")

    # Phase 5.0-A: 使用 LongLoop 替换裸 while True
    # - 支持 Ctrl+C / EOF / exit 关键字
    # - 退出前自动 final checkpoint
    # - 异常被隔离,不影响退出流程
    # - 旧模式: LongLoop 未注入 checkpoint_provider 时行为与之前等价
    loop = LongLoop(
        input_provider=lambda: input("你: "),
        orchestrator=orchestrator_adapter,  # R2.2：注入 Adapter（唯一入口 = RuntimePipeline）
        on_reply=_on_reply,
        on_goodbye=_on_goodbye,
        checkpoint_provider=None,  # Phase 5.0-A 内仅提供框架,具体 checkpoint 落盘由后续阶段提供
    )
    loop.run()


if __name__ == "__main__":
    print("🔴 进入 __main__")
    main()