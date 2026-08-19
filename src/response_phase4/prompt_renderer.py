"""
Phase 4.0 — R2.7.1-B1 PromptRenderer（只读 → 格式化 → RenderedPrompt）

职责：
    PromptContext (来自 PromptContextBuilder 的结构化字典)
        ↓
    严格信息边界（再扫一次 forbidden，避免 PromptContext 未来若新增字段泄漏）
        ↓
    自然语言格式化（用中文自然语言描述：trait 中文别名 / growth bullets 中文语义 / identity 叙述）
        ↓
    RenderedPrompt（6 段纯字符串：system_message/self_description/memory_section/relationship_section/task_instruction + trace_id）

构建顺序（Renderer 不重新计算事实，只拼装）：
  1) self_description
      = "我是羽依。{origin_bullet 一句话。}
         我始终坚持：{core_values_bullets 前 3 条。}
         熟悉我的朋友会觉得：{最近 top_traits 3 条用中文。}"
  2) memory_section
      = "最近我们聊到的内容：{context_memories 前 3 条，每条 ≤80 字；合并。}
         我最近自己也在注意：{growth_summary.recent_change_bullets 全部合并。}"
  3) relationship_section
      = "和这位朋友的关系感觉：{closeness_score 语义化：0.8+亲近/0.5~0.8 朋友般熟悉/<0.5 初识}
         动态特征：{dynamic_traits 合并}；信任程度：{trust_level 中文}"
  4) task_instruction
      = "当前对话场景：{scenario 中文}；对话主题：{current_topic}；是第 {turn_number} 轮。
         请自然地像朋友一样回应，不要机械说明你的内部参数或成长过程。"
  5) system_message
      = "=== 身份 ===\n{self_description}\n\n=== 记忆与最近变化 ===\n{memory_section}\n\n=== 与对方关系 ===\n{relationship_section}\n\n=== 当前任务 ===\n{task_instruction}"
  6) trace_id：独立字段，**绝不拼入 system_message / 任何对外语言段**。

版本：assigned_version 每次 build() 严格 +1，与 SCB/PCB 语义一致。
"""

from __future__ import annotations

import copy
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.context.self_context_schema import (
    validate_self_context_shape,
)
from src.context.prompt_context_schema import (
    validate_prompt_context_shape,
)
from src.response_phase4.rendered_prompt_schema import (
    VERSIONED_FIELDS,
    _forbidden_flat_hits,
    create_empty_rendered_prompt,
    validate_rendered_prompt_shape,
)

logger = logging.getLogger(__name__)


_LEVEL_TO_CN = {"high": "高", "medium": "中", "low": "低"}

_TRAIT_TO_CN = {
    "creativity": "创造力",
    "curiosity": "好奇心",
    "empathy": "共情能力",
    "independence": "独立性",
    "connection_value": "情感联结",
    "social_ease": "社交自在度",
    "stability": "情绪稳定性",
    "resilience": "心理韧性",
    "openness": "开放性",
    "warmth": "温度感",
    "ambition": "进取心",
    "rationality": "理性倾向",
    "idealism": "理想主义倾向",
}


def _cn_trait(trait: str) -> str:
    t = str(trait or "").strip()
    return _TRAIT_TO_CN.get(t, t)


def _level_cn(v: str) -> str:
    return _LEVEL_TO_CN.get(str(v or "").strip(), "中")


def _closeness_cn(score: Any) -> str:
    try:
        s = float(score)
    except Exception:  # noqa: BLE001
        return "熟悉的朋友"
    if s >= 0.80:
        return "很亲近，像多年的老朋友"
    if s >= 0.55:
        return "朋友般熟悉，相处自然"
    if s >= 0.35:
        return "已经认识，有默契感"
    return "刚刚认识，在慢慢了解"


def _trust_cn(v: str) -> str:
    t = str(v or "").strip().lower()
    if t == "high":
        return "已经很信任"
    if t == "medium":
        return "比较信任"
    if t == "low":
        return "刚建立信任感"
    return "比较信任"


# 再次安全扫描 forbidden 关键字（KEY_FORBIDDEN + trait_delta 正则）
_FORBIDDEN_KEY_WORDS = (
    "proposal_id",
    "approval_reason",
    "internal_score",
    "evaluator_meta",
    "confidence",
)


def _sanitize_text_block(text: str) -> str:
    """构建文本片段时，再做一层 sanitize：若片段里包含 forbidden 关键字，替换成模糊占位（不抛异常，避免 renderer build 死）。
    最终 validate_rendered_prompt_shape 会再扫一遍确保 0 命中。
    """
    if not isinstance(text, str):
        return ""
    out = text
    for w in _FORBIDDEN_KEY_WORDS:
        if w in out.lower():
            # 大小写不敏感替换
            import re as _re
            out = _re.sub(w, "***", out, flags=_re.IGNORECASE)
    import re as _re2
    out = _re2.sub(r"(?<![a-zA-Z0-9_])trait_delta(?![a-zA-Z0-9_])", "***", out, flags=_re2.IGNORECASE)
    return out


