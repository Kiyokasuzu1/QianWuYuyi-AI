# P2.6 Phase B-1 验收审查报告

> 性质：只读审查（未修改任何代码；仅新增本文档）
> 审查对象：Phase B-1 实施（pipeline.py / orchestrator.py / tests/test_governance_unification_phase_b.py）
> 审查依据：p2_6_phase_b_final_decision.md、p2_6_phase_b_implementation.md、AGENTS.md
> 审查日期：2026-08-21
> 审查方法：代码差异逐行核对 + 敏感区域内容比对 + 写入口全量 grep + 测试复跑

---

## 1. 审查结论

**Phase B-1 通过验收。** 结论要点：

1. **白名单合规**：生产修改仅 pipeline.py + orchestrator.py；未发现对 RuntimeCore / Stage 04 / drain / policy 的任何隐藏修改；未新建 proposal/store/model。
2. **A 态兼容**：flag=False 时四条写路径（apply(event) / apply_proposal / growth_records.add / Step 14.6 三分支）与修改前逐分支等价（代码级证明 + 测试级证明 + 真实链单测通过）。
3. **B 态闭环**：P2/P2b/P3 不变量全部成立；growth_records 仅 applied 追加（F1 生效）。
4. **绕过点**：统一治理模式 legacy 写入口全部覆盖；残余 3 项已知路径（2072 dormant / self_model approved 无消费器 / P4 resolver 主链）为 Phase C/D 已登记项。
5. **测试可靠性**：覆盖真实生产分支（真实 gateway 路由 + 真实 park + 真实治理执行层），非 mock 自娱；无单例/flag 污染；无顺序依赖；缺 isolated subprocess 测试（登记为 Phase C 前置条件）。

---

## 2. 白名单符合性

### 2.1 本任务文件清单核对

| 文件 | 白名单 | 实际 | 结论 |
|------|--------|------|------|
| src/growth/pipeline.py | ✅ | 修改（P-1~P-6，见实施文档 §2.1） | 合规 |
| src/orchestrator.py | ✅ | 修改（O-1~O-4，见实施文档 §2.2） | 合规 |
| tests/test_governance_unification_phase_b.py | ✅ | 新增 18 用例 | 合规 |
| docs/architecture/p2_6_phase_b_implementation.md | ✅ | 新增 | 合规 |
| docs/architecture/p2_6_phase_b_validation_review.md | ✅（审查文档） | 本文档 | 合规 |

### 2.2 隐藏修改排查（五个敏感对象逐一比对）

| 对象 | 核对点 | 结果 |
|------|--------|------|
| RuntimeCore（含 drain） | drain 消费 filter 仍为 `proposal_type != PERSONALITY → skip`（runtime_core.py:6452）；方法体 docstring「唯一生产 apply 入口」不变 | ✅ 未修改 |
| Stage 04 | `accept_experience` docstring 与入口语义不变（仍产出 pending Proposal） | ✅ 未修改 |
| approved drain 语义 | `list_by_status(APPROVED)` 消费点全仓唯一 = runtime_core.py:6438 | ✅ 未修改 |
| policy | `SelfModelGovernancePolicy.RULES` 阈值不变（context 0.50 / preference 0.65 / trait 0.80 AUTO_APPLY-APPROVAL 分界）；evaluate 仍为 Phase A 探针包装 | ✅ 未修改 |
| growth_engine.py | 仅 Phase A 探针 3 处（def 31 / 调用 188、334），无新增写入 | ✅ 未修改（Phase A 状态） |
| self_model_updater.py | **不在 modified 列表**——工作区与 HEAD 零差异 | ✅ 完全未触碰 |

### 2.3 工作区噪音说明（重要）

当前 `git status` 显示大量文件 modified（runtime_core 317 行、growth_integration 42 行、emotion/relationship/lifecycle 等）——这些是**既有未提交工作**（HEAD 中不存在 B.5 网关路由等代码，`git show HEAD:src/growth/pipeline.py | grep -c _route_proposal_through_gateway` = 0），非 Phase B-1 引入。git diff 相对 HEAD 无法区分新旧，本审查以「完整工具调用轨迹（本任务仅 Edit 白名单文件）+ §2.2 敏感区域内容比对」为准。评审 Phase B-1 增量时请以实施文档 §2 清单为界。

