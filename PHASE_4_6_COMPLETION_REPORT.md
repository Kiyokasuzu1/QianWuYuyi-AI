# Phase 4.6 — Self Model Persistence & Evolution History Layer

## Completion Report

- Project: QianWuYuyi-AI
- Phase: 4.6
- Status: COMPLETED
- Tests: 73 / 73 passed
- RUNTIME_VERSION: 4.5 → 4.6

---

## 1. 目标

让羽依的 SelfModel 演化在 runtime 重启之后仍然存在。

Phase 4.5 之后,SelfModel 已经可以在内存中完成以下链路:

```
Memory
 → GrowthProposal
 → SelfModelEvolutionEngine
 → New SelfModelSnapshot
 → IdentityContext
 → BehaviorSignature
```

但演化结果只活在内存中,重启即丢。Phase 4.6 为这条链路补上**持久化层**,让 SelfModel 在重启后可恢复、可回放、可回滚。

---

## 2. 架构变更概览

### 2.1 依赖方向 (保持单向)

```
Runtime (RuntimeCore)
  ↓
SelfModel Persistence Service (PersistenceRuntime)
  ↓
SelfModel Snapshot / EvolutionRecord
```

- Runtime 依赖 Persistence Service
- Persistence Service 只依赖 SelfModelSnapshot / EvolutionRecord 这类纯数据
- Persistence Service **不依赖** src.personality.* (人格核心)
- Persistence Service **不依赖** 任何 LLM SDK
- IdentityBinding / SelfModelEvolutionEngine **不依赖** Persistence
  (演化仍然可以在没有持久化的情况下运行)

### 2.2 阶段插入

`RuntimeStage` 新增:

```python
SELF_MODEL_PERSISTENCE = "self_model_persistence"
```

`RUNTIME_LIFECYCLE_ORDER` 调整:

```
SELF_MODEL_BUILD
  ↓
SELF_MODEL_EVOLUTION
  ↓
SELF_MODEL_PERSISTENCE   ← 新增 (默认 no-op)
  ↓
RESPONSE_GENERATION
  ↓
...
```

---

## 3. 新增文件

### 3.1 持久化模块

`src/runtime/self_model/persistence/`

| 文件 | 职责 |
|------|------|
| `__init__.py` | 模块入口,统一导出核心组件 |
| `self_model_store.py` | `SelfModelStore` —— JSON 文件后端,负责 SelfModelSnapshot 持久化 |
| `evolution_history_store.py` | `EvolutionHistoryStore` —— append-only JSONL,负责 EvolutionRecord 持久化 |
| `snapshot_manager.py` | `SnapshotManager` —— 协调 checkpoint / rollback |
| `persistence_runtime.py` | `PersistenceRuntime` —— 协调器,统一对外 API |

### 3.2 测试

`tests/test_phase_4_6_self_model_persistence.py`

73 个测试用例,覆盖所有组件、集成场景和架构不变量。

### 3.3 Runtime 集成

`src/runtime/runtime.py` 增量改动:

- `RuntimeStage.SELF_MODEL_PERSISTENCE` 新增
- `RUNTIME_LIFECYCLE_ORDER` 插入阶段
- `RuntimeCore.__init__` 新增 `persistence_runtime: Optional[Any] = None`
- `RuntimeCore.configure_persistence_runtime(...)` 新增配置入口
- `RuntimeCore._invoke_self_model_persistence_stage(...)` 新增阶段方法
- `RUNTIME_VERSION = "4.6"`

---

## 4. 组件设计

### 4.1 SelfModelStore

- 后端: JSON 文件 (`data/self_model/identity_<id>.json`)
- 提供: `save / load / exists / delete / validate / health_check`
- 特性:
  - atomic write (写临时文件 → `os.replace`)
  - 损坏文件隔离 (单文件 IO 错误不污染整体)
  - schema_version 校验
  - 序列化 round-trip 一致
  - 大小阈值保护 (`MAX_SNAPSHOT_FILE_BYTES`)

### 4.2 EvolutionHistoryStore

- 后端: JSONL (`data/evolution_history/identity_<id>.jsonl`)
- 提供: `append / list_history / latest / count / health_check`
- 特性:
  - append-only (从不覆盖历史)
  - 保留被拒绝的变更 (rejected records)
  - 保留时间戳 (UTC ISO8601)
  - 支持回放 (`list_history` 完整还原时序)
  - 单行损坏隔离 (坏行不污染后续读取)

### 4.3 SnapshotManager

- 提供: `save_snapshot / restore_snapshot / create_checkpoint / rollback`
- 不变量:
  - Snapshot 不可变 (输入永远深拷贝,绝不修改原对象)
  - Rollback 创建新 version 的 snapshot,保留历史
  - Checkpoint 落盘到独立 `checkpoints/` 子目录

### 4.4 PersistenceRuntime (协调器)

- 提供:
  - `initialize(identity_id)`
  - `load_current_self_model()`
  - `persist_evolution(evolution_result)`
  - `restore_on_startup()`
