# P2.3-B.10 Mutation Governance Stabilization & Proposal Persistence — 实施报告

> 记录时间：2026-08-20
> 任务目标：建立统一 Mutation Governance Runtime 层（提案落账 / 审计统一 / 只读查询 / 灰度开关设计）
> 基线：docs/governance/p2_3_b10_baseline.md（A 状态 = HEAD `225d040` + B.5/B.7/B.9 工作区改动，不含 B.10）

## 1. 修改文件

| 文件 | 状态 | 内容 |
| --- | --- | --- |
| src/governance/proposal_store.py | 新增 | GovernanceProposalStore（append-only JSONL）+ build_proposal_record |
| src/governance/mutation_gateway.py | 修改 | +proposal_store 注入、+save_pending_proposal()、evaluate 中 NEED_REVIEW 自动落账 |
| src/governance/audit_writer.py | 修改 | +MutationAuditRecord 统一投影、+build_audit_record、InMemoryAuditWriter.records |
| src/governance/governance_inspector.py | 新增 | 只读查询器（list_pending / get_by_domain / get_by_mutation_id） |
| src/governance/governance_config.py | 新增 | GovernanceConfig 灰度开关设计（legacy/shadow/enforce，默认 legacy） |
| tests/test_mutation_governance_runtime.py | 新增 | 10 个测试（任务书 8 项 + 守卫/幂等附加） |
| docs/governance/p2_3_b10_baseline.md | 新增 | Phase 0 基线 |
| docs/governance/mutation_adapter_consistency_audit.md | 新增 | Phase 1 三域 adapter 一致性审计（只读） |
| docs/governance/p2_3_b10_mutation_governance_runtime_report.md | 新增 | 本报告 |

未修改：三域 MutationAdapter、RuntimeCore、config.yaml、data/ 任何文件、五道检查器。
三域 adapter 模块级迁移开关保持默认关闭。

## 2. ProposalStore 设计（Phase 2）

`src/governance/proposal_store.py` —— 治理域横切落账存储：

- **append-only JSONL**：一行一条记录，只追加、不重写；损坏行读取时跳过（不修文件）。
- **记录 schema（10 字段冻结）**：proposal_id / mutation_id / request_id / trace_id /
  domain / target_path / actor_identity / decision / created_at / evidence_refs；
  可选扩展键 audit_reference（来自 MutationDecision.audit_reference）。
- **MutationRequest / MutationDecision / AuditReference 支持**：
  `record_pending(request, decision)` 由 `build_proposal_record` 纯函数构造记录；
  `save(record)` 接受任意 dict（10 键校验）。
- **目录守卫**：默认只写 `<repo>/data/governance/proposals.jsonl`；
  data_dir 命中 `data/users` 连续路径段 → ValueError；只写固定文件名
  proposals.jsonl（relationship_state.json / emotion_state.json 永不触碰）。
- **幂等**：proposal_id 已存在 → 跳过追加返回原 id（保护双写路径重复落账）。
- **零域依赖**：只 import mutation_contract，不导入任何 memory/emotion/growth/
  personality/relationship 模块对象；文件惰性创建（构造与读取不落盘）。
- 与 src/growth/proposal_store.py（growth 域提案库）并存，互不替代。

## 3. Gateway 变化（Phase 3）

`src/governance/mutation_gateway.py`：

- 构造器新增可选 kwarg `proposal_store`（默认 None）。
- 新增 `save_pending_proposal(request, decision) -> Optional[str]`：
  duck-typed 调用 store.record_pending；未注入 / 无协作方法 / 异常 → 全部隔离
  （返回 None / 告警，不影响 verdict）。
- `evaluate()` 在审计引用回填**之后**、verdict == NEED_REVIEW 时自动调用
  save_pending_proposal——保证落账记录携带最终 audit_reference。
- **旧 API 不变**：evaluate 签名不变；未注入 store 时零行为变化（不创建文件）。
- **双写语义**：落账层（Gateway → ProposalStore）+ 内存层（各域 adapter
  route() 的 pending_proposals 槽位，未做任何修改，旧测试兼容）。

## 4. Audit 统一（Phase 4）

`src/governance/audit_writer.py`：

- 新增 **MutationAuditRecord**（frozen dataclass，6 字段冻结）：
  mutation_id / domain / actor / decision / timestamp / reason ——
  填补 MutationDecision 不含 domain/actor 的投影缺口，是未来
  MutationJournal 正式落账（债务 #2）的目标形状。
- 新增 `build_audit_record(request, decision)` 纯函数（鸭子类型，dict 与
  dataclass 均可）。
- InMemoryAuditWriter 追加 `records: List[MutationAuditRecord]`：
  record_decision 时按 mutation_id 匹配最近一次 record_request 自动生成投影。
- **旧 API 不变**：AuditWriter ABC、record_request/record_decision 签名、
  requests/decisions 列表、返回引用格式 `audit_{n}_{mutation_id}` 全部保留。

## 5. Config 设计（Phase 6，未开启）

`src/governance/governance_config.py`：

