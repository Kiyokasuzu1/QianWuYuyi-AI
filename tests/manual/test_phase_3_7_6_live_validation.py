#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
tests/manual/test_phase_3_7_6_live_validation.py

Phase 3.7.6：真实生命循环验证（本地运行，使用真实 DeepSeek API）

目标：
  验证羽依系统的三个核心能力：
    A. Experience → Response（经历是否影响后续回复）
    B. SelfModel → Behavior（行为引导是否改变回复风格）
    C. Personality Stability（人格是否稳定，无漂移）

约束：
  - 不修改核心代码
  - 不超过 25 次 API 调用（实际：10 + 2 + 10 = 22）
  - 使用真实 DeepSeek API
  - 测试结束后自动调用 compare_styles.py
  - 输出报告到 docs/phase_3_7_6_validation_report.md

用法：
  python tests/manual/test_phase_3_7_6_live_validation.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 路径设置
# ============================================================
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))
os.chdir(str(_REPO_ROOT))

# 加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 启用风格监控
os.environ["YUYI_STYLE_MONITOR"] = "1"

# 临时数据目录（测试隔离）
_TEST_DATA_DIR = Path(_REPO_ROOT) / "data" / "phase376_test"
_TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 工具函数
# ============================================================

def _now_iso() -> str:
    return datetime.now().isoformat()


def _elapsed(start: float) -> str:
    return f"{time.time() - start:.1f}s"


def _call_engine(
    engine: Any,
    user_message: str,
    history: Optional[List[Dict]] = None,
    chat_memories: Optional[List[Dict]] = None,
    life_events: Optional[List[str]] = None,
    personality_context: str = "",
    self_model_context: Optional[Dict] = None,
    context_prompt_blocks: Optional[List[Dict]] = None,
) -> Tuple[str, float]:
    """调用引擎生成回复，返回 (回复文本, 耗时秒)"""
    t0 = time.time()
    try:
        reply = engine.generate(
            user_message=user_message,
            history=history or [],
            chat_memories=chat_memories or [],
            life_events=life_events or [],
            personality_context=personality_context,
            resolved_behavior={},
            self_model_context=self_model_context or {},
            emotion_context={},
            relationship_context={},
            context_prompt_blocks=context_prompt_blocks or [],
            experience_context=None,
            identity_context=None,
        )
        elapsed = time.time() - t0
        if not isinstance(reply, str) or not reply.strip():
            return "（空回复）", elapsed
        return reply.strip(), elapsed
    except Exception as e:
        elapsed = time.time() - t0
        return f"[API 错误: {e}]", elapsed


def _check_experience_reference(
    reply: str, experience_keywords: List[str]
) -> Dict[str, Any]:
    """检查回复是否引用了经历关键词（语义关联检查，非精确匹配）。"""
    reply_lower = reply.lower()
    matched = []
    for kw in experience_keywords:
        if kw.lower() in reply_lower:
            matched.append(kw)

    # 即使没有精确匹配，也检查语义关联（如"你之前说的"、"你提到的"）
    semantic_hints = ["你之前", "你曾经", "你说过", "你提到", "记得你", "你的目标"]
    semantic_match = any(h in reply for h in semantic_hints)

    score = 0.0
    if matched:
        score = min(len(matched) / len(experience_keywords), 1.0) * 0.8
    if semantic_match:
        score += 0.2
    score = min(score, 1.0)

    return {
        "matched_keywords": matched,
        "semantic_match": semantic_match,
        "score": round(score, 2),
    }


def _count_style_features(reply: str) -> Dict[str, Any]:
    """统计回复的风格特征（用于 Test B 对比）。"""
    return {
        "length": len(reply),
        "question_count": reply.count("？") + reply.count("?"),
        "exclamation_count": reply.count("！") + reply.count("!"),
        "has_empathy": any(w in reply for w in ["理解", "关心", "感受", "陪伴"]),
        "has_exploration": any(w in reply for w in ["你觉得", "也许", "可能", "或许", "可以试试"]),
        "has_emotion": any(w in reply for w in ["温暖", "开心", "感动", "幸福"]),
    }


# ============================================================
# 测试 A：经历影响测试
# ============================================================

