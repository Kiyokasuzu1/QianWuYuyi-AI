# P2.4-B.14 Runtime Cognitive Loop Architecture Audit — 架构审计报告

> 记录时间：2026-08-20
> 性质：**全程只读审计**。本任务未修改 src/、data/、tests/ 任何文件，未新增运行逻辑。
> 用途：审计当前 RuntimeCore / Orchestrator / Lifecycle / Persistence 架构，设计羽依长期认知循环，输出 B.15 实施路线。
> 产出文件：本文档（唯一）。

---

## Phase 0 — 基线

| 项目 | 值 |
| --- | --- |
| branch | `develop/v1.1` |
| HEAD | `225d0408e89b475605d62b0435f85a9954ffb024`（与 B.10~B.13 一致，全部治理工作仍在未提交工作区） |
| git status | 256 项变更（B.4~B.13 未提交改动 + 既有 in-flight；data/ 零修改） |
| data/ 状态 | 干净（`git status --short -- data/` 为空） |
| governance 状态 | B.13 完成态；governance_mode=legacy；五域治理 flag 全部 False；NEED_REVIEW 消费链（ProposalStore+ProposalManager）就绪未开启 |
| 测试状态 | 治理套件 31/31 通过（B.10/B.11/B.13）；全量 battery 已知 27 个既有失败（B.5~B.9 时代，环境+历史原因，非本阶段引入） |
| src/runtime/ 规模 | 103 个文件（含 lifecycle/、pipeline/、self_model/、reflection/ 等子包） |

生产入口事实（本审计最重要的架构事实）：
- **api_server.py 是生产进程**：`/v1/chat/completions` 在 `runtime.enabled=true`（config.yaml:124-125，当前为 true）时走 **RuntimePipeline**，异常/空回复自动 fallback 到 `orchestrator.process()`；`enabled=false` 时直走旧链路（秒级回滚开关）。
- **main.py 是 CLI 模拟器**：`LongLoopOrchestratorAdapter` 构造 `RuntimePipeline(runtime=None)` —— 注释明确"**暂不启用 RuntimeCore 17 阶段**"（main.py:34），Orchestrator 仍是业务实现者。
- **无认知心跳线程**：api_server 的常驻线程只有 AgentStatusWriter（状态文件心跳，:690）与 Agent Server（远程代理，:797）；注释声称"主动消息 RuntimeCore tick 由 api_server 独占"（:402），但**实际无任何 tick 驱动线程**。

---

## Phase 1 — RuntimeCore 审计

### 1.1 事件入口

| 入口 | 位置 | 语义 | 生产激活 |
| --- | --- | --- | --- |
| `process(event, ctx)` | runtime_core.py:4829 | 唯一 17 阶段生命循环入口；仅做 auto-start + ctx 归一化 + `LifecycleExecutor.execute` 委托 | ❌ 生产未激活（pipeline runtime=None / 依赖配置） |
| `tick()` | runtime_core.py:1072 | 周期心跳：状态衰减 + `_maybe_decide` 双层决策 + AutonomousDecisionLayer/Scheduler + 每 5 分钟 `_save_state` | ❌ 无驱动线程 |
| `start_lifecycle()` | :4502 | 生命周期启动 | 随 process auto-start |
| 域更新入口 | `record_relationship_interaction`(:3236)、`refresh_self_model*`(:2225/:2269)、`accept_self_model_suggestion`(:2146) 等 | Stage9/14 消费的细粒度能力口 | ✅ 经 RuntimeBridge 从 Orchestrator 调用 |
| 维护入口 | `run_memory_consolidation`(:1538)、`run_self_reflection`(:1556)、`run_scheduled_reflection`(:2849)、`handle_completed_experience`(:1492) | 认知维护能力 | ⚠️ 仅 AutonomousScheduler（disabled）与 AutonomousDecisionLayer 调用 → **休眠** |

### 1.2 17 阶段执行顺序（lifecycle_executor.py:19-35，SPEC v0.2 冻结）

