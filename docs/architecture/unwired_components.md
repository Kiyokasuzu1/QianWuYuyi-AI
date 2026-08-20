# P2.3-D 孤儿代码登记（unwired_components）

> 状态：**只读登记**　日期：2026-08-19　分支：develop/v1.1　HEAD：225d040
> 原则：**只登记、不删除**。每项处理建议均须 P2.3-A 审批后执行。
> "当前状态" 栏以本次勘察 grep 核实为准。

---

## 任务指定项

### 1. growth_loop
```
模块:      src/growth/growth_loop.py
存在位置:  src/growth/growth_loop.py（依赖 src/core/yuyi_cognitive_core + contracts/cognitive_event_types）
设计目的:  基于 Cognitive Core 的后台成长循环（事件驱动的人格成长评估闭环）
当前状态: UNWIRED——唯一非测试引用是 src/runtime/integration/adapters/growth_adapter.py
          （integration 层整体默认关闭）；tests/test_phase_5_0_d2 驱动
未来处理建议: 保持冻结；待 growth 双路径收敛决策后，要么接入主链要么正式退役
```

### 2. growth/sync
```
模块:      src/growth/sync/（proposal_sync_manager.py / retry_queue.py / retry_worker.py）
存在位置:  src/growth/sync/
设计目的:  growth proposal 的同步管理与重试队列
当前状态: UNWIRED——仅 tests/test_phase_5_5_1_hotfix.py 与 test_phase_5_5_2_stability.py 引用
未来处理建议: 保持冻结；P2.3-B 做审批队列持久化时评估复用其 retry 设计
```

### 3. proposal_events
```
模块:      src/growth/proposal_events.py
存在位置:  src/growth/proposal_events.py
设计目的:  growth proposal 生命周期事件（热修复 Phase 5.5.1 产物）
当前状态: UNWIRED——全仓库仅 tests/test_phase_5_5_1_hotfix.py / test_phase_5_5_2_stability.py 引用
未来处理建议: 保持冻结；若总线收敛后 proposal 生命周期通知需求真实出现，再评估接线
```

### 4. integration host
```
模块:      RuntimeIntegrationHost（runtime_integration_host.py + adapters/ + tasks/ + event_bridge + event_store）
存在位置:  src/runtime/integration/
设计目的:  Phase 5.0-D2 统一集成宿主——各模块 lifecycle task 的统一调度与事件桥
当前状态: UNWIRED——runtime_core.py:544 config `runtime_integration_enabled` 默认 False
          （959-964 才构造，972 才注册模块）；仅 tests/test_phase_5_0_d2/d3 系列驱动；
          admin dashboard 存在对缺失 get_default 的悬空引用
未来处理建议: 登记为 UNWIRED；修复 admin 悬空引用；宿主去留与 RuntimeCore 二选一决策联动
```

### 5. pipeline_server
```
模块:      pipeline_server.py
存在位置:  src/runtime/pipeline_server.py（平行 Flask 服务）
设计目的:  独立的 RuntimePipeline 服务器（Phase 7.3 产物）
当前状态: TEST_ONLY——仅 tests（test_pipeline_server / test_runtime_e2e_real /
          test_phase73_prod_smoke / test_runtime_health）引用，生产未使用
未来处理建议: 标记 TEST_ONLY 冻结；生产服务入口统一为 api_server.py 后退役
```

### 6. RuntimeBootstrap
```
模块:      RuntimeBootstrap（runtime_bootstrap.py + runtime_snapshot.py）
存在位置:  src/orchestrator/runtime_bootstrap.py
设计目的:  Phase 5.0-A SelfModel 五阶段编排器（Build→Evolution→Reflection→Validation→Persistence）引导
当前状态: UNWIRED——生产全仓库无 import（仅 tests/test_phase_5_0_c/ 引用）；
          orchestrator.py 已预留注入点 configure_self_model（默认 None，1395 门内调用）
未来处理建议: 保持冻结；接入点已存在，P2.3-A 决策是否接线（与治理链 14.6 的关系需先厘清）
```

### 7. register_builtin_handlers
```
模块:      register_builtin_handlers()（audit 桥 + proposal 桥 + experience 桥 + relationship handler）
存在位置:  src/events/handlers.py:46（经 src/events/__init__.py:18/34 导出）
设计目的:  事件→审计/提案/经历自动桥接（CT-11 契约：注册后 MEMORY_CREATED 订阅须含 experience 桥）
当前状态: UNWIRED（死接线）——生产零调用；仅 tests（test_relationship_candidate_bridge:188 等）显式调用
未来处理建议: P2.3-A 总线收敛第一步：接线或明确退役，二选一，不得继续悬置
```

---

## 补充登记项（勘察发现）

