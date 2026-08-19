# Phase 5.3 — Admin Governance & Editing 报告

> 实施时间：2026-07-30
> 状态：**已完成实施 + 测试通过（待最终验收）**
> 适用分支：`fix/runtime-unification`
> 前置阶段：Phase 5.1（RuntimeProvider）/ Phase 5.2（UI 集成）

---

## 1. 概述

Phase 5.3 的目标是在现有 Admin Dashboard 基础上**增加有限的治理能力**，让 Admin 从"观察窗口"升级为"羽依认知系统管理控制台"，但**不破坏 Runtime 单例权威、不绕开 Proposal 审批机制**。

**核心原则（全部遵守）：**

1. **不重写 Admin 系统** — 仅在 Phase 5.1/5.2 之上增量新增
2. **不修改 RuntimeCore** — RuntimeCore 与 RuntimeBridge 完全未触及
3. **不绕过 RuntimeBridge** — 所有只读访问通过 `RuntimeProvider → RuntimeBridge → RuntimeCore`
4. **不允许 Admin 直接创建或修改** `MemoryStore / PersonalityResolver / EmotionManager / GrowthState`
5. **所有修改必须经过** `RuntimeProvider → RuntimeBridge → RuntimeCore`（写入侧通过 `GrowthProposal → ProposalStorage`，再由 Growth 系统审核后 apply）
6. **优先使用已有 GrowthProposal / ProposalStore 设计** — 不新增 Proposal 类型，仅复用现有 `personality` / `identity` 类型承载治理语义
7. **保持向后兼容** — 现有 Phase 5.1/5.2 测试 0 改动、全部通过
8. **保留 Phase 3.5.13 ApprovalManager 的审批机制** — 治理层只标 status（approved/rejected），不直接 apply

**实现方式：**

- 新增独立模块 `src/admin/governance_provider.py`，封装只读 + 受控写入操作
- 新增数据契约 `src/contracts/governance_schema.py`
- 在 `src/admin/api/routes.py` 追加 8 个治理 API 端点
- 在 `static/admin/index.html` 中新增 "治理面板"区块（`runtime-governance-section`）
- 新增独立 JS `static/admin/js/governance_dashboard.js` 与 CSS `static/admin/css/governance_dashboard.css`

---

## 2. 修改文件列表

### 2.1 新增文件

| 文件路径 | 角色 | 行数 (估) |
| --- | --- | --- |
| `src/admin/governance_provider.py` | Phase 5.3 核心 Provider：Personality / Memory / Growth 三段只读 + 受控 Proposal 提交 | ~790 |
| `src/contracts/governance_schema.py` | 数据契约：`PersonalityChangeRequest` / `MemoryActionRequest` / `GrowthProposalReviewRequest` / `GovernanceSnapshot` + 常量 | ~120 |
| `static/admin/js/governance_dashboard.js` | 前端：治理面板交互（提交建议 / 审查 / 自动刷新 / 详情弹窗） | ~24 KB |
| `static/admin/css/governance_dashboard.css` | 治理面板样式（玻璃卡片、按钮、徽章、列表） | ~270 |
| `tests/test_admin_governance.py` | Phase 5.3 单元测试：约束 / 契约 / Personality / Memory / Growth / Review / 错误处理 / 向后兼容 / E2E | 41 tests |
| `PHASE5_3_ADMIN_GOVERNANCE_REPORT.md` | 本报告 | — |

### 2.2 修改文件

| 文件路径 | 改动 |
| --- | --- |
| `src/admin/api/routes.py` | 在文件末尾追加 **8 个治理 API 端点**（GET 5 个 + POST 3 个 + Proposal 详情 1 个），不修改既有 Phase 5.1/5.2 端点。 |
| `static/admin/index.html` | ① 在 `<head>` 引入 `governance_dashboard.css`；② 在 `runtime-dashboard-section` 之后插入 `runtime-governance-section`（含 Personality / Memory / Growth 三个子块 + 提交表单）；③ 在 `</body>` 之前引入 `governance_dashboard.js`。**未改动任何现有结构。** |

### 2.3 **未改动**的关键文件（强约束验证）

