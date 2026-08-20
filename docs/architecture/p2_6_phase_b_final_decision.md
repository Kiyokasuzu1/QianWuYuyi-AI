# P2.6 Phase B 实施前最终架构决策审查

> 性质：只读审查（不修改代码、不新增模块、不修改配置、不开启 flag、不删除 legacy auto_apply）
> 依据文档：
> - docs/architecture/p2_6_governance_unification_audit.md
> - docs/architecture/p2_6_phase_a_implementation.md
> - docs/architecture/p2_6_phase_b_design_review.md
> 审查日期：2026-08-21
> 审查方式：对全部结论点做了代码级复核（复核清单见附录 B），并在设计审查之上补充 6 项新发现（附录 A）。

---

## 0. 结论速览（TL;DR）

| 问题 | 裁决 |
|------|------|
| 1. Proposal 双体系处理策略 | **保留并存**（推荐），B-store 为长期权威账本；Phase B/C/D 不合并；复用现有 adapter，禁止第三套 |
| 2. SelfModel apply 闭环 | **Option B**（self_model 专用 approved 消费器，镜像 drain 约束）；Phase B 不落地（零 apply），推迟 Phase C；Option A 否决，Option C 列为长期方向 |
| 3. Phase B 修改边界 | **仅 pipeline.py + orchestrator.py**（+新测试 +新实施文档）；RuntimeCore 零修改 —— 确认满足 |
| 4. fallback 治理验证 | 无未覆盖绕过点；新增 1 处必须截断点（growth_records 门控）；残余 2 处登记 Phase C/D |
| 5. 风险评分 | Proposal 转换=低 / SelfModel apply=低 / fallback=低 / 数据兼容=低 / 回滚=低 |
| Go/No-Go | **允许进入 Phase B 实现**（附 §8 checklist 前提） |

---

## 1. Proposal 双体系最终处理策略（问题 1）

### 1.1 事实确认（两套体系现状）

| 维度 | 体系 A（生成侧） | 体系 B（治理侧） |
|------|------------------|------------------|
| 模型 | `src/contracts/growth_schema.py::GrowthProposal`（frozen，schema_version "1.0"） | `src/growth/proposal/proposal.py::GrowthProposal`（含 before/after_state、reviewer_id、metadata） |
| 存储 | `src/growth/proposal_store.py::ProposalStore`（JSONL `data/proposals/`，latest-wins，VALID_STATUSES 含 "approved"） | `src/growth/proposal/storage.py::ProposalStorage`（JSON 原子写 `data/growth/proposals/`，MAX_PROPOSALS=500） |
| 状态机 | proposed/accepted/rejected/cancelled/expired | pending/approved/rejected/applied/cancelled |
| 现有消费者 | B.5 gateway park（`pipeline._park_proposal_for_review`）、ProposalManager | admin 审核（`governance_provider.review_proposal` → admin UI）、drain（仅 personality 型）、`orchestrator._create_growth_proposal`（relationship pending） |
| 桥接 | `src/runtime/adapters/growth_proposal_adapter.py`（Type A↔B，detect/convert/build_approval_input） | 同左（已 sanctioned） |

### 1.2 是否应该长期并存 → 是（短期至中期并存）

理由：
1. 两套体系各有**唯一**生产消费者：生成/停车侧依赖 A（JSONL 追加、latest-wins、B.5 治理链接），审核/应用侧依赖 B（admin UI + drain 消费 + 原子 JSON）。删除任何一套都必须改主链消费者，违反 Priority 4（禁止大规模重构）。
2. 两套之间的 sanctioned bridge（growth_proposal_adapter）已在生产使用，链路已验证。
3. AGENTS.md Priority 2「闭环优先」：当前第一优先是让 P2/P2b/P3 进入治理闭环，而不是收敛存储。

### 1.3 是否存在必须合并的风险 → 存在，但无强制触发点

合并风险清单（定性为「维护成本」而非「正确性」风险）：

| 编号 | 风险 | 评估 |
|------|------|------|
| R-dup-1 | 双 pending 队列：人工审核只走 B（admin UI），A-store 的 pending 停车提案需另行进入审核通道 | 已有缓解：B.5 链路经 adapter 在 A-store 落治理链接（mutation_id/request_id/trace_id），Phase B 继续沿用 |
| R-dup-2 | 状态语义漂移：A "accepted" ≠ B "approved"；A-store 的 approved 状态无消费器 | 不触发正确性问题；A-store approved 不驱动任何写入 |
| R-dup-3 | 审计分叉：两套存储各自产生审计记录 | Phase A probe 独立于两 store；可接受 |

