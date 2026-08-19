# QianWuYuyi-AI Phase 4.2.2 SelfModel Authority 审计报告

**审计日期**：2026-07-30
**审计性质**：只读研究，未修改任何代码
**目标**：解决 Orchestrator 内部 SelfModelStore 双实例分裂，修复 Prompt【自我认知参考】为空的问题

---

## 执行摘要

本次审计确认 **Orchestrator 内部存在致命的双 SelfModelStore 实例分裂**：

1. **Orchestrator 创建了 2 个独立的 SelfModelStore 实例**，分别用于"写入"和"读取"，但二者从不交叉
2. **resolve() 更新的是 Store #1，而 Prompt 读取的是 Store #2** → 【自我认知参考】永远为空
3. **RuntimeCore 持有的是 SelfModelManager（不同类），不是 SelfModelStore**
4. **SelfModelStore 完全是内存对象，无持久化** → 重启后状态丢失

---

## 1. SelfModelStore 创建路径审计

### 1.1 所有 SelfModelStore 实例化位置

| # | 位置 | 文件:行号 | 持有者 | 用途 |
|---|---|---|---|---|
| **1** | Orchestrator 直接创建 | [orchestrator.py:90](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L90) | `orchestrator.self_model_store` | 被 `self_model_context_provider` 读取（注入 Prompt） |
| **2** | PersonalityResolver 内部创建 | [personality_resolver.py:57](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/personality_resolver.py#L57) | `resolver.self_model_store` | 被 `resolve()` 更新（写入 SelfModel） |
| **3** | RuntimeCore 内部（adapters_enabled 时） | `runtime_core.py:384` → PersonalityResolver → SelfModelStore | RuntimeCore 的 resolver | 仅 adapters_enabled=True 时存在 |
| 4 | GrowthPipeline 内部 | `pipeline.py:65` → PersonalityResolver → SelfModelStore | GrowthPipeline 的 resolver | 独立实例 |
| 5 | PersonalityController fallback | `personality_controller.py:19` → PersonalityResolver → SelfModelStore | Controller | 独立实例 |
| 6 | YuyiPersistence（死代码） | `yuyi_persistence.py:58` | 从未被调用 | - |

### 1.2 Orchestrator 内部的双 Store 分裂（致命缺陷）

```python
# orchestrator.py:89-91
self.personality_resolver = PersonalityResolver()        # ← 内部创建 SelfModelStore #2
self.self_model_store = SelfModelStore()                  # ← SelfModelStore #1（独立）
self.self_model_context_provider = SelfModelContextProvider(store=self.self_model_store)
                                                          # ↑ Provider 绑定 Store #1
```

**数据流断裂分析**：

```
【写入路径】
Orchestrator.process()
  → Step 4: personality_resolver.resolve()           # orchestrator.py:289
    → personality_resolver.py:242-243
      → self.self_model_store.update(...)              # 更新 Store #2（resolver 内部）
      → self.self_model_store.get()                   # 从 Store #2 读取

【读取路径】
Orchestrator.process()
  → Step 8: engine.generate()
    → orchestrator.py:331
      → self.self_model_context_provider.get_context()  # 从 Store #1 读取
        → self.store.get_active_self_model()            # Store #1 永远为 None
          → return ""                                   # 【自我认知参考】为空！
```

**结论**：Store #1（orchestrator.self_model_store）从未被 `update()` 调用，`get_active_self_model()` 始终返回 None，Prompt 中【自我认知参考】块永远为空字符串。

### 1.3 SelfModelStore 与 SelfModelManager 的区别

| 维度 | SelfModelStore | SelfModelManager |
|---|---|---|
| 文件 | [src/personality/self_model_store.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/self_model_store.py) | [src/personality/self_model_manager.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/self_model_manager.py) |
| 使用者 | PersonalityResolver（内部） | RuntimeCore |
| 数据模型 | SelfModel（旧 dict） / SelfModelV3 | SelfIdentity |
| 持久化 | 无（纯内存） | 无（纯内存） |
| 启用条件 | 无条件（PersonalityResolver 总是创建） | `adapters_enabled=True`（默认 False） |
| 关系 | 完全独立，无继承或引用 | 完全独立 |

**关键**：这两个类是**完全不同的实现**，互不相通。RuntimeCore 的 SelfModelManager 与 Orchestrator 的 SelfModelStore 毫无关系。

### 1.4 为什么【自我认知参考】为空

| 步骤 | 操作 | Store | 结果 |
|---|---|---|---|
| 1 | `orchestrator.self_model_store = SelfModelStore()` | Store #1 创建，`_current_model = None` | - |
| 2 | `self_model_context_provider = SelfModelContextProvider(store=Store #1)` | Provider 绑定 Store #1 | - |
| 3 | `personality_resolver = PersonalityResolver()` | Resolver 内部创建 Store #2，`_current_model = None` | - |
| 4 | `personality_resolver.resolve()` 调用 | Store #2.`should_update()` 返回 True（首次） | Store #2.`_current_model` 被设置 |
| 5 | `resolve()` 内部 `self.self_model_store.update(...)` | **Store #2 更新**，Store #1 不变 | Store #1 仍为 None |
| 6 | `self_model_context_provider.get_context()` | 从 **Store #1** 读取 | `get_active_self_model()` 返回 None |
| 7 | `get_context()` 返回 `""` | - | **【自我认知参考】为空** |

---

## 2. 当前写入与读取入口

### 2.1 写入口（更新 SelfModelStore）

| 写入口 | 位置 | 目标 Store | 说明 |
|---|---|---|---|
| `SelfModelStore.update()` | [self_model_store.py:58-73](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/self_model_store.py#L58-L73) | 仅 Store #2（resolver 内部） | 由 `resolve()` 触发 |

**调用链**：
```
PersonalityResolver.resolve()
  → should_update(history)  # 判断是否需要更新
  → update(history, trait_states)  # 构建 SelfModel 并存入 _current_model
```

### 2.2 读取入口

| 读取入口 | 位置 | 源 Store | 说明 |
|---|---|---|---|
| `SelfModelContextProvider.get_context()` | [self_model_context_provider.py:16-53](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/self_model_context_provider.py#L16-L53) | **Store #1**（orchestrator.self_model_store） | **永远返回空字符串** |
| `SelfModelStore.get()` | [self_model_store.py:75-77](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/self_model_store.py#L75-L77) | Store #2（resolver 内部） | resolve() 内部使用 |
| `SelfModelStore.get_active_self_model()` | [self_model_store.py:86-108](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/self_model_store.py#L86-L108) | Store #1 | **被 Provider 调用，返回 None** |

### 2.3 Orchestrator 中所有 self_model 相关调用

| 位置 | 行号 | 调用 | Store |
|---|---|---|---|
| `__init__` | [90](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L90) | `SelfModelStore()` | 创建 Store #1 |
| `__init__` | [91](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L91) | `SelfModelContextProvider(store=Store #1)` | 绑定 Store #1 |
| `process()` | [258](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L258) | `self_model_context_provider.get_context()` | 读 Store #1（空） |
| `process()` | [289](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L289) | `personality_resolver.resolve()` | 写 Store #2 |
| `process()` | [331](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L331) | `self_model_context_provider.get_context()` | 读 Store #1（空） |
| `generate_initiative()` | [742](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L742) | `self_model_context_provider.get_context()` | 读 Store #1（空） |
| `generate_initiative()` | [829](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L829) | `self_model_context_provider.get_context()` | 读 Store #1（空） |

---

## 3. RuntimeCore 能力边界

### 3.1 RuntimeCore 当前不持有 SelfModelStore

**关键发现**：RuntimeCore 持有的是 `SelfModelManager`（不同类），**不是** `SelfModelStore`。

```python
# runtime_core.py:273
self.self_model_manager: Optional[SelfModelManager] = None

# runtime_core.py:401（在 adapters_enabled 条件块内）
self.self_model_manager = SelfModelManager(...)
```

- `SelfModelManager` 是完全独立的类，使用 `SelfIdentity` 数据模型
- `SelfModelStore` 使用 `SelfModel`（旧 dict）/ `SelfModelV3` 数据模型
- 两者无继承、无引用关系

### 3.2 RuntimeCore 的 PersonalityResolver 也持有独立 SelfModelStore

当 `adapters_enabled=True` 时：
```python
# runtime_core.py:384
self.personality_resolver = PersonalityResolver()
# PersonalityResolver 内部创建自己的 SelfModelStore
```

这是**第 3 个独立 SelfModelStore 实例**，与 Orchestrator 的两个完全独立。

### 3.3 SelfModelStore 无持久化

```python
# self_model_store.py
class SelfModelStore:
    def __init__(self, ...):
        self._current_model: Optional[SelfModel] = None  # 纯内存
        self._last_growth_count: int = 0                  # 纯内存
```

- 无 `save()` / `load()` / `persist()` 方法
- 进程重启后所有 SelfModel 状态丢失
- `YuyiPersistence.save_self_model()` 是死代码，从未被调用

---

## 4. 修改方案

### 4.1 设计原则

- **不删除** SelfModelStore 或 SelfModelManager
- **不修改** SelfModelStore 内部逻辑
- **不修改** PersonalityResolver
- **不修改** Memory / Emotion / Relationship
- 保留 fallback 兼容
- 初始化失败不能影响聊天

### 4.2 目标架构

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
    ↓ self_model_context_provider（绑定共享实例）
    ↓ engine.generate(self_model_context=...)  ← 【自我认知参考】有内容
```

### 4.3 修改方案分层

#### 方案 A：最小修复（P0）— 修复双 Store 分裂

**目标**：让 `self_model_context_provider` 绑定到 `personality_resolver` 内部的 SelfModelStore

**修改文件**：
1. `src/orchestrator.py` — 删除 `self.self_model_store = SelfModelStore()`，改为使用 `self.personality_resolver.self_model_store`
2. `src/orchestrator.py` — `SelfModelContextProvider` 绑定到 `personality_resolver.self_model_store`

**风险**：低。仅改变引用来源，不改变 SelfModelStore 内部逻辑。

#### 方案 B：扩展（P1）— 建立权威实例

**目标**：让 RuntimeCore 持有唯一 SelfModelStore，Orchestrator 通过 RuntimeBridge 获取

**修改文件**：
1. `src/runtime/runtime_core.py` — 新增 `get_self_model_store()` 方法（lazy 创建）
2. `src/runtime/runtime_bridge.py` — 新增 `get_self_model_store()` 转发接口
3. `src/orchestrator.py` — 优先通过 RuntimeBridge 获取，fallback 到 `personality_resolver.self_model_store`

**风险**：中。需确保 RuntimeCore 的 SelfModelStore 与 PersonalityResolver 的 SelfModelStore 正确共享。

### 4.4 推荐实施顺序

| 顺序 | 方案 | 优先级 | 说明 |
|---|---|---|---|
| 1 | 方案 B（建立权威实例） | P0 | RuntimeCore lazy 创建 SelfModelStore，Orchestrator 通过 RuntimeBridge 获取 |
| 2 | 方案 A（修复 Prompt 数据链） | P0 | SelfModelContextProvider 绑定到共享实例，resolve() 更新后 Prompt 能读取 |

**两个方案合并实施**，因为方案 B 建立的权威实例需要被 PersonalityResolver 使用才能真正更新。

### 4.5 关键设计决策

| 决策 | 原因 |
|---|---|
| RuntimeCore lazy 创建 SelfModelStore | 不依赖 `adapters_enabled`，确保始终可用 |
| 不修改 PersonalityResolver 内部 | 避免破坏现有 resolve() 逻辑 |
| Orchestrator 通过 RuntimeBridge 获取 | 与 Phase 4.2.1 EmotionManager 模式一致 |
| Fallback 使用 `personality_resolver.self_model_store` | 即使 RuntimeBridge 不可用，也能修复双 Store 分裂 |
| 不添加持久化 | 超出 Phase 4.2.2 范围，保持现状（内存） |

---

## 5. 测试计划

### 5.1 新增测试文件

`tests/test_selfmodel_authority.py`

### 5.2 测试覆盖

| # | 测试类 | 覆盖项 |
|---|---|---|
| 1 | `TestSelfModelStoreShared` | RuntimeCore 与 Orchestrator 使用同一 SelfModelStore |
| 2 | `TestSelfModelUpdateReachesPrompt` | SelfModel 更新后 Prompt 能读取 |
| 3 | `TestSelfModelContextProvider` | Provider 绑定正确实例 |
| 4 | `TestFallbackCompatibility` | Fallback 兼容性 |
| 5 | `TestPhase41Regression` | Phase 4.1/4.2.1 回归保护 |

---

## 6. 风险分析

| 风险 | 严重性 | 缓解措施 |
|---|---|---|
| SelfModelStore 无持久化 | 中 | 超出 Phase 4.2.2 范围，保持现状 |
| PersonalityResolver 内部创建 SelfModelStore | 低 | 通过引用共享，不修改 Resolver |
| RuntimeBridge 未初始化 | 低 | Fallback 到 `personality_resolver.self_model_store` |
| SelfModelManager 与 SelfModelStore 并存 | 低 | 不修改 SelfModelManager，两者独立运行 |

---

**审计完成。等待确认后再编码。**

**分支**：`fix/selfmodel-authority`
**预计修改文件**：3-4 个
**预计新增测试**：10-15 个