```
00 CONTROL_CHECK → 01 RECEIVE_EVENT(+_on_event) → 02 MEMORY_RETRIEVAL
→ 03 EMOTION_UPDATE → 04 GROWTH_EVALUATION → 05 PERSONALITY_UPDATE
→ 06 PERSONALITY_CONTEXT_BUILD
→ 07 PERCEPTION_OBSERVATION (no-op) → 08 PERCEPTION_ANALYSIS (no-op)
→ 09 SELF_MODEL_BUILD (no-op) → 10 SELF_MODEL_EVOLUTION (no-op)
→ 11 SELF_MODEL_REFLECTION (no-op) → 12 SELF_MODEL_VALIDATION (no-op)
→ 13 SELF_MODEL_PERSISTENCE (no-op)
→ 14 RESPONSE_GENERATION → 15 GUARD_CHAIN → 16 RESPONSE
```

**关键发现**：阶段 07~13（感知两阶 + SelfModel 五阶）**默认 no-op**——认知循环的槽位已在冻结表里预留，但实现从未接线。tests/test_runtime_stage_contract.py 用 17 断言锁住此表。

### 1.3 Runtime Capability Matrix

| 能力 | 当前入口 | 调用者 | 是否治理 | 是否异步 | 是否持久化 | 缺陷 |
| --- | --- | --- | --- | --- | --- | --- |
| 记忆检索/保存 | Stage 02 / Orchestrator Step 10-11（MemorySystem） | Orchestrator | ❌（memory 域 adapter 未建） | ❌ 同步 | ✅ data/memory* | 检索在两套链路重复实现（pipeline + orchestrator fallback） |
| 情绪更新 | Stage 03（`_record_emotion_*`）/ Orchestrator Step 8 | Lifecycle / Bridge | ⚠️ B.7 flag 关 | ❌ | ✅ emotion_state | flag 关闭时 legacy 直写 |
| 关系更新 | Stage 09/14（`record_relationship_interaction` → `_relationship_update`） | Bridge | ⚠️ B.9 flag 关（`_relationship_update_allowed` 身份门常开） | ❌ | ✅ 三套权威文件（B.8 审计） | 三套并行状态权威未收敛 |
| 成长评估 | Stage 04 / Orchestrator Step 14.5（GrowthPipeline.incremental_update） | Orchestrator | ⚠️ B.5 flag 关 | ❌ | ✅ growth history/proposals | Stage 04 与 Step 14.5 双入口；GrowthPipeline 失败静默隔离 |
| SelfModel 更新 | Step 14.6 治理链（Phase 4.0.3）+ resolver refresh + Bridge 各入口 | Orchestrator / Bridge | ⚠️ Phase 4.0.3 旧治理活跃；B.13 Gateway flag 关 | ❌ | ✅ data/self_model.json | **17 阶段中 SelfModel 五阶段全 no-op**；AUTO_APPLY 语义待收敛（B.13 债务） |
| 回复生成 | Stage 14-16 / Orchestrator Step 12-13（ResponseEngine+LLM） | 双链路 | ❌（guard_chain 有守卫无治理） | ❌ | 历史 ✅（_persist_history） | 双链路行为差异靠 fallback 弥合 |
| 状态持久化 | `_save_state`(:1319) | tick（无驱动） | ❌ | ❌ | ⚠️ 仅 world_state/self_state | **不含**情绪/关系/记忆/governance —— 不是认知快照 |
| Context 持久化 | RuntimePersistenceHook（lifecycle/persistence_hook.py，Phase 6.2） | pipeline run 旁路 | ❌ | ❌ | ✅ data/runtime_context | 旁路桥接，仅 RuntimeContext 不含域状态 |
| 事件发布 | RuntimeEventBus(:351)/DomainRuntimeEventBus(:352) + cycle_event 常量（Phase C.1） | core.event_bus 单例 | ❌ | ❌（同步 emit） | ❌ 仅内存 history | 见 Phase 3-1 |
| 决策/行动 | `_maybe_decide` → DecisionEngine + CognitiveEngine → ActionDispatcher | tick | ❌ | ❌ | 部分（action_persistence） | 无 tick 驱动 → 全休眠 |

### 1.4 Persistence 调用总览

