# Phase 4.4-A1 — Chat Turn Experience Production 实施报告

日期：2026-08-12
阶段：Phase 4.4-A1（对话轮经历生产修复）
前置：Phase 4.4-A 生命周期审计（`PHASE4_4A_LIFECYCLE_AUDIT_REPORT.md`）

---

## 1. 背景：4.4-A 审计发现的问题

100 轮连续生命循环模拟发现三个断点：

| 编号 | 问题 | 严重度 |
|---|---|---|
| P1 | 对话轮经历不落 ExperienceJournal（100 轮 journal=0） | 高 |
| P2 | ExperienceBuilder `_building` 悬挂线性泄漏（10→100） | 中 |
| P3 | 经历中 user_response 为空串 / 经历内容不完整 | 高 |

根因（4.4-A 已定位）：`_on_event` 每轮 `start_building`，但 `finish_building` 只在
`_maybe_decide` 产生 `dispatched_actions` 时触发（runtime_core.py:962）。
纯对话轮没有 action 派发 → 永远不 finish → 经历悬挂、journal 为零、Growth 断粮。

---

## 2. 生产修改清单（最小修改，共 3 处）

### 2.1 `src/runtime/runtime_core.py` — 对话轮 finish 点（P1+P3）

- `_stage_16_response` 末尾调用新增的 `_finish_chat_turn_experience(ctx)`。
- 逻辑：
  - 若 `_building_experience_id` 已不在 builder `_building` 中（action 轮已完成）→ 跳过，不重复 finish；
  - user_message 与 reply 均为空（tick 型空轮）→ `cancel_building`，不落垃圾记录；
  - 否则 `record_decision(decision_source="conversation", action_type="conversation")`
    （ExperienceValidator 要求 action_type 非空，否则按 missing_action_type 丢弃）；
  - `finish_building` 使用 `ActionResult(success=True, response_received=bool(reply),
    user_response=<用户真实输入>)`——user_response 语义保持不变（4.3 Stage 4 投影契约依赖），
    助手回复通过 `side_effects=["assistant_response: ...", "emotion: ...", "relationship_stage: ..."]` 承载；
  - 统一走既有入口 `handle_completed_experience(experience, store_to_memory=True)`；
  - 全程 fail-soft，任何异常不影响响应主流程。

### 2.2 `src/runtime/adapters/memory_adapter.py` — 经历内容完整化（P3）

`_convert_to_memory` 的 metadata 新增四个键（既有键全部不变）：

- `user_input`：从 trigger_event.data.text/content 提取；
- `assistant_response` / `emotion_tag` / `relationship_stage`：从 result.side_effects 前缀串解析。

### 2.3 `src/runtime/experience_builder.py` — 悬挂上限（P2）

- `ExperienceBuilderConfig` 新增 `max_building_size: int = 100`；
- `start_building` 超限时按 start_time 丢弃最旧悬挂并 warning 日志；
- 兜底防线：即使未来出现新的"只 start 不 finish"路径，泄漏也有硬顶。

### 明确未触碰

Memory 架构、GrowthEvaluator、GrowthEngine、PersonalityResolver、IdentityCore、
Approval 流程、Proposal schema、ExperienceValidator 生产规则、legacy runtime.py。

---

## 3. 测试设计

新增 `tests/test_phase_4_4a1_chat_experience.py`（9 个测试），对照任务卡 E1~E5 + P2：

| 测试 | 验收项 |
|---|---|
| test_chat_rounds_write_journal | E1：5 轮 → journal=5（修复前=0） |
| test_experience_record_complete | E2：user_input/assistant_response/timestamp/relationship_stage 四要素 + user_response 契约不变 |
| test_tick_like_empty_turn_no_junk | 空轮 cancel，不落垃圾 |
| test_building_cleared_each_round | P2：10 轮每轮 pending=0 |
| test_building_cap_drops_oldest | P2：上限截断最旧 |
| test_chat_rounds_feed_growth_to_pending_proposal | E3：6 轮创造类表达 → GracePeriod → pending proposal + trace activated |
| test_no_personality_change_without_approval | E4：growth_count=0 + traits 不偏离 0.5±0.05 |
| test_journal_consistent_across_restart | E5：重启一致 + 可续写 |
| test_second_finish_is_noop | action 轮防护：不重复 finish |

### 测试侧确定性处理（两处，均不改生产逻辑）

1. **A1 `_make_rc` 注入 `validator.min_duration_ms = 0.0`**。
   模拟轮次全程在进程内完成，部分轮耗时 <10ms，会被 ExperienceValidator
   的生产规则（min_duration_ms=10，"低于此值可能为无效操作"）丢弃，造成测试抖动
   （同一测试两次运行 5/5 与 3/5 不定）。本测试验收的是"接线是否落账"而非耗时校验，
   故仅测试内关闭该门槛；生产规则保持不变（100 轮审计中该规则正常生效，见 §5）。

