# Phase 4.3 — Runtime Growth Activation 实施报告

日期：2026-08-12
依据：顾问执行批准（2026-08-12），方案 = 审计 §6（accept_experience 唯一入口）

---

## 1. 修改文件列表（共 2 个）

| 文件 | 性质 | 内容 |
|---|---|---|
| `src/runtime/runtime_core.py` | 修改（+约 150 行，替换 Stage 4 handler） | 4.3-A 接线 + 4.3-B 可观测性 |
| `tests/test_phase_4_3_runtime_growth_activation.py` | 新增 | GROWTH-R1~R5 验收测试 7 条 |

**冻结区零改动**：GrowthEvaluator / GrowthEngine / PersonalityResolver /
IdentityCore / Approval 流程 / Proposal schema / legacy runtime.py /
GrowthAdapterImpl / trust+0.05 —— git diff 可证。

### Stage 4 handler 新逻辑（runtime_core.py）

```
_stage_04_growth_evaluation(event, ctx)
  ↓ control_blocked 检查（SAFE 模式，保留）
  ↓ _get_growth_integration_service()      【4.3-A 懒装配，唯一入口】
  ↓ _collect_growth_candidate_experiences() 【来源唯一：ExperienceJournal】
      · 只读 memory_adapter._journal.load()（4.1D 资产）
      · 只取 metadata.type=="runtime_experience" 且 user_response 非空
      · 投影：「用户在经历 exp_X 中说了这段话」→ role="user" 诚实标注，
        journal 记录 id / experience_id 全程锚定（非 fake record）
      · 最近 3 条（config: growth_activation_experience_limit）
  ↓ service.accept_experience(record)       【既有管线，未改一行】
      EligibilityFilter → Normalizer → Validator → Matcher
      → GrowthEvaluator（红线）→ ProposalManager → Approval
  ↓ pending proposal 填入 ctx.growth_proposals（既有槽位，首次被生产使用）
  ↓ ctx.lifecycle_trace["growth_activation"] 全程记录（4.3-B）
```

可观测性三种状态（任务卡契约）：
- `{"status":"activated","experience_count":N,"proposal_count":M,"pipeline_states":[...]}`
- `{"status":"no_experience"}`
- `{"status":"failed","reason":"..."}`

---

## 2. 测试结果

### 4.3 验收测试：7/7 通过

| 验收 | 测试 | 结果 |
|---|---|---|
| GROWTH-R1 经历进入 Growth | `test_journal_experience_produces_pending_proposal` + `test_no_fake_record_used` | ✅ journal 经历 → Stage 4 → canonical pending proposal，evidence_ids 锚定 journal 记录 |
| GROWTH-R2 不直接改变人格 | `test_stage4_does_not_mutate_personality` | ✅ growth_history=0 记录，全部 proposal 停 pending |
| GROWTH-R3 审批后才改变 | `test_personality_change_requires_apply` | ✅ 审批前 growth_count=0 → apply_proposal → status=applied + growth_count≥1 |
| GROWTH-R4 无经历安全运行 | `test_empty_journal_noop` + `test_service_unavailable_traced_failed` | ✅ no_experience 无错误；装配失败可见（failed + reason） |
| GROWTH-R5 跨重启 | `test_proposal_survives_restart` | ✅ 新 RuntimeCore + 新 service 读到同一 journal；proposal 跨重启存在于 ProposalStore |

### 回归

| 套件 | 基线 | 改后 | 结论 |
|---|---|---|---|
| `tests/runtime` | 170P / 1F（test_identity_context_phase2 fallback，既有冻结项） | 170P / 1F（同一项） | 无回归 |
| growth 相关 + 4.2-D（9 套件） | — | **208P / 0F** | 无回归 |

### 设计语义说明（如实）

R1 需要跑**两次** Stage 4 才产生 proposal：GrowthEligibilityFilter 的
GracePeriod 是既有时机门控（首次观察只记账、第二次命中才放行）。
这不是缺陷，是「成长需要重复证据」的设计本身。

---

## 3. 新发现的隐藏断点（任务卡要求如实上报）

### H1. accept_experience 的 source_event_id 契约缝隙（既有，未修）

`growth_integration.py:649` 构造 source_event 用 `"event_id"` 键，
而 `proposal_manager.py:165` 读 `source_event.get("id")` ——
导致**所有经 accept_experience 产生的 proposal，source_event_id 恒为 None**。