---

## 3. A 态兼容性证明（flag=False）

### 3.1 代码级逐分支等价

| 路径 | flag=False 时布尔化简 | 等价性 |
|------|----------------------|--------|
| `_is_growth_governed()` | `False or False = False` | proposal 路由条件为 False → 走 `apply_proposal` 旧直写分支（与旧版仅注释差异） |
| growth_records 门控 `if not is_governance_unification_enabled() or proposal_applied:` | `True or … = True` | 无条件追加，与旧版逐字节同语义 |
| incremental_update else 链 | 第一分支 `B.5 and gateway_handled` 未变；新增第二分支 `elif is_governance_unification_enabled()` 恒 False | 落到 `apply(event)` legacy fallback |
| run_full_consolidation 两处 | `not False = True` | apply(event) 与 apply_evaluated+add 均按旧行为执行 |
| Step 14.6 三分支 | `is_governance_unification_enabled()` 恒 False | auto_apply → `apply_proposal`；approval_required → 仅 enqueue；deny → continue。与抽出前逐行一致（O-2 仅搬移，无逻辑变更） |

**Step 14.6 抽取的逐行对比证明**（实施前内联循环原文与当前文件的直接比对）：

| 实施前内联循环（旧 orchestrator.py:1353-1375） | 抽取后 `_process_self_model_governance`（当前 2208-2253） | 差异 |
|------|------|------|
| `_auto_applied = _pending = _denied = 0` 三计数器 | 相同（2219-2221） | 无 |
| `for _record in ...: evaluate → deny: _denied+=1; continue` | 相同（2222-2226） | 无 |
| `create_proposal_from_growth → None 则 continue` | 相同（2227-2229） | 无 |
| `auto_apply: apply_proposal + _auto_applied+=1` | 相同（2238-2240，flag=False 走 else 分支） | 仅加 flag 守卫（False 时惰性） |
| `approval_required: if queue: enqueue + _pending+=1` | 相同（2245-2247） | 仅前置 flag 守卫的 persist（False 时惰性） |
| 末尾 `if _auto_applied or _pending or _denied: print(...)` | 相同（2248-2252） | 无 |
| try/except 隔离 | 保留在调用点（1355-1358），异常消息不变 | 无 |
| — | 返回 `{"auto_applied", "pending", "denied"}` | 纯增量，生产调用方忽略 |

### 3.2 测试级证明

- 18 用例中 4 个 A 态等价/B.5 保持用例：`test_a_state_proposal_path_unchanged`（apply_proposal 直写 + records 无条件追加 + delta/写入路径一致）、`test_a_state_legacy_fallback_unchanged`（proposal 失败仍 legacy apply）、`test_b5_mode_gateway_records_preserved`（B.5 单独开启语义不被 Phase B-1 破坏）、`test_is_growth_governed_matrix`（4 组合）。
- 真实链单测（非 fake）：`test_proposal_success_skips_apply`（41.5s）与 `test_incremental_update_produces_proposal`（108s）**单独运行均通过**。
- 回归 battery：probe+gateway+pipeline+fallback 34 passed；b13+admin 48 passed；382b/385 的 12 个失败与 Phase A 基线一致且单独运行通过（批量污染，非本任务引入）。

### 3.3 数据级证明

独立临时目录真实链快照：A 态正常写入 growth_state.json（hash `0cf2dc…`）+ personality_growth_history.json + 1 条 probe 记录——与修改前预期一致。

---

## 4. B 态治理闭环检查

### 4.1 P2/P2b 不变量

