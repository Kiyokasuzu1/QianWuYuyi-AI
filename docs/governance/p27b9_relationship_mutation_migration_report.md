# P2.3-B.9 Relationship Mutation Boundary Migration — 实施报告

> 任务书：P2.3-B.9 Relationship Mutation Boundary Migration
> 前置审计：`docs/governance/relationship_mutation_boundary_audit.md`（B.8）
> 目标：让 Relationship 从「多源直写状态」变成「单入口治理状态变化」。
> 基线：branch `develop/v1.1`，HEAD `225d0408e89b475605d62b0435f85a9954ffb024`，
> `data/` 干净，B.3–B.8 治理工件齐全（`docs/governance/`）。
> 冻结约束（全部遵守）：保持旧 API、默认 flag=false、不删除 legacy 路径、
> 不改变 relationship 数据格式、不修改 `data/`、每步 A/B 回归。

---

## 1. 修改文件

| 文件 | 变更类型 | 内容 |
| --- | --- | --- |
| `src/relationship/mutation_adapter.py` | **新增（629 行）** | `RelationshipMutationAdapter`：RelationshipEvent → MutationRequest → MutationGateway → 裁决执行约定；迁移开关 `relationship_mutation_gateway_enabled`（默认 False）与 `relationship_self_model_gateway_enabled`（默认 False）；Phase 4 跨域 `route_self_model_influence()`；Phase 5 `LEGACY_MUTATION_SOURCES` 登记 + `applied_mutation_ids` 去重 |
| `src/relationship/relationship_model.py` | 修改（39+/8-，全部为 B.9） | `RelationshipIntelligenceEngine.process_interaction` 新增可选参数 `allowed_dimensions: Optional[Set[str]] = None`（**None = 旧行为字节一致**，测试证明）；非 None 时仅应用 Gateway 裁决 ACCEPT 的维度；治理模式下交互日志 delta 如实记录「已应用」增量（被拦截维度记 0） |
| `src/runtime/runtime_core.py` | 修改（手工回归验证过） | ① `record_relationship_interaction`（:3236）flag=False 走旧路径字节不变，flag=True 转 `_record_relationship_interaction_governed`（:3304：逐维度 extract→evaluate→`_plan_deltas`→`from_relationship_event`→`route`→仅 ACCEPT 维度进原 apply 方法，持久化/notify 与旧路径相同）；② `_relationship_update` 写路径加身份门 `_relationship_update_allowed`（:5331，行为与 B.7 emotion 一致：sandbox/unknown 拒绝，空 uid 保持 legacy 放行，门异常 fail-open） |
| `src/personality/self_model_manager.py` | 修改（33+，全部为 B.9） | `_apply_relationship_snapshot`（:665）R-14 治理开关：`relationship_self_model_gateway_enabled=True` 时不再直写 `SelfIdentity.preferences`，改经 `route_self_model_influence()` 走完整 Gateway（跨域强制 NEED_REVIEW）；flag=False 旧直写代码原样保留（未删除） |
| `src/governance/checks/identity_check.py` | 修改 | `PRIVILEGED_ACTORS` 追加 `relationship_system`（B.9 标准内部 actor，语义与 B.5 growth_system / B.7 emotion_system 同级：放行但 `requires_audit=True`） |
| `tests/test_relationship_mutation_gateway.py` | **新增（568 行，21 用例）** | 任务书 Phase 6 八项覆盖 + 引擎兼容回归 + 运行时治理/legacy 路径对比（详见 §5） |

未修改：`data/`（零修改，A/B 两轮验证）、growth pipeline / growth_engine（仅登记）、
RelationshipEvolution（保留未动）、所有数据结构与 JSON 格式。

---

## 2. Legacy mutation registry

延续 B.8 审计的 R-01~R-15 注册表，B.9 新增对 growth 链 legacy fallback
（B.8 分类 B-3）的**来源元数据登记**（`mutation_adapter.py` 模块常量）：