- 影响：proposal → 触发事件的直接溯源断裂；
  evidence_ids 锚定完好（测试已验证），所以证据链没断，断的是事件 id 字段。
- 4.2-D 路径不受影响（RelationshipEvidenceAdapter 的 source_event 有 "id" 键）。
- **建议**：单独立一个小修复（accept_experience 的 source_event 补 "id" 键，
  一行），不在 4.3 范围（任务卡冻结 Approval/Proposal 相关文件）。

### H2. accept_experience 生产零调用方（4.3 之前）

R2.5.1 建成的 Bridge→Growth 入口从未被生产调用——
它和 Stage 4 一样是"建好但没接线"的资产。**4.3 的 Stage 4 是它的第一个
生产调用方**，这条链路现在是真的活了。

### H3. EligibilityFilter 审计存储在非隔离环境写默认路径（噪音）

测试输出可见 `[AuditStorage] JSON 保存失败` —— emit_eligibility_audit
写默认审计路径失败时 fail-soft。既有行为，不影响功能，如实记录。

---

## 4. Growth 链路最终状态图

### 每轮生命循环（现在是真实心跳）

```
Stage 1  RECEIVE_EVENT ──→ 在途 experience 开始构建
Stage 2  MEMORY_RETRIEVAL ──→ ctx.retrieved_memories
Stage 3  EMOTION_UPDATE + Relationship（4.2-B）
              └─ RelationshipEvent ──→ 4.2-D 适配器 ──→ Growth 管线（事件旁路）
Stage 4  GROWTH_EVALUATION  【★ 4.3 激活】
              ExperienceJournal（上一轮真实经历）
                    ↓
              GrowthIntegrationService.accept_experience
                    ↓
              EligibilityFilter（时机）→ Evaluator（红线）
                    ↓
              GrowthProposal(pending) ──→ ctx.growth_proposals
                    ↓
              Approval（auto_accept=False 冻结）
Stage 5  PERSONALITY_UPDATE（只读 resolver/adapter，未被 Stage 4 触碰）
 ...     ...
Stage 14 RESPONSE_GENERATION
 ...     行动分派后：当轮 experience finish → 写入 ExperienceJournal
         ──→ 成为下一轮 Stage 4 的输入 ♻️
```

### 闭环确认

```
经历（journal 持久化，跨重启）
  → Runtime 感知（Stage 4 每轮读取）
  → Growth 判断（EligibilityFilter + Evaluator 红线）
  → 生成候选变化（canonical GrowthProposal，pending）
  → 等待审批（Approval，R3 验证审批后才生效）
```

任务卡五项目标全部达成。

---

## 5. 遗留与后续建议

| 项 | 状态 | 建议 |
|---|---|---|
| ~~H1 source_event_id 契约缝隙~~ | **✅ 已修（Phase 4.3-D，见附录）** | — |
| 双 RuntimeCore 并存 | 治理项（顾问决议：现在不动） | 后续收敛议题 |
| Stage 4 每轮重复评估同一条经历 | 设计使然（GracePeriod 需要第二次观察；ProposalManager dedupe 防重复提案） | 观察运行成本，必要时加"已提案"短路 |
| GrowthIntegrationService 默认 ProposalStore 路径（data/proposals/proposals.jsonl）与 GrowthAdapter 的 growth_proposals.json 并存 | 两个提案存储 | 后续提案存储统一议题 |
| EligibilityFilter ledger 进程级单例（重启归零） | 设计使然 | R5 已验证跨重启语义不受影响（提案靠 ProposalStore 持久） |

---

## 附录：Phase 4.3-D Evidence Trace Repair（2026-08-12，顾问裁决立项）

**范围**：只修 H1 source_event_id 追踪缝隙，不扩范围、不改 schema。

| 项 | 内容 |
|---|---|
| 生产修复 | `growth_integration.py` accept_experience Step 7：source_event 补 `"id": f"evt_{memory_id}"` 键（保留 `"event_id"` 兼容；顺带修复了 process_event 的 `[event] id=` 日志恒为空） |
| 回归测试 | R1 追加断言：`proposal.source_event_id == f"evt_{journal记录id}"`（修复前恒 None） |
| 测试结果 | 4.3 + 4.2-D + growth 提案/审批相关 6 套件 **178P / 0F**；digest 基线 4F/1P 不变（既有债） |
