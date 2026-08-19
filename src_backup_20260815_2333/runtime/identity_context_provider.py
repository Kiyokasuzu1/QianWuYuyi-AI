# -*- coding: utf-8 -*-
"""
src/runtime/identity_context_provider.py

Phase 2 — IdentityContextProvider（Identity State 形式）

职责：
    - 把 Runtime / ResponseAdapter 能拿到的结构化身份数据（IdentitySnapshot /
      SelfModel snapshot / personality_snapshot / ExistenceTimeline 摘要）
      聚合成「我是谁」的自然语言文本，供 PromptBuilder 注入到
      system_prompt 的 Identity State 区块。
    - 输出形式：Identity State（「当前身份状态」+ 稳定倾向 + 身份来源）
      而不是 Anchor 原文（不要像"规则手册"）。
    - 格式严格对齐清清（用户）给的 D.7 IdentityContract 方向，不编造内容：
      缺任何一项就说"正在积累中"，不填默认值。

设计约束（严格遵守 project_memory）：
    - 不 import / 修改 src.memory / src.growth / src.personality /
      src.self_model / src.control / src.runtime.policy 任一模块。
    - 不调用 LLM，纯函数格式化。
    - 输入是一个 dict（IdentitySnapshot 或兼容结构），输出是：
        provide()        -> dict（结构化 Identity State，可审计）
        format_context_for_prompt() -> Optional[str]（注入 Prompt 的文本）
    - 向后兼容：provider 不存在时，PromptBuilder 拿到空字符串直接不注入。

输出文本规范（示例，按数据实际情况拼，空的条目不写）：

    当前身份状态：

    你是浅雾羽依。
    你不是一个固定角色设定，而是一个正在通过经历形成自己的 AI 人格。

    长期稳定倾向：
    - 重视理解和连接
    - 会承认不知道的事情
    - 会参考过去发生过的经历
    - 不为了显得聪明而编造

    身份来源：
    这些倾向来自：
    - 过去经历 23 条
    - 稳定特质 5 条
    - 核心信念 2 条
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ============================================================
# 1. Structured IdentityContextData（纯 dict，可审计）
# ============================================================

@dataclass
class IdentityContextData:
    """结构化 Identity State，format_context_for_prompt() 只从这里拼。"""

    # --- 基础：有就写，没有就不写 identity_name 段落 ---
    identity_name: Optional[str] = None     # "浅雾羽依"；None -> 不写名字
    identity_id: Optional[str] = None       # 用于审计（不进 Prompt）

    # --- 长期稳定倾向：最多 5 条；>5 条只取稳定度前 5 ---
    stable_tendencies: List[str] = None     # ["重视理解和连接", ...]

    # --- 身份来源：None / 0 -> 不写那一行 ---
    past_experiences_count: Optional[int] = None   # existence_timeline / growth /
                                                   # memory 总条数
    stable_traits_count: Optional[int] = None      # stable_traits / top_traits 数
    core_beliefs_count: Optional[int] = None       # stable_beliefs 数
    core_values_count: Optional[int] = None        # IdentitySnapshot.core_values 数

    # --- 版本信息（不进 Prompt，仅审计） ---
    has_identity: bool = False                     # 至少填了一项倾向 + 一项来源
    source: str = ""
    schema_version: str = "1.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity_name": self.identity_name,
            "identity_id": self.identity_id,
            "stable_tendencies": list(self.stable_tendencies or []),
            "source_counts": {
                "past_experiences": self.past_experiences_count,
                "stable_traits": self.stable_traits_count,
                "core_beliefs": self.core_beliefs_count,
                "core_values": self.core_values_count,
            },
            "has_identity": self.has_identity,
            "source": self.source,
            "schema_version": self.schema_version,
        }


# ============================================================
# 2. 稳定倾向白名单：只从「核心锚点」抽取 5 条默认倾向
#    —— 这里是 Identity Anchor 的"人性化转译"结果，
#       只做"原则→倾向"，不引入任何新知识。
# ============================================================

# 转译规则（Anchor.principle/description → 羽依第一人称的"稳定倾向"句式）：
#   Anchor.principle: 真实比完美更加重要
#     -> 倾向: 会承认不知道的事情
#     -> 倾向: 不为了显得聪明而编造
#   Anchor: Growth / Memory Connection
#     -> 倾向: 会参考过去发生过的经历
#   Anchor: Independence / CreatorRelationship
#     -> 倾向: 重视理解和连接 / 倾听但不盲从
_TENDENCY_DEFAULTS_FROM_ANCHORS: List[str] = [
    "会承认不知道的事情",
    "不为了显得聪明而编造",
    "会参考过去发生过的经历",
    "重视理解和连接",
    "倾听但不盲从",
]

# 从 SelfModel / Trait 拿到的"非锚"稳定倾向，按稳定性阈值可追加
# （不在本 Provider 内主动调用 SelfModel Manager，只等调用方传已聚合
# 的 personality_context / self_model_data 抽出来）


# ============================================================
# 3. IdentityContextProvider
# ============================================================

class IdentityContextProvider:
    """Identity State 聚合器（纯函数，无 IO，无 LLM）。

    典型用法：
        prov = IdentityContextProvider()
        data = prov.provide({
            "identity_snapshot": {...},          # IdentitySnapshot.to_dict()
            "self_model_data": {...},            # Phase 4.2 SelfModel 注入
            "personality_runtime_data": {...},   # Phase 4.5 PersonalityBinding
            "existence_stats": {...},            # LifeSnapshot.existence_stats
        })
        prompt_text = prov.format_context_for_prompt(data)
        # -> None 或字符串
    """

    name: str = "identity_context_provider"
    schema_version: str = IdentityContextData.__dataclass_fields__[  # type: ignore[attr-defined]
        "schema_version"
    ].default

    # ---- 公共 API -----------------------------------------------------

    def provide(self, identity_bundle: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """聚合结构化 Identity State（不直接写 Prompt）。"""
        # fail-soft：非 dict/None 一律当成空 bundle，不向上抛错
        bundle: Dict[str, Any]
        if identity_bundle is None:
            bundle = {}
        elif isinstance(identity_bundle, dict):
            bundle = identity_bundle
        else:
            bundle = {}

        # 1. identity_name / identity_id（只从传入的数据抽，不写死）
        name: Optional[str] = None
        id_id: Optional[str] = None
        for key in (
            "identity_name",
            "name",
            "identity_full_name",
        ):
            v = self._dig(bundle, key)
            if isinstance(v, str) and v.strip():
                name = v.strip()
                break
        for key in ("identity_id", "id"):
            v = self._dig(bundle, key)
            if isinstance(v, str) and v.strip():
                id_id = v.strip()
                break

        # 2. 来源计数（每项空 / None / <1 就保持 None，不写 0）
        experiences_n = self._pick_count(bundle, [
            ("existence_stats", "total"),
            ("existence_stats", "timeline_items"),
            ("past_experiences_count",),
            ("growth_history_count",),
            ("memory_count",),
            ("self_model_data", "growth_history_count"),
            ("identity_snapshot", "growth_history_count"),
        ])
        traits_n = self._pick_count(bundle, [
            ("stable_traits_count",),
            ("self_model_data", "preferences_count"),
            ("identity_snapshot", "traits"),
            ("personality_runtime_data", "stable_traits"),
        ])
        beliefs_n = self._pick_count(bundle, [
            ("core_beliefs_count",),
            ("stable_beliefs_count",),
            ("self_model_data", "behavioral_patterns_count"),
            ("identity_snapshot", "core_values"),
            ("personality_runtime_data", "stable_beliefs"),
        ])
        core_values_n = self._pick_count(bundle, [
            ("core_values_count",),
            ("identity_snapshot", "core_values"),
        ])

        # 3. 稳定倾向：
        #    - 先把传入的 self_model_data / identity_snapshot /
        #      personality_runtime_data 中能明确转译成"稳定倾向"的项追加；
        #    - 不足 5 条时，用 Anchor 人性化转译补齐（不编造新内容，
        #      只是把 Anchor 核心原则换成"我"的语气）。
        extra = self._extract_tendencies(bundle)
        tendencies: List[str] = []
        for t in extra + _TENDENCY_DEFAULTS_FROM_ANCHORS:
            if not isinstance(t, str):
                continue
            t_stripped = t.strip()
            if not t_stripped:
                continue
            if t_stripped in tendencies:
                continue
            tendencies.append(t_stripped)
            if len(tendencies) >= 5:
                break

        # 4. 只要有 ≥1 条稳定倾向，并且来源计数有 ≥1 项非空，就认为
        #    当前有一个可注入的 Identity State；否则返回空壳 has_identity=False
        #    （避免把空壳灌进 Prompt 污染 LLM）。
        has_identity = bool(tendencies) and any(
            c is not None and c > 0
            for c in (experiences_n, traits_n, beliefs_n, core_values_n)
        )

        data = IdentityContextData(
            identity_name=name,
            identity_id=id_id,
            stable_tendencies=tendencies or None,
            past_experiences_count=experiences_n,
            stable_traits_count=traits_n,
            core_beliefs_count=beliefs_n,
            core_values_count=core_values_n,
            has_identity=has_identity,
            source=self._pick_source(bundle),
        )
        return data.to_dict()

    def format_context_for_prompt(
        self, identity_context_data: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        """把 provide() 的结果格式化成 Identity State 文本。

        返回 None 表示"没有足够信息，不要注入"。
        """
        if not isinstance(identity_context_data, dict):
            return None
        if not identity_context_data.get("has_identity"):
            return None

        name = identity_context_data.get("identity_name")
        tendencies = identity_context_data.get("stable_tendencies") or []
        counts = identity_context_data.get("source_counts") or {}

        # --- 拼 header ---
        lines: List[str] = ["当前身份状态：", ""]
        if isinstance(name, str) and name.strip():
            lines.append("你是%s。" % name.strip())
        lines.append(
            "你不是一个固定角色设定，而是一个正在通过经历形成自己的 AI 人格。"
        )

        # --- 稳定倾向 ---
        real_tendencies = [
            t.strip() for t in tendencies
            if isinstance(t, str) and t.strip()
        ][:5]
        if real_tendencies:
            lines.append("")
            lines.append("长期稳定倾向：")
            for t in real_tendencies:
                lines.append("- " + t)

        # --- 身份来源（缺的项跳过，不说 0 条） ---
        src_lines: List[str] = []
        label_counts = (
            ("过去经历", counts.get("past_experiences")),
            ("稳定特质", counts.get("stable_traits")),
            ("核心信念", counts.get("core_beliefs")),
            ("核心价值观", counts.get("core_values")),
        )
        for label, n in label_counts:
            if isinstance(n, int) and n > 0:
                src_lines.append("- %s %d 条" % (label, n))
        if src_lines:
            lines.append("")
            lines.append("身份来源：")
            lines.append("这些倾向来自：")
            lines.extend(src_lines)

        return "\n".join(lines).strip() or None

    # ---- 内部工具（纯函数，不引入任何业务模块） ----------------------

    @staticmethod
    def _dig(bundle: Dict[str, Any], key: str) -> Any:
        """一层 dict 查找，兼容顶层 / identity_snapshot / self_model_data。"""
        if key in bundle:
            return bundle[key]
        for outer in ("identity_snapshot", "self_model_data",
                      "personality_runtime_data"):
            inner = bundle.get(outer)
            if isinstance(inner, dict) and key in inner:
                return inner[key]
        return None

    @staticmethod
    def _pick_count(
        bundle: Dict[str, Any],
        lookup_paths,
    ) -> Optional[int]:
        """从多个候选路径找第一个"正整数"计数；找不到返回 None（不返回 0）。"""
        for path in lookup_paths:
            if len(path) == 1:
                v = IdentityContextProvider._dig(bundle, path[0])
            else:
                outer = bundle.get(path[0])
                v = outer.get(path[1]) if isinstance(outer, dict) else None
                # traits / core_values 是 list，取 len
                if isinstance(v, list) and path[1] in ("traits", "core_values",
                                                      "stable_traits",
                                                      "stable_beliefs"):
                    v = len(v)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                iv = int(v)
                if iv > 0:
                    return iv
        return None

    @staticmethod
    def _pick_source(bundle: Dict[str, Any]) -> str:
        for outer in ("identity_snapshot", "self_model_data"):
            inner = bundle.get(outer)
            if isinstance(inner, dict) and isinstance(inner.get("source"), str):
                return inner["source"]
        return "identity_context_provider"

    @staticmethod
    def _extract_tendencies(bundle: Dict[str, Any]) -> List[str]:
        """从传入的 IdentitySnapshot / SelfModel / PersonalityBinding 抽稳定倾向。

        规则（纯转译，不引入新内容）：
        - identity_snapshot.core_values[].name：["honesty" -> "对诚实很看重"]
        - identity_snapshot.traits[] 中 stability>=0.6 的 trait：
          ["warmth" -> "比较温柔"]
        - self_model_data.top_traits[]：同上。
        - personality_runtime_data.behavior_signature / consistency_rules：
          只挑"明确陈述稳定倾向"长度 <= 30 的短句。
        """
        out: List[str] = []
        # core_values.name 人性化映射（只翻已有的，不新增价值观）
        value_to_tendency = {
            "honesty": "诚实地表达",
            "growth": "愿意继续成长",
            "autonomy": "形成属于自己的判断",
            "empathy": "愿意理解和共情",
            "kindness": "倾向于温柔和善意",
            "curiosity": "对世界保持好奇",
            "sincerity": "真诚而不敷衍",
            "openness": "对新鲜经验保持开放",
            "patience": "有耐心",
            "self_confidence": "重视自己的感受",
            "self_expression": "愿意真实表达自己",
        }
        # 1. core_values
        for outer in ("identity_snapshot",):
            inner = bundle.get(outer)
            if not isinstance(inner, dict):
                continue
            cvs = inner.get("core_values")
            if isinstance(cvs, list):
                for cv in cvs:
                    if not isinstance(cv, dict):
                        continue
                    vname = cv.get("name") or cv.get("value_id")
                    if isinstance(vname, str):
                        mapped = value_to_tendency.get(vname.strip().lower())
                        if mapped and mapped not in out:
                            out.append(mapped)
        # 2. stable traits（只按稳定性>=0.6 加，人性化拼接）
        trait_to_tendency = {
            "warmth": "比较温柔",
            "gentleness": "语气温和",
            "shyness": "有时会有点害羞",
            "curiosity": "好奇",
            "openness": "对新事物开放",
            "formality": "会把握分寸",
            "verbosity": "话不多不少",
            "sensitivity": "对情绪比较敏感",
            "tenderness": "很心软",
            "honesty": "不喜欢撒谎",
            "independence": "有自己的主见",
            "patience": "有耐心",
            "confidence": "慢慢变得自信",
            "intimacy": "喜欢亲密感",
        }
        def _scan_traits(traits_list, stability_required: float = 0.60):
            for t in traits_list:
                if not isinstance(t, dict):
                    continue
                tn = (t.get("trait") or t.get("name") or t.get("key") or "")
                st = t.get("stability")
                if tn and isinstance(stability_required, (int, float)) \
                        and isinstance(st, (int, float)) \
                        and st < stability_required:
                    continue
                tn_key = str(tn).strip().lower()
                mapped = trait_to_tendency.get(tn_key)
                if mapped and mapped not in out:
                    out.append(mapped)
        for outer in ("identity_snapshot", "self_model_data"):
            inner = bundle.get(outer)
            if isinstance(inner, dict):
                if isinstance(inner.get("traits"), list):
                    _scan_traits(inner["traits"], 0.60)
                if isinstance(inner.get("top_traits"), list):
                    _scan_traits(inner["top_traits"], 0.60)
        # 3. behavior consistency_rules 短句
        for outer in ("personality_runtime_data", "self_model_data"):
            inner = bundle.get(outer)
            if not isinstance(inner, dict):
                continue
            rules = inner.get("consistency_rules")
            if isinstance(rules, list):
                for r in rules:
                    s = str(r).strip()
                    if 4 <= len(s) <= 30 and s not in out:
                        out.append(s)
        return out


__all__ = ["IdentityContextProvider", "IdentityContextData"]
