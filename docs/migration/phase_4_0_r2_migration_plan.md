# Phase 4.0-R2.0 Runtime Entry Consolidation · 迁移计划（规划文档 v1.0）

> **Phase**: 4.0-R2.0（**本文件仅做规划，未经架构师 review 前不实际执行代码变更**）
> **前置依赖**: Phase 4.0-R1 Authority Registry ✅（见 [docs/authority_registry.md](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/docs/authority_registry.md)）
> **严格约束**: 本 R2 系列**只改 Runtime 入口相关文件**；不新增任何 System/Class；不修改人格数据；不修改 Growth/Memory 业务逻辑（除了 7 处 `MemoryStore()` 的注入改造，且严格不碰 JSON/Vector 内部实现）。

---

# 一、迁移边界与严格红线

## 1.1 R2 只允许触碰的文件集合（白名单）

| 类别 | 文件 | R2 触碰方式 |
|---|---|---|
| 入口层 | `main.py` | 仅替换 Orchestrator 实例化为 RuntimePipeline 适配调用 |
| 入口层 | `api_server.py` | 仅替换两处 `Orchestrator.process()` 裸调用为走 RuntimePipeline；**禁止**在 pipeline.run 外再裸 Orchestrator.process() |
| Runtime Canonical | `src/runtime/runtime_pipeline.py` | 新增 RuntimePathAudit 结构、fallback 告警调用；**禁止**修改 _try_runtime_path / Orchestrator.process 的执行优先级（即：runtime 优先→失败回退 orchestrator，这个逻辑 R2 不动） |
| Runtime Bridge | `src/runtime/runtime_bridge.py` | ① RuntimeBridge.initialize() 内部默认构造 Canonical Adapter 版 RuntimeCore（而非 deprecated super-class 版）；② get_memory_store 取不到时**不允许调用方静默 fallback 自建 MemoryStore**（但这个改造在 R2.3 才做，R2.2 不动） |
| 测试层 | 只允许修改/新增 R2 专属 gate 测试（tests/ 下 test_runtime_entry_convergence_*.py 等）；**禁止**为了让老测试通过而改动 R2 之外的代码 |

## 1.2 R2 绝对禁止触碰的范围（黑名单）

- ❌ `config.yaml`（包括任何 API Key）
- ❌ `src/personality/` 下任何业务逻辑 / TraitState 写入 / PersonalityResolver 逻辑
- ❌ `src/memory/` 下 MemoryStore / VectorMemory 的 JSON、ChromaDB 实现（R2.3 只改"实例获取方式"不改 store 内部）
- ❌ `src/growth/` 下 ProposalManager / ApprovalManager 等 Growth 业务路径
- ❌ `src/runtime/self_model/` 下 Canonical SelfModel 体系（留给 Phase R3）
- ❌ `docs/authority_registry.md`（宪法文件，R2 全部做完后**单独**更新 deprecated 状态）
- ❌ 删除任何文件（runtime_core.py、orchestrator.py 等必须保留作为 Legacy Adapter）
- ❌ 同时执行 R2.1-R2.4 的多个子步骤（必须逐步、逐步 review、逐步测）

---

# 二、Before / After 调用链对比

## 2.1 当前实际调用链（Before 状态，审计发现）

