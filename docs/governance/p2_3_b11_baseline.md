# P2.3-B.11 Governance Runtime Activation & Self Model Growth Foundation — 基线确认

> 记录时间：2026-08-20（B.11 Phase 0）
> A 状态定义：**B.10 完成状态**（HEAD `225d040` + B.5/B.7/B.9/B.10 工作区改动）。
> B.11 各阶段 A/B 回归以此为 A 侧。

## 1. Git 状态

| 项目 | 值 |
| --- | --- |
| 分支 | `develop/v1.1` |
| HEAD | `225d0408e89b475605d62b0435f85a9954ffb024`（与 B.10 基线相同——全部治理工作仍在未提交工作区） |
| data/ 修改 | **零**（`git status --short -- data/` 无输出） |
| data/governance/ | 目录不存在（落账 store 从未在生产默认路径写过文件） |
| 工作区变更总数 | 248 |

## 2. src/governance 当前结构（B.10 完成态）

```
src/governance/
├── __init__.py               (B.3 导出，未变)
├── audit_writer.py           (B.10 Phase 4: +MutationAuditRecord/build_audit_record/records)
├── governance_config.py      (B.10 Phase 6: GovernanceConfig legacy/shadow/enforce，默认 legacy)
├── governance_inspector.py   (B.10 Phase 5: 只读 list_pending/get_by_domain/get_by_mutation_id)
├── mutation_contract.py      (B.3 冻结契约，未变)
├── mutation_gateway.py       (B.10 Phase 3: +proposal_store 注入/save_pending_proposal)
└── checks/                   (五道检查器，未变)
```

**B.11 前不存在**：`proposal_manager.py`、`mutation_adapter_base.py`、`src/personality/self_observation.py`。

## 3. 三个 MutationAdapter 状态

| 文件 | flag | 值 |
| --- | --- | --- |
| src/growth/mutation_adapter.py | `_growth_mutation_gateway_enabled` | **False** |
| src/emotion/mutation_adapter.py | `_emotion_mutation_gateway_enabled` | **False** |
| src/relationship/mutation_adapter.py | `_relationship_mutation_gateway_enabled` | **False** |
| src/relationship/mutation_adapter.py | `_relationship_self_model_gateway_enabled` | **False** |

GovernanceConfig.governance_mode = **legacy**。生产路径全部走 legacy，零行为变化。

## 4. B.10 测试结果（本阶段复跑确认）

`tests/test_mutation_governance_runtime.py`：**10 passed**（0.41s）。

B.10 A/B 回归结论（沿用）：失败集 27=27 一致，新增失败=0，data/ 零修改。
27 个共享失败为 B.5/B.7/B.9 时代既有项。

## 5. NEED_REVIEW 消费链当前断点（B.10 报告 §8 债务 #1 原文）

> ProposalStore 已持久化待审提案，但尚无消费者（审核 → 应用 / 拒绝）——
> 本任务只完成"落账侧"。

即当前链路：

```
MutationGateway ──(NEED_REVIEW)──> GovernanceProposalStore(落账) ──> 【断点：无消费方】
                                      adapter.pending_proposals(内存,无人消费)
```

B.11 Phase 1/2 的任务就是补上 Store 之后的消费链（生命周期 + 审批 + 防重复 apply）。

## 6. 冻结约束复述（本任务全程遵守）

1. 不修改 data/users、data/relationship_state.json、data/emotion_state.json
2. 不删除 legacy mutation 路径
3. 所有生产 flag 默认保持 False
4. 保持旧 API 兼容
5. 每阶段 A/B 回归
6. 不直接开启生产治理模式
7. 不做 B.12 内容（自动人格修改/自主意识/主动聊天/世界模型/多模态）

## 7. 基线验证命令（复现用）

```bash
git rev-parse HEAD                    # 225d0408e89b475605d62b0435f85a9954ffb024
git status --short -- data/           # 期望空
python -m pytest tests/test_mutation_governance_runtime.py -q   # 10 passed
ls src/governance/                    # 7 个 .py + checks/，无 proposal_manager / mutation_adapter_base
```
