# -*- coding: utf-8 -*-
"""
scripts/stability_test.py

Phase 5.0-B: Runtime Stability Verification Script

目的:
证明羽依可以长期稳定运行。

行为:
- 模拟 N 轮事件(默认 10000)
- 通过 LongLoop + RuntimeAuditLogger 跑完整闭环
- 注入可控异常(每 X 轮抛一次,模拟子系统抖动)
- 统计:
  * exception 数量(按 phase 分类)
  * checkpoint 数量(periodic + final)
  * memory 增长(orchestrator.history 长度)
  * self_model 变化次数(通过 audit log SELF_MODEL_EVOLUTION 计数)
  * LongLoop.turn_count
  * LongLoop.error_count
  * audit.write_count / write_error_count / rotation_count
- 输出报告(文本 + JSON)

使用:
    python scripts/stability_test.py
    python scripts/stability_test.py --events 5000 --fail-every 100 --report data/stability_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 让 src 可导入
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.orchestrator.long_loop import LongLoop  # noqa: E402
from src.runtime.audit_log.runtime_audit_logger import (  # noqa: E402
    RuntimeAuditLogger,
    AuditEvent,
)


# ============================================================
# StableFakeOrchestrator: 模拟可注入异常的 Orchestrator
# ============================================================
class StableFakeOrchestrator:
    """稳定性测试用 Orchestrator 替身。

    - 持有 self_model_snapshots 模拟 memory 增长
    - 每 fail_every 轮(若>0)抛错一次
    - 触发 SelfModelOrchestrator.run_after_event(模拟真实接入)
    - 自身有 process / clear_history
    """

    def __init__(
        self,
        fail_every: int = 0,
        self_model_orchestrator: Optional[Any] = None,
    ) -> None:
        self._fail_every = max(0, int(fail_every or 0))
        self._self_model_orchestrator = self_model_orchestrator
        self._turn = 0
        self.history: List[str] = []
        # 模拟 memory 增长
        self.memory_size = 0
        # 模拟 self_model 变化追踪
        self.self_model_change_count = 0
        # 模拟 growth 状态
        self.growth_state = {"warmth": 0.5, "trust": 0.0}
        self.smo_call_count = 0

    def process(self, user_input: str) -> str:
        self._turn += 1
        if self._fail_every > 0 and self._turn % self._fail_every == 0:
            raise RuntimeError(f"stability_test induced failure at turn {self._turn}")

        self.history.append(user_input)
        self.memory_size += len(user_input) + 1  # 模拟 memory 占用

        # 模拟 self_model 变化(每 7 轮变一次)
        if self._turn % 7 == 0:
            self.self_model_change_count += 1

        # 模拟 growth 推进
        self.growth_state["trust"] = min(1.0, self.growth_state.get("trust", 0) + 0.001)

        # 触发 SelfModel(若注入)
        if self._self_model_orchestrator is not None:
            self.smo_call_count += 1
            try:
                result = self._self_model_orchestrator.run_after_event({
                    "trait_states": {"warmth": self.growth_state["warmth"]},
                    "current_state": {"turn": self._turn, "memory_size": self.memory_size},
                    "identity_overrides": None,
                })
                # 模拟 audit hook(只通过 smo 返回值判断变化)
                if result and result.get("evolution") is not None:
                    self.self_model_change_count += 1
            except Exception:
                pass

        return f"reply_{self._turn}"

    def clear_history(self) -> None:
        self.history = []


# ============================================================
# StableCountingSubsystems: 真实 SelfModel 5 子系统计数
# ============================================================
class CountingFoundation:
    def __init__(self) -> None:
        self.build_count = 0
        self.snapshots: List[Dict[str, Any]] = []

    def build(self, inputs=None):
        self.build_count += 1
        snap = {"snapshot_id": self.build_count, "from": inputs or {}}
        self.snapshots.append(snap)
        return snap

    def current(self):
        return None

    def snapshot(self):
        return None


class CountingEvolution:
    def __init__(self) -> None:
        self.evolve_count = 0

    def evolve(self, snapshot, proposals=None, reflections=None, manual_changes=None):
        self.evolve_count += 1
        out = dict(snapshot) if isinstance(snapshot, dict) else {"value": snapshot}
        out["evolution_id"] = self.evolve_count
        return _EvoResult(snapshot, out)


class _EvoResult:
    def __init__(self, old_snap, new_snap) -> None:
        self.old_snapshot = old_snap
        self.new_snapshot = new_snap


class CountingPersistence:
    def __init__(self) -> None:
        self.persist_count = 0
        self.saved: List[Dict[str, Any]] = []

    def persist_evolution(self, evolution_result):
        self.persist_count += 1
        new_snap = getattr(evolution_result, "new_snapshot", None)
        if isinstance(new_snap, dict):
            self.saved.append({
                "persisted_id": self.persist_count,
                "snapshot_id": new_snap.get("snapshot_id"),
                "evolution_id": new_snap.get("evolution_id"),
            })
        return True


# ============================================================
# 报告统计
# ============================================================
def _collect_report(
    loop: LongLoop,
    orch: StableFakeOrchestrator,
    audit: RuntimeAuditLogger,
    foundation: Optional[CountingFoundation],
    evolution: Optional[CountingEvolution],
    persistence: Optional[CountingPersistence],
    duration_s: float,
    n_events: int,
) -> Dict[str, Any]:
    """汇总稳定性报告。"""
    audit_records = audit.read_all()

    # 按 phase 分类异常
    exception_by_phase: Dict[str, int] = {}
    for r in audit_records:
        if r.get("event") == AuditEvent.EXCEPTION:
            phase = r.get("phase", "unknown")
            exception_by_phase[phase] = exception_by_phase.get(phase, 0) + 1

    # checkpoint 数量
    ckpt_count = audit.count_by_event(AuditEvent.CHECKPOINT)
    periodic_ckpt = sum(
        1 for r in audit_records
        if r.get("event") == AuditEvent.CHECKPOINT and r.get("reason") == "periodic"
    )
    final_ckpt = sum(
        1 for r in audit_records
        if r.get("event") == AuditEvent.CHECKPOINT and r.get("reason") == "final"
    )

    # self_model 变化(用 audit 自家 evolution 事件 + orch 内 change)
    smo_evolution_count = audit.count_by_event(AuditEvent.SELF_MODEL_EVOLUTION)

    # 内存增长
    memory_growth = orch.memory_size

    return {
        "events_requested": n_events,
        "events_processed": loop.turn_count,
        "duration_seconds": round(duration_s, 3),
        "events_per_second": round(loop.turn_count / max(duration_s, 1e-9), 2),
        "loop": {
            "state": loop.state.value,
            "shutdown_reason": loop.shutdown_reason,
            "error_count": loop.error_count,
            "checkpoint_count": loop.checkpoint_count,
        },
        "exceptions": {
            "total": audit.count_exceptions(),
            "by_phase": exception_by_phase,
        },
        "checkpoints": {
            "total": ckpt_count,
            "periodic": periodic_ckpt,
            "final": final_ckpt,
        },
        "memory": {
            "history_size": len(orch.history),
            "memory_size_bytes": memory_growth,
        },
        "self_model": {
            "internal_change_count": orch.self_model_change_count,
            "smo_evolution_count": smo_evolution_count,
            "foundation_build_count": foundation.build_count if foundation else 0,
            "evolution_evolve_count": evolution.evolve_count if evolution else 0,
            "persistence_persist_count": persistence.persist_count if persistence else 0,
        },
        "audit": {
            "write_count": audit.write_count,
            "write_error_count": audit.write_error_count,
            "rotation_count": audit.rotation_count,
            "log_path": audit.log_path,
            "file_size_bytes": sum(
                1 for _ in open(audit.log_path, "rb")  # noqa: PTH123
            ) if os.path.exists(audit.log_path) else 0,
        },
    }


def _print_report(report: Dict[str, Any]) -> None:
    print("=" * 70)
    print("Phase 5.0-B Runtime Stability Report")
    print("=" * 70)
    print(f"Events requested : {report['events_requested']}")
    print(f"Events processed : {report['events_processed']}")
    print(f"Duration         : {report['duration_seconds']}s")
    print(f"Throughput       : {report['events_per_second']} events/s")
    print("-" * 70)
    print(f"Loop state       : {report['loop']['state']}")
    print(f"Shutdown reason  : {report['loop']['shutdown_reason']}")
    print(f"Loop error count : {report['loop']['error_count']}")
    print(f"Loop ckpt count  : {report['loop']['checkpoint_count']}")
    print("-" * 70)
    print(f"Exceptions total : {report['exceptions']['total']}")
    for phase, n in report["exceptions"]["by_phase"].items():
        print(f"  - {phase}: {n}")
    print("-" * 70)
    print(f"Checkpoints      : total={report['checkpoints']['total']} "
          f"periodic={report['checkpoints']['periodic']} "
          f"final={report['checkpoints']['final']}")
    print("-" * 70)
    print(f"Memory history   : {report['memory']['history_size']}")
    print(f"Memory bytes     : {report['memory']['memory_size_bytes']}")
    print("-" * 70)
    print(f"SelfModel internal_changes : {report['self_model']['internal_change_count']}")
    print(f"SelfModel audit evolutions : {report['self_model']['smo_evolution_count']}")
    print(f"Foundation builds          : {report['self_model']['foundation_build_count']}")
    print(f"Evolution evolves          : {report['self_model']['evolution_evolve_count']}")
    print(f"Persistence persists       : {report['self_model']['persistence_persist_count']}")
    print("-" * 70)
    print(f"Audit writes      : {report['audit']['write_count']}")
    print(f"Audit write errors: {report['audit']['write_error_count']}")
    print(f"Audit rotations   : {report['audit']['rotation_count']}")
    print(f"Audit log path    : {report['audit']['log_path']}")
    print("=" * 70)


# ============================================================
# 主入口
# ============================================================
def run_stability_test(
    n_events: int = 10000,
    fail_every: int = 0,
    checkpoint_interval: int = 100,
    audit_path: Optional[str] = None,
    enable_self_model: bool = True,
    report_path: Optional[str] = None,
    print_to_stdout: bool = True,
) -> Dict[str, Any]:
    """执行 stability test,返回报告 dict。"""
    # 准备路径
    if audit_path is None:
        tmp = tempfile.mkdtemp(prefix="stability_")
        audit_path = os.path.join(tmp, "audit.jsonl")

    # 准备 audit
    audit = RuntimeAuditLogger(log_path=audit_path, max_bytes=10 * 1024 * 1024)

    # 准备 SelfModel 子系统
    foundation = None
    evolution = None
    persistence = None
    smo = None
    if enable_self_model:
        from src.orchestrator.self_model_orchestrator import SelfModelOrchestrator

        foundation = CountingFoundation()
        evolution = CountingEvolution()
        persistence = CountingPersistence()
        smo = SelfModelOrchestrator(
            foundation=foundation,
            evolution_engine=evolution,
            persistence_runtime=persistence,
            identity_id="yuyi_default",
        )

    # 准备 Orchestrator
    orch = StableFakeOrchestrator(
        fail_every=fail_every,
        self_model_orchestrator=smo,
    )

    # 准备 LongLoop
    events = [f"event_{i}" for i in range(n_events)] + ["exit"]
    idx = {"i": 0}

    def input_provider():
        v = events[idx["i"]]
        idx["i"] += 1
        return v

    # checkpoint_provider 简单落盘一份 state 到内存
    ckpt_states: List[Dict[str, Any]] = []

    def checkpoint_provider(state: Dict[str, Any]) -> None:
        ckpt_states.append(dict(state))

    loop = LongLoop(
        input_provider=input_provider,
        orchestrator=orch,
        on_reply=lambda u, r: None,
        checkpoint_provider=checkpoint_provider,
        checkpoint_interval=checkpoint_interval,
        audit_logger=audit,
    )

    # 运行
    t0 = time.perf_counter()
    loop.run()
    duration = time.perf_counter() - t0

    # 关闭 audit
    audit.close()

    # 报告
    report = _collect_report(
        loop=loop,
        orch=orch,
        audit=audit,
        foundation=foundation,
        evolution=evolution,
        persistence=persistence,
        duration_s=duration,
        n_events=n_events,
    )
    report["checkpoint_states_collected"] = len(ckpt_states)
    report["fail_every"] = fail_every
    report["checkpoint_interval"] = checkpoint_interval
    report["enable_self_model"] = enable_self_model

    if print_to_stdout:
        _print_report(report)

    if report_path:
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"\n[stability_test] 报告已保存到 {report_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[stability_test] 保存报告失败(已隔离): {exc}")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 5.0-B Runtime Stability Test",
    )
    parser.add_argument("--events", type=int, default=10000, help="模拟事件数")
    parser.add_argument("--fail-every", type=int, default=0, help="每 N 轮注入一次异常(0=不注入)")
    parser.add_argument("--checkpoint-interval", type=int, default=100, help="LongLoop 周期 checkpoint 间隔")
    parser.add_argument("--audit-path", type=str, default=None, help="audit log 路径")
    parser.add_argument("--no-self-model", action="store_true", help="禁用 SelfModel 子系统")
    parser.add_argument("--report", type=str, default=None, help="JSON 报告输出路径")
    args = parser.parse_args()

    report = run_stability_test(
        n_events=args.events,
        fail_every=args.fail_every,
        checkpoint_interval=args.checkpoint_interval,
        audit_path=args.audit_path,
        enable_self_model=not args.no_self_model,
        report_path=args.report,
    )

    # 简单通过/失败判定
    events_processed = report["events_processed"]
    exceptions_total = report["exceptions"]["total"]
    if events_processed < args.events:
        print(f"\n⚠️ 事件处理未完成: {events_processed}/{args.events}")
        return 1
    if report["audit"]["write_error_count"] > 0:
        print(f"\n⚠️ Audit 写入错误: {report['audit']['write_error_count']}")
        return 2
    print(f"\n✅ Stability test passed ({events_processed} events, {exceptions_total} recovered exceptions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
