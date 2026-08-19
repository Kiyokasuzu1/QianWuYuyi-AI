# Phase 6.1 Self Model Integration 架构审核报告

> QianWuYuyi-AI — 架构审核（不写代码）
> 2026-07-30

## 0. 审核结论先行

**结论：Phase 6.1 可行，但存在两处结构性冲突需要先解决。**

1. **`src/personality/self_model.py` 已存在**（legacy dict-style SelfModel），用户要求的"新增"需先确定意图（重写 / 新建并行 / 升级到 V3）。
2. **现有 SelfModel 生态非常丰富但分散**：`self_model_manager.py` (Phase 3.5.8) + `self_model_updater.py` (Phase 3.5.8) + `self_model_v3.py` (Phase 12.2) + `self_model_store.py` + `self_model_schema.py` 已覆盖了用户要求的大部分概念；本次需"整合"而非"重写"。

建议：**以现有 `SelfIdentity` (dataclass) / `SelfModelUpdater` / `SelfModelManager` 为基础**，把用户要求的 4 个类（SelfIdentity / SelfBelief / SelfHistory / SelfReflection）映射为：
- **SelfIdentity** → 复用 `src/contracts/self_model_schema.py::SelfIdentity`（已存在）
- **SelfBelief** → 新建轻量 dataclass（schema 已有 Preference + CoreValue，可重新聚合为 SelfBelief）
- **SelfHistory** → 新建 `SelfModelHistory` 容器（聚合 GrowthHistoryEntry + DevelopmentHistoryItem）
- **SelfReflection** → 新建轻量 `SelfReflectionNote` 容器（与现有 ReflectionInsight 解耦）

`src/personality/self_model.py` 建议**重写**为统一入口模块（re-export + 工厂函数），避免文件冲突。

---

## 1. 现有 SelfModel 生态盘点

### 1.1 已有 SelfModel 相关文件

| 文件 | 关键类 | 状态 | Phase |
|---|---|---|---|
| [self_model.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/self_model.py) | SelfModel (TypedDict), SelfDescriptionSource, GrowthNarrative, PersonalityTension, SelfUnderstanding | **legacy** | 早期 |
| [self_model_schema.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/contracts/self_model_schema.py) | SelfIdentity, CoreValue, StableTrait, Preference, BehavioralPattern, SelfContradiction, GrowthHistoryEntry, DevelopmentHistoryItem, IdentityUnderstanding, SelfModelChangeSuggestion | 较新 | Phase 3.5.8 |
| [self_model_v3.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/self_model_v3.py) | SelfModelV3, NarrativeItem, NarrativeType | 较新 | Phase 12.2 |
| [self_model_manager.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/self_model_manager.py) | SelfModelManager (主逻辑 559 行) | 主逻辑 | Phase 3.5.8 |
| [self_model_updater.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/self_model_updater.py) | SelfModelUpdater, SelfModelUpdaterConfig | 主逻辑 | Phase 3.5.8 |
| [self_model_store.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/self_model_store.py) | SelfModelStore | 适配层 | Phase 3.4+ / V3 在 1.2 |
| [self_model_builder.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/self_model_builder.py) | SelfModelBuilder (V1) | 较老 | Phase 3.4 |
| [self_model_builder_v3.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/self_model_builder_v3.py) | SelfModelBuilderV3 | 较新 | Phase 12.2 |
| [self_model_context_provider.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/self_model_context_provider.py) | SelfModelContextProvider | 读取 | 早期 |
| [self_model_v3.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/self_model_v3.py) | SelfModelV3 | 主数据 | Phase 12.2 |

### 1.2 已有的 SelfModel 测试

