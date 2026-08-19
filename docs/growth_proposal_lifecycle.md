# GrowthProposal Lifecycle（Phase 3.6.5 Frozen）

> 文档版本: v1.0（与 `GrowthProposal.schema_version = "1.0"` 同步冻结）
> 创建时间: 2026-07-30
> 适用范围: Phase 3.6.x → Runtime Integration

本文档描述 GrowthProposal 的完整生命周期：从治理侧（legacy / governance）输入，到 canonical schema，再到下游 consumer / personality update 的端到端流程。

---

## 1. 生命周期总览

```
Legacy Governance Proposal
        ↓
GrowthProposalNormalizer
        ↓
Canonical GrowthProposal
        ↓
GrowthEvaluator
        ↓
GrowthProposalTranslator
        ↓
Personality Change Request (PCR)
        ↓
Personality Update
```

每一层都有明确职责边界，禁止跨层调用或绕过 Normalizer。

---

## 2. 各阶段详细说明

### 2.1 Legacy Governance Proposal（输入层）

**位置**: `src/growth/proposal/proposal.py::GrowthProposal`

**角色**: 输入兼容层 / 存储兼容层

**状态**: ⚠️ **Deprecated**（Phase 3.6.4 起）

**字段集**（已冻结，不再新增字段）:
- `proposal_id`, `timestamp`, `proposal_type`, `status`
- `source`, `source_event_id`, `user_id`
- `affected_dimensions`, `before_state`, `after_state`
- `confidence`, `reason`, `evidence`
- `priority`, `reviewer_id`, `review_comment`, `reviewed_at`
- `applied_at`, `applied_by`, `expires_at`
- `metadata`

**使用约束**:
- ✅ 允许 import: `src/growth/proposal/storage.py`、`src/growth/proposal/reviewer.py`、`src/growth/proposal/__init__.py`、`src/admin/governance_provider.py`、`src/runtime/adapters/growth_proposal_adapter.py`
- ❌ 禁止 import: consumer / personality_adapter / approval_manager / runtime 主链路
- ⚠️ Phase 3.6.5 起冻结字段集合（不再新增字段，包括 `schema_version`）

---

### 2.2 GrowthProposalNormalizer（归一化层）

**位置**: `src/contracts/proposal_normalizer.py::GrowthProposalNormalizer`

**角色**: 双 Schema 转换层（lossless）

**职责**:
- `detect(proposal)` → `"canonical" | "governance" | "unknown"`
- `normalize_to_canonical(proposal)` → canonical dict
- `to_governance_view(proposal)` → governance dict

**核心原则**:
- 纯函数（不修改原 dict）
- 防御优先（None / {} / 未知 schema / 类型错误 / 空列表 / 缺 confidence 全部安全处理）
- 不抛异常（任何失败都返回安全默认值）
- 不依赖 Runtime / Orchestrator / Growth / Personality
- 0 副作用
- 字段保留：B → A 时把 B 的原字段打包到 `evaluator_meta._governance_origin`

**禁止修改**: 核心转换规则（Phase 3.6.2 起冻结）

---

### 2.3 Canonical GrowthProposal（唯一内部标准）

**位置**: `src/contracts/growth_schema.py::GrowthProposal`

**角色**: **唯一内部标准（Source of Truth）**

**状态**: ✅ **Active**

**字段契约（v1.0 — Phase 3.6.5 冻结）**:
- `id`                       str              # 主键（唯一）
- `source_event_id`          str|None         # 触发事件
- `proposed_changes`         List[ChangeItem] # 提议的状态变更
- `confidence`               float            # 置信度 [0, 1]
- `evidence_ids`             List[str]        # 证据 id 列表
- `evaluator_meta`           Dict[str, Any]   # 评估器元数据
- `timestamp`                str              # ISO 8601 with Z
- `status`                   str              # proposed / accepted / rejected / cancelled / expired
- `accepted_at`              str|None         # 接受时间
- `rejected_at`              str|None         # 拒绝时间
- `schema_version`           str              # schema 版本（Phase 3.6.5 引入，默认 `"1.0"`）

