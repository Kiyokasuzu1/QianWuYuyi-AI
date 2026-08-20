# P2.4-B.15 Phase 4 — Runtime Cognitive Loop 激活前 A/B 验证报告

> 日期：2026-08-21
> 目标：只验证开启 B.15 已有 flag 后是否安全；不做架构扩展、不新增系统
> 结论：**安全**。开启两个 timeline flag 不产生任何失败、不修改 data/、
> 不触碰权威状态、不改变任何现有行为。
> 本阶段不进入生产启用，不进入 scheduler / persistent writer / growth apply。

## 1. Phase 0：基线快照

| 项目 | 值 |
| --- | --- |
| HEAD | 225d0408e89b475605d62b0435f85a9954ffb024 |
| git status | 与 B.15 Phase 3 完成态一致（本阶段零新增改动） |
| data/ 状态 | 607 文件 md5 快照；git status data/ 干净 |
| cognitive_activation.py | 14d617a5…（Phase 3 完成态） |
| lifecycle_executor.py | a41e81c8…（Phase 3 完成态） |
| cognitive_timeline.py | 9a348252…（Phase 2 完成态） |
| timeline_projection.py | 7f0ec80e…（Phase 3 完成态） |
| B.15 flag 默认值 | timeline_recording=False；timeline_event_projection=False；lifecycle_cycle_events=False；7 个 stage flag 全 False |

## 2. Phase 1：A 状态（全部 B.15 flag = False）

当前 Phase 3 完成态原样运行电池（11 文件）：
runtime cognitive activation / cognitive timeline / timeline projection /
stage contract / B.10 / B.11 / B.13 governance。

| 指标 | 结果 |
| --- | --- |
| 测试数 | 384 |
| 通过 | **384/384** |
| 失败集合 | 空（0 条） |
| data/ 修改 | **0**（md5 快照 diff 为空） |

## 3. Phase 2：B 状态（timeline 两 flag = True）

开启方式：/tmp 下的临时 pytest 插件（b15p4flags.py，pytest_configure
钩子，不进仓库），仅开启：
- `timeline_recording_enabled = True`
- `timeline_event_projection_enabled = True`

未开启：scheduler（代码库无接线，无此开关）、autonomous action
（不存在，无此开关）、growth mutation（未触发任何 growth apply 路径）。

同一电池、同一顺序运行：

| 指标 | A（flag 全 False） | B（timeline 两 flag True） |
| --- | --- | --- |
| 测试数 | 384 | 384 |
| 通过 | 384 | **384** |
| 失败数 | 0 | **0** |
| 新增失败 | - | **0** |
| 消失失败 | - | **0** |
| 行为差异 | - | **无**（warnings 亦一致：226 = 226） |
| data/ 修改 | 0 | **0**（md5 快照 diff 为空，含权威状态文件） |

## 4. 重点检查（任务书 5 项）

**1. Timeline 是否正确收到 cycle/reflection 事件 — PASS（双重验证）**
- 电池内 B.15 测试（projection 12 项 + timeline 17 项）在 B 态全过。
- 定向探针（/tmp 脚本，三 flag 齐开 + Stage 11）：
  bus 发布 8 事件（cycle_started → 4 阶段事件 → reflection_started →
  reflection_completed → cycle_completed），Timeline 收到 **8 节点、顺序
  完全一致**，全部 `trace_id == ctx.session_id`；reflection_completed 节点
  id 为 `tl_ref_*`（与 Phase 2 record 投影幂等合并），parent 链接保留。

**2. Reflection 仍不能直接修改 SelfModel — PASS**
test_reflection_does_not_modify_self_model / test_projection_does_not_modify_self_model
在 B 态通过（快照 deepcopy 前后一致，core 仅 17 阶段调用）。

**3. MutationGateway 无额外调用 — PASS**
test_projection_does_not_bypass_mutation_gateway /
test_reflection_does_not_bypass_mutation_gateway（探针运行前挂载，
网关零请求/零决策/零记录）+ B.13 全套治理测试在 B 态全过。

**4. Runtime 主循环保持稳定 — PASS**
stage contract 10/10、activation 11/11、17 阶段契约不变，B 态 384/384。

**5. flag=false 与旧行为一致 — PASS**
A/B 失败集合完全一致（均为空）；新进程验证三 flag 全部回到默认 False
（模块级状态不跨进程持久）。

## 5. 关键发现（安全性论证）

严格 B 状态只开两个 timeline flag 时，`lifecycle_cycle_events_enabled`
仍为 False → `publish_cycle_event` 不被调用 → 投影层零输入 → **行为与
A 态逐字节等价**（这正是 A/B 结果完全一致的原因）。可观测的 Timeline
活动只在叠加 cycle 事件 / stage flag 时出现（探针证明链路正确）。
因此激活顺序存在天然安全台阶：先开 timeline 两 flag（零行为差异），
再按需逐步开 cycle 事件与 stage adapter。

## 6. 回滚状态确认

- 仓库零改动（插件与探针均在 /tmp，不进仓库）；关键文件 md5 与基线一致。
- data/ 最终 md5 与基线 diff 为空。
- 每个新 pytest 进程 flag 均从默认 False 启动（无持久化开关）。

## 7. 结论

- 开启 B.15 已有 timeline flag **安全**：无新增/消失失败、无行为差异、
  data/ 零修改、权威状态零修改、主循环稳定。
- 按任务书要求：**不进入生产启用**，不进入 scheduler / persistent
  writer / growth apply。等待下一阶段任务书。
