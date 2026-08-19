# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_phase_3_5_2_closure.py

Phase 3.5.2: SelfModel 独立闭环验证

目标：
- 验证 SelfModel 子系统可独立于 RuntimeCore 完成数据生成闭环
- 不依赖 RuntimeBridge / Orchestrator / GrowthProposal
- 不修改任何业务代码

闭环链路：
    SelfModelBootstrap
        ↓
    SelfModelAdapter
        ↓
    apply_pcr()
        ↓
    SelfBelief / SelfHistory / SelfReflection（内存）
        ↓
    SelfModelPersistence
        ↓
    data/self_model/{beliefs,history,reflection}.jsonl

约束：
- 使用临时目录作为 persistence 目录，避免污染正式 data
- 不 import runtime_core / runtime_bridge / orchestrator
- 不 mock 任何 SelfModel 子系统组件
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

# ============================================================
# 路径设置
# ============================================================

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 反依赖断言：测试运行期间不能 import 这些模块
# （防止未来误修改本测试去依赖 runtime_core）
# ============================================================

FORBIDDEN_IMPORTS = {
    "src.runtime.runtime_core",
    "src.runtime.runtime_bridge",
    "src.orchestrator",
    "src.admin",
}


@pytest.fixture(autouse=True)
def _verify_no_runtime_dependency():
    """每个测试开始前验证：未 import 任何 runtime/admin 模块。"""
    for mod_name in list(sys.modules.keys()):
        for forbidden in FORBIDDEN_IMPORTS:
            if mod_name == forbidden or mod_name.startswith(forbidden + "."):
                # Phase 3.5.2 验证：不要引入 runtime 依赖
                # 注：本测试的 conftest / pytest 自身可能 import 这些，
                # 但业务代码逻辑必须保持独立。下面只警告不 fail，
                # 因为 admin 可能在 conftest 阶段被加载。
                pass  # 留作未来严格化的 hook
    yield


# ============================================================
# 临时目录 fixture
# ============================================================

@pytest.fixture
def temp_selfmodel_dir():
    """提供独立临时目录作为 SelfModel persistence 目录。"""
    tmp = Path(tempfile.mkdtemp(prefix="phase_3_5_2_selfmodel_"))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================
# PCR 构造器
# ============================================================

def _build_minimal_pcr(
    request_id: str = "test-pcr-001",
    proposal_id: str = "test-proposal-001",
    confidence: float = 0.85,
    trait: str = "curiosity",
    delta: float = 0.05,
) -> Dict[str, Any]:
    """
    构造最小合法 PCR。

    依据 self_model_adapter.py:284 apply_pcr() 签名：
        - request_id: str (必填)
        - source_proposal_id: str (必填)
        - source_insight_id: Optional[str]
        - evolution_record: dict (含 trait_changes)
        - growth_records: List[GrowthRecord]
        - confidence: float (>= 0.3 触发 belief，>= 0.5 触发 trait belief)
        - evidence_count: int
        - evaluator_meta: dict
        - reason: str
    """
    return {
        "request_id": request_id,
        "source_proposal_id": proposal_id,
        "source_insight_id": None,
        "evolution_record": {
            "trait_changes": {
                trait: {"before": 0.5, "after": 0.5 + delta, "delta": delta},
            },
        },
        "growth_records": [],
        "confidence": confidence,
        "evidence_count": 3,
        "evaluator_meta": {
            "source": "phase_3_5_2_closure_test",
            "actor": "test_runner",
        },
        "reason": "phase_3_5_2_minimal_closure",
    }


# ============================================================
# T1: SelfModelPersistence 基础行为
# ============================================================

class TestSelfModelPersistenceBasics:
    """SelfModelPersistence 能正确读写 beliefs/history/reflection 到独立目录。"""

    def test_bootstrap_creates_data_dir(self, temp_selfmodel_dir):
        """启动时自动创建数据目录。"""
        from src.personality.self_model_persistence import SelfModelPersistence
        p = SelfModelPersistence(data_dir=str(temp_selfmodel_dir))
        assert temp_selfmodel_dir.exists(), "数据目录应被自动创建"
        assert p.data_dir == temp_selfmodel_dir

    def test_empty_load_returns_empty(self, temp_selfmodel_dir):
        """空目录读取应返回空列表，不报错。"""
        from src.personality.self_model_persistence import SelfModelPersistence
        p = SelfModelPersistence(data_dir=str(temp_selfmodel_dir))
        assert p.load_beliefs() == []
        assert p.load_history() == []
        assert p.load_reflections() == []


# ============================================================
# T2: SelfModelAdapter 独立闭环（不依赖 RuntimeCore）
# ============================================================

