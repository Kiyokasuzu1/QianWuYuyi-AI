# -*- coding: utf-8 -*-
"""
Phase 3.6 B 组：5 条真实性场景模拟。

不用 LLM（避免不可控/调用成本），用 GrowthEvaluator 已有逻辑 + 手工 PCR 模拟
"用户说了 X → 产生什么成长提案 → 什么 trait delta → 保存 → 重启 → 诊断"
的完整链路，用于 Phase 3.6 初始真实性测试。

场景列表（模拟真实用户与羽依的一次"一周互动"）：
  S1 日常慰藉        用户表达疲惫，羽依尝试安慰  → 小幅度 warmth/caring 提升
  S2 风格反馈        用户说"提醒不要机械"        → warmth + natural_expression 提升
  S3 冲突互动        用户语气不好 + 不耐烦       → 紧张度上升 / shyness 波动
  S4 主动道歉        用户主动道歉                → trust / security 提升
  S5 重启恢复        关机模拟 → 新 Runtime 重启  → 确认 personality 连续存在

每步都输出:
  - 输入场景
  - PCR delta
  - 应用后 trait 状态
  - 最终 diagnose_growth_health 报告（稳定性/合理性/可解释性/恢复性）
"""
from __future__ import annotations

import json
import sys
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _adapter(data_dir: str):
    from src.personality.self_model_adapter import SelfModelAdapter
    from src.personality.self_model_persistence import SelfModelPersistence
    adapter = SelfModelAdapter(actor="sim_phase36")
    adapter.attach_persistence(SelfModelPersistence(data_dir))
    return adapter


def _pcr(traits: Dict[str, float], summary: str, confidence: float = 0.8) -> Dict[str, Any]:
    return {
        "request_id": f"pcr_{uuid.uuid4().hex[:8]}",
        "source_proposal_id": f"prop_{uuid.uuid4().hex[:8]}",
        "reason": summary,
        "confidence": confidence,
        "evidence_count": 1,
        "evolution_record": {
            "trait_changes": dict(traits),
        },
    }


def _apply(adapter, traits, summary, confidence=0.8):
    r = adapter.apply_pcr(_pcr(traits, summary, confidence))
    assert r.get("applied") is True, f"PCR 未应用: {r}"
    return r


def _print_divider(title):
    print("\n" + "─" * 72)
    print(f"  {title}")
    print("─" * 72)


