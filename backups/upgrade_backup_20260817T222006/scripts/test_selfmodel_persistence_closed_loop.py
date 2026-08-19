"""
Phase 3.5.2 - Test 1: SelfModel 持久化闭环测试
=================================================

目标：验证
    TraitState（内存变化）
        ↓
    SelfModelAdapter.apply_external_change（或 apply_pcr）
        ↓
    SelfBelief / SelfHistory / SelfReflection（内存）
        ↓
    SelfModelAdapter.save_state()
        ↓
    SelfModelPersistence（JSONL 落盘）
        ↓
    ========== 关闭程序 ==========
        ↓
    新实例 SelfModelAdapter.load_state()
        ↓
    warmth/curiosity 是否保持变化

约束：
- 不修改真实 data/self_model/ 和 data/storage/self_model.json
- 全部写入 tempfile 临时目录
- pytest 可复现
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REPORT: Dict[str, Any] = {
    "test_time": datetime.now().isoformat(),
    "scenario": "warmth 0.30→0.33, curiosity 0.25→0.28 → save → reload → verify",
    "stages": {},
    "errors": [],
}


def section(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def subsection(title: str):
    print(f"\n--- {title} ---")


# ============================================================
# 1. 准备：构造完整 Proposal → Trait 内存变化
# ============================================================
def stage_1_prepare(tmpdir: Path) -> Dict[str, Any]:
    """构造 TraitState 变化（不走完整 Proposal 流程，直接构造 PCR）。"""
    section("阶段 1: 构造 PersonalityChangeRequest + Trait 初始状态")
    result: Dict[str, Any] = {"success": False}

    try:
        from src.personality.trait_state import create_trait_state

        # 模拟 PersonalityAdapter.apply_proposal() 内部构造的 trait_states
        # 即 { warmth: TraitState dict, curiosity: TraitState dict }
        trait_states_before: Dict[str, Dict[str, Any]] = {
            "warmth": create_trait_state("warmth", 0.30),
            "curiosity": create_trait_state("curiosity", 0.25),
            "gentleness": create_trait_state("gentleness", 0.40),
            "self_confidence": create_trait_state("self_confidence", 0.25),
        }

        print(f"  TraitStates 初始值（模拟'重启前'人格基线）：")
        for t, ts in trait_states_before.items():
            print(f"    {t:20s} = {ts.get('current_value')}  "
                  f"(momentum={ts.get('momentum')}, stability={ts.get('stability')})")

        # 构造 PCR（PersonalityChangeRequest）
        # 直接按契约构造，不经过 Evaluator 节省时间
        pcr_id = f"pcr_test_sm_{int(datetime.now().timestamp())}"
        proposal_id = f"prop_test_sm_{uuid.uuid4().hex[:8]}"
        evolution_record_id = f"rec_test_sm_{uuid.uuid4().hex[:6]}"

        # 模拟 TraitStateUpdater 执行后的内存值
        trait_states_after: Dict[str, Dict[str, Any]] = {
            t: dict(ts) for t, ts in trait_states_before.items()
        }
        trait_states_after["warmth"]["current_value"] = 0.33
        trait_states_after["curiosity"]["current_value"] = 0.28
        trait_states_after["warmth"]["direction"] = "increase"
        trait_states_after["curiosity"]["direction"] = "increase"
        trait_states_after["warmth"]["last_updated"] = datetime.now().isoformat()
        trait_states_after["curiosity"]["last_updated"] = datetime.now().isoformat()

        print(f"\n  应用成长后的 TraitStates（内存）：")
        for t in ["warmth", "curiosity", "self_confidence"]:
            ts = trait_states_after.get(t, {})
            print(f"    {t:20s} = {ts.get('current_value')}  "
                  f"(direction={ts.get('direction')})")

        pcr: Dict[str, Any] = {
            "request_id": pcr_id,
            "source_proposal_id": proposal_id,
            "source_insight_id": None,
            "evolution_record": {
                "record_id": evolution_record_id,
                "timestamp": datetime.now().isoformat(),
                "reason": "表达节奏优化：用户反馈重复提醒休息 → 微调 warmth/curiosity",
                "proposal_id": proposal_id,
                "trait_changes": {
                    "warmth": {"before": 0.30, "after": 0.33, "delta": +0.03},
                    "curiosity": {"before": 0.25, "after": 0.28, "delta": +0.03},
                },
                "narrative": "用户反馈休息提醒太机械，羽依决定把关心表达得更自然，同时探索更多表达方式。",
            },
            "growth_records": [
                {
                    "record_id": f"gr_test_sm_warmth_{uuid.uuid4().hex[:6]}",
                    "timestamp": datetime.now().isoformat(),
                    "proposal_id": proposal_id,
                    "growth_level": "preference",
                    "affected_dimensions": {"warmth": 0.03, "curiosity": 0.03},
                    "confidence": 0.88,
                    "evidence_count": 5,
                    "narrative": "5 次重复用户反馈 + 1 次直接偏好表达，形成稳定成长信号。",
                }
            ],
            "confidence": 0.88,
            "evidence_count": 5,
            "evaluator_meta": {
                "growth_level": "preference",
                "stability": 0.60,
                "consistency": 0.70,
                "pattern": "mechanical_repetition_feedback",
            },
            "reason": "长期用户偏好：减少机械重复，提升表达自然度。",
        }

        result["trait_states_before"] = trait_states_before
        result["trait_states_after"] = trait_states_after
        result["state_before_close"] = {
            a: trait_states_after[a]["current_value"] for a in ["warmth", "curiosity"]
        }
        result["pcr"] = pcr
        result["proposal_id"] = proposal_id
        result["pcr_id"] = pcr_id
        result["evolution_record_id"] = evolution_record_id
        result["success"] = True

    except Exception as e:
        result["errors"] = [{
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }]
        print(f"  ❌ {type(e).__name__}: {e}")
        traceback.print_exc()

    return result


# ============================================================
# 2. 注入 SelfModelAdapter + Persistence → apply_pcr
# ============================================================
def stage_2_apply_pcr(tmpdir: Path, s1: Dict[str, Any]) -> Dict[str, Any]:
    """通过 SelfModelAdapter.apply_pcr 将 PCR 写入 beliefs/history/reflections。"""
    section("阶段 2: SelfModelAdapter.apply_pcr → 内存状态写入")
    result: Dict[str, Any] = {"success": False}

    try:
        from src.personality.self_model_adapter import SelfModelAdapter
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_persistence import SelfModelPersistence

        # 临时数据目录
        sm_dir = tmpdir / "self_model"
        sm_dir.mkdir(parents=True, exist_ok=True)

        persistence = SelfModelPersistence(data_dir=str(sm_dir))
        print(f"  Persistence 数据目录: {sm_dir}")

        adapter = SelfModelAdapter(
            self_model_manager=None,
            self_model_updater=SelfModelUpdater(),
            self_model_store=None,  # 本次不测试 legacy SelfModelStore
            snapshot_enabled=False,
            actor="test_runner",
            min_confidence_belief=0.6,  # 降低门槛让测试更宽松
            min_confidence_trait=0.6,
        )
        adapter.attach_persistence(persistence)
        print(f"  ✅ SelfModelAdapter 初始化（带 persistence）")

        # 执行 apply_pcr
        pcr = s1["pcr"]
        subsection("apply_pcr 执行")
        envelope = adapter.apply_pcr(pcr, actor="test_runner")
        print(f"  applied: {envelope.get('applied')}")
        print(f"  note: {envelope.get('note')}")
        print(f"  beliefs_added: {envelope.get('beliefs_added')}")
        print(f"  beliefs_reinforced: {envelope.get('beliefs_reinforced')}")
        print(f"  history_event_id: {envelope.get('history_event_id')}")
        print(f"  reflection_note_id: {envelope.get('reflection_note_id')}")
        print(f"  self_model_updated: {envelope.get('self_model_updated')}")
        print(f"  warnings: {envelope.get('warnings', [])}")
        if envelope.get("errors"):
            print(f"  errors: {envelope.get('errors')}")
        if envelope.get("applied_changes"):
            print(f"  applied_changes keys: {list(envelope.get('applied_changes', {}).keys())[:10]}")

        # 读取内存中的 Beliefs / History / Reflections
        subsection("内存状态检查（未保存前）")
        beliefs = list(adapter.get_beliefs().all())
        history = list(adapter.get_history().all())
        reflections = list(adapter.get_reflections().all())
        print(f"  beliefs 数量: {len(beliefs)}")
        for b in beliefs[:3]:
            print(f"    - [{getattr(b, 'domain', '?')}] {getattr(b, 'content', '?')[:60]} "
                  f"(conf={getattr(b, 'confidence', None)}, warmth={getattr(b, 'weight_map', {}).get('warmth', '?')})")
        print(f"  history 数量: {len(history)}")
        for ev in history[:3]:
            print(f"    - {getattr(ev, 'event_type', '?')} | "
                  f"affected_traits={getattr(ev, 'affected_traits', {})} | "
                  f"summary={str(getattr(ev, 'summary', ''))[:40]}")
        print(f"  reflections 数量: {len(reflections)}")
        for r in reflections[:3]:
            print(f"    - [{getattr(r, 'reflection_type', '?')}] "
                  f"{getattr(r, 'content', '')[:60]}")

        # 检查文件尚未落盘
        files_before = list(sm_dir.glob("*"))
        print(f"  目录文件数（save 前）: {len(files_before)} -> {[f.name for f in files_before]}")

        result.update({
            "adapter": adapter,
            "persistence": persistence,
            "sm_dir": sm_dir,
            "apply_envelope": envelope,
            "beliefs_before_save": beliefs,
            "history_before_save": history,
            "reflections_before_save": reflections,
            "files_before_save_count": len(files_before),
            "pcr_applied_ok": envelope.get("applied") is True,
        })
        result["success"] = envelope.get("applied") is True and (len(beliefs) > 0 or len(history) > 0)

    except Exception as e:
        result["errors"] = [{
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }]
        print(f"  ❌ {type(e).__name__}: {e}")
        traceback.print_exc()

    return result


# ============================================================
# 3. save_state() → JSONL 落盘
# ============================================================
def stage_3_save(s2: Dict[str, Any]) -> Dict[str, Any]:
    """调用 SelfModelAdapter.save_state() 并验证文件确实被写入。"""
    section("阶段 3: save_state() → JSONL 落盘")
    result: Dict[str, Any] = {"success": False}

    try:
        adapter: SelfModelAdapter = s2["adapter"]
        sm_dir: Path = s2["sm_dir"]

        subsection("调用 save_state()")
        save_result = adapter.save_state(note="test_growth_sm_closed_loop")
        print(f"  save_result: {save_result}")

        # 检查落盘文件
        subsection("文件层检查")
        files_after = sorted(sm_dir.glob("*"))
        print(f"  目录文件数（save 后）: {len(files_after)}")
        for f in files_after:
            size = f.stat().st_size
            lines = 0
            if f.suffix == ".jsonl":
                try:
                    lines = len([l for l in f.read_text(encoding="utf-8").split("\n") if l.strip()])
                except Exception:
                    pass
            print(f"    - {f.name:25s} {size:6d} bytes {('('+str(lines)+' lines)' if lines else '')}")

        # 读取 beliefs.jsonl / history.jsonl 内容并人工验证 warmth 线索
        if (sm_dir / "beliefs.jsonl").exists():
            subsection("beliefs.jsonl 内容检查")
            lines = [l for l in (sm_dir / "beliefs.jsonl").read_text(encoding="utf-8").split("\n") if l.strip()]
            for idx, line in enumerate(lines[:5]):
                obj = json.loads(line)
                content = obj.get("content", "")
                domain = obj.get("domain", "")
                weight_map = obj.get("weight_map", {})
                print(f"    line {idx+1}: domain={domain}, warmth_w={weight_map.get('warmth')}, "
                      f"curiosity_w={weight_map.get('curiosity')}, content[:60]={content[:60]}")

        if (sm_dir / "history.jsonl").exists():
            subsection("history.jsonl 内容检查")
            lines = [l for l in (sm_dir / "history.jsonl").read_text(encoding="utf-8").split("\n") if l.strip()]
            for idx, line in enumerate(lines[:3]):
                obj = json.loads(line)
                print(f"    line {idx+1}: type={obj.get('event_type')}, "
                      f"traits={obj.get('affected_traits')}, summary={str(obj.get('summary', ''))[:50]}")

        if (sm_dir / "meta.json").exists():
            meta = json.loads((sm_dir / "meta.json").read_text(encoding="utf-8"))
            subsection("meta.json")
            print(f"  {json.dumps(meta, indent=2, ensure_ascii=False)}")

        result.update({
            "save_result": save_result,
            "files_after_count": len(files_after),
            "files": {f.name: f.stat().st_size for f in files_after},
            "save_ok": save_result.get("beliefs") is True
                      or save_result.get("history") is True
                      or save_result.get("reflections") is True,
        })
        result["success"] = result["save_ok"]

    except Exception as e:
        result["errors"] = [{
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }]
        print(f"  ❌ {type(e).__name__}: {e}")
        traceback.print_exc()

    return result


# ============================================================
# 4. 模拟重启：新 adapter + load_state()
# ============================================================
def stage_4_reload(s2: Dict[str, Any], s3: Dict[str, Any]) -> Dict[str, Any]:
    """模拟程序关闭 + 重启。创建全新 adapter 实例，调用 load_state()。"""
    section("阶段 4: 模拟重启 → 新实例 load_state()")
    result: Dict[str, Any] = {"success": False}

    try:
        from src.personality.self_model_adapter import SelfModelAdapter
        from src.personality.self_model_persistence import SelfModelPersistence

        sm_dir: Path = s2["sm_dir"]

        # 关键：销毁旧引用（用局部变量模拟 GC 不可达）
        # Python 无法真的"清除"旧对象，但我们用新的类实例 + 新 persistence 模拟重启
        old_adapter = s2["adapter"]
        old_persistence = s2["persistence"]
        del old_adapter, old_persistence

        subsection("创建新实例（模拟程序启动）")
        new_persistence = SelfModelPersistence(data_dir=str(sm_dir))
        new_adapter = SelfModelAdapter(
            self_model_manager=None,
            self_model_updater=None,
            self_model_store=None,
            snapshot_enabled=False,
            actor="reloaded_process",
        )
        new_adapter.attach_persistence(new_persistence)
        print(f"  ✅ 新 SelfModelAdapter 实例创建完成")
        print(f"     旧 beliefs/history/reflections 尚未 load，检查当前内存状态：")
        beliefs_bare = list(new_adapter.get_beliefs().all())
        history_bare = list(new_adapter.get_history().all())
        print(f"     beliefs (未 load): {len(beliefs_bare)}")
        print(f"     history (未 load): {len(history_bare)}")

        subsection("调用 load_state() 恢复数据")
        load_result = new_adapter.load_state()
        print(f"  load_result: {load_result}")

        beliefs_after = list(new_adapter.get_beliefs().all())
        history_after = list(new_adapter.get_history().all())
        reflections_after = list(new_adapter.get_reflections().all())
        print(f"\n  恢复后 beliefs: {len(beliefs_after)}")
        print(f"  恢复后 history: {len(history_after)}")
        print(f"  恢复后 reflections: {len(reflections_after)}")

        subsection("恢复后的 Beliefs 内容检查（warmth / curiosity 线索）")
        for idx, b in enumerate(beliefs_after[:5]):
            content = getattr(b, "content", "")
            domain = getattr(b, "domain", "")
            wm = getattr(b, "weight_map", {}) or {}
            sources = getattr(b, "sources", []) or []
            conf = getattr(b, "confidence", None)
            print(f"    [{idx+1}] domain={domain}, conf={conf}")
            print(f"        warmth_w={wm.get('warmth','?')}, curiosity_w={wm.get('curiosity','?')}")
            print(f"        sources={sources[:3]}")
            print(f"        content[:80]={content[:80]}")

        subsection("恢复后的 History 内容检查")
        for idx, ev in enumerate(history_after[:3]):
            print(f"    [{idx+1}] event_type={getattr(ev, 'event_type','?')}, "
                  f"actor={getattr(ev, 'actor','?')}")
            print(f"        affected_traits={getattr(ev, 'affected_traits', {})}")
            print(f"        summary[:60]={str(getattr(ev, 'summary',''))[:60]}")
            snap_before = getattr(ev, "snapshot_before", None)
            snap_after = getattr(ev, "snapshot_after", None)
            if snap_before:
                print(f"        snapshot_before keys: {list(snap_before.keys()) if isinstance(snap_before, dict) else 'non-dict'}")
            if snap_after:
                print(f"        snapshot_after keys: {list(snap_after.keys()) if isinstance(snap_after, dict) else 'non-dict'}")

        result.update({
            "new_adapter": new_adapter,
            "load_result": load_result,
            "beliefs_after_reload": beliefs_after,
            "history_after_reload": history_after,
            "reflections_after_reload": reflections_after,
        })
        result["success"] = len(beliefs_after) > 0 or len(history_after) > 0

    except Exception as e:
        result["errors"] = [{
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }]
        print(f"  ❌ {type(e).__name__}: {e}")
        traceback.print_exc()

    return result


# ============================================================
# 5. 验证：重启后 warmth/curiosity 变化是否仍可恢复
# ============================================================
def stage_5_verify(s1: Dict[str, Any], s4: Dict[str, Any]) -> Dict[str, Any]:
    """验证闭环：重启后的数据是否仍表明 warmth/curiosity 的变化。"""
    section("阶段 5: 闭环验证 — 重启后 warmth=0.33 / curiosity=0.28 是否可恢复")
    result: Dict[str, Any] = {"success": False}

    try:
        state_before_close: Dict[str, float] = s1["state_before_close"]
        beliefs = s4["beliefs_after_reload"]
        history = s4["history_after_reload"]

        print(f"  目标：恢复 warmth={state_before_close['warmth']}, "
              f"curiosity={state_before_close['curiosity']}")

        # 验证方式 1：从 beliefs.weight_map 中找到 warmth / curiosity 权重
        # 验证方式 2：从 history.affected_traits 中找到 warmth / curiosity delta
        subsection("验证 A：History.affected_traits 增量累积")
        warmth_delta_hist = 0.0
        curiosity_delta_hist = 0.0
        for ev in history:
            at = getattr(ev, "affected_traits", {}) or {}
            if isinstance(at, dict):
                if "warmth" in at:
                    try:
                        warmth_delta_hist += float(at["warmth"])
                    except Exception:
                        pass
                if "curiosity" in at:
                    try:
                        curiosity_delta_hist += float(at["curiosity"])
                    except Exception:
                        pass
        print(f"  history 中 warmth 总增量 Δ: {round(warmth_delta_hist, 5)}")
        print(f"  history 中 curiosity 总增量 Δ: {round(curiosity_delta_hist, 5)}")
        expected_warmth_delta = round(state_before_close["warmth"] - 0.30, 5)
        expected_curiosity_delta = round(state_before_close["curiosity"] - 0.25, 5)
        hist_ok = (abs(warmth_delta_hist - expected_warmth_delta) < 1e-5
                   and abs(curiosity_delta_hist - expected_curiosity_delta) < 1e-5)
        print(f"  → History 增量匹配: {'✅' if hist_ok else '❌'} "
              f"(期望 Δwarmth={expected_warmth_delta}, Δcuriosity={expected_curiosity_delta})")

        subsection("验证 B：Beliefs.weight_map 中存在 warmth/curiosity 权重")
        has_warmth_weight = False
        has_curiosity_weight = False
        warmth_weights = []
        curiosity_weights = []
        for b in beliefs:
            wm = getattr(b, "weight_map", {}) or {}
            if isinstance(wm, dict):
                if wm.get("warmth") is not None:
                    has_warmth_weight = True
                    warmth_weights.append((getattr(b, "content", "")[:40], wm.get("warmth")))
                if wm.get("curiosity") is not None:
                    has_curiosity_weight = True
                    curiosity_weights.append((getattr(b, "content", "")[:40], wm.get("curiosity")))
        print(f"  beliefs 中有 warmth weight: {'✅' if has_warmth_weight else '❌'} 样本: {warmth_weights[:2]}")
        print(f"  beliefs 中有 curiosity weight: {'✅' if has_curiosity_weight else '❌'} 样本: {curiosity_weights[:2]}")
        belief_wm_ok = has_warmth_weight and has_curiosity_weight

        subsection("验证 C：Reflections 内容提到 growth 叙事")
        reflections = s4["reflections_after_reload"]
        has_growth_reflection = False
        for r in reflections:
            content = (getattr(r, "content", "") or "").lower()
            rtype = (getattr(r, "reflection_type", "") or "").lower()
            if "growth" in rtype or "成长" in content or "warmth" in content or "curiosity" in content:
                has_growth_reflection = True
                print(f"  ✅ 找到相关 Reflection: [{rtype}] {content[:80]}")
                break
        if not has_growth_reflection and len(reflections) > 0:
            print(f"  ⚠️  Reflections 存在 {len(reflections)} 条，但未匹配 growth/warmth/curiosity 关键词")
        elif len(reflections) == 0:
            print(f"  ⚠️  Reflections 为空（非关键错误：adapter 可能未启用或 narrative 生成被跳过）")

        # 最终判断：History 增量必须精确匹配
        result.update({
            "check_history_delta_ok": hist_ok,
            "check_belief_weight_ok": belief_wm_ok,
            "check_has_reflection": has_growth_reflection,
            "warmth_delta_expected": expected_warmth_delta,
            "warmth_delta_from_history": warmth_delta_hist,
            "curiosity_delta_expected": expected_curiosity_delta,
            "curiosity_delta_from_history": curiosity_delta_hist,
        })
        # 关键闭环条件：History 增量可恢复 → 下次启动时 PersonalityResolver 可据此重建 Trait 基线
        result["success"] = hist_ok
        result["level4_candidate"] = hist_ok and belief_wm_ok

    except Exception as e:
        result["errors"] = [{
            "type": type(e).__name__,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }]
        print(f"  ❌ {type(e).__name__}: {e}")
        traceback.print_exc()

    return result


# ============================================================
# 主流程
# ============================================================
def main():
    section("Phase 3.5.2 - Test 1: SelfModel 持久化闭环测试")
    print(f"时间: {datetime.now().isoformat()}")
    print(f"场景: warmth 0.30→0.33, curiosity 0.25→0.28 → 保存 → 重启 → 恢复")

    with tempfile.TemporaryDirectory(prefix="yuyi_sm_closed_loop_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        print(f"\n临时目录: {tmpdir}")

        s1 = stage_1_prepare(tmpdir)
        REPORT["stages"]["1_prepare"] = {k: v for k, v in s1.items() if k not in ("pcr",)}
        if not s1.get("success"):
            REPORT["errors"].extend(s1.get("errors", []))
            finalize(tmpdir)
            return

        s2 = stage_2_apply_pcr(tmpdir, s1)
        REPORT["stages"]["2_apply_pcr"] = {
            k: v for k, v in s2.items()
            if k not in ("adapter", "persistence", "beliefs_before_save",
                          "history_before_save", "reflections_before_save")
        }
        REPORT["stages"]["2_apply_pcr"]["apply_envelope_keys"] = list(s2.get("apply_envelope", {}).keys())
        REPORT["stages"]["2_apply_pcr"]["belief_cnt_before"] = len(s2.get("beliefs_before_save", []))
        REPORT["stages"]["2_apply_pcr"]["hist_cnt_before"] = len(s2.get("history_before_save", []))
        REPORT["stages"]["2_apply_pcr"]["refl_cnt_before"] = len(s2.get("reflections_before_save", []))
        if not s2.get("success"):
            REPORT["errors"].extend(s2.get("errors", []))
            finalize(tmpdir)
            return

        s3 = stage_3_save(s2)
        REPORT["stages"]["3_save"] = s3
        if not s3.get("success"):
            REPORT["errors"].extend(s3.get("errors", []))
            finalize(tmpdir)
            return

        s4 = stage_4_reload(s2, s3)
        REPORT["stages"]["4_reload"] = {
            k: v for k, v in s4.items()
            if k not in ("new_adapter", "beliefs_after_reload", "history_after_reload",
                          "reflections_after_reload")
        }
        REPORT["stages"]["4_reload"]["belief_cnt_after"] = len(s4.get("beliefs_after_reload", []))
        REPORT["stages"]["4_reload"]["hist_cnt_after"] = len(s4.get("history_after_reload", []))
        REPORT["stages"]["4_reload"]["refl_cnt_after"] = len(s4.get("reflections_after_reload", []))
        if not s4.get("success"):
            REPORT["errors"].extend(s4.get("errors", []))
            finalize(tmpdir)
            return

        s5 = stage_5_verify(s1, s4)
        REPORT["stages"]["5_verify"] = s5
        REPORT["errors"].extend(s5.get("errors", []))

        finalize(tmpdir, s5)


def finalize(tmpdir: Path, s5: Optional[Dict[str, Any]] = None):
    section("最终报告")

    # 保存 JSON 报告
    report_path = PROJECT_ROOT / "data" / "growth" / "test_selfmodel_persistence_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n详细 JSON 报告已保存: {report_path}")

    # 阶段成功率
    total = len(REPORT["stages"])
    passed = sum(1 for v in REPORT["stages"].values() if v.get("success"))
    print(f"\n阶段通过率: {passed}/{total}")
    for name, stg in REPORT["stages"].items():
        ok = stg.get("success")
        symbol = "✅" if ok else "❌"
        print(f"  {symbol} Stage {name}")

    # Level 判定
    print(f"\n🎯 等级判断：")
    hist_ok = s5 and s5.get("check_history_delta_ok")
    belief_ok = s5 and s5.get("check_belief_weight_ok")
    l4 = s5 and s5.get("level4_candidate")

    if l4:
        print("   Level 4: 持久化成长闭环 ✅")
        print("   - TraitState（内存变化）→ SelfModelAdapter → JSONL（落盘）→ 新实例 load_state → History/Beliefs 可恢复")
        print("   - warmth/curiosity 增量精确匹配，重启后人格可重建")
    elif hist_ok:
        print("   Level 3→4 过渡态 ⚠️")
        print("   - History 增量闭环通过（下次启动可据此重建 TraitState）")
        if not belief_ok:
            print("   - Beliefs.weight_map 缺失 warmth/curiosity 权重（非阻塞，Belief 生成侧可能规则过严）")
    elif passed >= 3:
        print("   Level 3（持久化缺口未关闭）❌")
        print("   - 至少 3 阶段通过但关键闭环不成立，查看下方错误")
    else:
        print("   未达到 Level 3 持久化测试前置条件 ❌")

    # 错误详情
    if REPORT["errors"]:
        print(f"\n⚠️  执行过程中的错误：")
        for idx, e in enumerate(REPORT["errors"]):
            if isinstance(e, dict):
                print(f"  [{idx+1}] {e.get('type', '?')}: {e.get('message', '')}")
                tb = e.get("traceback", "")
                if tb:
                    # 只显示最后 3 行
                    print(f"       (traceback 最后 3 行)")
                    for l in tb.strip().split("\n")[-3:]:
                        print(f"       {l}")
            else:
                print(f"  [{idx+1}] {e}")

    print(f"\n临时目录（关闭程序后自动清理）: {tmpdir}")


if __name__ == "__main__":
    main()
