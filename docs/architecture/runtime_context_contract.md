# P2.3-A.1 RuntimeContext 统一接口契约（runtime_context_contract）

> 状态：**只读设计文档 + 接口草案**——未修改 src/data/tests，未提交代码，未接线
> 日期：2026-08-19　分支：develop/v1.1　HEAD：225d040
> 前置：P2.3 勘察报告 + P2.3-D 五份台账（wiring_status / runtime_paths / event_bus_inventory / unwired_components / design_committee_future）
> 原则：不重写 RuntimeCore、不迁移 EventBus、不接线、只建立契约

---

## 1. 当前问题（Phase 1 分析结论）

### 1.1 不止两个：RuntimeContext 家族全景

经全仓库核查，名为（或近似）"RuntimeContext" 的类共有 **7 个**，其中 **3 个同时活跃在生产路径上**：

| # | 类 | 文件 | 本质 | 生产路径 | 状态 |
|---|---|---|---|---|---|
| 1 | `RuntimeContext`（组装器） | src/runtime/runtime_context.py（185 行） | 文件系统 agreement 加载器；`assemble_context()` 返回 **plain dict**（14 键） | orchestrator 兜底路径（orchestrator.py:27/344/1113/2376） | ACTIVE（legacy 兼容层） |
| 2 | `RuntimeContext`（生命周期快照 v1.0） | src/runtime/lifecycle_context.py（416 行） | **frozen** dataclass：session/lifecycle/state/时间/inputs/outputs/error/metadata/schema_version | RuntimePipeline 外层（runtime_pipeline.py:37/624/703）+ context_storage 持久化 | ACTIVE（主链外层） |
| 3 | `RuntimeContext`（思维循环快照 v1.0） | src/runtime/context/runtime_context.py（115 行） | **mutable** dataclass：session/user_input/timestamp/memory_context/emotion_state/personality_snapshot/growth_proposals/identity_context_text | Legacy RuntimeCore 17 阶段内层（runtime_core.py:35；lifecycle_executor.py:51/64/194） | ACTIVE（主链内层） |
| 4 | `LifecycleContext`（任务执行上下文） | src/runtime/lifecycle/lifecycle_context.py（420 行） | clock/emitter/quota/cancel 任务执行环境 | lifecycle_manager（模块启停默认关闭） | UNWIRED |
| 5 | `PersonalityRuntimeContext` | src/runtime/personality_context.py（47 行） | 人格绑定上下文 | canonical runtime.py:2335 | UNWIRED（生产） |
| 6 | `SelfModelRuntimeContext` | src/personality/self_model_runtime_context.py（57 行） | SelfModel 上下文提供者内部 | 仅 personality/ 包内（self_model_context_provider.py:217） | 内部用 |
| 7 | `PolicyContext` | src/runtime/policy/policy_context.py（80 行） | 策略/配额上下文 | canonical runtime.py + policy 层 | UNWIRED（生产） |

另有 `RuntimeControlContext`（src/runtime/context/control_context.py，Phase C.10.6）与测试用 `MockRuntimeContext`（src/admin/runtime_selfmodel_bridge_test.py:71）。

**生产请求的三段上下文流转（本次核实）**：

```
RuntimePipeline.run()                                   [外层 frozen #2]
  ├─ RuntimeContext(session_id, lifecycle_id, inputs, metadata)   ← pipeline:703
  ├─ Runtime.process(event, ctx) —— Legacy RuntimeCore
  │    └─ _normalize_runtime_ctx(ctx)  [runtime_core.py:76]  ← Phase 4.0.4-Pre 归一化 shim
  │         any → mutable #3（拷贝同名字段，inputs.user_input/metadata/started_at/user_id 适配）
  │         → lifecycle_executor.execute（runtime_core.py:4823 单点调度 17 阶段）
  ├─ fallback → Orchestrator.process()
  │    └─ self.runtime_context.assemble_context(...)  [orchestrator.py:344/1113]
  │         → dict（14 键）→ 15 步硬编码从 dict 取键
  ├─ persistence_hook.persist(context)  [frozen #2 → data/runtime_context]
  └─ event_sink.emit
```

