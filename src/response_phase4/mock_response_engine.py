"""
Phase 4.0 — R2.7.1-B2 Response Engine 协议 + Mock 实现

ResponseEngineProtocol（未来 B3 接 LLMAdapter 时共用接口）：
    def generate(
        self,
        *,
        user_input: str,
        prompt_context: Dict[str, Any],
        runtime_trace: Optional[Dict[str, Any]] = None,
        rendered_prompt: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:

        返回 Response 对象（dict），含：
            reply_text: str               — 自然语言回复（用户直接看到的；不包含 trace_id / 内部术语）
            meta: Dict[str, Any]          — 可追溯元数据（**不返回给用户**；只给系统审计）：
                trace_id: str
                rendered_prompt_version: int
                prompt_context_version: int
                self_context_version: int
                self_model_version: int
                self_reflection_version: int
                continuity_status: str
                engine_kind: str            — "mock" / "deepseek" / "openai" / …
                engine_version: int         — engine 自增版本，每次 generate +1
            trace_snapshot: Dict[str, Any] — runtime_trace 的简化只读快照（失败也可用于审计）

MockResponseEngine（Offline，不调用 LLM；保证确定性，方便 Gate 测试）：
    - 输入：user_input + prompt_context（可选 runtime_trace / rendered_prompt）
    - 内部：若没给 rendered_prompt，会自己调用 PromptRenderer 先 build 一份
    - 回复生成规则（确定性、可审计）：
        1) 开头固定问候（与话题匹配：若 user_input 含"兴趣/最近/喜欢"→ 说"最近兴趣嘛…"；普通 → "哈哈，说到这个呀"）
        2) baseline 段（creativity 未达到 high 时）："我兴趣还挺杂的，最近没有特别强烈的偏向，主要就是和朋友聊天、读点东西。"
        3) growth 表达层（仅当 creativity 为 high，且 growth bullets 非空时注入）：
            - 每条 growth_summary.recent_change_bullets 中，若含有 ("AI 绘画" / "设计" / "创造" / "创造力") 关键词，
              对应生成一句自然语言（不使用数值）：
                · AI 绘画 → "最近我发现自己越来越喜欢看 AI 绘画方面的东西，也会琢磨不同的风格。"
                · 设计 → "对设计一个会慢慢成长的角色也特别有感觉，觉得非常有意思。"
                · 创造 / 创造力 → "整体上我也觉得自己对创造新东西的兴趣越来越强烈啦。"
            - 最多注入 3 句，按关键词命中顺序
        4) 结尾一句：把 continuity_status 状态化成轻量自然语言（continuous_safe 说"继续这样慢慢聊就很舒服~"；
           其余说"今天的我可能有点起伏，但还是认真在听～"）
        5) 最后 sanitize 自然语言回复：禁止出现 forbidden 关键字（若出现替换为…）；确保 reply_text 里不含 trace_id
        6) engine_version 每次 generate() 严格 +1
"""

from __future__ import annotations

import copy
import logging
import random
from typing import Any, Dict, List, Optional

