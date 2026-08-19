# -*- coding: utf-8 -*-
"""
src/runtime/identity_context_builder.py

Phase 4.0.3: IdentityContextBuilder —— Runtime 层的「身份上下文组装器」。

架构定位（不可违背）：
    它不是 Identity 数据源，也不修改任何 Identity / Personality 状态。
    它是 Runtime 生命周期中的 orchestration 层：
        MemoryRetriever
        EmotionContextBuilder
        IdentityContextBuilder  ←  本文件
    都是 RuntimeCore 内部的「读引用 → 拼自然语言文本」。

设计原则（用户冻结的边界）：
    ❌ 不得调用 Identity 系统中任何写入方法：
       - apply_change_proposal() / update_personality() / update_state()
       - save() / store() / persist()
       - modify() / set() / delete()
    ❌ 不得调用 Personality 系统中任何写入方法。
    ✅ 只能读取 Identity 子系统输出的结构化报告/快照：
       - IdentityAnchor（display_name / principle / is_core / weight）
       - ContinuityReport（continuity_score / is_continuous / 分项分）
       - IdentityStabilityReport（is_stable / stability_score / 摘要）
       - IdentitySnapshot（version / overall_understanding / behavioral_patterns_count 等）

两段式输出（用户设计补充）：
    identity_context_text =
        stable_identity          ←  变化慢：名字、锚点核心原则、创造者关系
        +
        current_identity_state   ←  变化快：连续性评分、稳定性、近期理解

任何读取失败都 fail-soft：
    单个段失败 → 段落写空串，不抛异常，不中断 Runtime。
    全部失败 → identity_context_text = ""（Runtime 用 personality_context 继续生成）。
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional


logger = logging.getLogger(__name__)

# 写操作关键词黑名单（仅用于诊断日志，不阻断运行；防止未来扩展时越界）
_WRITE_KEYWORDS: tuple = (
    "apply_change_proposal",
    "update_personality",
    "update_state",
    "modify_",
    "save_state",
    "persist",
    "set_",
    "delete_",
    "write_",
)


class IdentityContextBuilder:
    """Runtime 层的身份上下文构建器（纯只读，不写状态）。

    构造参数全部为「只读引用」：
        continuity_checker: IdentityContinuityChecker（可选）
            只读接口: generate_change_report() / get_latest_report()
        anchor_manager: IdentityAnchorManager（可选）
            只读接口: get_core_anchors() / get_all_anchors() / get_latest_snapshot() /
                       validate_anchor_integrity()
        stability_engine: IdentityStabilityEngine（可选）
            只读接口: history.get_latest_report() / history.get_snapshot() / generate_report()
        last_identity_snapshot: IdentitySnapshot（可选）
            只读字段: identity_id / version / overall_understanding / traits / core_values 等
        identity_port: Any（可选，RuntimeCore.configure_ports 注入）
            外部自定义 Identity 适配器，必须提供 read-only 方法集合，
            缺失时视为 None 降级。

    所有参数为 None 都可安全降级：build() 返回空串。
    """

    # ------------------------------------------------------------
    # __init__: 仅保存只读引用
    # ------------------------------------------------------------
    def __init__(
        self,
        continuity_checker: Optional[Any] = None,
        anchor_manager: Optional[Any] = None,
        stability_engine: Optional[Any] = None,
        last_identity_snapshot: Optional[Any] = None,
        identity_port: Optional[Any] = None,
    ) -> None:
        self._continuity = continuity_checker
        self._anchors = anchor_manager
        self._stability = stability_engine
        self._last_snap = last_identity_snapshot
        self._identity_port = identity_port

        # 审计计数（Gate5 用）：允许查询 write 方法被调用次数；
        # 这里显式保存"期望写入次数=0"的初始值，方便 test spy。
        self._write_method_calls_count: int = 0

    # ============================================================
    # 主入口：build() → (identity_context_text: str)
    # 本方法永不抛异常；所有段内错误 fail-soft
    # ============================================================
    def build(self) -> str:
        """构建 Identity Context 自然语言（两段式）。

        Returns:
            非空 str 或 ""（所有数据源都不可用）。
        """
        parts: List[str] = []
        try:
            stable = self._build_stable_identity_section()
            if stable:
                parts.append("== 固定身份锚点（稳定存在）==")
                parts.append(stable)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[IdentityContextBuilder] stable_identity 构建失败（降级跳过）: %s", exc,
            )

        try:
            current = self._build_current_identity_state_section()
            if current:
                parts.append("== 当前身份状态（会随成长变化）==")
                parts.append(current)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[IdentityContextBuilder] current_identity_state 构建失败（降级跳过）: %s", exc,
            )

        result = "\n".join(parts).strip()
        return result

    # ============================================================
    # Section A: Stable Identity（变化慢）
    # 内容：核心锚点 display_name + principle + continuity_display_name(版本/identity_id)
    # ============================================================
    def _build_stable_identity_section(self) -> str:
        lines: List[str] = []

        # A1: 核心锚点（来源于 identity.md 的 5 个核心原则，变化最慢）
        if self._anchors is not None:
            try:
                get_core = getattr(self._anchors, "get_core_anchors", None)
                core_list = []
                if callable(get_core):
                    core_list = get_core() or []

                # A1b: 非核心锚点（用户偏好/习惯/"喜欢雨天"等,变化比核心锚点快）
                #      从 get_all_anchors() 减去 core_list 取差集（按 display_name 去重）
                extra_list = []
                try:
                    get_all = getattr(self._anchors, "get_all_anchors", None)
                    if callable(get_all):
                        all_anchors = get_all() or []
                        core_names = {
                            getattr(a, "display_name", "") for a in core_list
                        }
                        for a in all_anchors:
                            name = getattr(a, "display_name", "")
                            if name and name not in core_names:
                                extra_list.append(a)
                except Exception:
                    extra_list = []

                def _fmt_anchor_list(anchor_list):
                    out_lines = []
                    for anchor in anchor_list:
                        display = self._safe_str(
                            getattr(anchor, "display_name", "")
                            or getattr(anchor, "name", "")
                        )
                        principle = self._safe_str(getattr(anchor, "principle", ""))
                        weight_raw = getattr(anchor, "weight", None)
                        try:
                            weight_str = (
                                f"（权重 {float(weight_raw):.2f}）"
                                if weight_raw is not None else ""
                            )
                        except Exception:
                            weight_str = ""
                        if display:
                            title = f"- {display}{weight_str}"
                            if principle:
                                title += f": {principle}"
                            out_lines.append(title)
                    return out_lines

                core_fmt = _fmt_anchor_list(core_list)
                if core_fmt:
                    lines.extend(core_fmt)
                extra_fmt = _fmt_anchor_list(extra_list)
                if extra_fmt:
                    lines.append("【附属身份锚点:偏好与个人习惯】")
                    lines.extend(extra_fmt)
            except Exception as exc:
                logger.warning("[IdentityContextBuilder] core anchors 读取失败: %s", exc)

        # A2: identity_id / version（来源于 last IdentitySnapshot）
        if self._last_snap is not None:
            try:
                identity_id = self._safe_str(getattr(self._last_snap, "identity_id", ""))
                version = getattr(self._last_snap, "version", None)
                meta_parts: List[str] = []
                if identity_id:
                    meta_parts.append(f"身份标识: {identity_id}")
                if version is not None:
                    try:
                        meta_parts.append(f"快照版本 v{int(version)}")
                    except Exception:
                        pass
                if meta_parts:
                    lines.append("【身份元数据】" + " / ".join(meta_parts))
            except Exception as exc:
                logger.warning("[IdentityContextBuilder] identity meta 读取失败: %s", exc)

        return "\n".join(lines).strip()

    # ============================================================
    # Section B: Current Identity State（变化快，Growth 接入点）
    # 内容：连续性分数 + 锚点完整性 + 稳定性摘要 + 理解/成长计数
    # ============================================================
    def _build_current_identity_state_section(self) -> str:
        lines: List[str] = []

        # B1: Continuity（连续性评分：Identity 存在的连贯性）
        if self._continuity is not None:
            try:
                get_latest = getattr(self._continuity, "get_latest_report", None)
                report = get_latest() if callable(get_latest) else None
                if report is not None:
                    score = getattr(report, "continuity_score", None)
                    is_cont = getattr(report, "is_continuous", None)
                    if score is not None:
                        try:
                            score_f = float(score)
                            marker = "（连续）" if is_cont else "（需要关注）"
                            lines.append(f"身份连续性: {score_f:.3f} {marker}")
                        except Exception:
                            pass
                    # 分项分（有意义的摘要，不泄露实现细节）
                    sub_lines: List[str] = []
                    ts = getattr(report, "trait_stability_score", None)
                    if ts is not None:
                        try:
                            sub_lines.append(f"特质稳定 {float(ts):.2f}")
                        except Exception:
                            pass
                    cvs = getattr(report, "core_value_stability_score", None)
                    if cvs is not None:
                        try:
                            sub_lines.append(f"价值观稳定 {float(cvs):.2f}")
                        except Exception:
                            pass
                    if sub_lines:
                        lines.append("  - " + " / ".join(sub_lines))
            except Exception as exc:
                logger.warning("[IdentityContextBuilder] continuity 读取失败: %s", exc)

        # B2: Anchor Integrity（锚点完整性：identity.md 锚点有没有被偏移）
        if self._anchors is not None:
            try:
                validate = getattr(self._anchors, "validate_anchor_integrity", None)
                if callable(validate):
                    report = validate()
                    if report is not None:
                        intact = getattr(report, "is_intact", None)
                        drift = getattr(report, "max_weight_drift", None)
                        if intact is not None or drift is not None:
                            parts_anchor: List[str] = []
                            if intact is not None:
                                parts_anchor.append(
                                    "完整" if bool(intact) else "存在偏移"
                                )
                            if drift is not None:
                                try:
                                    parts_anchor.append(
                                        f"最大权重偏移 {float(drift):.3f}"
                                    )
                                except Exception:
                                    pass
                            if parts_anchor:
                                lines.append("身份锚点: " + " / ".join(parts_anchor))
            except Exception as exc:
                logger.warning("[IdentityContextBuilder] anchor integrity 读取失败: %s", exc)

        # B3: Stability（稳定性：合并连续性+锚点+记忆污染的综合分）
        if self._stability is not None:
            try:
                hist = getattr(self._stability, "history", None)
                if hist is not None:
                    get_snap = getattr(hist, "get_snapshot", None)
                    if callable(get_snap):
                        snap = get_snap()
                        if snap is not None:
                            stable = getattr(snap, "last_is_stable", None)
                            total = getattr(snap, "total_reports", None)
                            unstable_count = getattr(snap, "unstable_reports", None)
                            parts_stab: List[str] = []
                            if stable is not None:
                                parts_stab.append(
                                    "稳定" if bool(stable) else "波动"
                                )
                            if total is not None:
                                try:
                                    parts_stab.append(f"审计次数 {int(total)}")
                                except Exception:
                                    pass
                            if unstable_count is not None:
                                try:
                                    parts_stab.append(
                                        f"不稳定报告 {int(unstable_count)}"
                                    )
                                except Exception:
                                    pass
                            if parts_stab:
                                lines.append("身份稳定性: " + " / ".join(parts_stab))
            except Exception as exc:
                logger.warning("[IdentityContextBuilder] stability 读取失败: %s", exc)

        # B4: Growth / Understanding 概览（从 last IdentitySnapshot 取，Growth 写入点）
        if self._last_snap is not None:
            try:
                overview_lines: List[str] = []
                overall = getattr(self._last_snap, "overall_understanding", None)
                if overall is not None:
                    try:
                        overview_lines.append(
                            f"自我理解水平 {float(overall):.2f}"
                        )
                    except Exception:
                        pass
                for attr, label in (
                    ("growth_history_count", "成长记录"),
                    ("preferences_count", "偏好记录"),
                    ("behavioral_patterns_count", "行为模式"),
                    ("contradictions_count", "自我矛盾"),
                ):
                    val = getattr(self._last_snap, attr, None)
                    if val is not None:
                        try:
                            overview_lines.append(f"{label} {int(val)}")
                        except Exception:
                            pass
                if overview_lines:
                    lines.append("成长概览: " + " / ".join(overview_lines))
            except Exception as exc:
                logger.warning("[IdentityContextBuilder] growth/understanding 读取失败: %s", exc)

        return "\n".join(lines).strip()

    # ============================================================
    # Helper: fail-soft 字符串提取
    # ============================================================
    @staticmethod
    def _safe_str(value: Any) -> str:
        """任何对象安全转 str；None/异常 → ""。"""
        try:
            if value is None:
                return ""
            s = str(value)
            return s.strip()
        except Exception:
            return ""


__all__ = ["IdentityContextBuilder"]