- **tick 路径**：`_save_state` → world_state + self_state（atomic_write_json，仅此两项）。
- **pipeline 路径**：RuntimePersistenceHook → RuntimeContextStorage（data/runtime_context）。
- **域自持久化**：MemorySystem / EmotionRepository / RelationshipRepository / SelfModelStore / growth proposal store 各自落盘（B.12 SM-18 快照体系旁路存在）。
- **关闭路径**：api_server `atexit → RuntimeBridge.shutdown()`（:644/:653）；main.py LongLoop `checkpoint_provider=None`（"具体 checkpoint 落盘由后续阶段提供"）。
- **结论：无统一的 Shutdown Snapshot 能力**——进程退出只关 bridge，不做认知状态终照。

---

## Phase 2 — Orchestrator 审计

| 问题 | 结论 | 证据 |
| --- | --- | --- |
| 是否承担生命周期编排？ | **否（半）**：Orchestrator 自身是一条固定 14+ 步业务链（B.12 审计：身份→记忆→人格→情绪→回复→记忆保存→情绪更新→关系更新→Step14 持久化→Step14.5 成长→Step14.6 治理→Phase5.0-A self_model_orchestrator→guard→返回）；真正声明式生命周期编排在 RuntimeCore/LifecycleExecutor，但生产经 RuntimePipeline 以 orchestrator 为 fallback 实现 | orchestrator.py:1060 `process()`；runtime_pipeline.py fallback 审计字段（path="orchestrator_pipeline_fallback"） |
| 是否只是回复生成？ | **否**：它是记忆/情绪/关系/成长/SelfModel 六域更新的实际汇聚点（Step 8-14.6），认知副作用集中于此 | Step 14.5/14.6 代码块 |
| 是否有事件总线？ | **有（薄）**：`self.event_bus = get_event_bus()`（:450，src/events/bus 单例）+ publish_event；但发布为主、订阅稀疏；RuntimeCore 侧另有两层 bus（见 Phase 3-1），两套 bus 未统一 | orchestrator.py:31/:450 |
| 是否支持后台任务？ | **弱**：`_run_async_safe`（:586）只是同步包装 asyncio（浏览器/屏幕上下文）；无常驻后台任务、无任务队列；Phase 4.1 initiative_sender 是跨进程文件轮询（Step 14 持久化历史供其读取），非 Orchestrator 内部后台 | :586-601、:2541+ perform_* 系列 |

**定位结论**：Orchestrator 是**认知副作用汇聚型单轮管线**，不是生命周期编排器。编排职责已被 RuntimePipeline/LifecycleExecutor 接管（声明式），但生产 runtime=None / fallback 主导，形成"编排壳在 Runtime、认知实体在 Orchestrator"的双头结构。

---

## Phase 3 — 缺失能力审计（六项）

### 3.1 Event Bus —— **存在但未成体系**
- 已有：core.event_bus（单例，orchestrator 用）；RuntimeEventBus（Phase 3.5.23 包装，legacy alias 兼容）；DomainRuntimeEventBus（RuntimeDomainEvent 标准 schema）；cycle_event.py 常量 + cycle_adapter.py（Phase C.1，九个 cycle_* 事件类型）。
- 缺失：① 订阅方稀疏——事件多为"发了没人听"；② 无持久化事件历史（仅内存 get_history）；③ cycle 事件与 17 阶段的 STAGE_TO_EVENT 映射已定义但 Stage 执行器未发布；④ 跨进程（api_server / Agent Server / CLI）无总线共享。

### 3.2 Cognitive Timeline —— **碎片存在，无统一索引**
- 已有：runtime_trace_schema.py + trace_recorder.py（链路追踪）、status_tracker.py、growth_history_view、development_history、audit_log、admin event_stream_provider（"integration.self_model.*" 事件）。
- 缺失：无跨域时间线索引（"羽依今天经历了什么/变化了什么"无法一次查询）；trace 与 domain event 与 governance proposal 三套 id（trace_id/mutation_id/request_id）已可关联（B.10 契约）但无 Timeline 聚合器。

