# P2.3-B.4 Personality Mutation Boundary Migration 完成报告

> 阶段：P2.3-B.4（人格红线迁移）
> 分支：develop/v1.1 ｜ HEAD：225d040 ｜ 日期：2026-08-19
> 前置文档：docs/governance/mutation_boundary_audit.md（B.1）、
> docs/governance/mutation_gateway_design.md（B.2）、
> docs/governance/mutation_gateway_implementation_report.md（B.3）

本阶段目标：将 Personality 领域绕过 Mutation Gateway 的直接修改路径迁移到治理链，
重点处理 B.1 审计发现的 P0 人格红线问题（B2：PersonalityResolver 静默直写；
B3：RuntimeController 伪审批直写）。**不是删除旧能力，而是建立安全迁移层。**

---

## 1. 修改文件列表

| 文件 | 变更 | 说明 |
|---|---|---|
| `src/personality/mutation_adapter.py` | 新增（~260 行） | PersonalityMutationAdapter：Legacy intent → MutationRequest → MutationGateway → 裁决执行约定；迁移开关；伪审批 ID 防线；审计接线 |
| `src/personality/personality_resolver.py` | 修改（+135 行） | resolve() 由「计算+修改」变「计算+提议」（flag 门控）；新增 `_propose_trait_mutation` / `_propose_self_model_mutation`；新增 `mutation_requests` 观测字段 |
| `src/runtime/runtime_controller.py` | 修改（+61 行） | `_apply_delta_safely` 增加 flag 门控分支（Gateway 决策替代 p_rt_*/a_rt_* 直写）；`handle_message` 的 delta_applied 语义如实化；新增 adapter 实例与 outcomes 观测 |
| `tests/test_personality_mutation_gateway.py` | 新增（9 个测试） | 覆盖 B.4 Phase 6 任务书 7 项 + 2 项附加保证 |
| `docs/governance/p24b4_personality_mutation_migration_report.md` | 新增 | 本报告 |

未触碰：`src/governance/`（B.3 交付 11 文件保持原样）、
`data/`（0 修改）、`RuntimeContext`、`memory/emotion/growth`、
权限系统（`src/security/permission.py` 未改一行）。

## 2. Legacy mutation 入口数量变化

B.1 审计中 Personality 域 P0 红线直写入口共 **5 处**（B2 债务 4 处 + B3 债务 1 处）：

| # | 位置（迁移前） | 直写内容 | 治理状态 |
|---|---|---|---|
| 1 | personality_resolver.py:158/173 | `evolution_engine.update_trait` → `self._trait_states[dim] = updated_state`（每次 resolve 累积漂移） | 静默直写 |
| 2 | personality_resolver.py:167 | `personality_history.record_change(...)`（无 proposal/approval ID） | 静默直写 |
| 3 | personality_resolver.py:337 | `self_model_store.update(...)`（含 `_save_to_disk()` 落盘） | 静默直写 |
| 4 | personality_resolver.py:351 | `self._self_model_adapter.apply_external_change(...)`（空 proposal_id） | 静默直写 |
| 5 | runtime_controller.py:495-504 | `build_evolution_record(proposal_id="p_rt_*", approval_id="a_rt_*")` → `ps.apply_evolution(rec)` | **伪审批直写**（P0 红线） |

变化后：

- **迁移开关开启（`personality_mutation_gateway_enabled=True`）**：5 处直写全部
  转为 `MutationRequest → MutationGateway` 提议，直写数量 **0**。
- **迁移开关关闭（默认 False）**：5 处旧路径原样保留，字节不变
  （A/B 回归证明行为一致，见 §6）。

## 3. 新 MutationRequest 流程

```
Legacy intent（Resolver 漂移 / RuntimeController 消息增量）
    ↓
PersonalityMutationAdapter.build_request()      # 9 字段契约；域仅限 personality/self_model
    ↓                                            # 任何字段携带 p_rt_*/a_rt_* → ValueError
MutationRequest
    ↓
（route 兜底防线：手工构造的请求携带伪审批 ID → 直接 REJECT，不进 Gateway）
    ↓
MutationGateway.evaluate()                       # 固定五道：Identity→Boundary→Evidence→Conflict→Audit
    ↓
MutationDecision（ACCEPT / REJECT / NEED_REVIEW / DEFER）
    ↓
裁决执行约定：
  ACCEPT      → 注入 apply_route 时进入下游 Proposal/Approval 链（返回 True 才记 applied）
                未注入 → applied=False（禁止自动 apply）
  REJECT / NEED_REVIEW / DEFER → 不修改任何状态
    ↓
AuditWriter.record_request + record_decision     # request_id / mutation_id / decision / audit_reference
```

Resolver 迁移模式下的语义：resolve() 仍输出人格向量，但 trait 漂移与 SelfModel
重建不再静默提交——漂移只生成 MutationRequest（输出值保持已批准状态的旧值），
未经 Gateway ACCEPT 的变化不生效。RuntimeController 同理：消息增量先经治理，
未 ACCEPT 时 `delta_applied` 如实为空。

## 4. 哪些入口已封锁（开关开启后）

1. **Resolver trait 漂移直写**：`_trait_states` / `personality_history` 不再被
   resolve() 修改；漂移转为 `personality.traits.<dim>` MutationRequest。
2. **Resolver SelfModel 直写**：`self_model_store.update` 与
   `apply_external_change` 被替换为 `self_model.update` MutationRequest。
3. **RuntimeController 伪审批直写**：迁移模式下不再生成 `p_rt_*/a_rt_*`、
   不再调用 `ps.apply_evolution`；改为 Gateway 决策（当前证据条件下单轮消息
   delta 通常得到 NEED_REVIEW，即不落任何状态）。
