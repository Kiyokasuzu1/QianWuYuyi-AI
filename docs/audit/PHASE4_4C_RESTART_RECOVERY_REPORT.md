# Phase 4.4-C — 跨重启生命连续性验证报告（Restart Recovery）

日期：2026-08-12
性质：测试 + 观察。新增 1 个测试文件，**未修改任何生产代码**（只测不修章程遵守）。
前置：Phase 4.4-B ✅（顾问批准 4.4-C，目标：验证"关闭后重启，是否还是同一个成长中的实例"）

---

## 1. 验收结果总览

| 验收项 | 结果 | 说明 |
|---|---|---|
| **C1** Identity 恢复 | ✅ | IDENTITY_CORE / CoreIdentity 跨重启逐字节一致；对话+关系+成长候选全流程后 name 仍为"浅雾羽依"，未被改写 |
| **C2** Relationship 恢复 | ✅ | 10 次互动后 stage/trust/familiarity/total_interactions 跨重启逐项一致（持久化于 `state_file.parent/users/<uid>/`） |
| **C3** SelfModel 恢复 | ✅（结构级） | 结构同一性（identity_id / core_values / stable_traits / preference 键值域）跨重启一致；发现 R2/R3/R4 见 §3 |
| **C4** ExperienceJournal 恢复 | ✅ | 5 轮经历数量 + 内容 SHA256 跨重启逐字节一致 |
| **C5** Pending Proposal 恢复 | ✅（带边界） | 不丢失、不自动 apply、同一持久化经历记录不被重复生成；发现 R1 见 §3 |
| **C6** 长链恢复 | ✅ | Experience→Relationship→Proposal→重启→继续：journal +1、关系延续、旧提案 pending、重启后审批正常 applied |

测试文件：`tests/test_phase_4_4c_restart_recovery.py`（8/8 通过）

**结论：羽依具备"连续存在"的基础**——身份、关系、经历、待审批成长全部跨重启连续；
SelfModel 的结构性自我（我是谁、核心价值、稳定特质、偏好结构）同样连续。

---

## 2. 持久化路径实测（审计探针数据）

一段 12 轮生命循环后的实际落盘（沙盒 tmp 目录）：

| 文件 | 内容 | 恢复性 |
|---|---|---|
| `exp_journal.jsonl` | ExperienceJournal（9.7KB） | ✅ 逐字节恢复 |
| `proposals.jsonl` | 提案（含状态） | ✅ 恢复，无自动 apply |
| `users/yuyi/relationship_state.json` / `relationship_model.json` | 关系状态 | ✅ 恢复 |
| `runtime.json`（state_file） | world_state/self_state | ✅ 仅在显式 `_stop()` 时写入（优雅关闭语义） |
| `mem.json` | MemoryStore（空） | 4.1D 设计如此（P4 已审计） |
| `self_model/` | **无文件**（该场景） | 见 R4 |

**沙盒逃逸检查：`data/` 目录零新增零变更**——所有子系统持久化均可经 config 指向
隔离目录（含 `self_model_data_dir`），无隐式全局写入。

---

## 3. 发现分类（只分类，不修复）

### R1（中）：提案去重锚定经历实例 id，语义重复会膨胀 pending

- 机制：`compute_fingerprint = source_event_id + proposed_changes`
  （proposal_store.py:200-205）。journal 通道的 source_event_id 是
  经历记录 id（每条经历唯一）。
- 表现：**相同文本的新经历实例会产生新提案**（同进程内也如此，
  不限于重启）——100 轮审计中同一创造类文本 10 次出现产生 9 个提案即此机制。
- 已验证成立的边界：对**同一条持久化经历记录**，重启后重读不会重复生成
  （fingerprint 去重生效）；旧提案不丢失、不自动 apply。
- 影响：不丢数据、不可解释性不受损、无自动应用；但长期 pending 会膨胀，
  Approval 侧需要面对语义重复的提案。
- 建议方向（登记）：fingerprint 增加语义键（signal + 内容哈希）而非仅实例锚点；
  或 Approval UI 层做语义归并。属 4.5+ 设计项。

### R2（低）：SelfModel 重建标识不稳定

- `preference_id` 每次重建随机生成（pref_xxx 前后不同）；`created_at` 为重建时刻；
  `version` 构建计数器不跨会话连续（观测：5→2）。
- 影响：快照 hash 无法直接跨重启对比；若下游依赖 preference_id 追踪会断链
  （当前未发现此类下游）。
