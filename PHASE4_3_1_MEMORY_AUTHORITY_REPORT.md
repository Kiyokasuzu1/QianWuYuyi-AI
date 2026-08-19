# Phase 4.3.1 MemoryStore Authority 实施报告

## 1. 目标

建立 RuntimeCore 对 MemoryStore 的 Authority，解决多实例 MemoryStore 分裂风险：
- 当 RuntimeBridge 可用时，Orchestrator 与 RuntimeCore 共享同一个 MemoryStore 实例
- 避免多个 MemoryStore 并发写入 `data/memory.json` 导致的数据覆盖和丢失
- 保持与 Phase 4.2.x（Emotion/SelfModel/Personality Authority）一致的架构模式

## 2. 修改范围

### 2.1 新增/修改文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/runtime/runtime_core.py` | 新增 | `get_memory_store()` 方法，lazy 创建并缓存唯一实例 |
| `src/runtime/runtime_bridge.py` | 新增 | `get_memory_store()` 方法，转发 RuntimeCore 实例 |
| `src/orchestrator.py` | 修改 | MemoryStore 初始化：优先 RuntimeBridge，fallback 自建 |
| `tests/test_memory_authority.py` | 新增 | 22 个测试用例，覆盖共享/持久化/上下文/fallback/回归 |

### 2.2 禁止修改（遵守审计报告约束）

- MemoryStore 类内部（`src/memory/memory_store.py`）
- GrowthPipeline、VectorMemory、MemoryService
- QQ/API 接入层

## 3. 实现细节

### 3.1 RuntimeCore.get_memory_store()

```python
def get_memory_store(self) -> Optional["MemoryStore"]:
    # 1. 优先使用 adapters_enabled=True 时 MemoryAdapter 的 store
    if hasattr(self, "memory_adapter") and self.memory_adapter is not None:
        adapter_store = getattr(self.memory_adapter, "get_memory_store", lambda: None)()
        if adapter_store is not None:
            return adapter_store

    # 2. Lazy 创建基础 MemoryStore，缓存到 _lazy_memory_store
    try:
        from src.memory.memory_store import MemoryStore as _MemoryStore
        if not hasattr(self, "_lazy_memory_store") or self._lazy_memory_store is None:
            self._lazy_memory_store = _MemoryStore()
        return self._lazy_memory_store
    except Exception as _e:
        logger.warning(f"RuntimeCore: MemoryStore lazy 创建失败: {_e}")
        return None
```

**关键设计**：
- 与 `adapters_enabled` 开关无关：无论 adapters 是否启用，都能提供权威实例
- 优先返回 MemoryAdapter 的 store（当 RuntimeCore 已初始化完整适配层时）
- 失败返回 None，调用方自行 fallback（保持容错）

### 3.2 RuntimeBridge.get_memory_store()

```python
def get_memory_store(self) -> Any:
    if not self._runtime_core:
        return None
    try:
        return self._runtime_core.get_memory_store()
    except Exception as e:
        logger.warning(f"RuntimeBridge.get_memory_store 失败: {e}")
        return None
```

**设计意图**：
- RuntimeCore 保留 MemoryStore 所有权
- Orchestrator 不再主动创建 MemoryStore，而是通过本方法获取引用
- 解决多实例 MemoryStore 并发写入导致的数据覆盖和分裂风险

### 3.3 Orchestrator 初始化修改

**修改前**：
```python
self.memory_store = MemoryStore()
```

**修改后**：
```python
# 优先通过 RuntimeBridge 获取 RuntimeCore 持有的权威实例
self.memory_store = None
try:
    from src.runtime.runtime_bridge import get_runtime_bridge
    _bridge = get_runtime_bridge()
    _shared_store = _bridge.get_memory_store()
    if _shared_store is not None:
        self.memory_store = _shared_store
        print("[Orchestrator] MemoryStore 已从 RuntimeBridge 获取（共享 RuntimeCore 实例）")
except Exception as e:
    print(f"[Orchestrator] 通过 RuntimeBridge 获取 MemoryStore 失败: {e}")

# Fallback：RuntimeBridge 不可用时自建（保持向后兼容）
if self.memory_store is None:
    self.memory_store = MemoryStore()
    print("[Orchestrator] MemoryStore fallback 自建")
```