强制合并的触发条件（**均不满足**）：
1. 出现第三类消费者需要同时跨两套 store 读取（未出现）；
2. 出现跨 store 一致性 bug 报告（未报告）；
3. 出现跨提案历史的事务性需求（无需求）。

→ 裁决：**本阶段不合并**。

### 1.4 Phase B 是否继续复用现有 adapter → 是（强制）

- Phase B 全部 proposal 流转复用 `growth_proposal_adapter` 与既有两套 store，按用途分工：P2/P2b 停车走 A-store（B.5 park），P3 持久化走 B-store（admin + drain 链）。
- **禁止创建第三套 proposal 模型 / store / adapter**（红线，与任务书一致）。

### 1.5 长期权威 proposal source → B-store

裁决：`src/growth/proposal/storage.py`（B-store）为「已审核提案 / 生命周期」的**长期权威账本**，因为它是唯一同时具备以下三项的存储：
1. 人工审核闭环（`governance_provider.review_proposal` + admin UI，可 approve/reject/modify）；
2. 完整生命周期状态机（pending → approved → applied）；
3. 机器消费闭环（drain 只读 B-store approved）。

A-store 定位：**生成侧暂存与治理停车（park）层**——提案在此生成/暂存，通过 adapter 进入 B 账本后受审核。

**三方案输出：**

| 方案 | 内容 | 裁决 |
|------|------|------|
| 保留方案（推荐） | 双体系并存 + adapter 桥接 + 权威分工（A=生成/停车，B=审核/应用账本） | **采纳** |
| 合并方案 | 将 A-store 收敛进 B-store（或反向） | **不采纳**：触发条件不满足，回归面远大于收益；合并作为 Phase D 之后独立治理任务评估 |
| 迁移方案 | 渐进收敛：A-store 最终一致性镜像全部迁入 B-store | **记录方向，不执行**：Phase D 之后才启动；Phase B/C/D 保持现状 |

---

## 2. SelfModel apply 闭环裁决（问题 2）

### 2.1 三选项事实分析

| 维度 | A：复用 PersonalityEvolutionPipeline + drain | B：self_model 专用 approved 消费器（镜像 drain） | C：统一 Domain Apply Pipeline |
|------|---------------------------------------------|------------------------------------------------|-------------------------------|
| 数据语义 | ❌ 不兼容：drain 消费 ChangeItem(path=personality.traits.*) 写 PersonalityState；SelfModelChangeProposal 是 narrative_append/self_understanding，narrative 内容无处安放 | ✅ 一致：SelfModelChangeProposal ↔ SelfModelUpdater 同域数据结构往返 | ⚠️ 需要新契约定义全部领域路由，超范围 |
| 领域边界 | ❌ 违反：把 self_model 变更写入 PersonalityState（personality_state.json），跨域直写，违反单一职责 | ✅ 不违反：SelfModelUpdater 是既有 self_model 域执行件，只写 SelfModelStore | ⚠️ 新领域路由需重新划定边界 |
| Identity Continuity Check | ⚠️ drain 应用后的 identity continuity check 保护的是 PersonalityState，对 self_model narrative 无意义 | ✅ 不触碰 IDENTITY_CORE / PersonalityState；建议 Phase C 实现时在 approved→apply 前增加一次前后快照比较（入 Phase C checklist） | 未定义 |
| PersonalityState 生命周期 | ❌ 影响：追加写 personality_state.json | ✅ 不影响：只写 self_model store | 未定义 |
| 实现成本 | 低，但语义错误 | 中（镜像 drain 的一个新方法） | 高（新 Pipeline + 全领域路由，违反 Priority 4） |

### 2.2 最终推荐：Option B

- **Option A 明确否决**：写入目标错位（PersonalityState vs SelfModelStore）、narrative 内容丢失、跨域直写。
- **Option B 采纳**：self_model 专用 approved 消费器，完整镜像 drain 的约束集（config 开关默认 False、limit、已应用跳过、fail-soft、只消费 type=self_model、从 metadata 反序列化 SelfModelChangeProposal）。
- **Option C**：列为 Phase D 之后治理统一收尾时的长期方向，本阶段不采纳。