### 1.2 两个主变体对比表（任务要求）

| 维度 | runtime_context.py::RuntimeContext（组装器） | lifecycle_context.py::RuntimeContext（快照） |
|---|---|---|
| 类定义 | 普通 class（可变），仅 2 个字段（repo_root/agreements_dir） | `@dataclass(frozen=True)`，10 个字段 |
| 字段 | 无请求字段；`assemble_context()` 返回 dict：system_messages/prompt_blocks/conversation/self_model/memory_summary/relationship/trace/personality_context/emotion_manager/emotion_context/relationship_profile(别名)/screen_context/user_context/token_context | session_id/lifecycle_id/state/started_at/ended_at/inputs/outputs/error/metadata/schema_version |
| 生命周期 | orchestrator 实例属性（进程级，orchestrator.py:344）；每请求调 assemble 返回全新 dict | 每请求新建（pipeline:703）；`with_update()` 派生新实例；start/mark_success/failed/cancelled |
| 创建位置 | orchestrator.py:344；yuyi_runtime_integration.py:36（**零使用方，孤儿**） | runtime_pipeline.py:703；context_storage 反序列化 |
| 使用方 | orchestrator.py（1113/2376 两处 assemble） | pipeline、context_storage、persistence_hook、event_adapter/event_publisher、lifecycle_bridge |
| 生产依赖 | **是**——orchestrator 15 步从返回 dict 取键（含假键） | **是**——pipeline 主链 + 持久化 data/runtime_context |
| 身份信息 | ❌ 无 user/identity | ❌ 无（metadata 自由键） |
| request_id/trace_id | ❌ 无 | ⚠️ 仅 metadata 自由键（无强契约） |
| 序列化 | ❌ 不可序列化（含业务引用） | ✅ to_dict/from_dict + schema_version |
| 业务快照 | ⚠️ 半成品：prompt 文本 + 引用透传（emotion_manager 实例直接入 dict） | ❌ 无（inputs/outputs 盲盒） |
| 可派生/不可变 | ❌ dict 可任意改 | ✅ frozen + 派生 |
| 与业务模块耦合 | 依赖文件系统 agreements；dict 里直接装 EmotionManager 实例 | 零业务依赖（stdlib only，文件头声明） |

### 1.3 请求状态散落三处

1. Orchestrator 实例属性（`target_user_id`/`history`/`_current_snapshot`/`relationship_profile` 等）；
2. `assembled_context` dict（每次重组，14 键）；
3. pipeline 的 frozen RuntimeContext（inputs/outputs/metadata）。

三者互不投影、互不校验，同一请求在三处存在三份不一致的"身份/时间/状态"记录。

### 1.4 假连接（已在 P2.3-D 实锤）

- `relationship_repo`：只有读（orchestrator.py:2133）、全仓库无写入 → Step 12 关系 post 断链（2133-2136 early-return，2139-2164 段不可达）。
- `on_emotion_change`：只有读（orchestrator.py:2115-2118）、全仓库无写入 → 回调永不触发。
- 根因：组装器返回 dict 属于"约定俗成"接口，键的存在与否没有契约校验，断链静默存在。

### 1.5 已存在的归一化先例（重要）

Legacy RuntimeCore 已有 `_normalize_runtime_ctx`（runtime_core.py:76，Phase 4.0.4-Pre 类型归一化）：把外部任意 ctx（frozen #2 / 旧组装器）转换为内部 mutable #3 并拷贝同名字段。**这证明"upcast adapter"路线在本代码库已有成熟先例**，本契约的迁移策略正是它的泛化。

---

## 2. 唯一 RuntimeContext 目标设计

### 2.1 目标形态

**单一不可变请求上下文**：`RuntimeContext`（frozen dataclass，`schema_version="2.0"`）。

