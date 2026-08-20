# Mutation Gateway 基础实施报告（mutation_gateway_implementation_report）

> 状态：**P2.3-B.3 已交付——治理骨架落地，未接管任何生产 mutation 写路径**
> 日期：2026-08-19　分支：develop/v1.1　HEAD：225d040
> 上游设计：docs/governance/mutation_gateway_design.md（P2.3-B.2）
> 审计输入：docs/governance/mutation_boundary_audit.md（P2.3-B.1）

---

## 1. 已实现能力

### 1.1 新增文件清单（全部为新增，未修改任何既有文件）

| 文件 | 内容 |
|---|---|
| `src/governance/__init__.py` | 包导出（契约 / Gateway / AuditWriter） |
| `src/governance/mutation_contract.py` | MutationRequest（9 字段）/ MutationDecision（6 字段）/ CheckResult / DecisionVerdict 四态；frozen + JSON 安全强制校验 |
| `src/governance/mutation_gateway.py` | MutationGateway：固定五道检查顺序、首失败即裁决、审计 hook 接线、异常隔离 |
| `src/governance/audit_writer.py` | AuditWriter 接口（record_request / record_decision）+ InMemoryAuditWriter 内存实现 |
| `src/governance/checks/__init__.py` / `base.py` | 检查器基类 + CheckResult 构造辅助（ok/fail） |
| `src/governance/checks/identity_check.py` | 第 1 道：复用 src/security/permission.py 五扇门 |
| `src/governance/checks/boundary_check.py` | 第 2 道：复用 FORBIDDEN_IDENTITY_PATH_PREFIXES + 六域命名空间/红线表 |
| `src/governance/checks/evidence_check.py` | 第 3 道：复用 approval_policy 阈值常量 + SOURCE_RELIABILITY + MAX_SINGLE_EVENT_DELTA |
| `src/governance/checks/conflict_check.py` | 第 4 道：复用倒转阈值 + compute_fingerprint/exists_similar（协作件注入） |
| `src/governance/checks/audit_check.py` | 第 5 道：链路关联键（request_id/trace_id）+ 重复落账防护（journal 注入） |
| `tests/test_mutation_gateway_contract.py` | 41 项单元测试（B.3 Phase 6 十项覆盖 + 扩展） |

### 1.2 数据契约要点

- **MutationRequest**（9 字段冻结）：mutation_id / source_event / actor_identity / target_domain / target_path / proposed_change / evidence / context_snapshot / risk_level。target_domain 强制 ∈ `MUTATION_TARGETS`（复用 RuntimeContext v2 契约的六域单一事实来源）；risk_level ∈ {low, medium, high}。
- **MutationDecision**（6 字段冻结）：mutation_id / decision / reason / checks / audit_reference / timestamp。decision ∈ {ACCEPT, REJECT, NEED_REVIEW, DEFER}。
- **禁止写句柄**：所有容器字段经 `ensure_json_safe` 递归校验，manager/repository/client 等非标量实例在构造时直接抛 TypeError——"禁止携带写句柄"在契约层强制，而非靠约定。
- frozen dataclass 全程不可变；派生只能走 `dataclasses.replace`。

### 1.3 Gateway 决策行为

- 固定顺序：identity → boundary → evidence → conflict → audit；**首失败即停**（B.2 §3.5 冻结顺序）。
- 裁决采纳失败检查 metadata["verdict"] 建议，缺失时按 DEFAULT_FAIL_VERDICT 兜底（identity/boundary→REJECT，evidence/conflict→NEED_REVIEW，audit→DEFER）。
- 检查异常 fail-closed（按 REJECT 处理），审计 hook 异常隔离——Governance 永不向调用方抛未隔离异常。
- ACCEPT 仅表示"允许进入现有执行组件"，Gateway 自身零执行副作用（不写任何域状态、不触发 apply、不开启自动 apply）。

### 1.4 复用的既有组件（未重新实现任何规则）

