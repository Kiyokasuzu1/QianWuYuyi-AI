# P2.3-B.11 Governance Runtime Activation & Self Model Growth Foundation — 实施报告

> 记录时间：2026-08-20
> 任务目标：让 Governance 层从"落账基础设施"升级为"可运行的认知变化控制层"
> ——建立"AI 内部变化经过审核后才能成为自身变化"的闭环基础。
> 基线：docs/governance/p2_3_b11_baseline.md（A 状态 = B.10 完成状态）

## 1. 修改文件

| 文件 | 状态 | 内容 |
| --- | --- | --- |
| src/governance/proposal_manager.py | 新增 | GovernanceProposalManager（六态生命周期 + 审批 + 防重复 apply） |
| src/governance/mutation_adapter_base.py | 新增 | MutationAdapterBase ABC（统一五方法接口，三域暂不继承） |
| src/governance/governance_inspector.py | 修改 | +5 项只读统计（旧三方法与构造器不变） |
| src/personality/self_observation.py | 新增 | SelfObservation 冻结数据结构（六字段） |
| tests/test_governance_proposal_manager.py | 新增 | 7 个测试 |
| tests/test_b11_governance_runtime.py | 新增 | 7 个测试（任务书 6 项 + 全链路零污染） |
| docs/governance/p2_3_b11_baseline.md | 新增 | Phase 0 基线 |
| docs/governance/p2_3_b11_proposal_lifecycle_design.md | 新增 | Phase 1 消费链设计 |
| docs/governance/p2_3_b11_governance_runtime_report.md | 新增 | 本报告 |

未修改：三域 MutationAdapter、MutationGateway、ProposalStore、RuntimeCore、
config.yaml、data/ 任何文件。所有生产 flag 保持 False，governance_mode=legacy。

## 2. Proposal 生命周期

状态机（Phase 1 设计冻结，Phase 2 落地）：

```
CREATED ──register──→ PENDING_REVIEW ──approve──→ APPROVED ──mark_applied──→ APPLIED ──archive──→ ARCHIVED
                            │                      │                                       │
                            └──────reject──────→ REJECTED ────────────archive──────────────────┘
```

- **存储**：迁移事件流 `<governance_dir>/proposal_lifecycle.jsonl`（append-only，
  与 proposals.jsonl 同目录守卫）；当前状态 = 重放事件流的投影；落账文件永不被改写。
- **迁移表**：`VALID_TRANSITIONS` 7 条边（表驱动）；非法迁移抛 `InvalidTransition`
  （fail-closed，不产生写入）；REJECTED 不可翻案（无 → APPROVED 边）。
- **审批**：`approve(proposal_id, *, reviewer, reason)` —— reviewer 为必填
  keyword-only 参数，**签名层面不存在自动 approve 路径**；reviewer 命名空间预留
  `"human:<uid>"`（人工，未鉴权）/ `"system:<subsystem>"`（与既有
  ApprovalDecision.ALLOWED_REVIEWERS 对齐）。
- **防重复 apply**：双层守卫——proposal 维度（APPLIED 无 mark_applied 出边）+
  mutation 维度（`_applied_mutation_ids()` 跨 proposal 同 mutation 阻断，
  抛 `DuplicateApplyError`）。
- **边界**：mark_applied 只记录"外部执行件已应用"的事实；Manager 不持有任何
  personality/emotion/relationship 状态写句柄。

## 3. Governance Runtime 变化

- **消费链补全**：B.10 断点"Store 落账后无消费方"已闭合——
  Gateway → ProposalStore → **ProposalManager（register → approve/reject → mark_applied）** →
  Inspector 观测。治理层从"只写不读"变为有完整生命周期视图的可运行控制层。
- **MutationAdapterBase**（Phase 3）：统一 build_request/validate/route/apply/audit
  五方法 ABC；纯契约声明，无默认实现、无生产调用方；三域 adapter 未继承
  （旧 API 字节不变）——为未来三域收敛建立目标接口。
- **Inspector 增强**（Phase 4）：
  - `count_pending()`（PENDING_REVIEW 计数；未注入 manager 退化为落账计数）
  - `count_by_domain()`（各域 mutation 数量）
  - `reject_reason_stats()`（迁移事件流 reject 原因计数）
  - `review_times()`（待审停留秒数 / 已审耗时秒数 + reviewer）
  - `audit_completeness()`（落账记录 + 迁移事件的链路三键齐全率）
  - 全部只读；旧构造器（store=/data_dir=）与旧三方法完全兼容。

## 4. Self Model 基础变化

