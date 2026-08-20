# P2.4-B.15 Phase 2 — Phase 1 审计：Reflection 生命周期审计（只读）

> 日期：2026-08-20
> 性质：只读审计，无任何代码 / 数据修改
> 上游：B.14 runtime_cognitive_loop_audit.md、B.15 Phase 1 报告

## 0. Phase 0：B 状态基线快照（审计起点）

| 项目 | 值 |
| --- | --- |
| HEAD | 225d040 chore(data): organize data artifacts and archive legacy records |
| data/ 状态 | clean（git status 为空） |
| B.15 Phase 1 文件 | lifecycle_executor.py（md5 ae400300…）、cognitive_activation.py（md5 49cf768e…）、tests/test_runtime_cognitive_activation.py（md5 78e0112d…） |
| Phase 1 测试 | 11/11 通过；stage contract 10/10 通过 |
| Phase 1 A/B | 失败集合 A=B=118，新增=0，消失=0 |

**当前 reflection / eventbus 生命周期（Phase 1 后现状）**：

```
LifecycleExecutor.execute()                [17 阶段调度，唯一实现点]
  ├─ cycle_started         发布           (flag: _lifecycle_cycle_events_enabled=False)
  ├─ 每阶段成功后 → 领域 cycle 事件 + run_stage_adapter()
  │    └─ Stage 11 flag=True 时:
  │         reflection_started 发布
  │         → ReflectionEngine.reflect(diff, source="b15_stage11",
  │                                     categories=["state_change"])
  │         → ReflectionRecord → ctx._b15_reflection_record (dict)
  │         → reflection_completed 发布 (reflection_id/priority)
  ├─ 阶段异常 → cycle_failed 发布
  └─ cycle_completed         发布
```

## 1. ReflectionRecord 当前生命周期：在哪里创建、在哪里丢失

### 创建点（4 处）

| 创建点 | 文件:行 | 触发 | 记录去向 |
| --- | --- | --- | --- |
| ReflectionEngine.reflect() | src/runtime/self_model/reflection/reflection_engine.py:293 | GrowthAuditRecord / diff dict | 返回给调用方 + engine._last_reflection |
| SelfModelReflectionEngine.reflect() | reflection_engine.py:775 | 新旧 snapshot 一致性验证 | 返回给调用方 + engine._last_reflection |
| B.15 Phase 1 Stage 11 adapter | src/runtime/cognitive_activation.py:198 | 主循环 Stage 11（flag 门控） | ctx._b15_reflection_record + EventBus payload |
| run_scheduled_reflection() | src/runtime/runtime_core.py:2849 | ReflectionScheduler 触发 | ReflectionTaskRecord（不同 schema，scheduler 专用） |

### 丢失点（关键结论）

**ReflectionRecord 没有任何磁盘持久化，也没有进入任何 append-only store 的主循环路径：**

1. B.15 Phase 1 的记录只落在 `ctx._b15_reflection_record`（单轮上下文，循环结束即丢）和 EventBus payload（无订阅者即丢）。**下一轮循环无法追溯上一轮的反思。**
2. `ReflectionStore`（reflection_store.py，内存 append-only，per-identity，FIFO limit 100）**已存在但没有任何生产调用方 append**——runtime_core 只 attach 了 personality 侧的 `SelfReflectionStore`（不同 schema，self_reflection.py:122，同样是纯内存）。
3. `src/runtime/reflection/reflection_history.py` 的 ReflectionHistory（deque，容量 128）同样是内存实现，无落盘。
4. 全链路 grep（open/json.dump/save_state/persist）确认：reflection 包内**零磁盘 I/O**。

## 2. 是否已有持久化接口

有"append 接口形态"，但**全部内存实现，无落盘**：

