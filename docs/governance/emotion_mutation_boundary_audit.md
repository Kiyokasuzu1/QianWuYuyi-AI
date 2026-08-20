# P2.3-B.6 Emotion Mutation Boundary Audit（只读审计）

> 阶段：P2.3-B.6（Emotion 域状态变化边界审计）
> 分支：develop/v1.1 ｜ HEAD：225d040 ｜ 日期：2026-08-20
> 前置文档：docs/governance/mutation_boundary_audit.md（B.1）、
> docs/governance/mutation_gateway_design.md（B.2）、
> docs/governance/mutation_gateway_implementation_report.md（B.3）、
> docs/governance/p24b4_personality_mutation_migration_report.md（B.4）、
> docs/governance/p25b5_growth_mutation_migration_report.md（B.5）
> 本审计为**只读**：未修改 src/、data/、tests/ 任何文件；未接入 MutationGateway；
> 未改变任何运行行为。唯一产出即本文件。

---

## 0. 基线快照（Phase 0）

| 项 | 值 |
|---|---|
| branch | develop/v1.1 |
| HEAD | 225d040 chore(data): organize data artifacts and archive legacy records |
| data/ | 干净（`git status --short -- data/` 为空；仅 data/archive/README.md 受跟踪） |
| src/emotion/ | 25 文件共 1402 行；本次审计期间**零修改** |
| B.3 governance | src/governance/ 在位：mutation_contract.py / mutation_gateway.py / audit_writer.py / checks{identity,boundary,evidence,conflict,audit,base}；`PRIVILEGED_ACTORS = {admin, system, auto_accept, gateway, growth_system}`（growth_system 为 B.5 追加） |
| B.4 | src/personality/mutation_adapter.py 在位（personality_mutation_gateway_enabled 默认 False） |
| B.5 | src/growth/mutation_adapter.py + pipeline 网关分支在位（growth_mutation_gateway_enabled 默认 False） |
| MutationJournal | src/runtime/request_context.py:375 定义（结构在，生产零写入 — B.1 B11） |