| # | 不变量 | 依据（pipeline.py） | 状态 |
|---|--------|---------------------|------|
| 1 | pending（NEED_REVIEW）绝不触发 legacy apply | 路由强制走 gateway（`_is_growth_governed()`）；proposal_applied=False → else 链 → 统一分支返回 deferred，`apply(event)` 不可达 | ✅（测试：needs_review_no_direct_apply） |
| 2 | defer 同上 | DEFER → park + deferred，同链 | ✅（deferred_park_no_records） |
| 3 | reject 同上（且不停车） | REJECT → status rejected；无 park、无 apply | ✅（rejected_no_park_no_records） |
| 4 | ACCEPT 后只有 gateway 执行 | 执行唯一入口 = `_apply_route` 闭包内的 `engine.apply_proposal(single)`（pipeline.py:458），主流程不再直调 engine | ✅（accept_forced_through_gateway） |
| 5 | growth_records 仅 applied 后追加 | F1 门控 `not is_governance_unification_enabled() or proposal_applied` | ✅（5 用例覆盖 + 快照验证 history hash 不变） |

### 4.2 P3 不变量（Step 14.6）

| # | 不变量 | 依据（orchestrator.py） | 状态 |
|---|--------|------------------------|------|
| 1 | B 态 self_model 只生成 B-store pending proposal | `_persist_self_model_governance_proposal` 构造 `proposal_type="self_model", status="pending"` + metadata 载荷，经 `storage.save()` | ✅ |
| 2 | 无任何隐藏 apply | B 态下 `_self_model_updater.apply_proposal` 仅存在于 A 分支（2239），B 态分支只 persist + enqueue | ✅（测试断言 apply_calls==0） |
| 3 | 不可能绕过 approval_required | auto_apply 降级并入 approval_required 行为（persist+enqueue）；deny 跳过；无第三条路径 | ✅ |
| 4 | policy 零修改 | §2.2 比对 | ✅ |

---

## 5. 绕过点全量清单（写入口扫描）

扫描范围：pipeline.py / orchestrator.py / growth_engine.py / self_model_updater.py / runtime_core.py + 治理相关 admin 模块。仅列**影响人格状态 / self_model 状态**的写入口。

| # | 写入口 | 触发条件 | Phase B 状态 |
|---|--------|----------|--------------|
| W1 | pipeline.py:458 `_apply_route` → engine.apply_proposal | gateway ACCEPT | ✅ 治理内执行 |
| W2 | pipeline.py:599 legacy apply_proposal | 统一关闭且 B.5 关闭 | ✅ A 态 legacy（保留） |
| W3 | pipeline.py:615/621 apply_evaluated + growth_records.add | growth_allowed | ✅ F1 门控（B 态仅 applied） |
| W4 | pipeline.py:663 legacy apply(event) | proposal 未 applied | ✅ B 态 fail-closed 抑制 |
| W5 | pipeline.py:809/813/825 run_full_consolidation 两写 | 批量整理 | ✅ B 态抑制 + 门控 |
| W6 | growth_engine.apply / apply_proposal（update_metrics+save） | 仅被 W1/W2/W4/W5 调用（全仓核实无其他调用方） | ✅ 收敛于 pipeline，已覆盖 |
| W7 | growth_engine.apply_evaluated | 仅生成记录不写数值（docstring 核实） | ✅ 非直写 |
| W8 | orchestrator.py:2239 updater.apply_proposal | A 态 auto_apply | ✅ B 态不可达 |
| W9 | orchestrator.py:2335 `_create_growth_proposal` storage.save | relationship 型 pending 账本 | ✅ 非绕过（零 apply） |
| W10 | orchestrator.py:2255+ `_persist_self_model_governance_proposal` | B 态 Step 14.6 | ✅ 治理账本（零 apply） |
| W11 | runtime_core.py:2072 internal updater.apply_proposal | parent `accept_growth_proposal`（全仓零生产调用方，已核实） | ⚠️ 残余 dormant → Phase C |
| W12 | runtime_core.py:1999 personality_adapter.apply_proposal | preview 语义（docstring：不写真实状态） | ✅ 非直写（Phase C 复核） |
| W13 | runtime_core.py:6438 drain → PersonalityState | B-store approved 且 personality 型 | ✅ 治理内（approved 前提；self_model 型无消费器） |
| W14 | runtime_core.py:6341 `_stage_13_self_model_persistence` | 主链 ctx._self_model_changed 持久化（既有 Phase 4.0.x 工作，非治理 apply） | ⚠️ 范围外（主链，非 legacy），登记 Phase D |
| W15 | proposal_manager.apply_proposal(actor) / growth_integration.apply_proposal | 人工审核执行；auto_accept 默认 False（config 无键） | ✅ 治理内 |
| W16 | src/admin/selfmodel_consumer.py SelfModelConsumer | 全仓零生产调用方（仅测试/文档引用），dormant 基础设施 | ✅ 无生产写路径 |
| W17 | PersonalityResolver.resolve() 内 self_model 同步写（P4） | 主链读取侧 | ⚠️ 范围外（主链）；B 态经 W3 门控切断 legacy 来源记录后实际不再新增 → 快照已验证 history 不变 |