- 基底：沿用 lifecycle_context v1.0 的身份骨架（session/lifecycle/state/时间/inputs/outputs/error/metadata）——它是三个变体中唯一具备"不可变 + 序列化 + 状态机"的。
- 吸收：context/runtime_context v1.0 的模块快照字段（memory/emotion/personality/growth）。
- 新增：Identity / Perception / Mutation Journal / Audit Trail 四层。
- 导出：`legacy_view()` 方法投影出组装器 dict 的 14 键（兼容 orchestrator 15 步零修改读取）。

### 2.2 设计原则（本契约的硬规则）

1. **不可变**：frozen dataclass；变更一律 `with_update()` 派生；identity 与标识字段（session_id/lifecycle_id/request_id/started_at/schema_version/identity）禁止修改（沿用 v1.0 规则并扩展）。
2. **零业务依赖**：契约层只允许 stdlib 类型 + 快照 dict；禁止 import memory/emotion/growth/personality/relationship/llm 模块（沿用 lifecycle_context 文件头声明与 context v1.0 的"Runtime 不知道业务类型"原则）。
3. **可序列化**：to_dict/from_dict 双向；from_dict 对 v1.x 快照做缺省回填（先例：context v1.0 from_dict 的 schema 回退逻辑）。
4. **不持有可写句柄**：不携带 memory_store/emotion repo/relationship repo/personality writer 任何引用；**读写分离——读走快照，写走 proposal/event/request**。
5. **不持有总线**：不持有 EventBus 实例；事件关联只通过 envelope 帮助器输出数据（关系见 §5）。
6. **契约先行，不接线**：本阶段只冻结字段与语义；任何实现动作属于后续批准阶段。

### 2.3 总体结构草图（接口草案，非实现）

```python
@dataclass(frozen=True)
class RuntimeContext:                     # schema_version = "2.0"
    # ── 标识基底（继承 lifecycle_context v1.0，字段名不变）──
    session_id: str
    lifecycle_id: str
    state: str                            # pending/running/success/failed/cancelled
    started_at: str                       # ISO 8601 UTC
    ended_at: Optional[str]
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    error: Optional[str]
    metadata: Dict[str, Any]
    schema_version: str

    # ── 新增七层 ──
    identity: IdentitySnapshot            # §3.1
    request: RequestMeta                  # §3.2
    perception: Dict[str, PerceptionSnapshot]   # §3.3
    cognitive: CognitiveSnapshot          # §3.4
    state_snapshots: StateSnapshots       # §3.5（只读）
    mutations: MutationJournal            # §3.6（proposal/event/request 记录，不含写句柄）
    audit: AuditTrail                     # §3.7

    def with_update(self, **kw) -> "RuntimeContext": ...   # 派生
    def snapshot(self) -> Dict[str, Any]: ...              # 冻结视图（委员会读取面）
    def legacy_view(self) -> Dict[str, Any]: ...           # 投影 14 键 dict（orchestrator 兼容）
    def event_envelope(self, event_type: str, payload: Dict) -> Dict: ...  # 注入 request_id/trace_id
    def to_dict(self) / from_dict(cls, d) -> ...
```

---

## 3. 字段契约（七大部分）

### 3.1 Identity

| 字段 | 类型 | 语义 | 来源 |
|---|---|---|---|
| `identity.id` | str | 最终内部身份 ID（沙盒统一 `_unknown_sender`） | security/identity.py（Identity.id） |
| `identity.source` | str | qq / api / system / unknown | Identity.source |
| `identity.verified` | bool | 是否通过格式校验 | Identity.verified |
| `identity.permission` | str | user / sandbox / admin（预留） | Identity.permission |
| `identity.is_sandbox` | bool | 派生：permission=="sandbox" | Identity.is_sandbox |

规则：**fail-closed**——无法解析必须落沙盒；契约层只做快照（从 `resolve_identity` 结果拷贝四个冻结字段），**不重新解析、不猜测、不修正**。Identity 在请求开始时建立后不可变更（纳入 with_update 禁改集）。