### 2.3 落地时点裁决（本次审查的关键调整）

任务书问题 3 要求确认「不修改 RuntimeCore」。Option B 消费器的自然落点是 RuntimeCore stage 13（与 drain 并列）——与红线冲突。

裁决：
- **Phase B 不实现 Option B 消费器**。Phase B 的 P3 交付 = 提案化 + B-store 持久化（type=self_model, status=pending）+ 人工审核 + **零 apply**。
- self_model approved 的 apply 消费器推迟到 **Phase C**，届时由 Phase C 任务书单独明确 RuntimeCore 修改边界。
- 可接受性：flag 默认 False，B 态只存在于测试/A-B 环境；approved 提案在 Phase B 窗口内只有「审核通过」记录、无 apply，积压可由 admin 拒绝、Phase C 消费器补齐。
- 该裁决与 §4.2 新发现 F2（runtime_core.py:2072 dormant auto-apply）共同支撑「apply 闭环统一在 Phase C 闭合」。

---

## 3. Phase B 最小修改边界确认（问题 3）

### 3.1 允许修改（白名单，共 2 个生产文件）

| 文件 | 修改点 | 性质 |
|------|--------|------|
| `src/growth/pipeline.py` | ① line 574 条件并入统一开关：新增私有 helper `_is_growth_governed()` = `is_growth_mutation_gateway_enabled() or is_governance_unification_enabled()`；② line 622 同 helper；③ **新增**（本审查 F1）：line 596-603 growth_records 门控——统一态下仅当 proposal ACCEPT-applied 才 `apply_evaluated` + `growth_records.add`，A/B.5 态行为不变；④ run_full_consolidation 对应两处（781 apply 抑制、778 growth_records 门控；该方法当前零生产调用方，防御性覆盖） | 条件分支，无删除 |
| `src/orchestrator.py` | Step 14.6（line 1364-1365）：统一态下 auto_apply 分支并入 approval_required 分支——创建 SelfModelChangeProposal → 持久化为 B-store GrowthProposal(proposal_type="self_model", status="pending", affected_dimensions=record.affected_dimensions, metadata={"self_model_proposal": ..., "governance_decision": ..., "source": "legacy_step_14_6"}) → 继续 enqueue 内存队列 → **零 apply** | 条件分支，无删除 |
| `tests/test_governance_unification_phase_b.py` | 新增测试（注意 conftest `_SINGLETON_PATTERN` token 陷阱，用 getattr 模式） | 新增 |
| `docs/architecture/p2_6_phase_b_implementation.md` | 实施文档 | 新增 |

### 3.2 禁止修改（黑名单，逐条确认）

| 文件/对象 | 要求 | 确认 |
|-----------|------|------|
| `src/runtime/runtime_core.py`（含 drain 方法体、stage 13、line 2072、line 1999） | 零修改 | ✅ 满足（本裁决把 Option B 消费器推迟 Phase C） |
| `src/runtime/runtime_pipeline.py`（RuntimePipeline 主链） | 零修改 | ✅ 满足 |
| LifecycleExecutor 契约 | 零修改 | ✅ 满足 |
| `src/runtime/runtime_controller.py`（fallback 结构） | 零修改 | ✅ 满足 |
| Stage 04 `src/growth/growth_integration.py::accept_experience` | 零修改 | ✅ 满足（红线） |
| `src/growth/growth_engine.py` | 保持 Phase A 探针状态 | ✅ 满足 |
| `src/personality/self_model_governance.py`（policy 逻辑） | 零修改（Phase A 探针保留） | ✅ 满足（决策/执行分离：policy 只出决策，截断在调用方） |
| `src/personality/self_model_updater.py` | 零修改 | ✅ 满足 |
| `src/personality/personality_evolution_pipeline.py`、`personality_adapter` | 零修改 | ✅ 满足 |
| 所有 `data/` 文件、`config.yaml` | 不新增键、不改值、不开 flag | ✅ 满足 |
| legacy auto_apply 代码路径 | 不删除（A 态仍需，只加条件分支） | ✅ 满足（红线） |

### 3.3 flag=False 字节级兼容确认