```
┌─────────────────────────────────────────────────────────────────┐
│                       外部消息进入                               │
│  main.py（CLI）            api_server.py（HTTP / AstrBot）       │
└────────────┬──────────────────────────┬─────────────────────────┘
             │                          │
  main.py:74 │ orch=Orchestrator()      │ api_server.py:209 orchestrator = Orchestrator()
             │（完全没 RuntimePipeline！）│ api_server.py:303 _pipeline = RuntimePipeline(...)
             ▼                          ▼
    LongLoop(orchestrator=orch)    _process_via_pipeline(user_msg):
             │                          │
             │                          ├─ 尝试 pipeline.run({...})
             │                          │   ├─ 5A) runtime.process() → Runtime（若注入了）
             │                          │   ├─ 5B) reply 为空 → Orchestrator.process() （pipeline 内部 fallback）
             │                          │   └─ persistence_hook / event_sink
             │                          │
             │                          └─ L334-340：⚠️ **SECONDARY FALLBACK OUTSIDE PIPELINE！**
             │                              pipeline.run() 没拿到 reply → 裸 orchestrator.process()
             │                              （跳过 persistence_hook / event_sink / 所有 audit！）
             ▼
   Orchestrator.process(user_input)         两种路径：
   (Legacy 纯裸调用)                         · pipeline + internal_fallback
                                            · pipeline → secondary_outside_fallback  ← BAD

                  ↳ 两个入口各自调用 Orchestrator.process → 状态无法统一审计
                  ↳ RuntimeBridge.initialize() 默认构造 deprecated super-class RuntimeCore（4102 行版）
                    而非 Authority 指定的 src/runtime.py Adapter 版
                  ↳ RuntimeBridge.get_memory_store() 返回 None 时，7 处调用方：
                    `store = bridge.get_memory_store() or MemoryStore()`  ← 静默 fallback 自建，数据分裂
```

### 当前已知缺陷 6 项（R2 要逐一解决）