- `src/runtime/runtime_core.py` — **未修改**
- `src/runtime/runtime_bridge.py` — **未修改**
- `src/admin/runtime_provider.py` — **未修改**（Phase 5.1 已实现，治理层仅复用）
- `src/personality/personality_adapter.py` — **未修改**
- `src/growth/proposal/proposal.py` — **未修改**（治理层仅复用 `GrowthProposal` 数据结构）
- `src/growth/proposal/storage.py` — **未修改**（治理层仅复用 `ProposalStorage`）
- `src/growth/approval_manager.py` — **未修改**（保留 Phase 3.5.13 审批机制）
- 现有 JS / CSS — **未修改**

---

## 3. 新增 API 列表

### 3.1 GET 端点（只读）

| 端点 | 返回 | 来源 |
| --- | --- | --- |
| `GET /admin/api/admin/governance/personality` | 当前人格 + GrowthState + SelfModel + Traits + 最近人格 Proposal | `RuntimeProvider.get_personality_summary` + `ProposalStorage` |
| `GET /admin/api/admin/governance/memory` | 最近记忆 + 重要记忆 + 类型分布 + 来源事件 + 最近记忆 Proposal | `RuntimeProvider.get_memory_summary` + `ProposalStorage` |
| `GET /admin/api/admin/governance/growth` | GrowthState 指标 + pending/approved/rejected/applied Proposal 列表 + 各类型数量 | `RuntimeProvider.get_growth_summary` + `ProposalStorage` |
| `GET /admin/api/admin/governance/proposals?status=&type=&limit=&offset=` | 列出 Proposal（可过滤） | `ProposalStorage` |
| `GET /admin/api/admin/governance/proposal/<proposal_id>` | 获取 Proposal 详情 | `ProposalStorage.load` |

### 3.2 POST 端点（受控写入）

| 端点 | Body | 行为 | 落点 |
| --- | --- | --- | --- |
| `POST /admin/api/admin/governance/personality/propose` | `{ trait, delta, reason, confidence, priority, evidence, actor }` | 生成 `GrowthProposal(proposal_type="personality", status="pending")` | `ProposalStorage` |
| `POST /admin/api/admin/governance/memory/propose` | `{ memory_id, action, reason, target_memory_id?, priority, actor }` | 生成 `GrowthProposal(proposal_type="identity", status="pending", metadata.request_kind="memory_<action>")` | `ProposalStorage` |
| `POST /admin/api/admin/governance/growth/review` | `{ proposal_id, action: "approve"\|"reject"\|"modify", reason, modified_changes?, actor }` | 更新 Proposal status（**不直接 apply**） | `ProposalStorage` |

所有 POST 端点均：
- 返回 `proposal_id`
- 写入 `AuditLogger`（失败容错）
- 仅当所有受控条件满足时返回 `success: true`

---

## 4. 数据流图

