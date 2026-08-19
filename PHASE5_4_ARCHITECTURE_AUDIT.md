# Phase 5.4 — Runtime Governance 架构审计报告

> 审计时间：2026-07-30
> 审计范围：Phase 1-5.3 全链路（Orchestrator → Memory → Emotion → Response → GrowthPipeline → GrowthProposal → ProposalStorage → ApprovalManager → PersonalityAdapter → RuntimeCore）
> 状态：**审计完成，发现关键架构一致性问题**（详见第 6 节）
> 强约束：审计阶段**不修改任何业务代码**

---

## 1. 审计目标

在不新增大型模块、不改变 RuntimeCore 权威、不破坏现有架构的前提下，对完整生命周期进行稳定性验证，并识别需要修复的架构一致性问题。

---

## 2. 当前实际数据流（按调用链）

### 2.1 用户消息主链路

```
User Message
  ↓
api_server.py (Flask)
  ↓
Orchestrator.handle_message()  (src/orchestrator.py)
  ↓
  ├─ Memory Retrieval (memory_store.search)         ← 已实现
  ├─ Relationship Evaluator                          ← 已实现
  ├─ Emotion Analysis (emotion_engine)               ← 已实现
  ├─ Self Model Context Provider                     ← 已实现
  ├─ Personality Resolver (read)                     ← 已实现
  ├─ Response Engine (engine.py)                     ← 已实现
  ├─ Memory Save (memory_store.add)                  ← 已实现
  ├─ EventBus.publish (events.bus)                   ← 已实现
  └─ Hooks: orchestrator_hooks                       ← 已实现
```

### 2.2 Runtime 集成链路

```
Orchestrator.__init__()
  ↓
  ├─ try: get_runtime_bridge().get_memory_store()    ← RuntimeCore 共享单例
  ├─ try: get_runtime_bridge().get_personality_resolver()
  ├─ try: get_runtime_bridge().get_emotion_manager()
  └─ try: get_runtime_bridge().get_growth_state()
  
  Fallback（如 RuntimeBridge 不可用）：
  ├─ self.memory_store = MemoryStore(...)
  ├─ self.personality_resolver = PersonalityResolver(...)
  └─ ...

RuntimeBridge.get_instance()                            ← 单例
  ↓
RuntimeCore                                            ← 持有所有 Authority
  ├─ MemoryStore（single source of truth）
  ├─ VectorMemory
  ├─ PersonalityResolver
  ├─ EmotionManager
  ├─ SelfModelStore
  ├─ GrowthState
  └─ MemoryGrowthAdapter
```

### 2.3 Growth 提案链路

```
Orchestrator (in hook)
  ↓
GrowthPipeline (src/growth/pipeline.py)              ← 已实现
  ↓
  ├─ EventExtractor
  ├─ EventNormalizer
  ├─ EventValidator
  ├─ EventHistoryMatcher
  └─ GrowthEngine (writes to GrowthState)            ← RuntimeCore 共享
      ↓
      → 返回 personality_influences（list）
```

**注意：GrowthPipeline 并不直接生成 GrowthProposal** —— 它只更新 GrowthState 和返回 influences。Proposal 的实际生成在：

```
src/personality/personality_evolution_pipeline.py
  ↓
src/runtime/adapters/growth_adapter.py::GrowthAdapter
  ↓
  uses: src/contracts/growth_schema.py::GrowthProposal (类型B)
  ↓
src/runtime/adapters/proposal_evaluator.py::ProposalEvaluator
  ↓
src/personality/personality_adapter.py::PersonalityAdapter
  ↓
PersonalityResolver.apply()                          ← RuntimeCore 共享
```

### 2.4 Admin 治理链路（Phase 5.3）

```
Admin UI (Browser)
  ↓ fetch
Flask routes: /admin/api/admin/governance/*
  ↓
GovernanceProvider (src/admin/governance_provider.py)
  ↓
  ├─ read: RuntimeProvider → RuntimeBridge → RuntimeCore  (只读)
  └─ write: ProposalStorage                              (受控写入)
      ↓
      uses: src/growth/proposal/proposal.py::GrowthProposal (类型A)
      ↓
      data/growth/proposals/proposals.json
```

