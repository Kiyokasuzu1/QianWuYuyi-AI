# P2.3-B.10 Mutation Governance Stabilization & Proposal Persistence — 基线快照

> 记录时间：2026-08-20（B.10 Phase 0）
> 用途：B.10 全阶段 A/B 回归的 A 状态参照点。任何阶段回归失败时，与本快照比对。

## 1. Git 状态

| 项目 | 值 |
| --- | --- |
| 分支 | `develop/v1.1` |
| HEAD | `225d0408e89b475605d62b0435f85a9954ffb024` |
| data/ 修改 | **零**（`git status --short -- data/` 无输出） |
| 工作区变更总数 | 247（含 src/governance/ 全目录 untracked，B.5/B.7/B.9 未提交的历史包袱） |

## 2. 数据目录状态

`data/` 下无任何已跟踪文件的未提交修改。B.10 各阶段不得引入：
`data/users/*`、`data/relationship_state.json`、`data/emotion_state.json` 的写入。
新增的治理落账文件只允许出现在 governance 数据目录（Phase 2 定义）。

## 3. 治理源码结构（src/governance/）

```
src/governance/
├── __init__.py
├── audit_writer.py            (70 行, AuditWriter ABC + InMemoryAuditWriter)
├── mutation_contract.py       (289 行, MutationRequest/MutationDecision/DecisionVerdict/CheckResult)
├── mutation_gateway.py        (176 行, MutationGateway 五道检查链)
└── checks/
    ├── __init__.py
    ├── base.py
    ├── identity_check.py      (第 1 道；PRIVILEGED_ACTORS 已含 growth/emotion/relationship_system)
    ├── boundary_check.py      (第 2 道；六域命名空间 + 域红线)
    ├── evidence_check.py      (第 3 道；5 条证据规则)
    ├── conflict_check.py      (第 4 道；倒转/年度上限/重复提案，注入 proposal_store)
    └── audit_check.py         (第 5 道；request_id+trace_id 链路，注入 journal)
```

**B.10 前不存在**：`src/governance/proposal_store.py`、`src/governance/governance_inspector.py`、任何 GovernanceConfig 模块。
（`src/growth/proposal_store.py` 是 B.1.2 的 growth 域提案库，258 行，ConflictCheck 注入用的 `exists_similar` 来自它——与 B.10 Phase 2 要新建的 governance 域 ProposalStore 是两个不同的组件。）

## 4. 三个域 MutationAdapter 现状

| 文件 | 行数 | flag 默认值 | 备注 |
| --- | --- | --- | --- |
| src/growth/mutation_adapter.py | 23624 字节 | `_growth_mutation_gateway_enabled = False` | B.5 |
| src/emotion/mutation_adapter.py | 17818 字节 | `_emotion_mutation_gateway_enabled = False` | B.7 |
| src/relationship/mutation_adapter.py | 27276 字节 | `_relationship_mutation_gateway_enabled = False`；`_relationship_self_model_gateway_enabled = False` | B.9 |

三域 flag 全部默认关闭——生产仍走 legacy 路径。B.10 Phase 6 的灰度开关设计必须保持此默认不变。

## 5. config.yaml 治理段

`config.yaml` 中**不存在**任何 `governance` 键（grep 无命中）。B.10 Phase 6 的 GovernanceConfig 为纯代码层设计，不写 config.yaml、不开启任何生产 flag。

## 6. 历史任务报告存在性

| 任务 | 报告文件 | 存在 |
| --- | --- | --- |
| B.4 | docs/governance/p24b4_personality_mutation_migration_report.md | ✅ |
| B.5 | docs/governance/p25b5_growth_mutation_migration_report.md + p25b5_growth_legacy_mutation_registry.md | ✅ |
| B.7 | docs/governance/p26b7_emotion_mutation_migration_report.md | ✅ |
| B.9 | docs/governance/p27b9_relationship_mutation_migration_report.md | ✅ |

docs/governance/ 共 11 个 .md 文件。

## 7. B.10 起始债务（任务书原文对照）

1. NEED_REVIEW 目前多数只进入内存 `pending_proposals`，没有持久化消费链。
2. MutationJournal 只完成接口设计（audit_check 的 journal 协作件），没有正式落账。
3. Relationship / Emotion / Growth 各自维护 adapter，有重复逻辑。
4. Runtime flag 默认关闭，缺少灰度启用机制。
5. legacy mutation 来源已登记（B.5 注册表 + B.9 LEGACY_MUTATION_SOURCES），但未形成统一治理视图。

## 8. 基线验证命令（复现用）

```bash
git rev-parse HEAD                 # 225d0408e89b475605d62b0435f85a9954ffb024
git status --short -- data/        # 期望空
ls src/governance/                 # 期望无 proposal_store.py / governance_inspector.py
grep -n "governance" config.yaml   # 期望空
```