```
┌─────────────────────────────────────────────────────────────────┐
│                       Admin UI (Browser)                         │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  static/admin/index.html                                  │  │
│  │   - 【新增】 runtime-governance-section  ⬅ Phase 5.3      │  │
│  │       ├── Personality Governance Panel                    │  │
│  │       ├── Memory Governance Panel                         │  │
│  │       └── Growth Proposal Panel (Pending/Approved/...)   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                            │                                     │
│                            │ fetch (GET/POST, 10s 轮询)            │
│                            ▼                                     │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  static/admin/js/governance_dashboard.js  ⬅ Phase 5.3 新增 │  │
│  │   - 仅消费 API（GET 拉取快照 / POST 提交建议）              │  │
│  │   - 不创建任何 Runtime 实例                                │  │
│  │   - Offline 时显示降级提示                                 │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                            │
                            │ 8 endpoints (新增)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                Flask 蓝图 (api/routes.py)                        │
│   /admin/api/admin/governance/*  ⬅ Phase 5.3 新增                │
│   ├── GET  personality  (只读 → RuntimeProvider)                 │
│   ├── GET  memory       (只读 → RuntimeProvider)                 │
│   ├── GET  growth       (只读 → RuntimeProvider + Storage)      │
│   ├── GET  proposals    (只读 → Storage)                         │
│   ├── GET  proposal/<id>(只读 → Storage)                         │
│   ├── POST personality/propose (受控 → GovernanceProvider → ...  │
│   ├── POST memory/propose      (受控 → GovernanceProvider → ...) │
│   └── POST growth/review       (受控 → GovernanceProvider → ...) │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│       src/admin/governance_provider.py  ⬅ Phase 5.3 核心        │
│   - 单例 GovernanceProvider（懒加载）                              │
│   - 接受注入式 RuntimeProvider + ProposalStorage                  │
│   - 不实例化任何 Authority                                       │
│                                                                  │
│   ┌──────────────────┐           ┌──────────────────────┐       │
│   │ RuntimeProvider  │  ←只读   │  ProposalStorage     │ ←写入  │
│   │ (Phase 5.1 单例) │           │  (Phase 3.x 单例)    │       │
│   └──────────────────┘           └──────────────────────┘       │
└─────────────────────────────────────────────────────────────────┘
        │                                       │
        │ 只读引用                              │ 持久化到 JSON
        ▼                                       ▼
┌──────────────────────────┐         ┌────────────────────────┐
│ src/runtime/runtime_     │         │  data/proposals.json   │
│         bridge.py         │         │  (现有 Growth 系统)     │
│   (Phase 4.x 桥接)        │         └────────────────────────┘
└──────────────────────────┘                     │
        │                                         ▼
        ▼                              ┌────────────────────────┐
┌──────────────────────────┐            │  PersonalityAdapter / │
│ src/runtime/runtime_     │            │  GrowthAccumulator    │
│         core.py           │            │  (Phase 3.5.x 审批)   │
│   (Runtime 单例权威)       │            │   ↓                    │
│   - MemoryStore          │            │  PersonalityResolver   │
│   - PersonalityResolver   │            │  (Phase 4.4 写入)      │
│   - EmotionManager        │            └────────────────────────┘
│   - GrowthState          │
└──────────────────────────┘
```

**关键路径：**

- **只读流**：`Admin UI → fetch GET → routes.py → GovernanceProvider → RuntimeProvider → RuntimeBridge → RuntimeCore → Authority（不修改）`
- **写入流**：`Admin UI → fetch POST → routes.py → GovernanceProvider → ProposalStorage.save() → JSON 持久化 → （Phase 3.5.13 ApprovalManager 审批）→ PersonalityAdapter / GrowthAccumulator → 实际 apply`

Admin **永不**直接调用 `MemoryStore.delete()` / `PersonalityResolver.state.xxx =` / `GrowthState.advance()`。

---

## 5. Proposal 生命周期

### 5.1 状态机

```
                          ┌────────────────────┐
                          │  Admin 提交 Proposal │
                          │  (POST .../propose) │
                          └──────────┬─────────┘
                                     │
                                     ▼
                          ┌────────────────────┐
                          │      PENDING        │
                          │  (等待 Admin 审查)  │
                          └──────────┬─────────┘
                                     │
                  ┌──────────────────┼──────────────────┐
                  │                  │                  │
                  ▼                  ▼                  ▼
        ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐
        │   REJECTED   │  │     APPROVED     │  │  (no direct  │
        │              │  │  (标记为已批准)   │  │   apply)     │
        └──────────────┘  └──────────┬───────┘  └──────────────┘
                                     │
                                     │  Phase 3.5.13 ApprovalManager
                                     │  或 PersonalityAdapter 检测
                                     ▼
                          ┌────────────────────┐
                          │      APPLIED       │
                          │  (实际已生效)       │
                          └────────────────────┘
```

### 5.2 Phase 5.3 治理流程（不破坏既有机制）

```
Admin UI 提交建议
    │
    ▼
GovernanceProvider.propose_personality_change / propose_memory_action
    │
    │ 读取 before_state（via RuntimeProvider → RuntimeBridge → RuntimeCore）
    │ 计算 after_state（限幅 [0, 1]）
    │ 构造 GrowthProposal(proposal_type=..., status="pending", metadata.admin_governance=True)
    │
    ▼
ProposalStorage.save()  →  data/proposals.json
    │
    ▼
【Admin 端】  GovernanceProvider.review_proposal(action="approve" | "reject" | "modify")
    │            │
    │            ▼
    │       更新 status = approved / rejected
    │       写入 last_review 元数据
    │       记录 reviewer_id / review_comment
    │            │
    │            ▼
    │       ProposalStorage.save()
    │
    │  关键：只标 status，不 apply
    │
    ▼
【系统端】  Phase 3.5.13 ApprovalManager 流程
    │
    ▼
PersonalityAdapter / GrowthAccumulator
    │
    ▼
PersonalityResolver.state 实际更新（Phase 4.4 共享）
```