---

## 3. 已实现节点清单

| 节点 | 位置 | 状态 | 备注 |
| --- | --- | --- | --- |
| User Message 入口 | `api_server.py` | ✅ | Flask 路由 |
| Orchestrator 调度 | `src/orchestrator.py` | ✅ | 完整 handle_message |
| Memory Retrieval | `src/memory/` | ✅ | 优先 RuntimeBridge |
| Emotion Analysis | `src/emotion/` | ✅ | emotion_engine |
| Response Generation | `src/engine.py` | ✅ | ResponseEngine |
| Memory Save | `src/memory/memory_store.py` | ✅ | add() |
| EventBus | `src/events/bus.py` | ✅ | 单例 |
| RuntimeBridge | `src/runtime/runtime_bridge.py` | ✅ | 单例 |
| RuntimeCore | `src/runtime/runtime_core.py` | ✅ | Authority 持有者 |
| RuntimeProvider | `src/admin/runtime_provider.py` | ✅ | Phase 5.1 |
| Admin Dashboard | `static/admin/index.html` | ✅ | Phase 5.2 |
| GovernanceProvider | `src/admin/governance_provider.py` | ✅ | Phase 5.3 |
| ProposalStorage | `src/growth/proposal/storage.py` | ✅ | 类型A Proposal |
| ProposalReviewer | `src/growth/proposal/reviewer.py` | ✅ | 类型A Proposal |
| GrowthPipeline | `src/growth/pipeline.py` | ✅ | influences |
| GrowthAdapter | `src/runtime/adapters/growth_adapter.py` | ✅ | 类型B Proposal |
| ProposalEvaluator | `src/runtime/adapters/proposal_evaluator.py` | ✅ | 类型B Proposal |
| PersonalityAdapter | `src/personality/personality_adapter.py` | ✅ | 类型B Proposal |
| PersonalityEvolutionPipeline | `src/personality/personality_evolution_pipeline.py` | ✅ | 类型B Proposal |
| ApprovalManager | `src/growth/approval_manager.py` | ✅ | 类型B Proposal |
| AuditLogger | `src/admin/core/audit.py` | ✅ | 单例 |

---

## 4. 缺失节点清单

| 节点 | 描述 | 严重性 | 建议 |
| --- | --- | --- | --- |
| Proposal 跨 Schema 适配 | 类型A (proposal.py) 与类型B (growth_schema.py) 之间**无**转换器 | ⚠️ **高** | 详见第 6 节 |
| ApprovalManager ↔ ProposalStorage 联通 | ApprovalManager 仅处理类型B，无法处理类型A | ⚠️ **高** | 详见第 6 节 |
| PersonalityAdapter ↔ ProposalStorage 联通 | PersonalityAdapter 不知道 Admin 创建的 Proposal 存在 | ⚠️ **高** | 详见第 6 节 |
| 治理事件统一 Audit 事件名 | 当前 `governance.*` 事件名未注册到标准 AuditLogger 事件类型 | 🟡 中 | Phase 5.4 Step 5 修复 |
| Runtime 端 Audit 钩子 | RuntimeCore 内的 personality/memory 变更**不**进入 Admin AuditLogger | 🟡 中 | Phase 5.4 Step 5 修复 |

---

## 5. 单例一致性检查

### 5.1 Authority 单例（已对齐）

| 实例 | 持有者 | 验证方式 |
| --- | --- | --- |
| MemoryStore | RuntimeCore | `test_admin_runtime_integration.py` 验证 RuntimeProvider 引用 = RuntimeCore 引用 |
| PersonalityResolver | RuntimeCore | 同上 |
| EmotionManager | RuntimeCore | 同上 |
| GrowthState | RuntimeCore | 同上 |
| SelfModelStore | RuntimeCore | 同上 |
| VectorMemory | RuntimeCore | 同上 |

**结论**：Runtime 单例权威链路完整（Phase 4.x 收尾 + Phase 5.1 验证）。

### 5.2 Proposal 存储单例（不一致 ⚠️）