**核心约束**:
- 所有字段为**追加式**（不允许删除或重命名已有字段）
- 字段名 / 字段类型 / 字段顺序锁定
- 字段语义锁定

**Schema Version 策略**:
- `schema_version` 字段由 `CANONICAL_SCHEMA_VERSION = "1.0"` 常量控制
- 新建实例：自动填 `"1.0"`
- 旧数据反序列化：缺省 `schema_version` 时回退到 `"1.0"`（视为兼容 v1.0）
- 后续版本演进：仅追加字段 + 提升 `schema_version`（如 v1.1, v2.0）

---

### 2.4 GrowthEvaluator（评估层）

**位置**: `src/growth/growth_evaluator.py`

**角色**: 评估 canonical GrowthProposal 的可执行性

**职责**:
- 输入: canonical dict
- 评估: confidence / risk / boundary / 优先级
- 输出: 已评估的 canonical dict（可能调整 status / evaluator_meta）

**约束**:
- 仅接受 canonical dict（不接受 legacy dict）
- 不修改 Personality / Memory / Emotion 核心状态
- 评估结果通过 canonical dict 的 `evaluator_meta` 透传

---

### 2.5 GrowthProposalTranslator（PCR 翻译层）

**位置**: `src/admin/selfmodel_consumer.py::GrowthProposalTranslator`（已 deprecated） / `src/admin/normalized_proposal_translator.py::NormalizedProposalTranslator`（推荐）

**角色**: canonical dict → PCR dict

**职责**:
- 输入: canonical dict
- 翻译: trait_changes / growth_records / evolution_record
- 输出: PCR dict

**核心约束**:
- 仅处理 canonical dict（输入侧已通过 Normalizer 归一化）
- 双分支处理（legacy / canonical）已收敛到 Normalizer
- PCR 字段保持稳定（向后兼容）

**入口选择**:
- ✅ 新业务请使用 `NormalizedProposalTranslator.translate()`（Phase 3.6.3 起的标准入口）
- ⚠️ 旧 `GrowthProposalTranslator` 已 deprecated（仍兼容）

---

### 2.6 Personality Change Request（PCR）

**位置**: PCR dict（无独立 dataclass，结构由 PersonalityAdapter 定义）

**角色**: SelfModel 消费契约

**结构**:
```python
{
    "request_id": str,
    "source_proposal_id": str,
    "source_insight_id": str|None,
    "evolution_record": {
        "record_id": str,
        "trait_changes": Dict[str, {"delta": float, "before": float}],
        "confidence": float,
        ...
    },
    "growth_records": List[dict],
    "confidence": float,
    "evidence_count": int,
    "evaluator_meta": Dict[str, Any],
    "reason": str,
}
```

---

### 2.7 Personality Update（最终应用）

**位置**: `src/personality/personality_adapter.py::apply_pcr()` / `src/runtime/runtime_core.py`

**角色**: 将 PCR 落到 Personality trait 状态

**职责**:
- 输入: PCR dict
- 校验: path 白名单 / delta 限幅 / 冲突检测
- 应用: 更新 trait state
- 持久化: JSONL（beliefs / history / reflection）

**约束**:
- 仅接受 PCR dict
- 不接受 legacy / canonical GrowthProposal（已通过 Translator 翻译）
- 应用记录可审计

---

## 3. Schema Migration Policy（Phase 3.6.5 冻结）

### 3.1 Legacy Schema

| 维度 | 策略 |
|------|------|
| 状态 | ⚠️ **Deprecated**（Phase 3.6.4 起） |
| 角色 | 仅作输入兼容层 / 存储兼容层 |
| 业务依赖 | ❌ 禁止新的业务逻辑直接依赖 |
| 字段演进 | 🔒 **冻结**（Phase 3.6.5 起不再新增字段，包括 `schema_version`） |
| 兼容期 | 长期保留（直到 Runtime 链路 100% 切到 canonical） |
| 迁移路径 | `normalize_to_canonical()` → canonical dict |