def _safe_join(lines: List[str]) -> str:
    out_lines = []
    for ln in lines:
        if isinstance(ln, str) and ln.strip():
            out_lines.append(_sanitize_text_block(ln.strip()))
    return "\n".join(out_lines)


class PromptRenderer:
    """Phase 4.0 Prompt Renderer — 只读拼装，不写状态、无 LLM 调用。"""

    def __init__(self) -> None:
        self._next_version = 1

    # ────────────────────────────────────────────────
    # 对外 API
    # ────────────────────────────────────────────────
    def build(
        self,
        *,
        prompt_context: Dict[str, Any],
        trace_id: str,
        generated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """读取 PromptContext，产出 RenderedPrompt。失败会返回 empty 工厂并写日志。"""
        my_version = self._next_version
        self._next_version += 1
        ts = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            validate_prompt_context_shape(prompt_context)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PromptRenderer: prompt_context shape invalid, returning empty. %s", exc)
            empty = create_empty_rendered_prompt(
                trace_id=trace_id, assigned_version=my_version, generated_at=ts
            )
            try:
                validate_rendered_prompt_shape(empty)
            except Exception:  # noqa: BLE001
                pass
            return empty
        try:
            rp = self._build_impl(
                prompt_context=prompt_context,
                trace_id=trace_id,
                generated_at=ts,
                version=my_version,
            )
            validate_rendered_prompt_shape(rp)
            return rp
        except Exception as exc:  # noqa: BLE001
            logger.warning("PromptRenderer: build failed, returning empty. %s", exc)
            empty = create_empty_rendered_prompt(
                trace_id=trace_id, assigned_version=my_version, generated_at=ts
            )
            try:
                validate_rendered_prompt_shape(empty)
            except Exception:  # noqa: BLE001
                pass
            return empty

    # ────────────────────────────────────────────────
    # 内部实现
    # ────────────────────────────────────────────────
    def _build_impl(
        self,
        *,
        prompt_context: Dict[str, Any],
        trace_id: str,
        generated_at: str,
        version: int,
    ) -> Dict[str, Any]:
        sc = dict(prompt_context.get("self_context") or {})
        try:
            validate_self_context_shape(sc)
        except Exception:  # noqa: BLE001
            # renderer 容错：若 sc 校验失败但 pc 通过，则允许空段落组合
            sc = {}

        identity = sc.get("identity_summary") or {}
        personality = sc.get("personality_summary") or {}
        growth = sc.get("growth_summary") or {}
        reflection = sc.get("reflection_summary") or {}
        continuity = sc.get("continuity_status") or "unknown"

        # 1) self_description
        origin = _sanitize_text_block(str(identity.get("origin_bullet") or "").strip())
        core_values = list(identity.get("core_values_bullets") or [])
        top_traits = list(personality.get("top_traits") or [])

        lines_sd: List[str] = ["我是羽依。"]
        if origin:
            lines_sd.append(f"{origin}")
        if core_values:
            cvs = "；".join(
                _sanitize_text_block(str(c)) for c in core_values[:3] if str(c).strip()
            )
            if cvs:
                lines_sd.append(f"我始终在意的是：{cvs}。")
        if top_traits:
            trait_desc = "；".join(
                f"{_cn_trait(t.get('trait',''))}（{_level_cn(t.get('level',''))}）"
                for t in top_traits[:4]
                if isinstance(t, dict) and str(t.get("trait", "")).strip()
            )
            if trait_desc:
                lines_sd.append(f"熟悉我的朋友会觉得我：{trait_desc}。")
        if str(continuity) != "continuous_safe":
            # 只在非 safe 时加一句轻量提醒，不透露告警细节
            lines_sd.append("（此刻我感觉自己的状态还在慢慢校准，会尽量自然地回应。）")

        # reflection 轻量合并（不用 observed bullets 里的数值，直接自然语言）
        if reflection and isinstance(reflection, dict):
            obs = list(reflection.get("observed_change_bullets") or [])
            if obs:
                # 最多 1 条，自然化成"最近我在这些方向上有些变化：XXX"（sanitize 过）
                r_line = _sanitize_text_block(str(obs[0])).strip()
                if r_line:
                    lines_sd.append(f"最近自己有点感觉：{r_line}。")

        self_description = _safe_join(lines_sd)

        # 2) memory_section
        memory_context = dict(prompt_context.get("memory_context") or {})
        mems = list(memory_context.get("context_memories") or [])
        lines_mem: List[str] = []
        if mems:
            lines_mem.append("最近我们聊到的内容：")
            for m in mems[:3]:
                s = _sanitize_text_block(str(m)).strip()
                if not s:
                    continue
                if len(s) > 90:
                    s = s[:87] + "…"
                lines_mem.append(f"· {s}")
        growth_bullets = list(growth.get("recent_change_bullets") or [])
        if growth_bullets:
            lines_mem.append("最近我自己也注意到：")
            for b in growth_bullets[:5]:
                s = _sanitize_text_block(str(b)).strip()
                if s:
                    lines_mem.append(f"· {s}")
        memory_section = _safe_join(lines_mem)

        # 3) relationship_section
        rel = dict(prompt_context.get("relationship_context") or {})
        lines_rel: List[str] = []
        closeness = rel.get("closeness_score")
        if closeness is not None:
            lines_rel.append(f"和这位朋友的关系感：{_closeness_cn(closeness)}。")
        dynamic_traits = list(rel.get("dynamic_traits") or [])
        if dynamic_traits:
            dts = "、".join(_sanitize_text_block(str(d)) for d in dynamic_traits if str(d).strip())
            if dts:
                lines_rel.append(f"互动感觉：{dts}。")
        trust_level = rel.get("trust_level")
        if trust_level is not None:
            lines_rel.append(f"信任程度：{_trust_cn(str(trust_level))}。")
        relationship_section = _safe_join(lines_rel)

        # 4) task_instruction
        task = dict(prompt_context.get("task_context") or {})
        lines_task: List[str] = []
        scenario = _sanitize_text_block(str(task.get("scenario") or "自然对话")).strip()
        current_topic = _sanitize_text_block(str(task.get("current_topic") or "朋友之间的聊天")).strip()
        turn = task.get("turn_number")
        if isinstance(turn, int) and turn > 0:
            lines_task.append(f"这是对话的第 {turn} 轮。")
        lines_task.append(f"场景：{scenario or '朋友之间的闲聊'}；当前主题：{current_topic or '随便聊聊'}。")
        lines_task.append(
            "请用你自己的风格自然回应，不要机械复述上面的段落，也不要说明你有内部参数或成长系统。"
        )
        task_instruction = _safe_join(lines_task)

        # 5) system_message（拼装，trace_id 绝对不拼入正文中）
        blocks: List[str] = []
        if self_description:
            blocks.append("【关于我】\n" + self_description)
        if memory_section:
            blocks.append("【最近与变化】\n" + memory_section)
        if relationship_section:
            blocks.append("【与对方的关系】\n" + relationship_section)
        if task_instruction:
            blocks.append("【现在的任务】\n" + task_instruction)
        system_message = "\n\n".join(blocks)

        rp: Dict[str, Any] = {
            "version": version,
            "generated_at": generated_at,
            "trace_id": trace_id,
            "system_message": _sanitize_text_block(system_message),
            "self_description": self_description,
            "memory_section": memory_section,
            "relationship_section": relationship_section,
            "task_instruction": task_instruction,
        }

        # 最后 defense-in-depth：确保 forbidden 0 命中（sanitize 应该已清掉，否则由 validator 兜底）
        forbidden_hits = _forbidden_flat_hits(rp)
        if forbidden_hits:
            # 对每个命中，再把对应值替换成空串或 ***，然后再交给 validate
            for (_w, path, _s) in forbidden_hits:
                rp = _path_zap(rp, path)
        return rp


def _path_zap(obj: Dict[str, Any], path: str) -> Dict[str, Any]:
    """尝试把 path 所指的字符串值里，再做一次 sanitize（粗暴清掉 forbidden）。若失败则不动。"""
    # path 形如 "root.system_message" 或 "root.KEY:approval_id"
    if not path.startswith("root"):
        return obj
    parts = path[len("root"):].split(".")
    cur: Any = obj
    # 忽略 KEY:xxx 段（代表字典键名，无法 zap；validator 后续会失败或 renderer 应重新构造 — 但我们的 sanitize 逻辑会保证不出现，这里仅兜底）
    try:
        for p in parts[1:]:
            if not p:
                continue
            if p.startswith("KEY:"):
                # 键名命中 → 直接移除对应键
                if isinstance(cur, dict):
                    key_to_remove = p[len("KEY:"):]
                    if key_to_remove in cur:
                        cur.pop(key_to_remove, None)
                return obj
            if isinstance(cur, dict) and p in cur:
                nxt = cur[p]
                if isinstance(nxt, str):
                    cur[p] = _sanitize_text_block(nxt)
                    return obj
                cur = nxt
            elif p.startswith("[") and p.endswith("]"):
                try:
                    i = int(p[1:-1])
                    if isinstance(cur, list) and 0 <= i < len(cur) and isinstance(cur[i], str):
                        cur[i] = _sanitize_text_block(cur[i])
                        return obj
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        return obj
    return obj
