# Proposal Schema 迁移计划（PROPOSAL_SCHEMA_MIGRATION_PLAN.md）

> 制定时间：2026-07-30
> 适用阶段：Phase 5.4
> 状态：**规划完成，不立即执行迁移**（避免破坏 Phase 3.5.x 审批流程）
> 强约束：**禁止破坏现有数据**

---

## 1. 背景

架构审计发现系统存在 **两个并行的 `GrowthProposal` 数据模型**：

| Schema | 文件 | 主键 | 状态机 | 消费者 |
| --- | --- | --- | --- | --- |
| **类型A** | `src/growth/proposal/proposal.py` | `proposal_id` | pending / approved / rejected / applied | `ProposalStorage`, `ProposalReviewer`, `Admin Governance` |
| **类型B** | `src/contracts/growth_schema.py` | `id` | proposed / approved / rejected | `GrowthAdapter`, `ApprovalManager`, `PersonalityAdapter`, `ProposalEvaluator` |

两个 Schema **不兼容**，数据存储在**不同的 JSON 文件**中。这导致：

- Admin 提交的 Proposal（类型A）**永远不会**被 ApprovalManager 审批
- Admin approve 的 Proposal（类型A）**永远不会**被 PersonalityAdapter apply
- Admin 与系统自然成长走两条互不相通的路径

---

## 2. 设计目标

设计 `GrowthProposalAdapter`，实现：

```
proposal.py (类型A)                contracts/growth_schema.py (类型B)
        |                                       ^
        |                                       |
        +---> GrowthProposalAdapter --------->  |
        |        (新增)                          |
        |                                       |
        ↓                                       |
ProposalStorage  ← 写           read ←  ApprovalManager
                                          (类型B 消费者)
```

**核心思路**：不强制替换任何现有 Schema，而是在 GovernanceProvider 提交时**同时镜像**到类型B 的存储（反之亦可），让两种 Schema 都能被对应消费者识别。

---

## 3. 架构评估

### 3.1 哪个是真正 runtime 数据模型？

**两个都是，但服务于不同目的**：

- **类型A**：Admin / 用户可见的"完整生命周期"模型（含 reviewer/priority/expires 等管理字段）
- **类型B**：Growth 系统的"语义"模型（含 ChangeItem 列表、evaluator_meta 等业务字段）

### 3.2 PersonalityAdapter 与 ProposalStorage 是否存在类型断裂？

**是**。PersonalityAdapter 通过 `src/runtime/adapters/growth_adapter.py` 间接使用 `src/contracts.growth_schema.GrowthProposal`，**不识别** `src.growth.proposal.proposal.GrowthProposal`。

**影响**：Admin Governance 写入的 Proposal **永远不会被 PersonalityAdapter apply**。

### 3.3 ApprovalManager 是否能处理 Admin Governance 创建的 Proposal？

**不能**。`src/growth/approval_manager.py` 显式 `from src.contracts.growth_schema import GrowthProposal`，无法处理类型A。

**影响**：Admin 审查的 approve/reject **只更新了类型A 的 status**，对类型B 实际流程无影响。

---

## 4. 迁移策略

### 4.1 设计原则

1. **不删除** 任何现有 Schema
2. **不修改** 现有 Proposal 消费者
3. **只新增** 适配层
4. **不破坏** 现有 JSON 数据
5. **保持** Admin 与 Runtime 两端可独立运行

### 4.2 推荐方案：双写镜像（Mirror Adapter）

**核心思路**：GovernanceProvider 在写入类型A 存储的**同时**，将 Proposal 镜像到类型B 存储（通过 `GrowthProposalAdapter.to_type_b()` 转换）。

**不推荐方案**：

- ❌ **方案A：替换 Schema**：破坏现有 Phase 3.5.13 ApprovalManager 测试
- ❌ **方案B：强制重定向**：需要修改 ProposalStorage，影响 41 个现有测试
- ❌ **方案C：合并存储文件**：导致单例权威破坏，违反 Phase 4.x 强约束

### 4.3 双写镜像架构图

```
Admin POST /admin/api/admin/governance/personality/propose
  ↓
GovernanceProvider.propose_personality_change()
  ↓
  ┌────────────────────────────────────────────────────────┐
  │  1. 构造类型A Proposal（现有逻辑）                       │
  │  2. storage.save(proposal_a)              → 类型A 存储  │
  │  3. type_b = GrowthProposalAdapter.a_to_b(proposal_a)  │
  │  4. growth_adapter.store_proposal(type_b) → 类型B 存储  │
  │  5. 返回 proposal_id (类型A)                             │
  └────────────────────────────────────────────────────────┘
  
  之后：
  
  Admin POST /admin/api/admin/governance/growth/review
  ↓
GovernanceProvider.review_proposal()
  ↓
  ┌────────────────────────────────────────────────────────┐
  │  1. 更新类型A Proposal status                            │
  │  2. storage.save()                                       │
  │  3. GrowthProposalAdapter.a_to_b() → 更新类型B 状态      │
  │  4. growth_adapter.update_proposal_status()              │
  └────────────────────────────────────────────────────────┘
  
  最终：
  
  ApprovalManager 检测到类型B Proposal 状态变更
  ↓
  PersonalityAdapter.apply_proposal()
  ↓
  PersonalityResolver 实际更新 ← 真正生效！
```