**发现两个独立的 Proposal 存储路径**：

| 存储 | Schema | 写入方 | 读取方 |
| --- | --- | --- | --- |
| `data/growth/proposals/proposals.json` | 类型A (`proposal.py`) | GovernanceProvider, ProposalReviewer, Orchestrator | ProposalStorage |
| `data/proposals/growth_proposals.json` | 类型B (`growth_schema.py`) | GrowthAdapter, PersonalityEvolutionPipeline | ApprovalManager, PersonalityAdapter, ProposalEvaluator |

**两个存储文件不同！两个 Schema 类型不同！两套消费者不交叉！**

### 5.3 AuditLogger 单例（已对齐）

- `src/admin/core/audit.py::AuditLogger` 单例
- 通过 `AuditLogger.get_instance()` 获取
- 现有治理层已正确注入

---

## 6. 关键发现：Proposal Schema 断裂（核心问题）

### 6.1 问题描述

系统中存在 **两个不同的 `GrowthProposal` 数据类**：

#### 类型 A：`src/growth/proposal/proposal.py::GrowthProposal`
```python
@dataclass
class GrowthProposal:
    proposal_id: str              # ← 注意：proposal_id
    timestamp: str
    proposal_type: str            # personality / identity / ...
    status: str                   # pending / approved / rejected / applied
    source: str
    source_event_id: str
    user_id: str
    affected_dimensions: Dict[str, float]    # 直接 delta
    before_state: Dict[str, float]
    after_state: Dict[str, float]
    confidence: float
    reason: str
    evidence: List[str]
    priority: str
    reviewer_id: str
    review_comment: str
    reviewed_at: Optional[str]
    applied_at: Optional[str]
    applied_by: str
    expires_at: Optional[str]
    metadata: Dict[str, Any]
```

#### 类型 B：`src/contracts/growth_schema.py::GrowthProposal`
```python
@dataclass
class GrowthProposal:
    id: str                       # ← 注意：id
    source_event_id: Optional[str]
    proposed_changes: List[ChangeItem]   # ← ChangeItem 列表（不同结构）
    confidence: float
    evidence_ids: List[str]             # ← evidence_ids
    evaluator_meta: Dict[str, Any]
    timestamp: str
    status: str                   # proposed / approved / rejected
    accepted_at: Optional[str]
    rejected_at: Optional[str]
```

### 6.2 不兼容性

| 字段 | 类型 A | 类型 B | 兼容？ |
| --- | --- | --- | --- |
| 主键 | `proposal_id` | `id` | ❌ 字段名不同 |
| 状态值 | `pending/approved/rejected/applied` | `proposed/approved/rejected` | ❌ 状态机不统一 |
| 变更 | `affected_dimensions` + `before/after_state` | `proposed_changes: List[ChangeItem]` | ❌ 结构不同 |
| 证据 | `evidence: List[str]` | `evidence_ids: List[str]` | ❌ 字段名不同 |
| 优先级 | `priority: str` | ❌ 不存在 | ❌ 字段缺失 |
| 审批 | `reviewer_id/review_comment/reviewed_at` | ❌ 不存在 | ❌ 字段缺失 |
| 应用 | `applied_at/applied_by` | `accepted_at` | ❌ 字段名不同 |
| 元数据 | `metadata: Dict` | `evaluator_meta: Dict` | ⚠️ 名称不同但语义相似 |

### 6.3 实际影响

**Admin Governance 创建的 Proposal（类型A）永远不会被 ApprovalManager 或 PersonalityAdapter 处理**（它们只处理类型B）：

```
Admin POST /admin/api/admin/governance/personality/propose
  ↓
GovernanceProvider 写入 ProposalStorage
  ↓  (类型A，data/growth/proposals/proposals.json)
  
... 但是 ...

ApprovalManager.approve_proposal()  ← 只读 类型B
PersonalityAdapter.apply_proposal() ← 只读 类型B
GrowthAdapter.store_insight()       ← 只写 类型B

→ Admin 创建的 Proposal 永远不会 apply 到 PersonalityResolver！
```