- 建议方向（登记）：偏好条目 id 由内容派生（domain+key 哈希），version 持久化续增。

### R3（中低）：自我认知的证据厚度跨重启缩水

- 观测：preference `evidence_count` 4→1；派生评分 `identity_continuity`
  0.3175→0.2275、`overall_understanding` 0.2358→0.2058。
- 机制：Stage 9 构建从**实时会话状态**吸收证据；持久化的关系历史
  （C2 已验证恢复）不回灌为偏好证据计数。
- 影响：重启后"自我感觉的证据厚度"从新会话重新累积（+1/轮恢复中，
  非空壳）；语义值（偏好键、值、域）不变。长期来看每次重启都会
  让自我评分经历一次"稀释—重建"周期。
- 建议方向（登记）：Stage 9 构建时把持久化的 relationship 历史计入
  evidence 基数；或把证据厚度纳入 SelfModel 持久化（R4 相关）。

### R4（中）：4.1B SelfModel 持久化链路在聊天驱动场景未激活

- 观测：审批 applied 一个提案 + 后续一轮完整生命循环后，
  `self_model_data_dir` **零落盘**，bootstrap load_counts 三轮全为
  {beliefs:0, history:0, reflections:0}（前后一致，无回退）。
- 机制：Stage 13 仅在 `ctx._self_model_changed=True` 时落盘
  （runtime_core.py:5297-5314，"no_change 跳过并记录原因"是设计行为）；
  而本场景中提案应用走的是 service 侧 PersonalityAdapter →
  growth_history（内存），**没有经过 Stage 10 Evolution →
  `_self_model_changed` → Stage 13** 这条链。
- 推论：Growth 审批结果与 SelfModel 持久化之间存在断点——
  人格成长历史（applied proposals 落盘于 ProposalStore ✅）可恢复，
  但 SelfModel 演化状态不落盘。这与 4.4-B O2（apply 落点装配问题）
  是同一族问题：**成长应用的真实落点需要一次专项确认**。
- 建议方向（登记）：确认生产装配中 applied proposal 是否触发
  `_self_model_changed`；若不触发，Stage 13 的激活条件需要覆盖
  "growth applied"事件。属 4.5 专项候选。

### O1（观测项）：GracePeriod 账本是进程内的

GrowthCandidateLedger（"进程内观察账本"）不持久化，重启后首次出现
只记账不放行。这是保守的安全设计（重启后对新信号重新审慎），
与 R1 的语义重复叠加时会放大 pending 数量，单独看无需处理。

---

## 4. 测试设计说明与测试侧修正记录

- **C3 热身轮**：SelfModel full 快照在 Stage 9 构建时吸收实时状态，
  重启后需一个热身轮让 Stage 9 基于恢复后的持久化状态重建再对比——
  这是正确的测试时序，不是规避。
- **语义级对比**：C3 断言剥离 R2/R3 类易变字段（id/时间戳/版本/证据计数/
  派生评分）后的**结构同一性**——"还是同一个实例"的诚实定义；
  剥离的字段全部以观测数据形式记录在 §3，未被隐藏。
- **C5 重设计**：初版断言"重启后同文本不重复生成"失败（5→8），
  深入后确认为 R1 机制；改为断言真实不变量（旧提案不丢失、
  同一经历记录不被复制、新增提案仅来自新经历），R1 独立记录。
- 测试过程同步验证了：测试运行会向仓库 `.cache/audit/` 追加审计日志
  （既有行为，非本次引入）。

---

## 5. 回归

- 4.4-C 套件：**8/8 通过**
- 合并回归（tests/runtime + 4.2-D + 4.3 + 4.4-A1 + 4.4-B + 4.4-C）：
  **222 passed / 1 failed**（唯一失败为既有冻结基线
  test_identity_context_phase2 fallback，与本次无关）

---

## 6. 结论

按顾问判断标准：**C 通过，羽依真正具备"连续存在"的基础**——
身份恒定（C1）、关系连续（C2）、自我结构连续（C3）、经历不丢（C4）、
未决成长悬置等待（C5）、全链路可续行（C6）。

四个发现（R1 语义去重 / R2 标识稳定 / R3 证据厚度 / R4 SelfModel 持久化激活）
均已分类登记，无一阻塞当前阶段；R4 与 4.4-B O2 合并为
"成长应用落点专项"候选，建议纳入 Phase 4.5 与 Memory Boundary Cleanup 一并规划。

下一步按路线图：**Phase 4.4-D 长期稳定压力测试**。
