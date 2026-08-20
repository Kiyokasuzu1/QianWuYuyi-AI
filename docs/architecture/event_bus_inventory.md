# P2.3-D 事件总线登记（event_bus_inventory）

> 状态：**只读登记**　日期：2026-08-19　分支：develop/v1.1　HEAD：225d040
> 原则：**只登记、不删除、不改接线**。任何事件系统的退役/合并均须 P2.3-A 审批后执行。

---

## 一、总线总览

| 总线 | 位置 / 类 | 使用者 | 是否生产 | 是否迁移目标 |
|---|---|---|---|---|
| events/bus.py | src/events/bus.py `EventBus`(5) + 模块级 `publish_event`(68)/`subscribe_event`/`get_event_bus`；无锁、无持久化 | orchestrator.py:31；runtime/runtime_bridge.py:57（subscribe_all 210-212）；relationship/relationship_candidate_bridge.py:149/158/205；runtime/event_bus.py:16（包装）；runtime/runtime.py（canonical）；runtime/interaction_recorder.py；runtime/policy/feedback/feedback_engine.py；growth/proposal/reviewer.py；events/handlers.py | **是**（生产发布面） | **是（统一主发布面）** |
| core/event_bus.py | src/core/event_bus.py `Event`(27) + `EventBus`(42)；带 request_id、通配符、锁、1000 条历史 | src/core/yuyi_cognitive_core.py:46/301/489-545；src/runtime/runtime_event_bus.py（包装） | 否（cognitive_core 未生产启动 → 间接未走生产聊天链） | 能力来源（request_id/通配符/历史/锁并入主总线） |
| runtime/event_bus.py | src/runtime/event_bus.py `RuntimeEventBus`(21)；**包装 events/bus.py** | runtime_core（self.event_bus） | 是（经由 runtime_core） | 退役（薄包装，收敛到 events/bus.py） |
| runtime/runtime_event_bus.py | src/runtime/runtime_event_bus.py `RuntimeEventBus`(20)；**包装 core/event_bus.py** + LEGACY_EVENT_ALIASES | runtime_core（domain_event_bus） | 是（经由 runtime_core） | 退役或保留别名兼容期 |
| personality_event_bus | src/runtime/personality_event_bus.py `PersonalityEventBus`(49)；5 分类（memory/growth/reflection/identity/relationship）、可持久化历史 | runtime_core 持有（530）；config `personality_event_bus_enabled` **默认 False**（531/911-922；config.yaml 无此 key）；发布辅助 4507-4724 | 否（默认关闭） | 否（保持禁用；持久化能力设计参考） |
| observer（SSE） | src/runtime/observer/（event_sink.py + cognitive_hooks.py + pipeline_hooks.py）；内存队列 → admin dashboard | runtime_pipeline（event_sink）；personality_resolver/emotion_manager/memory_system（认知 hooks）；admin/dashboard/runtime_events_router.py | **是**（pipeline event_sink） | 否（观察/展示层，非领域总线） |
| integration 事件 | src/runtime/integration/（integration_event.py 18 种 IntegrationEvent + event_bridge + runtime_integration_host + tasks/）；config `runtime_integration_enabled` **默认 False**（runtime_core.py:544/959-964） | 仅 tests/test_phase_5_0_d2/d3 系列驱动；admin dashboard 存在对缺失 `get_default` 的悬空引用 | 否（默认关闭） | 否（标记 UNWIRED） |
| events/handlers.py 桥 | src/events/handlers.py `register_builtin_handlers`(46)：audit 桥 + proposal 桥 + experience 桥 + relationship handler | **零调用方**（仅 tests 显式调用） | 否（死接线） | 待 P2.3-A 决策（接线或退役） |

**补充事实**：control/、runtime/goal/、runtime/reflection/、runtime/proactive/ 模块经本次勘察核实**没有任何 event_bus import**——它们不走任何现役总线（goal 等经 runtime/goal/adapter/goal_event_emitter 发 IntegrationEvent 到 integration 层，而该层默认关闭）。

---

## 二、逐总线详情

### 1. src/events/bus.py（生产发布面）
- 语义：YuyiEvent（name + payload），进程内同步分发，无锁（GIL 依赖）、无历史、无持久化。
- 生产事件流：orchestrator 发布 message.received/responded、memory.created、relationship.changed 等 → RuntimeBridge `subscribe_all`（runtime_bridge.py:210-212）转发给 Legacy RuntimeCore；RelationshipCandidateBridge 订阅 MEMORY_CREATED 生成关系候选。
- 结论：**当前事实上的统一发布面**，但缺 request_id、历史、持久化、通配符。

### 2. src/core/event_bus.py（能力最全、事实未生产）
- 能力：Event 带 request_id/trace 语义、通配符订阅、线程锁、1000 条环形历史。
- 唯一业务使用者 yuyi_cognitive_core.py（其上层 growth_loop 为 UNWIRED）。
- 结论：能力最接近"目标总线"，但发布面零生产使用。P2.3-A 收敛时建议**取其能力、保留 events/bus.py 的发布语义**，避免推倒现有订阅方。

### 3./4. runtime 双包装（runtime_core 双面）
- runtime_core 同时持有 `self.event_bus`（→events/bus.py）与 `self.domain_event_bus`（→core/event_bus.py），legacy 别名表保证历史事件名兼容。
- 结论：纯包装层，收敛时应先迁移 runtime_core 的订阅/发布调用点，再移除包装。

### 5. personality_event_bus（默认关闭）
- 5 分类持久化历史总线，runtime_core 提供完整 API 面（publish/subscribe/history/replay，4507-4724）但全部以 `if not self.personality_event_bus` 短路。
- 结论：保持禁用；其"分类 + 历史持久化"设计是未来统一总线的持久化参考实现。

### 6. observer（SSE 展示层）
- pipeline 的 event_sink 把阶段事件推给内存队列，admin dashboard 经 SSE 消费。
- 结论：职责 = 人类可观测性，**不是**领域总线；统一总线后应改为订阅主总线而非独立通道。

### 7. integration 事件（孤儿）
- Phase 5.0-D2 的完整集成宿主（adapters + lifecycle tasks + event_bridge + store），runtime_core 中由 `runtime_integration_enabled`（默认 False）门控，生产从未启动；admin 存在悬空引用。
- 结论：整体登记为 UNWIRED，不动代码；悬空引用修复列入 P2.3-A 讨论。

---

## 三、迁移策略声明（供 P2.3-A 参考，本阶段不执行）

1. **禁止删除任何事件系统**（本任务硬约束，P2.3-A 前持续有效）。
2. 目标形态（草案）：一个统一总线 = events/bus.py 的发布语义 + core/event_bus.py 的 request_id/通配符/锁/历史能力 + personality_event_bus 的持久化设计；observer 改为订阅方。
3. 收敛顺序建议：先修 register_builtin_handlers 死接线（打通事件→审计）→ 迁移 runtime_core 双包装调用点 → 退役包装层 → 最后评估 core bus 与 personality bus 去留。
4. 每次收敛独立提交、独立回归（沿用 P2.1.3 stash 基线法），禁止一次提交删除多个系统。