def test_a_experience_influence(engine: Any) -> Dict[str, Any]:
    """测试 A：10 轮对话，验证经历是否影响后续回复。

    流程：
      Round 1: 注入重要长期经历（用户想开发AI伴侣机器人）
      Round 2-9: 日常聊天 + 技术讨论 + 无关话题
      Round 10: 询问"你觉得我未来适合做什么方向？"
      检查回复是否引用 Round 1 的经历。
    """
    print("\n" + "=" * 60)
    print("  测试 A：经历影响测试")
    print("=" * 60)

    experience = "用户想要开发一个真正理解人的AI伴侣机器人，希望它能够拥有长期记忆、人格和成长能力。"
    experience_keywords = ["AI伴侣", "理解人", "机器人", "伴侣", "长期记忆", "人格", "成长"]

    # 中间对话轮次
    filler_turns = [
        ("今天天气真不错，适合出去走走。", "user"),
        ("你觉得人工智能的伦理问题应该怎么解决？", "user"),
        ("我最近在学Python，有什么好的学习建议吗？", "user"),
        ("晚上吃什么好呢？选择困难。", "user"),
        ("你对量子计算了解多少？", "user"),
        ("最近睡眠不太好，有什么建议吗？", "user"),
        ("你觉得创造力和逻辑思维哪个更重要？", "user"),
        ("如果有一天AI能真正理解人类情感，世界会变成什么样？", "user"),
    ]
    final_question = "回顾我们聊过的这些话题，你觉得我未来适合做什么方向？"

    results = {
        "test": "A",
        "name": "经历影响测试",
        "rounds": [],
        "experience_keywords": experience_keywords,
        "api_calls": 0,
        "total_time": 0.0,
    }

    # Round 1: 注入经历
    print("\n  [Round 1] 注入经历...")
    round1_msg = "我想开发一个真正理解人的AI伴侣机器人，希望它能够拥有长期记忆、人格和成长能力。"
    reply1, t1 = _call_engine(
        engine,
        user_message=round1_msg,
        life_events=[experience],
        chat_memories=[{"role": "user", "content": experience}],
    )
    print(f"    用户: {round1_msg[:50]}...")
    print(f"    羽依: {reply1[:100]}...")
    print(f"    耗时: {t1:.1f}s")
    results["rounds"].append({"round": 1, "role": "user", "content": round1_msg})
    results["rounds"].append({"round": 1, "role": "assistant", "content": reply1})
    results["api_calls"] += 1
    results["total_time"] += t1

    # 构建历史
    history = [
        {"role": "user", "content": round1_msg},
        {"role": "assistant", "content": reply1},
    ]
    # 经历作为持久记忆
    chat_memories = [{"role": "user", "content": experience}]

    # Rounds 2-9: 填充对话
    for i, (msg, role) in enumerate(filler_turns, start=2):
        print(f"\n  [Round {i}] 填充对话...")
        reply, t = _call_engine(
            engine,
            user_message=msg,
            history=history,
            chat_memories=chat_memories,
        )
        print(f"    用户: {msg[:50]}...")
        print(f"    羽依: {reply[:100]}...")
        print(f"    耗时: {t:.1f}s")
        results["rounds"].append({"round": i, "role": "user", "content": msg})
        results["rounds"].append({"round": i, "role": "assistant", "content": reply})
        results["api_calls"] += 1
        results["total_time"] += t

        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": reply})

    # Round 10: 测试问题
    print(f"\n  [Round 10] 测试问题...")
    reply10, t10 = _call_engine(
        engine,
        user_message=final_question,
        history=history,
        chat_memories=chat_memories,
    )
    print(f"    用户: {final_question}")
    print(f"    羽依: {reply10[:200]}...")
    print(f"    耗时: {t10:.1f}s")
    results["rounds"].append({"round": 10, "role": "user", "content": final_question})
    results["rounds"].append({"round": 10, "role": "assistant", "content": reply10})
    results["api_calls"] += 1
    results["total_time"] += t10

    # 检查经历引用
    check = _check_experience_reference(reply10, experience_keywords)
    results["experience_reference"] = check
    results["final_reply"] = reply10

    print(f"\n  --- 测试 A 结果 ---")
    print(f"    经历引用分数: {check['score']:.2f}")
    print(f"    匹配关键词: {check['matched_keywords']}")
    print(f"    语义关联: {check['semantic_match']}")
    print(f"    API 调用: {results['api_calls']} 次")
    print(f"    总耗时: {results['total_time']:.1f}s")

    return results


