# Authority Registry · 权威注册表

> **Phase**: 4.0-R1 (Runtime Stabilization & Authority Consolidation)
> **状态**: 冻结 v1.0（本文件创建后未经架构师批准不得修改）
> **目标**: 任何新代码不再问"我应该创建什么新的 XYZSystem"，只问"Authority 要求我接入哪一个"。

---

## 总原则

1. **每一个核心领域有且仅有一个 Canonical（权威）实现/入口。**
2. **其余实现要么是 Adapter（只做兼容，不产生新状态），要么是 Deprecated（未来移除）。**
3. **Canonical = 可写状态的唯一合法入口**；Deprecated = 除了读状态不得有任何写盘/变更行为。
4. **任何新模块、新功能、新 Adapter 必须先在此文件登记后才能合入，否则视为架构违规。**

---

# 1. Runtime 领域

| 项目 | 内容 |
|------|------|
| **Canonical 入口** | `RuntimePipeline` |
| **Canonical 内部组件** | `LifecycleExecutor` + `stages.py` (18-stage 顺序唯一来源) + `runtime.py::RuntimeCore`（Adapter 模式，仅依赖 Protocol） |
| **负责人领域** | Runtime Orchestration Layer |

## Canonical 调用链（唯一合法路径）

```
外部消息（HTTP / CLI / AstrBot）
   ↓
api_server / main 入口
   ↓
RuntimePipeline.run(input_data)            ← 唯一对外入口（Canonical）
   │
   ├─ 存在 runtime 实例 → runtime.process(event, ctx)
   │                         ↓
   │                     runtime.py::RuntimeCore.process()  ← 内部执行（Canonical）
   │                         ↓
   │                     LifecycleExecutor 遍历 stages.py 的 18 阶段
   │
   └─ 不存在 / runtime 失败 → 走 legacy Orchestrator（必须告警）
```