- `GOVERNANCE_MODES = ("legacy", "shadow", "enforce")`：
  - **legacy**：旧路径唯一执行，Gateway 不参与（默认，零行为变化）
  - **shadow**：执行旧路径 + 同一 mutation 送入 Gateway 记录 verdict（只记录不拦截）
  - **enforce**：Gateway verdict 控制实际 mutation（对应各域迁移开关开启）
- `GovernanceConfig`（frozen）：governance_mode="legacy" +
  growth/emotion/relationship/_self_model 四个域 flag 默认全部 False。
- 进程级单例 get/set/reset（测试用），非法 mode 抛 ValueError。
- **设计层，不接线**：不写 config.yaml、不改三域模块级开关的权威地位，
  生产行为与 B.10 前完全一致。

## 6. 测试结果（Phase 7/8）

新套件 `tests/test_mutation_governance_runtime.py`：**10/10 通过**（0.46s），覆盖：

1. NEED_REVIEW 自动落 GovernanceProposalStore（10 字段全断言）
2. 旧 pending cache 保留（双写）
3. MutationAuditRecord 创建（6 字段冻结契约）
4. 三域 adapter 契约一致（envelope 11 键一致、三域落账、槽位差异文档化）
5. shadow 模式状态一致（旧路径执行结果与纯 legacy 相同 + verdict 已记录）
6. legacy 模式零行为变化（默认配置 + 三域模块开关全关 + no-op 不建文件）
7. proposal 查询接口（Inspector 三方法 + 只读性）
8. data/ 零污染（仓库三个关键路径 stat 快照前后一致）
9. 附加：业务目录守卫（data/users → ValueError）
10. 附加：append-only 幂等（同 mutation_id 重复落账仍 1 行）

**A/B 回归**（A = B.10 前状态 67 文件，B = B.10 后状态 68 文件；
HF_HUB_OFFLINE=1 环境；10 文件/块，共 7 块，逐块流式追加）：

| 指标 | A | B | 结论 |
| --- | --- | --- | --- |
| 失败数 | 27 | 27 | 一致 |
| 新增失败（comm -13） | — | **空** | ✅ 新增失败 = 0 |
| 消失失败（comm -23） | — | **空** | ✅ |
| 块退出码 | 1,0,1,1,1,0,1 | 1,0,1,1,1,0,1 | 完全一致 |
| data/ 修改 | 空 | 空 | ✅ 零污染 |

27 个共享失败为 B.5/B.7/B.9 时代的既有失败（growth 2、lifecycle_executor 1、
self_model 7+5+4+3、vision 2、personality_runtime_binding 1、stability 1、
runtime_full_lifecycle 1），A/B 完全相同，与 B.10 无关。
B 块 b_06 汇总 "1 failed, 98 passed"——唯一失败即上述既有项。

## 7. 回滚方案

```bash
cd <repo>
# 恢复 B.10 前状态（备份于 /tmp/p29b10/backup_b10/ 为本会话产物，
# 正式回滚按下方手工命令执行）
# 1) 删除新增文件
rm src/governance/proposal_store.py src/governance/governance_inspector.py \
   src/governance/governance_config.py tests/test_mutation_governance_runtime.py
# 2) 回退 mutation_gateway.py 三处编辑（或从 git 恢复——该文件为 untracked，
#    需手工反向编辑）：
#    - 移除构造器 proposal_store kwarg 与 self.proposal_store 赋值
#    - 移除 evaluate() 中 NEED_REVIEW 落账块
#    - 移除 save_pending_proposal() 方法与其 docstring 段落
# 3) 回退 audit_writer.py：删除 MutationAuditRecord / build_audit_record /
#    InMemoryAuditWriter.records 与 _append_unified_record（恢复 B.3 原版 70 行）
# 4) docs 文件可留可删（无代码影响）
```

因 src/governance/ 整体 untracked（247 项工作区变更的历史包袱），git checkout
无法单独回退；按上述手工命令执行即可。所有改动均为可加可减的增量，无状态迁移。

## 8. 剩余债务

1. **NEED_REVIEW 消费链仍未建立**：ProposalStore 已持久化待审提案，但尚无
   消费者（审核 → 应用 / 拒绝）——本任务只完成"落账侧"。
2. **MutationJournal 正式落账未接线**：MutationAuditRecord 是投影目标形状；
   向 MutationJournal/AuditTrail 的投影 adapter 仍待实施（B.3 边界约束不变）。
3. **growth adapter 无 pending_proposals 内存槽位**（Phase 1 审计 #5-B 文档化）；
   三域 route 样板与 `_gen_linkage_ids`/`_to_float` 三份重复未做 adapter 级重构。
4. **灰度机制未接线**：GovernanceConfig 为设计层，shadow/enforce 未接入
   RuntimeCore / 三域 adapter；三域模块级开关仍是唯一权威。
5. **ConflictCheck 重复提案检测**：proposal_store 协作件默认未注入；
   GovernanceProposalStore 的 exists_similar 语义与 growth 版提案库的关系待定。
6. **datetime.utcnow() 弃用警告**（Python 3.14）：mutation_contract / 三域
   adapter / proposal_store 均有——既有代码风格一致，未在 B.10 范围内统一替换。
