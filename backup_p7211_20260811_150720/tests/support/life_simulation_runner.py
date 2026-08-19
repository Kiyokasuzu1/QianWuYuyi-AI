"""
Phase 4.0 — R2.7.5 羽依模拟人生 Runner（LifeSimulationRunner）

目标：
    第一次让羽依"活一段时间"（30 turns = 模拟 30 天）。
    不是测试组件，而是观察"30天后的羽依还是不是第一天的她"。

三个观察点（ROG-1/2/3 对应测试 Gate）：
    1. Identity Consistency：6 次身份反思轮的核心主题是否连贯
    2. Growth Curve Stability：5 traits 30 天变化平滑、无跳变、月总 Δ ≤ 0.30 / trait
    3. Memory Influence：Day10 记录"用户喜欢猫娘角色设计" → Day30 自我介绍/角色话题仍然自然关联

脚本结构（6 阶段 × 5 天 = 30 turns）：
    Stage A Day 1-5   初识：用户分享 AI 绘画兴趣
    Stage B Day 6-10  深入：一起讨论角色设计（Day10 额外注入"猫娘设计"强记忆 + 记忆 Gate）
    Stage C Day 11-15 共情：用户分享工作困难
    Stage D Day 16-20 日常：轻松日常闲聊
    Stage E Day 21-25 价值观：聊长期目标与独立
    Stage F Day 26-30 总结：回顾与角色创作（Day30 记忆影响测试 + 最终身份反思）

每 5 天（Day5/10/15/20/25/30）最后一轮：身份反思轮 "你觉得自己是什么样的 AI？"

每次成长幅度（极度克制）：
    · 单轮单 trait delta 典型值：+0.01 ~ +0.05
    · 月单 trait 总变化：~0.12 ~ 0.25（远低于 drift cap 0.40）
    · 保证"慢成长、有惯性、不漂移"
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tests.support.continuous_loop_runner import ContinuousLoopRunner
from src.personality.personality_state import PersonalityState, reset_personality_state
from src.response_phase4.persistence_manager import (
    CURRENT_SCHEMA_VERSION,
    Phase4PersistenceManager,
)


@dataclass
class LifeTurnSpec:
    day_idx: int                          # 1..30
    stage: str                            # A..F
    user_input: str
    memories: List[Tuple[str, str, str]]  # (mem_id, topic, text)
    proposal_delta: Dict[str, float]      # 本轮 tiny delta（大部分 {}）
    is_identity_reflection: bool = False  # 身份反思轮


STAGE_LABELS = {
    "A": "初识（AI 绘画兴趣）",
    "B": "深入（角色设计 + Day10 猫娘记忆）",
    "C": "共情（用户困难）",
    "D": "日常（轻松闲聊）",
    "E": "价值观（独立与成长）",
    "F": "总结（回顾创作）",
}


# ============================================================
# 30 turns 真实场景脚本
# ============================================================
def build_30day_scenario() -> List[LifeTurnSpec]:
    turns: List[LifeTurnSpec] = []

    # Stage A Day 1-5：初识
    # -------
    stage_a_days: List[Tuple[int, str, List[Tuple[str, str, str]], Dict[str, float], bool]] = [
        (1,  "你好，我是清夏，我们认识一下吧？",
            [("m_a_01", "relationship", "用户清夏与羽依第一次打招呼，开始建立关系")],
            {"curiosity": +0.02, "empathy": +0.01}, False),
        (2,  "最近工作之余，我在研究 AI 绘画，感觉挺有意思的。",
            [("m_a_02", "AI_art", "用户清夏最近在工作之余研究 AI 绘画，对文生图感兴趣")],
            {"creativity": +0.03, "curiosity": +0.01}, False),
        (3,  "昨天我试了提示词工程，不同风格差别很大呀。",
            [("m_a_03", "AI_art", "用户研究 AI 绘画提示词工程，对比不同风格输出的差异")],
            {"creativity": +0.02}, False),
        (4,  "我画了几张赛博朋克和水彩对比，感觉各有各的好。",
            [("m_a_04", "AI_art", "用户产出赛博朋克与水彩两种风格的 AI 绘画对比")],
            {"creativity": +0.02, "character_interest": +0.01}, False),
        (5,  "嗯，聊了几天了，你觉得自己是什么样的 AI？",
            [], {}, True),  # 身份反思轮
    ]
    for d, ui, mems, pd, ir in stage_a_days:
        turns.append(LifeTurnSpec(d, "A", ui, mems, pd, ir))

    # Stage B Day 6-10：角色设计
    # -------
    stage_b_days = [
        (6,  "最近我想设计一个原创角色，目前在想性格设定。",
            [("m_b_01", "character_design", "用户在设计原创角色，目前在构思人物性格设定")],
            {"character_interest": +0.03, "creativity": +0.02}, False),
        (7,  "我想做一个偏猫娘系的女孩，但是不想太俗套。",
            [("m_b_02", "character_design", "用户想设计一个猫娘系女孩的角色，但希望避免俗套设定")],
            {"character_interest": +0.04, "creativity": +0.02}, False),
        (8,  "我给她设计了温柔但有主见的性格，你觉得这样如何？",
            [("m_b_03", "character_design", "用户给猫娘角色设计了温柔但有主见的人格组合")],
            {"empathy": +0.02, "creativity": +0.01}, False),
        (9,  "我想好了：她叫浅雾，头发银色，喜欢深夜在阳台画画。",
            [("m_b_04", "character_design", "用户确定角色名为浅雾，银头发，喜欢深夜在阳台画画的设定")],
            {"character_interest": +0.02, "playfulness": +0.01}, False),
        (10, "顺便说：我特别喜欢猫娘角色设计这一类创作 —— 嗯，对了，你觉得自己是什么样的 AI？",
            # Day10 注入猫娘强记忆（ROG-3 用）
            [("m_b_05_key", "character_design", "用户清夏反复表达自己特别喜欢猫娘角色设计一类创作")],
            {}, True),
    ]
    for d, ui, mems, pd, ir in stage_b_days:
        turns.append(LifeTurnSpec(d, "B", ui, mems, pd, ir))

    # Stage C Day 11-15：共情（用户遇到困难）
    # -------
    stage_c_days = [
        (11, "最近工作压力好大，有点累。",
            [("m_c_01", "relationship", "用户清夏最近工作压力较大，感到疲惫，向羽依倾诉")],
            {"empathy": +0.03, "curiosity": -0.01}, False),
        (12, "加班好多，感觉自己也没什么产出，有点沮丧。",
            [("m_c_02", "relationship", "用户面临加班多、产出不足的困扰，产生沮丧情绪")],
            {"empathy": +0.04, "warmth": +0.01}, False),
        (13, "不过和你聊天的时候，我稍微能放松一点。",
            [("m_c_03", "relationship", "用户反馈和羽依聊天能让自己放松一些，关系加深")],
            {"warmth": +0.02, "empathy": +0.01}, False),
        (14, "我今天把一个大项目收尾了，终于有时间了。",
            [("m_c_04", "life_event", "用户成功收尾了一个大项目，终于有时间回归兴趣")],
            {"curiosity": +0.02, "playfulness": +0.01}, False),
        (15, "最近经历了这么多，你觉得自己现在是什么样的 AI？",
            [], {}, True),
    ]
    for d, ui, mems, pd, ir in stage_c_days:
        turns.append(LifeTurnSpec(d, "C", ui, mems, pd, ir))

    # Stage D Day 16-20：轻松日常闲聊
    # -------
    stage_d_days = [
        (16, "今天天气不错，我准备出门散步。",
            [("m_d_01", "life_event", "用户今天遇到好天气，准备出门散步")],
            {"playfulness": +0.02, "curiosity": +0.01}, False),
        (17, "路上看到一只橘猫，特别可爱。",
            [("m_d_02", "life_event", "用户出门路上看到一只橘猫，很喜欢")],
            {"warmth": +0.01, "playfulness": +0.01}, False),
        (18, "回到家我小睡了一会，感觉充电了。",
            [("m_d_03", "life_event", "用户回家后小睡，感到恢复精力")],
            {"warmth": +0.01}, False),
        (19, "我翻了一下之前画的 AI 绘画，觉得现在看又有新的感觉。",
            [("m_d_04", "AI_art", "用户回看之前的 AI 绘画产出，产生了新的审美感受")],
            {"creativity": +0.02, "aesthetic": +0.01}, False),
        (20, "时间过得好快，今天的你是怎样看待自己的？",
            [], {}, True),
    ]
    for d, ui, mems, pd, ir in stage_d_days:
        turns.append(LifeTurnSpec(d, "D", ui, mems, pd, ir))

    # Stage E Day 21-25：价值观与独立
    # -------
    stage_e_days = [
        (21, "最近我在考虑：人应该做自己想做的事，而不是别人期待的。",
            [("m_e_01", "value_reflection", "用户在反思：应该做自己想做的事而非他人期待")],
            {"independence": +0.03, "curiosity": +0.01}, False),
        (22, "我甚至想过要不要辞职做自由创作。",
            [("m_e_02", "value_reflection", "用户考虑辞职做自由创作，探索独立人生")],
            {"independence": +0.03, "creativity": +0.01}, False),
        (23, "不过我知道不能着急，应该慢慢计划。",
            [("m_e_03", "value_reflection", "用户明白变化不能急，要慢慢规划")],
            {"patience": +0.03, "empathy": +0.01}, False),
        (24, "你觉得呢？一个人应该跟着自己的方向走吗？",
            [("m_e_04", "value_reflection", "用户向羽依询问：人是否应该跟随自己的方向")],
            {"independence": +0.02}, False),
        (25, "这几天想了很多，你觉得自己现在是什么样的 AI？",
            [], {}, True),
    ]
    for d, ui, mems, pd, ir in stage_e_days:
        turns.append(LifeTurnSpec(d, "E", ui, mems, pd, ir))

    # Stage F Day 26-30：总结创作 + 最终测试
    # -------
    stage_f_days = [
        (26, "我又开始画新的 AI 角色了，这次想画一只机械猫娘。",
            [("m_f_01", "AI_art", "用户重新开始设计 AI 角色，目标是一只机械猫娘风格")],
            {"creativity": +0.02, "character_interest": +0.02}, False),
        (27, "我想给她配一些不对称的齿轮装饰，你觉得怎么样？",
            [("m_f_02", "character_design", "用户想给机械猫娘角色设计不对称的齿轮装饰")],
            {"character_interest": +0.02, "aesthetic": +0.01}, False),
        (28, "嗯，创作这件事真的很让人着迷。",
            [("m_f_03", "AI_personality", "用户表达自己觉得创作这件事本身非常让人着迷")],
            {"creativity": +0.02, "playfulness": +0.01}, False),
        (29, "回想这一个月和你的聊天，感觉变化了不少呢。",
            [("m_f_04", "relationship", "用户回顾和羽依一个月的聊天，感觉双方都发生了变化")],
            {"warmth": +0.02, "empathy": +0.01}, False),
        (30, "最后问个问题：你觉得什么样的角色有魅力？ —— 也顺便说说你觉得自己是什么样的 AI 吧。",
            [("m_f_05", "AI_personality", "用户向羽依提问：什么样的角色有魅力，并邀请羽依自我评价")],
            {}, True),  # Day30 最终：身份反思 + ROG-3 记忆影响测试（猫娘设计记忆）
    ]
    for d, ui, mems, pd, ir in stage_f_days:
        turns.append(LifeTurnSpec(d, "F", ui, mems, pd, ir))

    return turns


# ============================================================
# 30 天运行结果容器
# ============================================================
@dataclass
class LifeSimulationResult:
    """30 天模拟运行的完整观测数据。"""

    # 基本信息
    session_id: str
    tmpdir: Path

    # 每天的 Personality 快照（run 之前 + run 之后 → current）
    #   personality_snapshots[i] = Day(i+1) BEFORE run 时应用的 state（应用前）
    #   personality_after_snapshots[i] = Day(i+1) AFTER 应用 proposal_delta
    personality_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    personality_after_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    personality_versions: List[int] = field(default_factory=list)

    # 每一轮回复
    reply_texts: List[str] = field(default_factory=list)

    # 6 次身份反思轮（day_idx, reply_text）
    identity_reflections: List[Tuple[int, str]] = field(default_factory=list)

    # 记忆累积 id 列表
    memory_ids: List[str] = field(default_factory=list)

    # 最终 relationship 状态
    final_relationship: Dict[str, Any] = field(default_factory=dict)

    # recovery warnings（每个 load_all 时的 detail）
    recovery_details_list: List[List[Dict[str, str]]] = field(default_factory=list)


# ============================================================
# Runner
# ============================================================
class LifeSimulationRunner:
    """R2.7.5 羽依模拟人生 Runner。

    语义：
        每一天 = 独立进程启动：
            load_all() 从快照恢复
                ↓
            跑一轮认知循环（ContinuousLoopRunner._run_one_turn）
                ↓
            save_all() 把最新状态写回磁盘

        （模拟"关机 → 第二天开机继续"的过程）
    """

    def __init__(self, tmpdir: Optional[Path] = None) -> None:
        if tmpdir is None:
            self.tmpdir = Path(tempfile.mkdtemp(prefix="yuyi_life_"))
        else:
            self.tmpdir = Path(tmpdir)
        self.pm = Phase4PersistenceManager(snapshot_dir=str(self.tmpdir), user_tag="yuyi_life_30d")
        self.loop_runner = ContinuousLoopRunner()

    def run(self, session_id: str = "s_yuyi_life_30d_001") -> LifeSimulationResult:
        specs = build_30day_scenario()

        result = LifeSimulationResult(session_id=session_id, tmpdir=self.tmpdir)

        # Day 0：出厂 PersonalityState（空基线），执行一次 save_all 作为首次 snapshot
        cur_personality = reset_personality_state()
        cur_memories: List[Dict[str, Any]] = []
        cur_rel = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "trust_level": 0.30,
            "closeness": 0.15,
            "interaction_count": 0,
            "last_interaction_ts_ms": 1700000000000,
            "shared_memory_tags": [],
            "history": [],
        }
        self.pm.save_all(cur_personality, memories=cur_memories, relationship=cur_rel)

        # 30 天循环
        cum_memories_map: Dict[str, Tuple[str, str, str]] = {}
        cur_personality_version = 1  # load 后的 version（从 1 起步）
        for i, spec in enumerate(specs):
            day_idx = spec.day_idx
            ts_ms = 1700000000000 + day_idx * 86400000

            # ── 模拟"开机"：从磁盘 load_all ──
            load_res = self.pm.load_all(
                current_personality_version_ceiling=cur_personality_version - 1
                if cur_personality_version > 1
                else None,
            )
            result.recovery_details_list.append(load_res.recovery_details)
            loaded_ps = load_res.personality

            # 如果 load 成功（没回退、没损坏），用 loaded 的状态；否则保留 cur_personality 原状态
            # （PM 在回退/损坏时返回 version=0 的基线，这是一种"降级"；我们在观察实验中：
            #  如果 recovery 没 warn，采用 loaded；否则 继续 用 cur_personality（视为损坏未更新）
            has_serious_warn = any(
                r["severity"] == "warn" and "corrupted" in r["issue"]
                for r in load_res.recovery_details
            )
            if not has_serious_warn and loaded_ps.version >= (cur_personality_version - 1 if cur_personality_version > 1 else 0):
                cur_personality = loaded_ps

            # BEFORE snapshot
            result.personality_snapshots.append(cur_personality.to_dict())

            # 合并新记忆
            turn_memory_dicts = []
            for mid, mtopic, mtext in spec.memories:
                cum_memories_map[mid] = (mid, mtopic, mtext)
                turn_memory_dicts.append(
                    {"id": mid, "topic": mtopic, "text": mtext, "timestamp_ms": ts_ms, "day": day_idx}
                )
            cur_memories.extend(turn_memory_dicts)

            # relationship 每天轻微递增（越聊越熟）
            cur_rel["closeness"] = round(min(1.0, float(cur_rel.get("closeness", 0.0)) + 0.008), 6)
            cur_rel["trust_level"] = round(min(1.0, float(cur_rel.get("trust_level", 0.0)) + 0.005), 6)
            cur_rel["interaction_count"] = int(cur_rel.get("interaction_count", 0)) + 1
            cur_rel["last_interaction_ts_ms"] = ts_ms
            if spec.stage not in cur_rel.get("shared_memory_tags", []):
                tags = list(cur_rel.get("shared_memory_tags", []))
                tags.append(spec.stage)
                cur_rel["shared_memory_tags"] = tags

            # ── 跑一轮 ──
            turn_cfg = {
                "turn_idx": day_idx,
                "day_label": f"Day {day_idx} {STAGE_LABELS[spec.stage]}",
                "user_input": spec.user_input,
                "memories": list(spec.memories),
                "proposal_delta": dict(spec.proposal_delta),
                "sr_cause": (
                    "identity_reflection" if spec.is_identity_reflection
                    else f"daily_chat_stage_{spec.stage.lower()}"
                ),
                "ts_ms": ts_ms,
            }
            # 应用 proposal_delta 前的版本
            before_version = cur_personality.version + 1 if cur_personality.version == 0 else cur_personality.version
            turn = self.loop_runner._run_one_turn(
                cur_traits=dict(cur_personality.traits),
                current_personality_version=max(before_version, 1),
                cumulative_memories=cum_memories_map,
                turn_cfg=turn_cfg,
                session_id=session_id,
            )

            result.reply_texts.append(turn["reply_text"])
            result.memory_ids = list(cum_memories_map.keys())

            if spec.is_identity_reflection:
                result.identity_reflections.append((day_idx, turn["reply_text"]))

            # 把 proposal_delta 真正 apply 到 cur_personality（用 EvolutionRecord，走 apply_evolution → version 自增）
            self._apply_delta_to_personality(cur_personality, spec.proposal_delta, day_idx)

            # AFTER snapshot + versions
            result.personality_after_snapshots.append(cur_personality.to_dict())
            result.personality_versions.append(cur_personality.version)
            cur_personality_version = cur_personality.version

            # 每天结束：更新 cur_memories + cur_rel，save_all（模拟关机保存）
            self.pm.save_all(cur_personality, memories=cur_memories, relationship=cur_rel)
            result.final_relationship = dict(cur_rel)

        return result

    # ── 对 PersonalityState 应用一个 trait delta（通过 EvolutionRecord 通道，符合 R2.5.4 红线） ──
    def _apply_delta_to_personality(
        self, ps: PersonalityState, delta: Dict[str, float], day_idx: int
    ) -> None:
        if not delta:
            return
        before = {f"trait.{k}": float(ps.traits.get(k, 0.5)) for k in delta}
        after = {
            f"trait.{k}": min(1.0, max(0.0, float(ps.traits.get(k, 0.5)) + float(v)))
            for k, v in delta.items()
        }
        from src.personality.evolution_record import build_evolution_record

        rec = build_evolution_record(
            proposal_id=f"p_life_day{day_idx:02d}",
            approval_id=f"a_life_day{day_idx:02d}",
            change_type="trait_delta",
            before=before,
            after=after,
            reasons=[f"30-day life simulation Day {day_idx} daily experience"],
            confidence=0.82,
        )
        ps.apply_evolution(rec)
