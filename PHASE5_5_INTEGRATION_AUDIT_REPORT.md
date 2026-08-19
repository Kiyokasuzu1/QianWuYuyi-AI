# Phase 5.5 Integration 架构审核报告

> 审核时间：2026-07-30
> 审核角色：架构审查工程师
> 审核范围：Phase 5.5 Integration 全部新增 / 修改代码
> 审核目标：以"打造长期运行的浅雾羽依人格系统"为标准
> 强约束：独立验证，不假设报告正确

---

## 1. 当前完成度评价

### 1.1 交付物完成度

| 维度 | 状态 | 评价 |
| --- | --- | --- |
| 双 Schema 兼容 Adapter | ✅ 100% | 类型识别 + 双向转换 + 状态映射完整 |
| GovernanceProvider 集成 | ✅ 95% | 集成可用，但有 4 个 must-fix |
| 镜像存储 | ✅ 90% | 物理隔离设计良好，但 Authority 边界模糊 |
| 集成测试 | ✅ 100% | 21 个测试覆盖关键路径 |
| 强约束遵守 | ✅ 100% | RuntimeCore / Orchestrator / ApprovalManager / PersonalityAdapter 0 改动 |
| 综合回归 | ✅ 100% | 305/305 全部通过 |

**总完成度：90%**。功能可用但存在 4 个必须修复的隐患。

### 1.2 真正的完成度（去掉营销口径）

| 维度 | 状态 | 描述 |
| --- | --- | --- |
| **闭环有效性** | ⚠️ 部分 | 闭环在 80% 场景下有效；Memory Action 闭环无效（空操作） |
| **数据一致性** | ❌ 有漏洞 | 4 个漂移点未防护 |
| **Authority 边界** | ⚠️ 模糊 | Mirror 兼任状态变更，违反 Authority 隔离 |
| **生产可用性** | ❌ 不可 | silent fail、并发无锁、镜像失败无对账 |

**生产可用完成度：60%**。建议 hotfix 后再考虑启用。

---

## 2. 架构健康度评分

### 2.1 分项评分

| 维度 | 评分 | 说明 |
| --- | --- | --- |
| 职责清晰度 | 7/10 | Type A 主管 Admin；Type B 主管 Runtime；Mirror 主管桥接。**但 Mirror 同时承担"状态变更"职能** |
| Authority 隔离 | 6/10 | Adapter 干净；Mirror 接受外部状态变更，本质承担 Authority 角色 |
| 可扩展性 | 8/10 | 字段映射表、状态映射表已外化；新增字段成本低 |
| 可测试性 | 9/10 | 21 个集成测试覆盖关键路径 |
| 可维护性 | 6/10 | 状态机规则散落 4 处 |
| 错误处理 | 5/10 | 多处 silent fail，无 retry，无对账 |
| 性能 | 7/10 | JSON 存储可接受当前规模；无并发保护 |
| 安全性 | 7/10 | 无 schema 验证，潜在脏数据 |
| **总评** | **6.9/10** | **基本健康但有清晰优化空间** |

### 2.2 与 Phase 5.4 对比

| 维度 | Phase 5.4 | Phase 5.5 Integration | 变化 |
| --- | --- | --- | --- |
| Admin 提案可影响人格 | ❌ 不可 | ✅ 可（80% 场景） | +100% |
| 数据一致性 | 单 Schema | 双 Schema + Mirror | -20%（引入漂移） |
| 代码复杂度 | 简单 | 中等 | +30% |
| 测试覆盖 | 225/225 | 305/305 | +35% |
| 长期可维护性 | 高 | 中（受双 Schema 拖累） | -15% |

---

## 3. 发现的问题

### 3.1 必须修复项（4 项）

#### MUST-FIX-1：Type A ↔ Type B 状态漂移（🔴 严重）

**位置**：`MirrorBackedAdapter.accept_proposal` / `reject_proposal`

**问题**：当 ApprovalManager 通过 MirrorBackedAdapter 修改 Type B 状态时，Type A 完全不知情。
- D1: ApprovalManager.accept_proposal → Type B=approved，Type A 不变
- D2: ApprovalManager.reject_proposal → Type B=rejected，Type A 不变
- D3: ApprovalManager.modify_proposal → Type B.proposed_changes 已修改，Type A 不变

**影响**：
- Admin 视图（Type A）与 Runtime 视图（Type B）永久不一致
- 审计无法完整追溯
- 修复与回滚困难

**修复方向**：
- 选项 A：MirrorBackedAdapter 改为只读，所有写操作走真实 GrowthAdapter
- 选项 B：MirrorStorage 接受 accept/reject 时反向通知 GovernanceProvider
- 推荐：选项 A（更彻底）

