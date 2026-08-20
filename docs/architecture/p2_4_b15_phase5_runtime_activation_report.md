# P2.4-B.15 Phase 5 — Runtime Cognitive Loop 最小灰度激活报告

> 日期：2026-08-21
> 目标：让已有 Runtime 生命周期能力进入真实运行链（可回滚、flag 控制）
> 结论：**完成**。staging 激活机制已接入生产唯一入口，默认关闭不变；
> A/B 验证 394/394 双向一致，data/ 零修改，权威状态零变化。
> 未进入 scheduler / persistent timeline / growth apply。

## 1. Phase 0：基线快照

| 项目 | 值 |
| --- | --- |
| HEAD | 225d0408e89b475605d62b0435f85a9954ffb024 |
| data/ 状态 | 607 文件 md5 快照；git 干净 |
| B.15 文件 md5 | cognitive_activation 14d617a5… / lifecycle_executor a41e81c8… / cognitive_timeline 9a348252… / timeline_projection 7f0ec80e… |
| flag 默认值 | 全部 False（cycle_events / recording / projection / 7 stage flag） |

## 2. Phase 1：最小激活组合设计

开启（4 项）：
1. `lifecycle_cycle_events_enabled`
2. `timeline_recording_enabled`
3. `timeline_event_projection_enabled`
4. `SELF_MODEL_REFLECTION` stage adapter（**设计决策**：任务书 Phase 1 目标事件链
   含 reflection_started/reflection_completed，且 Phase 3 检查 4 要求
   "Reflection 产生记录"——该 adapter 只读反思、不产生 mutation，属既有
   flag，一并纳入组合并在此记录）

保持关闭（已逐一确认现状，config 驱动、默认 False，本次绝不触碰）：
- scheduler：`reflection_scheduler_enabled` / `autonomous_scheduler_enabled`（config 默认 False）
- growth mutation：`approval_manager_enabled`（config 默认 False）
- reflection growth bridge：`reflection_growth_bridge_enabled`（config 默认 False）
- self model mutation auto apply：B.13 MutationGateway 之外无任何路径

目标链验证：cycle_started → 4 阶段事件 → reflection_started →
reflection_completed → cycle_completed，全部进入 CognitiveTimeline。

## 3. Phase 2：实现（只接线，不新增系统）

生产 Runtime cycle 唯一入口确认：
`RuntimeCore.process()` → `self._lifecycle.execute()`
（runtime_core.py:946 持有 LifecycleExecutor，4909 行单调用）。

改动（2 文件）：
1. `cognitive_activation.py`：
   - 新增 staging flag `runtime_cognitive_activation_enabled`（**默认 False**）
   - 新增 `apply_runtime_cognitive_activation()`（幂等；staging False →
     no-op；True 时开启上节 4 项组合，绝不触碰 config 驱动系统）
   - `reset_all_cognitive_activation_flags()` 同步复位 staging flag
   - `__all__` 增补 3 个新符号
2. `lifecycle_executor.py`：`execute()` 顶部（既有 B.15 事件块内）调用
   `apply_runtime_cognitive_activation()` —— 生产唯一接线点，默认 no-op。

未改变：事件 schema、EventBus、17 阶段契约、config.yaml、任何默认值。

## 4. Phase 3：测试（tests/test_b15_phase5_runtime_activation.py，10/10）

| 任务书检查 | 测试 | 结果 |
| --- | --- | --- |
| 1. Runtime cycle 完整跑完 | test_runtime_cycle_runs_completely_and_timeline_chain | PASS（17 阶段） |
| 2. Timeline 完整事件链 | 同上（8 事件 == 8 节点，顺序一致） | PASS |
| 3. trace_id 连续 | test_trace_id_continuous | PASS（全部 == ctx.session_id） |
| 4. Reflection 产生记录 | test_reflection_produces_record | PASS（ctx._b15_reflection_record + payload 一致） |
| 5. SelfModel 不变化 | test_self_model_unchanged | PASS（deepcopy 前后一致） |
| 6. MutationGateway 无调用 | test_mutation_gateway_no_calls | PASS（零请求/决策/记录） |
| 7. flag 关闭时行为恢复 | test_flag_off_restores_behavior | PASS（零事件/零节点/零记录） |

另含 staging 语义 3 项：默认 False / apply no-op / 最小组合仅 4 项（其余
stage flag 一律不动，growth 相关不开启）/ 幂等。

## 5. Phase 4：A/B 回归

方法：A = 全部 B.15 flag 默认 False；B = 插件仅开启三 flag
（cycle_events / recording / projection，staging 保持 False——A/B 轴是
三个子 flag，staging 机制由 Phase 3 单测覆盖）。同一 12 文件电池
（11 个既有 + Phase 5 新测试），同一顺序。

| 指标 | A | B |
| --- | --- | --- |
| 测试数 | 394 | 394 |
| 通过 | 394 | **394** |
| 失败数 | 0 | **0** |
| 新增失败 | - | **0** |
| 消失失败 | - | **0** |
| 行为差异 | - | 无（warnings 272 = 272） |
| data/ 修改 | 0 | **0**（md5 快照与基线一致） |
| 权威状态变化 | 0 | **0** |

新进程验证：staging / cycle_events / recording / projection 全部回到
默认 False（模块级状态不跨进程持久）。

## 6. 回滚状态确认

- 仓库改动仅 2 个源文件 + 1 个新测试文件（md5：cognitive_activation
  8c617f7f… / lifecycle_executor cb41d3f9… / test c6ec1d26…）；插件在 /tmp。
- data/ 与基线一致；git 状态除上述改动外无新增。
- 任何时刻恢复默认：staging 关闭即完全回到 Phase 4 行为。

## 7. 结论

- 最小灰度激活机制就绪：生产入口单点接线、默认关闭、幂等、可回滚。
- 开启 staging 后一次 Runtime cycle 产出完整 8 事件链并进入
  CognitiveTimeline，trace_id 连续，Reflection 产生记录；
  SelfModel 零修改、MutationGateway 零调用。
- 按任务书要求停止：**未进入 scheduler、persistent timeline、growth
  apply**。等待下一阶段任务书。