| RID | 文件:行号 | 函数 | 目标 | governance | 备注 |
| --- | --- | --- | --- | --- | --- |
| R-04 | `src/growth/growth_engine.py:336-389` | `GrowthEngine.apply_relationship` | v0.6 RelationshipState | `legacy_fallback_ungoverned` | proposal 路径（pipeline.py:615）上游已治理；legacy fallback（633/786）无 Proposal |
| R-05 | `src/growth/pipeline.py:615,633,786` | GrowthPipeline 调用点 | v0.6 RelationshipState | `legacy_fallback_ungoverned` | B.5 gateway 只裁决 GrowthState 提案，不覆盖关系增量 |

登记不删除、不改动原代码（任务书冻结）。每个经 `build_request`/`from_relationship_event`
构造的 MutationRequest 都写入 `context_snapshot["mutation_source"]` 元数据
（如 `runtime_stage03`、`relationship_system`、`relationship_system_cross_domain`），
供未来审计区分来源。

重复应用防护（Phase 5「同一 mutation_id 不能重复应用」）：`route()` 前置检查
`applied_mutation_ids` 集合——同一 mutation_id 二次 route 直接 DEFER
（`duplicate_mutation_id`），不再调用 Gateway / apply_route / 不再变更状态。
这为未来 R-04/R-05 接线时 Runtime 链与 growth 链的「双写收敛」提供了去重机制。

---

## 3. Gateway 流程

B.9 建立的目标链（B.8 §4 蓝图 → 实际落地）：

```
RelationshipEvent（extractor/evaluator 评估通过）
    ↓
RelationshipMutationAdapter.from_relationship_event(dimension, delta, confidence, …)
    ↓ 逐维度（trust / familiarity / collaboration / interaction_frequency）
MutationRequest（9 字段契约，target_domain="relationship"，
                 target_path="relationship.state.<dim>"，actor_identity="relationship_system"）
    ↓
MutationGateway 五道检查（identity → boundary → evidence → conflict → audit，首败即停）
    ↓
ACCEPT       → 仅该维度进入原 apply 方法（engine.process_interaction 的
               allowed_dimensions 白名单，由 RuntimeCore 注入编排；
               adapter 无 apply_route 时禁止自动 apply）
REJECT       → 该维度不改变状态（丢弃，仅审计留痕）
NEED_REVIEW  → 进入 pending_proposals（内存槽位，治理链接键齐全：mutation_id/
               request_id/trace_id/evidence；不落盘、不写 data/）
DEFER        → 进入 deferred 延迟队列（不改变状态）
```

关键裁决语义（来自 B.3 冻结检查器，测试验证）：
- **trust / bond 直改**：BoundaryCheck 红线 → 恒 NEED_REVIEW（`relationship_trust_bond_requires_review`）
- **|delta| > 0.01**：EvidenceCheck 单次事件上限 → NEED_REVIEW
- **证据 <3 条去重 ref / 低置信 / LLM 推断单独成立** → NEED_REVIEW
- **审计链路缺 request_id/trace_id** → DEFER；**身份越权** → REJECT
- 真实提取事件（trust_building 等）的计划增量全部 ≥0.02，因此运行时治理路径
  目前**全部落 NEED_REVIEW**（保守，符合「必须治理」清单）；ACCEPT 由合成请求
  （familiarity、delta 0.005、3 条 memory_link、confidence 0.9）在测试中验证。

身份门（Phase 3）：Stage_03 关系更新前经 `can_modify_relationship(resolve_identity(uid))`，
uid 回退链 `ctx.user_id → config.memory.target_user_id`，均缺失时放行并记日志
（legacy fallback），门自身异常 fail-open——与 B.7 emotion 门行为一致。

跨域（Phase 4）：`relationship → SelfIdentity.preferences` 写入意图转为
`target_domain="self_model"` 的 MutationRequest 走完整五道检查，且即使 Gateway
判定 ACCEPT 也**强制改写 NEED_REVIEW**（`cross_domain_relationship_to_self_model_
requires_review`），绝不自动直写 self_model。