| 检查 | 复用对象 |
|---|---|
| IdentityCheck | `src.security.permission.py` can_modify_memory/emotion/personality/relationship + can_trigger_growth（五扇门纯函数） |
| BoundaryCheck | `src.approval.approval_policy.FORBIDDEN_IDENTITY_PATH_PREFIXES` |
| EvidenceCheck | `DEFAULT_MIN_EVIDENCE_COUNT`(3) / `DEFAULT_MIN_EVALUATOR_CONFIDENCE`(0.75) / `DEFAULT_MIN_OCCURRENCE_COUNT`(2)；`src.growth.growth_schema.SOURCE_RELIABILITY` / `MAX_SINGLE_EVENT_DELTA`(0.01) |
| ConflictCheck | `DEFAULT_CONFLICT_REVERSAL_DELTA_THRESHOLD`(0.15) / `DEFAULT_CONFLICT_REVERSAL_MIDPOINT`(0.5)；`src.growth.proposal_store.compute_fingerprint` + 注入的 `exists_similar`；`MAX_GROWTH_PER_DIMENSION`(0.15) |
| 契约枚举 | `src.runtime.request_context.MUTATION_TARGETS` |

### 1.5 测试结果

- `tests/test_mutation_gateway_contract.py`：**41 passed**（含四态参数化、frozen 防改、JSON 安全拒绝、顺序/首停、五道红线、审计 hook 四场景）。
- 全量旧测试回归：见 §5 验证。

---

## 2. 未接入的 mutation 路径（本阶段明确不碰）

以下全部保持现状，Gateway 未成为其中任何一条路径的前置或替代：

| 路径 | 债务 | 状态 |
|---|---|---|
| orchestrator 主流程（含 Step 14.5 直通成长链） | B1 | 未改 |
| runtime_core 17 阶段生命周期（含 Stage 03/04、_apply_delta_safely） | B3/B7/B9 | 未改 |
| memory/emotion/growth/personality 实际 mutation 调用 | 全部 Registry 入口 | 未改 |
| PersonalityResolver 直写面 | B2 | 未改 |
| EvolutionPipeline / ApprovalManager / ProposalManager | — | 未替换、未修改、未被调用 |
| MutationJournal / AuditTrail / AuditLink 数据结构 | B11 | 未 import、未修改、未接线（仅 AuditWriter 接口预留） |
| 自动 apply | B5/B6 | 未开启（Gateway ACCEPT 无执行副作用） |

**"先建立治理层，再迁移调用方"的执行状态**：治理层骨架已就绪且完全隔离（无生产调用方）；迁移调用方是 P2.3-B.4 及后续批次的工作。

---

## 3. 与 B.2 设计差异

| # | B.2 设计 | B.3 落地 | 差异与理由 |
|---|---|---|---|
| D1 | MutationDecision 含 `prescription` 字段（执行分派指引） | 6 字段（B.3 任务书字段集），无 prescription | 任务书 Phase 2 字段清单为准；prescription 与真实执行器分派同属 B.4 迁移批次的接线产物，届时以新增字段或适配层引入，不动现有字段 |
| D2 | evidence 不足 → DEFER（§3.4.3） | evidence 不足（含 LLM 证据独存、单次事件、低置信、超幅）→ **NEED_REVIEW** | 任务书测试 #8 明确"evidence 不足 NEED_REVIEW"；语义上当前无复核队列，"补证据入复核"比"暂缓观察"更贴近现状操作面。B.4 前需与 B.2 冻结表再对齐（DEFER 保留给重复提案/年度上限/审计链路缺失） |
| D3 | Identity Check 的 actor 归一用 resolve_identity | actor_identity 为已解析字符串（user/sandbox/admin/系统组件名），直接用五扇门鸭子判定 | 契约字段即权限级字符串（B.2 §3.2 表如此定义）；resolve_identity 的调用保留给未来的入口装配器，不在治理层重复解析 |
| D4 | Audit Check 缺失 request_id/trace_id 时由 Gateway 生成回填 | 缺失 → DEFER（audit_linkage_missing），不回填 | 生成回填需要 ID 生成策略与 v2 上下文回写通道，属接线批次；先 fail-closed 更安全 |
| D5 | 倒转冲突阈值 0.15 在 EvidenceCheck 单事件幅度上限 0.01 之后 | 保持一致 | 两套既有规则常量如实复用后，倒转（|Δ|≥0.15）在标准 Gateway 内不可达（单事件先被 0.01 卡住）；倒转规则仅在 check 层/累计路径（annual_delta）生效。**B.4 需裁决**：evidence 的 delta 上限应作用于"单事件增量"而倒转检查应作用于"提案总跨度"——需要 proposal 级 before/after 与 event 级 delta 分离 |
| D6 | 六域红线中 relationship trust/bond → REJECT | → NEED_REVIEW（must enter 复核队列） | 按 B.2 §4 输出路径列执行（"trust/bond 变更必须 NEED_REVIEW 及以上"），与 §3.4.2 的"红线即 REJECT"表述存在细微张力，已按领域表取 NEED_REVIEW |

