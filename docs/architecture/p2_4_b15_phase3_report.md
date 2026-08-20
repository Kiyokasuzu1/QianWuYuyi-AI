# P2.4-B.15 Phase 3 完成报告 — Timeline Event Projection

> 日期：2026-08-21
> 目标：扩展 CognitiveTimeline，使 Runtime 生命周期事件进入统一认知时间线
> 基线：B.15 Phase 2 完成态（HEAD 225d040）

## 1. 变更清单

| 文件 | 变更 | Phase 2 基线 md5 | Phase 3 完成态 md5 |
| --- | --- | --- | --- |
| src/runtime/timeline_projection.py | **新增**（投影层） | — | 7f0ec80e… |
| tests/test_timeline_projection.py | **新增**（12 项测试） | — | 628aeada… |
| src/runtime/cognitive_activation.py | 修改（publish_cycle_event 接线 + trace_id） | f51233d4… | 14d617a5… |
| src/runtime/lifecycle_executor.py | 修改（4 处 publish 传 trace_id） | ae400300… | a41e81c8… |

未修改：cognitive_timeline.py（9a348252… 保持 Phase 2 完成态）、
runtime_event_bus.py、事件 schema、EventBus、17 阶段契约。

## 2. Phase 2 交付：timeline_projection.py

- `project_domain_event(event, *, trace_id="", parent_event_id=None) → Optional[TimelineEvent]`
  纯转换，鸭子类型读取 event_type/payload/event_id/timestamp/source/related_ids，
  异常/不支持时返回 None，**永不抛出**。
- 职责边界：只转换。无 save / update / mutation；不 import personality /
  self_model；零磁盘 I/O；不决策、不修改任何权威状态。
- 可投影事件（全部为既有常量，不新增类型）：cycle_started /
  cycle_memory_completed / cycle_emotion_completed / cycle_growth_completed /
  cycle_personality_completed / reflection_started / reflection_completed /
  cycle_completed / cycle_failed（任务书 "stage_completed" 对应阶段完成族，
  审计已记录，代码库无该字面量）。
- 确定性 event_id 规则（审计 §4 重复风险解决）：
  reflection_completed（payload 有 reflection_id）→ `"tl_" + reflection_id`
  （与 Phase 2 record 投影同 id，CognitiveTimeline 幂等去重 → 合并单节点）；
  其余 → RuntimeDomainEvent.event_id。

## 3. Phase 3 交付：EventBus → Timeline 接线

- 新 flag `timeline_event_projection_enabled`（默认 **False**）。
- `publish_cycle_event(core, event_type, payload=None, *, trace_id="")`：
  emit 成功后调用 `_maybe_project_domain_event`；返回值改为 emit 产生的
  RuntimeDomainEvent（Phase 2 前返回 None，旧调用方忽略返回值 → 向后兼容）。
- `_maybe_project_domain_event`：flag 检查 → 投影 → `get_timeline().append(node)`，
  全程 try/except 隔离（Timeline 异常绝不影响 Runtime）；flag False 时完全跳过。
- trace 贯通：lifecycle_executor.execute() 取 `ctx.session_id` 传至 4 处
  publish（cycle_started / cycle_failed / 阶段事件 / cycle_completed）；
  Stage 11 adapter 取 `ctx.session_id` 传至 3 处 publish（reflection_started /
  record=None 与正常路径的 reflection_completed）。
- 未改变 EventBus、未改变事件 schema；无订阅者时零影响（投影不订阅、不发事件）。

## 4. Phase 4 交付：测试（12/12 通过）

tests/test_timeline_projection.py，覆盖任务书 6 项：