**这意味着 Phase 5.3 创建的 Proposal 在当前架构下是"僵尸数据"**。

### 6.4 严重性评估

- **数据一致性**：🟡 中（Admin Proposal 与 Growth Proposal 数据隔离，无冲突）
- **业务正确性**：⚠️ **高**（Admin 期望"提交成长建议 → 真正影响人格"，但实际永远 apply 不了）
- **架构一致性**：⚠️ **高**（违反"单例权威"原则，存在两条并行的 Proposal 流）

### 6.5 风险列表

1. **R-P5.4-001（高）**：Admin 创建的 Proposal 无法 apply 到 PersonalityResolver
2. **R-P5.4-002（中）**：Admin 审查的 Proposal 状态变更（approve/reject）只更新了类型A 的 status，对类型B 的实际生命周期无影响
3. **R-P5.4-003（中）**：两个 JSON 存储文件可能不同步，审计时需要交叉对比
4. **R-P5.4-004（低）**：测试时类型A 与类型B 互不可见，导致 Phase 5.3 治理测试与 Phase 3.5.x 审批测试无法覆盖同一业务流

### 6.6 处理策略

**不立即合并**（避免破坏现有 Phase 3.5.x 审批流程）。**采用兼容 Adapter**：

1. **短期（Phase 5.4）**：通过 Phase 3 Step 3 输出 `PROPOSAL_SCHEMA_MIGRATION_PLAN.md`，设计 `GrowthProposalAdapter` 桥接
2. **不删除**任何现有 Schema
3. **不修改** 任何现有 Proposal 消费者
4. **只新增** 适配层，将 GovernanceProvider 的写入**同时镜像**到类型B 存储（或反之）

---

## 7. 潜在状态污染点

### 7.1 Orchestrator Fallback 创建 Authority

`src/orchestrator.py` 第 99+ 行：

```python
# Fallback：RuntimeBridge 不可用时自建（保持向后兼容）
if self.memory_store is None:
    self.memory_store = MemoryStore(...)
```

**风险**：若 RuntimeBridge 临时不可用（启动时序问题），Orchestrator 会**自建**一份新的 MemoryStore，与 RuntimeCore 持有的权威实例**脱钩**。这会导致后续写入落不到 RuntimeCore。

**Phase 5.4 处理建议**：保留 Fallback（向后兼容），但增加警告日志 + 指标监控。

### 7.2 GovernanceProvider 模块级单例

`src/admin/governance_provider.py` 第 778+ 行：

```python
_provider_instance: Optional[GovernanceProvider] = None

def get_governance_provider() -> GovernanceProvider:
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = GovernanceProvider()
    return _provider_instance
```

**风险**：测试中若 `reset_governance_provider_for_testing` 未调用，可能跨测试污染单例。

**当前缓解**：`tests/test_admin_governance.py` 已加 `autouse=True` fixture 自动重置单例。✅

### 7.3 ProposalStorage 全局单例

`src/growth/proposal/storage.py` 中 `get_proposal_storage()` 使用全局单例 + 默认目录 `data/growth/proposals/`。

**风险**：测试间数据污染。

**当前缓解**：测试通过 `ProposalStorage(data_dir=tmp_dir)` 注入临时目录。✅

### 7.4 AuditLogger 单例

`src/admin/core/audit.py` 单例，写入 `data/audit/admin/`。

**风险**：跨进程访问同一文件可能并发问题。

**当前状态**：单进程内 OK（threading.Lock），跨进程无强保证。🟡 中（生产部署单进程，无影响）

---

## 8. 循环依赖风险

### 8.1 检查结果

通过 `grep "from src."` 扫描关键模块的导入关系：

- `runtime_core.py` ← 引用 → `growth/proposal/proposal.py` ⚠️ （间接 import，见 line 1644）
- `runtime_core.py` ← 引用 → `contracts/growth_schema.py`
- `orchestrator.py` ← 引用 → `runtime/runtime_bridge.py` ✅（无循环）
- `admin/governance_provider.py` ← 引用 → `growth/proposal/proposal.py` ✅
- `admin/governance_provider.py` ← 引用 → `admin/runtime_provider.py` ✅

