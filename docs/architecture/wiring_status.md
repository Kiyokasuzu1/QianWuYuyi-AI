# P2.3-D 架构连线台账（wiring_status）

> 状态：**只读台账**——未修改任何代码/数据/配置
> 日期：2026-08-19　分支：develop/v1.1　HEAD：225d040
> 用途：P2.3-A 实施前的架构事实地图。任何 P2.3-A 决策必须先对照本台账。

## 状态分类定义

| 状态 | 含义 |
|---|---|
| ACTIVE | 生产请求路径上实际执行，且职责清晰 |
| COMPATIBILITY | 仍被生产路径调用，但身份为兼容/兜底/降级层 |
| LEGACY | 历史实现，主链已不再真实写入或调用，仅保留读取兼容 |
| UNWIRED | 代码存在且完整，但生产无调用方（孤儿/死接线/默认关闭） |
| TEST_ONLY | 仅测试引用，从未进入生产装配 |

---

## 一、核心模块台账

| 模块 | 当前实现 | 是否生产路径 | 调用入口 | 状态 |
|---|---|---|---|---|
| RuntimeCore | **双实现并存**：Legacy（src/runtime/runtime_core.py，6805 行，docstring 自标 `DEPRECATED SINCE Phase 4.0-R1`，382-398）为常驻权威；canonical（src/runtime/runtime.py，17 阶段 Protocol/Adapter 状态机）生产不启动 | Legacy：**是**（常驻进程 + 60s tick）；canonical：否 | Legacy：api_server.py:377 `_init_runtime_bridge` → RuntimeBridge.initialize → `_load_state`（1019-1052）+ tick；orchestrator 经 OrchestratorRuntimeBridge 共享同一实例（orchestrator.py:486-505）。canonical：仅 orchestrator.py:497 桥接失败兜底分支 `from src.runtime.runtime import RuntimeCore as _RC`（生产永不触发）+ 测试 | Legacy=ACTIVE；canonical=UNWIRED（生产） |
| RuntimePipeline | src/runtime/runtime_pipeline.py，`run()` 598，Phase 7.2 聊天主入口 | **是**（config `runtime.enabled=true`） | api_server.py:387 `_init_phase72_pipeline`（427-470，装配 TokenOptimizer+PersistenceHook）；聊天端点三级降级第 2 级；内部：`Runtime.process()` 优先（761，Phase 4.0.2 17 阶段）→ 空/异常回退 `Orchestrator.process()`（820，5B） | ACTIVE |
| Orchestrator | src/orchestrator.py（2619 行），类自标 `[DEPRECATED]`（115-124），15 步 `process()`（1060） | **是**（作为 pipeline 兜底 + `/initiative` 专用路径） | RuntimePipeline 5B fallback（820）；pipeline 不可用时 api_server 直调；`/initiative` 端点只走 Orchestrator（api_server.py:1007-1041） | COMPATIBILITY |
| Memory | 三实现：MemoryStore（权威，data/memory.json，memory_store.py:94，每请求全量读盘 290-339）；MemorySystem（legacy，仅 src/core/ 使用）；MemoryService（registry 装配才用） | MemoryStore：**是**；其余：否 | 主链：orchestrator Step 2 检索 + Step 10 `memory_store.add` 直写（orchestrator.py:1246）+ `vector_memory.add_memory`（1261），`can_modify_memory` 门（1197）；admin 读面：memory_provider/memory_service；runtime_core 经 bridge 同 store | ACTIVE（MemoryStore）；MemorySystem/MemoryService=TEST_ONLY |
| Emotion | 双实例写同一文件：orchestrator 自建 EmotionManager（data/emotion_state.json 全局 + data/emotions/{uid}.json 镜像）；runtime_core 自建 EmotionManager+EmotionDynamicsEngine（runtime_core.py:952） | **是**（双实例，同一 data/emotion_state.json） | orchestrator Step 7 pre（`process_event` 2040，门 2029）+ Step 11 post（repo.filepath 覆盖 + save 2070-2081，门 2052）；runtime_core 17 阶段经自身 manager。**decay 断链**：`EmotionManager.update()`（emotion_manager.py:76，内部 decay.apply 84）生产唯一调用方是未接线的 emotion_adapter_impl.py:141 | ACTIVE；decay 调用链=UNWIRED |
| Relationship | 三层存储：① data/users/{uid}/relationship_state.json（新仓库，runtime_core 权威读写，relationship_repository.py:47/113，load 930-931、save 3319-3320）；② data/relationship_state.json（legacy，orchestrator 仅读：380 构造包装器、857-948 只读 get/snapshot）；③ data/relationship_core/（未启用，磁盘目录不存在） | ①：**是（唯一权威）**；②：只读；③：否 | 生产写入：runtime_core 17 阶段（经 relationship_repository）。orchestrator Step 12 **断链**：`rel_repo = assembled_context.get("relationship_repo")` 全仓库无写入方 → 2133-2136 early-return，RelationshipEvent/evaluator/legacy delta（2139-2164）整段在生产不可达；门 `can_modify_relationship` 在 2130（security/permission.py:93）。候选桥 RelationshipCandidateBridge 已接线（orchestrator.py:981-992，订阅 MemoryCreatedEvent） | ACTIVE（运行时侧）；orchestrator 侧关系链=LEGACY（断链）；relationship_core=UNWIRED |
| Growth | 双路径：orchestrator GrowthPipeline.incremental_update（Step 14.5，门 can_trigger_growth 1319）；runtime_core GrowthIntegrationService（懒装配，auto_accept=False） | incremental_update：**是**；IntegrationService：懒装配（3367 注入点，配置/状态门控） | orchestrator.py:404 GrowthPipeline 构造（**user_id 硬编码 366648462**，407）；proposal 落盘 data/proposals/（proposal_store.py 默认路径）；历史提案已归档 data/archive/proposals.jsonl（P2.2，声明不可作为 Growth 输入） | ACTIVE（incremental）；IntegrationService=COMPATIBILITY |
| SelfModel | 治理链 v1.0（self_model_governance.py:1-45 硬契约：`apply_change_proposal` 只能来自 AUTO_APPLY 或已批准 Proposal，无第三条直写路径）；SelfModelStore（data/self_model.json，唯一合法写入口）；Phase 5.0-A 五阶段编排器（RuntimeBootstrap）未接线 | 治理链：**是**（Step 14.6，门 1350/1384/1395）；5.0-A 编排器：否 | orchestrator.py:432 `SelfModelGovernancePolicy()` + 435 `SelfModelUpdater(governance_policy=...)` 构造；审批队列 SelfModelApprovalQueue **内存态**（self_model_governance.py:185-240，重启丢失）；编排器注入点已预留（`configure_self_model`，orchestrator.py:510 附近，默认 None） | ACTIVE（治理链）；5.0-A 编排器=UNWIRED |
| EventBus | ≥6 套并存互不相通（详见 event_bus_inventory.md）：events/bus.py（orchestrator+runtime_bridge+候选桥）；core/event_bus.py（仅 yuyi_cognitive_core + runtime_event_bus 包装）；runtime/event_bus.py + runtime/runtime_event_bus.py（runtime_core 双面包装）；personality_event_bus（runtime_core 持有，config 默认 False，runtime_core.py:531/911-922）；observer SSE；integration（孤儿） | events/bus.py：**是**；observer（pipeline event_sink）：**是**；core bus：否（cognitive_core 未生产启动）；其余：否 | 发布：orchestrator.py:31 `publish_event`；订阅：runtime_bridge.py:210-212 `subscribe_all`；管线：pipeline event_sink.emit（468 附近）；**死接线**：events/handlers.py:46 `register_builtin_handlers()` 生产零调用 | ACTIVE（events/bus.py、observer）；UNWIRED（handlers 桥、integration、core bus）；personality bus=TEST_ONLY（默认关） |
| Audit | 双通道：① record_audit_log（audit/record.py:48 → data/audit/audit_logs.jsonl，append+fsync 同步写，286 条）；② runtime_audit_logger（data/runtime_audit.jsonl，**文件从未创建**） | ①：**是**（33 处调用点）；②：否 | 调用方：orchestrator Step 0/13、self_model_audit、growth_eligibility_filter、relationship_memory、runtime/growth 系列等；**缺口**：Legacy RuntimeCore 17 阶段主循环不写审计；无 trace_id（correlation_id 实际为空）；事件→审计桥因 register_builtin_handlers 未接线而失效 | ACTIVE（通道①）；通道②=UNWIRED |