---

## 4. 状态权威变化

**权威无变化（迁移不触碰数据结构，符合任务书冻结）：**

| 权威 | 文件 | B.9 前 | B.9 后 |
| --- | --- | --- | --- |
| v3.5.27 RelationshipState | `data/users/<uid>/relationship_state.json` | 引擎直写（R-01~R-03） | flag=false 不变；flag=true 仅 Gateway ACCEPT 维度进入同一 apply 方法 + 同一 `repository.save_state/save_relationship_model` 持久化 |
| v0.6 RelationshipState | `data/relationship_state.json` | growth 链（R-04/R-05/R-15） | 未触碰（仅登记） |
| legacy profile dict | `data/relationships/<uid>.json` | dormant（R-06~R-08） | 未触碰 |

变化的是**入口治理**而非权威本身：
- 新增治理链：RelationshipEvent → MutationRequest → MutationGateway → 裁决执行约定
  （单入口，不再多源直写）。
- SelfModelManager 的 `SelfIdentity.preferences`（R-14）在开关开启时不再被
  relationship snapshot 直写，改经 self_model 域治理（强制 NEED_REVIEW）。
- 双写问题（Runtime trust + Growth trust 同一事件双倍累积）：本阶段以
  mutation_id 去重 + 来源登记收敛，growth 侧暂不接线（见 §7 剩余债务）。

---

## 5. 测试结果

**新增测试**：`tests/test_relationship_mutation_gateway.py` — **21/21 通过**（0.5s）

任务书八项覆盖：
1. adapter 不直接写状态（无 save/repository 句柄，无 apply_route 时状态零变化）
2. Gateway ACCEPT 应用（apply_route 注入后 applied=True，状态按 proposed after 变更）
3. REJECT 不变化（sandbox actor，审计留痕，无 pending/deferred）
4. NEED_REVIEW pending（trust 路径，治理链接键齐全）
5. trust delta 超阈值（|delta|>0.01 → evidence 关；bond/trust → boundary 关）
6. identity deny（适配层 sandbox REJECT + 运行时身份门：unknown 拒 / QQ 放行 / 空 uid legacy 放行）
7. self_model cross-domain block（ACCEPT 被强制改写 NEED_REVIEW（stub gateway 验证）；
   SelfModelManager flag on 零直写偏好 / flag off 保留 3 条 legacy 偏好）
8. duplicate mutation_id 去重（二次 route DEFER，apply 仅执行一次）

另加回归锚点：引擎 `allowed_dimensions=None` 与旧行为数值一致；白名单只应用
ACCEPT 维度且日志 delta 如实为 0；运行时 flag=True 治理路径（真实事件全落
NEED_REVIEW、状态零变化、tmp_path 持久化）与 flag=False legacy 路径
（trust 0.06 / familiarity 0.02 / freq 0.13 原行为）对比。

**A/B 回归**（battery：67 个测试文件 ≈2500+ 用例；A=迁移前还原态，B=迁移后）：
- 环境：`HF_HUB_OFFLINE=1`（见下方网络说明）
- A 失败集：27；B 失败集：27；**新增失败 = 0**；消失失败 = 0
- 分块退出码逐块一致（aa=1, ab=0, ac=1, ad=1, ae=1, af=0, ag=1）
- `git status --short -- data/` 两轮均空（**data/ 零修改**）

A/B 两侧共有的 27 个失败全部为既有失败（与迁移无关，B.7 及更早阶段已知）：
- `test_growth_mutation_gateway.py`：`test_flag_false_keeps_legacy_behavior`、
  `test_flag_true_goes_through_governance`（B.5 遗留，`test_full_chat_lifecycle`
  模块级 `DEEPSEEK_API_KEY` 环境污染所致，单独运行可通过）
- `test_phase_4_0_1_lifecycle_executor.py::test_missing_stage_method_is_noop`
- `test_phase_4_2_*`（self_model foundation/integration/consumption/history_audit）14 项
  （版本号断言 4.2.x 与当前实现版本不符等）
