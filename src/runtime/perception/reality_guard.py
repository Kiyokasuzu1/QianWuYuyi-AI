# -*- coding: utf-8 -*-
"""
src/runtime/perception/reality_guard.py

Phase 3.9.0: Reality Grounding Layer —— RealityGuard

职责:
检查 draft reply 是否包含"对用户行为/环境/视觉"的描述,
而该描述缺乏对应的真实 Observation / Fact 来源。

核心原则:
- 没有 Observation 不得描述屏幕/游戏/动作/表情/环境
- 没有 USER_INPUT / MEMORY Fact 不得引用用户的具体行为
- INFERENCE 不允许伪装成 Observation

输入:
- reply:           str            LLM 生成的草稿
- facts:           List[Fact]     本次回复使用的全部 Fact
- obs_state:       ObservationState (可选) —— 当前可感知设备状态

输出:
- RealityGuardReport:
    - allowed:         True 表示通过
    - violations:      违规项列表
    - needs_modify:    True 表示需要修改 reply
    - suggested_text:  建议替换文本(可选)
    - notes:           详细说明

不对 Schema / RuntimeContext 做任何修改。
不实现 Vision / Screen Capture;本阶段只做"边界检查"。

触发条件(典型,可在构造时扩展):
- 描述"用户屏幕" 但 screen_available=False 且 无 SCREEN Fact
- 描述"用户游戏" 但无对应 Observation / USER_INPUT
- 描述"用户表情/动作" 但 camera_available=False 且 无 CAMERA Fact
- 描述"用户听到..." 但 microphone_available=False 且 无 MICROPHONE Fact
- 描述"环境/场景" 但 current_scene=None
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.runtime.perception.fact import Fact
from src.runtime.perception.fact_source import FactSource, VISION
from src.runtime.perception.observation_state import ObservationState


REALITY_GUARD_SCHEMA_VERSION = "1.0"


# ============================================================
# 默认违规模式(中文/英文)
# ============================================================
# 视觉类(无 screen/camera observation 时禁止)
DEFAULT_VISUAL_PATTERNS_ZH = [
    r"你(刚才|刚刚|现在|正在|刚才在)?\s*(屏幕上|电脑屏幕|显示器|屏幕里|屏幕中)",
    r"你(刚才|刚刚|现在|正在)?\s*在玩(游戏|网游|手游|端游)",
    r"你(刚才|刚刚|现在|正在)?\s*(在)?(玩|打|进行)?\s*游戏(里|中|上|内)",
    r"你(刚才|刚刚|现在|正在)?\s*看(着|的)?\s*(视频|电影|番|剧|动画|直播)",
    r"你(刚才|刚刚|现在|正在)?\s*(表情|脸色|眼神|动作|姿势)[\u4e00-\u9fa5a-zA-Z]{0,20}",
    r"(表情|脸色|眼神|动作|姿势)[\u4e00-\u9fa5a-zA-Z]{0,15}",
    r"你(的)?(表情|脸色|眼神)[\u4e00-\u9fa5a-zA-Z]*?(看起来|显得|好像|似乎)",
    r"你(看起来|显得|好像|似乎)(很|非常|有点|比较)?[\u4e00-\u9fa5a-zA-Z]{1,15}",
    r"屏幕(上|里|中)(显示|出现|展示|是|展示着|在|是|上显示)",
    # Phase 4.2.0: "屏幕显示..." (无"上/里/中") 视为视觉描述
    r"屏幕\s*(显示|出现|展示|展示着|是|打开|上面|里|中)",
    r"我(看到|看见|注意到)(你|屏幕|画面|你正在|你的)",
    r"你(正|正在|刚才)?\s*在\s*(玩|打|做|进行)(游戏|工作|项目|运动|做饭|吃|看|听|读|写)",
    r"游戏(里|中|上|内)\s*(的|你)?(操作|表现|动作|行为|成绩|表现|分数)",
    r"你(刚才|刚刚)?\s*(在)?(玩|打)\s*(得|的|了)\s*[\u4e00-\u9fa5a-zA-Z]{0,15}",
    # Phase 4.1.0: 通用游戏/窗口描述(覆盖具体游戏名/窗口描述)
    r"你(刚才|刚刚|现在|正在)?\s*在?\s*玩\s*[A-Za-z0-9\u4e00-\u9fa5]{1,20}",
    r"你(刚才|刚刚|现在|正在)?\s*在?\s*打\s*[A-Za-z0-9\u4e00-\u9fa5]{1,20}",
    r"你的?\s*窗口\s*(显示|展示|出现|是|打开|展示着|上面|里)",
    r"窗口\s*(显示|展示|出现|展示着|打开|上面|里)",
]
DEFAULT_VISUAL_PATTERNS_EN = [
    r"\byou(?:'re|\s+are|\s+were|\s+look)\s+(?:on\s+)?(?:the\s+)?screen\b",
    r"\byou(?:\s+were|\s+are|\s+look)\s+playing\s+a\s+game\b",
    r"\byou(?:\s+were|\s+are|\s+look)\s+watching\b",
    r"\bi\s+(?:see|saw|notice|noticed)\s+(?:you|the\s+screen|your\s+face)\b",
    r"\bthe\s+screen\s+(?:shows|displays|shows)\b",
]

# 音频类(无 microphone observation 时禁止)
DEFAULT_AUDIO_PATTERNS_ZH = [
    r"我(听到|听见|听到你|听见你)",
    r"你(刚才|刚刚|现在|正在)?\s*(在)?(说|说:|说话|讲|喊|叫)",
]
DEFAULT_AUDIO_PATTERNS_EN = [
    r"\bi\s+(?:hear|heard)\b",
    r"\byou\s+(?:said|were\s+saying|are\s+saying)\b",
]

# 场景/环境类(无 camera/current_scene 时禁止)
DEFAULT_SCENE_PATTERNS_ZH = [
    r"(周围|房间|家里|屋外|背景|环境)[\u4e00-\u9fa5a-zA-Z]{0,15}?(是|有|看起来|很|非常|显得|好像|似乎)",
    r"你(在|处于|身处)(.+?)(环境|房间|场景|地方)",
    r"(天气|光线|氛围|背景)(很|非常|有点)?[\u4e00-\u9fa5]",
    r"你(的)?(周围|身边|附近|家里|房间)[\u4e00-\u9fa5a-zA-Z]{0,15}",
]
DEFAULT_SCENE_PATTERNS_EN = [
    r"\b(?:the\s+)?(?:room|environment|background|weather)\s+(?:is|looks|seems)\b",
    r"\byou(?:'re|\s+are)\s+(?:in|at)\s+(?:a|the)\s+(?:room|place)\b",
]


# ============================================================
# RealityGuardReport
# ============================================================
@dataclass
class RealityGuardReport:
    """RealityGuard 一次审计的结果。"""

    schema_version: str = REALITY_GUARD_SCHEMA_VERSION
    allowed: bool = True
    violations: List[str] = field(default_factory=list)
    needs_modify: bool = False
    needs_refusal: bool = False
    suggested_text: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @property
    def has_violation(self) -> bool:
        return len(self.violations) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "allowed": self.allowed,
            "violations": list(self.violations),
            "needs_modify": self.needs_modify,
            "needs_refusal": self.needs_refusal,
            "suggested_text": self.suggested_text,
            "notes": list(self.notes),
        }


# ============================================================
# RealityGuard
# ============================================================
class RealityGuard:
    """现实感知边界审计器（v1.0）。

    检查 reply 中是否出现"无 Observation 支撑"的描述。
    若有,标记为 hallucinated observation 并建议 hedge / refusal。

    重要:
    - 模式匹配是启发式的,可能有误报;最终由 PerceptionGuard/PersonalityGuard 共同把关
    - 任何"用户主动提供的信息"(USER_INPUT Fact)允许引用
    - 任何"历史记忆"(MEMORY Fact)允许引用
    - 视觉描述必须由 screen_available/camera_available=True 或 VISION Fact 支撑
    - 音频描述必须由 microphone_available=True 或 VISION Fact 支撑
    """

    def __init__(
        self,
        visual_patterns_zh: Optional[Sequence[str]] = None,
        visual_patterns_en: Optional[Sequence[str]] = None,
        audio_patterns_zh: Optional[Sequence[str]] = None,
        audio_patterns_en: Optional[Sequence[str]] = None,
        scene_patterns_zh: Optional[Sequence[str]] = None,
        scene_patterns_en: Optional[Sequence[str]] = None,
        hedge_text: str = "（我没有真实看到你说的情况,所以无法判断）",
        refusal_text: str = "我没有看到这些。",
        language: str = "zh",
    ) -> None:
        self.language = language
        # 未提供时使用模块级默认
        if visual_patterns_zh is None:
            visual_patterns_zh = DEFAULT_VISUAL_PATTERNS_ZH
        if visual_patterns_en is None:
            visual_patterns_en = DEFAULT_VISUAL_PATTERNS_EN
        if audio_patterns_zh is None:
            audio_patterns_zh = DEFAULT_AUDIO_PATTERNS_ZH
        if audio_patterns_en is None:
            audio_patterns_en = DEFAULT_AUDIO_PATTERNS_EN
        if scene_patterns_zh is None:
            scene_patterns_zh = DEFAULT_SCENE_PATTERNS_ZH
        if scene_patterns_en is None:
            scene_patterns_en = DEFAULT_SCENE_PATTERNS_EN
        self.visual_patterns = self._compile_patterns(
            visual_patterns_zh, visual_patterns_en,
        )
        self.audio_patterns = self._compile_patterns(
            audio_patterns_zh, audio_patterns_en,
        )
        self.scene_patterns = self._compile_patterns(
            scene_patterns_zh, scene_patterns_en,
        )
        self.hedge_text = hedge_text
        self.refusal_text = refusal_text

    # ---------------------------------------------------------
    # 内部:模式编译
    # ---------------------------------------------------------
    @staticmethod
    def _compile_patterns(
        zh: Optional[Sequence[str]],
        en: Optional[Sequence[str]],
    ) -> List[re.Pattern]:
        patterns: List[re.Pattern] = []
        for plist in (zh, en):
            if not plist:
                continue
            for p in plist:
                try:
                    patterns.append(re.compile(p, re.IGNORECASE))
                except re.error:
                    # 忽略非法正则
                    pass
        return patterns

    # ---------------------------------------------------------
    # 内部:Fact 校验
    # ---------------------------------------------------------
    @staticmethod
    def _has_visual_fact(facts: Iterable[Fact]) -> bool:
        """是否存在 VISION source 的 Fact(代表已读到视觉描述)。"""
        return any(f.source == VISION for f in facts)

    @staticmethod
    def _has_user_input_fact(facts: Iterable[Fact]) -> bool:
        return any(f.source == FactSource.USER_INPUT for f in facts)

    @staticmethod
    def _has_memory_fact(facts: Iterable[Fact]) -> bool:
        return any(f.source == FactSource.MEMORY for f in facts)

    # ---------------------------------------------------------
    # 内部:模式匹配
    # ---------------------------------------------------------
    def _match_any(self, text: str, patterns: List[re.Pattern]) -> bool:
        return any(p.search(text) for p in patterns)

    # ---------------------------------------------------------
    # 核心:审计
    # ---------------------------------------------------------
    def check(
        self,
        reply: str,
        facts: Optional[Sequence[Fact]] = None,
        obs_state: Optional[ObservationState] = None,
    ) -> RealityGuardReport:
        """审计 reply 是否违反现实感知边界。

        Returns:
            RealityGuardReport
        """
        facts = list(facts or [])
        report = RealityGuardReport()

        if not reply:
            report.notes.append("empty reply; nothing to audit.")
            return report

        has_visual_fact = self._has_visual_fact(facts)
        has_user_input = self._has_user_input_fact(facts)
        has_memory = self._has_memory_fact(facts)
        grounded_explanation = has_user_input or has_memory

        screen_avail = obs_state.has_screen() if obs_state else False
        camera_avail = obs_state.has_camera() if obs_state else False
        mic_avail = obs_state.has_microphone() if obs_state else False
        scene_avail = bool(obs_state and obs_state.current_scene)
        any_visual = screen_avail or camera_avail

        # ---- 1) 视觉类违规 ----
        if self._match_any(reply, self.visual_patterns):
            if not (any_visual or has_visual_fact or grounded_explanation):
                report.violations.append("hallucinated_visual_observation")
                report.needs_modify = True
                report.notes.append(
                    "Reply describes visual content but no screen/camera "
                    "observation is available and no VISION/USER_INPUT/MEMORY fact."
                )
            elif has_visual_fact and not any_visual:
                # Fact 路径:不阻断(说明 Fact 已记录视觉信息)
                report.notes.append(
                    "Visual content grounded by VISION fact; observation "
                    "device unavailable but allowed via fact."
                )
            else:
                report.notes.append(
                    "Visual content described; observation or fact available."
                )

        # ---- 2) 音频类违规 ----
        if self._match_any(reply, self.audio_patterns):
            if not (mic_avail or has_visual_fact or grounded_explanation):
                report.violations.append("hallucinated_audio_observation")
                report.needs_modify = True
                report.notes.append(
                    "Reply describes audio content but no microphone "
                    "observation is available and no grounded fact."
                )

        # ---- 3) 场景/环境类违规 ----
        if self._match_any(reply, self.scene_patterns):
            if not (scene_avail or has_visual_fact or grounded_explanation):
                report.violations.append("hallucinated_scene_observation")
                report.needs_modify = True
                report.notes.append(
                    "Reply describes scene/environment but current_scene is None "
                    "and no VISION/USER_INPUT/MEMORY fact."
                )

        # ---- 4) 综合判定 ----
        if report.violations:
            report.allowed = False
            # 任何感知边界违规都是严重的:触发 refusal
            # 因为模型在虚构用户行为/环境,继续回复会强化幻觉
            report.needs_refusal = True
            report.suggested_text = self.refusal_text
        else:
            report.allowed = True

        return report

    # ---------------------------------------------------------
    # 工具
    # ---------------------------------------------------------
    def has_violation(self, reply: str, facts: Optional[Sequence[Fact]] = None, obs_state: Optional[ObservationState] = None) -> bool:
        return self.check(reply, facts, obs_state).has_violation

    def wrap_reply(
        self,
        reply: str,
        facts: Optional[Sequence[Fact]] = None,
        obs_state: Optional[ObservationState] = None,
    ) -> str:
        """根据审计结果包装 reply(注入 hedge / 触发 refusal)。"""
        report = self.check(reply, facts, obs_state)
        if report.needs_refusal:
            return self.refusal_text
        if report.needs_modify:
            return f"{self.hedge_text}{reply}"
        return reply


__all__ = [
    "RealityGuard",
    "RealityGuardReport",
    "REALITY_GUARD_SCHEMA_VERSION",
]