工作区既有未提交改动（runtime/* 系列 M 文件、docs/governance 未跟踪等）均为
B.4/B.5 及上下文阶段遗留，与本审计无关，审计未触碰其中任何文件。

---

## 1. Emotion 状态模型测绘（Phase 1）

### 1.1 核心数据结构

| 结构 | 位置 | 性质 |
|---|---|---|
| EmotionState（6 维：valence/arousal/curiosity/anxiety/confidence/energy + updated_at） | src/emotion/emotion_state.py:17 | 短期情绪状态；`apply_delta()` 返回新对象（纯函数）；`dominant`/`intensity` 为派生只读属性 |
| EmotionDelta | src/emotion/emotion_delta.py:9 | 变化量协议（非状态） |
| EmotionEvent | src/emotion/emotion_event.py:9 | 事件协议（event_type/intensity/description/source） |
| EmotionalTrace | src/emotion/emotional_trace.py:25 | 一次情绪变化的证据引用（trace_id/memory_id/cause/event_type） |
| EmotionPattern | src/emotion/emotion_pattern.py | 长期模式（pattern_type/event_type/emotion/confidence/stability/evidence_trace_ids） |
| EmotionBelief | src/emotion/emotion_belief.py | 情绪信念（跨域：→ SelfModel） |
| EmotionTransitionRecord / EmotionDynamicsSnapshot | src/contracts/emotion_dynamics_schema.py:18/47 | 瞬态记录（内存，不落盘） |
| MutationJournal（v2 契约） | src/runtime/request_context.py:375 | 只记录不生效；生产零写入 |

### 1.2 Emotion Mutation Registry（写入口全量登记）

图例：门=权限门（can_modify_emotion）；Proposal=GrowthProposal 类审批；Approval=审核；
Audit=record_audit_log/MutationJournal/域事件。风险：高/中/低。

| ID | file:line:function | 写入目标 | 当前调用链 | 直接改状态 | 经 Proposal | 经 Approval | Audit | 风险 |
|---|---|---|---|---|---|---|---|---|
| E-01 | src/emotion/emotion_manager.py:46-47 `process_event` | EmotionState 6 维 + data/emotion_state.json | 见下方 C1-C6 六条调用链 | 是（apply_delta→替换→save） | 否 | 否 | 视调用方（C2 无） | 高 |
| E-02 | src/emotion/emotion_manager.py:76-87 `update` | EmotionState（时间衰减）+ 落盘 | emotion_adapter_impl.py:141（legacy 链） | 是（decay.apply→替换→save） | 否 | 否 | 否 | 中 |
| E-03 | src/orchestrator.py:2074-2075 `_process_emotion_post` | **现场改写共享 repo.filepath** → per-user 文件 | orchestrator.py:1283（主链后置） | 是（绕过 EmotionManager 封装直调 repo.save） | 否 | 否 | 条件式（dominant/intensity 变化才记） | 高 |
| E-04 | src/orchestrator.py:2077/2081 `_atomic_write_emotion_fallback`（orchestrator.py:103 定义） | per-user 情绪文件（state dict 原子写） | 同上 fallback 分支 | 是（直接文件写） | 否 | 否 | 同上条件式 | 中 |
| E-05 | src/emotion/emotion_repository.py:16-27 `save` | data/emotion_state.json（原子写原语） | 被 E-01/E-02/E-03 调用 | 持久化原语 | — | — | — | （原语） |
| E-06 | src/emotion/emotion_manager.py:101-119 `increment/reset_analysis_counter` | data/emotion_analysis_counter.json | **increment 零生产调用方**；reset 仅 EmotionGrowthService（死链） | 是（计数器） | 否 | 否 | 否 | 低 |
| E-07 | src/emotion/emotion_trace_repository.py:45-49 `append` | data/emotional_traces.json（整文件重写） | E-01 process_event 内 bind→append | 追加（证据流） | — | — | 无 | 低 |
| E-08 | src/emotion/emotion_pattern_repository.py:39-44 `append/save_all` | data/emotion_patterns.json | **零生产调用方**（grep 仅类内引用） | 追加 | — | — | 无 | 低 |
| E-09 | src/emotion/emotion_growth_service.py:41-100 `analyze_and_merge` | SelfModelV3 信念 + self_model 落盘（跨域） | **零生产实例化**（grep 无调用方） | 是（bridge.merge 内存直改 + legacy save） | 否（adapter 注入路径除外） | 否 | 否 | 高（若接线） |
| E-10 | src/emotion/emotion_self_model_bridge.py:16-80 `merge/_update_existing` | model.emotional_self_understanding（confidence/stability/evidence 直接改写） | 仅 E-09 链（死链） | 是（直接改传入对象） | 否 | 否 | 否 | 高（若接线） |
| E-11 | src/emotion/emotion_dynamics_engine.py:104-114 `_push_transition` | 内存 _transition_history / _persistent_mood / _mood_streak | E-01 经 dynamics 包装（runtime_core.py:896 实例化，_emotion_enabled 默认 False） | 是（内存瞬态，不落盘） | — | — | 否 | 低 |

### 1.3 E-01 六条调用链明细

| 链 | 位置 | 门 | 审计 | 状态 |
|---|---|---|---|---|
| C1 主链前置 | orchestrator.py:2027-2048 `_process_emotion_pre`（detector→process_event，:2040；调用点 :1167） | ✅ can_modify_emotion（:2029，P2.1.3） | ❌ 无审计无事件 | **活跃**（B.1 EMO-01） |
| C2 runtime 主链 | runtime_core.py:5112-5157 `_stage_03_emotion_update`（detector→process_event，:5157；lifecycle_executor.py:83 舞台映射驱动） | ❌ 仅 `_control_blocked`（safe/maintenance 模式，:5115），**无身份门** | ❌ 无审计无事件 | **活跃**（B.1 EMO-02「审计黑洞」） |
| C3 legacy runtime 链 | runtime/adapters/impl/emotion_adapter_impl.py:95-133 `analyze`（Event→EmotionEvent→process_event，:125） | ❌ 无门 | ❌ 无审计 | legacy（B.1 注：可能已被 delegate 模式旁路） |
| C4 模块级 hooks | orchestrator_hooks.py:19-37/40-96 `_process_emotion_pre/post`（:30/:48 process_event） | ❌ 无门 | ❌ | **死代码**：EmotionEvent 构造传 `source=…, content=…, timestamp=…`，EmotionEvent 无 content/timestamp 字段 → TypeError 被 except 吞掉；且无生产调用方（orchestrator.py 仅 import apply_legacy_relationship_profile_delta） |
| C5 公开 API | runtime_core.py:3826-3857 `record_emotion_event`（→dynamics.process_event，:3847） | ❌ 无门 | 部分（notify_emotion_changed 域事件 :3848） | **零生产调用方**（API 预留） |
| C6 动力学委托 | emotion_dynamics_engine.py:30-57 `process_event`（委托 manager.process_event + 内存 transition） | 视调用方 | ❌ | 活跃（随 C2/runtime_core 场景） |

---

## 2. 调用链分析（Phase 2）

### 2.1 用户消息如何影响 emotion？

```
用户消息
  ↓ 主链：orchestrator.process() 前置 Step（orchestrator.py:1167）
P2.1.3 门（can_modify_emotion，2029）
  ↓
EmotionEventDetector.detect(user_message)   ← 纯关键词规则（mitigation/否定/负面/正面），无 LLM
  ↓
EmotionManager.process_event → EmotionEvaluator（规则表缩放）→ apply_delta → repository.save
  ↓ 后置 Step（orchestrator.py:1283）
_process_emotion_post：repo.filepath 现场改写 → per-user 保存 → 条件式
  audit（emotion.changed）+ EmotionChangedEvent（2095/2105）

次链：runtime_core.process() → LifecycleExecutor 17 阶段 Stage 3 EMOTION_UPDATE
  → _stage_03_emotion_update（无身份门、无审计；仅 safe/maintenance 阻断）
  → detector(ctx.user_message) → process_event
```

### 2.2 系统事件如何影响 emotion？

| 系统事件 | 是否影响 emotion | 证据 |
|---|---|---|
| memory event（记忆事件/回忆） | **无生产路径** | `record_emotion_event(memory_id=…)` API 存在但零调用；无 memory→EmotionEvent 发射 |
| growth event（成长事件） | 无 | growth pipeline 不发射 EmotionEvent |
| relationship event（关系事件） | 无 | relationship post-processing 不写 emotion |
| runtime Event（legacy 链） | 有 | EmotionAdapterImpl.analyze：`_DEFAULT_INTENSITY_MAP` 按 event.type 定强度（legacy 链） |
| 时间流逝 | 有 | EmotionManager.update() 衰减（E-02，仅 legacy 链调用；无 trace） |
| safe_mode / maintenance_mode | 阻断 | runtime_core.py:4892-4917 `_stage_00_control_check` → `ctx._control_blocked` → stage_03 跳过 |
| admin Live2D | 只读 | src/admin/core/l2d_adapter.py 消费 "achievement" 标签做表情（不写状态） |

### 2.3 专项问题回答

1. **LLM 输出直接修改 emotion？** —— **不存在。** src/response/engine.py、prompt_builder.py 中的
   emotion 仅是把 EmotionContext 注入 prompt（表达层，只读）。情绪事件检测为纯关键词规则
   （EmotionEventDetector），无 LLM 解析输出写情绪状态。
2. **handler 内部直写 emotion？** —— 存在：E-01（EmotionManager 内部 apply_delta+save 原语，无内部门）、
   E-03（orchestrator 直写 per-user 文件）、E-04（直接文件写 fallback）。
3. **无 evidence 的情绪永久化？** —— 部分存在：C1 pre 链 process_event(ev) 不传 memory_id → trace 无记忆关联；
   E-02 衰减无 trace；E-03 per-user 写入无 before/after 结构化审计（仅 dominant/intensity 变化才触发）。
   但当前生产代码无 "permanent" 语义——B.3 BoundaryCheck 已预留 `emotion.permanent=True → REJECT`
   规则（boundary_check.py:72-74），生产侧尚无对应概念。
4. **emotion 修改影响 personality？** —— 状态层**无**（无 EmotionChangedEvent → personality 的 handler）；
   表达层**有**（EmotionContext → prompt 注入，dominant/intensity 影响语气——合法，非状态变更）；
   唯一跨域状态路径是 E-09/E-10（emotion→self_model 信念），**全链死代码**（无实例化 + B.1 B14
   签名 bug：emotion_growth_service.py:94/97 `save(model)` vs SelfModelStore.save() 无参签名）。

---

## 3. 治理缺口分析（Phase 3）

### 3.1 B.1 已知问题的现状复核

| B.1 结论 | 位置 | 现状 | 判定 |
|---|---|---|---|
| B7 情绪写审计黑洞 + 不对称 | mutation_boundary_audit.md:111 | E-01 C2（stage_03 无门无审计）、E-01 C1（pre 有门无审计）、E-03（filepath 现场改写）全部**原样存在** | ✅ 仍存在 |
| B7 filepath 现场改写 | orchestrator.py:2074 `repo.filepath = per_user_file` | 未变；副作用：共享 repository 对象被临时改指向，后续任何 repo.save（如衰减）会写到最后一个用户的文件 | ✅ 仍存在 |
| B11 MutationJournal 零写入 | request_context.py:375 定义 | emotion 域无任何 ctx.mutations 写入；B.3 audit_writer 仅在请求经 MutationGateway 时才投影，而 emotion 从不进 Gateway | ✅ 仍存在 |
| B14 save(model) 签名 bug | emotion_growth_service.py:94/97 | 未变；该链 src 内仍无驱动方，暂未触发 | ✅ 仍存在 |

### 3.2 本次新增/深化发现

1. **权限门不对称（新）**：同一 EmotionManager.process_event 原语，orchestrator 链有
   P2.1.3 身份门，runtime Stage 3 链只有 `_control_blocked`（safe/maintenance 模式），
   **无 resolve_identity 门**。生产主链中 sandbox/未知身份经 runtime_core.process 路径
   仍可触发情绪状态变更。
2. **原语无内部门**：EmotionManager.process_event/update 内部没有任何门或审计，安全性
   完全依赖调用方自觉——六条调用链中三条无门（C2/C3/C4）。
3. **计数器死链**：`increment_analysis_counter` 零生产调用方 → analysis_counter 恒 0 →
   EmotionGrowthService.should_analyze() 恒 False → 即使实例化也永不分析（双死锁链）。
4. **E-03 filepath 改写绕封装**：绕过 EmotionManager 封装直调 repo.save，且改写的是
   共享实例的 filepath 属性——既是审计缺口，也是状态残留风险点。

### 3.3 A/B/C 分类

**A 类（符合治理链 / 治理侧就绪）：**
- EmotionRuntimeAdapter（Phase C.3，只读适配器，硬约束清单明确禁止 process_event/update/save/bind）
- EmotionContextProvider / EmotionDecay / EmotionState.apply_delta / EmotionEvaluator /
  EmotionEventDetector / EmotionPatternAnalyzer / EmotionBeliefExtractor —— 全部纯函数/只读
- admin/runtime_provider.py —— 只读桥，明确禁止清单（`Admin → EmotionManager() ❌`）
- orchestrator C1 链的 P2.1.3 门部分（门✅，审计缺口归入 B 类）
- B.3 治理骨架的 emotion 预留：MUTATION_TARGETS 六域含 emotion（mutation_contract.py）；
  DOMAIN_GATES emotion=can_modify_emotion（identity_check.py:41）；
  DOMAIN_NAMESPACES emotion=("emotion.",)（boundary_check.py:30）；
  `permanent=True → REJECT`（boundary_check.py:72-74）

**B 类（绕过 Proposal/Gateway 或审计缺口）：**
- E-01 C2：runtime_core stage_03 无门无审计（生产主链审计黑洞）
- E-01 C3：EmotionAdapterImpl.analyze 无门
- E-01 C1：orchestrator pre 链有门无审计
- E-02：decay 无门无审计无 trace
- E-03：filepath 现场改写（共享对象状态劫持）
- E-04：per-user 文件直接写 fallback
- E-09（legacy save 分支）：如接线即成 emotion→self_model 跨域直写

**C 类（死代码/遗留入口）：**
- E-01 C4：orchestrator_hooks 模块级 `_process_emotion_pre/post`（构造参数非法必抛 + 零调用方）
- E-01 C5：runtime_core.record_emotion_event（零调用方）
- E-06：increment_analysis_counter（零调用方）
- E-08：EmotionPatternRepository（零调用方）
- E-09/E-10：EmotionGrowthService + EmotionSelfModelBridge 全链（零实例化 + B14 签名 bug）

---

## 4. 设计建议（Phase 4，只写文档，供 B.7 实施）

### 4.1 目标路径

```
EmotionEvent（detector / adapters / 系统事件）
  ↓
EmotionEvaluator（现有规则引擎，纯函数，不变）
  ↓
EmotionMutationAdapter.build_request()      # 新增 src/emotion/mutation_adapter.py（B.7）
  ↓
MutationRequest                              # actor_identity="emotion_system"
  ↓                                          # target_domain="emotion"
MutationGateway（B.3 五道原样复用）           # target_path="emotion.state.<dim>"
  ↓                                          # proposed_change/evidence/context_snapshot/risk_level
ACCEPT    → EmotionApplyAdapter（现有 apply_delta + repository.save 作为执行件，仅此支改状态）
REJECT    → 不改变任何状态
NEED_REVIEW → proposal 入库待审（pending）
DEFER     → 延迟队列
  ↓
Audit（route 信封 → audit_writer → MutationJournal 投影）
```

### 4.2 允许自动变化（免 Proposal，但必须可审计）

- **短期 mood / transient state**：单事件 delta ≤ MAX_SINGLE_EVENT_DELTA（沿用 B.3
  EvidenceCheck 阈值）；detector 规则表已内置强度上限
- **时间衰减 decay**：基线回归天然无证据 → 免 Proposal，但补 trace + audit 记录
- **计数器 bookkeeping**（E-06）
- **轨迹/模式 append**（E-07/E-08，append-only 证据流）

### 4.3 需要治理的变化

- **长期 emotion pattern 固化**：permanent=True（BoundaryCheck 已 REJECT，未来 pattern
  写入须走 Proposal）
- **emotion → self_model 信念**（E-09/E-10）：必须经 self_model 域 MutationRequest 或
  Phase 6.2 SelfModelAdapter，禁止 legacy `self_model_store.save(model)` 直写
- **跨域情绪联动**（如 relationship trust 变化引发情绪基线变化）：跨域事件 → emotion
  域 MutationRequest，禁止跨域直写
- **高幅度单事件变更**（|delta| 超阈值）→ NEED_REVIEW

### 4.4 推荐 B.7 实施顺序

1. **修 E-03 filepath 现场改写**：per-user 专用 EmotionRepository 实例注入，不碰共享
   repo.filepath（纯重构，低风险，独立可合并）
2. **补 C2 门 + 审计**：`_stage_03_emotion_update` 加
   `can_modify_emotion(resolve_identity(ctx.user_id))` + notify_emotion_changed/audit
   （一行级改动，与 orchestrator 链对称，填审计黑洞）
3. **新增 src/emotion/mutation_adapter.py** + `emotion_mutation_gateway_enabled=False`
   （与 B.4/B.5 同模式：迁移层 + 默认关闭）；IdentityCheck `PRIVILEGED_ACTORS` 追加
   `"emotion_system"`（同 B.5 growth_system 白名单扩展，requires_audit=True 不变）
4. **EmotionManager.process_event 加 flag 门控网关分支**：ACCEPT → apply_route 进现有
   apply_delta+save 执行件；REJECT 不改状态；NEED_REVIEW 存提案；DEFER 进队列
5. **接线三链**：orchestrator pre/post、runtime stage_03、legacy adapter analyze 统一
   走同一 adapter（flag 开启时）
6. **MutationJournal 接线**：ctx.mutations append（B.3 audit_writer 已有投影能力，B11 收口）
7. **C 类清理**：orchestrator_hooks 模块级 emotion 函数删除/退役标记；record_emotion_event
   标记 deprecated 或接线；EmotionGrowthService 修复 B14（save 签名）且仅经 SelfModelAdapter
   写入，或整体退役；EmotionPatternRepository 接线或下线

### 4.5 与 B.3/B.4/B.5 Gateway 的衔接方案

- **复用 MutationGateway 原样**：五道全注入式；emotion 域已在 B.3 骨架中——
  MUTATION_TARGETS 六域含 emotion；DOMAIN_GATES emotion=can_modify_emotion 已就位；
  BoundaryCheck emotion 命名空间 + permanent REJECT 已就位——**B.3 无需任何改动**
- **MutationRequest 契约不变**（9 字段）；emotion 证据来源：detector 命中关键词
  （user_statement 类）+ EmotionalTrace.trace_id + memory_id（未来接线）
- **actor_identity="emotion_system"**：需 B.7 时追加进 PRIVILEGED_ACTORS（与 B.5
  growth_system 完全相同的处理模式）
- **与 B.4 互补**：emotion 表达（prompt 注入）不触发人格变更，维持现状；E-09 若接线
  必须走 self_model 域请求（B.4 人格门已覆盖 self_model）
- **与 B.5 互补**：growth 事件当前不影响 emotion（无路径）；未来"成长成就 → 情绪"联动
  须经同一 emotion adapter，禁止 growth 直写 emotion 状态
- **风险等级映射**：detector 规则事件 → low；衰减 → low（自动白名单）；跨域联动/pattern
  固化 → medium/high

---

## 5. 验证（Phase 5）

- src/ 无修改 ✅（`git status --short` 对比基线：src/emotion/ 零出现；本次审计全程只读）
- data/ 无修改 ✅（`git status --short -- data/` 为空）
- tests/ 无修改 ✅（未新增/未修改任何测试文件）
- 唯一产出：docs/governance/emotion_mutation_boundary_audit.md（本文件）✅

---

## 6. 结论摘要

1. **当前 Emotion 写入口数量**：11 个登记条目（E-01..E-11），其中状态写入口 6 个
   （E-01/E-02/E-03/E-04/E-05/E-06），追加流 2 个（E-07/E-08），跨域 2 个（E-09/E-10），
   内存瞬态 1 个（E-11）；E-01 展开为 6 条调用链（C1 活跃有门无审计 / C2 活跃无门无审计 /
   C3 legacy 无门 / C4 死 / C5 零调用方 / C6 委托）。
2. **高风险绕过点**：E-01 C2（runtime Stage 3 审计黑洞，无身份门）；E-03（filepath
   现场改写共享 repository）；E-09/E-10（emotion→self_model 跨域直写，死链待清理）。
3. **B 类债务列表**：E-01 C1/C2/C3、E-02、E-03、E-04、E-09 legacy 分支（详见 §3.3）。
4. **推荐 B.7 实施顺序**：①修 filepath 改写 → ②补 C2 门+审计 → ③emotion mutation
   adapter（flag 默认关）→ ④process_event 网关分支 → ⑤三链接线 → ⑥MutationJournal 接线 →
   ⑦C 类清理（详见 §4.4）。
5. **与 B.3/B.4/B.5 衔接**：B.3 骨架已含 emotion 域全部预留（零改动复用）；
   actor 白名单按 B.5 growth_system 模式扩展 "emotion_system"；B.4 人格门覆盖
   emotion→self_model 跨域；B.5 禁止 growth 直写 emotion（详见 §4.5）。