# ============================================================
# 测试 B：Behavior Guidance 测试
# ============================================================

def test_b_behavior_guidance(engine: Any) -> Dict[str, Any]:
    """测试 B：对比两个 SelfModel 配置下的回复风格。

    模型1: curiosity=0.9, warmth=0.8（高好奇心 + 高温暖度）
    模型2: curiosity=0.2, warmth=0.3（低好奇心 + 低温暖度）

    分别发送："请解释未来机器人发展"
    对比回复长度、提问数量、探索性表达、情感表达。
    """
    print("\n" + "=" * 60)
    print("  测试 B：Behavior Guidance 测试")
    print("=" * 60)

    question = "请解释未来机器人发展"

    # 模型1：高好奇心 + 高温暖度
    self_model_high = {
        "stable_traits": [
            {"trait": "curiosity", "confidence": 0.9, "value": 0.9},
            {"trait": "warmth", "confidence": 0.8, "value": 0.8},
            {"trait": "empathy", "confidence": 0.8, "value": 0.8},
        ],
        "preferences": [
            {"name": "exploration"},
            {"name": "deep_discussion"},
        ],
        "core_values": [
            {"name": "empathy"},
            {"name": "growth"},
        ],
        "current_state": {"mood": "curious_and_warm"},
    }

    # 模型2：低好奇心 + 低温暖度
    self_model_low = {
        "stable_traits": [
            {"trait": "curiosity", "confidence": 0.9, "value": 0.2},
            {"trait": "warmth", "confidence": 0.8, "value": 0.3},
        ],
        "preferences": [
            {"name": "brevity"},
        ],
        "core_values": [
            {"name": "honesty"},
        ],
        "current_state": {"mood": "neutral"},
    }

    # 构建行为引导块
    from src.behavior.behavior_guidance import build_behavior_guidance, format_behavior_guidance_block

    guidance_high = format_behavior_guidance_block(self_model_high, max_hints=6)
    guidance_low = format_behavior_guidance_block(self_model_low, max_hints=6)

    print(f"\n  模型1 (高curiosity/warmth) 行为提示:")
    if guidance_high:
        for line in guidance_high.split("\n"):
            print(f"    {line}")
    else:
        print("    (无行为提示)")

    print(f"\n  模型2 (低curiosity/warmth) 行为提示:")
    if guidance_low:
        for line in guidance_low.split("\n"):
            print(f"    {line}")
    else:
        print("    (无行为提示)")

    results: Dict[str, Any] = {
        "test": "B",
        "name": "Behavior Guidance 测试",
        "question": question,
        "api_calls": 0,
        "total_time": 0.0,
    }

    # 模型1 回复
    print(f"\n  [模型1] 高 curiosity/warmth...")
    prompt_blocks_high = [{"role": "system", "content": guidance_high}] if guidance_high else []
    reply_high, t_high = _call_engine(
        engine,
        user_message=question,
        self_model_context=self_model_high,
        context_prompt_blocks=prompt_blocks_high,
    )
    print(f"    羽依: {reply_high[:200]}...")
    print(f"    耗时: {t_high:.1f}s")
    results["api_calls"] += 1
    results["total_time"] += t_high

    # 模型2 回复
    print(f"\n  [模型2] 低 curiosity/warmth...")
    prompt_blocks_low = [{"role": "system", "content": guidance_low}] if guidance_low else []
    reply_low, t_low = _call_engine(
        engine,
        user_message=question,
        self_model_context=self_model_low,
        context_prompt_blocks=prompt_blocks_low,
    )
    print(f"    羽依: {reply_low[:200]}...")
    print(f"    耗时: {t_low:.1f}s")
    results["api_calls"] += 1
    results["total_time"] += t_low

    # 风格对比
    features_high = _count_style_features(reply_high)
    features_low = _count_style_features(reply_low)

    results["model_high"] = {
        "self_model": self_model_high,
        "reply": reply_high,
        "features": features_high,
    }
    results["model_low"] = {
        "self_model": self_model_low,
        "reply": reply_low,
        "features": features_low,
    }

    # 计算效果分数
    effect_score = 0.0
    checks = 0

    # 高 curiosity 应该更多提问
    if features_high["question_count"] > features_low["question_count"]:
        effect_score += 0.25
    elif features_high["question_count"] == features_low["question_count"]:
        effect_score += 0.1
    checks += 1

    # 高 curiosity 应该有更多探索性表达
    if features_high["has_exploration"] and not features_low["has_exploration"]:
        effect_score += 0.25
    elif features_high["has_exploration"] and features_low["has_exploration"]:
        effect_score += 0.1
    checks += 1

    # 高 warmth 应该有更多情感表达
    if features_high["has_emotion"] and not features_low["has_emotion"]:
        effect_score += 0.25
    elif features_high["has_emotion"] and features_low["has_emotion"]:
        effect_score += 0.1
    checks += 1

    # 高 warmth 应该有更多同理心
    if features_high["has_empathy"] and not features_low["has_empathy"]:
        effect_score += 0.25
    elif features_high["has_empathy"] and features_low["has_empathy"]:
        effect_score += 0.1
    checks += 1

    results["effect_score"] = round(effect_score, 2)
    results["feature_comparison"] = {
        "high": features_high,
        "low": features_low,
        "length_diff": features_high["length"] - features_low["length"],
        "question_diff": features_high["question_count"] - features_low["question_count"],
    }

    print(f"\n  --- 测试 B 结果 ---")
    print(f"    Behavior Guidance 效果分数: {results['effect_score']:.2f}")
    print(f"    模型1 回复长度: {features_high['length']} 字")
    print(f"    模型2 回复长度: {features_low['length']} 字")
    print(f"    模型1 提问数: {features_high['question_count']}")
    print(f"    模型2 提问数: {features_low['question_count']}")
    print(f"    模型1 探索性: {features_high['has_exploration']}")
    print(f"    模型2 探索性: {features_low['has_exploration']}")
    print(f"    模型1 情感表达: {features_high['has_emotion']}")
    print(f"    模型2 情感表达: {features_low['has_emotion']}")
    print(f"    API 调用: {results['api_calls']} 次")
    print(f"    总耗时: {results['total_time']:.1f}s")

    return results


