# P2.3-B.11 Phase 1 — GovernanceProposalLifecycle 消费链设计（只读分析）

> 记录时间：2026-08-20
> 本阶段只读分析 + 设计输出，未修改任何代码。

## 1. 当前链路与断点（B.10 完成态实测）

```
MutationRequest
    ↓ MutationGateway.evaluate()（五道检查）
NEED_REVIEW verdict
    ↓ save_pending_proposal()（B.10 Phase 3）
GovernanceProposalStore.append(proposals.jsonl)   ←—— 落账侧完成 ✅
adapter.pending_proposals.append(内存缓存)          ←—— 双写保留 ✅
    ↓
【断点】无任何消费方读取 proposals.jsonl：
        - 谁来审？→ 无审批入口
        - 审完去哪？→ 无状态迁移
        - 批准后谁应用？→ 无 apply 通道
        - 如何防重复应用？→ 仅 B.9 relationship adapter 有 mutation_id 去重
```

**关键存储约束**：GovernanceProposalStore 是 append-only JSONL（B.10 Phase 2 冻结），
**禁止改写已有行**——生命周期状态不能通过"更新 proposals.jsonl 里的 decision 字段"
表达，必须引入独立的**迁移事件流**。

## 2. 既有组件盘点（复用，不重建）

| 组件 | 位置 | B.11 复用方式 |
| --- | --- | --- |
| GovernanceProposalStore | src/governance/proposal_store.py | 只读取（load_records/list_pending）；新增迁移事件文件由 ProposalManager 管理 |
| ApprovalDecision | src/approval/approval_decision.py | 审批记录形状参照（TypedDict, reviewer 枚举）；**不修改该文件** |
| ApprovalManager | src/approval/approval_manager.py | growth 域自动治理（govern_proposal）；B.11 生命周期与其平行，不接线 |
| MutationAuditRecord | src/governance/audit_writer.py | Phase 2 审计留痕沿用同字段精神（mutation_id/domain/actor/decision/timestamp/reason） |
| IdentityCheck.PRIVILEGED_ACTORS | src/governance/checks/identity_check.py | 审批者身份校验参照（human 审批者未来接入） |

## 3. GovernanceProposalLifecycle 状态机设计

### 3.1 状态集（任务书冻结六态）

```
CREATED ──→ PENDING_REVIEW ──→ APPROVED ──→ APPLIED ──→ ARCHIVED
                  │                │           │
                  └──→ REJECTED ←──┘           │
                          │                    │
                          └────→ ARCHIVED ←────┘
```

| 状态 | 语义 | 谁触发 |
| --- | --- | --- |
| CREATED | 落账瞬间（proposals.jsonl 追加行） | Gateway（自动） |
| PENDING_REVIEW | 进入待审队列（初始可审状态） | ProposalManager.register()（自动） |
| APPROVED | 审批通过，等待 apply | approve()（**仅人工/审批者显式调用**） |
| REJECTED | 审批否决（终态之一） | reject()（显式） |
| APPLIED | 已应用到域状态 | mark_applied()（apply 执行件回告） |
| ARCHIVED | 归档终态（不再参与任何迁移） | archive()（显式/维护操作） |

### 3.2 合法迁移表（冻结，其余一律拒绝）

| from | to | 事件方法 | 守卫 |
| --- | --- | --- | --- |
| CREATED | PENDING_REVIEW | register | proposal_id 必须存在于 proposals.jsonl |
| PENDING_REVIEW | APPROVED | approve | reviewer 必填；**禁止自动 approve** |
| PENDING_REVIEW | REJECTED | reject | reviewer + reason 必填 |
| APPROVED | APPLIED | mark_applied | **一次性**：已 APPLIED 再调 → 阻断（防重复 apply） |
| APPROVED / REJECTED / APPLIED | ARCHIVED | archive | 终态收纳 |
| REJECTED | APPROVED | —— | **禁止**（否决不可翻案，需重新提案走全链） |

非法迁移 → `InvalidTransition` 异常（fail-closed），不产生任何写入。

### 3.3 存储设计：迁移事件流（append-only）

新文件 `<governance_data_dir>/proposal_lifecycle.jsonl`（与 proposals.jsonl 同目录、
同为 append-only、同目录守卫），一行一条迁移事件：

```json
{
  "event": "approve",                      // register/approve/reject/mark_applied/archive
  "proposal_id": "prop_xxx",
  "from_status": "PENDING_REVIEW",
  "to_status": "APPROVED",
  "reviewer": "human:pending_design",      // 见 §4
  "reason": "...",
  "actor": "proposal_manager",
  "timestamp": "2026-08-20T...Z",
  "trace_ref": {"mutation_id": "...", "request_id": "...", "trace_id": "..."}
}
```

**当前状态 = 重放迁移事件流的投影**（每次查询时重放；不缓存可变状态行）。
proposals.jsonl 永不被改写——append-only 原则贯穿全链。

## 4. 人工审批接口预留（本阶段不实现审批者鉴权）

- `approve(proposal_id, *, reviewer, reason)` / `reject(...)` 的 `reviewer`
  参数为必填显式参数——**代码层面不存在任何无 reviewer 的 approve 路径**，
  即"自动 approve"在 API 形状上被排除。
- reviewer 取值：本阶段接受任意非空字符串；预留命名空间：
  - `"human:<uid>"` —— 人工审批者（B.11 只预留，不鉴权）
  - `"system:<subsystem>"` —— 与既有 ApprovalDecision.ALLOWED_REVIEWERS=("system",)
    对齐的子系统审批（如未来 ApprovalManager 接线）
- **禁止清单**（ProposalManager 模块 docstring 冻结）：
  - 禁止自动 approve（无任何内部定时器/回调能触发 approve）
  - 禁止自动修改 personality/emotion/relationship 状态（mark_applied 只记录事实，
    apply 执行件由外部显式注入且回告，Manager 不持有任何域写句柄）

## 5. ProposalManager 职责边界（Phase 2 实现蓝图）

1. **读取** GovernanceProposalStore（只读消费方）
2. **状态迁移**（§3.2 表驱动 + InvalidTransition fail-closed）
3. **审批记录**（approve/reject 事件即审批记录，reviewer/reason 全留痕）
4. **防重复 apply**（APPROVED→APPLIED 一次性守卫 + mutation_id 已应用集合）
5. **audit trace**（迁移事件流本身即审计轨迹；audit_trace(proposal_id) 返回该
   proposal 的全部迁移事件按序）

不修改三域 adapter；不接 RuntimeCore；不写 data/users、
data/relationship_state.json、data/emotion_state.json。

## 6. 与后续阶段的接口

- **Phase 4 Inspector 增强**：pending 数量 = PENDING_REVIEW 投影计数；
  review 时间 = PENDING_REVIEW 事件 timestamp 与最后的差值；audit 完整性 =
  每条 proposal 的 trace_ref 三键齐全校验。
- **Phase 5 SelfObservation**：Observation → GrowthProposal 的未来接线点在
  PENDING_REVIEW 入口（register），本阶段只建 Observation 数据结构，不接线。
- **B.12（禁止提前做）**：自动人格修改 = 自动 approve + mark_applied 的组合，
  已在 §4 禁止清单中从 API 形状层面阻断。
