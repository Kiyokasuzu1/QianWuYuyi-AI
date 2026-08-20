# RuntimeContext 归一化入口统一方案（runtime_context_normalization_plan）

> 任务：P2.3-A.2.6 Phase 4　日期：2026-08-19　分支：develop/v1.1　HEAD：225d040
> 性质：**只读设计文档**——本任务未修改任何归一化实现（runtime_core.py 仅新增一行预留注释，见 Phase 3）
> 被调查对象：`runtime_core.py:_normalize_runtime_ctx` 与 `lifecycle_executor.py:_normalize_mutable_ctx`

---

## 1. 结论（先行）

**判定：A——合并为唯一归一化入口（终态）。** 分两阶段落地：

- **当前阶段（A.3 之前）**：保持双实现现状（即"B 保留"作为过渡态），禁止任何一方单独演进。
- **P2.3-A.3 步骤 2**：`_normalize_runtime_ctx` 委托 `context_adapter.from_lifecycle_context()` 承接 v1→v2，新增 v2→mutable 直通桥；`_normalize_mutable_ctx` 收窄为极薄安全网（直通 mutable，其余一律重建，不再做字段拷贝）。

理由：同语义双实现已产生 4 处实证漂移（§3）；归一化是「外部 context → 17 阶段 mutable 工作集」的**唯一收口点**，多实现即多权威，A.3 接入 v2 后漂移将直接演变为行为分叉。

---

## 2. 现状盘点

| 项目 | `runtime_core.py:76` `_normalize_runtime_ctx` | `lifecycle_executor.py:61` `_normalize_mutable_ctx` |
|---|---|---|
| 唯一调用点 | `RuntimeCore.process()` `:4787`（第 2 步，ctx 初始化） | `LifecycleExecutor.execute()` `:174`（第 0 步，ctx 安全网） |
| 输入形态 | pipeline 构造点（runtime_pipeline.py:703）产出的 lifecycle v1.0（frozen）；或 None；或 mutable | 经 process 归一化后的 mutable（主路径 fast-path 直通）；直调 execute 时可能为任何类型 |
| 输出 | 永远 mutable RuntimeContext | 永远 mutable RuntimeContext |
| 存在原因 | Phase 4.0.4-Pre：外部可能传 frozen lifecycle_context，Stage 赋值需要 mutable | 同语义；文件内注释声明「不 import runtime_core 避免环，直接做 isinstance 检查 + 必要时重建」 |
| 生产实际分工 | 主路径中承担全部归一化工作 | 主路径中几乎恒为 fast-path no-op（ctx 已 mutable）；仅作防御性兜底 |

调用链（生产主路径）：`process(ctx) → _normalize_runtime_ctx（foreign→mutable）→ _lifecycle.execute(core, event, ctx) → _normalize_mutable_ctx（mutable→直通）`。

---

## 3. 逐行对照与漂移清单

### 3.1 两实现相同部分

1. 十字段拷贝循环：`session_id / user_input / timestamp / schema_version / memory_context / emotion_state / personality_snapshot / growth_proposals / identity_context_text / identity_snapshot_ref`，`getattr` 兜底 + 空值跳过 + `setattr` 失败吞掉。
2. `inputs["user_input"] or inputs["content"]` 回填 `mutable.user_input`（executor 版带 `and not mutable.user_input` 前置，core 版不带——见 D5）。
3. `started_at` → `mutable.timestamp`。
4. mutable 输入直通（isinstance fast path，`return external_ctx` 原对象）。
5. None 输入 → 全新 mutable。

### 3.2 漂移清单（core 版有、executor 版无）

| # | 漂移项 | runtime_core | lifecycle_executor | 影响 |
|---|---|---|---|---|
| D1 | `inputs["user_id"]` → `mutable._ctx_user_id` | ✅ | ❌ | 直调 execute 的外部 ctx 丢失用户隔离键 |
| D2 | `inputs["recent_history"]` → `mutable.history` + `mutable._recent_history`（V1.1.1 Context Continuity） | ✅ | ❌ | 指代类消息的记忆检索连续性只对 process 入口生效 |
| D3 | `metadata["user_id"]` → `mutable._ctx_user_id`（未设置时） | ✅ | ❌ | 同 D1，第二条兜底来源 |
| D4 | `started_at` 覆盖守卫 | `mutable.timestamp == mutable.timestamp`（自比较恒真，实质无条件覆盖） | 无条件覆盖 | 效果等价；core 版守卫是失效代码，合并时删除 |
| D5 | `user_input` 回填前置 | 无 `not mutable.user_input` 前置（先拷贝十字段，后回填也可能覆盖） | 有前置 | 顺序差异；对同一输入当前结果一致，合并时统一语义 |