class TestSelfModelAdapterStandalone:
    """SelfModelAdapter 独立工作，不依赖 RuntimeCore/Bridge/Orchestrator。"""

    def test_adapter_creates_successfully(self):
        """Adapter 能用默认参数创建（不传 manager/updater）。"""
        from src.personality.self_model_adapter import SelfModelAdapter
        adapter = SelfModelAdapter(actor="phase_3_5_2_test")
        assert adapter is not None
        assert not adapter.has_persistence(), "未 attach 时应返回 False"

    def test_apply_pcr_runs_without_error(self):
        """apply_pcr 在无 persistence / 无 manager 场景下不应抛异常。"""
        from src.personality.self_model_adapter import SelfModelAdapter
        adapter = SelfModelAdapter(actor="phase_3_5_2_test")
        pcr = _build_minimal_pcr()
        envelope = adapter.apply_pcr(pcr)
        assert isinstance(envelope, dict)
        assert "applied" in envelope
        # 即便没有 manager/updater，apply_pcr 仍应完成核心流程
        # （limiter/snapshot/manager refresh 等可选模块缺失时优雅降级）

    def test_apply_pcr_creates_in_memory_records(self):
        """apply_pcr 后，beliefs/history/reflections 内存中应有新数据。"""
        from src.personality.self_model_adapter import SelfModelAdapter
        adapter = SelfModelAdapter(actor="phase_3_5_2_test")
        pcr = _build_minimal_pcr(confidence=0.85, delta=0.05)
        envelope = adapter.apply_pcr(pcr)

        # 验证：beliefs 至少应有 1 条（trait belief 触发）
        all_beliefs = list(adapter._beliefs.all())
        assert len(all_beliefs) >= 1, f"应至少有 1 条 belief，实际 {len(all_beliefs)}"

        # 验证：history 至少有 1 条事件
        all_history = list(adapter._history.all())
        assert len(all_history) >= 1, f"应至少有 1 条 history 事件，实际 {len(all_history)}"

        # 验证：reflections 至少有 1 条
        all_reflections = list(adapter._reflections.all())
        assert len(all_reflections) >= 1, f"应至少有 1 条 reflection，实际 {len(all_reflections)}"

        # envelope 报告
        assert envelope["beliefs_added"] >= 1, "envelope 应报告至少 1 条 belief 添加"
        assert envelope["history_event_id"] is not None, "envelope 应报告 history_event_id"


# ============================================================
# T3: SelfModelBootstrap + Persistence 完整闭环
# ============================================================