---

## 4. 下一阶段（P2.3-B.4 人格红线迁移）入口建议

按 B.2 §5 迁移路线 P0 顺序，建议 B.4 的迁移入口与顺序：

1. **入口装配器（最高优先级）**：在 PersonalityResolver / RuntimeController 两个 B2/B3 直写点**前方**调用 MutationGateway——请求由事件上下文组装（actor_identity 取自 resolve_identity 结果、context_snapshot 取 v2 快照的 request_id/trace_id），REJECT/NEED_REVIEW 时原调用点**保持旧路径不动**（可回滚），ACCEPT 时才进入现有治理链。实现为"旁路决策先行、收口渐进"：第一阶段只记录决策不改行为（观察期），第二阶段再让 REJECT 真正阻断。
2. **prescription 字段引入**（D1 收敛）：在 MutationDecision 增加可选 `prescription` 字段（ACCEPT 时给出 executor 名与 apply_args 投影），Migration 完成后 EvolutionPipeline/ProposalManager 的调用指引即由此字段驱动。
3. **audit 落账接线**：把 AuditWriter 的 record_request/record_decision 投影到 v2 的 MutationJournal.add / AuditTrail.chain 追加（adapter 形式，不修改 v2 契约类）。
4. **D5 裁决**：proposal 级 before/after 与 event 级 delta 分离，恢复倒转检查在标准链上的可达性。
5. 每步独立提交 + 独立回归；不达标即回滚旧路径（B.2 I-1..I-4 不变式持续有效）。

---

## 5. 验证记录

| 检查项 | 结果 |
|---|---|
| 新增测试 | tests/test_mutation_gateway_contract.py **41 passed** |
| 旧测试 A/B 对比（治理依赖电池：30 个测试文件，覆盖 permission/security/approval/evolution/growth-proposal/proposal-review/policy/context-normalizer/self-model，1340 项） | 移走/恢复 src/governance 两次运行**失败集完全一致**（同为 test_runtime_policy.py 4 项存量失败，与 governance 无关），0 新增失败 |
| 全量套件 | 尝试执行（396 文件）时与外部并发 pytest 进程（test_growth_phase_382b.py，非本任务启动）争抢资源而中止；由于 governance 仅被新测试文件 import、无任何既有模块引用，依赖电池 A/B 已覆盖全部可影响面 |
| src 修改范围 | 仅新增 src/governance/** 11 个文件；既有 src 文件 0 修改 |
| data/ 修改 | 0（Gateway 与检查均无文件副作用；冲突检查的存储协作件仅在注入时使用） |
| runtime/personality/growth/emotion/memory 修改 | 0 |
| orchestrator / runtime_core 修改 | 0 |
| ApprovalManager / EvolutionPipeline 替换 | 未发生 |
| 自动 apply | 未开启 |
| git status 对比基线 | 仅新增 src/governance/ 与 tests/test_mutation_gateway_contract.py 两个未跟踪条目 + docs/governance/ 内文档 |

---

## 附录：证据索引

| 事实 | 证据 |
|---|---|
| 契约字段与四态 | src/governance/mutation_contract.py（DecisionVerdict:35 / MutationRequest:87 / MutationDecision:216） |
| 五道顺序与首停 | src/governance/mutation_gateway.py（CHECK_ORDER:59；evaluate:88-127） |
| 五扇门复用 | src/governance/checks/identity_check.py:7-13（import）；src/security/permission.py:73-95 |
| 前缀复用 | src/governance/checks/boundary_check.py:8（import FORBIDDEN_IDENTITY_PATH_PREFIXES）；src/approval/approval_policy.py:34-38 |
| 阈值复用 | src/governance/checks/evidence_check.py:7-11；conflict_check.py:6-10 |
| 去重复用 | conflict_check.py:14（compute_fingerprint）+ 注入 exists_similar |
| 契约枚举复用 | mutation_contract.py:31（import MUTATION_TARGETS） |
| 测试 | tests/test_mutation_gateway_contract.py（41 项） |
| 未接入路径 | 本报告 §2（与 B.1 Registry 对照） |