`src/personality/self_observation.py`：

- **SelfObservation**（frozen dataclass，六字段冻结）：observation_id /
  source_event / domain / evidence_refs / confidence / timestamp。
- 域枚举复用 `MUTATION_TARGETS` 六域（单一事实来源）；构造守卫：非法域、
  空证据、置信度越界均 ValueError；`from_event()` 为唯一标准构造入口
  （Event → Observation，只登记事实，不理解、不推断）。
- **不直接改变人格**（三重保障）：
  1. 结构层：纯数据结构 + 纯函数，无 I/O、无单例、无写句柄；
  2. 源级：模块不 import 任何人格写入口（测试源扫描 SelfModelManager/
     PersonalityResolver/GrowthEngine/repository.save 等 8 项禁词）；
  3. 流程层：`to_proposal_seed()` 只产出纯 dict 投影种子，创建 proposal /
     进治理链属于未来接线——冻结流程 Event → Observation → GrowthProposal
     → Governance → Personality update，本阶段只落地第一跳。
- **B.12 禁区未触碰**：无自动人格修改、无自主意识模块、无主动聊天、
  无世界模型、无多模态。

## 5. 测试结果

三套件共 **24/24 通过**：

| 套件 | 数量 | 覆盖 |
| --- | --- | --- |
| tests/test_mutation_governance_runtime.py（B.10） | 10/10 | 回归确认旧 API 兼容 |
| tests/test_governance_proposal_manager.py | 7/7 | 创建/pending/approve/reject/防重复/审计/append-only |
| tests/test_b11_governance_runtime.py | 7/7 | 生命周期全路径/审批守卫/审计保留/防重复/Observation 创建/Observation 不改人格/全链路零污染 |

**A/B 回归**（A = B.10 完成状态 68 文件，B = B.11 完成状态 70 文件；
HF_HUB_OFFLINE=1；10 文件/块 × 7 块流式运行）：

| 指标 | A | B | 结论 |
| --- | --- | --- | --- |
| 失败数 | 27 | 27 | 一致（B.5/B.7/B.9 时代既有失败） |
| 新增失败（comm -13） | — | **空** | ✅ 新增失败 = 0 |
| 消失失败（comm -23） | — | **空** | ✅ |
| 块退出码 | 1,0,1,1,1,0,1 | 1,0,1,1,1,0,1 | 完全一致 |
| data/ 修改 | 空 | 空 | ✅ 零污染 |

B 状态恢复后字节校验 6 文件 cmp 通过；B 块 b_06 汇总
"1 failed, 112 passed"（唯一失败为既有 test_20_orchestrator_health_check）。

## 6. 回滚方式

```bash
cd <repo>
# 1) 删除 B.11 新增文件
rm src/governance/proposal_manager.py src/governance/mutation_adapter_base.py \
   src/personality/self_observation.py \
   tests/test_governance_proposal_manager.py tests/test_b11_governance_runtime.py
# 2) governance_inspector.py 恢复 B.10 版（备份 /tmp/p29b11/backup_b11 为本会话产物；
#    正式回滚手工删除 Phase 4 新增的 5 个统计方法与 manager kwarg，恢复 B.10 原版）
# 3) docs 文件可留可删（无代码影响）
# 4) 验证：python -m pytest tests/test_mutation_governance_runtime.py -q → 10 passed
```

全部为增量文件 + 单文件增强，无状态迁移、无数据格式变化；
proposals.jsonl / proposal_lifecycle.jsonl 均为治理目录专属文件，删除即净。

## 7. 剩余债务

1. **审批者鉴权未实现**：reviewer 为自由字符串（"human:<uid>" 只预留命名空间），
   未接身份系统——人工审批入口的鉴权属于后续任务。
2. **APPLIED 执行件未接线**：mark_applied 只记录事实；"审批通过后谁执行域状态
   变更"（apply 执行件注册与调用）待 B.12+ 设计。
3. **三域 adapter 未继承 MutationAdapterBase**：B.11 只建契约；迁移涉及
   三域 629+439+578 行改动，需独立任务书。
4. **SelfObservation 消费链未接线**：Observation → GrowthProposal 的转换器
   未实现（本阶段冻结为只做第一跳）。
5. **Inspector 统计无持久化视图**：review_times 每次重放事件流（当前量级可忽略；
   事件流增长后需索引）。
6. **人工审批 UI/CLI 入口**：approve/reject 只有 API，无人机交互界面。
7. **datetime.utcnow() 弃用警告**：延续既有代码风格，未统一替换（同 B.10 债务）。