### 3.2 Canonical Schema

| 维度 | 策略 |
|------|------|
| 状态 | ✅ **Active** |
| 角色 | 唯一内部标准（Source of Truth） |
| 业务依赖 | ✅ 所有新业务逻辑必须使用 |
| 字段演进 | 🔒 **冻结字段集合**（仅允许追加，schema_version 提升） |
| 版本号 | `schema_version = "1.0"` |
| 兼容期 | 永久 |
| 迁移路径 | 业务方应直接 import canonical |

### 3.3 Import Direction（Phase 3.6.4 锁定）

```
contracts (canonical + normalizer)
   ↓
admin (governance provider + translator)
   ↓
adapter (双 schema 转换器)
   ↓
consumer / runtime 主链路
```

**禁止**:
- consumer → legacy schema（必须通过 Normalizer）
- personality_adapter → legacy schema（必须通过 NormalizedProposalTranslator）

**允许**:
- contracts → admin → adapter → consumer（依赖方向单向）
- storage / reviewer / governance_provider（producer / storage 兼容层）

---

## 4. Schema Version 演进规则（Phase 3.6.5 冻结）

### 4.1 兼容性原则

- **Patch 版本**（v1.0 → v1.1）: 字段追加，不破坏旧数据
- **Minor 版本**（v1.x → v2.0）: 字段语义变更或类型变更，旧数据反序列化时需迁移
- **Major 版本**（v2.x → v3.0）: 字段重命名或删除，需提前公告

### 4.2 当前版本: v1.0

- 字段集合: 11 个核心字段（含 `schema_version`）
- 兼容性: 旧 v0.x 数据（无 `schema_version`）反序列化时回退到 v1.0
- 演进策略: 仅允许追加字段

### 4.3 后续版本计划

- v1.1（计划）: 追加 `origin_proposal_id`（跨链路追踪）
- v2.0（远期）: 拆分 `evaluator_meta` 为子结构

---

## 5. 验证矩阵

| 检查项 | 命令 | 期望 |
|--------|------|------|
| canonical schema version 默认存在 | `pytest tests/test_phase_3_6_5_schema_freeze.py` | 通过 |
| legacy schema 仍可 normalize | 同上 | 通过 |
| round-trip 不丢字段 | 同上 | 通过 |
| consumer 不 import legacy schema | 同上 | 通过 |
| runtime/personality 不依赖 legacy schema | 同上 | 通过 |
| migration policy 文档存在 | `ls docs/growth_proposal_lifecycle.md` | 存在 |
| Phase 3.6.x 全量测试 | `pytest tests/test_phase_3_6_*.py` | 全通过 |

---

## 6. Phase 3.6.x 累计交付

| Phase | 范围 | 测试数 |
|-------|------|--------|
| 3.6.1 | Schema B 标识 | - |
| 3.6.2 | GrowthProposalNormalizer（双 Schema 转换） | 56 |
| 3.6.3 | 主链路接入（NormalizedProposalTranslator） | 33 |
| 3.6.4 | Schema Governance Cleanup | 48 |
| **3.6.5** | **Schema Final Audit / Freeze** | **+** |
| **累计** | **canonical + governance 全栈治理** | **137+** |

---

## 7. 进入 Runtime Integration 的前置条件

✅ Phase 3.6.5 schema 已冻结
✅ canonical schema 是唯一内部标准
✅ legacy schema 已 deprecated（仅作输入兼容层）
✅ Normalizer 是唯一归一化层（lossless）
✅ NormalizedProposalTranslator 是业务层统一入口
✅ import 方向已约束（contracts → admin → adapter → consumer）
✅ 字段完整性在主链路保留
✅ schema_version 字段已就位（v1.0）
✅ 文档已落地（`docs/growth_proposal_lifecycle.md`）

**可以进入 Runtime Integration 阶段。**