**关键不变量：**

1. **Admin 永远不能跳过 Proposal 状态机** — `review_proposal` 不调用任何 Authority 写入接口
2. **modify 视为已批准**（status = approved）— 与 Phase 3.5.13 保持一致
3. **终态不可再审查** — approved/rejected/applied 状态返回错误
4. **审计完整** — 所有 propose / review 操作都写入 `AuditLogger`（失败容错）
5. **每条 Proposal 都有 `metadata.admin_governance = True` 标识** — 便于区分 Admin 治理 vs 系统自然成长

---

## 6. 测试结果

### 6.1 Phase 5.3 新增测试

| 测试类 | 测试数 | 通过 | 失败 |
| --- | --- | --- | --- |
| `TestGovernanceProviderConstraints` | 3 | 3 | 0 |
| `TestGovernanceSchema` | 6 | 6 | 0 |
| `TestPersonalityGovernance` | 5 | 5 | 0 |
| `TestMemoryGovernance` | 6 | 6 | 0 |
| `TestGrowthGovernance` | 5 | 5 | 0 |
| `TestProposalReview` | 7 | 7 | 0 |
| `TestErrorHandling` | 3 | 3 | 0 |
| `TestBackwardCompatibility` | 3 | 3 | 0 |
| `TestEndToEndGovernance` | 3 | 3 | 0 |
| **合计** | **41** | **41** | **0** |

**结果：`41 passed, 0 failed`**

### 6.2 Phase 5.1 回归测试

| 测试文件 | 通过 | 失败 |
| --- | --- | --- |
| `tests/test_admin_runtime_integration.py` | 32 | 0 |

### 6.3 Phase 5.2 回归测试

| 测试文件 | 通过 | 失败 |
| --- | --- | --- |
| `tests/test_admin_ui_integration.py` | 50 | 0 |

### 6.4 综合执行命令

```bash
python -m pytest tests/test_admin_governance.py \
                 tests/test_admin_runtime_integration.py \
                 tests/test_admin_ui_integration.py -v
```

**结果：`123 passed, 0 failed, 65 warnings`**

### 6.5 已知非阻塞问题

`tests/test_admin_phase1.py::TestAdminModulesAPI::test_start_all_modules_and_stop` 报告 subTest 失败：

```
SUBFAILED(module='initiative')
AssertionError: False is not true
```

**根因分析：**
- `initiative` 模块在生产环境通过 `systemd` 服务（`yuyi-sender`）运行（参见 `project_memory.md` 强约束）
- 测试环境（Windows + pytest）无法启动 systemd 服务的替代进程
- 该测试在 Phase 5.3 **之前** 就已存在此问题，与本次改动无关
- **不影响** Phase 5.3 治理层的任何功能

**缓解：**
- Phase 5.1/5.2/5.3 的所有相关测试均通过
- 治理层未涉及 module start/stop 链路
- 建议在部署文档中注明 `initiative` 模块需通过 systemd 启动，不应通过 Admin 启停接口

### 6.6 关键不变量测试覆盖

| 不变量 | 测试用例 |
| --- | --- |
| Provider 不创建新 Authority 实例 | `TestGovernanceProviderConstraints::test_provider_does_not_create_memory_store` |
| 所有只读经由 RuntimeProvider | `TestGovernanceProviderConstraints::test_provider_reads_via_runtime_provider` |
| 注入式依赖（不硬编码） | `TestGovernanceProviderConstraints::test_provider_uses_injected_dependencies` |
| Personality change 不直接修改 resolver | `TestPersonalityGovernance::test_propose_personality_change_uses_authority_only_for_reading` |
| Memory action 不修改 store | `TestMemoryGovernance::test_memory_action_does_not_modify_store` |
| RuntimeProvider 状态不被破坏 | `TestBackwardCompatibility::test_governance_provider_does_not_modify_runtime_provider` |
| Phase 5.1 API 仍可工作 | `TestBackwardCompatibility::test_phase5_1_apis_still_work` |
| 终态不可再审查 | `TestProposalReview::test_review_already_reviewed_fails` |
| modify 必须提供 changes | `TestProposalReview::test_review_modify_without_changes_fails` |
| 异常安全降级 | `TestErrorHandling::test_provider_handles_runtime_exception` |