- line 574：统一态恒 False → `False or False = False` → 走 585 旧路径，与现状逐字节同路径。
- line 622：同理 → 走 630 legacy apply(event)。
- line 596-603 门控：A 态条件恒真 → 原行为（records 无条件追加）。
- line 1364：统一态恒 False → 原 auto_apply。
- 验证手段：设计审查 §6 的 A/B battery（失败集合 diff + data/ md5 快照 + probe 行为指标）。

---

## 4. fallback 治理验证 —— 绕过点全量清单（问题 4）

### 4.1 收敛性论证（fallback ≠ 绕过）

生产入口共 4 条（本次审查逐一核实）：

1. `runtime_pipeline.run()` 主链；
2. `runtime_pipeline.py:988` pipeline 内 fallback → `orchestrator.process()`；
3. `runtime_controller.py:72` legacy fallback → `orchestrator.process()`；
4. `orchestrator/long_loop.py:348` 直调 `orchestrator.process()`。

四条路径的 growth 写全部收敛到 `pipeline.incremental_update`（orchestrator.py:1327 调用）与 Step 14.6。统一 flag 是**模块级全局**（`governance_unification.py`），两处截断点对所有入口无条件生效 → **fallback 无法绕过治理**。probe `direct_apply=0` 为全局不变式兜底。

### 4.2 直写/影响调用点全量清单（12 项，逐一定性）

| # | 位置 | 类型 | B 态处理 | 定性 |
|---|------|------|----------|------|
| 1 | pipeline.py:445 `_apply_route` 闭包 | B.5 网关 ACCEPT 执行 | 经网关 ACCEPT 后执行 | 治理内，非绕过 |
| 2 | pipeline.py:585 legacy `apply_proposal` | GrowthState 直写 | 切断（helper 条件，§3.1-①） | **Phase B 覆盖** |
| 3 | pipeline.py:630 legacy `apply(event)` | GrowthState 直写 | 抑制（622 分支，§3.1-②） | **Phase B 覆盖** |
| 4 | pipeline.py:781 run_full_consolidation 内 `apply(event)` | GrowthState 直写（零生产调用方，dormant） | 抑制（§3.1-④） | **Phase B 覆盖** + dormant |
| 5 | pipeline.py:596-603 `apply_evaluated` + `growth_records.add` | **人格影响泄漏点**（新发现 F1） | 统一态仅 applied 追加（§3.1-③） | **Phase B 覆盖（本审查新增截断点）** |
| 6 | orchestrator.py:1365 Step 14.6 auto_apply | SelfModelStore 直写 | 转 approval_required + B-store 持久化，零 apply（§3.1） | **Phase B 覆盖** |
| 7 | runtime_core.py:2072 `_self_model_updater_internal.apply_proposal` | SelfModelStore 直写（store-backed，3520 初始化已核实） | Phase B 不动（RuntimeCore 红线） | **残余 → Phase C**（新发现 F2） |
| 8 | runtime_core.py:1999 `_preview_proposal_evolution` | docstring 明示「不会修改任何真实人格状态或 Persona 文档」，preview 语义 | 不动 | 非绕过（新发现 F3；Phase C 复核 adapter 无副作用） |
| 9 | runtime_core.py:6390 drain_approved_growth_proposals（6355 stage13 / 1287 startup） | 只消费 B-store approved personality 型 → PersonalityState | 不动 | 治理内（approved 才应用），非绕过 |
| 10 | orchestrator.py:2227 `_create_growth_proposal` | 只写 B-store pending（relationship 型），零 apply | 不动 | 治理账本，非绕过 |
| 11 | growth_integration.py:536 `accept_experience`（Stage 04） | → ProposalManager → A-store pending；auto_accept_enabled 默认 False（config 无 auto_accept 键，已核实） | 不动（红线） | 治理内，非绕过 |
| 12 | proposal_manager.apply_proposal / growth_integration.apply_proposal(actor) | 人工审核后执行路径 | 不动 | 治理内，非绕过 |