2. **4.2-D `test_record_interaction_forwards_event_to_growth` 断言锚定修复**。
   A1 测试首次在套件内跑完整 `process()` 轮次（relationship_enabled=True），
   暴露了一个既有的测试脆弱性：`RuntimeEventBus` 包装的是 `EventBus.get_instance()`
   **全局单例**（src/core/event_bus.py:62），事件历史跨 RuntimeCore 实例共享，
   且 `get_history` 返回**时间倒序**（event_bus.py:167）——`history[-1]` 取到的是
   窗口内**最旧**的事件，即其他测试实例留下的 `growth_evidence_state=None`  stale 事件。
   修复：改为以本轮唯一 evidence_id（`mem_42d_rt_1`）在全局历史中锚定属于本测试的事件。
   这是测试断言修正，EventBus 单例架构为既有设计，未改动。

---

## 4. E1~E5 验收对照

| 验收项 | 目标 | 结果 | 状态 |
|---|---|---|---|
| E1 | 多轮模拟 journal > 0（修复前 100 轮=0） | 100 轮 journal=90；单测 5 轮=5 | ✅ |
| E2 | 经历含 user_input/assistant_response/timestamp/relationship 证据 | 单测逐项断言通过 | ✅ |
| E3 | Growth 重获输入（≥1 pending proposal） | 100 轮 pending proposal=9；单测通过 | ✅ |
| E4 | personality delta=0（除非 approval） | 100 轮 drift=0.0；growth_count=0 | ✅ |
| E5 | 重启后 journal 数量一致 | 单测通过（含续写验证） | ✅ |

---

## 5. 100 轮生命周期审计：修复前后对比

同一脚本 `scripts/audit_phase_4_4a_lifecycle.py` 重跑：

| 指标 | 修复前（4.4-A） | 修复后（4.4-A1） |
|---|---|---|
| journal 记录 | **0**（100 轮全程） | 9→18→…→**90**（每 10 轮 +9） |
| builder 悬挂 | 10→100 线性泄漏 | **每档 = 0** |
| Growth proposal | 0 | 0→**9**（全部 pending） |
| Stage4 trace | 无数据 | activated=98 / no_experience=2 |
| personality drift | 0.0 | 0.0（保持不变） |
| relationship | initial→deep_collaboration | initial→deep_collaboration（不变） |
| MemoryStore 写入 | 0 | 0（见 §7 遗留 P4） |

快照文件：`docs/audit/phase4_4a_lifecycle_snapshots.json`

**journal=90 而非 100 的说明**：约 10% 的模拟轮耗时 <10ms，被 ExperienceValidator
的 min_duration_ms=10 生产规则判为无效经历丢弃——这是既有反垃圾守卫按设计工作，
不是接线缺陷。真实对话（LLM 调用数百毫秒起）不会触发。

**审计工具变更披露**：为在 100 轮尺度演示 E3 食物链，`message_for_round` 每 10 轮
插入一条创造类文本（"我已经创建了一个新角色，设计了她的形象和性格"，含行为标记"了"，
confidence 0.945≥0.8 门槛）。首轮经 GracePeriod 只记账不放行，故 proposal 从第 20 轮
档位开始出现（每 10 轮 +1）。这是审计脚本的消息计划调整，不涉及生产代码。

---

## 6. 回归测试

命令：`pytest tests/runtime tests/test_phase_4_4a1_chat_experience.py tests/test_phase_4_3_runtime_growth_activation.py tests/test_phase_4_2d_relationship_growth_evidence.py -q`

结果（连续两轮一致）：**208 passed / 1 failed**

唯一失败为基线冻结项 `test_identity_context_phase2::test_fallback_when_engine_missing_no_exception`
（ResponseAdapterImpl fallback 语义，4.1 起记录的既有冻结失败，与本次修改无关）。

A1 测试文件单独运行：9/9 通过（连续两轮，计时确定性已消除）。

---

## 7. 遗留与后续

1. **P4：MemoryStore 双载体问题（待顾问裁决专项审计）**。
   100 轮模拟中 `mem=0`：RuntimeCore 的 `handle_completed_experience(store_to_memory=True)`
   链路写入的是 ExperienceJournal + 领域事件，对话长期记忆实际载体仍是 Orchestrator 侧
   VectorMemory。经历 → MemoryStore 的入口是否存在第二个断点，需专项审计裁决。
2. **4.4-B（冲突成长测试）/ 4.4-C（恢复测试）**：仍按顾问指示暂停，待 A1 验收后恢复。
3. **EventBus 全局单例**：跨实例事件历史共享是既有架构事实，本次仅修正了依赖
   `history[-1]` 的脆弱断言。若未来更多测试依赖领域事件历史，建议在测试基类中统一
   使用 `EventBus.reset_instance()`（官方标注"仅测试用"）做隔离——属测试基建项，未实施。

---

## 8. 架构影响评估

本次修复接通了生命循环的最后一公里：

```
用户输入 → Memory → Emotion → Relationship → Growth ←┐
    ↓                                      ↑          │
 Response → ExperienceJournal ─────────────┘          │
    ↓                                                 │
 未来召回 / Growth 证据 / SelfModel 反思 ──────────────┘
```

- **经历连续性**：对话轮首次真实落账，Growth 断粮问题解除（trace activated 98%）；
- **人格安全性**：全程 drift=0，proposal 全部 pending 等待审批，红线保持；
- **修改性质**：3 处生产修改均为"接线 + 防御上限"，零新增系统、零 schema 变更；
- **判断标准对照**：代码量增加极小，增强的是记忆连续性（经历落账）与
  成长连续性（Growth 在生命循环中自动获得输入）。