### 3.2 Request Metadata

| 字段 | 类型 | 语义 | 现状对照 |
|---|---|---|---|
| `request.request_id` | str | 请求唯一 ID（入口生成） | ❌ 缺失（无契约） |
| `request.trace_id` | str | 追踪 ID（贯穿 trace/audit/event 三系统） | ❌ 缺失（仅 lifecycle_id 近似） |
| `request.timestamp` | str | 请求到达时间 ISO 8601 UTC | 散落 |
| `request.channel` | str | 入口通道：chat / initiative / cli / admin（枚举） | ❌ 缺失（/initiative 与 main.py 无法区分） |
| `request.entry` | str | 入口路径标识（pipeline / orchestrator / controller） | pipeline metadata 有近似 |

规则：request_id 与 trace_id 由入口装配器**一次性生成**，写入 metadata 并注入所有事件/审计记录（见 §3.7）；两者不可变。

### 3.3 Perception Context（感知上下文）

| 字段 | 类型 | 语义 | 现状对照 |
|---|---|---|---|
| `perception` | Dict[str, PerceptionSnapshot] | 按模态注册：`"screen"` / `"voice"` / `"camera"` / 未来任意 key | screen_context 已有先例（orchestrator Step 3.5 硬编码） |
| `perception[k].modality` | str | 模态名 | — |
| `perception[k].captured_at` | str | 采集时间 | — |
| `perception[k].data` | Dict | 模态载荷（屏幕描述文本/语音转写/视觉描述） | — |
| `perception[k].source` | str | 采集来源（adapter 标识） | — |

规则：**开放扩展槽**——新模态只新增 key 与 adapter，不改契约本体。本阶段不定义任何 adapter 实现；未来视觉/身体模块通过"注册 → 采集 → 写 perception 快照"接入（见 §6）。

### 3.4 Cognitive Context（认知上下文）

| 字段 | 类型 | 语义 | 现状对照 |
|---|---|---|---|
| `cognitive.memory_refs` | List[str] | 本次检索命中的记忆 ID 列表（引用，不复制内容） | Step 2 检索结果散落 |
| `cognitive.retrieved_knowledge` | Dict | 检索到的知识/记忆摘要（供 LLM 与委员会引用） | memory_summary 近似 |
| `cognitive.reasoning_context` | Dict | 推理中间态（思考痕迹、判断依据） | 散落/缺失 |

规则：只存**引用与摘要**，不存完整记忆体；引用列表是"人格变化可解释"（P2.3 目标 B）的证据链起点。

### 3.5 Internal State Access（内部状态访问——只读快照）

| 字段 | 类型 | 语义 | 现状对照 |
|---|---|---|---|
| `state_snapshots.emotion` | Dict | {dominant, intensity, updated_at} 只读拷贝 | 组装器直接装 EmotionManager **实例**（越界） |
| `state_snapshots.relationship` | Dict | 关系摘要只读拷贝 | relationship/relationship_profile 双键混乱 |
| `state_snapshots.personality` | Dict | 人格快照只读拷贝 | personality_context |
| `state_snapshots.growth` | Dict | 增长快照 + 本请求相关 proposal 引用 | growth_proposals（context v1.0） |
| `state_snapshots.self_model` | Dict | SelfModel 快照只读拷贝 | self_model |

**硬规则**：
- 快照一律**拷贝**（深拷贝标量 + 浅拷贝子 dict），禁止把 manager/repo/store 实例放进契约。
- 快照只是"读取面"；任何写回都必须走 §3.6 通道。
- 对照治理链：`state_snapshots.self_model` 的写回 = `SelfModelStore.apply_change_proposal`（唯一合法写入口），契约不新增直写路径。

### 3.6 Mutation Interfaces（变更接口——禁止直写）

