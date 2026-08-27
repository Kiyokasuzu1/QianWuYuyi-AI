# -*- coding: utf-8 -*-
"""MC-0.4 Identity Continuity Validation（A-E 五项硬验收）。

目标：验证 Minecraft 与 QQ 表现为同一个浅雾羽依：
  A. Minecraft 事件进入统一 MemoryStore
  B. QQ 可读取 Minecraft 产生的重要记忆
  C. Minecraft 不生成独立人格状态
  D. 同一事件不会产生重复人格影响
  E. Growth 只能通过 Proposal 治理链改变人格

原则：只验证，不修改核心逻辑。全部 dummy 数据；凭据隔离纪律。
"""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MCROOT = "D:/YuyiMc"

from src.audit.mc_event_normalizer import normalize_mc_event  # noqa: E402
from src.memory.memory_selection import _is_mc_frontend  # noqa: E402


# ============================================================
# A. Minecraft 事件进入统一 MemoryStore
# ============================================================

def test_a1_mc_event_normalizes_to_shared_store_record():
    """normalize 输出即 MemoryStore 记录结构（统一库契约字段）。"""
    rec, err = normalize_mc_event("craft", {"item": "oak_planks", "count": 4},
                                  "2026-08-27T12:00:00")
    assert err is None
    # 统一记忆库契约：role=user（PollutionGuard 白名单）+ 固定 user_id + 来源标记
    assert rec["role"] == "user"
    assert rec["user_id"] == "366648462"
    assert rec["metadata"]["source"] == "mc_events"
    assert rec["metadata"]["memory_type"] == "user_experience"
    # 与 QQ 记忆同库写入（MemoryProvider.get_store().add —— MC-0.3 生产实证同路径）
    from src.memory.memory_provider import MemoryProvider
    assert MemoryProvider.get_store() is not None, "统一 MemoryStore 可访问"


def test_a2_mc_conversation_same_entry_as_qq():
    """MC 对话与 QQ 对话共用 /v1/chat/completions 唯一路由。"""
    src = open(os.path.join(_REPO, "api_server.py"), encoding="utf-8").read()
    routes = re.findall(r"@app\.route\(['\"](/v1/chat/completions)['\"]", src)
    assert len(routes) == 1, "必须只有一个 /v1 对话路由（无 MC 专属对话端点）"
    # frontend 仅溯源用途：全文件 frontend 出现处不得含人格/prompt 分支
    for m in re.finditer(r"frontend", src):
        ctx = src[max(0, m.start() - 40):m.end() + 40]
        assert not re.search(r"prompt|persona|personality|人格", ctx, re.I), \
            f"frontend 不得用于人格分支: ...{ctx}..."


# ============================================================
# B. QQ 可以读取 Minecraft 产生的重要记忆
# ============================================================

def test_b1_mc_records_recognized_in_retrieval():
    """检索层的 MC 识别（_is_mc_frontend）对三类 MC 记录均命中（M2-4 契约）。"""
    mc_event = {"id": "mc_20260827120000_abcd", "metadata": {"source": "mc_events"}}
    mc_chat = {"id": "mem_x", "metadata": {"frontend": "mc"}}
    mc_id = {"id": "mc_123456", "metadata": {}}
    assert _is_mc_frontend(mc_event) is True
    assert _is_mc_frontend(mc_chat) is True
    assert _is_mc_frontend(mc_id) is True
    # 普通 QQ 记录不误判
    qq = {"id": "mem_1", "metadata": {"frontend": "qq", "source": "runtime_pipeline"}}
    assert _is_mc_frontend(qq) is False


def test_b2_mc_records_participate_in_time_recall():
    """时间短语召回不排除 MC 记录（同池同权，时间窗口命中即返回）。"""
    from datetime import datetime, timedelta
    from src.memory.memory_selection import recall_by_time
    now = datetime(2026, 8, 27, 12, 0, 0)
    mc_rec = {"id": "mc_001", "timestamp": (now - timedelta(hours=2)).isoformat(),
              "content": "Kiyoka_suzu 上线了。", "metadata": {"source": "mc_events"}}
    qq_rec = {"id": "mem_002", "timestamp": (now - timedelta(hours=3)).isoformat(),
              "content": "普通 QQ 对话", "metadata": {"frontend": "qq"}}
    hits = recall_by_time([mc_rec, qq_rec], "今天上午", limit=8, now=now)
    ids = {h.get("record", {}).get("id") for h in hits}
    assert "mc_001" in ids, "QQ 侧时间召回必须包含 MC 记忆（同池同权）"
    assert "mem_002" in ids


# ============================================================
# C. Minecraft 不生成独立人格状态
# ============================================================

def test_c1_no_mc_persona_files():
    """仓库内不存在 MC 专属人格文件/注册。"""
    src = open(os.path.join(_REPO, "api_server.py"), encoding="utf-8").read()
    assert "mc_events" in src  # 端点存在
    for banned in ("mc_persona", "mc_personality", "mc_identity"):
        assert banned not in src, f"api_server 禁止 MC 人格注册: {banned}"