---

## 二、补充模块台账

| 模块 | 当前实现 | 调用入口 | 状态 |
|---|---|---|---|
| 身份解析 | src/security/identity.py 为权威：`resolve_identity`（188）、SANDBOX_ID=`_unknown_sender`（37）、QQ_PATTERN `^[0-9]{5,12}$`（40）；另有 identity/user_resolver.py（UserResolver，P2.1.2 接入层）与 personality/identity_resolver.py（人格维度，不同用途） | api_server.py:826/1012 `DEFAULT_RESOLVER`；orchestrator.py:43 import + 1099 解析 | ACTIVE |
| Permission Gate | security/permission.py 五扇门纯函数（can_modify_memory/emotion/personality/trigger_growth/relationship，仅 permission=="user" 放行） | orchestrator.py 8 处接线：1197(memory)/1319(growth)/1350+1384+1395(personality)/2029+2052(emotion)/2130(relationship) | ACTIVE |
| RuntimeBridge | src/runtime/runtime_bridge.py：Legacy RuntimeCore 生命周期管理（构造+start+60s tick+atexit shutdown）+ 事件桥（subscribe_all 210-212） | api_server.py:585 `_init_runtime_bridge`；orchestrator.py:489 `get_runtime_bridge()` | ACTIVE |
| RuntimeController | src/runtime/runtime_controller.py（Phase4 路径控制器） | api_server.py:390 + 522-533（`phase4_enabled=false` → 内部 No-Op）；聊天端点第 1 级（871-908，仅 phase4_enabled=true 接管） | COMPATIBILITY（生产为 No-Op） |
| TokenOptimizer | src/runtime/token_optimizer.py | pipeline 装配（api_server.py:456-464，config `token_opt.enabled=false` → 不装配） | UNWIRED（默认关闭） |
| Trace | RuntimeTraceRecorder（data/runtime_trace/trace_YYYYMMDD.jsonl，每日轮转）+ RuntimeTraceContext | 唯一生产写入方 runtime_pipeline（468 `record_stage`，3 粗粒度阶段：token_optimization/runtime_path/orchestrator_fallback，42 条）；runtime_trace_schema.py 只有契约无写入方 | ACTIVE（仅 pipeline 粗粒度）；schema=UNWIRED |
| approval_manager | src/growth/approval_manager.py:63（approve_proposal 117/reject_proposal 181/get_pending_proposals 334）；审批历史**落盘**（_save_history_to_file 550） | growth proposal 审核流；admin governance 端点 | ACTIVE（历史落盘）；SelfModelApprovalQueue=内存态 |
| governance_provider | src/admin/governance_provider.py（propose_personality_change 343 / propose_memory_action 503 / list_proposals 679 / review_proposal 713 / get_governance_provider 870） | /api/admin/governance/personality（routes.py:2538）/memory（2563）/growth（2589） | ACTIVE（治理操作面，非聊天主链） |
| self_model_store | src/personality/self_model_store.py（DEFAULT_STORAGE_PATH 99：data/self_model.json）；**唯一合法写入口 apply_change_proposal** | 治理链 AUTO_APPLY/已批准 Proposal；启动期 `set_experience_context` 直注（orchestrator.py:296）与 resolver 属性注入（262）为旁路 | ACTIVE |