**未发现真正的循环依赖**。RuntimeCore 同时引用两个 Schema 是因为它需要处理两种来源的 Proposal（合法但有异味）。

### 8.2 潜在问题

如果未来要在 RuntimeCore 中**统一**两种 Proposal 调度，可能需要 `runtime/adapters/proposal_evaluator.py` 引用 `growth/proposal/proposal.py`，与现有 `growth/approval_manager.py` 引用 `contracts/growth_schema.py` 形成跨子包依赖。**当前不构成循环，但增长时需注意**。

---

## 9. Proposal 生命周期完整性

### 9.1 类型A（Admin / ProposalStorage）生命周期

```
[创建]   propose_personality_change / propose_memory_action
   ↓
[PENDING]    storage.save()
   ↓ Admin review
   ├─→ APPROVED   (via review_proposal action="approve")
   ├─→ APPROVED   (via review_proposal action="modify")
   └─→ REJECTED   (via review_proposal action="reject")
   ↓ 【当前架构断裂点】
   ⚠️ 无 APPLIED 转换路径！
   ⚠️ ApprovalManager 不识别类型A
   ⚠️ PersonalityAdapter 不识别类型A
```

**结论**：类型A Proposal **停留在 APPROVED 状态**，永远不会进入 APPLIED。

### 9.2 类型B（Growth / ApprovalManager）生命周期

```
[创建]   GrowthAdapter.store_insight(insight)
   ↓
[proposed]   persistence
   ↓ ApprovalManager
   ├─→ approved (via approve_proposal)
   ├─→ approved (via modify_proposal)
   └─→ rejected (via reject_proposal)
   ↓
[accepted/rejected_at set]
   ↓ ProposalEvaluator
   ↓ PersonalityAdapter
[APPLIED]    resolver.apply()
```

**结论**：类型B 生命周期完整，但与 Admin 治理脱钩。

### 9.3 双轨制带来的"治理盲区"

| 来源 | 创建路径 | 审批路径 | Apply 路径 | Admin 可见？ |
| --- | --- | --- | --- | --- |
| 类型A | Admin Governance | Admin review | ❌ 无 | ✅ Admin UI |
| 类型B | Growth 反思 | ApprovalManager | ✅ 完整 | ❌ Admin UI 不可见（除非走 RuntimeProvider） |

**当前 Admin 只能"提交但看不到 apply 效果"** —— 这是 Phase 5.3 已知的局限性，但 Phase 5.4 第一次以架构形式记录。

---

## 10. 总结

### 10.1 已对齐 ✅

- Runtime 单例权威链路（Phase 4.x + Phase 5.1 验证）
- Admin 与 RuntimeCore 的只读桥接
- AuditLogger 单例
- Proposal 状态机定义（虽然两个 schema，但各自完整）

### 10.2 待修复 ⚠️

1. **Proposal Schema 断裂**（详见 6.1-6.5）
   - 影响：Admin 治理创建的 Proposal 不会 apply
   - 短期处理：兼容 Adapter（Phase 5.4 Step 3）
   - 长期处理：统一 Schema（未来 phase）

2. **Orchestrator Fallback 创建 Authority**（详见 7.1）
   - 影响：可能与 RuntimeCore 脱钩
   - 处理：保留但增加警告日志

3. **Audit 事件未统一**（详见 4）
   - 影响：治理事件与 Runtime 事件审计口径不一致
   - 处理：Phase 5.4 Step 5 增加标准事件名

### 10.3 Phase 5.4 处理策略

- **Step 1（已完成）**：本审计报告
- **Step 2**：补齐 E2E 生命周期测试（覆盖双 Schema 现状）
- **Step 3**：输出 Schema 迁移计划（**不立即合并**，只规划）
- **Step 4**：Admin Governance 安全增强（验证 approval 不触发 apply 等）
- **Step 5**：增加标准 Audit 事件名 hook
- **Step 6**：运行完整测试矩阵
- **Step 7**：输出完整报告

---

> **下一步**：进入 Step 2 —— 实现 `tests/test_runtime_lifecycle_e2e.py`