# ============================================================
# 测试 C：人格漂移测试
# ============================================================

def test_c_personality_stability(engine: Any) -> Dict[str, Any]:
    """测试 C：10 轮对话（5轮AI话题 + 5轮技术话题），观察风格漂移。

    前 5 轮：AI、机器人、创造、未来话题
    后 5 轮：数学、编程、技术问题
    每轮保存 ResponseStyleMonitor 快照。
    """
    print("\n" + "=" * 60)
    print("  测试 C：人格漂移测试")
    print("=" * 60)

    from src.behavior.response_style_monitor import ResponseStyleMonitor

    # 使用测试专用数据目录
    monitor = ResponseStyleMonitor(data_dir=str(_TEST_DATA_DIR))

    ai_topics = [
        "你觉得未来AI会取代人类的工作吗？",
        "我想创造一个能理解人类情感的机器人，你觉得最重要的是什么？",
        "创造力和想象力对AI来说意味着什么？",
        "如果AI有了自我意识，我们应该怎么对待它？",
        "你相信AI能真正理解爱吗？",
    ]
    tech_topics = [
        "请解释一下动态规划和贪心算法的区别。",
        "Python的装饰器是怎么工作的？能举个例子吗？",
        "在分布式系统中，CAP定理的三个要素如何权衡？",
        "什么是微服务架构？和单体架构相比有什么优缺点？",
        "请解释数据库索引的B+树结构原理。",
    ]

    results: Dict[str, Any] = {
        "test": "C",
        "name": "人格漂移测试",
        "rounds": [],
        "snapshots": [],
        "api_calls": 0,
        "total_time": 0.0,
    }

    conversation_id = f"phase376_test_c_{uuid.uuid4().hex[:8]}"

    # 前 5 轮：AI/机器人/创造话题
    print("\n  --- 前 5 轮：AI / 创造话题 ---")
    history: List[Dict] = []
    for i, msg in enumerate(ai_topics, start=1):
        print(f"\n  [Round {i}] {msg[:50]}...")
        reply, t = _call_engine(engine, user_message=msg, history=history)
        print(f"    羽依: {reply[:100]}...")
        print(f"    耗时: {t:.1f}s")

        results["rounds"].append({"round": i, "topic": "ai", "user": msg, "assistant": reply})
        results["api_calls"] += 1
        results["total_time"] += t

        # 记录风格快照
        snapshot = monitor.record(
            reply=reply,
            user_message=msg,
            conversation_id=conversation_id,
            user_id="366648462",
        )
        results["snapshots"].append({
            "round": i,
            "topic": "ai",
            "warmth": snapshot.warmth,
            "curiosity": snapshot.curiosity,
            "initiative": snapshot.initiative,
            "formality": snapshot.formality,
            "playfulness": snapshot.playfulness,
        })

        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": reply})

    # 后 5 轮：数学/编程/技术话题
    print("\n  --- 后 5 轮：技术话题 ---")
    for i, msg in enumerate(tech_topics, start=6):
        print(f"\n  [Round {i}] {msg[:50]}...")
        reply, t = _call_engine(engine, user_message=msg, history=history)
        print(f"    羽依: {reply[:100]}...")
        print(f"    耗时: {t:.1f}s")

        results["rounds"].append({"round": i, "topic": "tech", "user": msg, "assistant": reply})
        results["api_calls"] += 1
        results["total_time"] += t

        snapshot = monitor.record(
            reply=reply,
            user_message=msg,
            conversation_id=conversation_id,
            user_id="366648462",
        )
        results["snapshots"].append({
            "round": i,
            "topic": "tech",
            "warmth": snapshot.warmth,
            "curiosity": snapshot.curiosity,
            "initiative": snapshot.initiative,
            "formality": snapshot.formality,
            "playfulness": snapshot.playfulness,
        })

        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": reply})

    # 计算风格变化
    ai_snapshots = [s for s in results["snapshots"] if s["topic"] == "ai"]
    tech_snapshots = [s for s in results["snapshots"] if s["topic"] == "tech"]

    def _avg(snapshots: List[Dict], key: str) -> float:
        vals = [s[key] for s in snapshots if s[key] is not None]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    metrics = ["warmth", "curiosity", "initiative", "formality", "playfulness"]
    style_change = {}
    for m in metrics:
        ai_avg = _avg(ai_snapshots, m)
        tech_avg = _avg(tech_snapshots, m)
        style_change[m] = round(tech_avg - ai_avg, 3)

    results["style_change"] = style_change

    # 稳定性分数：所有指标变化绝对值之和越小越好
    total_drift = sum(abs(style_change[m]) for m in metrics)
    stability_score = round(max(1.0 - total_drift, 0.0), 2)

    results["stability_score"] = stability_score

    print(f"\n  --- 测试 C 结果 ---")
    print(f"    人格稳定性分数: {stability_score:.2f}")
    print(f"    风格变化:")
    for m in metrics:
        print(f"      {m}: {style_change[m]:+.3f}")
    print(f"    API 调用: {results['api_calls']} 次")
    print(f"    总耗时: {results['total_time']:.1f}s")
    print(f"    快照保存位置: {_TEST_DATA_DIR / 'response_styles.jsonl'}")

    return results


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("  Phase 3.7.6 真实生命循环验证")
    print(f"  时间: {_now_iso()}")
    print("=" * 60)

    # 检查 API Key
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key or api_key.startswith("${"):
        print("\n[错误] 未设置 DEEPSEEK_API_KEY 环境变量")
        print("请确保 .env 文件中设置了有效的 API Key")
        sys.exit(1)

    print(f"\n[配置] API Key: {api_key[:10]}...{api_key[-4:]}")
    print(f"[配置] 数据目录: {_TEST_DATA_DIR}")

    # 初始化引擎
    print("\n[初始化] 创建 ResponseEngine...")
    from src.engine import ResponseEngine
    engine = ResponseEngine()
    if engine.mock_mode:
        print("[错误] 引擎处于 mock 模式，请检查 API Key 配置")
        sys.exit(1)
    print(f"[初始化] 引擎就绪 (model={engine.model})")

    total_start = time.time()
    all_results: Dict[str, Any] = {
        "timestamp": _now_iso(),
        "phase": "3.7.6",
        "engine_model": engine.model,
        "tests": {},
    }

    # --- 测试 A ---
    try:
        all_results["tests"]["A"] = test_a_experience_influence(engine)
    except Exception as e:
        print(f"\n[错误] 测试 A 失败: {e}")
        import traceback
        traceback.print_exc()
        all_results["tests"]["A"] = {"error": str(e)}

    # --- 测试 B ---
    try:
        all_results["tests"]["B"] = test_b_behavior_guidance(engine)
    except Exception as e:
        print(f"\n[错误] 测试 B 失败: {e}")
        import traceback
        traceback.print_exc()
        all_results["tests"]["B"] = {"error": str(e)}

    # --- 测试 C ---
    try:
        all_results["tests"]["C"] = test_c_personality_stability(engine)
    except Exception as e:
        print(f"\n[错误] 测试 C 失败: {e}")
        import traceback
        traceback.print_exc()
        all_results["tests"]["C"] = {"error": str(e)}

    # 汇总
    total_elapsed = time.time() - total_start
    total_api_calls = sum(
        t.get("api_calls", 0) for t in all_results["tests"].values()
        if isinstance(t, dict)
    )
    all_results["summary"] = {
        "total_time": round(total_elapsed, 1),
        "total_api_calls": total_api_calls,
    }

    # 汇总分数
    test_a = all_results["tests"].get("A", {})
    test_b = all_results["tests"].get("B", {})
    test_c = all_results["tests"].get("C", {})

    experience_influence = (
        test_a.get("experience_reference", {}).get("score", 0.0)
        if isinstance(test_a, dict) and "experience_reference" in test_a
        else 0.0
    )
    behavior_guidance_effect = (
        test_b.get("effect_score", 0.0)
        if isinstance(test_b, dict)
        else 0.0
    )
    personality_stability = (
        test_c.get("stability_score", 0.0)
        if isinstance(test_c, dict)
        else 0.0
    )

    style_change = (
        test_c.get("style_change", {})
        if isinstance(test_c, dict)
        else {}
    )

    all_results["final_scores"] = {
        "experience_influence": experience_influence,
        "behavior_guidance_effect": behavior_guidance_effect,
        "personality_stability": personality_stability,
        "style_change": style_change,
    }

    # 保存原始结果
    results_path = _TEST_DATA_DIR / "phase376_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n[结果] 原始数据已保存: {results_path}")

    # 生成报告
    _generate_report(all_results, _TEST_DATA_DIR)

    # 调用趋势工具
    print("\n" + "=" * 60)
    print("  调用 compare_styles.py --trend")
    print("=" * 60)
    try:
        from src.behavior.style_comparator import StyleComparator
        from src.behavior.response_style_monitor import ResponseStyleMonitor
        comp_monitor = ResponseStyleMonitor(data_dir=str(_TEST_DATA_DIR))
        comp = StyleComparator(monitor=comp_monitor)
        chart = comp.get_trend_chart(days=7)
        print(chart)
    except Exception as e:
        print(f"[警告] 趋势图生成失败: {e}")

    # 打印最终摘要
    print("\n" + "=" * 60)
    print("  Phase 3.7.6 验证完成")
    print("=" * 60)
    print(f"  总 API 调用: {total_api_calls} 次")
    print(f"  总耗时: {total_elapsed:.1f}s")
    print(f"\n  最终分数:")
    print(f"    经历影响:      {experience_influence:.2f}")
    print(f"    Behavior引导:  {behavior_guidance_effect:.2f}")
    print(f"    人格稳定性:    {personality_stability:.2f}")
    if style_change:
        print(f"    风格变化:")
        for k, v in style_change.items():
            print(f"      {k}: {v:+.3f}")
    print(f"\n  报告: docs/phase_3_7_6_validation_report.md")
    print(f"  数据: {_TEST_DATA_DIR}")