- 集成:
  - 默认持有 `SelfModelStore + EvolutionHistoryStore + SnapshotManager`
  - 可注入自定义实例 (便于测试 + 适配其它后端)
- 容错:
  - 每个方法 try/except,失败记录 `_last_error` 但不抛出
  - 调用方拿到 `False` / `None` 时可降级

---

## 5. Runtime 集成

### 5.1 演化后持久化

```python
# 在 SELF_MODEL_EVOLUTION 阶段已经算出 evolution_result 之后
# RuntimeCore 读取 ctx 中的 evolution_result 并交给 PersistenceRuntime

def _invoke_self_model_persistence_stage(self, ctx):
    if self._persistence_runtime is None:
        # 旧实例: 阶段完全 no-op,只跑 hook
        self._invoke_hook(RuntimeStage.SELF_MODEL_PERSISTENCE, ctx)
        return
    evolution_result = ctx.metadata.get("self_model_evolution_result")
    identity_id = ctx.metadata.get("identity_id")
    if evolution_result is None or getattr(evolution_result, "is_noop", True):
        self._invoke_hook(RuntimeStage.SELF_MODEL_PERSISTENCE, ctx)
        return
    self._persistence_runtime.persist_evolution(evolution_result, identity_id)
    self._invoke_hook(RuntimeStage.SELF_MODEL_PERSISTENCE, ctx)
```

### 5.2 启动时恢复

```python
# Runtime 启动时(RuntimeCore 构造或首次 init)
persistence_runtime = self._persistence_runtime
if persistence_runtime is not None:
    snap = persistence_runtime.restore_on_startup(identity_id)
    if snap is not None:
        # 喂回 SelfModelBuilder,作为新 session 的基线
        ...
```

---

## 6. 持久化流程图

### 6.1 写入流 (Write Path)

```
SELF_MODEL_EVOLUTION
   │ evolution_result (accepted)
   ▼
SELF_MODEL_PERSISTENCE
   │ persist_evolution(evolution_result, identity_id)
   ▼
PersistenceRuntime
   ├── 1) SnapshotManager.save_snapshot(new_snapshot)
   │       └── SelfModelStore.save(snapshot)
   │              └── atomic write: data/self_model/identity_<id>.json
   │
   ├── 2) SnapshotManager.append_evolution_record(record)
   │       └── EvolutionHistoryStore.append(record)
   │              └── append line: data/evolution_history/identity_<id>.jsonl
   │
   └── 3) (可选) SnapshotManager.create_checkpoint(snapshot, label)
           └── atomic write: data/self_model/checkpoints/identity_<id>__v<n>.json
```

### 6.2 读取流 (Read Path, 启动恢复)

```
RuntimeCore 启动
   │
   ▼
PersistenceRuntime.restore_on_startup(identity_id)
   │
   ├── 1) SelfModelStore.load(identity_id)  → latest snapshot
   │       └─ 文件不存在 → 返回 None (首次启动)
   │       └─ 文件损坏 → 隔离并返回 None
   │
   └── 2) (可选) create_checkpoint  (auto_startup_checkpoint)
           └─ 启动基线 checkpoint,便于回滚
   │
   ▼
返回 SelfModelSnapshot → 喂回 SelfModelBuilder
```

### 6.3 回滚流 (Rollback Path)

```
SnapshotManager.rollback(identity_id, target_version)
   │
   ├── 1) 加载当前 snapshot (current_v)
   ├── 2) 在 checkpoints 目录查找 target_version 对应的 snapshot
   ├── 3) 拷贝为新 snapshot, version = current_v + 1
   │       meta: rollback_from_version = current_v
   │             rollback_to_version  = target_version
   ├── 4) SelfModelStore.save(new_snapshot)         # 不覆盖历史
   └── 5) EvolutionHistoryStore.append(rollback_record) # 记录这次回滚
```

---

## 7. 测试覆盖

`tests/test_phase_4_6_self_model_persistence.py` — 73 个用例:

| 测试类 | 用例数 | 覆盖范围 |
|--------|--------|----------|
| `TestSelfModelStore` | ~14 | save / load / exists / delete / missing file / corrupted json / version mismatch / round-trip / atomic write / health_check / 大小阈值 / validate |
| `TestEvolutionHistoryStore` | ~12 | append / list / latest / count / multiple records / rejected preserved / append-only / 单行损坏隔离 / round-trip / health_check |
| `TestSnapshotManager` | ~12 | save_snapshot / restore / create_checkpoint / rollback / 不可变行为 / 深拷贝 / rollback 创建新 version / rollback 写 history |
| `TestPersistenceRuntime` | ~12 | initialize / load_current_self_model / persist_evolution (accepted/noop/rejected) / restore_on_startup / 异常隔离 / 自定义组件注入 |
| `TestRuntimeCorePersistenceIntegration` | ~8 | SELF_MODEL_PERSISTENCE 阶段调用 / no-op 兼容 / 老 RuntimeCore 仍可运行 / 旧接口无 breaking change |
| `TestEndToEndPersistence` | ~5 | 完整演化 → 持久化 → 重启 → 恢复 闭环 / 多轮演化历史 / 回滚后状态 |
| `TestInvariants` | ~10 | persistence 不 import personality / 不 import LLM SDK / evolution 不依赖 persistence / identity_binding 不依赖 persistence / ResponseEngine 签名未变 / RuntimeContext schema 未变 |