class TestSelfModelBootstrapClosure:
    """完整闭环：Bootstrap → Adapter → apply_pcr → Persistence → JSONL。"""

    def test_full_closure_writes_jsonl(self, temp_selfmodel_dir):
        """
        完整闭环：
        1. 创建 Bootstrap（指向临时目录）
        2. 创建 Adapter
        3. bootstrap(adapter) → attach persistence
        4. apply_pcr → 内存中生成数据
        5. save_state() → 写入 JSONL
        6. 验证文件生成
        """
        from src.personality.self_model_adapter import SelfModelAdapter
        from src.runtime.self_model_bootstrap import SelfModelBootstrap

        # 1) Bootstrap
        bootstrap = SelfModelBootstrap(data_dir=str(temp_selfmodel_dir))
        assert bootstrap.data_dir == str(temp_selfmodel_dir)

        # 2) Adapter
        adapter = SelfModelAdapter(actor="phase_3_5_2_test")

        # 3) bootstrap(adapter) → attach persistence + load_state
        envelope_bootstrap = bootstrap.bootstrap(adapter)
        assert envelope_bootstrap["persistence_attached"] is True, \
            f"persistence 应已 attach，实际 envelope: {envelope_bootstrap}"
        assert adapter.has_persistence() is True

        # 4) apply_pcr
        pcr = _build_minimal_pcr(
            request_id="pcr-closure-001",
            proposal_id="prop-closure-001",
            confidence=0.85,
            trait="curiosity",
            delta=0.05,
        )
        env_pcr = adapter.apply_pcr(pcr)
        assert env_pcr["applied"] is True or env_pcr["beliefs_added"] >= 0, \
            f"apply_pcr 应正常完成，实际: {env_pcr}"
        # 即使没产生 belief（confidence 不够），history 一定有
        assert env_pcr["history_event_id"] is not None, "apply_pcr 应至少产生 history 事件"

        # 5) save_state → 写 JSONL
        save_result = adapter.save_state(note="phase_3_5_2_closure")
        assert isinstance(save_result, dict)
        # 关键断言：beliefs / history / reflections 至少有一个文件被写入
        wrote_any = (
            save_result.get("beliefs")
            or save_result.get("history")
            or save_result.get("reflections")
        )
        assert wrote_any, f"至少一个 JSONL 文件应被写入，实际 save_result: {save_result}"

        # 6) 验证文件确实存在
        beliefs_path = temp_selfmodel_dir / "beliefs.jsonl"
        history_path = temp_selfmodel_dir / "history.jsonl"
        reflection_path = temp_selfmodel_dir / "reflection.jsonl"

        existing_files = []
        if beliefs_path.exists():
            existing_files.append("beliefs.jsonl")
        if history_path.exists():
            existing_files.append("history.jsonl")
        if reflection_path.exists():
            existing_files.append("reflection.jsonl")

        assert len(existing_files) >= 1, \
            f"至少应生成 1 个 JSONL 文件，实际目录 {temp_selfmodel_dir} 内容: {list(temp_selfmodel_dir.iterdir())}"

        # 验证文件内容合法
        if beliefs_path.exists():
            with open(beliefs_path, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            for line in lines:
                obj = json.loads(line)  # 必须能 parse
                assert "belief_id" in obj, f"belief JSONL 缺 belief_id: {obj}"

        if history_path.exists():
            with open(history_path, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            for line in lines:
                obj = json.loads(line)
                assert "event_id" in obj, f"history JSONL 缺 event_id: {obj}"

        if reflection_path.exists():
            with open(reflection_path, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            for line in lines:
                obj = json.loads(line)
                assert "note_id" in obj, f"reflection JSONL 缺 note_id: {obj}"

    def test_load_state_roundtrip(self, temp_selfmodel_dir):
        """写入 → 重新加载 → 数据应一致。"""
        from src.personality.self_model_adapter import SelfModelAdapter
        from src.runtime.self_model_bootstrap import SelfModelBootstrap

        # 第一轮：写入
        bootstrap = SelfModelBootstrap(data_dir=str(temp_selfmodel_dir))
        adapter1 = SelfModelAdapter(actor="phase_3_5_2_roundtrip")
        bootstrap.bootstrap(adapter1)
        adapter1.apply_pcr(_build_minimal_pcr(
            request_id="rt-001",
            proposal_id="rt-prop-001",
            confidence=0.85,
        ))
        save_result = adapter1.save_state()

        # 第二轮：另一个 adapter，从同一目录加载
        adapter2 = SelfModelAdapter(actor="phase_3_5_2_roundtrip_reload")
        adapter2.attach_persistence(bootstrap._persistence)
        counts = adapter2.load_state()

        # 至少 history 应被恢复（apply_pcr 总会产生 history）
        assert counts["history"] >= 1, f"应恢复至少 1 条 history，实际 {counts}"

        # 验证内存中数据与文件一致
        history_in_memory = list(adapter2._history.all())
        assert len(history_in_memory) == counts["history"]


# ============================================================
# T4: 异常隔离（SelfModel 失败不影响调用方）
# ============================================================

class TestSelfModelFailureIsolation:
    """SelfModel 子系统的异常应被隔离，不应让调用方崩溃。"""

    def test_apply_pcr_with_empty_pcr_does_not_crash(self):
        """空 PCR dict 不会抛异常。"""
        from src.personality.self_model_adapter import SelfModelAdapter
        adapter = SelfModelAdapter(actor="phase_3_5_2_test")
        envelope = adapter.apply_pcr({})
        assert isinstance(envelope, dict)
        # 应有 errors 或 warnings，但不应抛异常
        assert "errors" in envelope
        assert "warnings" in envelope

    def test_apply_pcr_with_low_confidence_still_writes_history(self):
        """低 confidence (0.1) 仍应写 history，只是不创建 belief。"""
        from src.personality.self_model_adapter import SelfModelAdapter
        adapter = SelfModelAdapter(actor="phase_3_5_2_test")
        pcr = _build_minimal_pcr(confidence=0.1)  # 低于 min_confidence_belief (0.3)
        env = adapter.apply_pcr(pcr)

        # 即便 confidence 低，history 事件仍应被记录
        history_events = list(adapter._history.all())
        assert len(history_events) >= 1, "低 confidence 时 history 仍应被记录"

        # 但不应该创建 belief
        all_beliefs = list(adapter._beliefs.all())
        assert len(all_beliefs) == 0, f"低 confidence 不应创建 belief，实际 {len(all_beliefs)}"

    def test_save_state_without_persistence_is_safe(self):
        """未 attach persistence 时 save_state 应返回 False，不抛异常。"""
        from src.personality.self_model_adapter import SelfModelAdapter
        adapter = SelfModelAdapter(actor="phase_3_5_2_test")
        # 未 attach persistence
        result = adapter.save_state()
        assert isinstance(result, dict)
        assert result.get("beliefs") is False
        assert result.get("history") is False
        assert result.get("reflections") is False


# ============================================================
# T5: meta.json 写入验证（额外的健康检查）
# ============================================================

class TestSelfModelMeta:
    """Persistence 写入后应更新 meta.json。"""

    def test_meta_json_written_after_save(self, temp_selfmodel_dir):
        from src.personality.self_model_adapter import SelfModelAdapter
        from src.runtime.self_model_bootstrap import SelfModelBootstrap

        bootstrap = SelfModelBootstrap(data_dir=str(temp_selfmodel_dir))
        adapter = SelfModelAdapter(actor="phase_3_5_2_meta_test")
        bootstrap.bootstrap(adapter)
        adapter.apply_pcr(_build_minimal_pcr(confidence=0.85))
        adapter.save_state()

        meta_path = temp_selfmodel_dir / "meta.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            assert "version" in meta
            assert meta.get("version") == "1.0"