4. **伪审批 ID 双重防线**：
   - `build_request()` 发现任何字段含 `p_rt_*`/`a_rt_*` → 抛 `ValueError`（无法生成请求）；
   - `route()` 对手工构造绕过 build_request 的请求 → 直接 REJECT（不进入 Gateway 放行逻辑）。
5. **identity path 永远拒绝**：`identity.core.*` / `identity.origin.*` /
   `manifesto.*` 路径经 BoundaryCheck 硬拒（REJECT），且 PersonalityState 内部
   EP-4 二次保护不受影响。

## 5. 哪些旧入口暂时保留（渐进迁移策略）

- **迁移开关默认 `False`**：上述 5 处旧直写路径完整保留，行为与迁移前字节一致
  （已由 A/B 回归与 legacy 行为测试双重验证）。
- **旧 API 全部保留**：`PersonalityResolver.resolve()` / `get_trait_states()`、
  `RuntimeController.handle_message()` / `_apply_delta_safely()` 签名不变；
  `EvolutionPipeline` / `ApprovalManager` / `ProposalStore` 未被替换；
  合法演化链路（Proposal → Approval → EvolutionPipeline → apply_evolution）未动。
- 保留理由（AGENTS.md Priority 4）：迁移层是安全网而非大重构；开关开启即封锁，
  关闭即回退旧行为，灰度可控、可回滚。

## 6. 测试结果

**新增测试**：`tests/test_personality_mutation_gateway.py` 9/9 通过
（Resolver 不直改 trait、Resolver 生成 MutationRequest、reject 不改状态、
accept 才允许进入 apply adapter、伪 approval ID 拒绝、identity path 拒绝、
audit 记录含拒绝、开关默认关闭旧行为不变、RuntimeController 迁移模式不伪造直写）。
B.3 契约测试 `tests/test_mutation_gateway_contract.py` 41/41 仍全绿。

**A/B 回归对比**（36 个文件电池：Personality 15 / Evolution 9 / Approval 3 /
Governance 2 / Context 6 / RuntimeController 2；共 ~1030+ 测试）：

| 运行 | 代码状态 | 结果 |
|---|---|---|
| A（迁移前） | resolver/controller = HEAD，新文件移除 | 8 failed / 1026 passed |
| B（迁移后） | 本阶段全部改动就位 | 8 failed / 1035 passed |

- **FAILED 集合 diff：完全一致（新增失败 = 0）**；B 多出的 9 个 passed
  恰为新增测试文件（9/9）。
- 8 个失败均为迁移前已存在（A 运行同样失败），与 B.4 无关：
  `test_personality_growth_runtime.py::test_01_end_to_end_lifecycle`、
  `test_personality_growthstate_authority.py::test_pipeline_shares_state_through_runtime_core`、
  `test_phase_3_8_0_personality_runtime.py` 3 项 + `test_phase_3_8_0_summary`、
  `test_r276_real_chain_audit.py` T23 2 项。
- `data/` 0 修改；`src/security/permission.py` 0 修改；`src/governance/` 0 修改。

## 7. 回滚方式

**运行时回滚（零成本）**：迁移开关默认 `False`，任何部署都不受影响；
若灰度期间开启开关出现问题，调用
`set_personality_mutation_gateway_enabled(False)`（或重启进程）即回到旧路径。

**代码回滚**：

```bash
git checkout -- src/personality/personality_resolver.py src/runtime/runtime_controller.py
rm src/personality/mutation_adapter.py tests/test_personality_mutation_gateway.py
```

两个被修改文件在 HEAD 无其他未提交改动（本阶段前为 clean），回滚后即与迁移前
完全一致；新增文件删除后无残留引用（旧测试均不 import mutation_adapter）。

---

## 附录 A：Phase 1 只读审计结论（Resolver Mutation Map）

- resolve() 是「计算 + 修改」：6 个 core_dimensions 每轮经
  `evolution_engine.update_trait` 计算后无条件提交 `_trait_states`；
  |Δ|>0.005 时追加 `personality_history.record_change`（均无审批 ID）。
- SelfModel 链路：`should_update()` 为真时 `self_model_store.update()`（落盘）
  与注入 adapter 的 `apply_external_change()` 同步直写。
- RuntimeController：`_apply_delta_safely` 伪审批 ID（`p_rt_{turn_uuid}` /
  `a_rt_{turn_uuid}`，confidence=0.78）直写 `PersonalityState.apply_evolution`，
  绕过 Proposal/Approval 全链。
- 合法链路确认：`evolution_pipeline.py:170` / `personality_evolution_pipeline.py:329`
  的 apply 均为 approved-only，不在红线内。

## 附录 B：合规自查（AGENTS.md）

- **身份连续性（Priority 1）**：identity 路径经 BoundaryCheck 与 EP-4 双重硬拒；
  迁移模式输出值只取已批准状态，未批准变化不生效。
- **Priority 3（事件→理解→评估→Proposal→审核→应用）**：迁移模式将全部直写
  转为 MutationRequest → Gateway 五道检查 → （ACCEPT 才可能）进入
  Proposal/Approval 链；无自动 apply。
- **Priority 4（小修改、保留旧接口、加适配层）**：全部改动以 flag 门控增量实现，
  旧路径未删除；A/B 证明旧行为不变。
- **Rule 4（新增模块四问）**：为何已有模块不能完成——B.3 Gateway 是惰性骨架，
  缺「旧调用方→契约」的转换+裁决执行层；如何接入生命周期——Resolver 与
  RuntimeController 的直写点即接入点（flag 门控）；数据如何流动——见 §3；
  如何测试——9 项新测试 + A/B 失败集对比。