| 文件 | 范围 |
|---|---|
| tests/test_self_model.py | SelfModel V1 基础 |
| tests/test_self_model_v2.py | V2 升级测试 |
| tests/test_self_model_v3.py | V3 升级测试 |
| tests/test_self_model_system.py | 系统集成 |
| tests/test_self_model_store.py | Store |
| tests/test_self_model_context.py | ContextProvider |
| tests/test_self_model_behavior.py | 行为影响 |
| tests/test_self_model_influence.py | 影响分析 |
| tests/test_selfmodel_authority.py | Authority 审计 |
| tests/test_self_reflection_engine.py | Self 反思引擎 |
| tests/test_self_narrative.py | 叙事 |
| tests/test_emotion_self_model.py | 情绪-自我模型 |

**结论**：SelfModel 是项目长期重点，不能误伤；Phase 6.1 必须**做加法（新增 + 接入）而非减法**。

---

## 2. 现有 Runtime Growth Pipeline（Phase 6.0）状态

### 2.1 现有 6 阶段

```
EXPERIENCE → MEMORY → REFLECTION → PROPOSAL → LIFECYCLE → PERSONALITY
```

各阶段对 SelfModel 的处理：

| 阶段 | 是否涉及 SelfModel | 说明 |
|---|---|---|
| EXPERIENCE | ❌ | 原始经验 |
| MEMORY | ❌ | 写入记忆 |
| REFLECTION | ⚠️ 间接 | ReflectionInsight 可触发 SelfModelUpdater 间接更新 |
| PROPOSAL | ❌ | 仅 GrowthProposal |
| LIFECYCLE | ❌ | 仅 ProposalLifecycle |
| PERSONALITY | ❌ | 仅 TraitState |

**Pipeline 没有任何阶段**专门处理 SelfModel 变更。

### 2.2 PersonalityAdapter 的当前行为

[personality_adapter.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/personality/personality_adapter.py)：
- `apply_proposal` 走 `TraitStateUpdater.apply(record, trait_states)`
- **完全无 SelfModel 接触**
- `envelope` 不包含 SelfModel 字段
- `ALLOWED_PERSONALITY_PATHS` 不包含 `self_model.*`

### 2.3 现有路径白名单（参考基线）

```python
ALLOWED_PERSONALITY_PATHS = frozenset({
    "self_state.initiative", "self_state.social_need", ...
    "personality.traits.shyness", ...
    "initiative", "social_need", ...
    "开放性", "严谨性", ...
})
```

SelfModel 路径完全不在白名单内 → Phase 6.1 必须新增 `ALLOWED_SELF_MODEL_PATHS`。

---

## 3. 用户要求的 4 个核心类分析

### 3.1 SelfIdentity

**用户要求**：实现 SelfIdentity 类
**现有映射**：`src/contracts/self_model_schema.py::SelfIdentity`（已存在，306-360 行）

```python
@dataclass
class SelfIdentity:
    identity_id: str
    core_values: List[CoreValue]
    stable_traits: List[StableTrait]
    preferences: List[Preference]
    behavioral_patterns: List[BehavioralPattern]
    contradictions: List[SelfContradiction]
    growth_history: List[GrowthHistoryEntry]
    development_history: List[DevelopmentHistoryItem]
    identity_understanding: IdentityUnderstanding
    experience_awareness / trait_awareness / identity_continuity / overall_understanding
    created_at / last_updated / version
```

**判断**：SelfIdentity 已存在且完整，**直接复用**。

### 3.2 SelfBelief

**用户要求**：实现 SelfBelief 类
**现有映射**：间接存在 → `Preference` + `CoreValue` + `BehavioralPattern` + `SelfContradiction`

**判断**：SelfBelief 在概念上是"羽依持有的信念集合"，需要新建轻量 dataclass 包装这些元素 + `confidence` + `sources`。

建议设计：
```python
@dataclass
class SelfBelief:
    belief_id: str
    domain: str                 # value / preference / pattern / contradiction
    content: str                # 信念内容（结构化或自然语言摘要）
    confidence: float           # 信念强度
    sources: List[str]          # 来源 (proposal_id / insight_id / gr_id)
    first_seen: str
    last_confirmed: str
    evidence_count: int
    version: int = 1
```

### 3.3 SelfHistory