### 4.4 GrowthProposalAdapter 设计

**新增文件**：`src/growth/proposal/schema_adapter.py`

```python
"""
GrowthProposalAdapter - 兼容两种 GrowthProposal Schema

不修改任何现有 Schema，仅提供双向转换：
  - a_to_b(type_a: src.growth.proposal.proposal.GrowthProposal) -> src.contracts.growth_schema.GrowthProposal
  - b_to_a(type_b: src.contracts.growth_schema.GrowthProposal) -> src.growth.proposal.proposal.GrowthProposal
  - sync_status(type_a, type_b): 同步状态变更

字段映射策略：
  - 状态：pending → proposed, approved → approved, rejected → rejected, applied → accepted
  - 变更：affected_dimensions → proposed_changes (ChangeItem 列表)
  - 证据：evidence → evidence_ids
  - 元数据：metadata → evaluator_meta
"""

from src.growth.proposal.proposal import GrowthProposal as ProposalA
from src.contracts.growth_schema import GrowthProposal as ProposalB, ChangeItem


class GrowthProposalAdapter:
    """兼容两种 Schema 的桥接器"""

    # 状态映射
    STATUS_A_TO_B = {
        "pending": "proposed",
        "approved": "approved",
        "rejected": "rejected",
        "applied": "accepted",  # 注意：类型B 用 accepted_at 表示
    }
    STATUS_B_TO_A = {
        "proposed": "pending",
        "approved": "approved",
        "rejected": "rejected",
        "accepted": "applied",  # 反向
    }

    @classmethod
    def a_to_b(cls, a: ProposalA) -> ProposalB:
        """类型A → 类型B"""
        proposed_changes = []
        for trait, delta in (a.affected_dimensions or {}).items():
            proposed_changes.append(ChangeItem(
                path=trait,
                before=a.before_state.get(trait),
                after=a.after_state.get(trait),
                reason=a.reason,
            ))

        b_status = cls.STATUS_A_TO_B.get(a.status, "proposed")
        b = ProposalB(
            id=a.proposal_id,
            source_event_id=a.source_event_id or None,
            proposed_changes=proposed_changes,
            confidence=a.confidence,
            evidence_ids=list(a.evidence or []),
            evaluator_meta={
                **(a.metadata or {}),
                "source_proposal_a": True,
                "origin": a.source,
                "priority": a.priority,
                "admin_actor": a.user_id,
            },
            timestamp=a.timestamp,
            status=b_status,
            accepted_at=a.applied_at if b_status == "accepted" else None,
            rejected_at=a.reviewed_at if b_status == "rejected" else None,
        )
        return b

    @classmethod
    def b_to_a(cls, b: ProposalB) -> ProposalA:
        """类型B → 类型A"""
        from src.growth.proposal.proposal import GrowthProposal as ProposalA
        affected = {}
        before = {}
        after = {}
        for c in (b.proposed_changes or []):
            affected[c.path] = (c.after or 0) - (c.before or 0) if c.before is not None else (c.after or 0)
            before[c.path] = c.before or 0.0
            after[c.path] = c.after or 0.0

        a_status = cls.STATUS_B_TO_A.get(b.status, "pending")
        a = ProposalA(
            proposal_id=b.id,
            timestamp=b.timestamp,
            proposal_type="personality",  # 默认（类型B 不区分）
            status=a_status,
            source=f"b_mirror:{b.evaluator_meta.get('origin', '')}",
            source_event_id=b.source_event_id or "",
            user_id=b.evaluator_meta.get("admin_actor", "system"),
            affected_dimensions=affected,
            before_state=before,
            after_state=after,
            confidence=b.confidence,
            reason="mirror_from_type_b",
            evidence=list(b.evidence_ids or []),
            priority=b.evaluator_meta.get("priority", "medium"),
            reviewer_id="" if a_status == "pending" else "system",
            review_comment="",
            reviewed_at=b.rejected_at if a_status == "rejected" else (b.accepted_at if a_status == "applied" else None),
            applied_at=b.accepted_at,
            applied_by="system" if a_status == "applied" else "",
            metadata={**(b.evaluator_meta or {}), "mirrored_from_b": True},
        )
        return a

    @classmethod
    def sync_status_a_to_b(cls, a: ProposalA) -> ProposalB:
        """仅同步状态字段（其他字段保留）"""
        b = cls.a_to_b(a)
        return b
```