| 类型 | 类名 | 文件 | 处置 | 说明 |
|------|------|------|------|------|
| **CANONICAL** | `RuntimePipeline` | [runtime/runtime_pipeline.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_pipeline.py#L121) | ✅ 保留并增强 | 对外唯一 process 入口，Phase R2 要加 path audit + fallback 告警 |
| **CANONICAL** | `RuntimeCore`（Adapter 版） | [runtime/runtime.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime.py#L215) | ✅ 保留，作为内部 RuntimeCore | 仅依赖 Protocol，不直 import 业务，是 docs/runtime.md §12 规范实现 |
| **CANONICAL** | `LifecycleExecutor` | [runtime/lifecycle_executor.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/lifecycle_executor.py#L88) | ✅ 保留 | 18 stage 的实际执行器，阶段顺序从 `stages.py` 读 |
| **CANONICAL** | `RuntimeStage` / 顺序常量 | [runtime/stages.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/stages.py) | ✅ 冻结 | 18 stage 顺序唯一真相来源，禁止在此文件之外再定义一份 stage 枚举 |
| **LEGACY ADAPTER** | `OrchestratorRuntimeBridge` | [orchestrator_runtime_bridge.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator_runtime_bridge.py#L32) | ⚠️ 降级为 Legacy Adapter | R2 之后仅在 RuntimePipeline 需要时被内部调用，不对外直接暴露；继续统计 fallback |
| **LEGACY ADAPTER** | `RuntimeBridge`（singleton） | [runtime/runtime_bridge.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_bridge.py#L27) | ⚠️ 降级为 Legacy Adapter | Orchestrator 等 fallback 路径通过它获取 shared 实例；未来 RuntimePipeline 统一注入后可移除 |
| **DEPRECATED** | `RuntimeCore`（超级类 4102 行） | [runtime/runtime_core.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_core.py#L204) | ❌ 废弃，Phase R2 开始标记为 Legacy | 直接 import 60+ 业务模块，违反 Runtime 单向依赖原则。短期作为 shared modules 持有者被新 RuntimeCore 通过 adapter 调用；长期 2-3 Phase 内拆解到 LifecycleExecutor tasks |
| **DEPRECATED** | `Orchestrator`（作为 process 入口） | [orchestrator.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L84) | ❌ 废弃作为对外入口 | 仅保留 Legacy fallback 生成回复路径；对外 process() 不再被任何新代码直接调用（必须走 RuntimePipeline） |
| **DEPRECATED** | `LifecycleManager`（顶层版本） | [runtime/lifecycle_manager.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/lifecycle_manager.py#L99) | ❌ 废弃 | 迁移到 `runtime/lifecycle/` 目录下新的 `LifecycleManager`（注册式） |
| **DEPRECATED** | `RuntimeLifecycleOrchestrator` | [runtime/lifecycle_orchestrator.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/lifecycle_orchestrator.py) | ❌ 废弃（概念层已合并） | 认知事件流职责合并到 LifecycleExecutor 的 stage result 记录中 |

---

# 2. Identity 领域

| 项目 | 内容 |
|------|------|
| **Canonical 数据层** | `OriginIdentity`（起源不可变历史） + `IdentityAnchor` / `IdentityContinuityChecker` / `IdentityStabilityEngine`（稳定性门控三件套） |
| **Canonical 运行时构建层** | `IdentityContextBuilder`（位于 `src/runtime/identity_context_builder.py`，唯一写入 RuntimeContext.identity_context_text 的入口） |
| **Canonical 用户边界层** | `UserContext` + `UserResolver` |
| **严格约束** | Identity 领域不允许直接写 Personality/ TraitState。只输出 StabilityReport / ContinuityReport 作为门控。 |

| 类型 | 类名 | 文件 | 处置 | 说明 |
|------|------|------|------|------|
| **CANONICAL** | `OriginIdentity` | [identity/origin_identity.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/identity/origin_identity.py#L52) | ✅ 冻结，append-only | 起源贡献者、创造关系的不可变记录 |
| **CANONICAL** | `OriginManager` | [identity/origin_manager.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/identity/origin_manager.py#L14) | ✅ 权威管理器 | 起源身份的唯一读写管理入口 |
| **CANONICAL** | `OriginBoundary` + `OriginVerifier` | [identity/origin_boundary.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/identity/origin_boundary.py#L21) / [origin_verifier.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/identity/origin_verifier.py#L10) | ✅ 保留 | 边界检查 + 完整性验证 |
| **CANONICAL** | `IdentityAnchorManager` | [personality/identity_anchor.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/identity_anchor.py#L214) | ✅ 保留 | 核心锚点完整性检查 |
| **CANONICAL** | `IdentityContinuityChecker` + `IdentityContinuityHistory` | [personality/identity_continuity.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/identity_continuity.py#L85) | ✅ 保留 | 连续性检查 |
| **CANONICAL** | `IdentityStabilityEngine` | [personality/identity_stability_engine.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/identity_stability_engine.py#L77) | ✅ 保留 | 输出 StabilityReport |
| **CANONICAL** | `IdentityContextBuilder`（Runtime 顶层版本） | [runtime/identity_context_builder.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/identity_context_builder.py#L59) | ✅ 唯一构建入口 | Phase 4.0.3 产物，稳定身份 + 当前状态的两段式文本构建，只读不写源数据 |
| **CANONICAL** | `UserContext` + `UserResolver` | [identity/user_context.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/identity/user_context.py#L12) / [user_resolver.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/identity/user_resolver.py#L9) | ✅ 保留 | 用户隔离 + 解析 |
| **COMPAT (只读取)** | `IdentityCore`（TypedDict） | [personality/identity_core.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/identity_core.py#L22) | ⚠️ 保留为只读 schema | Canonical 数据结构的兼容映射，不得新增代码写入此 TypedDict |
| **DEPRECATED** | `CoreIdentity`（类） | [personality/core_identity.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/core_identity.py#L9) | ❌ 废弃 | 职责与 OriginIdentity 重复，调用方迁移到 OriginManager |
| **DEPRECATED** | `IdentityContextBuilder`（runtime/self_model 下重复版本） | [runtime/self_model/identity_binding/identity_context_builder.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/identity_binding/identity_context_builder.py#L159) | ❌ 废弃 | 与 Canonical 同名不同实现。Phase R3 SelfModel 收敛时改为透传调用 Canonical IdentityContextBuilder |

---

# 3. Memory 领域

| 项目 | 内容 |
|------|------|
| **Canonical 存储入口** | `MemoryStore`（当前 JSON 实现；Phase R4 将通过 Repository 模式切换为 SQLite） |
| **Canonical 辅助系统** | `PollutionGuard`（写前过滤） + `VectorMemory`（向量检索） + `MemoryRelevanceEvaluator`（统一排序） + `MemoryConsolidationEngine`（维护视图，追加不覆盖） |
| **Phase R4 冻结接口** | `save()` / `query()` / `search()` / `get_recent()`（调用方只依赖 Repository Protocol，不依赖具体 JSON/SQLite） |
| **严格约束** | 任何地方不得直接 `MemoryStore()` 自建实例；必须通过 `RuntimeBridge.get_memory_store()` 或 Phase R2 统一注入。 |

| 类型 | 类名 | 文件 | 处置 | 说明 |
|------|------|------|------|------|
| **CANONICAL** | `MemoryStore` | [memory/memory_store.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_store.py#L35) | ✅ 当前权威（R4 将升级为 Repository 实现） | 唯一权威记忆存储接口；Phase R4 保持方法签名不变，内部切换 SQLite |
| **CANONICAL** | `PollutionGuard`（check 函数） | [memory/pollution_guard.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/pollution_guard.py) | ✅ 冻结（只允许更新规则集） | 写前三层过滤：白名单+黑名单+启发式 |
| **CANONICAL** | `VectorMemory` | [memory/vector.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/vector.py#L14) | ✅ 保留，需修正检索范围 | Phase R2 需放宽 role in (user, assistant)；当前仅 role=user 导致召回缺失 |
| **CANONICAL** | `MemoryRelevanceEvaluator` | [memory/memory_relevance_evaluator.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_relevance_evaluator.py#L62) | ✅ 保留 | 7-factor 统一排序，不得在 MemoryStore 之外再写排序逻辑 |
| **CANONICAL** | `MemoryConsolidationEngine` | [memory/memory_consolidation_engine.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_consolidation_engine.py#L28) | ✅ 保留 | episodic/semantic/identity/relationship/emotional 五分类巩固 |
| **CANONICAL** | `MemoryContext` | [memory/memory_context.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_context.py#L22) | ✅ 保留 | RuntimeContext.memory_context 的唯一形状 |
| **COMPAT (薄包装，可复用)** | `MemoryGate` | [memory/memory_gate.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_gate.py#L13) | ⚠️ 保留为写前预检查 | 是 PollutionGuard + extra 规则的组合调用，不绕过权威写入 |
| **COMPAT (复用)** | `MemoryRetriever` / `MemoryService` | [memory/memory_retriever.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_retriever.py#L9) / [memory_service.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_service.py#L9) | ⚠️ 保留但禁止自建 MemoryStore | 必须注入 Canonical MemoryStore 实例；不得内部自建 |
| **DEPRECATED** | `MemorySystem` | [memory/memory_system.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_system.py#L28) | ❌ 废弃 | 自身 `MemoryStore()` 自建实例（L26 `self.store = MemoryStore()`）。迁移到通过 RuntimeBridge 获取共享实例 |
| **需要改造** | 7 处分散的 `MemoryStore()` 自建点 | 见 audit 报告（orchestrator fallback / context_manager / self_check / memory_system / topic_tracker / event_extractor / runtime_core lazy） | ⚠️ Phase R2 必须改造 | 改为取不到共享实例时 `raise RuntimeError` 启动失败，不再静默自建 |

---

# 4. Personality 领域

| 项目 | 内容 |
|------|------|
| **Canonical Trait 定义** | `PERSONALITY_DIMENSIONS` in `traits.py`（7 维度 + 范围保护 + TRAIT_BEHAVIOR_MAP） |
| **Canonical 数据结构** | `TraitState`（TypedDict in trait_state.py） |
| **唯一合法写入入口** | `TraitStateUpdater`（任何写入必须经过它） |
| **读取与 Prompt 构建** | `PersonalityResolver` |
| **演化链路** | `EvolutionEngine`（proposal 生成） → `PersonalityEvolutionPipeline`（执行） → `PersonalityStabilityEngine`（门控） |
| **严格约束** | 不得在 TraitStateUpdater 之外直接修改 TraitState 文件。 |

| 类型 | 类名 | 文件 | 处置 | 说明 |
|------|------|------|------|------|
| **CANONICAL** | `PERSONALITY_DIMENSIONS` / `TRAIT_BEHAVIOR_MAP` | [personality/traits.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/traits.py#L25) | ✅ 冻结 | 所有 7 维度 + default + range 的唯一真相。新 trait 必须先改此文件再改其他。 |
| **CANONICAL** | `TraitState`（TypedDict） | [personality/trait_state.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/trait_state.py#L19) | ✅ 冻结（schema 变更需 migration） | 人格状态的唯一结构化形状 |
| **CANONICAL** | `TraitStateUpdater` | [personality/trait_state_updater.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/trait_state_updater.py#L20) | ✅ 唯一写入入口 | 任何人格 trait 变更只有这一个合法写入点，含 apply 审计、边界保护 |
| **CANONICAL** | `PersonalityResolver` | [personality/personality_resolver.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/personality_resolver.py#L30) | ✅ 权威读取/解析 | 读取 TraitState 并结合 behavior map 构建 prompt 级人格上下文 |
| **CANONICAL** | `EvolutionEngine` | [personality/evolution_engine.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/evolution_engine.py#L103) | ✅ 保留（参数需调整） | MIN_EVIDENCE_COUNT=2 → Phase 4.0-R2 后提升至 ≥5；需追加"跨日期分布"校验 |
| **CANONICAL** | `PersonalityEvolutionPipeline` | [personality/personality_evolution_pipeline.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/personality_evolution_pipeline.py#L48) | ✅ 保留 | approved proposal → TraitStateUpdater 的固定链路 |
| **CANONICAL** | `PersonalityStabilityEngine` | [personality/personality_stability_engine.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/personality_stability_engine.py#L24) | ✅ 保留 | 人格 drift 监控 + 阻断 |
| **CANONICAL** | `TraitRelations`（相关逻辑） | [personality/trait_relations.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/trait_relations.py) | ✅ 保留 | trait 之间的关联/约束规则 |
| **COMPAT** | `PersonalityAdapter`（personality.py 版本） | [personality/personality_adapter.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/personality_adapter.py#L256) | ⚠️ 作为兼容层保留 | 是 PersonalityResolver + TraitStateUpdater 的组合包装，新代码直用两个 Canonical |
| **COMPAT** | `BehaviorEngine` | [personality/behavior_engine.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/behavior_engine.py#L58) | ⚠️ 保留为 prompt 行为增强 | 是 trait → 行为文字的映射，不修改状态 |
| **DEPRECATED** | `PersonalityAdapter`（runtime/adapters/ 版本） | [runtime/adapters/personality_adapter.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/adapters/personality_adapter.py#L19) | ❌ 废弃，重名冲突 | 迁移到 `impl/` 下的 `PersonalityAdapterImpl`（Canonical 的 Adapter 层） |
| **DEPRECATED** | `PersonalityAdapter`（runtime/integration/adapters/ 版本） | [runtime/integration/adapters/personality_adapter.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/integration/adapters/personality_adapter.py#L37) | ❌ 废弃，重名冲突 | 同上，统一收敛到 `adapters/impl/` |
| **DEPRECATED** | `TraitState`（runtime/self_model/self_state.py 中私定义） | [runtime/self_model/self_state.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/self_state.py#L280) | ❌ 废弃重复定义 | 必须改为 import personality.trait_state 的 Canonical TraitState |

---

# 5. Growth 领域

| 项目 | 内容 |
|------|------|
| **Canonical 提案结构** | `GrowthProposal`（contracts/growth_schema.py 版本）—— 所有提案必须符合此 schema |
| **Canonical 运行链路** | `GrowthEvaluator`（五维评分） → `ProposalManager`（状态机） → `ProposalReviewer`（growth/proposal 子包版本，结构化评审） → `ApprovalManager`（人工/自动审批边界） → `GrowthLimiter`（限流） → `PersonalityEvolutionPipeline`（执行） |
| **Canonical 历史/状态** | `GrowthState` + `GrowthRecord` + `ProposalStore`（growth/proposal_store.py） |
| **Canonical 事件抽取** | `EventExtractor` → `EventNormalizer` → `EventValidator`（事件到 growth proposal 的前置处理管道） |

| 类型 | 类名 | 文件 | 处置 | 说明 |
|------|------|------|------|------|
| **CANONICAL** | `GrowthProposal`（schema） | [contracts/growth_schema.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/contracts/growth_schema.py#L55) | ✅ 冻结 schema | 提案数据结构的唯一真相。后续 schema 变更需做迁移 + 版本号。 |
| **CANONICAL** | `GrowthEvaluator` | [growth/growth_evaluator.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/growth_evaluator.py#L39) | ✅ 保留 | novelty / relevance / consistency / stability / alignment 五维评估 |
| **CANONICAL** | `GrowthLimiter` | [growth/growth_limiter.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/growth_limiter.py) | ✅ 冻结（阈值可调） | 单次/每日/每小时限流 + 置信度门槛，防止漂移 |
| **CANONICAL** | `ProposalManager` | [growth/proposal_manager.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/proposal_manager.py#L52) | ✅ 权威状态机 | 提案的 pending/approved/rejected/rolled_back 全生命周期管理 |
| **CANONICAL** | `ProposalReviewer`（growth/proposal 子包版本） | [growth/proposal/reviewer.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/proposal/reviewer.py#L17) | ✅ 权威评审 | 结构化评审。旧的 `growth/proposal_review.py::ProposalReviewer` 迁移到此。 |
| **CANONICAL** | `ApprovalManager` | [growth/approval_manager.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/approval_manager.py#L63) | ✅ 权威审批边界 | approve/reject/modify 动作的唯一入口 |
| **CANONICAL** | `ProposalStore`（growth 版本） | [growth/proposal_store.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/proposal_store.py#L37) | ✅ 权威持久化 | 提案的磁盘存储；R4 一起升级为 Repository |
| **CANONICAL** | `GrowthState` + `GrowthRecord` | [growth/growth_state.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/growth_state.py#L12) / [growth_record.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/growth_record.py#L17) | ✅ 保留 | 成长统计与成长记录（不可变 append-only） |
| **CANONICAL** | `EventExtractor` / `EventNormalizer` / `EventValidator` | [growth/event_extractor.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/event_extractor.py#L12) / 对应文件 | ✅ 保留 | 事件到候选 evidence 的前置管道 |
| **CANONICAL** | `MeaningResolver` / `BehaviorResolver` | [growth/meaning_resolver.py]() / [behavior_resolver.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/behavior_resolver.py#L12) | ✅ 保留 | 事件意义解析 |
| **COMPAT** | `GrowthEngine`（v1.7 映射表） | [growth/growth_engine.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/growth_engine.py#L25) | ⚠️ 保留但标记为 Legacy Metric 引擎 | GROWTH_MAP 硬编码方式对 GrowthState metrics 的更新，仅供历史兼容；新路径走 Proposal |
| **COMPAT** | `GrowthProposalNormalizer`（contracts） | [contracts/proposal_normalizer.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/contracts/proposal_normalizer.py#L741) | ⚠️ 保留为规范化工具 | 旧 proposal → canonical schema 的规范化器 |
| **DEPRECATED** | `GrowthProposal`（growth_loop.py 内重定义） | [growth/growth_loop.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/growth_loop.py#L79) | ❌ 废弃，重名冲突 | 与 contracts/growth_schema.py 重名。迁移到 Canonical GrowthProposal |
| **DEPRECATED** | `GrowthLoop` | [growth/growth_loop.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/growth_loop.py#L157) | ❌ 废弃 | 职责已被 ProposalManager + Pipeline 取代 |
| **DEPRECATED** | `ProposalReviewer`（growth 顶层版本，非子包） | [growth/proposal_review.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/proposal_review.py#L93) | ❌ 废弃重名 | 迁移到 `growth/proposal/reviewer.py` Canonical 版本 |
| **DEPRECATED** | `GrowthProposal`（growth/proposal/proposal.py 版本） | [growth/proposal/proposal.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/proposal/proposal.py#L68) | ❌ 废弃（re-export 壳） | 标注为 backward compatibility，实际直 import contracts/growth_schema.py 的 Canonical |
| **DEPRECATED** | `GrowthProposalMirrorStorage` | [runtime/adapters/growth_proposal_mirror.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/adapters/growth_proposal_mirror.py#L42) | ❌ 废弃镜像写入 | 双写导致数据不一致。ProposalStore 只有 growth/ 下一个权威版本。 |
| **DEPRECATED** | `ProposalStore`（policy/feedback 版本） | [runtime/policy/feedback/proposal.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/policy/feedback/proposal.py#L203) | ❌ 废弃重名 | policy 层的 proposal 与 Growth Proposal 不是一回事，建议重命名避免歧义 |
| **DEPRECATED** | `GrowthProposalRuntime` / `GrowthProposalLifecycleManager` / `GrowthProposalHistory` / `GrowthProposalApprovalWorkflow`（runtime/growth 目录） | `src/runtime/growth/growth_proposal_*` | ❌ 废弃 Runtime 侧重复管理层 | 运行时只需通过 ProposalManager + Adapter 调用，不需要在 runtime/growth 下再做一套管理 CRUD |

---

# 6. Self Model 领域（最复杂）

| 项目 | 内容 |
|------|------|
| **Canonical 体系** | `src/runtime/self_model/` **目录整体为 Canonical SelfModel 体系**（含 evolution / persistence / audit / reflection / identity_binding 五个子系统，是唯一具备完整演化-持久化-审计-反思-身份绑定五层闭环的实现） |
| **Canonical 核心类** | `SelfModelFoundation`（数据结构基类） + `SelfModelManager`（管理器） + `SelfModelRegistry`（注册中心） + `SelfModelEvolutionEngine`（`evolution/` 子目录内版本） + `SelfModelStore`（`persistence/` 子目录内版本） + `SelfModelEvolutionStore` |
| **Canonical 构建入口** | `SelfModelBuilderBase` / `SelfModelFoundation` 内部 build 方法 |
| **严格约束** | 本文件登记后，任何代码不得 import `src/personality/self_model_*.py` 下的类作为"状态写入入口"；必须改为通过 Adapter 透传到 `src/runtime/self_model/` Canonical 体系。 |

| 类型 | 类名 | 文件 | 处置 | 说明 |
|------|------|------|------|------|
| **CANONICAL (体系)** | `src/runtime/self_model/` 整个目录 | [runtime/self_model/](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/) | ✅ Canonical SelfModel 体系 | 具备完整 evolution / persistence / audit / reflection / identity_binding 五层子系统。未来所有 SelfModel 相关开发在此目录内。 |
| **CANONICAL** | `SelfModelFoundation` + `SelfModelBuilderBase` | [runtime/self_model/self_model_foundation.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/self_model_foundation.py#L76) | ✅ 核心结构基类 | 结构化字段 + identity understanding 层（who_i_am / what_i_value / what_changed / why_changed） |
| **CANONICAL** | `SelfModelManager` | [runtime/self_model/self_model_manager.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/self_model_manager.py#L126) | ✅ 权威管理器 | 生命周期 / 快照 / 演化触发 |
| **CANONICAL** | `SelfModelEvolutionEngine`（evolution 子目录内版本） | [runtime/self_model/evolution/self_model_evolution_engine.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/evolution/self_model_evolution_engine.py#L83) | ✅ 权威演化引擎 | Canonical 演化入口；注意：文件外还有一个同名类需要废弃。 |
| **CANONICAL** | `SelfModelStore`（persistence 子目录内版本） | [runtime/self_model/persistence/self_model_store.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/persistence/self_model_store.py#L87) | ✅ 权威持久化 | append-only snapshot + hash chain |
| **CANONICAL** | `SelfModelEvolutionStore` + `EvolutionRecord` + `EvolutionResult` | [runtime/self_model/self_model_record.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/self_model_record.py#L270) + evolution/ 对应 | ✅ 保留 | 演化历史 + 记录结构 |
| **CANONICAL** | `SelfModelRegistry` | [runtime/self_model/self_model_registry.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/self_model_registry.py#L37) | ✅ 保留 | 多 Source Adapter 的注册中心 |
| **CANONICAL** | `SelfModelSourceAdapter` 基类 + 4 个实现（Memory/Growth/Emotion/Personality） | `runtime/self_model/*_self_model_adapter.py`（5 文件） | ✅ 保留（仅这些 Adapter 是 Canonical） | 汇聚 4 个子系统快照 → Canonical SelfModel；**注意：同名的 personality/self_model_adapter.py 需废弃。** |
| **CANONICAL** | Reflection / Audit 子系统（整个子目录） | `runtime/self_model/reflection/` + `runtime/self_model/audit/` | ✅ 保留 | Canonical 反思 + 审计能力 |
| **CANONICAL** | `SelfModelContextProvider`（runtime 版本） | [runtime/self_model/self_model_context_provider.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/self_model_context_provider.py#L78) | ✅ 保留 | Runtime 内 prompt context provider |
| **CANONICAL** | `SelfModelPolicy` | [runtime/self_model/self_model_policy.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/self_model_policy.py#L164) | ✅ 保留 | 演化约束 / 速率限制 / 保护规则 |
| **CANONICAL** | `SelfModelBootstrap` | [runtime/self_model_bootstrap.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model_bootstrap.py#L36) | ✅ 保留 | 启动时 Canonical SelfModel 初始化引导 |
| **COMPAT Adapter（只读透传）** | `src/personality/self_model_*.py` **整个 personality 层 SelfModel 生态**（共 16 个文件：self_model.py/TypedDict、self_model_v3.py、self_model_core.py、self_model_manager.py、self_model_store.py、self_model_snapshot.py、self_model_updater.py、self_model_builder.py、self_model_builder_v3.py、self_model_adapter.py、self_model_sync_adapter.py、self_model_context_provider.py、self_model_runtime_context.py、self_model_guardian.py、self_model_health.py、self_model_retention.py、self_model_persistence.py） | `src/personality/self_model_*.py`（16 文件） | ⚠️ **Phase R3: 降级为 Read-Through Adapter 层**：不得写盘，内部改为转发调用 Canonical SelfModel（runtime/self_model/ 体系）对外公开相同方法签名 | 这是 R3 最大的改造。保留对外接口但改变内部实现，做到调用方零修改。最终目标：personality/self_model_*.py 不再自己持有任何状态文件。 |
| **DEPRECATED (必须迁移/移除)** | `SelfModel`（TypedDict in personality） | [personality/self_model.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/self_model.py#L127) | ❌ 废弃：只是 TypedDict，且与 Canonical SelfModelFoundation 字段不完全一致。 | 作为兼容 schema 在 R3 改为 re-export Canonical 的字段子集 TypedDict 别名 |
| **DEPRECATED** | `SelfModelV3` | [personality/self_model_v3.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/self_model_v3.py#L30) | ❌ 废弃：旧版本。 | R3 改为透传调用 Canonical SelfModelFoundation，自身不再持有任何可变状态 |
| **DEPRECATED** | `SelfModel`（core/self_model.py） | [core/self_model.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/core/self_model.py) | ❌ 废弃：Core 层历史遗留。 | 同上 |
| **DEPRECATED** | `SelfModel`（reflection_system.py 内私定义） | [reflection_system/reflection_system.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/reflection_system/reflection_system.py#L97) | ❌ 废弃：局部实现。 | 改用 Canonical |
| **DEPRECATED** | `SelfModelEvolutionEngine`（runtime/self_model/self_model_evolution.py 内重复版本） | [runtime/self_model/self_model_evolution.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/self_model_evolution.py#L340) | ❌ 废弃：与 evolution/ 子目录内的权威 EvolutionEngine **同名不同实现**！极度危险，必须优先改类名并标记 deprecated。 | R1.4 立即加注释。R3 改为对 Canonical EvolutionEngine 的薄包装转发。 |
| **DEPRECATED** | `SelfModelSnapshot`（runtime/self_model/self_model_data.py 内版本） | [runtime/self_model/self_model_data.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/self_model_data.py#L128) | ❌ 废弃：与 personality/self_model_snapshot.py 同名。 | 统一收敛到 Canonical SelfModelFoundation 作为 snapshot 形态 |
| **DEPRECATED** | `SelfModelStore`（personality 版本） | [personality/self_model_store.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/self_model_store.py#L27) | ❌ 废弃：与 Canonical persistence 层 SelfModelStore 同名不同实现 | R3 改为透传 |
| **DEPRECATED** | `SelfModelAdapter`（personality 版本，非 Canonical SourceAdapter 基类体系） | [personality/self_model_adapter.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/self_model_adapter.py#L93) | ❌ 废弃：与 Canonical 同名不同接口 | R3 改为透传 |
| **DEPRECATED** | `SelfModelOrchestrator` | [orchestrator/self_model_orchestrator.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator/self_model_orchestrator.py#L46) | ❌ 废弃：Orchestrator 不应编排 SelfModel | SelfModel 编排由 Canonical SelfModelManager + Bootstrap 完成 |

---

# 7. Emotion 领域（补充）

审计时在你建议的 6 领域之外，补充 Emotion 的权威（因为它也涉及是否 prompt 装饰的问题）：

| 项目 | 内容 |
|------|------|
| **Canonical 状态结构** | `EmotionState`（valence/arousal/curiosity/anxiety/confidence/energy 6 维 + dominant/intensity 派生） |
| **写入/更新** | `EmotionManager`（唯一更新 + trace 持久化） |
| **动态引擎** | `EmotionDynamicsEngine`（转移历史 + mood persistence + emotional memory） |
| **评估/产生变化** | `EmotionEngine` + `EmotionEvaluator` + `EmotionDelta` |
| **衰减** | `EmotionDecay` |
| **R2+ 要求** | 必须验证 Emotion 实际影响 Sampling 参数（temperature/top_p/penalty），而不只是 prompt 装饰 |

| 类型 | 类名 | 文件 | 处置 |
|------|------|------|------|
| **CANONICAL** | `EmotionState` | [emotion/emotion_state.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/emotion/emotion_state.py#L17) | ✅ 冻结结构 |
| **CANONICAL** | `EmotionManager` | [emotion/emotion_manager.py] | ✅ 唯一写入更新 + trace 持久化 |
| **CANONICAL** | `EmotionDynamicsEngine` | [emotion/emotion_dynamics_engine.py] | ✅ 保留 |
| **CANONICAL** | `EmotionEngine` + `EmotionEvaluator` + `EmotionDelta` | [emotion/emotion_engine.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/emotion/emotion_engine.py#L10) + 对应文件 | ✅ 保留 |
| **CANONICAL** | `EmotionDecay` + `EmotionPattern` + `EmotionalTrace` | `src/emotion/emotion_decay.py` 等 | ✅ 保留 |

---

# 8. 执行路线与时间表（Phase 4.0-R 系列）

```
Phase 4.0-R1 Authority Registry            ← 本文件（你在这里 ✅）
    ↓
Phase 4.0-R2 Runtime 入口收敛
    · RuntimePipeline.run() 成为唯一对外 process 入口
    · 新增 Runtime path audit 记录（entry / path / fallback）
    · fallback 触发 audit CRITICAL + EventBus 告警
    · 7 处 MemoryStore() 自建点 → 取不到就启动失败（不静默 fallback）
    · 保留旧文件（runtime_core.py、orchestrator.py）作为 Legacy Adapter，不删除
    ↓
Phase 4.0-R3 Self Model 收敛
    · 所有 personality/self_model_*.py（16 文件）降级为 Read-Through Adapter
    · 所有 self_model_* 的写盘全部转发到 Canonical src/runtime/self_model/ persistence 层
    · 禁止产生新的独立状态文件
    · 搜索 "class SelfModel" 后只允许 1 个核心实现（Canonical Foundation），其余为 adapter/deprecated
    ↓
Phase 4.0-R4 Memory 抽象升级
    · 定义 MemoryRepository Protocol（save / query / search / get_recent 冻结接口）
    · 现有 JsonRepository（当前 MemoryStore 内部改名）
    · 为未来 SQLiteRepository 预留接口，调用方不再 import 具体 MemoryStore 类
    · 注意：本期只做接口冻结，不执行百万级数据迁移（等接口稳定后再迁）
    ↓
Phase 4.0-R5 长期运行测试
    · 创建 tests/stability/ 目录
    · 运行 10000 轮模拟聊天测试，断言：
        - Personality trait drift 在 ±10% 阈值内（t=0 vs t=10000）
        - Identity anchor 未变化
        - Memory PollutionGuard 通过率 = 100%
        - Runtime path 100% = full_runtime，fallback 次数 = 0
        - Growth 总 apply 次数 ≤ GrowthLimiter 理论上限
        - 目标：模拟"羽依运行一年"量级的稳定性

完成 4.0-R 系列后 → 回到原路线 Phase 3.5.17（Identity Continuity）→ 3.5.18 → ... → 3.5.20 Final Integration
```

---

# 9. 新代码接入规则（开发者纪律）

从本注册表冻结之日（Phase 4.0-R1 交付）起：

1. **禁止**创建任何"看起来像"Runtime / Memory / Identity / Personality / Growth / SelfModel / Emotion 的新类，除非：
   - 已在此文件登记为 Canonical 的**新必需子类**，或
   - 是 Canonical 类的 **Adapter / Facade**，标注明确的依赖关系。

2. **禁止**在任何地方直接构造 Canonical 类的第二个实例。单例获取方式：
   - Runtime：通过 `RuntimePipeline` 或 `RuntimeBridge` 获取共享实例
   - Memory：通过 `RuntimeBridge.get_memory_store()`（R2 后改为 Repository 注入）
   - 其他领域：通过 Runtime 的注入接口，禁止 `XxxClass()` 直接 new

3. **禁止**修改 Canonical 类的 public 方法签名（加字段/改返回）—— 需走 schema migration 流程，保证 backward compatibility。

4. **任何改动涉及 authority 归属变更**（例如想把某个 deprecated 类提升为 canonical）→ 必须先更新本文件 → 架构师 review → 合入 → 执行迁移。

---

**版本**: v1.0（Phase 4.0-R1 冻结）  
**下次评审**: Phase 4.0-R2 交付前复查 deprecated 清单，R3 交付后更新 SelfModel 收敛结果。