### 8. emotion decay 调用链
```
模块:      EmotionManager.update() → EmotionDecay.apply（emotion_manager.py:76/84）
存在位置:  src/emotion/emotion_manager.py；唯一调用方 src/runtime/adapters/impl/emotion_adapter_impl.py:141
设计目的:  情绪时间衰减（"情绪应该影响表达"设计的一部分）
当前状态: UNWIRED——registry 版 adapter impl 生产不装配 → 生产请求路径从不衰减
未来处理建议: P2.3-C 在请求路径或 tick 中接入 decay 调度（一行级改动，需先确认衰减参数）
```

### 9. runtime_audit_logger 通道
```
模块:      runtime_audit_logger（data/runtime_audit.jsonl）
存在位置:  src/runtime/audit_log/
设计目的:  Runtime 侧审计第二通道
当前状态: UNWIRED——目标文件从未创建，无生产写入方
未来处理建议: 与通道①（audit/record.py）合并或正式退役，禁止双通道继续并存
```

### 10. runtime_trace_schema.py
```
模块:      runtime_trace_schema（Phase 4.0 冻结 schema 契约）
存在位置:  src/runtime/runtime_trace_schema.py
设计目的:  RuntimeTraceRecorder 的阶段事件契约
当前状态: UNWIRED（契约占位）——有契约定义无生产写入方；实际写入仅 trace_recorder.py:131 record_stage 低级 API（pipeline 3 阶段）
未来处理建议: P2.3-A 追踪统一时按此契约扩写入方
```

### 11. canonical runtime.py + adapters/impl
```
模块:      RuntimeCore（17 阶段）+ AdapterRegistry + 5 个 impl adapter
存在位置:  src/runtime/runtime.py（228 行 docstring 内 import 为示例，非代码）;
          src/runtime/adapters/impl/
设计目的:  Phase 3.7 装配版 Runtime（Protocol/Adapter 解耦设计）
当前状态: UNWIRED（生产）——api_server 不启动；唯一非测试引用为 orchestrator.py:497
          桥接失败兜底分支（生产正常时不可达）；api_server.py:487 `from src.runtime.runtime
          import impl as _runtime_impl` 为死 fallback（永远 ImportError）
未来处理建议: RuntimeCore 二选一决策（legacy 权威 vs canonical 切换）独立立项；P2.3 内不动
```

### 12. lifecycle_manager（模块启停）
```
模块:      runtime/lifecycle/lifecycle_manager.py + lifecycle_bridge + policy/lifecycle/manager
存在位置:  src/runtime/lifecycle/
设计目的:  模块级生命周期启停管理
当前状态: UNWIRED——模块启停默认关闭；lifecycle_orchestrator.record_lifecycle_event 经 grep
          确认 src/ 零调用方
未来处理建议: 与 RuntimeCore 决策联动；台账期内登记冻结
```

### 13. MemoryService / MemorySystem
```
模块:      memory_service.py（registry 装配版）/ memory_system.py（legacy）
存在位置:  src/memory/
设计目的:  Memory 的适配器实现与 legacy 系统实现
当前状态: TEST_ONLY——MemorySystem 仅 src/core/（未生产启动）使用；MemoryService 仅 registry
          装配（tests）使用；生产权威是 MemoryStore
未来处理建议: 保持兼容不动；P2.3-A 后评估退役
```

### 14. src/runtime/growth/ proposal 系列
```
模块:      growth_proposal_approval / growth_proposal_history / growth_proposal_lifecycle /
          growth_proposal_runtime
存在位置:  src/runtime/growth/
设计目的:  runtime 侧 growth proposal 生命周期（Phase 4.0 R2.5x 审批闸门产物）
当前状态: UNWIRED——runtime_core 仅经 growth_history_view 注入（3676-3680）；这些模块仅 tests
          （test_growth_proposal_*）驱动
未来处理建议: 与 growth 双路径收敛决策联动；登记冻结
```

### 15. relationship_core store
```
模块:      relationship_core 磁盘存储
存在位置:  data/relationship_core/（目录不存在，磁盘未启用）
设计目的:  第三套关系存储（设计遗留）
当前状态: UNWIRED
未来处理建议: 不启用；权威已声明为 data/users/{uid}/（见 runtime_paths.md）
```

### 16. data/archive/proposals.jsonl
```
模块:      历史 growth proposal 归档
存在位置:  data/archive/proposals.jsonl（P2.2 已归档，6.7MB）
设计目的:  Growth 历史提案流
当前状态: 已归档——data/archive/README.md 声明"当前不可作为 Growth 输入"
未来处理建议: 保持归档；若 Growth 需要历史回放须先做污染清洗（P2.2 报告已评估）
```

---

## 维护规则

1. 本登记只记录"存在但不在生产路径"的组件；接线或删除动作必须 P2.3-A 审批后执行并同步更新。
2. "当前状态"以 grep 证据为准，引用行号随代码变化更新。
3. 与 wiring_status.md 交叉引用；两者冲突时以本文件 + 行号证据为准。