---

## 5. 实施步骤（未来 Phase）

### Phase 5.4（本阶段）✅
- [x] 架构审计：识别双 Schema 问题
- [x] 设计 GrowthProposalAdapter
- [x] 输出本迁移计划文档
- [ ] **不实施**（避免破坏现有 Phase 3.5.x 流程）

### Phase 5.5 / 5.6（未来）
- [ ] 在 `GrowthProposalAdapter` 中**新增** `schema_adapter.py`
- [ ] 在 `GovernanceProvider.propose_personality_change` 中增加双写
- [ ] 在 `GovernanceProvider.review_proposal` 中增加状态同步
- [ ] 新增 `tests/test_proposal_schema_adapter.py` 验证双向转换
- [ ] **保持** 所有现有测试通过
- [ ] **保持** Admin 现有行为不变
- [ ] **不修改** 任何类型A / 类型B 现有消费者

### 长期（Phase 6.x）
- [ ] 设计统一 Proposal Schema（合并类型A + 类型B）
- [ ] 提供迁移脚本（数据搬运）
- [ ] 灰度切换：双写 → 单写
- [ ] 废弃旧 Schema

---

## 6. 数据兼容性保证

### 6.1 不破坏现有数据

- ✅ 类型A JSON 文件 `data/growth/proposals/proposals.json` 保留
- ✅ 类型B JSON 文件 `data/proposals/growth_proposals.json` 保留
- ✅ 新增双写时，**只追加**，不覆盖
- ✅ 现有所有读路径**完全不变**

### 6.2 新增数据

- 双写时，类型A 写入现有 `data/growth/proposals/proposals.json`
- 双写时，类型B 写入现有 `data/proposals/growth_proposals.json`
- 通过 `metadata.source_proposal_a = True` / `metadata.mirrored_from_b = True` 标记来源

### 6.3 回滚保证

如果双写导致问题，**只回滚** GovernanceProvider 内部的双写逻辑（不修改任何存储数据）：
- 删除 `growth_adapter.store_proposal()` 调用
- 删除 `growth_adapter.update_proposal_status()` 调用
- 现有 41 个 Phase 5.3 测试 + 27 个 Phase 5.4 测试不变

---

## 7. 风险评估

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| 类型B 重复写入导致 GrowthAdapter 误以为有新 Proposal | 🟡 中 | 使用 `source_proposal_a=True` 标记，ProposalEvaluator 跳过 |
| 双写时类型B 字段缺失导致 PersonalityAdapter 失败 | 🟡 中 | 完整填充 ChangeItem（含 before/after/reason）|
| 类型A/B 状态机不同步 | 🟡 中 | `review_proposal` 中显式调用 sync |
| 数据存储增长加快 | 🟢 低 | 复用现有 MAX_PROPOSALS 限制 |
| 双 Schema 概念混淆 | 🟡 中 | 本文档 + 命名空间隔离 |

---

## 8. 验证策略

未来实施时，必须验证：

- [ ] `GrowthProposalAdapter.a_to_b()` 与 `b_to_a()` 双向转换无损
- [ ] `GovernanceProvider.propose_personality_change` 双写后类型B 文件中能找到对应 Proposal
- [ ] Admin approve 类型A 后，类型B 状态同步
- [ ] PersonalityAdapter.apply_proposal 看到类型B 的"新" Proposal（来自 Admin 镜像）能正常 apply
- [ ] 所有现有 Phase 5.1/5.2/5.3/5.4 测试 0 改动通过
- [ ] Admin 操作最终真的影响了 PersonalityResolver

---

## 9. 不立即实施的理由

1. **现有 41 + 27 = 68 个测试全部通过**，双写可能引入新问题
2. **Phase 3.5.13 ApprovalManager 流程**经过 3.x 多版本打磨，避免触动
3. **类型A Proposal 已能完成 Admin 可见的"提议 → 审查"流程**，业务价值已部分实现
4. **完整双写** 需 PersonalityAdapter 端也能识别 `source_proposal_a=True` 的 Proposal，影响面较大
5. **未来 Phase 5.5+** 可以在不破坏现有架构的前提下渐进式集成

---

## 10. 总结

| 项 | 状态 |
| --- | --- |
| 双 Schema 问题识别 | ✅ |
| 适配器设计 | ✅ |
| 迁移策略 | ✅ 选定双写镜像方案 |
| 数据兼容性 | ✅ 不破坏现有数据 |
| 回滚保证 | ✅ 仅回滚 GovernanceProvider 内部逻辑 |
| 实施时间 | ⏸ 推迟到 Phase 5.5+ |
| 当前架构约束 | ✅ 完全保持 |

> **本阶段唯一目标**是**记录问题 + 规划方案**。实际迁移需要后续 Phase 单独设计。
