# P2.3-A.2.5 RuntimeContext v2 接入前验证（runtime_context_integration_readiness）

> 状态：**接入前验证文档**——未切换任何生产入口，未修改生产逻辑，未修改 data
> 日期：2026-08-20　分支：develop/v1.1　HEAD：225d040
> 前置：P2.3-A.1（契约）/ A.1.5（冻结 R1-R5）/ A.2（v2 本体 + adapter 实现）
> 结论：**v2 满足 Pipeline 接入要求，准入下一阶段（P2.3-A.3 构造点切换）；**
> **但步骤 1 存在 3 处 isinstance 硬门（P1-1/P1-2），必须纳入同一提交适配，禁止半接入。**

---

## 1. 当前 Context 分布

| # | 类 | 文件 | 状态（本任务核实） |
|---|---|---|---|
| 1 | `RuntimeContext` 组装器（14 键 dict） | src/runtime/runtime_context.py | ACTIVE（orchestrator 兼容层） |
| 2 | `RuntimeContext` frozen v1.0 | src/runtime/lifecycle_context.py | ACTIVE（pipeline 主链外层，唯一构造点 :703） |
| 3 | `RuntimeContext` mutable v1.0 | src/runtime/context/runtime_context.py | ACTIVE（Legacy RuntimeCore 17 阶段内层） |
| 4-7 | LifecycleContext D1 / Personality / SelfModel / Policy | 各模块 | UNWIRED / 内部用（维持现状） |
| **8** | **`RuntimeContext` v2.0** | **src/runtime/request_context.py** | **READY（本任务验证通过，未接入）** |
| **9** | **context_adapter（4 函数 + ContextAdapterError）** | **src/runtime/adapters/context_adapter.py** | **READY（未接入）** |

生产三段流转（未变）：

```
RuntimePipeline.run()                                   [frozen #2 v1.0]
  ├─ 构造 :703（唯一点）→ with_update(RUNNING) :716
  ├─ Runtime.process(event, ctx) → runtime_core.py:4787 _normalize_runtime_ctx
  │    → lifecycle_executor.execute（17 阶段, mutable #3）
  ├─ fallback → Orchestrator.process()（assemble :1113 / :2376 → 14 键 dict）
  ├─ persistence_hook.persist(frozen #2) → data/runtime_context
  └─ event_sink.emit
```

---

## 2. 生产接入点（本次全量静态扫描结果）

### 2.1 RuntimePipeline（frozen #2 消费者）

- **唯一构造点**：runtime_pipeline.py:703（grep 证实全仓库仅此一处生产构造）。
- 生命周期：构造(pending) → :716 running → `process_fn(event, context)`（:1150-1168）
  → 提取 `ctx.finalized_reply`/`_final_reply` → `_build_outputs`（outputs 已含
  reply/orchestrator/reply_source，与 R2 兼容）→ 终态 → persist → emit。
- **现存静默无效写**：pipeline:1160 `context._runtime_process_ctx = ctx` 对 frozen
  dataclass 必然抛 FrozenInstanceError 被 except 吞掉——现状已失效，切换 v2 后无回归。

### 2.2 Legacy RuntimeCore（mutable #3）

- **归一化单点**：runtime_core.py:4787 `ctx = _normalize_runtime_ctx(ctx)`；入口
  :4817 `ctx = ctx or RuntimeContext()`。
- normalize 拷贝清单：同名字段 10 项 + `inputs.user_input/content → user_input`、
  `inputs.user_id → _ctx_user_id`、`inputs.recent_history → ctx.history + _recent_history`
  （V1.1.1 记忆查询融合依赖）、`metadata.user_id` 兜底、`started_at → timestamp`。
- **隐藏依赖（新发现）**：lifecycle_executor.py:61 `_normalize_mutable_ctx` 是
  **第二份同语义归一化器**（调用点 :174），与 runtime_core 版本双实现并存、
  有漂移风险；executor 另对 #3 写动态属性（`ctx.current_stage` / `ctx._phase_errors`），
  **v2 frozen 不可直接进入 executor**——架构结论：v2 只做 pipeline 外层，
  RuntimeCore 内部继续用 #3，降级发生在 4787。
- 适配可行性：normalize 可以泛化为「v2 → #3 downcast」（读 v2.inputs/
  metadata 即得全部所需字段），无需触碰 17 阶段。

### 2.3 Orchestrator（组装器 dict 消费者）——键清单完整盘点