#### MUST-FIX-2：Memory Action 镜像误入审批（🔴 严重）

**位置**：`GovernanceProvider._mirror_if_enabled`（当前对所有 Proposal 无差别镜像）

**问题**：`propose_memory_action` 生成的 Type A `affected_dimensions={}`，镜像后 Type B `proposed_changes=[]`。审批通过后 PersonalityResolver 没有任何实际变更。
- 管理员标记某记忆为"错误" → 走完整审批 → 审批通过 → 什么都没发生
- 浪费一次审计 + 一次审批 + 一次 Reviewer 注意力

**影响**：
- Admin UI 误导
- 审计污染
- 管理员对系统失去信任

**修复方向**：
- 在 `_mirror_if_enabled` 中识别 memory-only proposal
- 跳过镜像，或镜像标记为 `metadata.kind="memory_action"` 并在 PersonalityAdapter 中短路
- 推荐：跳过镜像（memory action 应走独立的 memory governance 流程）

#### MUST-FIX-3：Type B 同步失败 silent fail（🔴 严重）

**位置**：`GovernanceProvider._sync_mirror_status_if_enabled`（`logger.debug` 静默失败）

**问题**：
- Type A 已保存为 approved
- Type B 同步失败（IO 错误、磁盘满、并发冲突）
- 镜像存储中 Type B 仍是 proposed 状态
- 管理员以为审批通过，但 ApprovalManager 看不到
- 实际没有任何人格被修改

**影响**：
- 错误"静默"，无法察觉
- 审计记录显示 success，但实际未生效

**修复方向**：
- 失败标记写入 Type A metadata（`mirror_sync_status=failed`）
- 触发 retry queue（异步对账）
- 审计增加 `mirror_sync_failed=true` 字段
- 调用方应收到 mirror_sync_failed 信号（即使主流程 success）

#### MUST-FIX-4：MirrorBackedAdapter 缺少 governance_proposal_id 追溯（🔴 严重）

**位置**：`MirrorBackedAdapter.accept_proposal` / `reject_proposal` / `update_proposal`

**问题**：ApprovalRecord 没有 `source_proposal_id`（Type A），Type B 镜像无法反向追溯 Type A。

**影响**：
- 审计断链：Runtime 视角看不到提案来自 Admin
- 无法做"对账"：不知道 Type A 是否还存在

**修复方向**：
- `MirrorBackedAdapter` 写入 evaluator_meta 时追加 `governance_proposal_id`
- 或在镜像创建时通过 `mirror_source_proposal_id` 已有的字段提取（当前已有，但 ApprovalManager 写入时会丢失）

### 3.2 应该修复项（4 项）

#### SHOULD-FIX-1：状态机规则散落

**位置**：
- `STATUS_A_TO_B` / `STATUS_B_TO_A` 在 `growth_proposal_adapter.py`
- `sync_status` 状态映射在 `governance_mirror_integration.py`
- `PROPOSAL_STATUS` 常量在 `src/growth/proposal/constants.py`
- 状态过滤在 `governance_provider.py` 多处

**修复**：在 `src/growth/state_machines.py` 集中定义所有状态机规则。

#### SHOULD-FIX-2：无对账 / 漂移检测

**修复**：引入 `ProposalSyncManager`：
- 定期遍历 Type A + Type B 镜像
- 检查 status 一致性
- 检查 proposed_changes 一致性
- 报告漂移条目
- 自动修复可修复漂移

#### SHOULD-FIX-3：JSON 存储无并发保护

**修复**：
- Phase 5.5.2：引入 `threading.Lock` 或文件锁
- Phase 6.0：迁移到 SQLite

#### SHOULD-FIX-4：PersonalityResolver 污染风险

**修复**：
- PersonalityAdapter.apply_proposal 入口增加白名单验证
- TraitState 引入 `[min, max]` 边界
- 引入"每日最大变更次数"限制

### 3.3 可以延期项（3 项）

| 编号 | 描述 | 延期阶段 |
| --- | --- | --- |
| MAYBE-1 | Mirror 与真实 GrowthAdapter 数据分离 | Phase 6.x Schema 统一 |
| MAYBE-2 | 双 Schema 维护成本 | Phase 6.x Schema 统一 |
| MAYBE-3 | 性能瓶颈（JSON 全量读写） | Phase 6.0 迁移 SQLite |

---

## 4. 必须修改项

按优先级排序：