**结论**：统一治理模式下，legacy 链全部人格/self_model 写入口（W1-W8）已被 Phase B 覆盖；残余项 W11（dormant）、W13 的 self_model 消费缺口、W14/W17（主链）均为最终决策文档已登记项，分别对应 Phase C / Phase D。无新发现绕过点。

---

## 6. 测试可靠性审查

| 审查项 | 结论 |
|--------|------|
| 真实路径覆盖 | ✅ 关键生产分支用真实代码：真实 `_route_proposal_through_gateway` + 真实 `_park_proposal_for_review` + 真实 `attach_governance_linkage`（fake A-store 注入）；真实 `_process_self_model_governance` + 真实 `_persist_self_model_governance_proposal`（fake B-store 注入）。mock 边界仅止于 gateway evaluate 本体（由 test_growth_mutation_gateway.py 独立覆盖）与 store 落盘替身——非 mock 自娱 |
| singleton/global 污染 | ✅ conftest trap 正则扫描 = NONE；autouse fixture 每测复位统一开关；monkeypatch 自动还原 gateway flag 与 storage 注入；唯一进程级全局（统一开关）仅本文件读写 |
| 测试顺序依赖 | ✅ 无：每测独立构造对象；零真实 data 写入（全部 fake store） |
| isolated subprocess 测试 | ⚠️ 缺失：conftest trap 禁止在测试文件内构造真实单例，真实文件级验证目前为手工快照脚本（实施文档 §4）。**建议 Phase C 固化为 subprocess 隔离测试** |

---

## 7. 已知风险

| 编号 | 风险 | 等级 | 处理 |
|------|------|------|------|
| R1 | git diff 相对 HEAD 含既有未提交工作，评审边界易混淆 | 中 | 以实施文档 §2 清单为界（§2.3） |
| R2 | probe 将「ACCEPT 后的治理内执行」也记为 direct_apply，判据需区分路径 | 低 | 已在实施文档 §5.4 声明语义边界 |
| R3 | B 态 self_model 提案积压（Phase B 窗口内 approved 无消费器） | 中 | admin 可拒绝；Phase C 消费器补齐 |
| R4 | runtime_core.py:2072 dormant auto-apply（零调用方） | 低 | Phase C 门控 |
| R5 | run_full_consolidation 在仅 B.5 开启（统一关闭）时仍直写 | 低 | B.5 迁移层既有范围，未扩大 |
| R6 | 统一开关为进程内全局、无热配置 | 低 | 设计使然（fallback 不可绕过）；Phase D 评估配置化 |

---

## 8. Phase C 前置条件（进入前必须满足）

1. Phase C 任务书单独授权 runtime_core.py 修改边界（含 drain 邻近 stage 13 区域）。
2. 门控 runtime_core.py:2072（`accept_growth_proposal` 链的 auto_apply 分支，flag-gated，3 行条件）。
3. 实现 self_model approved 消费器（Option B：镜像 drain 约束——config 开关默认 False、limit、已应用跳过、fail-soft、只消费 type=self_model、metadata 反序列化 SelfModelChangeProposal），并在 approved→apply 前评估前后快照检查。
4. 将手工快照脚本固化为 isolated subprocess 测试（真实单例 + 临时 cwd + 文件 hash 断言）。
5. B 态实验期监控指标：probe direct_apply==0（非 ACCEPT 路径）、A-store park 计数、B-store self_model pending 计数。
6. 红线持续有效：Stage 04 零修改、drain 方法体语义零修改、RuntimePipeline 主链零修改、policy 规则零修改、legacy auto_apply 保留、生产 flag 默认 False、无自动 approve。