**用户要求**：实现 SelfHistory 类
**现有映射**：`GrowthHistoryEntry` + `DevelopmentHistoryItem`

**判断**：需要新建 `SelfHistory` 容器类作为时序日志的统一定义，支持：
- `append(event)` 追加
- `query(trait=, source_type=, since=)` 查询
- `iter_recent(n)` 流式遍历
- `to_snapshot()` / `load_snapshot()`
- 支持回滚（rollback to N）

建议设计：
```python
@dataclass
class SelfHistoryEvent:
    event_id: str
    timestamp: str
    event_type: str              # growth_record / reflection_insight / self_belief_change / rollback
    source_type: str             # personality_change_request / reflection / ...
    source_id: str
    affected_traits: Dict[str, float]
    affected_beliefs: List[str]  # belief_ids
    summary: str
    snapshot_before: Optional[Dict[str, Any]]  # 回滚用
    snapshot_after: Optional[Dict[str, Any]]
    actor: str
    metadata: Dict[str, Any]

class SelfHistory:
    events: List[SelfHistoryEvent]
    def append(event) -> None
    def query(...) -> List[SelfHistoryEvent]
    def latest(n: int) -> List[SelfHistoryEvent]
    def snapshot_at(event_id) -> Optional[Dict[str, Any]]
    def rollback_to(event_id) -> bool
    def to_dict() / load_dict()
```

### 3.4 SelfReflection

**用户要求**：实现 SelfReflection 类
**现有映射**：`ReflectionInsight`（在 experience_schema.py 中），但这是认知反思，不是"自我反思"。

**判断**：SelfReflection 是 SelfModel 自身产生的反思（meta-cognition），与现有 ReflectionInsight 解耦。
建议设计：
```python
@dataclass
class SelfReflectionNote:
    note_id: str
    timestamp: str
    trigger_source: str          # self_belief_change / snapshot_diff / manual
    reflection_type: str         # identity / value / behavior / contradiction
    content: str
    related_belief_ids: List[str]
    related_trait_changes: Dict[str, float]
    confidence: float
    sources: List[str]
```

注：SelfReflection **不**直接修改 SelfModel，只记录"自我反思的笔记"，避免循环。

---

## 4. 接入方案设计

### 4.1 闭环流程（满足用户硬约束）

```
Experience
   ↓
Memory
   ↓
Reflection
   ↓
GrowthProposal
   ↓
ApprovalManager
   ↓          ├─ 已通过：进入 PersonalityAdapter
   ↓          └─ 拒绝：归档，不产生 SelfModel 影响
PersonalityAdapter
   ↓          ├─ Path 白名单 (ALLOWED_PERSONALITY_PATHS + ALLOWED_SELF_MODEL_PATHS)
   ↓          ├─ GrowthRateLimiter (Phase 6.0)
   ↓          └─ apply_proposal
                       ↓
                  TraitState（人格）
                       ↓
                  PersonalityChangeRequest
                       ↓
                  SelfModelUpdater.from_pcr(pcr)
                       ↓
                  SelfModelChangeSuggestion(s)
                       ↓
                  SelfModelAuthorityGate
                  ├─ path 验证 (ALLOWED_SELF_MODEL_PATHS)
                  ├─ confidence 阈值
                  └─ approval 状态校验
                       ↓
                  apply_suggestion → SelfModelManager
                       ↓
                  SelfHistory.append(event)
                       ↓
                  SelfIdentity（自我模型）
                       ↓
                  SelfReflectionNote（自我反思）
```

### 4.2 SelfModel 修改入口（唯一）

**SelfModelAdapter**（新增，`src/personality/self_model_adapter.py`）：
- 唯一允许修改 SelfModelStore 的入口
- 接收 `PersonalityChangeRequest` 或 `SelfModelChangeSuggestion`
- 内部调用 `SelfModelUpdater` + `SelfModelManager.apply_suggestion`
- 完整审计 + 路径白名单 + 异常隔离