| # | 缺陷 | 位置 | 影响 | R2 子步骤修复 |
|---|---|---|---|---|
| D1 | main.py CLI **完全不走 RuntimePipeline**，100% 纯 legacy | [main.py:74](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/main.py#L74) + [main.py:88](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/main.py#L88) | LongLoop 产生的所有回复都绕开 Runtime stage，Memory/Emotion 可能走 legacy 自建实例 | R2.2 |
| D2 | api_server.py L334-340 有 **pipeline 外部的二次 Orchestrator.process() 兜底** | [api_server.py:334-340](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/api_server.py#L334-L340) | pipeline.run() 内部已经做过 persistence + event，但这个裸调用完全不经过；audit 计数全错 | R2.2 |
| D3 | RuntimePipeline 只有内存计数器，**没有结构化 RuntimePathAudit** 写入 RuntimeContext | [runtime_pipeline.py:189-196](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_pipeline.py#L189-L196) | fallback 了之后只能去读 `_legacy_reply_count` 这种整数，无法在历史记录里追溯"某条消息到底走了哪条路径"；Phase R5 1 万轮稳定性断言无法做精确审计 | R2.1 |
| D4 | fallback 发生是**静默**的，没有告警（logger.debug 或 warning），没有事件通知 | [runtime_pipeline.py:494-497](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_pipeline.py#L494-L497) + [runtime_pipeline.py:351-375](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_pipeline.py#L351-L375) | 长期运行后 Runtime 路径 silently 失败 100% fallback 到 Orchestrator，根本没人知道 | R2.4 |
| D5 | RuntimeBridge.initialize() 默认 import 的是 **deprecated super-class RuntimeCore**（4102 行版），而不是 Authority 指定的 Adapter 版 RuntimeCore（runtime.py） | [runtime_bridge.py:21](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_bridge.py#L21) + [runtime_bridge.py:88](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_bridge.py#L88) | RuntimeBridge 持有的 RuntimeCore 和 RuntimePipeline.process() 注入的 runtime_inst 可能是两个不同类（super-class vs adapter），状态完全分裂 | R2.2 |
| D6 | **7 处** `MemoryStore()` 自建点使用"get_memory_store or 自建"模式，取不到共享实例就静默 fallback，导致 Memory 多实例写入分裂 | 具体位置见 Authority Registry §3 | 长期运行 100 万条后：VectorMemory 索引各自独立，PollutionGuard 写入规则不一致，consolidation 只扫自己那份 | R2.3 |

---

## 2.2 目标调用链（After R2.1-R2.4 全部完成后）

```
┌─────────────────────────────────────────────────────────────────┐
│                       外部消息进入                               │
│  main.py（CLI）            api_server.py（HTTP / AstrBot）       │
└────────────┬──────────────────────────┬─────────────────────────┘
             │                          │
             │ 通过 RuntimePipelineFactory 或相同初始化函数获取
             │  canonical RuntimePipeline（内部必须注入 RuntimeBridge）
             ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ RuntimePipeline.run(input_data)   ← CANONICAL，唯一对外 process 入口        │
  │                                                                          │
  │  1) 构建 RuntimeContext + RuntimePathAudit（entry="RuntimePipeline"）      │
  │  2) token_optimizer.optimize (可选)                                       │
  │  3) 尝试 Runtime.process() → 如果 runtime_inst 存在                        │
  │     ├─ 成功非空 → path="full_runtime", fallback=False                      │
  │     └─ 失败/空 → 进入 Pipeline 内部 fallback                               │
  │           ↳ 写 audit: runtime_attempted=True, runtime_succeeded=False      │
  │           ↳ 触发 CRITICAL fallback 告警（EventBus + logger.critical）      │
  │  4) Pipeline 内 fallback Orchestrator.process (legacy adapter)            │
  │     ├─ 成功非空 → path="orchestrator_legacy_fallback", fallback=True       │
  │     └─ 失败/空 → 记录到 audit，最终 reply 空字符串                          │
  │  5) **禁止 pipeline 之外任何代码裸调 Orchestrator.process()**              │
  │     → main.py LongLoop 的 orchestrator 必须替换为 pipeline.run() 适配       │
  │     → api_server.py _process_via_pipeline 中二次裸兜底必须删除              │
  │  6) persistence_hook.persist(context.outputs + audit)                      │
  │  7) event_sink.emit(audit_event)                                           │
  └──────────────────────────────────────────────────────────────────────────┘

Runtime 共享实例来源：
  RuntimeBridge.initialize() 默认 → src.runtime.runtime RuntimeCore（CANONICAL Adapter 版，非 deprecated）
  → 调用方获取 MemoryStore：bridge.get_memory_store() 若 None → 直接 raise RuntimeError 启动失败
    （不再允许 `or MemoryStore()` 静默自建）
```

---

# 三、RuntimePathAudit 数据结构（R2.1 交付物规范）

**写入位置**: `RuntimeContext.metadata["runtime_path_audit"]` 字段；同时 `context.outputs["snapshot"]` 中也附加 audit 摘要（用于持久化到磁盘）。

```python
# schema_version = "1.0"（R2.1 冻结，后续版本号递增）
{
  "schema_version": "1.0",

  # ─── 用户要求的 3 个强制字段 ────────────────────
  "entry": "RuntimePipeline",          # enum: "RuntimePipeline" / "Orchestrator.direct" / "Unknown"
                                        # 任何直接 Orchestrator.process() → entry="Orchestrator.direct"
                                        # 只要用户正常入口进 RuntimePipeline → entry="RuntimePipeline"
  "path": str,                          # enum:
                                        #   "full_runtime"                    = Runtime.process 成功拿到回复
                                        #   "orchestrator_pipeline_fallback"  = Pipeline 内部 fallback Orchestrator
                                        #   "orchestrator_direct"             = 绕过 Pipeline，直接 Orchestrator.process（D1/D2 的罪证）
                                        #   "empty_reply"                     = 最终 reply 为空
  "fallback": bool,                     # True = 走了任何一次 legacy Orchestrator（pipeline 内或外都算）

  # ─── 诊断辅助字段（Phase R5 10k 轮稳定性断言必需）───────────────
  "runtime_attempted": bool,            # 是否尝试过 Runtime.process()
  "runtime_succeeded": bool,            # Runtime.process() 是否返回了非空回复
  "orchestrator_invoked": bool,         # 是否实际调用过 Orchestrator.process()
  "orchestrator_invoked_outside_pipeline": bool,  # DANGER: True = pipeline 外部裸调用（D1/D2）
  "reply_source": str | None,           # "runtime" / "orchestrator_fallback_pipeline" / "orchestrator_direct_outside_pipeline" / None
  "reply_empty": bool,

  # ─── 追踪字段 ─────────────────────────────────
  "lifecycle_id": str,
  "session_id": str,
  "timestamp_iso": str,                 # ISO 8601 UTC
  "duration_ms": int,

  # ─── 错误追踪 ─────────────────────────────────
  "runtime_error": str | None,          # Runtime.process 抛错的 type+message
  "orchestrator_error": str | None,     # Orchestrator.process 抛错的 type+message
}
```

**审计承诺（R2.1 验收）**:
- 任何一次 `RuntimePipeline.run()` 调用完成后，`context.metadata["runtime_path_audit"]` 一定存在且字段齐全。
- **D1/D2 两个致命绕过**，一旦发生 audit 的 `orchestrator_invoked_outside_pipeline` 必须为 True。

---

# 四、R2.1-R2.4 四步子计划

> 严格按顺序执行；每步完成后必须跑测试 + 架构师 review，再进入下一步。
> 每步都**不删除文件、不改业务逻辑、不改人格/成长/记忆内部实现**。

---

## 4.1 R2.1 Runtime Path Audit（先只加审计，不改任何执行路径）

### ✅ 目标
给现有 RuntimePipeline + 入口层 增加结构化 RuntimePathAudit 写入，但**不改任何执行优先级和 fallback 逻辑**。  
这一步完成后，我们能精确知道"每一条消息到底走了哪条路径"，为后面 R2.2-R2.4 的收敛提供真实数据。

### 📝 修改文件清单（严格白名单）
| 文件 | 修改内容 |
|---|---|
| `src/runtime/runtime_pipeline.py` | ① 顶部定义 `RUNTIME_PATH_AUDIT_SCHEMA_VERSION = "1.0"` 常量；② 定义 `RuntimePathAudit` TypedDict（放在文件顶部或 contracts/，不得放到其他核心模块）；③ `RuntimePipeline.__init__` 不改；④ `RuntimePipeline.run()` 中在 `context = RuntimeContext(...)` 创建之后、`self._extract_user_message` 之后就创建 audit dict 骨架，在 **5A runtime 尝试后** / **5B orchestrator 调用后** / **_process_via_pipeline 外兜底（若仍存在）** 三处分别更新 audit 字段，最终 `context.metadata["runtime_path_audit"] = audit`；⑤ `context.outputs["snapshot"]` 追加 `audit_summary = {entry, path, fallback}` 字段持久化。 |
| `api_server.py` | **这一步只加审计，不改逻辑**：在 `_process_via_pipeline` 的 L334-340 secondary fallback 前后写 audit 的 `orchestrator_invoked_outside_pipeline=True` 标记。**不要删掉这 6 行**。 |
| `main.py` | **这一步只加审计，不改逻辑**：在 LongLoop 回调里，或 LongLoop 内部（如果 LongLoop 有 hook）记录每次调用是 Orchestrator.direct，写入 audit（如果 LongLoop 不支持 RuntimeContext，先在 `LongLoop(orchestrator=orch)` 的 on_reply 中 logger.warning("R2.1 Audit: Orchestrator.direct entry detected from main.py CLI")）。 |

### ⚠️ 风险等级（R2.1）
🟢 **低风险**
- 只加 audit 字段、dict、logger 写入；不改任何执行分支。
- 潜在风险：RuntimeContext.metadata / outputs 契约是否允许额外字段？—— 现有阶段 gate 测试只断言 required 字段存在，unused 字段未禁止。若失败，改放到 `context._runtime_path_audit_unsafe_private` 属性而非 metadata。

### 🧪 测试范围（R2.1）
1. **新增 gate 测试** `tests/test_runtime_path_audit_r2_1.py`：
   - 构造 RuntimePipeline（带 Mock Runtime 成功返回）→ 断言 audit.entry="RuntimePipeline", path="full_runtime", fallback=False
   - 构造 RuntimePipeline（带 Mock Runtime 返回 None）→ 内部 fallback Orchestrator → 断言 entry="RuntimePipeline", path="orchestrator_pipeline_fallback", fallback=True
   - 构造直接 Orchestrator.process()（模拟 main.py）→ 断言 audit.entry="Orchestrator.direct"
   - 构造 api_server 外兜底（Mock RuntimePipeline 返回 empty）→ 断言 orchestrator_invoked_outside_pipeline=True
2. **运行 R1 已通过的 1377 条测试**，确认 audit 新增没引入回归（docstring 也没改到的测试应 100% 通过）。

### ✅ R2.1 验收标准
- 所有 R1 已通过测试保持通过。
- 新 gate 测试 4/4 通过。
- 能从 RuntimeContext.metadata / outputs.snapshot 拿到 audit 字典且字段与 §三 schema 完全一致（含 schema_version=1.0）。

---

## 4.2 R2.2 Runtime Entry 收敛（入口统一 + Bridge RuntimeCore 升级）

### ✅ 目标
**关闭 D1 和 D5 两个缺陷**：
1. main.py LongLoop 不再直接 `Orchestrator.process()` → 改为适配调用 RuntimePipeline.run()。
2. api_server.py L334-340 **pipeline 外部裸 Orchestrator.process()** → 移除这个二次兜底（如果 pipeline.run() 真失败就让 reply=""，让 persistence 仍然只做一次）。
3. RuntimeBridge.initialize() 默认构造 **Canonical Adapter 版 RuntimeCore**（src.runtime.runtime），而非 deprecated 4102 行版本。

### 📝 修改文件清单
| 文件 | 修改内容 |
|---|---|
| `main.py` | 在 CLI 入口（main.py:74-94 附近）创建 shared RuntimeBridge → 创建 RuntimePipeline(orchestrator=orch, runtime=bridge.runtime_core 或从 src.runtime.runtime import impl) → 给 LongLoop 一个适配器 `orchestrator_like` 对象，其 `.process(user_message)` 内部做 `RuntimePipeline.run({"user_message": user_message})` + 从 outputs.snapshot.reply 提取回复字符串，完全等价于之前的 Orchestrator.process 返回 str。LongLoop 的 orchestrator 参数传入这个适配器。**保留 Orchestrator 实例**（作为 fallback），但不让 LongLoop 直接调它。 |
| `api_server.py` | 删除 L334-340（6 行 secondary fallback outside pipeline）。保持 pipeline.run() 的内部 fallback 仍工作。 |
| `src/runtime/runtime_bridge.py` | ① `RuntimeBridge.__init__` 内部默认 RuntimeCore 来源改为：**先尝试 Canonical** `from src.runtime.runtime import impl as _canonical_runtime_impl`，**如果这个 impl 不满足 process(event, ctx) 方法签名**（比如因为配置问题未正确初始化）→ 才 fallback 到旧 super-class RuntimeCore（并在 R2.4 fallback 告警中记一条 CRITICAL）。② 把 `from src.runtime.runtime_core import RuntimeCore`（line 21 的 import）改为 `from src.runtime.runtime_core import RuntimeCore as _LegacyRuntimeCoreSuperClass`，避免 type 同名误导。 |

### ⚠️ 风险等级（R2.2）
🟡 **中高风险**（这是 R2 中最容易出 bug 的一步）
- **风险 1（高）**: LongLoop 适配器 `process(str)->str` 与原 Orchestrator.process 签名差异（比如返回 None、返回 RuntimeContext 而非 str）→ 导致 CLI 打印 None。→ 缓解：adapter 中做多层 if/else 兜底返回空串，严格类型 str。
- **风险 2（中）**: api_server 删除外部 fallback 之后，pipeline 内部 fallback 万一也失败，会真的返回 reply="" 而不是之前"哪怕 pipeline 挂了 Orchestrator 裸调还能救"。→ 缓解：R2.4 fallback 告警做到位，一旦发生立刻 CRITICAL；但"回复空"比"状态写两次、persistence 不一致"更安全，可接受。
- **风险 3（中）**: RuntimeBridge 默认改成 Adapter 版 RuntimeCore 后，之前依赖 super-class RuntimeCore 上某个具体 `get_xxx_manager()` 方法（如 action_dispatcher / world_state）的 RuntimeBridge 方法（460+ 行中 get_snapshot 用到 action_dispatcher）调用会 AttributeError。→ 缓解：改 import 后不改 RuntimeBridge 的 L100+ 的方法体，先只改 L21 + L88 的构造来源，然后针对 get_snapshot / health_check 做"如果 Adapter 版没有该属性，就回退 _LegacyRuntimeCoreSuperClass"。这一步允许 RuntimeBridge 保留双实例（一个 canonical runtime，一个 legacy runtime_core），只要对外统一就行。R2.2 不要求把 super-class 彻底移除。

### 🧪 测试范围（R2.2）
1. **回归测试**：跑所有 R1 已通过 + R2.1 的 gate 测试
2. **新增入口收敛测试** `tests/test_runtime_entry_convergence_r2_2.py`：
   - `test_cli_longloop_goes_through_pipeline`: 模拟 main.py LongLoop 使用 adapter 后，内部调用的是 RuntimePipeline.run()（Mock 断言 pipeline.run 被调用）
   - `test_no_orchestrator_direct_entry_from_api_server`: 模拟 api_server _process_via_pipeline，pipeline.run 返回空 reply → 断言 **不再**调用 `orchestrator.process` outside pipeline
   - `test_runtime_bridge_default_uses_adapter_runtimecore`: RuntimeBridge.initialize() 后，内部 runtime_core 类型是 `src.runtime.runtime.RuntimeCore`（Adapter 版）
3. **冒烟测试**（手动，自动化可选）：启动 main.py CLI 输入"你好"能拿到回复，打印 audit_summary 显示 fallback=True 或 False，不再打印 Orchestrator.direct。

### ✅ R2.2 验收标准
- R2.1 测试全部保持通过。
- RuntimePathAudit 统计：运行 10 条消息，`orchestrator_invoked_outside_pipeline` 全为 False（D2 修复）。
- RuntimePathAudit 统计：CLI 入口不再出现 `entry="Orchestrator.direct"`（D1 修复）。
- RuntimeBridge 初始化后：`type(bridge.runtime_core).__name__` 以 Adapter 版 RuntimeCore 为主（legacy super-class 仅可能作为 fallback 实例保留）。

---

## 4.3 R2.3 MemoryStore 注入改造（关掉 7 处静默自建）

### ✅ 目标
**关闭 D6**：Registry 中记录的 7 处 `MemoryStore()` 自建点（orchestrator fallback / context_manager / self_check / memory_system / topic_tracker / event_extractor / runtime_core lazy），从模式 `store = bridge.get_memory_store() or MemoryStore()` → 改成：
```python
store = bridge.get_memory_store()
if store is None:
    # 在生产（runtime.enabled=true）环境，直接启动失败
    raise RuntimeError(
        "[R2.3 Memory Authority] RuntimeBridge.get_memory_store() 返回 None，"
        "禁止静默自建 MemoryStore 实例。请先初始化 RuntimeBridge 并启用 Runtime。"
    )
```

### 📝 修改文件清单
先**精准定位 7 处自建点**（通过 Grep `MemoryStore(` 且不在 memory/memory_store.py 自身）：
| 文件（待精确定位） | 当前代码模式 | 改造方式 |
|---|---|---|
| ① `src/orchestrator.py`（fallback 新建位置） | `self.memory_store = bridge.get_memory_store() if bridge else MemoryStore()` | 严格 `bridge.get_memory_store()`，None 就 raise |
| ② `src/context_manager.py`（或对应位置） | MemoryStore 自建 | 同上 |
| ③ `src/self_check/...` 或对应 | MemoryStore 自建 | 同上 |
| ④ `src/memory/memory_system.py`（已标记 DEPRECATED） | 已知：L26 `self.store = MemoryStore()`（无 bridge） | 这个改法：要求构造时必须传入 store 参数，不接受 None → 旧调用方改注入 |
| ⑤ `src/.../topic_tracker.py` | MemoryStore 自建 | 严格 raise on None |
| ⑥ `src/growth/event_extractor.py` | MemoryStore 自建 | 严格 raise on None |
| ⑦ `src/runtime/runtime_core.py`（deprecated super-class，lazy init） | lazy `if not self._memory_store: self._memory_store = MemoryStore()` | 改为 lazy 时也必须从 bridge 取，None 就 raise RuntimeError |

> ⚠️ 这 7 处的**具体文件+行号在 R2.3 执行前**先用精准 grep 列出来，不能靠记忆；本计划中只做设计，执行时 R2.3.0 子任务先列清单再动手改。

### ⚠️ 风险等级（R2.3）
🟡 **中风险**
- **风险 1**: R2.2 中 RuntimeBridge.get_memory_store() 内部还拿 deprecated super-class RuntimeCore 版本的 store，而如果 R2.2 把 RuntimeBridge 默认换成 Adapter 版 RuntimeCore，它没有 `get_memory_store()` 方法 → bridge.get_memory_store() 永远返回 None → 7 处全 raise → 启动不了。→ 缓解：**R2.3 之前，RuntimeBridge 层的 get_memory_store() 实现**（334-355 行）做双实例兜底：先试 `self._runtime_core.get_memory_store()`（不管它是 legacy super 还是 canonical adapter），若 None / AttributeError → 就 fallback 到 `self._legacy_runtime_core.get_memory_store()`（双实例共存期间保证拿到 store）。这样 7 处 raise 不会误触发。
- **风险 2**: 某些测试构造类时故意不走 RuntimeBridge（为了 mock 隔离）→ 启动失败。→ 缓解：测试中显式注入 fake MemoryStore，不走"get_memory_store or raise"路径；或给 7 处的 raise 加环境变量开关 `YUYI_ALLOW_LEGACY_MEMORY_FALLBACK_FOR_TESTS=1`，仅测试可打开。

### 🧪 测试范围（R2.3）
1. **新增 gate 测试** `tests/test_memory_store_injection_r2_3.py`：
   - 7 处场景，在 RuntimeBridge 正常初始化下各自启动 → 断言不会 raise
   - 7 处场景，在 RuntimeBridge 未初始化（None）下各自启动 → 断言**明确** raise RuntimeError（而非静默自建）
2. **运行已有 memory/ 相关测试**（memory_authority / pollution_guard / vector_memory / consolidation 等）→ 之前通过的要保持通过（通过 YUYI_ALLOW_LEGACY_MEMORY_FALLBACK_FOR_TESTS env）。

### ✅ R2.3 验收标准
- 没有任何 `MemoryStore()` 的裸调用（除了 tests/ 测试 fixture 和 memory/memory_store.py 自身）。
- 代码全局 grep `MemoryStore()` 只出现在：`src/memory/memory_store.py` + tests/。
- 启动 cli/api_server 后 logger 中**不出现** "[R2.3 Memory Authority] 禁止静默自建" 这条 RuntimeError。

---

## 4.4 R2.4 Fallback 告警

### ✅ 目标
**关闭 D4**：任何 fallback 发生 → 必须通过 **双重通道** 告警（logger.critical + EventBus）。  
这一步不改变是否 fallback，只让 fallback 的时候"有声音"，不至于静默 1 个月。

### 📝 修改文件清单
| 文件 | 修改内容 |
|---|---|
| `src/runtime/runtime_pipeline.py` | ① `_try_runtime_path` 中：Runtime.process 抛错 / 返回 None 两个分支 → logger.critical（不是 warning / debug）**同时** event_sink.emit({"event_type": "runtime.fallback.triggered", "reason": ...})；② Pipeline 内部 5B Orchestrator.process 被调用 → logger.critical + emit；③ 构造 audit dict 中 `fallback=True` 时必须同步触发告警（**不重复**，用同一个 "triggered once per run" 防止同一条消息多次告警）。 |
| `src/runtime/runtime_bridge.py`（可选） | 如果 RuntimeBridge 初始化时 fallback 到 legacy super-class RuntimeCore（因为 canonical adapter 版不可用）→ 记录一条 logger.critical "[R2.4] RuntimeBridge fell back to legacy super-class RuntimeCore（non-canonical）" |

### ⚠️ 风险等级（R2.4）
🟢 **低风险**
- 只加日志和事件发送；不改逻辑分支。
- 注意：event_sink 可能为 None（构造时没传）→ 告警必须 "if self._event_sink: ... else: logger.critical(... double print ...)"

### 🧪 测试范围（R2.4）
1. **新增 gate 测试** `tests/test_runtime_fallback_alert_r2_4.py`：
   - Runtime.process 返回 None → 断言 logger.critical 被调用 1 次（caplog），EventBus 收到 `runtime.fallback.triggered`
   - Runtime.process 抛错 → 同上
2. **运行已有 RuntimePipeline 测试**（test_runtime_pipeline.py、Phase 4.0.2 相关 gate）：统计 fallback 计数是否一致，是否没有产生重复告警。

### ✅ R2.4 验收标准
- 100 条消息中，只要任何一条走了 orchestrator_fallback 路径 → 至少 N 条 logger.critical / EventBus 事件（N = fallback 发生的条数，不重复不遗漏）。
- 不产生 log 风暴：同一次 run 只发一次 fallback 告警。

---

# 五、回滚策略（每步独立回滚）

R2.1-R2.4 **每步**都能独立回滚，不需要回到 R1 起点：

| 子步骤 | 回滚方式 | 回滚成本 |
|---|---|---|
| R2.1 | 把 RuntimePipeline.run() 中 audit 相关代码移除；移除新增的 TypedDict；恢复 main.py / api_server.py 中 audit 钩子。 | 🟢 低（纯追加代码，删掉即可） |
| R2.2 | ① main.py：LongLoop 从 adapter 恢复到直接传 Orchestrator；② api_server.py：恢复 L334-340 的 outside fallback；③ runtime_bridge.py：恢复 `from src.runtime.runtime_core import RuntimeCore` 原名、恢复默认构造 super-class。 | 🟡 中（3 处改动，每处都是恢复原状） |
| R2.3 | 7 处 `raise RuntimeError` 恢复为 `or MemoryStore()` 模式；移除 YUYI_ALLOW_LEGACY_MEMORY_FALLBACK_FOR_TESTS env。 | 🟢 低（都是一行条件判断的替换） |
| R2.4 | 删除 RuntimePipeline 中 logger.critical / EventBus.emit 的 fallback 告警调用。 | 🟢 低 |

---

# 六、R2 全部完成后的最终检查表（Final Acceptance）

| # | 验收项 | 验收方法 |
|---|---|---|
| 1 | 所有入口（main.py + api_server.py）都经过 RuntimePipeline，不再出现 Orchestrator.direct | 生产环境跑 1000 条消息，audit.entry 全为 "RuntimePipeline"，"Orchestrator.direct" 计数 = 0 |
| 2 | Pipeline 外部裸 Orchestrator.process() 清零（D2 修复） | audit.orchestrator_invoked_outside_pipeline = 0 for all messages |
| 3 | RuntimeBridge 默认 RuntimeCore 类型 = Canonical Adapter 版 | `type(bridge.runtime_core).__module__` 是 src.runtime.runtime |
| 4 | 全局 grep `MemoryStore(` 除了 src/memory/memory_store.py + tests/，其他位置为 0 | 代码搜索：7 处改造完成确认 |
| 5 | fallback 不静默：任何 fallback 发生都会 logger.critical + EventBus | caplog 断言 + mock EventBus 断言 |
| 6 | R1 1377 条测试全绿 + R2 四个子步骤的 gate 测试全绿 | pytest 全绿 |
| 7 | 没有删除任何文件；RuntimeCore（super-class）、Orchestrator 类还在（只是降级 Legacy Adapter） | 文件系统 + import 验证 |
| 8 | `docs/authority_registry.md` 未随 R2 代码自动修改；R2 结束后**单独**提交 PR 更新 deprecated 状态 | git diff docs/authority_registry.md 为空（R2 做完再改） |

---

# 七、进度追踪（执行时勾选）

| 阶段 | 状态 | 代码合入 PR | 测试通过 | Review |
|---|---|---|---|---|
| R2.0 Plan（本文档） | ✅ 规划完成，待 review | — | — | — |
| R2.1 Runtime Path Audit | ⬜ | — | — | — |
| R2.2 Entry Convergence | ⬜ | — | — | — |
| R2.3 MemoryStore Injection | ⬜ | — | — | — |
| R2.4 Fallback Alert | ⬜ | — | — | — |
| 更新 authority_registry.md deprecated 列表 | ⬜（全部 R2 完后才做） | — | — | — |
| R2 总体 Final Acceptance（§六 8/8） | ⬜ | — | — | — |

---

**文档版本**: v1.0（Phase 4.0-R2.0 Migration Plan，纯规划，未执行任何代码变更）  
**要求**: 架构师逐节 review 本计划，确认 R2.1-R2.4 的拆分粒度、风险评估、测试范围都没问题，然后**授权执行 R2.1**；不允许 Trae 一次跳过多个子步骤。
