# QianWuYuyi-AI Phase 4.2.2 SelfModel Authority 变更报告

**日期**：2026-07-30
**分支**：`fix/selfmodel-authority`
**目标**：解决 Orchestrator 内部 SelfModelStore 双实例分裂，修复 Prompt【自我认知参考】为空的问题

---

## 1. 修改文件列表

| # | 文件 | 修改类型 | 说明 |
|---|---|---|---|
| 1 | [src/runtime/runtime_core.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_core.py) | 修改 | 新增 `get_self_model_store()` 方法（lazy 创建权威实例） |
| 2 | [src/runtime/runtime_bridge.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_bridge.py) | 修改 | 新增 `get_self_model_store()` 转发接口 |
| 3 | [src/orchestrator.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py) | 修改 | 优先通过 RuntimeBridge 获取 SelfModelStore，并注入到 PersonalityResolver |
| 4 | [tests/test_selfmodel_authority.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/tests/test_selfmodel_authority.py) | 新增 | 22 个测试覆盖 6 类验证场景 |

**总计**：修改 3 个文件，新增 1 个测试文件

---

## 2. 架构变化

### 2.1 修改前（双 Store 分裂）

```
┌─────────────────────────────────────────────────────────┐
│  Orchestrator                                            │
│                                                          │
│  ┌─ personality_resolver                                  │
│  │   └─ self_model_store = SelfModelStore()  ← Store #2  │
│  │       └─ update() 被调用（resolve() 时写入）          │
│  │                                                        │
│  ├─ self_model_store = SelfModelStore()      ← Store #1  │
│  │   └─ 从未被 update()，_current_model 始终为 None      │
│  │                                                        │
│  └─ self_model_context_provider                            │
│      └─ store = Store #1（永远读到 None）                │
│          └─ get_context() → ""（【自我认知参考】为空）    │
└─────────────────────────────────────────────────────────┘

问题：Store #1（读取）和 Store #2（写入）从不交叉，Prompt 永远为空
```

### 2.2 修改后（单一权威实例 + 注入共享）

```
┌─────────────────────────────────────────────────────────┐
│  RuntimeCore（唯一权威来源）                             │
│  └─ get_self_model_store()  ← lazy 创建权威实例          │
│      └─ SelfModelStore（唯一实例）                       │
└─────────────────────────────────────────────────────────┘
              ↑ get_self_model_store()
              │
┌─────────────────────────────────────────────────────────┐
│  RuntimeBridge（转发层）                                 │
│  └─ get_self_model_store() → RuntimeCore.get_self_      │
│       model_store()                                     │
└─────────────────────────────────────────────────────────┘
              ↑ get_runtime_bridge().get_self_model_store()
              │
┌─────────────────────────────────────────────────────────┐
│  Orchestrator                                            │
│  └─ self_model_store  ← 引用 RuntimeCore 的实例          │
│      │                                                     │
│      ↓ 注入到 personality_resolver.self_model_store       │
│      │   （resolve() 更新的就是共享实例）                 │
│      │                                                     │
│  └─ self_model_context_provider                            │
│      └─ store = self_model_store（共享实例）              │
│          └─ get_context() → 非空（【自我认知参考】有内容）│
└─────────────────────────────────────────────────────────┘

目标架构实现：
RuntimeCore → SelfModelStore → RuntimeBridge → Orchestrator
    → RuntimeContext → engine.generate() → 【自我认知参考】
```

### 2.3 关键设计决策

| 决策 | 原因 |
|---|---|
| RuntimeCore 持有 SelfModelStore 所有权 | RuntimeCore 是长期状态中心，符合 Phase 4.2 目标 |
| `get_self_model_store()` 采用 lazy 创建 | `adapters_enabled` 默认 False，但 SelfModelStore 应始终可用 |
| 优先使用 PersonalityResolver 内部的 SelfModelStore | `adapters_enabled=True` 时 RuntimeCore 已有 resolver，复用其 store |
| Orchestrator 将共享 store 注入到 PersonalityResolver | **核心修复**：让 resolve() 更新共享实例，Provider 才能读取到 |
| 不修改 PersonalityResolver 代码 | 通过属性赋值注入，避免破坏现有 resolve() 逻辑 |
| Orchestrator 保留 fallback 到 resolver 内部 store | 保证 RuntimeBridge 不可用时也能修复双 Store 分裂 |
| 不添加持久化 | 超出 Phase 4.2.2 范围，保持现状（内存） |