**PersonalityAdapter 与 SelfModelAdapter 的关系**：

| 维度 | PersonalityAdapter | SelfModelAdapter |
|---|---|---|
| 主管对象 | TraitState | SelfIdentity / SelfModelStore |
| 输入 | GrowthProposal | PersonalityChangeRequest（来自 PersonalityAdapter）|
| 路径白名单 | ALLOWED_PERSONALITY_PATHS | ALLOWED_SELF_MODEL_PATHS |
| 限流 | GrowthRateLimiter | GrowthRateLimiter (复用) |
| 审批 | ApprovalManager | 同 ApprovalManager（pcr 来自审批后）|
| 输出 | EvolutionRecord + GrowthRecord | SelfModelChangeSuggestion.apply → SelfModelManager |
| 审计 | EvolutionRecord.id | SelfHistoryEvent.event_id |

### 4.3 SelfModel 路径白名单（新增）

```python
ALLOWED_SELF_MODEL_PATHS: frozenset = frozenset({
    # 核心身份
    "self_model.core_value.{value_id}.weight",      # 权重调整
    "self_model.core_value.{value_id}.confidence",  # 置信度
    # 稳定特质
    "self_model.stable_trait.{trait}.current_value",
    "self_model.stable_trait.{trait}.confidence",
    "self_model.stable_trait.{trait}.stability",
    # 偏好
    "self_model.preference.{domain}.{key}.value",
    "self_model.preference.{domain}.{key}.confidence",
    # 行为模式
    "self_model.behavioral_pattern.{pattern_id}.frequency",
    "self_model.behavioral_pattern.{pattern_id}.confidence",
    # 自我理解水平
    "self_model.understanding.experience_awareness",
    "self_model.understanding.trait_awareness",
    "self_model.understanding.identity_continuity",
    "self_model.understanding.overall_understanding",
})
```

注：白名单使用 `.{var}.` 通配符，运行时解析。

### 4.4 Pipeline 阶段扩展方案（两种）

#### 方案 A：新增第 7 阶段 `SELF_MODEL`

```
EXPERIENCE → MEMORY → REFLECTION → PROPOSAL → LIFECYCLE → PERSONALITY → SELF_MODEL
```

优点：清晰可见
缺点：现有 6 阶段测试需要更新；`PipelineStage` 枚举扩展

#### 方案 B：作为 PERSONALITY 阶段内的子步骤

`step_personality` 内部：
1. TraitState.apply
2. **SelfModelAdapter.apply_pcr** (新)

优点：不动枚举
缺点：阶段混合

**建议**：方案 B，保持 Pipeline 结构稳定。SelfModel 修改在 step_personality 末尾，作为"人格修改的连锁反应"。

### 4.5 接入位置决策矩阵

| 修改点 | 是否接入 | 说明 |
|---|---|---|
| RuntimeGrowthPipeline.run_cycle | ✅ 末尾追加 SelfModel 步骤 | step_personality 内部 |
| PersonalityAdapter.apply_proposal | ✅ 末尾调用 SelfModelAdapter | 回调形式 |
| SelfModelAdapter | ✅ 新建 | 唯一 SelfModel 写入口 |
| SelfModelStore.update | ⚠️ 加保护 | 调用前需经过 SelfModelAdapter |
| SelfModelManager.apply_suggestion | ⚠️ 加保护 | 建议显式参数 `via_self_model_adapter=True` |
| RuntimeCore | ❌ **禁止修改** | 满足硬约束 |
| 直接 SelfModelStore 操作 | ❌ **禁止** | 所有修改必须经 SelfModelAdapter |

---

## 5. Snapshot / History / 回滚架构

### 5.1 SelfModelSnapshot（新增）

**目的**：保存 SelfModel 在某个时间点的完整状态。