### 3.3 Reflection Engine —— **实现最多、激活为零**
- 已有三套：src/runtime/reflection_engine.py + reflection_evaluator.py + reflection_scheduler.py + reflection_growth_bridge.py；src/runtime/self_reflection_engine.py；src/runtime/self_model/reflection/{reflection_engine, reflection_record, reflection_store, consistency_checker, contradiction_detector}.py。
- 入口：RuntimeCore.run_self_reflection(:1556) / run_scheduled_reflection(:2849)；Stage 11 SELF_MODEL_REFLECTION = no-op。
- 调用者：仅 AutonomousScheduler（enabled=False）与 AutonomousDecisionLayer（可选注入）。**生产零激活**。ReflectionRecord 数据结构已就绪（runtime/self_model/reflection/reflection_record.py）。

### 3.4 Background Consolidation —— **能力就绪、无调度**
- run_memory_consolidation(:1538) 存在；AutonomousScheduler.memory_maintenance_interval_ticks=10 配置存在；GrowthPipeline.run_full_consolidation 存在（main.py 测试代码手动调用）。
- 缺失：无任何生产调度驱动（无 tick 线程 → scheduler.on_tick 永不执行）。

### 3.5 Shutdown Snapshot —— **缺失**
- 现状：atexit 只关 RuntimeBridge；LongLoop checkpoint_provider=None（框架占位）；_save_state 仅 world/self state。
- 缺失：退出时对 memory/emotion/relationship/growth/self_model/governance-pending 的统一终照与恢复点（重启后 NEED_REVIEW 队列、反思中间态全部丢失或依赖各域自身落盘时序）。

### 3.6 Daily Self Review —— **缺失**
- 无日期语义的回顾触发（AutonomousScheduler 只有 tick 计数间隔）；无"昨日总结/今日自我评估"产物；reflection_growth_bridge 可把反思接成长，但缺 daily 聚合视图与触发器。

---

## Phase 4 — Runtime Cognitive Loop 设计（只设计，不实现）

总原则：**不新建平行系统**（AGENTS.md Rule 4）——四层循环全部锚定既有组件，只补"驱动 + 接线 + 槽位实现"。

```
┌─────────────────────────────────────────────────────────────┐
│ L1 Conversation Loop（秒级，已活跃）                          │
│ request → RuntimePipeline → [17-Stage｜orchestrator fallback]│
│ → reply → 六域更新（memory/emotion/relationship/growth/      │
│ self_model）→ DomainEventBus 发布 cycle_* 事件 →             │
│ RuntimePersistenceHook 落 RuntimeContext                     │
├─────────────────────────────────────────────────────────────┤
│ L2 Maintenance Loop（分钟级，休眠待激活）                     │
│ CognitiveScheduler.tick → RuntimeCore.tick() →               │
│ 状态衰减 + _save_state + AutonomousScheduler.on_tick →      │
│ memory_consolidation / identity_check / proposal_evaluation │
├─────────────────────────────────────────────────────────────┤
│ L3 Reflection Loop（小时/日级，槽位已预留）                   │
│ Scheduler 触发 → run_scheduled_reflection →                  │
│ reflection_engine（三套合一路由）→ ReflectionRecord 落盘 →   │
│ SelfObservation（B.11）→ GrowthProposal 种子（B.12 判定表）│
│ + Stage 11 SELF_MODEL_REFLECTION 从 no-op 转 governed 实现  │
├─────────────────────────────────────────────────────────────┤
│ L4 Growth Loop（日/周级，部分活跃）                           │
│ GrowthPipeline 事件累积 → evaluator → GrowthProposal →       │
│ Governance（MutationGateway 五道 + ProposalManager 生命周期）│
│ → approve（人工 reviewer）→ ApplyAdapter → 域状态更新 →      │
│ SelfModel 叙事 + development_history（Timeline 输入）        │
└─────────────────────────────────────────────────────────────┘
横切：DomainEventBus（层间解耦）｜CognitiveTimeline（观测）｜
ShutdownSnapshot（安全停机）｜Governance（全变更单入口）
```