---

## 3. 详细修改内容

### 3.1 RuntimeCore.get_self_model_store()（新增方法）

**位置**：[src/runtime/runtime_core.py:2841-2866](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_core.py#L2841-L2866)

```python
def get_self_model_store(self) -> Optional["SelfModelStore"]:
    """
    获取 SelfModelStore 权威实例（Phase 4.2.2 SelfModel Authority）。

    - 若 adapters_enabled=True 且 PersonalityResolver 已创建，则返回其内部的 self_model_store
    - 否则 lazy 创建一个基础 SelfModelStore，作为 Orchestrator 的权威来源
    - 创建失败返回 None，调用方需自行 fallback

    设计意图：让 RuntimeCore 成为 SelfModelStore 的唯一持有者，
    Orchestrator 不再主动创建 SelfModelStore，而是通过 RuntimeBridge 获取引用。
    这解决了 Orchestrator 内部双 Store 分裂导致【自我认知参考】为空的问题。
    """
    # 优先使用 PersonalityResolver 内部的 SelfModelStore（若已创建）
    if self.personality_resolver is not None and hasattr(self.personality_resolver, "self_model_store"):
        return self.personality_resolver.self_model_store

    # Lazy 创建基础 SelfModelStore（不依赖 adapters_enabled 开关）
    try:
        from src.personality.self_model_store import SelfModelStore as _SelfModelStore
        if not hasattr(self, "_lazy_self_model_store") or self._lazy_self_model_store is None:
            logger.info("RuntimeCore: lazy 创建基础 SelfModelStore（SelfModel Authority）")
            self._lazy_self_model_store = _SelfModelStore()
        return self._lazy_self_model_store
    except Exception as _e:
        logger.warning(f"RuntimeCore: SelfModelStore lazy 创建失败: {_e}")
        return None
```

**行为**：
- 若 `adapters_enabled=True` 且 PersonalityResolver 已创建，返回 resolver 内部的 store
- 否则 lazy 创建基础 SelfModelStore，并缓存到 `_lazy_self_model_store`
- 后续调用返回缓存的实例
- 创建失败返回 None，调用方自行 fallback

---

### 3.2 RuntimeBridge.get_self_model_store()（新增方法）

**位置**：[src/runtime/runtime_bridge.py:271-290](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_bridge.py#L271-L290)

```python
def get_self_model_store(self) -> Any:
    """
    获取 RuntimeCore 持有的 SelfModelStore 权威实例。

    Phase 4.2.2 SelfModel Authority：
    - RuntimeCore 保留 SelfModelStore 所有权
    - Orchestrator 不再主动创建 SelfModelStore，而是通过本方法获取引用
    - 若 RuntimeCore 未初始化或创建失败，返回 None（调用方需 fallback）
    - 这解决了 Orchestrator 内部双 Store 分裂导致【自我认知参考】为空的问题

    Returns:
        SelfModelStore 实例，或 None
    """
    if not self._runtime_core:
        return None
    try:
        return self._runtime_core.get_self_model_store()
    except Exception as e:
        logger.warning(f"RuntimeBridge.get_self_model_store 失败: {e}")
        return None
```

**行为**：
- RuntimeCore 未初始化时返回 None
- 转发到 `runtime_core.get_self_model_store()`
- 异常时返回 None

---

### 3.3 Orchestrator.__init__()（修改 SelfModelStore 初始化）

**位置**：[src/orchestrator.py:91-123](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L91-L123)

```python
# SelfModelStore（Phase 4.2.2 SelfModel Authority）
# 修复双 Store 分裂：确保 Orchestrator、Provider、Resolver 使用同一实例
# 优先级：1. RuntimeBridge 共享实例  2. personality_resolver.self_model_store  3. fallback 自建
self.self_model_store = None
try:
    from src.runtime.runtime_bridge import get_runtime_bridge
    _bridge = get_runtime_bridge()
    _shared_store = _bridge.get_self_model_store()
    if _shared_store is not None:
        self.self_model_store = _shared_store
        print("[Orchestrator] SelfModelStore 已从 RuntimeBridge 获取（共享 RuntimeCore 实例）")
except Exception as e:
    print(f"[Orchestrator] 通过 RuntimeBridge 获取 SelfModelStore 失败: {e}")

# Fallback 1：使用 PersonalityResolver 内部的 SelfModelStore（修复双 Store 分裂）
if self.self_model_store is None and hasattr(self.personality_resolver, "self_model_store"):
    self.self_model_store = self.personality_resolver.self_model_store
    print("[Orchestrator] SelfModelStore 使用 personality_resolver 内部实例（修复双 Store 分裂）")

# Fallback 2：RuntimeBridge 和 Resolver 都不可用时自建（保持兼容）
if self.self_model_store is None:
    self.self_model_store = SelfModelStore()
    print("[Orchestrator] SelfModelStore fallback 自建")

# 关键修复：将共享 SelfModelStore 注入到 PersonalityResolver
# 这样 resolve() 更新的就是共享实例，Provider 也能读取到
# 不修改 PersonalityResolver 代码，只通过属性赋值注入
if hasattr(self.personality_resolver, "self_model_store"):
    self.personality_resolver.self_model_store = self.self_model_store

# SelfModelContextProvider 绑定到共享的 SelfModelStore
# 关键修复：Provider 现在读取的是 resolve() 更新的同一实例
self.self_model_context_provider = SelfModelContextProvider(store=self.self_model_store)
```

**行为**（三级 fallback）：
1. **优先**：从 RuntimeBridge 获取 RuntimeCore 的共享实例
2. **Fallback 1**：使用 `personality_resolver.self_model_store`（修复双 Store 分裂的核心）
3. **Fallback 2**：RuntimeBridge 和 Resolver 都不可用时自建（保持兼容）
4. **关键注入**：将共享 store 注入到 `personality_resolver.self_model_store`，使 resolve() 更新共享实例
5. **Provider 绑定**：`SelfModelContextProvider` 绑定到共享 store
6. 所有异常都被捕获，不影响聊天

---

## 4. 测试结果

### 4.1 新增测试

```
tests/test_selfmodel_authority.py
======================= 22 passed, 17 warnings in 0.51s =======================
```

| 测试类 | 测试数 | 覆盖项 |
|---|---|---|
| TestSelfModelStoreShared | 4 | RuntimeCore 与 Orchestrator 使用同一 SelfModelStore |
| TestSelfModelUpdateReachesPrompt | 4 | SelfModel 更新后 Prompt 能读取（核心修复） |
| TestSelfModelContextProviderBinding | 3 | Provider 绑定正确实例，无双 Store 分裂 |
| TestFallbackCompatibility | 3 | Fallback 兼容性 |
| TestPhaseRegression | 4 | Phase 4.1/4.2.1 回归保护 |
| TestRuntimeCoreLazyCreation | 4 | RuntimeCore lazy 创建行为 |

### 4.2 Phase 4.2.1 回归测试

```
tests/test_emotion_authority.py
======================= 16 passed in 1.14s =======================
```

| 测试类 | 测试数 | 覆盖项 |
|---|---|---|
| TestEmotionManagerShared | 3 | EmotionManager 共享仍正常 |
| TestEmotionUpdateNoDoubleWrite | 3 | 情绪更新只产生一次状态变化 |
| TestEmotionStateFileNoDoubleWrite | 2 | emotion_state.json 不被双写 |
| TestRuntimeBridgeEmotionForwarding | 4 | RuntimeBridge 转发正确 |
| TestFallbackCompatibility | 3 | Fallback 兼容性 |
| TestPhase41Regression | 1 | Phase 4.1 情绪闭环回归 |

### 4.3 Phase 4.1 回归测试

```
tests/test_runtime_unification.py
23 passed, 2 failed in 1.17s
```

| 测试结果 | 数量 | 说明 |
|---|---|---|
| 通过 | 23 | Phase 4.1 大部分测试通过 |
| 失败 | 2 | `TestVectorRealtimeUpdate` 的 2 个测试 |

**2 个失败的预存问题说明**：

| 失败测试 | 原因 |
|---|---|
| `test_add_memory_then_search` | fake ChromaDB 的 `query()` 返回空列表，无法匹配搜索词 |
| `test_vector_search_after_multiple_adds` | 同上，fake ChromaDB 不支持语义搜索 |

**这 2 个失败是预存问题**：
- 失败发生在 `_FakeChromaCollection.query()` 方法，该方法硬编码返回 `{"documents": [[]], ...}`（空结果）
- 与 Phase 4.2.2 SelfModel Authority 修改完全无关
- Phase 4.2.1 报告中记录的回归测试也是同样情况
- 在 `test_selfmodel_authority.py` 的 `test_vector_memory_still_works` 中已通过验证 count 来规避此 mock 限制

---

## 5. Prompt 数据链修复验证

### 5.1 修复前的数据链（断裂）

```
SelfModelStore #2（resolver 内部）
    ↓ update()         ← resolve() 调用，更新 Store #2
    ↓ get()            ← resolve() 内部读取 Store #2
    │
    ✗ 断裂 ✗           ← Store #1 和 Store #2 是不同实例
    │
SelfModelContextProvider
    ↓ get_context()    ← 从 Store #1 读取（_current_model 为 None）
    ↓ get_active_self_model() → None
    │
RuntimeContext.assemble_context()
    ↓ self_model_snapshot = ""
    │
engine.generate()
    ↓ self_model_context = ""
    │
【自我认知参考】→ 空字符串
```

### 5.2 修复后的数据链（完整）

```
SelfModelStore（共享唯一实例）
    ↓ update()         ← resolve() 调用，更新共享实例
    ↓ get()            ← resolve() 内部读取共享实例
    │
    ✓ 连接 ✓           ← Provider 和 Resolver 使用同一实例
    │
SelfModelContextProvider
    ↓ get_context()    ← 从共享实例读取（_current_model 非 None）
    ↓ get_active_self_model() → SelfModelV3
    │
RuntimeContext.assemble_context()
    ↓ self_model_snapshot = "【自我认知参考】\n身份：..."
    │
engine.generate()
    ↓ self_model_context = "【自我认知参考】\n身份：..."
    │
【自我认知参考】→ 非空，包含身份、性格维度、信念、成长叙事
```

### 5.3 验证项

| 验证项 | 状态 | 测试 |
|---|---|---|
| RuntimeCore 和 Orchestrator 引用同一 SelfModelStore | ✅ | `test_orchestrator_uses_runtime_core_self_model_store` |
| 多个 Orchestrator 共享同一 SelfModelStore | ✅ | `test_self_model_store_identity_across_multiple_orchestrators` |
| RuntimeBridge 转发正确 | ✅ | `test_runtime_bridge_get_self_model_store_returns_core_instance` |
| Provider 绑定到共享 store | ✅ | `test_context_provider_binds_to_shared_store` |
| resolve() 更新共享 store | ✅ | `test_resolve_updates_shared_store` |
| resolve() 后 Provider 能读取非空内容 | ✅ | `test_context_provider_reads_after_resolve` |
| engine.generate() 收到非空 self_model_context | ✅ | `test_engine_receives_non_empty_self_model_context` |
| context 包含身份信息 | ✅ | `test_resolve_then_context_contains_identity` |
| Provider store 与 Resolver store 相同 | ✅ | `test_provider_store_is_resolver_store` |
| 无双 Store 分裂 | ✅ | `test_no_dual_store_split` |
| Fallback 修复双 Store 分裂 | ✅ | `test_orchestrator_fallback_to_resolver_store` |
| Fallback 不影响聊天 | ✅ | `test_orchestrator_fallback_does_not_crash` |
| Fallback 模式 resolve() 后 context 非空 | ✅ | `test_fallback_context_non_empty_after_resolve` |
| Phase 4.2.1 EmotionManager 共享不受影响 | ✅ | `test_emotion_manager_still_shared` |
| Phase 4.1 情绪闭环不受影响 | ✅ | `test_emotion_context_reaches_engine` |
| Phase 4.1 RuntimeContext key 完整性 | ✅ | `test_runtime_context_keys_still_complete` |
| Phase 4.1 向量索引仍正常 | ✅ | `test_vector_memory_still_works` |
| Lazy 创建（adapters_enabled=False 时） | ✅ | `test_lazy_creates_when_adapters_disabled` |
| 多次调用返回同一实例 | ✅ | `test_lazy_creates_same_instance_on_multiple_calls` |
| 创建失败返回 None | ✅ | `test_returns_none_on_failure` |
| Bridge 未初始化返回 None | ✅ | `test_bridge_returns_none_when_not_initialized` |

---

## 6. 当前闭环状态

### 6.1 SelfModel Authority 闭环（已建立）

```
RuntimeCore
    ↓ get_self_model_store()  ← lazy 创建权威实例
    │
SelfModelStore（唯一实例）
    ↓ update()                ← 被 PersonalityResolver.resolve() 调用
    ↓ get_active_self_model() ← 被 SelfModelContextProvider 读取
    │
RuntimeBridge
    ↓ get_self_model_store()  ← 转发
    │
Orchestrator
    ↓ self_model_store（引用共享实例）
    ↓ 注入到 personality_resolver.self_model_store
    ↓ self_model_context_provider（绑定共享实例）
    ↓ engine.generate(self_model_context=...)  ← 【自我认知参考】有内容
```

### 6.2 关于"重启后状态保持"

SelfModelStore 当前是纯内存对象，无持久化机制（无 `save()` / `load()` 方法）。这意味着进程重启后 SelfModel 状态会丢失，但：
- `resolve()` 在首次调用时会重建 SelfModel（`should_update()` 返回 True）
- 这是**预存行为**，不属于 Phase 4.2.2 修改范围
- 添加持久化超出 Phase 4.2.2 目标（不重构、不新增无关功能）
- 测试 `test_resolve_updates_shared_store` 验证了 resolve() 能重建 SelfModel

---

## 7. 未做的事项（符合约束）

- ❌ 不删除任何模块
- ❌ 不修改 Memory / Emotion / Relationship
- ❌ 不修改 PersonalityResolver 内部代码（仅通过属性赋值注入）
- ❌ 不修改 SelfModelStore 内部逻辑
- ❌ 不修改 SelfModelContextProvider
- ❌ 不改变 QQ 接入
- ❌ 不修改 API Key
- ❌ 不添加持久化（超出范围）
- ❌ 不重构 SelfModelManager（两者独立运行）

---

## 8. 后续建议

### 8.1 Phase 4.2.3：PersonalityResolver Authority

**问题**：4 处独立 PersonalityResolver 实例
- Orchestrator 内部创建
- RuntimeCore 内部创建（adapters_enabled=True 时）
- GrowthPipeline 内部创建
- PersonalityController fallback 创建

**修复方案**：
1. RuntimeBridge 新增 `get_personality_resolver()` 方法
2. RuntimeCore 启用 PersonalityResolver（或 lazy 创建）
3. Orchestrator 通过 RuntimeBridge 获取，fallback 自建
4. 将共享 SelfModelStore 注入到共享 PersonalityResolver

### 8.2 Phase 4.2.4：tick 状态回流

**问题**：RuntimeCore 的 tick 修改的 self_state 不传递给 Orchestrator

**修复方案**：
1. RuntimeBridge 新增 `get_self_state_snapshot()` 方法
2. Orchestrator.process() 中获取 self_state 快照
3. RuntimeContext 新增 `runtime_state` key

### 8.3 技术债提醒

- SelfModelStore 无持久化，重启后状态丢失（resolve() 会重建）
- SelfModelManager 与 SelfModelStore 并存，两者独立运行（不修改）
- `adapters_enabled` 默认 False，SelfModelStore 通过 lazy 创建始终可用
- 若未来启用 `adapters_enabled=True`，RuntimeCore 的 PersonalityResolver 会创建自己的 SelfModelStore，`get_self_model_store()` 会优先返回该实例（行为一致）

---

## 9. 风险评估

| 风险 | 严重性 | 缓解措施 |
|---|---|---|
| RuntimeBridge 单例在 Orchestrator 之前未初始化 | 低 | Fallback 1 使用 resolver 内部 store，仍修复双 Store 分裂 |
| 注入共享 store 到 PersonalityResolver 可能影响 resolve() | 低 | 仅替换 store 引用，不修改 resolve() 逻辑，测试验证通过 |
| Lazy 创建的 SelfModelStore 与 adapters_enabled=True 时的 store 不同 | 低 | `get_self_model_store()` 优先返回 resolver 的 store，行为一致 |
| SelfModelStore 无持久化 | 中 | 预存行为，resolve() 会重建，超出 Phase 4.2.2 范围 |
| SelfModelManager 与 SelfModelStore 并存 | 低 | 两者完全独立，互不影响 |

---

**Phase 4.2.2 SelfModel Authority 完成。**

**核心修复**：通过将共享 SelfModelStore 注入到 PersonalityResolver，使 resolve() 更新共享实例，Provider 能读取到内容，修复了【自我认知参考】为空的问题。

**等待审计通过后再进入 Phase 4.2.3。**