**契约禁止暴露**（作为字段、方法或注入对象出现）：`memory_store.add`、`emotion.save`、`relationship.save`、`personality.write`、`vector_memory.add_memory` 及任何 repo/store 写句柄。

**只允许三种形式**：

| 形式 | 结构 | 走向 |
|---|---|---|
| MutationRequest（申请） | `{mutation_id, target(五模块枚举), action, payload, evidence_refs, requester_identity}` | 进入审批队列（对齐 approval_manager / SelfModelApprovalQueue 语义），**不直接生效** |
| Event（事件） | 经 `event_envelope()` 注入 request_id/trace_id 后发布 | 事件总线（总线收敛见 P2.3-A 后续批次） |
| Proposal（提案） | 复用 GrowthProposal / governance 提案载体 | 治理链：Review → Approval → Apply → Audit |

**Apply 前置条件（契约级不变式）**：
1. 权限门放行（`identity.permission=="user"` 或 admin 通道；对齐 security/permission.py 五扇门语义）；
2. 治理决策允许（AUTO_APPLY 或已批准 Proposal——对齐 self_model_governance.py 硬契约，扩展至五模块）；
3. Apply 由 RuntimeCore 内部的 adapter 执行（写句柄只存在于 adapter 层，不出现在 context）；
4. Apply 前后写审计（§3.7）。

**假键处置（契约裁决）**：`relationship_repo`、`on_emotion_change` 两个键**正式从契约中删除**——仓库访问改由 adapter 接口在 RuntimeCore 内部完成，回调改由事件订阅替代。组装器 dict 中的这两个键随之退役（实施阶段处理，本阶段只裁决契约语义）。

### 3.7 Audit Context（审计上下文）

**要求：一次请求必须能重建完整链**：

```
request(received) → event(message.received) → mutation proposal → apply → audit(before/after)
```

| 字段 | 类型 | 语义 |
|---|---|---|
| `audit.chain` | List[AuditLink] | 本请求的审计链节点（每节点 {step, at, event_ref, mutation_ref, audit_ref}） |
| `audit.audit_refs` | List[str] | 关联的 record_audit_log 记录 ID |
| `audit.request_id` / `audit.trace_id` | str | 与 request 层一致的关联键（冗余存放便于落盘） |

**现状缺口（契约要求补，实施在后续阶段）**：audit_logs.jsonl 无 trace_id（correlation_id 实际为空）；Legacy RuntimeCore 17 阶段主循环不写审计；trace 只有 pipeline 3 个粗粒度阶段。契约层面的承诺：所有审计写入必须携带 `request_id + trace_id`（向后兼容追加字段，不破坏现有 286 条记录与解析方）。

---

## 4. 生命周期

1. **创建**：每个请求一个实例；由入口装配器（pipeline:703 现构造点）在身份解析**之后**构造（identity/request 在构造时一次填定）。
2. **状态机**：沿用 v1.0 五态（pending→running→success/failed/cancelled）；终态自动填 ended_at。
3. **派生**：`with_update()` 派生新实例；禁改集 = session_id/lifecycle_id/started_at/schema_version/inputs/**identity**/**request**。
4. **阶段挂载**：17 阶段 / 15 步各自**读快照、写 mutations journal**，不写业务状态。
5. **序列化**：to_dict/from_dict；v1.x 旧快照 from_dict 回填缺省（schema 演进兼容）。
6. **持久化**：沿用 context_storage → data/runtime_context（现有机制不动）；留存策略（当前 147 个快照）另行裁决，不属本契约。
7. **终态快照**：`snapshot()` 输出冻结视图，是委员会/审计/追踪的统一读取面。

---

## 5. 与 EventBus / Audit / Governance 的关系

