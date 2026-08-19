# -*- coding: utf-8 -*-
"""
Phase 6.5 演示脚本:Token Optimization 节省比例示例。

运行:
    python scripts/demo_phase_6_5_token_opt.py

输出真实链路下 Token 优化前后的节省比例。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.runtime_pipeline import RuntimePipeline
from src.runtime.token_optimizer import (
    RuntimeTokenOptimizer,
    NoOpTokenOptimizer,
    estimate_tokens,
    build_token_usage,
)


class _DemoOrchestrator:
    """演示用 orchestrator:把收到的 user_message 原样回显。"""

    def __init__(self) -> None:
        self.last_msg: str = ""
        self.call_count: int = 0

    def process(self, user_message: str) -> str:
        self.call_count += 1
        self.last_msg = user_message
        return f"echo({len(user_message)} chars): {user_message[:40]}{'...' if len(user_message) > 40 else ''}"


def build_long_history(turns: int = 30) -> list:
    history = []
    for i in range(turns):
        history.append({
            "role": "user",
            "content": f"第 {i} 轮对话,用户问了一个关于 Python 编程与机器学习入门的问题,内容是:{'非常详细' * 8}",
        })
        history.append({
            "role": "assistant",
            "content": f"第 {i} 轮回答,我详细介绍了{'相关技术细节' * 8},并推荐了一些学习资源。",
        })
    return history


def build_memories(count: int = 12) -> list:
    return [
        {
            "content": f"记忆 #{i}:关于 {['编程', '音乐', '旅行', '美食', '运动'][i % 5]} 的重要事项,{'细节内容' * 5}",
            "importance": 0.2 + (i % 5) * 0.15,
            "emotion_tag": ["开心", "平静", "忧伤", "兴奋", "焦虑"][i % 5],
        }
        for i in range(count)
    ]


def main() -> None:
    user_msg = "最近想学深度学习,你能推荐一些入门路径吗?"
    history = build_long_history(turns=30)
    memories = build_memories(count=12)

    print("=" * 70)
    print("Phase 6.5 — Token Optimization Runtime Integration 演示")
    print("=" * 70)

    # --- 1) 估算输入总 tokens ---
    history_tokens = sum(
        estimate_tokens(m.get("content", "")) for m in history
    )
    memory_tokens = sum(
        estimate_tokens(m.get("content", "")) for m in memories
    )
    user_tokens = estimate_tokens(user_msg)
    raw_total = user_tokens + history_tokens + memory_tokens
    print(f"\n[输入维度]")
    print(f"  user_message     : {user_tokens:>5} tokens")
    print(f"  history (30 轮)  : {history_tokens:>5} tokens")
    print(f"  memories (12 条) : {memory_tokens:>5} tokens")
    print(f"  ───────────────")
    print(f"  优化前总计       : {raw_total:>5} tokens")

    # --- 2) 真实链路:带 token_optimizer ---
    print(f"\n[链路 A:启用 Token 优化]")
    orch_a = _DemoOrchestrator()
    opt = RuntimeTokenOptimizer(
        recent_turns=4,
        max_memories=4,
        use_llm_summary=False,
        use_memory_summary=True,
    )
    pipe_a = RuntimePipeline(orchestrator=orch_a, token_optimizer=opt)
    ctx_a = pipe_a.run({
        "user_message": user_msg,
        "history": history,
        "memories": memories,
    })
    print(f"  状态              : {ctx_a.state}")
    print(f"  Orchestrator 收到  : {len(orch_a.last_msg)} chars")
    usage_a = ctx_a.outputs["snapshot"].get("token_usage", {})
    print(f"  token_usage       :")
    print(f"    before_tokens   : {usage_a.get('before_tokens')}")
    print(f"    after_tokens    : {usage_a.get('after_tokens')}")
    print(f"    saved_tokens    : {usage_a.get('saved_tokens')}")
    print(f"    compression     : {usage_a.get('compression_ratio')}")
    print(f"    applied         : {usage_a.get('applied')}")

    # --- 3) 真实链路:不启用 token_optimizer ---
    print(f"\n[链路 B:未启用 Token 优化(默认行为)]")
    orch_b = _DemoOrchestrator()
    pipe_b = RuntimePipeline(orchestrator=orch_b, token_optimizer=None)
    ctx_b = pipe_b.run({"user_message": user_msg})
    print(f"  状态              : {ctx_b.state}")
    print(f"  Orchestrator 收到  : {len(orch_b.last_msg)} chars")
    snap_b = ctx_b.outputs.get("snapshot", {})
    print(f"  token_usage       : {'<未启用,不写入>' if 'token_usage' not in snap_b else 'present'}")

    # --- 4) 节省比例对比 ---
    print(f"\n[节省比例对比]")
    before_a = usage_a.get("before_tokens", 0)
    after_a = usage_a.get("after_tokens", 0)
    saved_a = max(0, before_a - after_a)
    pct = (saved_a / before_a * 100) if before_a > 0 else 0.0
    print(f"  链路 A (优化后)  : {after_a:>5} tokens (节省 {saved_a} tokens,约 {pct:.1f}%)")
    print(f"  链路 B (原样)    : {user_tokens:>5} tokens (仅 user_message,history/memories 尚未注入 Pipeline)")
    print()
    print("=" * 70)
    print("结论:启用 Token 优化后,进入 Orchestrator 的实际输入 tokens 显著减少。")
    print("     未启用时(默认),Pipeline 行为与 Phase 6.3 完全一致(零侵入)。")
    print("=" * 70)


if __name__ == "__main__":
    main()
