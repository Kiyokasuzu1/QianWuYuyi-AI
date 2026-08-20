# 羽依设计委员会——未来接口预留（design_committee_future）

> 状态：**设计接口文档，只写接口、不实现、不接线**
> 日期：2026-08-19　分支：develop/v1.1　HEAD：225d040
> 依据：AGENTS.md §6（多角色推理）+ §10（最终原则）+ P2.3 勘察 Phase 3-D 评估结论（接入点安全度约 60%）

---

## 一、定位与边界

"羽依设计委员会"（下称委员会）是未来用于人格/记忆/成长变更决策的**多角色评审机制**。

必须遵守 AGENTS.md §6 的硬约束：

- **多角色 ≠ 多人格**。评审角色只是不同认知视角（Architecture Reviewer / Safety Reviewer / Emotion Reviewer / Growth Reviewer），不是多个"羽依"。
- **羽依只能拥有一个 Identity Core**。所有评审意见最终收敛为**单一决策**。

本文件的地位：

- 只定义接口形状与约束，**不实现**；
- 不改权限系统、不接线委员会、不创建代码实体；
- 为未来实现（不在 P2.3-A 范围内）预留挂接面。

---

## 二、接口定义

### 2.1 委员会输入（ReviewRequest）

```
ReviewRequest:
    proposal            # 待审变更提案（复用现有 GrowthProposal / governance 提案载体）
    evidence            # 证据列表：事件、记忆引用、情绪轨迹、关系证据等
                        #   evidence[i] = {type, ref, summary, source_time}
    context_snapshot    # RuntimeContext 快照（评审时点的运行时状态）
                        #   依赖 P2.3-A RuntimeContext 统一后可得稳定快照
    permission_identity # 发起方身份（resolve_identity 输出：user / sandbox / admin）
                        #   委员会必须校验：proposal 发起者是否具备对应权限
```

### 2.2 委员会输出（CommitteeDecision）

```
ReviewOpinion:                          # 单个评审角色的独立意见
    reviewer_id        # architecture | safety | emotion | growth
    verdict            # APPROVE | REJECT | REQUEST_MORE_EVIDENCE
    rationale          # 理由（可审计文本）
    evidence_refs      # 引用的证据索引
    risk_notes         # 该视角下的风险备注

CommitteeDecision:
    verdict            # 最终统一决策：APPROVE | REJECT | REQUEST_MORE_EVIDENCE
    opinions           # 全部角色的 ReviewOpinion 列表
    decided_by         # 决策规则说明（如"全部角色 APPROVE 或无 REJECT"）
    timestamp
    audit_ref          # 决策全过程的审计记录引用
```

### 2.3 硬约束（不可协商）

1. **委员会不能直接修改任何状态**：memory / personality / relationship / growth 一律禁止直写。
2. 唯一变更链（AGENTS.md Priority 3 的流程，已有代码实体支撑）：

```
Proposal → Review（委员会多角色意见）→ Approval（审批队列）→ Apply（Adapter/Updater）→ Audit（before/after）
```

3. `REQUEST_MORE_EVIDENCE` 必须指明缺哪类证据（如"缺少情绪轨迹"、"缺少时间关联"），不得作为变相否决。
4. 委员会决策本身也必须审计留痕（谁、何时、基于什么证据、结论是什么）。

---

## 三、现有锚点（未来实现的既有支撑，不新增模块）

| 环节 | 现有实体 | 位置 |
|---|---|---|
| 身份与权限 | resolve_identity + 五扇 Permission Gate（纯函数） | src/security/identity.py:188；src/security/permission.py（can_modify_*） |
| Proposal 载体 | GrowthProposal（evidence_ids / source_event_id）；governance 提案 | src/growth/；src/admin/governance_provider.py:343/503 |
| Review | governance_provider.review_proposal / list_proposals | src/admin/governance_provider.py:679/713；/api/admin/governance/{personality,memory,growth}（src/admin/api/routes.py:2538-2589+） |
| Approval | ApprovalManager（历史落盘）| src/growth/approval_manager.py:63/117/550；SelfModelApprovalQueue（内存态）| src/personality/self_model_governance.py:185-240 |
| Apply | SelfModelUpdater → SelfModelStore.apply_change_proposal（硬契约唯一入口） | src/personality/self_model_governance.py:1-45；src/personality/self_model_store.py |
| Audit | record_audit_log（before/after） | src/audit/record.py:48 |

---

## 四、当前缺口（未来实现前必须补齐，均不属于本阶段）

| # | 缺口 | 说明 | 建议时机 |
|---|---|---|---|
| G1 | 审批队列内存态 | SelfModelApprovalQueue 重启丢失；ApprovalManager 历史虽落盘但 pending 队列不落盘 | P2.3-B（data/approval_queue.json） |
| G2 | 多角色评审会话无实体 | 四个 Reviewer 视角无任何代码/数据承载 | 委员会立项时新建（仅此一项允许新增模块，须回答 AGENTS.md Rule 4 四问） |
| G3 | 委员会唤醒机制缺失 | 无统一事件通知"有新提案待审"；6 套总线互不相通 | P2.3-A 总线收敛后 |
| G4 | RuntimeContext 快照不可序列化 | 请求状态散落实例属性 + dict，无法稳定快照 | P2.3-A RuntimeContext 统一后 |
| G5 | 旁路直写仍存在 | 7 处直写（见 wiring_status.md）未经治理收口 | P2.3-A 收口后 |

---

## 五、明确非目标（本阶段）

- 不实现委员会本体、评审角色、决策规则引擎。
- 不接线：不修改 orchestrator / governance_provider / approval_manager 任何行为。
- 不修改权限系统（五扇门语义保持 P2.1.3 现状）。
- 不新增任何代码文件；本文件是唯一产出。

---

## 六、审批要求

委员会本体立项时，必须依据 AGENTS.md Rule 4 回答四问（为何已有模块无法完成 / 如何接入生命周期 / 数据如何流动 / 如何测试），并对照本文件 §三 复用现有锚点，禁止另起炉灶。