| 系统 | 关系约定 |
|---|---|
| EventBus | 契约**不持有**总线引用（防耦合，与 lifecycle_context 的"只通过 EventEmitter 通信"思想一致）；context 只产出 `event_envelope()` 数据（带 request_id/trace_id）供任何总线发布。总线收敛与 register_builtin_handlers 死接线处置属 P2.3-A 后续批次，本契约不迁移总线 |
| Audit | `audit.chain` 是审计链的**关联键载体**；record_audit_log 未来扩展 trace_id/request_id 参数（追加、向后兼容）。本契约不修改 audit 写入逻辑 |
| Governance | `mutations` journal 是治理决策的**输入队列**；"无第三条直写路径"从 self_model 扩展到五模块；权限门从 `identity` 层读取（can_modify_* 继续是纯函数，不在 context 内做权限判断——context 只携带 Identity 快照） |

---

## 6. 未来视觉 / 身体 / 委员会扩展接口

### 6.1 视觉 / 语音 / 身体模块

- 接入点 = `perception` 开放扩展槽：新模态只需 ① 实现采集 adapter（输出 PerceptionSnapshot）② 在装配器注册表登记一个 key。契约本体零改动。
- 现状先例：屏幕模块（Step 3.5 硬编码 `get_screen_description`）是"硬插核心"的反面教材；契约的目标是把它降级为 perception["screen"] 的一个普通注册项（实施阶段改，本契约只定义挂接面）。
- 约束：感知数据只读进入 context；传感器数据要触发人格变化仍必须走 §3.6 的 proposal 通道。

### 6.2 设计委员会（与 design_committee_future.md 对齐）

- **读取面**：委员会输入 `RuntimeContext Snapshot` = 本契约的 `snapshot()` 输出（identity + cognitive.memory_refs + state_snapshots + audit.chain）。
- **其余输入**：GrowthProposal、Evidence（沿用 design_committee_future.md §2.1）。
- **输出**：APPROVE / REJECT / REQUEST_MORE_EVIDENCE（ReviewOpinion + CommitteeDecision）。
- **禁止**：委员会不得直接修改人格/记忆/关系——其 APPROVE 只把 `mutations` 中的对应项推进到 Apply 环节，Apply 仍由 adapter 经权限门 + 审计执行。
- 两文档关系：design_committee_future.md 定义委员会本体接口；本契约定义委员会读写的**数据面**（快照与变更通道）。

---

## 7. 迁移策略（Phase 3 输出）

### A. 旧 RuntimeContext 保留方式（不删除）

| 旧变体 | 保留方式 | 新状态 |
|---|---|---|
| runtime_context.py 组装器（#1） | 保留为 orchestrator 兼容层；其 14 键 dict 由 v2 `legacy_view()` 投影供给 | COMPATIBILITY（台账同步标记） |
| context/runtime_context.py v1.0（#3） | Legacy RuntimeCore 内部继续使用（`_normalize_runtime_ctx` 保留）；作为 v2 state_snapshots 的字段来源映射表 | LEGACY（内部循环用） |
| lifecycle_context.py v1.0（#2） | 作为 v2 的**基底子集**保留；v2 是其超集，旧快照可从 v2 投影（字段名直通） | COMPATIBILITY → 逐步替换 |
| LifecycleContext D1 / PersonalityRuntimeContext / SelfModelRuntimeContext / PolicyContext | 不动（台账已登记 UNWIRED / 内部用） | 维持现状 |

### B. 新 RuntimeContext 引入方式

1. 本阶段：只产出本契约文档（**唯一产出**）。
2. 实施阶段（另行批准）：新增单一模块（建议 `src/runtime/request_context.py`，schema_version="2.0"），**不改动**三个旧文件。
3. 接入顺序（每步独立提交 + 独立回归）：
   - 第 1 步：pipeline 构造点（唯一，pipeline:703）改用 v2（基底字段直通，风险最低）；
   - 第 2 步：Legacy RuntimeCore 的 `_normalize_runtime_ctx` 扩展为 upcast（v2→#3 字段映射），17 阶段零改动；
   - 第 3 步：orchestrator 的 `assemble_context` 调用点改为消费 `legacy_view()`，15 步零改动；
   - 第 4 步：假键 removal（relationship_repo/on_emotion_change）随 §3.6 裁决执行。