各层既有资产与缺口：
- **L1**：活跃。缺口 = Stage 07-13 no-op；cycle_* 事件未随 Stage 发布。
- **L2**：组件全在（tick/decay/scheduler/consolidation）。缺口 = 驱动线程（api_server 内单例 CognitiveScheduler，遵守"状态写盘由 api_server 独占"的既有注释契约）。
- **L3**：数据结构与引擎全在。缺口 = 调度触发 + Stage 11 实现 + Reflection→Observation→Proposal 桥。
- **L4**：治理链完备（B.2~B.13）。缺口 = flag 全关 + 审批 UI + AUTO_APPLY 收敛（B.13 债务）。

---

## Phase 5 — B.15 实施路线（建议顺序，每步独立 flag + A/B 回归）

| # | 交付物 | 锚点（不新建平行系统） | 核心工作 | 默认状态 |
| --- | --- | --- | --- | --- |
| 1 | **Runtime Event Bus 激活** | DomainRuntimeEventBus + cycle_event 常量（Phase C.1 已定义）+ LifecycleExecutor | Stage 执行器按 STAGE_TO_EVENT 发布 cycle_* 事件；订阅方注册表（先接 Governance 留痕与 Timeline）；事件历史落 governance 目录 | flag off |
| 2 | **ReflectionRecord 接线** | runtime/self_model/reflection/{reflection_engine, reflection_record, reflection_store}（已实现） | Stage 11 从 no-op 改为受 flag 控制的 governed 反思（输出 ReflectionRecord + SelfObservation 种子）；三套反思引擎路由统一 | flag off |
| 3 | **CognitiveTimeline** | trace_id/mutation_id/request_id 三键（B.10 契约）+ development_history + governance 落账 | 只读聚合器：按时间合并 domain events / governance decisions / reflections / growth history → "羽依认知时间线"查询（GovernanceInspector 扩展） | 只读，无 flag |
| 4 | **Scheduler** | RuntimeCore.tick + AutonomousScheduler（enabled=False）+ supervisor.py | api_server 内单例 CognitiveScheduler 线程（遵守进程独占契约）：驱动 L2/L3；AutonomousScheduler 改由其持有 | enabled=false |
| 5 | **SnapshotManager** | runtime/self_model/persistence/snapshot_manager.py（已实现，非 Authority）+ _save_state | ShutdownSnapshot：atexit/信号时对六域 + governance pending 做统一终照（各域已有 save 口，编排而非重写）；启动恢复对齐 _restore_self_model_authority 模式 | flag off |
| 6 | **Governance 接入** | B.10/B.11/B.13 全链 + GovernanceConfig shadow 模式 | L2/L3/L4 的全部状态变更走 MutationGateway（shadow 先行记录 verdict 不拦截 → enforce 灰度）；Daily Self Review = L3 的日级触发特例（日期聚合 ReflectionRecord → 周报式 Timeline 视图） | shadow 设计，不开启 |

实施顺序理由：1（事件）先行是 2/3 的观测基础；2（反思）是 L3 核心缺口；4（调度）依赖 1/2 有东西可驱动；5（快照）独立可并行；6（治理接入）最后灰度。

---

## 最终验证

- `git rev-parse HEAD` = `225d0408e89b475605d62b0435f85a9954ffb024`（不变）
- `git status --short -- data/` 为空；src/tests 无新增无修改（工作区变更数与本任务开始时一致：256）
- 本任务唯一文件系统产出：`docs/architecture/runtime_cognitive_loop_audit.md`

## 审计结论摘要

1. **Runtime Capability Matrix**：11 类能力中 4 类生产活跃（记忆/情绪/关系/SelfModel 经 Orchestrator 汇聚）、1 类双链路并存（回复）、6 类休眠或缺失（决策/tick 持久化/事件订阅/整理/反思/快照）。
2. **最核心架构事实**：17 阶段生命周期表中阶段 07-13（感知+SelfModel 五阶）默认 no-op；生产无 tick 驱动线程；RuntimePipeline 以 orchestrator 为 fallback 实现——"编排壳在 Runtime、认知实体在 Orchestrator"。
3. **六项缺失能力**中 Event Bus/Reflection/Consolidation 属"已建未激活"，Cognitive Timeline/Shutdown Snapshot/Daily Review 属"真缺失"。
4. **四层认知循环设计**全部锚定既有资产（零新建平行系统），B.15 六项路线按事件→反思→时间线→调度→快照→治理灰度排序。