---

## 三、关键断链 / 死接线速查（P2.3-A 决策依据）

| # | 位置 | 现象 | 后果 |
|---|---|---|---|
| 1 | orchestrator.py:2133-2136 | `assembled_context["relationship_repo"]` 全仓库只有读没有写 → Step 12 early-return | 关系 post 链断链，2139-2164 段生产不可达 |
| 2 | orchestrator.py:2115-2118 | `assembled_context["on_emotion_change"]` 全仓库只有读没有写 | 情绪变更回调永不触发 |
| 3 | events/handlers.py:46 | `register_builtin_handlers()` 生产零调用（仅 tests 显式调用） | 事件→审计/提案/经历桥全部失效 |
| 4 | orchestrator/runtime_bootstrap.py | RuntimeBootstrap 生产无 import（仅 tests/test_phase_5_0_c/） | Phase 5.0-A self_model 编排器未接线 |
| 5 | emotion_adapter_impl.py:141 | EmotionManager.update()（decay）唯一生产外调用方；registry impl 生产不装配 | 情绪衰减不执行 |
| 6 | runtime/audit_log/ | runtime_audit.jsonl 文件从未创建 | 审计通道②停滞 |
| 7 | api_server.py:487 | `from src.runtime.runtime import impl as _runtime_impl` 死 fallback | 永远 ImportError，无实际降级 |
| 8 | orchestrator.py:407 | GrowthPipeline user_id 硬编码 "366648462" | 多用户下 growth 归属错误（P2.2 已评估，另行裁决） |

---

## 四、台账维护规则

1. 本台账只陈述**已核实的事实**（行号 = 本次勘察核实的当前 HEAD 代码），不包含计划。
2. 任何 P2.3-A 接线/退役动作完成后，必须同步更新本台账对应行。
3. 状态变更必须提供新行号证据。
4. 与 event_bus_inventory.md / unwired_components.md / runtime_paths.md 交叉引用，四份文档保持一致。