---

## 7. 强约束验证（全部通过）

| 约束 | 实现方式 | 验证手段 |
| --- | --- | --- |
| 不重写 Admin 系统 | 仅在 Phase 5.1/5.2 之上增量新增 | Phase 5.1/5.2 测试 0 改动通过 |
| 不修改 RuntimeCore | `src/runtime/runtime_core.py` 未触碰 | `git diff` 验证 |
| 不绕过 RuntimeBridge | 所有只读经 `RuntimeProvider` 注入 | Provider 单元测试 + Mock 计数 |
| Admin 不直接修改 Authority | Provider `__init__` 不实例化任何 Authority | `test_provider_does_not_create_memory_store` |
| 所有修改经 Proposal | 写入侧只走 `ProposalStorage.save` | E2E 测试 + 审计日志 |
| 复用 GrowthProposal | 不新增 Proposal 类型，仅复用 `personality` / `identity` | `governance_provider.py` 构造逻辑 |
| 向后兼容 | 治理层是独立模块，可选启用 | Phase 5.1/5.2 测试无破坏 |
| 保留 ApprovalManager | 治理层 `review_proposal` 只标 status，不 apply | `review_proposal` 源码 + 测试 |

---

## 8. 部署建议

### 8.1 是否可以进入部署阶段？

**结论：✅ 可以进入部署阶段（灰度）**

- 所有新增测试 41/41 通过
- 所有回归测试 82/82 通过（Phase 5.1 + 5.2）
- 综合 123/123 通过
- 治理层完全可选（独立模块），默认不破坏任何现有行为

### 8.2 灰度建议

1. **先灰度 UI**：先让 Admin 能看到治理面板（GET 端点），暂不开放 POST 提交
2. **再灰度 propose**：先开放人格变更建议（受控、可逆）
3. **最后灰度 review**：先开放 approve / reject，暂不开放 modify
4. **关注审计**：所有治理操作会写入 `AuditLogger`，运维侧可监控 `governance.*` 事件

### 8.3 已知后续可改进项（非阻塞）

1. `governance_provider.py` 中使用 `datetime.utcnow()` — 收到 Python 3.14 DeprecationWarning，建议改用 `datetime.now(timezone.utc)`
2. Memory action 当前复用 `identity` proposal_type，可在未来扩展为独立 `memory` 类型（保持向后兼容可暂不修改）
3. UI 端可增加 Proposal 列表分页与搜索（当前 limit 固定 50）

### 8.4 配置开关（可选）

如需在生产环境默认关闭治理层，可在 `config.yaml` 中添加：

```yaml
admin:
  governance:
    enabled: true            # 默认开启
    allow_personality_propose: true
    allow_memory_propose: true
    allow_growth_review: true
    audit_enabled: true
```

当前实现**不依赖**此配置即可工作，配置开关为后续增强预留。

---

## 9. 总结

Phase 5.3 在**完全遵守强约束**的前提下，把 Admin 升级为"认知系统管理控制台"：

- ✅ **观察能力**：5 个 GET 端点暴露 Personality / Memory / Growth / Proposal 详情
- ✅ **受控能力**：3 个 POST 端点支持人格变更、记忆处理、Proposal 审查
- ✅ **零破坏**：Phase 5.1/5.2 现有功能 0 改动
- ✅ **强约束**：Runtime 单例权威不变、审批机制不变、Authority 写入路径不变
- ✅ **完整测试**：41 个新测试 + 82 个回归测试 全部通过
- ✅ **完整审计**：所有治理操作写入 AuditLogger

**后续可进入 Phase 5.4（如有）或部署灰度阶段。**

---

> 报告生成时间：2026-07-30
> 生成者：TRAE（自动驾驶）
> 适用代码版本：Phase 5.3 实施完成版
