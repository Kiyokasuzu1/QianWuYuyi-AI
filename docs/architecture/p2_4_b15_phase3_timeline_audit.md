# P2.4-B.15 Phase 3 — Phase 1 审计：事件投影审计（只读）

> 日期：2026-08-20
> 性质：只读审计，无任何代码 / 数据修改
> 基线：B.15 Phase 2 完成态（HEAD 225d040）

## 0. Phase 0：B.15 Phase 2 基线快照

| 项目 | 值 |
| --- | --- |
| HEAD | 225d040 chore(data): organize data artifacts and archive legacy records |
| data/ 状态 | clean |
| cognitive_timeline.py | md5 9a348252…（Phase 2 完成态） |
| cognitive_activation.py | md5 f51233d4…（Phase 2 完成态） |
| lifecycle_executor.py | md5 ae400300…（Phase 1 起未变） |
| Phase 2 测试 | tests/test_cognitive_timeline.py 17/17 通过 |

## 1. 当前已有 cycle 事件有哪些

### 常量层（全部已有，不新增）

**cycle_event.py（Phase C.1）**——9 个主事件 + 别名：
cycle_started / cycle_memory_completed / cycle_emotion_completed /
cycle_personality_completed / cycle_relationship_completed /
cycle_growth_completed / cycle_decision_completed / cycle_completed / cycle_failed

**runtime_event_schema.py（Phase 3.5.23）**：reflection_started /
reflection_completed（另有 experience_created / memory_created /
evaluation_completed / proposal_* / identity_changed / emotion_changed /
relationship_changed，均不在本阶段投影范围）

### 实际发布层（B.15 Phase 1 现状）

LifecycleExecutor.execute() + Stage 11 adapter 实际发布的事件（全部经
`publish_cycle_event` → core.domain_event_bus.emit）：

| 事件 | 发布点 | payload |
| --- | --- | --- |
| cycle_started | execute() 循环前 | {"stages": 17} |
| cycle_memory_completed | STAGE_CYCLE_EVENTS[记忆阶段] | {"stage": name} |
| cycle_emotion_completed | 情绪阶段 | {"stage": name} |
| cycle_growth_completed | 成长阶段 | {"stage": name} |
| cycle_personality_completed | 人格上下文阶段 | {"stage": name} |
| cycle_failed | 阶段异常分支 | {"stage", "error"} |
| cycle_completed | execute() 循环后 | {"stages": N} |
| reflection_started | Stage 11 adapter | {"stage": "SELF_MODEL_REFLECTION"} |
| reflection_completed | Stage 11 adapter | {"stage", "reflection_id", "priority"}（Phase 2 flag 开启时附 timeline_event_id） |

**注意**：任务书提到的 "stage_completed" 字面量在代码库中不存在——它对应
STAGE_CYCLE_EVENTS 的 4 个阶段完成事件族（memory/emotion/growth/personality），
不新增事件类型（B.15 一贯约束）。

## 2. 哪些事件有完整 payload

**全部 9 个已发布事件都有完整 payload**（见上表）。事件对象层
（RuntimeDomainEvent，runtime_event_schema.py:46）字段齐全：
event_id / event_type / timestamp / source / source_id / payload / related_ids，
且 RuntimeEventBus.emit 返回该对象。

**现状缺口**：`publish_cycle_event`（cognitive_activation.py）目前
**不传 source_id / related_ids**，也不携带 trace 标识。trace 来源可用
`RuntimeContext.session_id`（runtime_context.py:52，已有字段）。

## 3. 哪些事件适合进入 Timeline

适合（生命周期边界 + 阶段完成 + 反思域，共 9 个）：

| 事件 | Timeline domain |
| --- | --- |
| cycle_started / cycle_completed / cycle_failed | lifecycle |
| cycle_memory_completed | memory |
| cycle_emotion_completed | emotion |
| cycle_growth_completed | growth |
| cycle_personality_completed | personality |
| reflection_started / reflection_completed | reflection |

不适合（本阶段不投影，留后续 Phase）：
- experience_created / memory_created 等 runtime_core 其他 emit 路径
  （非生命周期阶段事件，且 `_emit_domain_event` 受 `_event_driven_enabled`
  门控，属于另一条链路）
- cycle_relationship_completed / cycle_decision_completed：常量存在但
  B.15 阶段未发布（无发布点，投影了也永远为空）

## 4. 是否存在重复事件风险

**存在一个真实风险 + 一个已解决项：**

1. **reflection_completed 双重投影风险（已设计解决）**：Phase 2 已将
   ReflectionRecord 投影为节点 `event_id = "tl_" + reflection_id`。
   Phase 3 若再投影 reflection_completed 事件，将产生第二个语义相同节点。
   **解决方案**：投影层对 reflection_completed 采用同一确定性规则
   `event_id = "tl_" + payload["reflection_id"]` → CognitiveTimeline 按
   event_id 幂等去重 → 两阶段投影合并为**单节点**（Phase 2 record 节点
   先追加且带 parent 链接，去重保留它）。
2. **无 reflection_id 的事件无冲突**：cycle_* / reflection_started 使用
   RuntimeDomainEvent.event_id（每次 emit 唯一）→ 天然无重复。
3. **EventBus 历史与 Timeline 双轨**：core EventBus 无订阅者也记录历史
   （event_bus.py:98，上限 1000）；Timeline 是独立投影，两者不同源、
   不同生命周期，不构成重复（Timeline 是可选持久语义，EventBus 历史是
   总线自身行为）。

## 5. 接线点结论

- **不改变 EventBus**（runtime_event_bus.py 零修改）
- **不改变事件 schema**（RuntimeDomainEvent 零修改）
- 投影钩子放在 `publish_cycle_event`（唯一发布入口）内，flag
  `timeline_event_projection_enabled=False` 门控 → 关闭时与 Phase 2
  行为逐字节等价；无订阅者时 EventBus.emit 无副作用，投影 flag 关闭
  时零影响。
- trace 来源：`RuntimeContext.session_id` 经 publish_cycle_event 新增
  keyword-only 参数 `trace_id=""` 传入（向后兼容：既有 3 位置参数调用不变）。