**#5 说明（F1，本审查最重要新发现）**：已核实 `apply_evaluated` 本身「仅生成记录，不修改人格数值」，但生成的 record 经 `growth_records.add`（pipeline.py:602 / 778）写入共享 PersonalityGrowthHistory（pipeline.py:104-153），而 PersonalityResolver 在 Phase 3.2 据该历史**统一变更人格向量**并条件同步 self_model store（审计 P4）。若 B 态不门控此追加，GrowthState 虽被冻结，人格向量仍会经 resolver 漂移——「未 approve 前状态不变化」判据会在 resolver 层失效。因此该门控是 Phase B 的**必备截断点**（A 态行为不变）。

### 4.3 兜底不变式

- Phase A probe：B 态下观察点 `direct_apply` 计数必须 = 0（覆盖 engine.apply / apply_proposal 与 Step 14.6 决策三处）。
- probe 已知盲区：apply_evaluated → growth_records 路径（非 GrowthState 直写）→ 由 §3.1-③ 新门控覆盖；resolver 主链 P4 同步写（主链，审计已列）→ Phase C/D 治理。

### 4.4 结论

Phase B 设计 + 新增 growth_records 门控后，legacy 链**无未覆盖绕过点**。残余 2 项（#7 runtime_core.py:2072 dormant、P4 resolver 主链）均为已知、可接受，分别登记 Phase C / Phase D，并在 §5 风险表登记。

---

## 5. Phase B 实施风险评分（问题 5）

| 风险项 | 评分 | 依据 | 缓解 |
|--------|------|------|------|
| Proposal 转换 | **低** | 复用 sanctioned adapter + 既有 B.5 park 链路；无新模型；B-store 枚举已含 type=self_model 与全部所需状态（设计审查 §3.5 已确认无需补充枚举） | A/B battery 覆盖转换往返 |
| SelfModel apply | **低** | Phase B 零 apply（apply 推迟 Phase C，§2.3）；policy 逻辑零修改；唯一残余 runtime_core.py:2072 为 dormant（parent `accept_growth_proposal` 在 src 内零生产调用方，已核实） | Phase C 门控任务已登记（checklist #9） |
| fallback | **低** | 模块级全局 flag + 4 入口收敛论证（§4.1）+ probe direct_apply=0 不变式（§4.3） | B 态专项测试覆盖 runtime_controller:72 入口 |
| 数据兼容 | **低** | flag=False 字节级同路径（§3.3）；B 态新增写入仅 proposals 两 store 追加（可删除回滚）；无 data 结构变更 | A/B md5 快照对比 |
| 回滚 | **低** | 回滚 = flag 置 False + git 还原 2 个生产文件 + 清理测试期提案记录；无数据迁移 | 回滚预案（checklist #10） |

总体：**允许进入 Phase B**。

---

## 6. 架构裁决汇总 + Go/No-Go

### 6.1 架构裁决（5 条）

1. **双 proposal 体系保留并存**：Phase B/C/D 不合并；B-store 为长期权威审核/应用账本，A-store 为生成/停车层；复用现有 adapter，禁止第三套模型/store/adapter。
2. **SelfModel apply 闭环 = Option B**：self_model 专用 approved 消费器（镜像 drain 约束），Phase C 落地；Phase B 交付 P3 提案化 + 零 apply；Option A 否决；Option C 列为 Phase D 后长期方向。
3. **Phase B 修改边界 = pipeline.py + orchestrator.py**（+新测试 +新实施文档）；RuntimeCore / Stage 04 / RuntimePipeline 主链 / LifecycleExecutor 契约零修改——确认满足任务书全部约束。
4. **Phase B 必须新增 growth_records 门控**（F1：本审查发现的人格影响泄漏点，否则 resolver 层绕过治理）。
5. **治理红线（§7）全部保持**，其中新增第 8 条「A 态字节级行为不变」。

### 6.2 Go/No-Go 裁决

**允许进入 Phase B 实现**，前提是 §8 checklist 全部满足。实现过程中若发现本审查 §4.2 清单之外的新写入点：**暂停实现**，回到本审查补录后再继续。

---

## 7. 必须保持的红线

1. 不修改 Stage 04 `accept_experience`。
2. 不修改 approved drain 语义（`drain_approved_growth_proposals` 方法体零改动）。
3. 不修改 RuntimePipeline 主链 / LifecycleExecutor 契约 / RuntimeCore（Phase B 不触碰 runtime_core.py 任何行）。
4. 不删除 legacy auto_apply 代码路径（A 态仍需；只做条件分支）。
5. 生产 flag 保持 False（默认值，任何代码路径不得自动开启）。
6. 不新建第二/第三套 proposal、store、adapter、模型。
7. 禁止任何自动 approve（测试环境 approve 仅通过显式调用现有 approve API）。
8. 【本审查新增】A 态（flag=False）字节级行为不变——所有 Phase B 修改必须通过设计审查 §6 的 A/B 六判据 battery。