`.get()` 键（全量）：emotion_context(:1158)、prompt_blocks(:1170 ×2)、
emotion_manager(:2033/:2056/**:2412 第三处，本任务新核实**)、
on_emotion_change(:2115 ×2)、relationship_repo(:2133)、relationship_profile(:2135 ×2)。

括号键：emotion_context(:1159 读)、trace(:2042/:2086/:2212 原地 append)。

- **无未记录新键**：除已知 7 键外无其他 dict 键读取。
- `screen_context` dict 键**只写不读**：orchestrator 屏幕一律走
  `self.screen_context_manager` 直接调用（:1130-1133/:2389-2392），
  投影保留该键无害。
- 假键断链确认：relationship_repo / on_emotion_change 只有读无写；
  2139-2164 关系段不可达；orchestrator_hooks.py 唯一引用位于该不可达段
  （orchestrator.py:2164），hooks 内的三个 `_process_*` 函数为死代码副本。
- **R1 步骤 3 前提成立**：`self.emotion_manager` 属性存在（orchestrator.py:349/
  353/363），且正是 assemble 的实例来源（:1120）——改读自持属性完全可行。

### 2.4 持久化与外围读取者（frozen #2 消费者）

| 消费点 | 位置 | 对 v2 的行为 | 结论 |
|---|---|---|---|
| persistence_hook.persist | :150 isinstance 硬检查 | **静默跳过**（saved=False，不抛异常） | ⚠️ 步骤 1 前置条件 P1-1 |
| context_storage.save | :243-246 isinstance 硬检查 | ValueError | ⚠️ P1-1（persist 已先挡） |
| context_storage.load | payload schema ∈ {"1.0"}（:59）+ lifecycle from_dict | v2 dict 被 from_dict 过滤已知字段 → 基底保留、七层丢失 | 短期可接受，回放需求另议 |
| event_adapter | :174 isinstance 分支 | v2 走 else 分支 | ⚠️ P1-2 须核对分支语义 |
| lifecycle_bridge | :227 isinstance 分支 | 同上 | ⚠️ P1-2 |
| event_publisher | 无 isinstance（duck-typed 读字段） | 基底字段名直通，兼容 | ✅ |

入口侧（api_server.py）：RuntimeController No-Op → runtime.process(:761) →
orchestrator.process(:820) 三级降级，与本任务无交互。

---

## 3. 迁移顺序（A.1.5 步骤 0-4 + 本次新增前置条件）

- **步骤 0（已完成）**：键清单固化测试 = `tests/test_runtime_context_contract_freeze.py`
  （本次 12 项）+ A.2 的 13 项测试。
- **步骤 1：pipeline:703 构造点切换 v2** —— 前置条件（同一提交内完成，否则静止）：
  - **P1-1**：persistence_hook:150 与 context_storage:243 的 isinstance 门必须扩展
    （建议 `isinstance(ctx, (LifecycleRC, v2))`；storage payload schema 不变，
    旧 147 份快照零影响）。不处理 = 持久化静默丢失且无告警。
  - **P1-2**：event_adapter:174 / lifecycle_bridge:227 的 isinstance 分支语义须核对，
    v2 基底字段直通可 duck-type 兼容。
  - **P1-3**（可选清理）：pipeline:1160 对 frozen 的无效属性写。
  - **P1-4**：v2 构造 inputs 必须带 user_message/user_id（R2）+ recent_history
    （V1.1.1 记忆查询融合依赖，缺失即断指代检索）。
- **步骤 2：normalizer upcast/downcast** —— 前置条件：
  - P2-1：两个归一化器（runtime_core:76 + lifecycle_executor:61）同步或明确分工；
  - P2-2：downcast 映射 = inputs.user_message→user_input、user_id→_ctx_user_id、
    recent_history→history/_recent_history；v2.metadata 在 #3 无落点
    （现状即如此，无回归）；
  - P2-3：17 阶段继续使用 mutable #3，v2 不进 executor。
- **步骤 3：orchestrator 消费 legacy_view** —— 前置条件：
  - P3-1：两处 assemble 调用点（:1113/:2376）同步替换；emotion_manager 先注入
    （to_legacy_view(emotion_manager=self.emotion_manager)）后改读自持属性并按
    R1 移除注入参数；
  - P3-2：第三处读取 :2411 一并覆盖；键清单已被步骤 0 冻结锁死。
- **步骤 4：假键退役** —— 先裁决「接链 or 退役」（与 P2.3 勘察建议 1 联动）；
  v2 侧 FORBIDDEN_LEGACY_KEYS deny-list 已生效（本任务加固），生产侧假键读点
  删除前无行为冲突。

---

## 4. 风险

| # | 风险 | 概率 | 缓解 | 回滚 |
|---|---|---|---|---|
| R-A | 步骤 1 单独切换 → persistence_hook 静默丢持久化 | 高（必现） | P1-1 同一提交适配 | 单提交可逆 |
| R-B | 第二归一化器（lifecycle_executor:61）双实现漂移 | 中 | P2-1 同步或收敛 | 局部 |
| R-C | v2.metadata 降级到 #3 时无落点（runtime_path_audit 等） | 低（现状即如此） | 记录为已知限制 | — |
| R-D | context_storage.load 用 v1.0 from_dict 读 v2 dict → 七层丢失 | 中 | 需回放 v2 快照时改 load；短期只读基底可接受 | 可回滚 |
| R-E | pipeline:1160 对 frozen 的无效属性写 | 低（已失效） | P1-3 清理 | — |
| R-F | 147 份旧快照兼容性 | 低（storage payload schema 不变） | SUPPORTED_SCHEMA_VERSIONS 不动 | — |
| R-G | orchestrator_hooks 死代码副本含同类键读取 | 低（唯一引用在不可达段） | 步骤 4 同期处置 | — |
| R-H | R4 未核实（17 阶段写入口无权限门） | — | 独立专项，不混入上下文迁移 | — |

---

## 5. 禁止事项

1. 本任务与 A.3 之前：不切换入口、不接线、不改 orchestrator/runtime_core/
   runtime_pipeline/persistence_hook/context_storage/event_adapter/lifecycle_bridge。
2. 不删除三个 legacy Context 文件与 orchestrator_hooks.py。
3. 不重新引入 relationship_repo / on_emotion_change（v2 deny-list 已锁，
   冻结测试防回归）。
4. 不把 manager/repo/path/client/bus 句柄放入 v2（结构冻结测试已锁）。
5. 不修改 data/、不修改持久化目录与留存策略、不改变权限门语义、
   不修改 memory/emotion/growth/personality 算法。
6. 步骤 1 实施必须把 P1-1/P1-2 纳入同一提交；未满足前置条件时禁止半接入状态。
7. 旁路收口（B1-B7）是独立实施线，禁止混入上下文迁移。