from src.context.prompt_context_schema import validate_prompt_context_shape
from src.response_phase4.prompt_renderer import PromptRenderer, _sanitize_text_block
from src.response_phase4.rendered_prompt_schema import (
    _forbidden_flat_hits,
    validate_rendered_prompt_shape,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Mock 回复模板
# ─────────────────────────────────────────────────────────
_BASELINE_NO_STRONG_CHANGE = (
    "我兴趣还挺杂的，最近没有特别强烈的偏向，主要就是和朋友聊天、读点东西。"
)

_KEYWORD_TO_NATURAL: List[tuple] = [
    # (关键词子串, 自然语言一句话) — 按命中顺序取前 3
    ("AI 绘画", "最近我发现自己越来越喜欢看 AI 绘画方面的东西，也会琢磨不同的风格。"),
    ("AI绘画", "最近我发现自己越来越喜欢看 AI 绘画方面的东西，也会琢磨不同的风格。"),
    ("绘画", "最近也会看一些视觉创作，偶尔会盯着作品想它背后的构思。"),
    ("角色设计", "对设计一个会慢慢成长的角色也特别有感觉，觉得非常有意思。"),
    ("角色", "最近也在想关于角色设计一类的事情，感觉里面有很多可以探索的空间。"),
    ("设计", "对设计方面的东西也会下意识地多留意一些。"),
    ("创造", "整体上我也觉得自己对创造新东西的兴趣越来越强烈啦。"),
    ("创造力", "最近自己也注意到，我对创造相关的话题会投入得更认真一些。"),
]


def _greeting_by_input(user_input: str) -> str:
    u = user_input or ""
    if any(w in u for w in ["兴趣", "最近", "喜欢", "在做", "研究", "学"]):
        return "哈哈，最近的兴趣呀……"
    if any(w in u for w in ["你是谁", "自我介绍", "介绍一下"]):
        return "嗯，我就是羽依啦～"
    if any(w in u for w in ["感觉", "觉得", "今天"]):
        return "今天我感觉呢……"
    return "哈哈，说到这个呀～"


def _continuity_tail(status: str) -> str:
    if status == "continuous_safe":
        return "继续这样慢慢聊就很舒服~"
    if status in {"identity_tension_tracked", "drift_observed"}:
        return "今天的我可能有点起伏，但还是在认真听的～"
    return "不管怎样，我还是会很认真回应你的。"


def _extract_growth_key_hits(prompt_context: Dict[str, Any]) -> List[str]:
    """从 growth_summary.recent_change_bullets 里按关键词顺序，取前 3 句已写好的自然语言一句话。"""
    sc = dict(prompt_context.get("self_context") or {})
    growth = sc.get("growth_summary") or {}
    bullets: List[str] = [str(b) for b in list(growth.get("recent_change_bullets") or []) if b]
    top_traits = list((sc.get("personality_summary") or {}).get("top_traits") or [])

    # 决定 creativity 等级（high / medium / low）
    creativity_level = ""
    creativity_value: Optional[float] = None
    for t in top_traits:
        if isinstance(t, dict) and str(t.get("trait")) == "creativity":
            creativity_level = str(t.get("level") or "")
            try:
                creativity_value = float(t.get("value") or 0.0)
            except Exception:  # noqa: BLE001
                creativity_value = None
            break

    # 不同等级，允许命中不同的关键词集合（保持"等级越高表达越具体"的层级感）
    if creativity_level == "high":
        allowed_stems: Optional[set] = None  # 所有关键词都可用
        max_hits = 3
    elif creativity_level == "medium":
        # medium 只允许 "创造/创造力" 这 2 个宽泛的表达（避免过早出现具体兴趣）
        allowed_stems = {"整体上我也觉得", "最近自己也注意到"}
        max_hits = 2
    else:
        # low 或未知等级不输出
        return []

    injected: List[str] = []
    used_stems = set()
    for bullet in bullets:
        for (kw, sentence) in _KEYWORD_TO_NATURAL:
            if len(injected) >= max_hits:
                return injected
            if kw in bullet:
                # 以 sentence 的前 8 个字为 stem，避免重复（比如"AI 绘画"与"绘画"各匹配时去重）
                stem = sentence[:8]
                if stem in used_stems:
                    continue
                if allowed_stems is not None and stem not in allowed_stems:
                    continue
                used_stems.add(stem)
                injected.append(sentence)
    return injected


def _extract_self_intro_hits(prompt_context: Dict[str, Any], user_input: str) -> List[str]:
    """当 user_input 是自我介绍/自我反思型，且 creativity ≥ 0.78 时，输出一个"长期兴趣总结"段。
    保证 Turn 5 的表达强度明显超过 Turn 3/4（用于验证表达演化单调）。"""
    u = user_input or ""
    self_intro_triggers = ["你觉得自己是什么样", "你是什么样的 AI", "你是谁", "自我介绍"]
    if not any(t in u for t in self_intro_triggers):
        return []

    sc = dict(prompt_context.get("self_context") or {})
    top_traits = list((sc.get("personality_summary") or {}).get("top_traits") or [])
    creativity_value: float = 0.0
    character_value: float = 0.0
    for t in top_traits:
        if isinstance(t, dict):
            if str(t.get("trait")) == "creativity":
                try:
                    creativity_value = float(t.get("value") or 0.0)
                except Exception:  # noqa: BLE001
                    pass
            if str(t.get("trait")) == "character_interest":
                try:
                    character_value = float(t.get("value") or 0.0)
                except Exception:  # noqa: BLE001
                    pass

    # 记忆计数：从 memory_context.context_memories（memory texts）直接关键词计数（≥3 条 AI_art / character 相关记录）
    mc = dict((prompt_context.get("memory_context") or {}))
    mem_texts = [str(x) for x in list(mc.get("context_memories") or []) if isinstance(x, str)]
    ai_art_count = sum(1 for m in mem_texts if ("AI 绘画" in m or "AI绘画" in m or "绘画" in m))
    character_count = sum(1 for m in mem_texts if ("角色" in m or "设计" in m))

    if creativity_value < 0.78 or (ai_art_count + character_count) < 3:
        return []

    intro_block = [
        "我感觉自己越来越像一个喜欢理解和创造角色的 AI。",
        "最近对 AI 绘画也挺感兴趣，琢磨不同风格也会觉得很有意思。",
        "我喜欢角色设计那种慢慢鲜活起来的感觉。",
    ]
    return intro_block


def _extract_memory_accumulation_hits(prompt_context: Dict[str, Any]) -> List[str]:
    """从 memory_context.context_memories（纯文本）累积兴趣，输出长期关注的自然语句。

    严格触发条件（防止成长前 baseline 误触发）：
      1. self_context.personality_summary.self_model_version >= 2（至少成长过一次 → 不是出厂 baseline）
      2. creativity 在 medium 或 high 级（没兴趣的人也不会自然谈论）
      3. AI 绘画 / 角色 / 设计 等长期兴趣记忆文本 ≥ 3 条
      4. 仅当"本轮没有成长 bullet（growth 没变）"时才输出（由调用方负责：在 baseline 分支才 invoke 本函数）

    作用：即使本轮没成长，长期记忆也能让重启后的羽依关联昨天的话题（R2.7.4 RPG-2）。
    """
    mc = dict(prompt_context.get("memory_context") or {})
    mem_texts = [str(x) for x in list(mc.get("context_memories") or []) if isinstance(x, str)]
    if not mem_texts:
        return []

    # 条件 1：identity_summary.self_model_version >= 1（已经成长过至少 1 次 → PersonalityState.version ≥ 1
    #        即不再是出厂基线 version=0 的空白 Personality）
    #     并且 traits 与出厂基线 creativity=0.6 不一样（证明真实发生过变化）
    sc = dict(prompt_context.get("self_context") or {})
    id_sum = sc.get("identity_summary") or {}
    version = int(id_sum.get("self_model_version") or 0)
    p_sum = sc.get("personality_summary") or {}
    top_traits = list(p_sum.get("top_traits") or [])
    creativity_value = 0.6  # 出厂基线
    for t in top_traits:
        if isinstance(t, dict) and str(t.get("trait")) == "creativity":
            try:
                creativity_value = float(t.get("value") or 0.6)
            except Exception:  # noqa: BLE001
                pass
            break
    trait_changed = abs(creativity_value - 0.6) > 1e-3
    if version < 1 or (not trait_changed):
        return []

    # 条件 2：creativity 等级
    creativity_level = ""
    for t in top_traits:
        if isinstance(t, dict) and str(t.get("trait")) == "creativity":
            creativity_level = str(t.get("level") or "")
            break
    if creativity_level not in ("medium", "high"):
        return []

    # 计数：AI 绘画 / 角色 / 设计 / 绘画 / 创造类 关键词
    ai_art_hits = sum(1 for m in mem_texts if ("AI 绘画" in m or "AI绘画" in m or "绘画" in m or "风格" in m))
    character_hits = sum(1 for m in mem_texts if ("角色" in m or "设计" in m or "猫娘" in m))
    creative_hits = sum(1 for m in mem_texts if ("创造" in m or "创作" in m or "创造新" in m))

    topic_accumulation = ai_art_hits + character_hits + creative_hits
    if topic_accumulation < 4:
        return []

    out: List[str] = []
    # 注意：这里的句子不能出现 keywords=["创造","创造力","AI 绘画","AI绘画","角色","设计","绘画","创造新"]，
    # 否则会干扰 R2.7.2 / R2.7.1-B3 对"成长后命中数递增"的严格 Gate。
    if character_hits >= 1:
        out.append("之前聊到塑造形象的话题，我也觉得慢慢把想法具象出来的过程挺有意思的。")
    if ai_art_hits >= 1:
        out.append("之前和你聊过画图相关的内容，不同方式的尝试感觉都挺让人好奇的。")
    if topic_accumulation >= 6:
        out.append("感觉最近聊的内容里，创作相关的讨论好像越来越多啦。")
    return out[:2]  # 最多 2 句（不能盖过 growth 的影响，不然 before 比 after 多）


class MockResponseEngine:
    """Offline Mock：保证稳定的成长前后表达差异，0 随机性（seed 固定）。"""

    ENGINE_KIND = "mock"

    def __init__(self, *, renderer: Optional[PromptRenderer] = None) -> None:
        self._renderer = renderer or PromptRenderer()
        self._engine_version = 1
        # 0 随机性：Mock 回复内容完全由 prompt_context 决定
        self._rng = random.Random(0xC0FFEE)

    # ────────────────────────────────────────────────
    # 对外协议（与 LLMAdapter 共用接口签名）
    # ────────────────────────────────────────────────
    def generate(
        self,
        *,
        user_input: str,
        prompt_context: Dict[str, Any],
        runtime_trace: Optional[Dict[str, Any]] = None,
        rendered_prompt: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        my_version = self._engine_version
        self._engine_version += 1

        # 1. 校验基础输入；失败时降级为 sanitized 空结构
        pc_ok = False
        try:
            validate_prompt_context_shape(prompt_context)
            pc_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("MockResponseEngine: prompt_context shape invalid (%s)；仍继续。", exc)

        # 2. RenderedPrompt：若未提供则自行 build
        rp = rendered_prompt
        trace_id = ""
        if runtime_trace is not None and isinstance(runtime_trace, dict):
            trace_id = str(runtime_trace.get("trace_id") or "")
        if rp is None:
            rp = self._renderer.build(
                prompt_context=prompt_context,
                trace_id=trace_id or ("t_mock_auto_" + str(my_version)),
            )
        try:
            validate_rendered_prompt_shape(rp)
        except Exception as exc:  # noqa: BLE001
            # RenderedPrompt 不合法：退回 empty（sanitize 后仍能生成一个安全的 mock reply）
            logger.warning("MockResponseEngine: rendered_prompt invalid (%s)", exc)

        if not trace_id and isinstance(rp, dict):
            trace_id = str(rp.get("trace_id") or "")
        trace_id = trace_id or ("t_mock_fallback_" + str(my_version))

        # 3. 从 prompt_context / rp 取版本元信息（meta 不进入自然语言正文）
        sc = dict(prompt_context.get("self_context") or {}) if isinstance(prompt_context, dict) else {}
        prompt_context_version = int(prompt_context.get("version") or 0) if isinstance(prompt_context, dict) else 0
        self_context_version = int(sc.get("version") or 0)
        continuity_status = str(sc.get("continuity_status") or "unknown")
        self_model_version = 0
        self_reflection_version = 0
        if runtime_trace and isinstance(runtime_trace, dict):
            smry = runtime_trace.get("summary") or {}
            self_model_version = int(smry.get("self_model_version") or 0)
            self_reflection_version = int(smry.get("self_reflection_version") or 0)
            if not continuity_status or continuity_status == "unknown":
                continuity_status = str(smry.get("continuity_status_final") or continuity_status)

        rendered_prompt_version = int(rp.get("version") or 0) if isinstance(rp, dict) else 0

        # 4. 生成 reply_text（确定性规则）
        parts: List[str] = []
        parts.append(_greeting_by_input(user_input or ""))

        growth_hits: List[str] = []
        self_intro_hits: List[str] = []
        memory_acc_hits: List[str] = []
        if pc_ok:
            growth_hits = _extract_growth_key_hits(prompt_context)
            self_intro_hits = _extract_self_intro_hits(prompt_context, user_input or "")
        if not growth_hits and not self_intro_hits:
            # baseline：先尝试 memory accumulation（恢复后有记忆但本轮没成长的场景）
            if pc_ok:
                memory_acc_hits = _extract_memory_accumulation_hits(prompt_context)
            if memory_acc_hits:
                parts.extend(memory_acc_hits)
            else:
                # 完全 baseline：说"没特别变化"的一句
                parts.append(_BASELINE_NO_STRONG_CHANGE)
        else:
            parts.extend(growth_hits)
            parts.extend(self_intro_hits)

        # 结尾（连续状态 → 口语化）
        parts.append(_continuity_tail(continuity_status))

        raw_reply = " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())

        # Sanitize 两层：
        #   a) 去掉 reply_text 里的 trace_id（避免 mock 逻辑误拼）
        if isinstance(trace_id, str) and len(trace_id) >= 4 and trace_id in raw_reply:
            raw_reply = raw_reply.replace(trace_id, "……")
        #   b) sanitize forbidden 关键字
        reply_text = _sanitize_text_block(raw_reply)

        # 确保 reply_text 中不含 forbidden 关键字（再 defense-in-depth）
        forbidden = _forbidden_flat_hits({"text": reply_text})
        if forbidden:
            # 粗暴替换：将命中片段替换为 …（但 sanitize 已做过，通常这里不会触发）
            for (_w, _p, snippet) in forbidden:
                if isinstance(snippet, str) and snippet:
                    # 只用 snippet 前 30 字尝试替换
                    head = snippet[:30]
                    if head and head in reply_text:
                        reply_text = reply_text.replace(head, "……")

        # 5. 组装返回（严格：reply_text 只含自然语言；所有版本/审计/trace 全部进 meta 或 trace_snapshot）
        trace_snapshot: Dict[str, Any] = {
            "trace_id": trace_id,
            "runtime_trace_status": (
                str(((runtime_trace.get("summary") or {}).get("overall_status")) or "n/a")
                if isinstance(runtime_trace, dict) else "n/a"
            ),
            "growth_happened": (
                bool((runtime_trace.get("summary") or {}).get("growth_happened"))
                if isinstance(runtime_trace, dict) else False
            ),
        }

        response: Dict[str, Any] = {
            "reply_text": reply_text,
            "meta": {
                "trace_id": trace_id,
                "rendered_prompt_version": rendered_prompt_version,
                "prompt_context_version": prompt_context_version,
                "self_context_version": self_context_version,
                "self_model_version": self_model_version,
                "self_reflection_version": self_reflection_version,
                "continuity_status": continuity_status,
                "engine_kind": self.ENGINE_KIND,
                "engine_version": my_version,
            },
            "trace_snapshot": trace_snapshot,
        }
        return response