def _current_traits(adapter):
    """通过 TraitRebuilder 重建当前 trait 值（BASE + sum(deltas)）。"""
    from src.runtime.trait_rebuilder import TraitRebuilder
    return TraitRebuilder().rebuild(adapter)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="yuyi_phase36_sim_")
    data_dir = Path(tmp)
    print(f"📁 临时数据目录: {data_dir}\n")

    try:
        # ============================================================
        # 初始基线（BASE）
        # ============================================================
        _print_divider("【基线】新羽依，0 次互动")
        adapter = _adapter(str(data_dir))
        from src.personality.personality_profile import PersonalityProfile
        base = PersonalityProfile.get_base()
        print("初始 BASE (启动时的人格底色):")
        for k, v in base.items():
            print(f"  {k:<24s}: {v:.4f}")

        # ============================================================
        # S1 日常慰藉
        # ============================================================
        _print_divider("S1 | 日常慰藉  — 用户说: 『今天好累…加班到现在，什么都不想做。』")
        print("预期: 用户示弱 → 羽依的温暖与关心有机会提升，小幅度。")
        _apply(
            adapter,
            traits={"warmth": 0.02, "caring": 0.025},
            summary="用户表达疲惫与疲惫，羽依尝试提供安慰。温柔与关心倾向微升。",
            confidence=0.82,
        )
        adapter.save_state()
        cur = _current_traits(adapter)
        print(f"当前 trait 重建值: {json.dumps(cur, ensure_ascii=False)}")

        # ============================================================
        # S2 风格反馈（高价值成长信号）
        # ============================================================
        _print_divider("S2 | 风格反馈  — 用户说: 『刚刚你的提醒有点像系统通知，和我聊天可以自然一点～』")
        print("预期: 明确风格偏好反馈 → warmth 中幅提升（知道怎么调整更好）；shyness 略降。")
        _apply(
            adapter,
            traits={"warmth": 0.04, "shyness": -0.015, "sensitivity": 0.01},
            summary="用户明确反馈提醒过于机械化，希望对话更自然、更像朋友。温暖倾向显著提升，羞怯略降。",
            confidence=0.9,  # 用户明确表达偏好，高置信
        )
        adapter.save_state()
        cur = _current_traits(adapter)
        print(f"当前 trait 重建值: {json.dumps(cur, ensure_ascii=False)}")

        # ============================================================
        # S3 冲突互动
        # ============================================================
        _print_divider("S3 | 冲突互动  — 用户说: 『行了行了别问了，我现在没心情说话。』")
        print("预期: 用户不耐烦 → shyness/sensitivity 上升（更紧张、更敏感对方情绪）；warmth 轻微下降。")
        _apply(
            adapter,
            traits={"shyness": 0.02, "sensitivity": 0.015, "warmth": -0.01, "caring": -0.005},
            summary="用户表达强烈不耐烦情绪。羽依感知到被拒绝，敏感与羞怯上升，温暖与关心暂时回调。",
            confidence=0.75,
        )
        adapter.save_state()
        cur = _current_traits(adapter)
        print(f"当前 trait 重建值: {json.dumps(cur, ensure_ascii=False)}")

        # ============================================================
        # S4 主动道歉（修复关系信号）
        # ============================================================
        _print_divider("S4 | 主动道歉  — 用户说: 『不好意思刚刚语气不好，我不是针对你。』")
        print("预期: 用户修复关系 → trust/security 提升；shyness 回调；warmth 反弹（关系被确认）。")
        _apply(
            adapter,
            traits={"warmth": 0.02, "caring": 0.01, "shyness": -0.015, "sensitivity": -0.005},
            summary="用户主动道歉并确认关系无问题。温暖回归，羞怯回调，敏感减轻。",
            confidence=0.88,
        )
        adapter.save_state()
        cur = _current_traits(adapter)
        print(f"当前 trait 重建值: {json.dumps(cur, ensure_ascii=False)}")

        # ============================================================
        # S5 重启模拟
        # ============================================================
        _print_divider("S5 | 关机 → 重新启动（模拟第二天）")
        # 旧 adapter 完全丢弃
        del adapter
        # 新 adapter + 新 resolver
        adapter2 = _adapter(str(data_dir))
        load_counts = adapter2.load_state()
        print(f"load_state: {load_counts}")

        from src.personality.personality_resolver import PersonalityResolver
        from src.runtime.trait_rebuilder import TraitRebuilder, inject_trait_states
        from src.runtime.growth_history_view import inject_growth_history_view

        resolver = PersonalityResolver()
        values = TraitRebuilder().rebuild(adapter2)
        inject_trait_states(resolver, values)
        inject_growth_history_view(resolver, adapter2)

        # resolve 3 次模拟三轮对话，观察漂移
        prev = None
        print("\n三次 resolve() 观察漂移:")
        for i in range(1, 4):
            vec = resolver.resolve()
            data = dict(vec.get_all() or {})
            warmth = data.get("warmth")
            shyness = data.get("shyness")
            print(f"  第 {i} 次 resolve  warmth={warmth:.4f}  shyness={shyness:.4f}")
            if prev is not None:
                dw = warmth - prev[0]
                ds = shyness - prev[1]
                print(f"         ↳ 相对上次 Δwarmth={dw:+.5f} Δshyness={ds:+.5f}")
            prev = (warmth, shyness)

        # growth_history 视图检查
        gh = resolver.growth_history
        print(f"\nGrowthHistoryView 记录数: {gh.count()}")
        for i, record in enumerate(gh.all()[:3], 1):
            dims = ", ".join(record.get("affected_dimensions", []))
            changes = " ".join(
                f"{k}{v['delta']:+.3f}" for k, v in record.get("changes", {}).items()
            )
            print(f"  [{i}] {dims}  {changes}  mean={record.get('meaning','')[:45]}")

        # ============================================================
        # 最终诊断
        # ============================================================
        _print_divider("【 Phase 3.6 初始诊断报告 】")
        from scripts.diagnose_growth_health import diagnose, _render_human
        report = diagnose(str(data_dir), warn_delta=0.04)
        print(_render_human(report, show_events=5))

        # ============================================================
        # 总结：相对 BASE 的变化合理性检查
        # ============================================================
        _print_divider("【 相对 BASE 变化汇总（人类审核） 】")
        final = values
        print(f"{'维度':<24s} {'BASE':>7s} {'最终值':>7s} {'总Δ':>9s} {'评估':>10s}")
        for trait in sorted(set(base.keys()) | set(final.keys())):
            bv = base.get(trait, 0.5)
            fv = final.get(trait, bv)
            delta = fv - bv
            # 合理性判断：单维度变化幅度应该在 -0.05 ~ +0.08 之间（一周 5 次互动）
            assessment = "✅ 合理"
            if delta > 0.08:
                assessment = "⚠️ 过大"
            elif delta < -0.05:
                assessment = "⚠️ 过大"
            print(f"{trait:<24s} {bv:>7.4f} {fv:>7.4f} {delta:>+9.4f} {assessment:>10s}")

        _print_divider("模拟完成。临时数据目录保留供检查:")
        print(f"  {data_dir}")
        return 0

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[FATAL] 模拟失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