```python
@dataclass
class SelfModelSnapshot:
    snapshot_id: str
    timestamp: str
    self_identity: Dict[str, Any]   # SelfIdentity.to_dict()
    self_beliefs: List[Dict[str, Any]]
    self_history_event_count: int
    self_understanding: Dict[str, float]
    version: int
    trigger_event_id: Optional[str]  # 触发此快照的事件
    note: str

class SelfModelSnapshotStore:
    snapshots: List[SelfModelSnapshot]   # 内存
    _path: Path                          # 持久化
    def add(snapshot) -> None
    def get(snapshot_id) -> Optional[SelfModelSnapshot]
    def latest(n=10) -> List[SelfModelSnapshot]
    def rollback_to(snapshot_id) -> bool
    def prune(max_count=200) -> None
```

### 5.2 SelfModelHistory

已在 3.3 设计。`SelfModelHistory` 存储 `SelfHistoryEvent` 列表。

### 5.3 回滚支持

**原则**：回滚必须经过 SelfModelAdapter（避免旁路）。

```python
class SelfModelAdapter:
    def rollback(
        self,
        snapshot_id: str,
        actor: str = "system",
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        回滚 SelfModel 到指定 snapshot。
        1. 读取 snapshot
        2. 校验 snapshot 完整性
        3. 写入 SelfModelStore（经过路径白名单）
        4. 追加 SelfHistoryEvent (event_type=rollback)
        5. 重新计算 understanding
        返回 envelope。
        """
```

**回滚触发方式**：
- 手动：admin panel API
- 自动：GrowthRateLimiter 检测到连续 DENY 后触发
- 自动：SelfModel 校验失败后回滚到上一个 stable snapshot

**回滚与 Audit 关系**：
- ApprovalManager 不涉及（回滚是 SelfModel 内部操作）
- 但回滚事件必须写入 SelfHistory（用于审计）
- 写一份 `RollbackEvent` 写入 `audit/storage.py`（可选）

### 5.4 Snapshot 触发时机

| 触发点 | 说明 |
|---|---|
| SelfModelAdapter.apply_pcr 成功后 | 默认 |
| 手动 `snapshot()` | admin / 测试 |
| GrowthRateLimiter 连续 DENY | 自动保护 |
| ApprovalManager.reject_proposal 后 | 保护性快照（可选）|
| 每天 0:00 | 定时（可选） |

---

## 6. SelfModel ↔ Personality 数据流

### 6.1 方向：Personality → SelfModel

```
GrowthProposal (approved)
   ↓
PersonalityAdapter.apply_proposal
   ↓
TraitState 改变
   ↓
PersonalityChangeRequest (pcr)
   ↓
SelfModelAdapter.apply_pcr(pcr)
   ↓
SelfModelUpdater.from_pcr
   ↓
SelfModelChangeSuggestion
   ↓
SelfModelManager.apply_suggestion
   ↓
SelfIdentity 更新
   ↓
SelfHistoryEvent 追加
   ↓
SelfSnapshot
```

### 6.2 方向：SelfModel → Personality（只读）

PersonalityResolver.get_active_self_model() 已存在，**复用**。

### 6.3 不允许方向

- SelfModel → TraitState（SelfModel 不得反向修改人格参数）
- SelfModel → GrowthState（SelfModel 不得影响运行时状态）
- SelfModel → EmotionState（除非 Emotion 显式订阅 SelfModel 变化）

---

## 7. Phase 5.5.1 / 5.5.2 / 6.0 兼容性影响

| 模块 | 影响 | 处理 |
|---|---|---|
| PersonalityAdapter（Phase 5.5.1+6.0） | 末尾追加 SelfModel 回调（可选） | 通过 `self_model_adapter` 参数注入，向后兼容 |
| ApprovalManager（Phase 5.5.2） | apply_hook 可包含 SelfModel 步骤 | 现有 apply_hook 行为不变，新 hook 可加 SelfModel 子步骤 |
| LifecycleManager（Phase 5.5.2） | 无影响 | SelfModel 修改发生在 lifecycle=applied 之后 |
| GrowthRateLimiter（Phase 5.5.2） | 复用，无需新增 | SelfModelAdapter 内部可调用同一 limiter |
| RuntimeGrowthPipeline（Phase 6.0） | step_personality 末尾追加 SelfModel 子步骤 | 步骤结构不变 |
| RuntimeCore | **零修改** | 满足硬约束 |