执行结果:

```
73 passed, 93 warnings in 1.64s
```

---

## 8. 架构不变量验证

所有不变量均通过 `TestInvariants` 自动化校验:

- [x] `persistence/` 不 import `src.personality.*` (regex 严格匹配 import 语句)
- [x] `persistence/` 不 import LLM SDK (openai / qwen / llava / anthropic / google.generativeai)
- [x] `evolution/` 不依赖 `persistence/` (单向)
- [x] `identity_binding` 不依赖 `persistence/` (单向)
- [x] `ResponseEngine.generate()` 签名未修改
- [x] `RuntimeContext.schema_version` 未修改
- [x] `RUNTIME_VERSION` 已更新到 `4.6`

---

## 9. 向后兼容性报告

| 兼容性维度 | 状态 | 说明 |
|------------|------|------|
| 老 `RuntimeCore()` 构造 (无 persistence_runtime 参数) | ✅ | 仍可工作,SELF_MODEL_PERSISTENCE 阶段 no-op |
| 老 `ResponseEngine.generate()` 调用方 | ✅ | 签名零修改 |
| 老 `RuntimeContext` schema | ✅ | schema_version 未变 |
| `src/personality` 核心 | ✅ | 未修改 |
| 旧 Phase 4.1–4.5 测试 | ✅ | 已更新版本白名单兼容 4.6, 全部仍可运行 |
| 已有 `EvolutionRecord` / `SelfModelSnapshot` 数据结构 | ✅ | 复用现有 dataclass,未引入新格式 |
| 已有 `SelfModelEvolutionEngine` 行为 | ✅ | 未修改;通过适配层 (PersistenceRuntime) 协同 |
| 已有 `IdentityBinding` 行为 | ✅ | 未修改,完全独立于 persistence |
| 没有磁盘写权限的运行环境 | ✅ | 启动时 `load` 返回 `None`,不抛异常 |
| 单文件损坏 | ✅ | 隔离到 `_last_error`,不影响其它 identity |

---

## 10. 验收清单 (Acceptance Criteria)

- [x] persistence 目录已创建 (`src/runtime/self_model/persistence/`)
- [x] `SelfModelStore` 已实现
- [x] `EvolutionHistoryStore` 已实现
- [x] `SnapshotManager` 已实现
- [x] `PersistenceRuntime` 已实现
- [x] `SELF_MODEL_PERSISTENCE` 阶段已加入 Runtime
- [x] Runtime 启动恢复可用 (`restore_on_startup`)
- [x] EvolutionRecord 可跨重启存活 (JSONL append-only)
- [x] Rollback 支持 (新 version + 写 history)
- [x] 73 个测试全部通过 (>= 60 要求)
- [x] 架构不变量已验证
- [x] `RUNTIME_VERSION = "4.6"`

---

## 11. 关键设计决策

1. **JSON / JSONL 单一后端** — 默认零依赖、可读、可手编;通过接口允许替换成 SQLite/Redis。
2. **Append-only 历史** — 演化历史不可变,rejected 变更也保留,便于审计与回放。
3. **Snapshot 不可变 + Rollback 新版本** — 永不丢失历史,回滚本身也是一次可观测的演化。
4. **Coordinator 模式** — `PersistenceRuntime` 把三个底层组件粘合成高层 API,RuntimeCore 只依赖协调器,不直接触碰两个 store。
5. **异常隔离** — 每个组件方法都 try/except + `_last_error`,保证局部失败不拖垮整个 Session。
6. **No-op 默认** — 未注入 `persistence_runtime` 时,阶段只跑 hook,保留 Phase 4.1–4.5 所有行为。

---

## 12. 后续可扩展点 (非本阶段范围)

- 多后端: 抽象 `SelfModelStoreBackend`,允许切到 SQLite / S3
- 历史压缩: JSONL 自动 rotate
- 加密落盘: 敏感 preference 字段 AES
- 跨 identity 共享 checkpoint 池
- 与 `MemoryStore` 协同 (本阶段保持解耦)

---

## 13. 结论

Phase 4.6 交付完成。SelfModel 现在具备:

- ✅ **持久化** — 快照落盘 JSON
- ✅ **可追溯** — 演化历史 JSONL append-only
- ✅ **可恢复** — 启动时自动 restore_on_startup
- ✅ **可回滚** — SnapshotManager.rollback 创建新版本快照
- ✅ **可降级** — 未注入时整体 no-op,旧 RuntimeCore 不受影响
- ✅ **可测试** — 73 个用例覆盖组件、集成、不变量

羽依的"自我"现在第一次真正意义上**跨进程存活**。
