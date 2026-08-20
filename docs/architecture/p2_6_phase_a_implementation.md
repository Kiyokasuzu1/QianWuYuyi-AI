# P2.6 Phase A 实施报告：治理统一审计探针

> 范围：仅 Phase A（只读审计 + 占位开关）。未进入 Phase B，未改变任何生产行为。
> 日期：2026-08-21

## 1. 修改文件

| 文件 | 改动 |
|---|---|
| src/growth/growth_engine.py | 新增模块级 `_record_legacy_growth_audit()` 辅助函数；在 `apply()`（P2b）与 `apply_proposal()`（P2）实际写状态前各插入一行只读审计调用。不改变任何返回值、不改变任何写入逻辑 |
| src/personality/self_model_governance.py | `evaluate()` 拆为外层 `evaluate()`（决策后附一条只读审计记录）+ `_evaluate_rule()`（原 evaluate 逻辑逐行不变）。决策结果与之前完全一致 |

## 2. 新增文件

| 文件 | 内容 |
|---|---|
| src/governance/governance_unification.py | `governance_unification_enabled` 占位开关，默认 False；`is/set_governance_unification_enabled()`。Phase A 无任何逻辑读取它 |
| src/governance/audit_probe.py | `GovernanceAuditProbe`（JSONL append-only，线程安全，写失败静默吞掉）+ 进程级单例 `get/set_governance_audit_probe()` + 业务侧唯一入口 `record_governance_audit()`（含导入失败全量吞掉） |
| tests/test_governance_audit_probe.py | 16 个用例（见 §4） |
| docs/architecture/p2_6_phase_a_implementation.md | 本文档 |

## 3. 接入点

| 观察点 | 位置 | source_path | domain | decision | payload_summary |
|---|---|---|---|---|---|
| P2 | growth_engine.py `apply_proposal()`，`update_metrics` 之前（所有拒绝分支之后，仅真实写入时记录） | legacy_growth_apply | growth | direct_apply | proposal_id / dimensions / confidence |
| P2b | growth_engine.py `apply()`，`_apply_metrics` 之前（所有 skip 分支之后） | legacy_growth_event_apply | growth | direct_apply | event_id / meaning / mode / importance |
| P3 | self_model_governance.py `evaluate()` 决策后（覆盖 auto_apply / approval_required / deny 全部决策） | legacy_self_model_policy | self_model | decision.action.value | growth_level / confidence / threshold_context / reason |

记录字段（任务书 §2）：timestamp / source_path / domain / mutation_type / decision / payload_summary / triggered_by / request_id。

存储：`data/governance_audit/governance_audit_probe.jsonl`，append-only；目录惰性创建；`data/` 已在 .gitignore 中，不影响 git 状态与既有 data 文件。`request_id` 当前为空串（Phase B 接线调用方上下文时填充）。

## 4. 测试结果

- 新测试 `tests/test_governance_audit_probe.py`：**16 passed**。
  - probe 正常记录（JSONL 逐字段断言、append-only、单例）
  - 写失败吞掉（目录被文件阻塞 → 返回 False 不抛出）
  - 旧链行为完全一致（apply_proposal/apply 返回值、before/delta、save_count 逐项断言；被拒绝的 proposal 零记录）
  - 审计失败不影响业务（探针指向坏路径时 apply/apply_proposal 仍 applied）
  - P3 决策记录（parametrized：auto_apply/approval_required/deny + threshold_context 断言；evaluate 与纯规则 _evaluate_rule 等价）
  - 默认 config 无治理行为变化（flag 默认 False；flag 置 True 时探针与决策行为不变）
- 回归电池（growth/policy 相关）：73 passed。
  - tests/test_growth_phase_382b.py + test_phase_4_0_3_governance.py + test_phase_385_step5_dual_update.py：41 passed / **12 failed**
  - tests/test_growth_history.py + test_growth_state_authority.py + test_growth_mutation_gateway.py：32 passed
- **12 个失败为既有失败，与本任务无关**：A/B 对照（stash 本任务两处源码改动后跑同一电池）失败集合完全一致（改动前 12 failed/41 passed，改动后 12 failed/41 passed，12 个测试名逐一相同）；`test_same_event_no_dual_delta` 单跑通过，属测试间串扰而非本任务引入。

## 5. 未改变的行为（验收对照）

| 验收项 | 状态 |
|---|---|
| governance_unification_enabled 默认 false | ✅ 模块级默认 False，无任何逻辑读取 |
| RuntimePipeline 行为无变化 | ✅ 未触碰 runtime 任何文件 |
| RuntimeCore 行为无变化 | ✅ 未触碰（17 阶段契约、stage 04、approved drain 链均未改动） |
| Legacy auto_apply 仍保持原行为 | ✅ apply/apply_proposal/evaluate 返回值逐项断言一致；policy 阈值未动 |
| 新增 audit 文件 | ✅ 运行时惰性创建 data/governance_audit/governance_audit_probe.jsonl（append-only） |
| audit failure 不影响业务 | ✅ 双层吞掉（record 内部 + 接入点辅助函数），测试覆盖 |
| 测试通过 | ✅ 新测试 16 passed；相关电池零新增失败 |

## 6. 边界声明

- 未删除 legacy auto_apply；未开启任何治理 flag（B.5/B.7/B.9/B.13 未动）；未修改任何已有写入逻辑。
- 探针不受 `governance_unification_enabled` 门控——Phase A 只观察；Phase B 才会让该开关改变 legacy 路由（生成 Proposal + 网关），届时探针记录将成为 A/B 对照的关键证据（B 态 direct_apply 记录数应降为零）。
- P3 记录发生在 `evaluate()`（决策层）而非 `apply_proposal`（写入层），因此 policy 被非 orchestrator 调用方（如 growth_integration）使用时也会被记录——这是观察语义的一部分，文档于 p2_6_governance_unification_audit.md §2。