### C. 兼容 adapter 方案

| Adapter | 方向 | 映射要点 |
|---|---|---|
| upcast_adapter | v1.x → v2 | lifecycle v1.0 身份字段直通；context v1.0 业务快照 → state_snapshots（memory_context→cognitive.retrieved_knowledge 等）；组装 dict 14 键 → legacy_view 反投影 |
| downcast / legacy_view | v2 → orchestrator dict | 14 键投影（含 system_messages/prompt_blocks/trace 等），orchestrator 15 步**零修改**读取 |
| normalizer | any → #3 | 泛化 runtime_core.py:76 `_normalize_runtime_ctx`（Phase 4.0.4-Pre 已证明可行，作为参考实现） |

### D. 未来 RuntimeCore 切换时的影响范围

- canonical runtime.py 已使用 context v1.0（#3）+ PolicyContext；v2 的 state_snapshots 命名与 #3 字段对齐后，切换成本集中在 **两处**：`_normalize_runtime_ctx`（归一化单点）与 lifecycle_executor 的 ctx 传入点（runtime_core.py:4823 单点调度）。
- 契约先行使切换从"上下文重构"降级为"adapter 替换"；双 RuntimeCore 并存期间的上下文互认由 upcast/downcast adapter 保证。
- **本契约不推进切换**；切换独立立项（P2.3 勘察报告 §7 已列为高风险项）。

---

## 8. 不属于本阶段范围的内容

1. **不写任何代码**：契约以本文档形式存在，不创建 src 文件。
2. 不修改三个旧 RuntimeContext 文件、不迁移 EventBus、不重写 RuntimeCore。
3. 不接线设计委员会、不修改权限系统（五扇门保持 P2.1.3 现状）。
4. 不修复假键（relationship_repo/on_emotion_change 的删除是实施阶段动作，本契约只裁决语义）。
5. 不动 data/、不迁移数据、不改变持久化目录与留存策略。
6. 审计字段扩展（trace_id 落盘）、总线收敛、RuntimeCore 二选一——均为后续已审批批次。
7. 不提交代码。

---

## 附录 A：核实依据（本次勘察行号清单）

| 事实 | 证据 |
|---|---|
| 组装器类与 14 键 dict | src/runtime/runtime_context.py:6/52-179 |
| 组装器生产使用 | orchestrator.py:27/344/1113/2376；yuyi_runtime_integration.py:36（该文件**零使用方**） |
| frozen 快照 v1.0 | src/runtime/lifecycle_context.py:94-360（字段/start/with_update/序列化） |
| pipeline 构造 | src/runtime/runtime_pipeline.py:624-640/703-712 |
| mutable 循环快照 v1.0 | src/runtime/context/runtime_context.py:46-114 |
| Legacy Core 内层使用 + 归一化 shim | src/runtime/runtime_core.py:35/76-105；lifecycle_executor.py:51/64/194；runtime_core.py:4823 |
| canonical 使用 | src/runtime/runtime.py:54/2335/2453-2500 |
| 家族其余成员 | lifecycle/lifecycle_context.py:140；personality_context.py:47；self_model_runtime_context.py:57；policy/policy_context.py:80；context/control_context.py（包 __init__ 导出） |
| 假键 | orchestrator.py:2115-2118/2133-2136 |
| 身份/权限契约 | src/security/identity.py:37/40/66-90/188；src/security/permission.py:40-60/93 |
| 治理硬契约 | src/personality/self_model_governance.py:1-45/185-240 |
| 审计现状 | src/audit/record.py:48；audit_logs.jsonl 286 条、无 trace_id |

## 附录 B：与既有台账的联动

- 实施本契约前，须先更新 wiring_status.md（三个 RuntimeContext 行的状态变更）与 unwired_components.md（yuyi_runtime_integration 新登记为孤儿）。
- 本契约与 design_committee_future.md 共同构成委员会的两份前置文档（本体接口 + 数据面）。