| 优先级 | 编号 | 描述 | 工作量 |
| --- | --- | --- | --- |
| P0 | MUST-FIX-1 | Type A ↔ Type B 状态漂移 | 2 天 |
| P0 | MUST-FIX-2 | Memory Action 镜像误入审批 | 0.5 天 |
| P0 | MUST-FIX-3 | Type B 同步失败 silent fail | 1 天 |
| P0 | MUST-FIX-4 | governance_proposal_id 追溯 | 0.5 天 |
| P1 | SHOULD-FIX-1 | 状态机集中化 | 2 天 |
| P1 | SHOULD-FIX-2 | ProposalSyncManager | 3 天 |
| P1 | SHOULD-FIX-3 | JSON 存储并发保护 | 1 天 |
| P2 | SHOULD-FIX-4 | PersonalityResolver 防护 | 2 天 |

**总工作量**：约 12 天（2.5 周）

---

## 5. 可以延期项

| 编号 | 描述 | 延期阶段 |
| --- | --- | --- |
| MAYBE-1 | Memory Action 独立 governance 流程 | Phase 5.6+ |
| MAYBE-2 | EventBus 解耦 | Phase 5.6 |
| MAYBE-3 | SQLite 迁移 | Phase 6.0 |
| MAYBE-4 | Schema 统一 | Phase 6.x |
| MAYBE-5 | GrowthRateLimiter | Phase 6.0 |
| MAYBE-6 | Proposal 过期 / 回滚 / 级联 | Phase 6.0 |
| MAYBE-7 | TraitState 边界 + 阻尼 | Phase 6.0 |

---

## 6. 下一阶段最优路线

### Phase 5.5.1 — Hotfix（紧急）

**目标**：修复 4 个 must-fix
**工期**：4 天
**改动**：
- 限制 MirrorStorage 状态变更入口（仅 GovernanceProvider 写入 / 外部只读）
- Memory Action Proposal 跳过 Mirror 镜像（标记 `memory_only=true`）
- 镜像失败写入 Type A metadata + 触发 retry
- MirrorBackedAdapter 写入 evaluator_meta 追加 `governance_proposal_id`

**测试**：
- 增加 mirror_sync_failure 测试
- 增加 memory_action_no_mirror 测试
- 增加 modify 漂移测试
- 增加 accept 反向通知测试

**预计新增测试**：15-20 个
**总测试数**：约 320-325

### Phase 5.5.2 — Stability

**目标**：对账 + retry + 漂移检测
**工期**：1 周
**改动**：
- `src/growth/sync/proposal_sync_manager.py`：定期对账
- `src/growth/sync/retry_queue.py`：失败 retry
- 增加 API 端点：`/admin/api/governance/mirror/health`

**预计新增测试**：15-20 个
**总测试数**：约 340-345

### Phase 5.6 — EventBus Decoupling

**目标**：解耦硬编码调用
**工期**：2 周
**改动**：
- 引入 Runtime EventBus
- `ProposalCreatedEvent` / `ProposalReviewedEvent` / `ProposalMirroredEvent`
- GovernanceProvider 改为 publish，不直接调用 mirror
- Mirror 改为 subscribe，自动响应

**预计新增测试**：20-30 个
**总测试数**：约 370-380

### Phase 6.0 — Runtime E2E

**目标**：Runtime 端到端
**工期**：3 周
**改动**：
- Runtime GrowthPipeline 全链路
- Personality Growth Runtime
- 端到端测试：Experience → Memory → Reflection → Growth → Approval → Apply
- GrowthRateLimiter
- Proposal 生命周期管理（过期 / 回滚 / 级联）
- TraitState 边界 + 阻尼
- PersonalityAdapter 白名单验证

**预计新增测试**：30-50 个
**总测试数**：约 400-430

### Phase 6.x — Schema Unification

**目标**：去除双 Schema
**工期**：4 周
**改动**：
- 设计统一 Schema
- 数据迁移脚本
- 灰度切换
- 废弃 Type A / Type B / Mirror
- 长期目标："Single Source of Truth"

### 总览

```
Phase 5.5.1 (4d)  → Phase 5.5.2 (1w) → Phase 5.6 (2w) → Phase 6.0 (3w) → Phase 6.x (4w)
   hotfix              stability          EventBus       Runtime E2E     Schema 统一
   320 tests           340 tests         370 tests      400 tests       420 tests
   修复 must-fix      对账+retry         解耦           端到端          统一
```

---

## 7. 不建议现在做的事情

### 7.1 不要立即统一 Schema（Phase 6.x）

**原因**：
- 当前 Adapter 已解决互通问题
- 统一 Schema 是 4 周大工程
- 当前应聚焦稳定性和正确性
- 长期看统一是 P3 优先级