## 4. 测试覆盖

### 4.1 测试执行结果

```
pytest tests/test_memory_authority.py -v
=========================== 22 passed, 18 warnings in 0.87s =======================
```

### 4.2 测试分类

| 测试类 | 用例数 | 覆盖目标 |
|--------|--------|----------|
| `TestMemoryStoreShared` | 4 | RuntimeCore 与 Orchestrator 使用同一个 MemoryStore；多次调用返回同一实例 |
| `TestMemoryPersistence` | 4 | add/load 正常；持久化到文件；get_by_user 过滤；count 正确 |
| `TestMemoryReachesContext` | 2 | MemoryStore 数据能通过 process() 进入 engine.generate；新记忆被保存 |
| `TestRuntimeBridgeMemoryForwarding` | 4 | Bridge 未初始化返回 None；初始化后返回 MemoryStore；多次调用同一实例；lazy 创建 |
| `TestFallbackCompatibility` | 3 | Bridge 不可用时 fallback 自建；不崩溃；fallback 持久化正常 |
| `TestCrossPhaseRegression` | 5 | Emotion/SelfModel/Personality Authority 不受影响；情绪/自我认知上下文仍传递到 engine |

### 4.3 回归测试

```
pytest tests/test_emotion_authority.py tests/test_selfmodel_authority.py tests/test_personality_authority.py -v
=========================== 59 passed, 47 warnings in 1.35s =======================
```

Phase 4.2.1/4.2.2/4.2.3 的全部 59 个回归测试通过，确认 Memory Authority 未破坏现有功能。

## 5. 架构一致性

Phase 4.3.1 采用与 Phase 4.2.x 完全一致的 Authority 模式：

| 组件 | Phase 4.2.1 Emotion | Phase 4.2.2 SelfModel | Phase 4.2.3 Personality | Phase 4.3.1 Memory |
|------|---------------------|-----------------------|-------------------------|--------------------|
| RuntimeCore lazy 创建 | get_emotion_manager() | get_self_model_store() | get_personality_resolver() | get_memory_store() |
| RuntimeBridge 转发 | get_emotion_manager() | get_self_model_store() | get_personality_resolver() | get_memory_store() |
| Orchestrator 优先获取 | emotion_manager | self_model_store | personality_resolver | memory_store |
| Fallback 自建 | EmotionManager() | SelfModelStore() | PersonalityResolver() | MemoryStore() |

## 6. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| RuntimeBridge 未初始化导致聊天功能中断 | Orchestrator 保留 fallback 自建逻辑，Bridge 不可用时自动降级 |
| adapters_enabled=True 时 MemoryAdapter 的 store 与 lazy store 不一致 | RuntimeCore 优先返回 MemoryAdapter.get_memory_store()，确保使用适配层创建的实例 |
| 多 Orchestrator 实例仍创建多个 MemoryStore | RuntimeCore 缓存 `_lazy_memory_store`，所有 Orchestrator 通过 Bridge 获取同一引用 |
| 回滚需求 | 仅修改初始化路径，不修改 MemoryStore API 或内部逻辑，回滚只需恢复 Orchestrator 的三行初始化代码 |

## 7. 结论

Phase 4.3.1 MemoryStore Authority 实施完成：
- **22 个新增测试全部通过**
- **59 个回归测试全部通过**
- **0 个破坏性变更**
- **与 Phase 4.2.x 架构模式完全一致**

多实例 MemoryStore 分裂风险已消除。RuntimeCore 成为 MemoryStore 的唯一权威来源，Orchestrator 通过 RuntimeBridge 获取共享引用，fallback 机制保证兼容性。