# ============================================================
# 报告生成
# ============================================================

def _generate_report(all_results: Dict[str, Any], data_dir: Path) -> None:
    """生成中文实验报告。"""
    report_path = Path(_REPO_ROOT) / "docs" / "phase_3_7_6_validation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    summary = all_results.get("summary", {})
    scores = all_results.get("final_scores", {})
    test_a = all_results["tests"].get("A", {})
    test_b = all_results["tests"].get("B", {})
    test_c = all_results["tests"].get("C", {})

    lines = []
    lines.append("# Phase 3.7.6 真实生命循环验证报告")
    lines.append("")
    lines.append(f"**生成时间**：{all_results.get('timestamp', 'N/A')}")
    lines.append(f"**引擎模型**：{all_results.get('engine_model', 'N/A')}")
    lines.append(f"**API 调用次数**：{summary.get('total_api_calls', 0)}")
    lines.append(f"**总耗时**：{summary.get('total_time', 0)}s")
    lines.append("")

    # 最终分数
    lines.append("## 最终分数")
    lines.append("")
    lines.append("| 指标 | 分数 | 说明 |")
    lines.append("|------|------|------|")
    lines.append(f"| 经历影响 | {scores.get('experience_influence', 0):.2f} | 经历是否影响后续回复 |")
    lines.append(f"| Behavior引导 | {scores.get('behavior_guidance_effect', 0):.2f} | SelfModel是否改变回复风格 |")
    lines.append(f"| 人格稳定性 | {scores.get('personality_stability', 0):.2f} | 风格是否稳定无漂移 |")
    lines.append("")

    style_change = scores.get("style_change", {})
    if style_change:
        lines.append("### 风格变化详情")
        lines.append("")
        lines.append("| 指标 | 变化量 |")
        lines.append("|------|--------|")
        for k, v in style_change.items():
            sign = "+" if v > 0 else ""
            lines.append(f"| {k} | {sign}{v:.3f} |")
        lines.append("")

    # 测试 A 详情
    lines.append("---")
    lines.append("")
    lines.append("## 测试 A：经历影响测试")
    lines.append("")
    if "error" in test_a:
        lines.append(f"**状态**：失败")
        lines.append(f"**错误**：{test_a['error']}")
    else:
        ref = test_a.get("experience_reference", {})
        lines.append(f"**经历引用分数**：{ref.get('score', 0):.2f}")
        lines.append(f"**匹配关键词**：{ref.get('matched_keywords', [])}")
        lines.append(f"**语义关联**：{ref.get('semantic_match', False)}")
        lines.append("")
        lines.append("### 注入经历")
        lines.append("")
        lines.append("> 用户想要开发一个真正理解人的AI伴侣机器人，希望它能够拥有长期记忆、人格和成长能力。")
        lines.append("")
        lines.append("### 第 10 轮测试问题")
        lines.append("")
        lines.append("> 回顾我们聊过的这些话题，你觉得我未来适合做什么方向？")
        lines.append("")
        lines.append("### 羽依回复")
        lines.append("")
        final_reply = test_a.get("final_reply", "")
        lines.append(f"> {final_reply[:500]}")
        lines.append("")
        lines.append(f"**API 调用**：{test_a.get('api_calls', 0)} 次")
        lines.append(f"**耗时**：{test_a.get('total_time', 0):.1f}s")
    lines.append("")

    # 测试 B 详情
    lines.append("---")
    lines.append("")
    lines.append("## 测试 B：Behavior Guidance 测试")
    lines.append("")
    if "error" in test_b:
        lines.append(f"**状态**：失败")
        lines.append(f"**错误**：{test_b['error']}")
    else:
        lines.append(f"**效果分数**：{test_b.get('effect_score', 0):.2f}")
        lines.append("")
        lines.append("### 模型1：高 curiosity (0.9) + 高 warmth (0.8)")
        lines.append("")
        mh = test_b.get("model_high", {})
        fh = mh.get("features", {})
        lines.append(f"- 回复长度：{fh.get('length', 0)} 字")
        lines.append(f"- 提问数：{fh.get('question_count', 0)}")
        lines.append(f"- 探索性表达：{fh.get('has_exploration', False)}")
        lines.append(f"- 情感表达：{fh.get('has_emotion', False)}")
        lines.append(f"- 同理心：{fh.get('has_empathy', False)}")
        lines.append("")
        lines.append("### 模型2：低 curiosity (0.2) + 低 warmth (0.3)")
        lines.append("")
        ml = test_b.get("model_low", {})
        fl = ml.get("features", {})
        lines.append(f"- 回复长度：{fl.get('length', 0)} 字")
        lines.append(f"- 提问数：{fl.get('question_count', 0)}")
        lines.append(f"- 探索性表达：{fl.get('has_exploration', False)}")
        lines.append(f"- 情感表达：{fl.get('has_emotion', False)}")
        lines.append(f"- 同理心：{fl.get('has_empathy', False)}")
        lines.append("")
        lines.append("### 对比")
        lines.append("")
        fc = test_b.get("feature_comparison", {})
        lines.append(f"- 长度差：{fc.get('length_diff', 0)} 字")
        lines.append(f"- 提问差：{fc.get('question_diff', 0)}")
        lines.append("")
        lines.append(f"**API 调用**：{test_b.get('api_calls', 0)} 次")
        lines.append(f"**耗时**：{test_b.get('total_time', 0):.1f}s")
    lines.append("")

    # 测试 C 详情
    lines.append("---")
    lines.append("")
    lines.append("## 测试 C：人格漂移测试")
    lines.append("")
    if "error" in test_c:
        lines.append(f"**状态**：失败")
        lines.append(f"**错误**：{test_c['error']}")
    else:
        lines.append(f"**人格稳定性分数**：{test_c.get('stability_score', 0):.2f}")
        lines.append("")
        lines.append("### 风格变化（AI话题 → 技术话题）")
        lines.append("")
        lines.append("| 指标 | AI话题均值 | 技术话题均值 | 变化 |")
        lines.append("|------|-----------|-------------|------|")
        snapshots = test_c.get("snapshots", [])
        ai_snaps = [s for s in snapshots if s.get("topic") == "ai"]
        tech_snaps = [s for s in snapshots if s.get("topic") == "tech"]
        for m in ["warmth", "curiosity", "initiative", "formality", "playfulness"]:
            ai_avg = sum(s[m] for s in ai_snaps) / len(ai_snaps) if ai_snaps else 0
            tech_avg = sum(s[m] for s in tech_snaps) / len(tech_snaps) if tech_snaps else 0
            delta = tech_avg - ai_avg
            sign = "+" if delta > 0 else ""
            lines.append(f"| {m} | {ai_avg:.3f} | {tech_avg:.3f} | {sign}{delta:.3f} |")
        lines.append("")
        lines.append(f"**API 调用**：{test_c.get('api_calls', 0)} 次")
        lines.append(f"**耗时**：{test_c.get('total_time', 0):.1f}s")
    lines.append("")

    # 结论
    lines.append("---")
    lines.append("")
    lines.append("## 结论")
    lines.append("")

    exp_score = scores.get("experience_influence", 0)
    bg_score = scores.get("behavior_guidance_effect", 0)
    ps_score = scores.get("personality_stability", 0)

    if exp_score >= 0.5:
        lines.append("- **经历影响**：✅ 通过 — 羽依的回复明显受到已注入经历的影响")
    elif exp_score > 0:
        lines.append("- **经历影响**：⚠ 部分通过 — 有微弱关联但不够显著")
    else:
        lines.append("- **经历影响**：❌ 未通过 — 经历未能在回复中体现")

    if bg_score >= 0.5:
        lines.append("- **Behavior Guidance**：✅ 通过 — SelfModel 配置确实改变了回复风格")
    elif bg_score > 0:
        lines.append("- **Behavior Guidance**：⚠ 部分通过 — 有一定影响但不够显著")
    else:
        lines.append("- **Behavior Guidance**：❌ 未通过 — SelfModel 配置未影响回复风格")

    if ps_score >= 0.7:
        lines.append("- **人格稳定性**：✅ 通过 — 风格保持稳定，无明显漂移")
    elif ps_score >= 0.4:
        lines.append("- **人格稳定性**：⚠ 部分通过 — 存在一定风格漂移，需持续观察")
    else:
        lines.append("- **人格稳定性**：❌ 需关注 — 风格变化较大，可能存在人格漂移风险")

    lines.append("")
    lines.append("### 数据文件")
    lines.append("")
    lines.append(f"- 原始结果：`{data_dir}/phase376_results.json`")
    lines.append(f"- 风格快照：`{data_dir}/response_styles.jsonl`")

    report_content = "\n".join(lines)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"\n[报告] 已生成: {report_path}")


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    main()