---

## 8. 实施前 checklist

- [ ] 1. 将 §4.2 的 12 个写入点清单与实现截断点一一对应复核（尤其 #5 growth_records 门控）。
- [ ] 2. 实现 `_is_growth_governed()` helper 后，先跑 A 态 battery：失败集合与 Phase A 后基线一致（73 passed / 12 pre-existing failed），无新增失败。
- [ ] 3. 新测试文件规避 conftest `_SINGLETON_PATTERN` token 列表（用 getattr 模式）。
- [ ] 4. B 态六判据：① direct_apply=0（probe）；② proposal 数增加；③ gateway 调用增加；④ 未 approve 前 growth_state / self_model 快照不变；⑤ approve 后（personality 型走既有 drain；self_model 型仅状态迁移，零 apply）；⑥ fallback 路径（runtime_controller:72 入口）同样治理化。
- [ ] 5. growth_records 门控专项测试：B 态 parked / REJECT / DEFER 不产生 growth_records 追加；ACCEPT 后追加且与 applied 列表一致；A/B.5 态行为不变。
- [ ] 6. A/B 数据快照：data/growth_state.json、self_model store、personality_state.json 运行前后 md5 对比。
- [ ] 7. 确认 config.yaml 未新增任何键；`growth_apply_drain_enabled: true`（config.yaml:146）保持原样。
- [ ] 8. 输出 docs/architecture/p2_6_phase_b_implementation.md（修改点、测试结果、A/B 数据）。
- [ ] 9. Phase C 任务书登记两项残余：runtime_core.py:2072 flag 门控、self_model approved 消费器（Option B 落地，含 approved→apply 前快照检查评估）。
- [ ] 10. 回滚预案就绪：flag=False + git 还原 pipeline.py / orchestrator.py + 清理测试提案记录。

---

## 附录 A：本审查新增发现（相对设计审查）

| 编号 | 发现 | 影响 |
|------|------|------|
| F1 | `apply_evaluated` 纯生成记录，但 `growth_records.add`（pipeline 602/778）写入共享 PersonalityGrowthHistory，resolver 据此变更人格向量 → B 态人格影响泄漏点 | Phase B 新增截断点（§3.1-③） |
| F2 | runtime_core.py:2072 第二处 SelfModel auto_apply（store-backed，3520 初始化），parent `accept_growth_proposal`（1788）在 src 内零生产调用方 | 残余 dormant 路径 → Phase C 门控 |
| F3 | runtime_core.py:1999 preview 路径，docstring 明示不写真实状态 | 非绕过；Phase C 复核 adapter 无副作用 |
| F4 | `apply_evaluated` 方法体核实：「仅生成记录，不修改人格数值」 | 支持 F1 定性 |
| F5 | 生产入口收敛 4 条（主链 / pipeline:988 / runtime_controller:72 / long_loop:348），全部收敛于 pipeline.incremental_update + Step 14.6 | fallback ≠ 绕过论证（§4.1） |
| F6 | config.yaml:146 `growth_apply_drain_enabled: true`；config 无 auto_accept 键（auto_accept 默认 False） | checklist #7 依据 |

## 附录 B：审查方法

- 复核文件：src/growth/pipeline.py（445/555-648/778/781）、src/growth/proposal/storage.py（全量）、src/growth/proposal/constants.py（全量）、src/runtime/runtime_core.py（631/1287/1788-1866/1960-2095/3505-3537/6355）、src/orchestrator.py（1327/1364-1365/2227-2280）、src/growth/growth_engine.py（apply_evaluated）、src/runtime/runtime_pipeline.py（路径审计结构）。
- 交叉验证：`accept_growth_proposal` 外部调用方 = 0（governance_provider / API routes 均不调用）；GrowthEngine 直写调用点全集 = pipeline 4 处；`_self_model_updater_internal` = store-backed 真实 Updater。
- 本审查仅新增本文档，未修改任何代码、配置或数据文件。
