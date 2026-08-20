# P2.4-B.15 Phase 2 报告：Runtime Reflection → 可审计认知事件（Cognitive Timeline 基础层）

> 日期：2026-08-20
> 基线：B.15 Phase 1 完成态（HEAD 225d040 + Phase 1 三文件）
> 依据：docs/architecture/p2_4_b15_phase2_reflection_audit.md（本阶段 Phase 1 审计）

## 0. 结论

Phase 2 完成。Reflection 从"可调用能力"升级为"可审计认知事件"：
新增 CognitiveTimeline 最小层（纯内存 + 可选 writer，零磁盘 I/O），
Stage 11 反思链在 flag 开启时投影 TimelineEvent 节点。全部 flag 默认 False，
未开启任何生产行为。

- 新增测试：17/17 通过
- 回归：Phase 1 套件 11/11、stage contract 10/10、B.10/B.11/B.13 治理套件 334/334
- A/B 回归：失败集合完全一致（A=118，B=118，新增=0，消失=0）
- data/ 零修改

## 1. Phase 1 审计结论摘要

（详见 p2_4_b15_phase2_reflection_audit.md）

1. **ReflectionRecord 创建点 4 处、丢失点 1 个**：Phase 1 的反思记录只落在
   `ctx._b15_reflection_record`（单轮即丢）与 EventBus payload（无订阅者即丢）。
   ReflectionStore / SelfReflectionStore / ReflectionHistory 均已存在但**纯内存、
   无落盘、无生产调用方**——reflection 域缺审计轨迹层。
2. **持久化接口**：只有 append 形态的内存实现；唯一落盘的 JSONL 是 governance 域
   的 ProposalStore（不属于 reflection）。
3. **Reflection 不直接修改人格/self_model**：ReflectionEngine /
   SelfModelReflectionEngine 纯输出；唯一间接通道 ReflectionGrowthBridge
   走 GrowthProposal（治理路径），且受 config flag 门控。
4. **EventBus 能承载 trace**：core.domain_event_bus 的 emit 返回带 event_id /
   timestamp / payload 的 RuntimeDomainEvent；缺 trace_id / parent 标准化。
5. **节点字段映射**：TimelineEvent 全部字段都有既有来源（session_id / record
   时间戳 / 已有事件常量 / evidence 字段），纯投影层。

## 2. Phase 2：CognitiveTimeline 最小层

新文件 `src/runtime/cognitive_timeline.py`：

- **TimelineEvent**（dataclass，冻结字段）：event_id / trace_id / timestamp /
  event_type / source / domain / evidence_refs / parent_event_id(optional)；
  to_dict / from_dict；evidence_refs 自动规范化（去空、去重不重复）。
- **CognitiveTimeline**：append-only 内存实现，容量上限 FIFO（默认 1000）；
  **幂等去重**：同一 event_id 重复 append 不产生重复节点（对应"重复 trace_id
  不产生重复节点"治理边界）；可选注入 writer（Callable[[dict], Any]）；
  **fail-open**：append / writer 异常绝不上抛，内存记录保留，只计
  writer_fail_count / last_error。
- **timeline_event_from_reflection(record_dict, trace_id, parent_event_id)**：
  ReflectionRecord → TimelineEvent 纯投影。event_id = `"tl_" + reflection_id`
  （同一反思重放幂等）；event_type 复用已有常量 `reflection_completed`；
  evidence_refs = source_audit_id + reflection_id + evidence_ids。
- **flag**：`_timeline_recording_enabled` 默认 False；get / set / reset /
  get_timeline / set_timeline 进程内单例管理。

