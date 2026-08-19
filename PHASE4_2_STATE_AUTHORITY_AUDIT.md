# QianWuYuyi-AI Phase 4.2 Runtime State Authority 审计报告

**审计日期**：2026-07-30
**审计性质**：只读研究，未修改任何代码
**目标**：在 Phase 4.1 基础上确认 RuntimeCore 作为长期状态唯一来源，识别状态实例分裂

---

## 执行摘要

本次审计确认系统存在 **严重的实例分裂与状态权威缺失**：

| 问题类型 | 数量 | 风险等级 |
|---|---|---|
| Personality 独立实例 | **4 处**（Orchestrator / RuntimeCore / GrowthPipeline / PersonalityController） | 高 |
| SelfModelStore 独立实例 | **3 处**（Orchestrator 内部还有双 Store 分裂） | 高 |
| MemoryStore 独立实例 | **8 处**（生产代码） | 中（多数路径相同，存在并发写风险） |
| VectorMemory 独立实例 | **4 处** | 低（共享同一 ChromaDB） |
| RelationshipState 同名异类 | **2 个同名类**（personality v0.6 vs relationship dataclass） | 高 |
| EmotionManager 双实例 | **2 处**（Orchestrator + RuntimeCore 写同一文件） | 高 |
| 死代码 | 3 处（orchestrator_hooks.py / orchestrator.py.backup / _process_relationship_post） | 中 |

**核心结论**：系统设计了完整的"唯一裁判"（MemoryVerifier）和"长期状态中心"（RuntimeCore），但**实际生产中全部被绕过**。所有生产写入路径直接操作底层存储，无统一权威。

---

## 1. Personality 状态来源审计

### 1.1 状态来源清单

| 组件 | 文件 | 持久化路径 | 是否被调用 |
|---|---|---|---|
| **PersonalityResolver** | `src/personality/personality_resolver.py` | 无（内存） | 是 |
| **SelfModelStore** | `src/personality/self_model_store.py` | 无（内存） | 是（但被错误的实例使用） |
| **SelfModelManager** | `src/personality/self_model_manager.py` | 无（内存） | 仅 RuntimeCore 使用 |
| **GrowthEngine** | `src/growth/growth_engine.py` | `data/growth_state.json` | 是 |
| **GrowthPipeline** | `src/growth/pipeline.py` | 通过 GrowthEngine | 是 |
| **PersonalityEvolutionPipeline** | `src/personality/personality_evolution_pipeline.py` | 条件持久化（history_path） | 仅 RuntimeCore |
| **TraitStateUpdater** | `src/personality/trait_state_updater.py` | 无（内存） | 仅通过 EvolutionPipeline |
| **YuyiPersistence.save_self_model** | `src/storage/yuyi_persistence.py:58` | `data/self_model.json` | **死代码，从未调用** |

### 1.2 实例化位置（4 处独立 PersonalityResolver）

```
PersonalityResolver( 出现位置：
├─ src/orchestrator.py:89              # Orchestrator 实例（无参数）
├─ src/runtime/runtime_core.py:384     # RuntimeCore 实例（无参数）
├─ src/growth/pipeline.py:65           # GrowthPipeline 实例（传入 GrowthEngine.state）
└─ src/personality/personality_controller.py:19  # PersonalityController fallback
```

**这 4 个实例完全独立**，各自的 `_trait_states`、`growth_records`、`growth_history` 互不同步。

### 1.3 Orchestrator 内部的双 SelfModelStore 分裂（致命缺陷）

```python
# orchestrator.py:89-91
self.personality_resolver = PersonalityResolver()        # 内部又创建 SelfModelStore #1
self.self_model_store = SelfModelStore()                  # SelfModelStore #2（独立）
self.self_model_context_provider = SelfModelContextProvider(store=self.self_model_store)
```