**关键点**：所有新组件通过**参数注入**（不修改现有类签名），保持向后兼容。

---

## 8. 测试策略（≥50 个）

### 8.1 测试分布

| 测试组 | 数量 | 范围 |
|---|---|---|
| TestSelfIdentity | 8 | dataclass + to_dict / from_dict + IdentityUnderstanding |
| TestSelfBelief | 6 | CRUD + confidence 累加 + sources 合并 |
| TestSelfHistory | 8 | append / query / latest / rollback event |
| TestSelfReflection | 6 | 笔记生成 + 不修改 SelfModel + 关联 belief |
| TestSelfModelAdapter | 10 | apply_pcr / path 白名单 / limiter / 异常隔离 |
| TestSelfModelSnapshot | 6 | snapshot 创建 / rollback / prune |
| TestPhase61PipelineIntegration | 10 | Pipeline 6.1 集成（端到端）|
| TestPhase61RegressionSafety | 6 | Phase 5.5.1 / 5.5.2 / 6.0 兼容性 |
| **合计** | **60** | ≥ 50 |

### 8.2 关键测试点

- ✅ SelfModelAdapter 是 SelfModelStore 唯一写入口
- ✅ 路径白名单拒绝 self_model.invalid.path
- ✅ 低 confidence proposal 触发 SelfBelief 但不修改 StableTrait
- ✅ SelfHistory 记录所有 SelfModel 变化
- ✅ Rollback 恢复完整状态
- ✅ Pipeline step_personality 末尾触发 SelfModel
- ✅ 与 Phase 5.5.1 / 5.5.2 / 6.0 兼容
- ✅ RuntimeCore 零修改验证
- ✅ SelfModel 异常隔离（不影响 Pipeline 后续）

---

## 9. 文件修改清单（计划）

### 9.1 新增文件

| 路径 | 行数预估 | 职责 |
|---|---|---|
| `src/personality/self_model.py`（重写） | ~120 | 4 核心类 + 工厂 + re-export |
| `src/personality/self_belief.py` | ~150 | SelfBelief dataclass + 容器 |
| `src/personality/self_history.py` | ~200 | SelfHistoryEvent + SelfHistory 容器 |
| `src/personality/self_reflection.py` | ~120 | SelfReflectionNote + 容器 |
| `src/personality/self_model_adapter.py` | ~300 | 唯一 SelfModel 写入口 |
| `src/personality/self_model_snapshot.py` | ~180 | SelfModelSnapshot + SnapshotStore |
| `src/runtime/pipeline/runtime_growth_pipeline_v61.py` 或 修改现有 | ~+80 | 扩展 step_personality |
| `tests/test_phase_6_1_self_model_integration.py` | ~1200 | 60 个测试 |
| `scripts/check_phase_6_1_ast.py` | ~150 | AST + Authority 验证 |

### 9.2 修改文件

| 文件 | 变更点 | 行数增量 |
|---|---|---|
| `src/personality/personality_adapter.py` | `apply_proposal` 末尾调用 `self_model_adapter.apply_pcr()` | +20 |
| `src/runtime/pipeline/runtime_growth_pipeline.py` | `__init__` 新增 `self_model_adapter` 参数；`step_personality` 末尾追加 | +30 |
| `src/personality/__init__.py` | 导出新类 | +10 |

### 9.3 未修改文件

- `src/runtime/runtime_core.py`（硬约束）
- `src/growth/approval_manager.py`（不修改）
- `src/growth/lifecycle_manager.py`（不修改）
- `src/growth/growth_limiter.py`（复用）
- 所有 Phase 5.5.1 / 5.5.2 / 6.0 已有测试

---