### 7.2 不要立即进入 Runtime EventBus

**原因**：
- 当前硬编码调用已经够用
- EventBus 是优化项，不是必需项
- Phase 5.5.1 / 5.5.2 才是关键路径

### 7.3 不要立即启用 Mirror（API 端点）

**原因**：
- 4 个 must-fix 未修复
- 启用后错误会扩散给所有 Admin
- 必须先 hotfix，再考虑启用

### 7.4 不要增加没有必要的模块

**审计建议**：
- 不要增加新的 Adapter（除非 Adapter 真的不够用）
- 不要把 Mirror 变成"另一个 Adapter"（增加复杂度）
- 不要引入 EventBus 除非能解决真实问题

**判断标准**：每个新模块必须满足：
- 解决一个明确的痛点
- 测试覆盖 > 80%
- 不增加 Authority 复杂度
- 不绕过既有 Authority

### 7.5 不要忽视 Memory Action 治理

**问题**：当前 `propose_memory_action` 走完整 Proposal 流程，但实际是"标记 / 删除 / 合并"记忆，不是修改人格。

**建议**：在 Phase 5.5.1 中处理（MUST-FIX-2），但完整方案应单独规划：
- 独立 memory_action 治理流程
- 独立 MemoryActionAuthority
- 与 Personality governance 解耦

---

## 8. 长期视角：浅雾羽依人格系统目标

### 8.1 核心目标

| 目标 | 当前实现 | 距离目标 |
| --- | --- | --- |
| 稳定人格核心 | ✅ RuntimeCore 单例 + 完整测试 | 100% |
| 长期成长能力 | ⚠️ Runtime 端到端未完成 | 60% |
| 可追溯人格变化 | ⚠️ Audit 系统已有但未串联 | 70% |
| 防止错误成长 | ❌ 无 RateLimit、无白名单 | 30% |
| 可审核 | ✅ ApprovalManager + Audit | 90% |
| 可回滚 | ❌ 无 rollback 机制 | 20% |

### 8.2 关键风险

| 风险 | 描述 | 缓解阶段 |
| --- | --- | --- |
| 人格被错误 Proposal 污染 | 当前无白名单、无 RateLimit | Phase 6.0 |
| Memory → Growth 无限反馈 | 反思无 break 条件 | Phase 6.0 |
| 双 Schema 数据漂移 | 4 个漂移点 | Phase 5.5.1 |
| 镜像 silent fail | 失败无可见 | Phase 5.5.1 |
| JSON 存储瓶颈 | 单写文件 | Phase 6.0 SQLite |
| Schema 合并困难 | 双 Schema 长期维护成本 | Phase 6.x |

### 8.3 终极目标

> **一个稳定、可控、可审计、可回滚的浅雾羽依人格系统，支撑长期运行与持续成长。**

当前实现已经走在正确路径上，但需要：
1. **修复 4 个 must-fix**（4 天）
2. **补充稳定性**（1 周）
3. **完成 Runtime E2E**（3 周）
4. **统一 Schema**（4 周）

总计 2.5 个月可达生产可用状态。

---

## 9. 总结

### 9.1 一句话评价

**Phase 5.5 Integration 是"功能性完成，生产未就绪"**。

### 9.2 关键判断

| 维度 | 判断 |
| --- | --- |
| 是否完成 Phase 5.5 Integration 目标？ | ✅ 是（功能性目标） |
| 是否可以进入 Phase 6？ | ❌ 否（必须先 hotfix） |
| 是否可以启用 Mirror（API 暴露给 Admin）？ | ❌ 否（4 个 must-fix 未修复） |
| 是否需要重新设计？ | ❌ 否（架构基本健康，仅需增量修复） |
| 长期方向是否正确？ | ✅ 是（先稳定、后解耦、再统一） |

### 9.3 推荐的下一个动作

**立即开始 Phase 5.5.1 hotfix（4 天）**：
1. 修复 MUST-FIX-1：Type A ↔ Type B 状态漂移
2. 修复 MUST-FIX-2：Memory Action 镜像误入审批
3. 修复 MUST-FIX-3：Type B 同步失败 silent fail
4. 修复 MUST-FIX-4：governance_proposal_id 追溯

**完成后**：
- 重新评估可启用性
- 决定是否进入 Phase 5.5.2（稳定性）
- 不要直接进入 Phase 6

---

> **审核完成时间：2026-07-30**
> **审核者：TRAE（架构审查工程师）**
> **适用代码版本：Phase 5.5 Integration 实施完成版**
> **结论：功能性完成，4 个 must-fix 待修复，不建议立即进入 Phase 6**