**结论：executor 版是 core 版的严格子集（能力更弱）。** 主路径中因 fast-path 不暴露差异；一旦 A.3 把 v2 引入（frozen，无法 setattr），两处兜底行为将分叉——这正是必须合并的证据。

---

## 4. 设计判定：A（合并）还是 B（保留双 adapter）

### 4.1 方案 A：合并为唯一归一化入口（**选定**）

落点：`src/runtime/adapters/context_adapter.py`（P2.3-A.2 已建立，双方均可安全 import——它仅依赖 mutable RuntimeContext、lifecycle v1.0、request_context v2、security.identity，无环）。

形态：

```
normalize_external_ctx(external_ctx, *, identity=None) -> mutable RuntimeContext
    ├─ mutable            → 原对象直通（isinstance fast path 保留）
    ├─ lifecycle v1.0     → from_lifecycle_context() → v2 → upcast_to_mutable(v2)
    ├─ request_context v2 → upcast_to_mutable(v2)
    ├─ None / 其他        → 新建 mutable（现状兜底）
```

- `_normalize_runtime_ctx` 改为一行委托（保留函数签名，A.3 步骤 2 实施）。
- `_normalize_mutable_ctx` 收窄为：mutable 直通，否则新建 mutable（删除十字段拷贝——该工作由统一入口完成）；或直接删除并让 executor 信任上游（前置条件 P2-3 决定）。

### 4.2 方案 B：保留双 adapter

优点：零改动。缺点：漂移 D1-D5 永久化；v2 接入后两处必须各自适配（双倍维护、双倍测试面）；与 A.2.5 已登记的阻断项 P2-1（双归一化器漂移）直接冲突。

### 4.3 判定依据

1. **实证**：§3.2 已证明双实现漂移不是理论风险，是既成事实（D1-D5）。
2. **单一权威**：归一化是外→内唯一收口点，与「pipeline:703 唯一构造点」「legacy_view 唯一投影」同类，属于必须单点的关键路径。
3. **环依赖可解**：executor 不 import runtime_core 的顾虑，通过把入口放到中立的 adapter 层解决。
4. **回滚成本**：合并为单函数替换，单提交可逆（§6）。

---

## 5. 合并实施要点（A.3 步骤 2 时实施，本任务不实施）

1. 在 `context_adapter.py` 新增 `upcast_to_mutable(v2) -> mutable RuntimeContext`：拷贝 session_id / user_input（inputs.user_message 优先）/ timestamp（started_at）/ schema_version（标记 `upcast_from_v2` 入 metadata）等十字段；**不回填 D1-D3 的业务字段**——由调用方按需读取 v2 快照，避免把漂移语义固化进 adapter。
2. `_normalize_runtime_ctx` 委托统一入口；`_normalize_mutable_ctx` 按 P2-3 收窄或删除。
3. D4 失效守卫删除、D5 语义统一（以「直通优先、拷贝兜底」为准）。
4. 测试：`test_runtime_core_adapter_reserve.py`（本任务已建）升级为行为等价断言（normalize 前后对 v1/None/mutable 三类输入输出一致）。

### 前置条件

| 编号 | 前置条件 | 状态 |
|---|---|---|
| P2-1 | 漂移 D1-D5 的语义裁决（合并时以哪个实现为准）与等价测试 | 本任务已盘点，裁决待 A.3 |
| P2-2 | `upcast_to_mutable`（v2→mutable 直通桥）设计评审 | 本任务给出形态（§5.1），实施待 A.3 |
| P2-3 | executor 侧 fast-path 语义保留（mutable 直通 + None 重建）锁定测试 | 既有 test_phase_4_0_1_lifecycle_executor.py 已覆盖，A.3 复核 |
| P2-4 | 环依赖验证：runtime_core / lifecycle_executor 均可 import adapter 层 | 已静态核实无环（adapter 不依赖二者） |

---