- `resolve()` 在 [personality_resolver.py:242-243](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/personality_resolver.py#L242-L243) 调用 `self.self_model_store.update(...)` → 更新的是 **Store #1**
- `self_model_context_provider` 绑定的是 **Store #2**（永不更新）
- **结果：注入到 LLM Prompt 的【自我认知参考】块始终为空字符串**

### 1.4 真正的写入口与读取入口

| 角色 | 组件 | 说明 |
|---|---|---|
| **唯一持久化写入口** | `GrowthState.save()` ([growth_state.py:207](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/growth_state.py#L207)) | 写入 `data/growth_state.json`，由 `GrowthEngine.apply()` 触发 |
| **关系状态写入口** | `RelationshipState._save()` ([personality/relationship_state.py:47](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/relationship_state.py#L47)) | 写入 `data/relationship_state.json`，由 GrowthPipeline._update_relationship() 触发 |
| **内存写入口（不持久化）** | TraitStateUpdater / SelfModelStore.update / SelfModelManager.refresh | 进程重启即丢失 |
| **读取入口** | 各消费者独立创建 PersonalityResolver 实例并调用 `resolve()` | 无统一读取入口 |

### 1.5 关键风险

1. **TraitState 完全内存**：演化结果不持久化，重启后从 `PersonalityProfile.BASE` 重新开始
2. **Orchestrator 的 SelfModel 注入永远为空**：双 Store 分裂导致 Prompt 中【自我认知参考】失效
3. **GrowthPipeline 的成长记录不进入 Resolver**：`pipeline.growth_records ≠ resolver.growth_history`
4. **YuyiPersistence.save_self_model() 是死代码**：设计上曾打算持久化 SelfModel，但从未接线

---

## 2. Memory 状态来源审计

### 2.1 所有写入口（12 处，全部绕过 Verifier）

| # | 写入口 | 文件:行号 | 目标存储 | 经 Verifier |
|---|---|---|---|---|
| 1 | Orchestrator.process() Step 10 | [orchestrator.py:354](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L354) | `data/memory.json` | 否 |
| 2 | Orchestrator 向量同步 | [orchestrator.py:358](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L358) | `data/chroma_db` | 否 |
| 3 | YuyiCore.chat() (core) | `src/core/yuyi_core.py:191,218` | `data/memory.json` | 否 |
| 4 | YuyiCore.chat() (yuyi.py) | `yuyi.py:162,163` | `data/memory.json`（独立实现） | 否 |
| 5 | RuntimeCore._on_action_completed() | `runtime_core.py:911` | `data/memory.json`（via MemoryAdapter） | 否 |
| 6 | RuntimeCore.handle_completed_experience() | `runtime_core.py:1181` | 同上 | 否 |
| 7 | ExperienceBuilder.flush() | `experience_builder.py:210` | 同上 | 否 |
| 8 | CognitiveLoopVerifier | `cognitive_loop_verifier.py:95` | 同上 | 否 |
| 9 | import_chat.py | `import_chat.py:39-40` | 直接文件写 | 否 |
| 10 | MemorySystem.add() | `memory_system.py:133` | `data/memory.json` | 否 |
| 11 | MemoryAdapter.store_experience() | `memory_adapter.py:68` | `data/memory.json` | 否 |
| 12 | MemoryStore.add()（基础） | [memory_store.py:78](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_store.py#L78) | `data/memory.json` 或用户路径 | - |

### 2.2 MemoryStore 实例分裂（8 处生产代码）

| # | 文件:行号 | 持久化路径 | 风险 |
|---|---|---|---|
| 1 | [orchestrator.py:87](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L87) | `data/memory.json` | 主写入实例 |
| 2 | `src/memory/memory_system.py:33` | `data/memory.json` | 旧版聚合器 |
| 3 | `src/runtime/adapters/memory_adapter.py:43` | `data/memory.json`（默认） | RuntimeCore 间接实例 |
| 4 | `src/runtime/runtime_core.py:346` | 配置路径 | 可能与主路径不同 |
| 5 | `src/runtime/runtime_core.py:2055` | 配置路径 | 稳定性检查时单独 new |
| 6 | `src/context/context_manager.py:37` | `data/memory.json` | 懒加载 |
| 7 | `src/thinking/self_check.py:31` | `data/memory.json` | 懒加载 |
| 8 | `src/memory/memory_service.py:15` | 可配置 | 仅测试使用 |

### 2.3 Memory 生命周期图

```
【数据产生源】                  【写入路径】                    【持久化层】
───────────────                ────────────                    ──────────

用户对话消息 ──┬──> Orchestrator.process() ──┐
              │     memory_store.add()       │
              │     vector_memory.add_memory()│
              │                              ├──> data/memory.json ◄──┐
              ├──> YuyiCore.chat() ──────────┤                        │
              │     MemorySystem.add()       │                        │
              │                              │                        │
              └──> yuyi.py (独立实现) ────────┤                        │
                    直接 json.dump()         │                        │
                                                                       │
Runtime 经验 ──┬──> RuntimeCore._on_action_completed()                 │
              │     MemoryAdapter.store_experience() ──────────────────┤
              ├──> RuntimeCore.handle_completed_experience() ───────────┤
              ├──> ExperienceBuilder.flush() ──────────────────────────┤
              └──> CognitiveLoopVerifier ─────────────────────────────┤
                                                                       │
批量导入 ──────> import_chat.py (直接文件写) ──────────────────────────┤
                                                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          持久化存储层                                   │
├──────────────────┬──────────────────┬──────────────────┬──────────────┤
│ data/memory.json │ data/chroma_db/  │ data/normalized_ │ data/        │
│ (主对话记忆)     │ (向量索引)       │ events.json      │ identity_    │
│                  │                  │ (人生事件)       │ memory.json  │
└──────────────────┴──────────────────┴──────────────────┴──────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  MemoryGate (设计但未接入)                                              │
│    ├─ MemoryExtractor.extract() → MemoryCandidate[]                     │
│    └─ MemoryVerifier.verify() → 标准化记忆 (truth/usage/risk)            │
│  (设计意图：唯一裁判，过滤后才允许持久化)                                │
│  (现实：所有写入口都绕过此 Pipeline)                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.4 关键风险

1. **无唯一写入口**：12 个写入口分散在 5 个子系统中，全部绕过 MemoryVerifier
2. **并发写风险**：MemoryStore 执行 `load() → modify → _save()` 全量操作，两实例并发写入会丢失数据
3. **Pipeline 形同虚设**：MemoryGate 设计完整但未接入生产
4. **行为不一致**：Orchestrator 只写 user 消息，yuyi.py 同时写 user 和 assistant
5. **VectorMemory 用户隔离缺失**：MemoryStore 支持 UserContext 路径隔离，但 VectorMemory 始终使用全局路径

---

## 3. Relationship 状态审计

### 3.1 两个同名 RelationshipState 类（致命问题）

| 类 | 文件 | 字段 | 持久化路径 |
|---|---|---|---|
| **类 A**（dataclass） | [src/relationship/relationship_state.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/relationship/relationship_state.py) | familiarity/trust/collaboration/interaction_frequency/communication_style/relationship_stage | `data/users/<uid>/relationship_state.json`（**不存在**） |
| **类 B**（v0.6 持久化版） | [src/personality/relationship_state.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/relationship_state.py) | bond_strength/trust/familiarity/promise_level/shared_history/activity_level/milestones/important_events | `data/relationship_state.json`（**存在**） |

**两者字段重叠（trust/familiarity）但语义不同，永不sync。**

### 3.2 实例分裂检查表

| 实例位置 | 类版本 | 存储路径 | 生产是否激活 | 风险 |
|---|---|---|---|---|
| [orchestrator.py:117](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L117) `self.relationship_state` | 类 B 包装 | `data/relationship_state.json` | 是（实例化）但仅测试用 | 字段与类 A 重叠但不同步 |
| [orchestrator.py:115](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L115) `self.relationship_profile` | None（恒为 None） | 无 | 否 | Phase 7 入口完全断开 |
| [orchestrator.py:572](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L572) `rel_repo` | Phase 7 | `data/users/<uid>/...` | **否（死代码）** | RuntimeContext 从不设置该 key |
| `runtime_core.py:621` `relationship_state_runtime` | 类 A | `data/users/yuyi/...` | **否（默认禁用）** | 即使启用，data/users/ 不存在 |
| `personality_resolver.py:39` | 类 B | 默认实例（无持久化） | 是 | 与 Orchestrator 实例独立 |
| `growth/pipeline.py:61` | 类 B | 默认实例 | 是 | 又一个独立实例 |

### 3.3 关键死代码

[orchestrator.py:570-643](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L570-L643) `_process_relationship_post()`：
- line 572 读取 `assembled_context.get("relationship_repo")`
- RuntimeContext 从不设置 `relationship_repo` key
- line 574 `if not rel_repo: return` 必然触发
- **整个函数体从未执行**

### 3.4 关键风险

1. **三个独立持久化路径不同步**：`data/relationship_state.json`（类 B）、`data/users/<uid>/relationship_state.json`（类 A，不存在）、`data/runtime_state.json`（RuntimeCore）
2. **RuntimeCore 的关系系统默认禁用**：`relationship_enabled=False`，且 `data/users/` 目录不存在
3. **Orchestrator 的关系后处理是死代码**：`_process_relationship_post` 从未执行

---

## 4. RuntimeCore 能力边界审计

### 4.1 Runtime State Matrix

| 状态模块 | 当前管理者 | 应管理者 | 持久化路径 | 风险等级 | 风险说明 |
|---|---|---|---|---|---|
| **SelfState**（energy/mood） | RuntimeCore | RuntimeCore | `data/runtime_state.json` | 低 | 设计正确，但 Orchestrator 不读取 |
| **WorldState** | RuntimeCore | RuntimeCore | `data/runtime_state.json` | 低 | 同上 |
| **EmotionManager** | **Orchestrator + RuntimeCore 双实例** | 应单一（RuntimeCore） | `data/emotion_state.json`（**双实例写同一文件**） | **高** | 写冲突风险 |
| **RelationshipState**（类 A） | RuntimeCore（禁用中） | RuntimeCore | `data/users/<uid>/...`（不存在） | **高** | 默认禁用；持久化目录从未创建 |
| **RelationshipState**（类 B） | **Orchestrator + PersonalityResolver + GrowthPipeline 三处独立实例** | 应单一 | `data/relationship_state.json` | **高** | 三实例不同步 |
| **RelationshipInfluenceProfile**（Phase 7） | 无人（死代码） | 应删除或迁移 | `data/users/<uid>/...`（不存在） | **高** | `_process_relationship_post` 完全失效 |
| **MemoryStore** | Orchestrator | Orchestrator（即时记忆） | `data/memory.json` | 中 | RuntimeCore 通过 MemoryAdapter 独立访问 |
| **VectorMemory** | Orchestrator | Orchestrator | `data/chroma_db/` | 低 | 仅 Orchestrator 使用 |
| **PersonalityResolver** | **Orchestrator + RuntimeCore 双实例** | 应单一 | 无（内存） | **高** | 人格解析结果可能不一致 |
| **SelfModelStore / SelfModelManager** | **Orchestrator + RuntimeCore 双实例** | 应单一 | 无（内存） | **高** | Orchestrator 内部还有双 Store 分裂 |
| **EmotionEventDetector** | Orchestrator | Orchestrator | 无 | 低 | 仅 Orchestrator 使用 |
| **生命周期 tick** | RuntimeCore | RuntimeCore | `data/runtime_state.json` | 中 | tick 修改的状态不传递给 Orchestrator |
| **ActionDispatcher** | RuntimeCore | RuntimeCore | 内存 | 低 | 设计正确 |
| **Scheduler** | RuntimeCore | RuntimeCore | 内存 | 低 | 设计正确 |
| **UserContext / UserResolver** | Orchestrator | Orchestrator | 无 | 低 | 仅 Orchestrator 使用 |
| **ScreenContextManager** | Orchestrator | Orchestrator | 无 | 低 | 远程模块，默认禁用 |
| **ResponseEngine** | Orchestrator | Orchestrator | 无 | 低 | LLM 生成层 |

### 4.2 RuntimeBridge 是单向桥（关键缺陷）

**设计意图**：单例模式，不修改核心代码，通过 adapter/bridge 完成对接

**实际行为**：
- **单向数据流**：事件 → RuntimeBridge → RuntimeCore（`inject_event`）
- **Orchestrator 从不查询 RuntimeCore 状态**：搜索 `get_runtime_bridge` 调用方，仅在 `api_server.py`、`initiative_sender.py`、`initiative_bridge.py` 中调用
- **Orchestrator 内部完全没有调用 `get_runtime_bridge`**

**结果**：
- Orchestrator 的 `self.emotion_manager`（[orchestrator.py:101](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L101)）与 RuntimeCore 的 `self.emotion_manager`（`runtime_core.py:637`）是**两个独立实例**
- Orchestrator 的 `self.relationship_state`（类 B）与 RuntimeCore 的 `relationship_state_runtime`（类 A）是**两个独立实例**
- RuntimeCore 的 tick 修改的 self_state/world_state **完全不传递给 Orchestrator**

### 4.3 RuntimeCore 当前管理哪些状态

| 状态 | 启用条件 | 实际生产状态 |
|---|---|---|
| SelfState（energy/mood/curiosity） | 默认启用 | **激活** |
| WorldState | 默认启用 | **激活** |
| ActionDispatcher | 默认启用 | **激活** |
| Scheduler（tick 60s） | 默认启用 | **激活** |
| EmotionManager | `emotion_enabled=False` | **禁用** |
| MemoryAdapter | `adapters_enabled=False` | **禁用** |
| PersonalityResolver | `adapters_enabled=False` | **禁用** |
| PersonalityEvolutionPipeline | `adapters_enabled=False` | **禁用** |
| SelfModelManager | `adapters_enabled=False` | **禁用** |
| RelationshipRepository | `relationship_enabled=False` | **禁用** |
| CognitiveEngine | `cognitive_enabled=False` | **禁用** |
| ExperienceBuilder/ReflectionEngine | `experience_enabled=False` | **禁用** |
| AutonomousScheduler | `autonomous_scheduler_enabled=False` | **禁用** |
| ReflectionScheduler | `reflection_scheduler_enabled=False` | **禁用** |

**结论**：RuntimeCore 在生产中**仅作为"自我状态衰减器 + 定时器"运行**，其设计的所有高级能力（人格演化、记忆巩固、关系智能、认知决策）**全部默认禁用**。

### 4.4 生命周期 tick

**频率**：`DEFAULT_TICK_INTERVAL = 60.0` 秒

**tick 执行操作**（`runtime_core.py:770-811`）：
1. `self._tick_count += 1`
2. `self.self_state.decay(delta)` — 内部状态自然衰减
3. `self.world_state.update_time()` — 更新时间戳
4. `self._maybe_decide()` — 触发双层决策
5. 每 5 分钟调用 `_save_state()` 持久化到 `data/runtime_state.json`

**tick 结果传递给 Orchestrator**：**完全不传递**
- Orchestrator 从未读取 `data/runtime_state.json`
- RuntimeBridge 不回传 tick 结果

---

## 5. Phase 4.2 最小修改方案

### 5.1 设计原则

**允许**：
- 引用共享（通过 RuntimeBridge 传递实例引用）
- 状态转发（RuntimeCore → Orchestrator 单向状态同步）
- 生命周期统一（统一初始化顺序）
- 接口补齐（添加缺失的 getter 方法）

**禁止**：
- 删除模块
- 改 QQ 链路
- 修改 API
- 大规模目录调整
- 合并所有代码

### 5.2 修改方案分层

#### 方案 A：最小可行（P0 - 解决双实例写冲突）

**目标**：消除 EmotionManager 双实例写同一文件的风险，让 Orchestrator 读取 RuntimeCore 的 EmotionManager

**修改文件**：
1. `src/runtime/runtime_bridge.py` — 新增 `get_emotion_manager()` 方法
2. `src/orchestrator.py` — `__init__` 中通过 RuntimeBridge 获取 EmotionManager（fallback 到自建）
3. `src/runtime/runtime_core.py` — 启用 EmotionManager（将 `emotion_enabled` 默认改为 True 或通过配置启用）

**风险**：低。仅改变引用来源，不改变 EmotionManager 内部逻辑。

**测试**：
- 验证 Orchestrator 的 emotion_manager 与 RuntimeCore 的是同一实例
- 验证情绪变化能正确进入 prompt（已在 Phase 4.1 覆盖）

---

#### 方案 B：扩展（P1 - 解决 SelfModel 分裂）

**目标**：修复 Orchestrator 内部的双 SelfModelStore 分裂，让 SelfModel 注入真正生效

**修改文件**：
1. `src/orchestrator.py` — 删除 `self.self_model_store = SelfModelStore()`，改为从 `self.personality_resolver.self_model_store` 获取引用
2. `src/orchestrator.py` — `SelfModelContextProvider` 绑定到 `self.personality_resolver.self_model_store`

**风险**：中。改变了 SelfModelStore 的所有权，需验证 `resolve()` 的副作用是否正确传递。

**测试**：
- 验证 `resolve()` 后 `self_model_context_provider.get_context()` 返回非空
- 验证 Prompt 中【自我认知参考】块有真实内容

---

#### 方案 C：扩展（P2 - 解决 Personality 实例分裂）

**目标**：让 Orchestrator 使用 RuntimeCore 的 PersonalityResolver 实例

**修改文件**：
1. `src/runtime/runtime_bridge.py` — 新增 `get_personality_resolver()` 方法
2. `src/runtime/runtime_core.py` — 启用 PersonalityResolver（`adapters_enabled` 或单独开关）
3. `src/orchestrator.py` — `__init__` 中通过 RuntimeBridge 获取 PersonalityResolver（fallback 到自建）

**风险**：中。PersonalityResolver 内部有内存状态（_trait_states），需确保 RuntimeCore 的实例已被正确初始化。

**测试**：
- 验证 Orchestrator 的 personality_resolver 与 RuntimeCore 的是同一实例
- 验证 `resolve()` 结果的一致性

---

#### 方案 D：扩展（P3 - 解决 Relationship 死代码）

**目标**：修复 Orchestrator 的关系后处理死代码，或明确删除

**修改文件**：
1. `src/orchestrator.py` — `_process_relationship_post` 中改为从 RuntimeBridge 获取 RelationshipRepository，或直接删除该方法
2. `src/runtime/runtime_context.py` — `assemble_context` 新增 `relationship_repo` key（若保留）

**风险**：低。当前是死代码，修改不会影响生产行为。

**测试**：
- 验证关系状态能正确更新到 `data/relationship_state.json`

---

#### 方案 E：扩展（P4 - tick 状态回流）

**目标**：让 RuntimeCore 的 tick 修改的 self_state 能被 Orchestrator 读取

**修改文件**：
1. `src/runtime/runtime_bridge.py` — 新增 `get_self_state_snapshot()` 方法
2. `src/orchestrator.py` — `process()` 中通过 RuntimeBridge 获取 self_state 快照
3. `src/runtime/runtime_context.py` — `assemble_context` 新增 `runtime_state` key

**风险**：低。只读取，不修改。

**测试**：
- 验证 tick 后 Orchestrator 能读取到最新的 self_state

---

### 5.3 推荐实施顺序

| 顺序 | 方案 | 优先级 | 依赖 | 预计修改文件数 |
|---|---|---|---|---|
| 1 | 方案 A（EmotionManager 共享） | P0 | 无 | 3 |
| 2 | 方案 B（SelfModel 分裂修复） | P1 | 无 | 1 |
| 3 | 方案 E（tick 状态回流） | P1 | 方案 A | 3 |
| 4 | 方案 C（PersonalityResolver 共享） | P2 | 方案 A | 3 |
| 5 | 方案 D（Relationship 死代码修复） | P3 | 无 | 2 |

### 5.4 不做的事项

- ❌ 不删除任何模块（包括死代码 orchestrator_hooks.py）
- ❌ 不合并 PersonalityResolver / SelfModelStore / MemoryStore 的所有实例
- ❌ 不接入 MemoryVerifier（保持现状，所有写入口绕过裁判）
- ❌ 不改变 QQ 接入
- ❌ 不修改 API Key
- ❌ 不开发屏幕控制（等待 Phase 4.3）
- ❌ 不合并两个 RelationshipState 类（保持双轨）
- ❌ 不启用 RuntimeCore 的所有高级能力（adapters_enabled 保持 False）

---

## 6. 测试计划

### 6.1 新增测试文件

`tests/test_runtime_state_authority.py`

### 6.2 测试覆盖

| # | 测试类 | 覆盖项 |
|---|---|---|
| 1 | `TestEmotionManagerShared` | EmotionManager 实例共享（方案 A） |
| 2 | `TestSelfModelStoreUnified` | SelfModelStore 分裂修复（方案 B） |
| 3 | `TestPersonalityResolverShared` | PersonalityResolver 实例共享（方案 C） |
| 4 | `TestTickStatePropagation` | tick 状态回流（方案 E） |
| 5 | `TestRelationshipDeadCode` | 关系死代码处理（方案 D） |

### 6.3 回归测试

- 运行 Phase 4.1 的 `tests/test_runtime_unification.py`（24 个测试）
- 运行 `tests/test_emotion_runtime_integration.py`
- 运行 `tests/test_personality_growth_runtime.py`

---

## 7. 风险分析总表

| 风险 | 严重性 | 影响范围 | Phase 4.2 是否解决 |
|---|---|---|---|
| EmotionManager 双实例写同一文件 | 高 | 情绪状态丢失/覆盖 | **是（方案 A）** |
| Orchestrator 双 SelfModelStore 分裂 | 高 | Prompt 中自我认知永远为空 | **是（方案 B）** |
| PersonalityResolver 4 处独立实例 | 高 | 人格解析结果不一致 | 部分（方案 C 解决 Orchestrator + RuntimeCore） |
| MemoryStore 8 处独立实例 | 中 | 并发写丢失数据 | 否（超出 Phase 4.2 范围） |
| 两个同名 RelationshipState 类 | 高 | 关系状态不同步 | 否（保持双轨，仅修复死代码） |
| RuntimeBridge 单向桥 | 高 | RuntimeCore 状态不回流 | **是（方案 E + A）** |
| MemoryVerifier 未接入 | 中 | 记忆质量无保障 | 否（超出范围） |
| TraitState 不持久化 | 中 | 人格演化重启丢失 | 否（超出范围） |
| tick 状态不回流 | 中 | RuntimeCore 对对话无影响 | **是（方案 E）** |

---

## 8. 审计结论

1. **系统设计了完整的"长期状态中心"（RuntimeCore）和"唯一裁判"（MemoryVerifier），但实际生产中全部被绕过**
2. **Orchestrator 与 RuntimeCore 是两个完全隔离的实例集合**，通过 RuntimeBridge 仅实现单向事件转发
3. **最严重的实例分裂**：EmotionManager（双实例写同一文件）、PersonalityResolver（4 处独立实例）、SelfModelStore（Orchestrator 内部双 Store 分裂）
4. **Phase 4.2 最小修改方案**可通过 5 个独立方案（A-E）逐步解决实例分裂问题，每个方案都可独立实施和回滚
5. **不解决 MemoryStore 多实例和 MemoryVerifier 未接入问题**，这些超出 Phase 4.2 范围

---

**审计完成。等待确认后再编码。**

**分支**：`fix/runtime-state-authority`（待创建）
**预计修改文件**：3-8 个（取决于实施哪些方案）
**预计新增测试**：5-15 个