- `test_phase_4_2_vision_adapter.py` 2 项、`test_phase_4_5_personality_runtime_binding.py` 1 项
- `test_phase_6_4_stability_hardening.py::test_01_empty_timeline`
- `test_runtime_full_lifecycle_verification.py::test_20_orchestrator_health_check`

**网络环境说明（诊断记录）**：本机断网期间，任何构造 `Orchestrator(config)` 的
测试会在 `VectorMemory → chromadb → sentence_transformers → huggingface_hub
get_hf_file_metadata → socket.create_connection` 处挂起（faulthandler 栈取证）。
已验证该挂起在 A（迁移前）与 B（迁移后）**同样存在**，属环境问题而非回归；
A/B 两轮均以 `HF_HUB_OFFLINE=1` 使 HF 元数据请求快速失败（orchestrator 已隔离
该失败，相关测试转为确定性地通过）。

---

## 6. 回滚方案

迁移开关默认关闭本身即回滚位：`relationship_mutation_gateway_enabled=False`
（默认）与 `relationship_self_model_gateway_enabled=False`（默认）下，
`relationship_model.py` 行为与旧版字节一致（测试验证），RuntimeCore 走原
`process_interaction` 直写路径，SelfModelManager 保留原直写代码。

完整还原到迁移前文件状态（A 状态，本阶段 Phase 7 已实际执行并验证）：

```bash
# 1) 新增文件
rm src/relationship/mutation_adapter.py tests/test_relationship_mutation_gateway.py
# 2) 纯 B.9 修改的 tracked 文件（diff 与 HEAD 全为 B.9）
git checkout -- src/relationship/relationship_model.py src/personality/self_model_manager.py
# 3) 含既有修改的文件：从备份还原（/tmp/p29b9/backup_b9/ 保存迁移前字节）
#    src/runtime/runtime_core.py（撤 flag 分支 / 两个 governed 方法 / _relationship_update_allowed / 身份门包裹）
#    src/governance/checks/identity_check.py（撤 PRIVILEGED_ACTORS 中 relationship_system，目录未跟踪无法 checkout）
#    还原后 cmp 逐字节校验（本阶段已执行 3 轮，均 byte-exact）
```

回滚验证方式：A/B 回归（§5）即真实回滚演练——A 轮为还原态运行全套 battery，
与 B 轮失败集逐条比对，新增失败 = 0。

---

## 7. 剩余债务

| 债务 | 内容 | 处置 |
| --- | --- | --- |
| B-3（R-04/R-05） | growth 链 legacy fallback 直写 v0.6 RelationshipState（pipeline.py:633/786 无 Proposal） | 本阶段仅登记 + mutation_id 去重机制；正式接线留待后续（任务书 Phase 5 范围） |
| B-4（R-15） | v0.6 RelationshipState 自身迁移未做 | 超出 B.9 范围（任务书禁止本阶段做完整迁移） |
| R-06 | RelationshipEvolution（旧演化引擎）dormant、无调用方 | 按任务书「不删除旧 RelationshipEvolution」保留，未接线 |
| R-07/R-08 | legacy profile dict 旁路（`data/relationships/<uid>.json`）dormant | 未接线（assembled_context 从不提供 rel_repo） |
| 开关未开启 | 两个迁移 flag 默认 False，生产路径尚未接管 | 灰度计划留待后续任务书；开启前需完成 NEED_REVIEW 队列消费方（pending_proposals 目前仅内存槽位） |
| SelfModel 完整迁移 | R-14 仅加了治理开关，self_model 域完整迁移未做 | 按任务书禁止范围保留 |

核心目标已达成：Relationship 域的状态变化从「多源直写」（引擎直写 / growth
双写 / self_model 直写）收敛为「单入口治理」（MutationRequest → MutationGateway
五道检查 → ACCEPT/REJECT/NEED_REVIEW/DEFER 裁决执行约定），默认关闭、可回滚、
零 data/ 修改、零新增回归。