## 6. 风险与回滚

| 风险 | 等级 | 缓解 |
|---|---|---|
| 合并后 executor 直调路径行为变化（外部 ctx 不再做十字段拷贝） | 中 | 生产主路径 ctx 恒经 process 归一化；直调点仅测试与防御路径；P2-3 等价测试先行 |
| v2→mutable 桥丢字段（D1-D3 语义不回填） | 中 | 桥只负责标识字段；业务字段读取面在 A.3 改为 v2 快照读取，不回填是设计目标而非缺陷 |
| adapter 层职责膨胀 | 低 | 归一化与四转换函数同属「Context 契约面」，单一文件符合 A.2 定位 |

**回滚**：合并为两个文件内的函数级替换，单提交 revert 即回退到双实现现状；测试断言锁定「normalize 对 v1/None/mutable 的输出与合并前逐字段一致」。

---

## 7. 与 P2.3-A.3 的关系

- **A.3 步骤 1**（pipeline:703 构造点切换）不依赖本合并——v2 经现有 `_normalize_runtime_ctx` 进入会退化为「十字段拷贝 + inputs 部分回填」（已由 `test_runtime_core_adapter_reserve.py::test_v2_foreign_context_does_not_raise` 锁定为不抛异常的地板行为）。
- **A.3 步骤 2**（normalizer upcast）即本方案的实施点；届时 `_normalize_runtime_ctx` 按本任务 Phase 3 预留注释委托 `context_adapter.from_lifecycle_context()`。
- 本任务冻结声明：**未修改 `_normalize_runtime_ctx` / `_normalize_mutable_ctx` 任何实现代码**；runtime_core.py 仅新增预留注释一行（Phase 3 授权范围内）。

---

## 附录：证据索引

| 事实 | 证据 |
|---|---|
| core 归一化器全貌 | runtime_core.py:76-156（本任务实测） |
| executor 归一化器全貌 | lifecycle_executor.py:61-93（本任务实测） |
| 双调用点 | runtime_core.py:4787；lifecycle_executor.py:174 |
| executor 不 import runtime_core 的原因 | lifecycle_executor.py:58-60 注释 |
| D4 失效守卫 | runtime_core.py:151（`mutable.timestamp == mutable.timestamp` 自比较） |
| v2 直通地板行为 | tests/test_runtime_core_adapter_reserve.py（P2.3-A.2.6 Phase 3 新建） |
| 归一化先例 | Phase 4.0.4-Pre（runtime_core.py:76 注释自述） |

---

## 8. A.3.2 落地记录（2026-08-19）

合并已实施，形态与本方案 §4.1 有两处有意偏差（任务书 P2.3-A.3.2 约束优先）：

1. **落点改为新文件** `src/runtime/adapters/context_normalizer.py`
   （`normalize_context()`），非 context_adapter.py——旧两函数保留原签名作为
   wrapper 委托之，调用点零改动。
2. **未新增 `upcast_to_mutable` 中转桥**：现有 adapter 家族输出方向均为
   →v2，无 →mutable 转换体；经 v2 中转会改变 v1 默认行为（schema_version /
   字段面漂移），与本任务「禁止改变默认 v1 行为」冲突。归一化入口直接做
   纯字段投影，语义基准 = 现 core 版（superset：D1-D3 补齐、D4 失效守卫
   删除、D5 统一）。

落地内容：

- 输入面：mutable 直通 / None 新建 / legacy dict（from_dict + 动态字段补齐，
  新增能力）/ 上下文对象（v1、v2、context-like，duck-typed 识别）/
  其余 → `ContextNormalizerError`（wrapper 捕获回退全新 mutable，旧宽容行为不变）。
- 验证：`tests/test_context_normalizer.py` 33 测试全绿；57 文件全量回归
  失败集与 HEAD 基线逐条 diff 一致（59/59，唯一差异为锁定新语义的
  executor 等价测试，旧代码下必败）；`test_runtime_core_adapter_reserve.py`
  7 测试通过（旧行为锁定未破）；data/ 零修改。

**回滚**：两个 wrapper 的函数级替换，单提交 revert 即回退双实现现状；
`test_runtime_core_adapter_reserve.py` 的「行为不变」断言与
`test_context_normalizer.py` 的 wrapper 等价断言共同锁定回滚安全网。