边界遵守：模块零业务 import（不触碰 personality / self_model / memory /
relationship），默认零磁盘 I/O，**不写 data/**。

## 3. Phase 3：Stage 11 接线

`src/runtime/cognitive_activation.py` 的 `_adapter_stage_11_reflection` 增加
一段（原链不变）：

```
reflection_started 发布（返回 event_id 供 parent 链接）
 ↓
ReflectionEngine.reflect → ReflectionRecord
 ↓
[flag 开启时] timeline_event_from_reflection → CognitiveTimeline.append
   trace_id = ctx.session_id；parent_event_id = reflection_started.event_id
 ↓
reflection_completed 发布（flag 开启时 payload 附 timeline_event_id）
```

- **flag=False**：行为与 Phase 1 逐字节等价（测试锁住事件序列与 payload
  不含 timeline 字段）
- **flag=True**：只增加 timeline 节点与既有事件发布，**零状态写入**、
  零人格变化（治理测试证明）
- `publish_cycle_event` 返回值从 None 变为 emit 产生的 domain event
  （向后兼容：既有调用方忽略返回值）

## 4. Phase 4：治理边界（4 项测试全过）

1. **Reflection 不能直接修改 SelfModel**：快照深拷贝前后相等；
   core 只被调用 17 个阶段方法，无任何额外写入方法。
2. **Reflection 不能绕过 MutationGateway 修改 Personality**：真实
   MutationGateway + InMemoryAuditWriter 作探针——反思运行期间网关零请求、
   零决策、零审计投影；自我模型零修改。
3. **Timeline 记录失败不能阻断主循环**：writer 抛异常时主循环 17 阶段
   照常完成、反思事件照常发布、内存节点保留（fail-open）。
4. **重复 trace_id 不产生重复节点**：同 event_id 幂等重放 count 恒 1；
   同 trace 下不同节点正常成链。

## 5. Phase 5：测试

`tests/test_cognitive_timeline.py`（17 项，全过）：

- event 创建：默认值 / 序列化 roundtrip / 证据规范化 / 非法输入
- trace 链：parent 链接 / chain / get_by_trace_id / get_by_event_id
- reflection 接入：投影映射字段逐一断言；Stage 11 全链路（trace=session、
  parent=reflection_started.event_id、payload 附 timeline_event_id）
- fail-open：writer 异常隔离、内存保留、append 非法输入 fail-soft、
  主循环不阻断
- flag=false：事件序列与 Phase 1 完全一致、payload 无 timeline 字段、
  单例 timeline 未被触碰
- 治理边界：上述 Phase 4 四项

回归套件：
- `test_runtime_cognitive_activation.py` 11/11（Phase 1 不受影响）
- `test_runtime_stage_contract.py` 10/10（17 阶段契约不变）
- B.13：test_b13_self_model_mutation_governance.py + test_runtime_phase_b13.py
- B.10/B.11：test_runtime_phase_b10.py + test_runtime_phase_b11.py +
  test_b11_governance_runtime.py + test_governance_proposal_manager.py +
  test_mutation_governance_runtime.py
- 合计治理回归 334/334 通过

## 6. Phase 6：A/B 回归

方法：A=Phase 1 文件态（cognitive_activation.py 回退 Phase 1 版本、
cognitive_timeline.py 与测试文件移除），B=Phase 2 后。同一 103 文件电池
（含 Phase 1 测试，不含 Phase 2 新测试），同一 chunk 顺序，HF_HUB_OFFLINE=1。

| 指标 | A（Phase 1） | B（Phase 2） |
| --- | --- | --- |
| 失败数 | 118 | 118 |
| 新增失败（B 有 A 无） | - | **0** |
| 消失失败（A 有 B 无） | - | **0** |
| data/ 修改 | 0 | **0** |

本轮 A/B 无方法学修正：电池清单在两个状态下均完整存在（Phase 1 测试文件
在 A/B 两态都保留），chunk 顺序严格一致，无 Phase 1 曾出现的
"file not found 整 chunk 中止"问题。

## 7. 约束遵守确认

- [x] 不修改 data/（A/B 全程 git status 干净）
- [x] 所有新 flag 默认 False（timeline recording / 各 stage flag）
- [x] 未开启生产 runtime 行为（config.yaml 未改）
- [x] 保持旧 API（publish_cycle_event 返回值向后兼容；17 阶段契约不变）
- [x] 不修改 Personality/SelfModel/Growth 权威状态（治理测试证明）
- [x] 只复用已有 Runtime/EventBus/Reflection 资产，无平行系统
- [x] 每步测试 + A/B 回归

## 8. 留给后续 Phase

- Timeline 的 writer 落盘策略（当前刻意零磁盘 I/O，注入点已留好）
- cycle_started / reflection_started 等其余节点的 timeline 投影（当前只投影
  ReflectionRecord 一个节点类型，parent 链已支持扩展）
- Cognitive Timeline 查询侧接入（诊断面板 / 审计回放）
- 生产 flag 开启决策（不做，留给后续）