## 10. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| self_model.py 文件已存在 | 中 | 明确为"重写"，加入 deprecation 警告；保留旧类 1 个 phase 后再删 |
| SelfModelManager / SelfModelUpdater 已重 | 中 | 新组件作为"调用方"，不替换内部逻辑 |
| SelfModel 修改入口分散 | 高 | 强制所有写路径经 SelfModelAdapter；AST 检查 |
| 路径白名单通配符匹配误用 | 中 | 单元测试覆盖；明确 whitelist 解析规则 |
| 回滚导致状态不一致 | 中 | rollback 必须读取完整 snapshot，校验后原子写 |
| Pipeline step_personality 阻塞 | 低 | SelfModel 异常隔离为 stage.warning，不影响 Pipeline 继续 |
| 与 Phase 12.2 V3 冲突 | 低 | SelfModelAdapter 可选 v3_compat 标志；默认走 Phase 3.5.8 schema |
| 测试 mock 复杂 | 中 | 使用临时 JSON 文件 + MagicMock 组合 |

---

## 11. 实施步骤建议

### Step 1：核心数据结构（无运行时副作用）
1. 新建 `src/personality/self_belief.py`
2. 新建 `src/personality/self_history.py`
3. 新建 `src/personality/self_reflection.py`
4. 重写 `src/personality/self_model.py`（4 类 + re-export）

### Step 2：Snapshot 基础设施
1. 新建 `src/personality/self_model_snapshot.py`
2. 单元测试 SelfModelSnapshot / SnapshotStore

### Step 3：SelfModelAdapter（关键）
1. 新建 `src/personality/self_model_adapter.py`
2. 路径白名单 + Limiter 集成 + SelfModelUpdater 调用
3. 单元测试 apply_pcr / rollback / 异常隔离

### Step 4：Pipeline 集成
1. 修改 `src/runtime/pipeline/runtime_growth_pipeline.py`：新增 `self_model_adapter` 参数
2. 修改 `step_personality` 末尾追加 SelfModel 子步骤
3. 修改 `src/personality/personality_adapter.py`：可选 `self_model_adapter` 回调

### Step 5：集成测试
1. 新建 `tests/test_phase_6_1_self_model_integration.py`（60 个测试）
2. 验证 Phase 5.5.1 / 5.5.2 / 6.0 全部回归通过

### Step 6：AST 检查 + 报告
1. 新建 `scripts/check_phase_6_1_ast.py`
2. 验证 Authority 边界 + 路径白名单 + RuntimeCore 零修改
3. 输出架构报告 + 完成度报告

---

## 12. 关键决策点（待用户确认）

1. **`self_model.py` 策略**：重写 / 新建 `self_model_v61.py` / 创建子包 `self_model/`？建议**重写**（保持文件语义一致）
2. **Pipeline 集成方案**：A 第 7 阶段 vs B PERSONALITY 阶段内子步骤？建议 **B**（更稳定）
3. **SelfModel 路径白名单**：使用通配符 `.{var}.` 还是显式列举？建议**通配符 + 显式列举混合**（核心路径显式，可选维度用通配符）
4. **SelfModelAdapter 接收什么**：pcr dict 还是 GrowthProposal？建议**两者都支持**（pcr 优先）
5. **回滚触发**：手动 / 自动？建议**手动 + 限流器自动触发**（保留审批权威）
6. **是否接入 SelfModelV3**：默认 Phase 3.5.8 schema，可选 v3_compat？建议**默认 V3**（统一新组件）

---

## 13. Phase 6.1 实施前置检查清单

- [ ] 确认 `self_model.py` 重写策略（建议选项）
- [ ] 确认 Pipeline 集成方案（建议 B）
- [ ] 确认 SelfModelV3 作为默认数据模型
- [ ] 确认路径白名单通配符语法
- [ ] 确认回滚策略（手动 + 自动）
- [ ] 确认测试数量目标（≥50，建议 60）
- [ ] 确认报告输出位置

**等待用户确认上述 7 项后，再进入实施阶段。**