| # | 覆盖点 | 测试 |
| --- | --- | --- |
| 1 | EventBus 事件可以投影 Timeline | test_eventbus_events_are_projected_to_timeline、test_reflection_completed_projected_with_phase2_merge_id、test_projection_field_mapping |
| 2 | flag=false 无 Timeline 变化 | test_flag_false_no_timeline_changes |
| 3 | 重复 event_id 幂等 | test_projection_deterministic_event_id_and_dedup、test_reflection_completed_projection_merges_with_phase2_node |
| 4 | Timeline 异常不影响 Runtime | test_projection_exception_does_not_affect_runtime、test_timeline_append_failure_does_not_affect_runtime |
| 5 | Timeline 不能触发 MutationGateway | test_projection_does_not_bypass_mutation_gateway（运行前挂探针，网关零请求/决策/记录） |
| 6 | Timeline 不能修改 SelfModel | test_projection_does_not_modify_self_model（快照 deepcopy 前后一致，core 仅 17 阶段调用） |

风格：伪 core + 捕获 bus（直接构造真实 RuntimeDomainEvent，不触全局单例），
autouse fixture 重置全部 activation flag + timeline + projection 状态。

## 5. Phase 5 交付：回归套件

| 套件 | 结果 |
| --- | --- |
| Phase 2 认知时间线（test_cognitive_timeline.py） | 17/17 |
| Phase 1 激活（test_runtime_cognitive_activation.py） | 11/11 |
| stage contract（test_runtime_stage_contract.py） | 10/10 |
| B.10/B.11/B.13 治理套件（7 文件） | 334/334 |
| **合计** | **372/372 通过** |

## 6. Phase 6 交付：A/B 回归

方法：A=Phase 2 文件态（cognitive_activation.py 回退 backup_b15p2 f51233d4、
lifecycle_executor.py 回退 backup_b15p1 ae400300、移除 timeline_projection.py
与 test_timeline_projection.py），B=Phase 3 完成态。同一 103 文件电池
battery_b2.txt（两态均完整存在，含 Phase 1/2 测试），同一 chunk 顺序，
HF_HUB_OFFLINE=1。B 态运行前/后文件 md5 逐字节校验一致。

| 指标 | A（Phase 2） | B（Phase 3） |
| --- | --- | --- |
| 失败数 | 118 | 118 |
| 新增失败（B 有 A 无） | - | **0** |
| 消失失败（A 有 B 无） | - | **0** |
| data/ 修改 | - | **0** |

data/ 验证（三重）：
1. git status 全程干净（项目既有准则，data/ 整体 gitignore）；
2. 定向实验：Phase 3 相关 4 套件（50 项，含新 12 项）前后 data/ md5
   快照 diff 为空；
3. 代码证明：新代码零文件 I/O（writer 仅可注入、默认 None；
   timeline_projection.py 无任何磁盘操作）。电池运行产生的
   cog_test_* / _e2e_tmp_should_not_exist / 状态文件痕迹均溯源至
   既有测试（test_cognitive_engine / test_deploy_preflight /
   test_runtime_e2e_real），A/B 两态行为一致，非 Phase 3 引入。

## 7. 约束遵守确认

- [x] 不写 data/（三重验证，见 §6）
- [x] 所有新 flag 默认 False（timeline_event_projection_enabled=False）
- [x] 未开启生产 runtime 行为（config.yaml 未改）
- [x] 不改变任何权威状态（Timeline 只记录，不决策、不修改；治理测试证明）
- [x] 不接管 Personality/SelfModel/Growth（零 import、零写入路径）
- [x] 保持旧 API（publish_cycle_event 位置参数不变，新增 keyword-only
      trace_id；返回值扩展向后兼容；17 阶段契约不变）
- [x] 只复用已有资产（EventBus / CognitiveTimeline / Reflection），无平行系统
- [x] 每阶段测试 + A/B 回归

## 8. 留给后续 Phase

- Cognitive Timeline 基础层已就绪：生命周期事件（9 类）+ 反思记录均可在
  flag 开启后进入统一时间线，具备 trace_id 链与幂等合并语义。
- 尚未开启生产 runtime 行为（两个 timeline flag 均 False），符合任务书
  "完成后停止，等待下一阶段"。