| 接口 | 位置 | 形态 | 磁盘? |
| --- | --- | --- | --- |
| ReflectionStore.append(record) | runtime/self_model/reflection/reflection_store.py:82 | append-only，FIFO 淘汰 | 否 |
| SelfReflectionStore.append(note) | personality/self_reflection.py:137 | append-only | 否 |
| ReflectionHistory | runtime/reflection/reflection_history.py | deque | 否 |
| EventBus.get_history() | runtime_event_bus.py:58 → core EventBus | 内存历史 | 否 |
| GovernanceProposalStore（B.10） | governance/proposal_store.py | **JSONL append-only 落盘** | 是（但属 governance 域） |

结论：reflection 域**缺一个"审计轨迹"层**——这正是 Phase 2 CognitiveTimeline 的定位。
同时要遵守任务书红线：CognitiveTimeline 默认内存实现 + 可选注入 writer，**不写 data/**。

## 3. Reflection 是否可能直接修改人格 / self_model

**审计结论：不直接修改，且修改只能走 Governance 路径。**

| 组件 | 写人格/self_model? | 说明 |
| --- | --- | --- |
| ReflectionEngine | 否 | 纯函数，只返回 record；失败返回 None |
| SelfModelReflectionEngine | 否 | 只汇总检测结果；docstring 明确"不修改 snapshot" |
| B.15 Phase 1 Stage 11 adapter | 否 | 零状态写入（Phase 1 测试锁住 snapshot 前后一致） |
| ReflectionGrowthBridge.process | 间接 | 经 growth_adapter.create_proposal_from_insight() 创建 **GrowthProposal**（治理域），docstring 明确"不自动应用任何 proposal"——这是被允许的治理路径，非直接修改 |

唯一需要长期警惕的间接通道：reflection_growth_bridge 的 proposal 创建，
受 config flag（reflection_growth_bridge_enabled，默认随 evaluator=False）门控。
Phase 2 的 CognitiveTimeline 只记录、不触碰任何这些通道。

## 4. EventBus 能否承载 reflection trace

**能，且 Phase 1 已在用。** 事实链：

- `core.domain_event_bus = RuntimeEventBus()`（runtime_core.py:352）
- RuntimeEventBus（runtime_event_bus.py:20）包装全局 `src/core/event_bus.EventBus`：
  - `emit(event_type, *, source, source_id, payload, related_ids, emit_legacy_aliases)` → 返回带 **event_id / timestamp / payload** 的 RuntimeDomainEvent
  - `get_history(event_type, limit)` 可回查
- Phase 1 已通过它发布 `reflection_started` / `reflection_completed`（EVENT_REFLECTION_*，runtime_event_schema.py:25-26 已有常量）

承载缺口：payload 是自由 dict，**没有 trace_id / parent_event_id 的标准化**——
这就是 CognitiveTimeline 节点要补的结构。EventBus 仍作为发布通道保留，
Timeline 作为可追溯的结构化投影。

## 5. 哪些字段可以成为 CognitiveTimeline 节点

TimelineEvent 契约（任务书）：event_id / trace_id / timestamp / event_type /
source / domain / evidence_refs / parent_event_id(optional)

映射来源（全部既有字段，不发明新语义）：

| TimelineEvent 字段 | 来源 |
| --- | --- |
| event_id | RuntimeDomainEvent.event_id，或 "timeline_" + uuid（节点自身 id） |
| trace_id | RuntimeContext.session_id（runtime_context.py:52，已有） |
| timestamp | ReflectionRecord.timestamp（ISO，已有） |
| event_type | 复用既有常量："reflection_completed"（EVENT_REFLECTION_COMPLETED） |
| source | ReflectionRecord.source（已有："b15_stage11" 等） |
| domain | ReflectionRecord.trigger_category / reflection_kind（identity/trait/state…） |
| evidence_refs | ReflectionRecord.source_audit_id + evidence_ids + reflection_id（全已有） |
| parent_event_id | 上游 reflection_started RuntimeDomainEvent.event_id（optional） |

结论：**所有节点字段都有既有来源，Timeline 是纯投影层**，不触碰
Personality/SelfModel/Growth 权威状态，无新增事件类型。