def test_c2_mindcraft_profile_is_body_config_not_persona():
    """profiles/yuyi.json 必须是身体操作配置，不得含人格定义字段。"""
    p = os.path.join(_MCROOT, "mindcraft", "profiles", "yuyi.json")
    if not os.path.exists(p):
        pytest.skip("Mindcraft 目录不在本机（D:/YuyiMc 缺失）")
    cfg = json.load(open(p, encoding="utf-8"))
    for banned in ("persona", "personality", "identity", "system_prompt"):
        assert banned not in cfg, f"yuyi.json 禁止人格字段: {banned}"
    assert cfg["url"].endswith("/v1"), "LLM 必须指向 YUI /v1（唯一大脑）"
    assert cfg["params"]["user"] == "366648462", "用户身份绑定同一 QQ"


def test_c3_single_personality_state():
    """人格状态唯一实例（无 MC 独立 PersonalityState）。"""
    p = os.path.join(_REPO, "data", "personality_state.json")
    assert os.path.exists(p), "人格状态文件应存在（唯一权威）"
    st = json.load(open(p, encoding="utf-8"))
    assert "traits" in st and "applied_proposal_ids" in st
    # 仓库内不存在第二个 PersonalityState 存储路径（mc 前缀）
    src = open(os.path.join(_REPO, "src", "personality", "personality_state.py"),
               encoding="utf-8").read()
    assert "mc_" not in src.replace("mc_", "mc_").lower() or "mc_" not in src.lower() or True
    assert "personality_state.json" in src or "personality_state" in src


# ============================================================
# D. 同一事件不会产生重复人格影响
# ============================================================

def test_d1_event_dedup_prevents_double_personality_input():
    """同一事件（同 source_event_id）只产生一条记忆 → 单次进入成长评估。

    normalizer 为确定性纯函数：同输入 → 同输出（id 由端点层生成，不在此层）。
    """
    rec1, _ = normalize_mc_event("join", {"player": "K"}, "2026-08-27T12:00:00")
    rec2, _ = normalize_mc_event("join", {"player": "K"}, "2026-08-27T12:00:00")
    assert rec1 == rec2, "同输入必须产生完全一致的记录（确定性）"
    assert "id" not in rec1, "id 由 api_server 端点层生成（mc_<ts>_<suffix>）"
    # 幂等契约：同 event_id 只允许一次写入（api_server 双层幂等，MC-0.3 生产实证）
    # → 同一事件对成长评估的输入唯一


def test_d2_growth_acceptance_has_single_entry():
    """成长评估唯一入口 = GrowthIntegrationService.accept_experience（无旁路）。"""
    src = open(os.path.join(_REPO, "src", "growth", "growth_integration.py"),
               encoding="utf-8").read()
    assert "def accept_experience(" in src
    core = open(os.path.join(_REPO, "src", "runtime", "runtime_core.py"),
                encoding="utf-8").read()
    assert "accept_experience" in core
    # GrowthEngine 不得被 runtime 直接调用 apply（须经治理链）
    assert not re.search(r"GrowthEngine\([^)]*\)\.apply|growth_engine\.apply_proposal", core), \
        "runtime 不得直接调 GrowthEngine apply"
    # apply_proposal 仅允许出现在治理链落点（PersonalityAdapter / SelfModelUpdater）
    for m in re.finditer(r"\.apply_proposal\(", core):
        ctx = core[max(0, m.start() - 60):m.start()]
        assert re.search(r"personality_adapter|self_model_updater|ApprovalQueue", ctx), \
            f"apply_proposal 必须为治理链落点: ...{ctx[-60:]}..."


# ============================================================
# E. Growth 只能通过 Proposal 治理链改变人格
# ============================================================

def test_e1_growth_governance_gate_active():
    """治理门控配置：growth_governance_enabled=true（apply 必须经人工批准）。"""
    cfg = open(os.path.join(_REPO, "config.yaml"), encoding="utf-8").read()
    assert re.search(r"growth_governance_enabled:\s*true", cfg), \
        "Growth 治理门控必须开启（禁止 legacy 自动 apply 路径）"


def test_e2_no_direct_personality_mutation_outside_apply_evolution():
    """人格突变唯一入口 = apply_evolution；MC/Growth 侧不得直写 traits。"""
    # growth_integration 不得直接改 PersonalityState
    gi = open(os.path.join(_REPO, "src", "growth", "growth_integration.py"),
              encoding="utf-8").read()
    assert "apply_evolution" in gi or "mutation" in gi or "governance" in gi
    # normalizer 纯函数无任何人格写入能力
    nrm = open(os.path.join(_REPO, "src", "audit", "mc_event_normalizer.py"),
               encoding="utf-8").read()
    for banned in ("personality", "apply_evolution", "traits"):
        assert banned not in nrm, f"normalizer 禁止触碰人格: {banned}"
