# P2.4-B.15 Phase 6 A/B 回归验证报告

> 日期：2026-08-21
> 性质：只验证、不重构、不新增架构、不修改 schema/EventBus/治理边界
> 结论：**A/B 全通过**。四 flag 全开（含 staging）与全关在 394 项电池上
> 零差异；data/ 零修改；权威状态零变化；MutationGateway 零调用。

## 1. 基线

| 项目 | 值 |
| --- | --- |
| HEAD | 225d0408e89b475605d62b0435f85a9954ffb024 |
| git status | 与 Phase 5 完成态一致（5 个 B.15 文件，本阶段零新增改动） |
| 关键文件 md5 | cognitive_activation 8c617f7f… / lifecycle_executor cb41d3f9… / cognitive_timeline 9a348252… / timeline_projection 7f0ec80e… / test_b15_phase5 c6ec1d26… |
| data/ 状态 | 607 文件 md5 快照；git 干净 |
| flag 默认值（新进程） | staging=False / cycle_events=False / recording=False / projection=False / stage11=False |

## 2. A 状态结果（全部 B.15 flag 默认关闭）

同一 12 文件电池（Phase 1 激活 + Phase 2 时间线 + Phase 3 投影 +
stage contract + B.10/B.11/B.13 治理 + Phase 5 staging 测试）：

| 指标 | 值 |
| --- | --- |
| 测试数量 | 394 |
| 通过 / 失败 | **394 / 0** |
| warnings | 272 |
| data/ md5 | 与基线一致（零修改） |

## 3. B 状态结果（临时插件开启四 flag）

仅通过 /tmp 临时插件（不进仓库）开启：
`runtime_cognitive_activation_enabled`（staging）+
`lifecycle_cycle_events_enabled` + `timeline_recording_enabled` +
`timeline_event_projection_enabled`。

未修改 config.yaml；未开启 scheduler / growth mutation；未触发
MutationGateway 写入路径（config 驱动系统一律不动）。

| 指标 | 值 |
| --- | --- |
| 测试数量 | 394 |
| 通过 / 失败 | **394 / 0** |
| warnings | 272 |
| data/ md5 | 与基线一致（零修改） |

## 4. 差异分析

| 对比项 | 结果 |
| --- | --- |
| A/B 测试数量 | 394 = 394 |
| 新增失败 | **0** |
| 消失失败 | **0** |
| 行为差异 | 无 |
| warnings 差异 | **0**（数量 272=272；warnings 摘要文本去数字后 diff 为空） |
| data/ 差异 | **0**（全程 md5 与基线一致） |
| 权威状态变化 | 0（personality/self_model/growth 状态文件零变化） |

**为何 staging 全局开启仍零差异**（机制解释）：
- B.15 各测试文件（activation/timeline/projection/phase5）的 autouse
  fixture 调用 `reset_all_cognitive_activation_flags()`——Phase 5 已将其
  扩展为同时复位 staging flag——因此对 flag 敏感断言的测试内部 staging
  始终为 off，行为与 Phase 5 B 态一致。
- 其余电池文件（stage contract / B.10 / B.11 / B.13 治理）驱动
  execute() 时不依赖事件/时间线断言：apply 开启只增加事件发布与内存
  时间线节点（fail-open、无副作用），不触及任何断言面。
- 生产等效行为（staging on → execute() 入口 apply）已由 Phase 5 测试
  10/10 直接覆盖（完整 8 事件链、trace 连续、反思记录、零 mutation）。

## 5. 重点验证（任务书 6 项）

| # | 验证项 | 证据（B 态电池内） | 结果 |
| --- | --- | --- | --- |
| 1 | Runtime cycle 正常完成 | stage contract 10/10、activation 11/11、phase5 链测试 | PASS |
| 2 | Timeline 收到完整生命周期事件 | test_runtime_cycle_runs_completely_and_timeline_chain（8 事件 == 8 节点，顺序一致） | PASS |
| 3 | trace_id 保持连续 | test_trace_id_continuous（全部 == ctx.session_id） | PASS |
| 4 | Reflection 只记录、不直接修改 SelfModel | test_reflection_produces_record / test_self_model_unchanged / test_reflection_does_not_modify_self_model | PASS |
| 5 | MutationGateway 无额外调用 | test_mutation_gateway_no_calls / test_projection_does_not_bypass_mutation_gateway / B.13 全套 | PASS |
| 6 | flag 关闭后恢复旧行为 | test_flag_off_restores_behavior + A 态结果（默认态） | PASS |

## 6. 风险结论

- 四 flag 全开（含 staging）在 394 项电池上**零风险暴露**：无新增/消失
  失败、无 warnings 漂移、data/ 零写入、权威状态零变化、网关零调用。
- staging 激活链自 Phase 5 起默认关闭、幂等、单点接线；任何新进程
  均从默认 False 启动，**回滚即时**（关掉 staging 即完全回到旧行为）。
- 剩余风险面仅在"生产环境真正开启 staging"后出现（Timeline 内存增长
  ——受 capacity FIFO 上限约束；事件发布频率）——属灰度观察项，非本次
  验证范围。

## 7. 下一阶段建议（仅列项，不提前开发）

1. **staging 生产试点**：在 staging 环境开启 staging flag，观察真实
   Runtime cycle 的事件输出与 Timeline 增长（现有 capacity 上限内）。
2. **Timeline 持久化**：利用已有可注入 writer 接口接落盘 writer（重启
   后认知时间线可续），保持 flag 控制。
3. **成长闭环接线**：GROWTH_EVALUATION 槽位 → GrowthEvaluator →
   GrowthProposal → 审核 → B.13 MutationGateway apply（组件全有，只接线）。
4. **反思周期触发**：启用既有 `reflection_scheduler_enabled` config 开关
   （最小灰度，不开发新调度器）。
5. **记忆摘要策略评估**：开启默认压缩策略，降低 token 消耗并强化长期
   连续性。

以上均不改变现有架构边界；每项独立 flag 控制、独立 A/B 验证。

---

A/B 全通过，按任务书要求停止，等待下一阶段任务书。
