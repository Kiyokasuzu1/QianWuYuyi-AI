# Phase 4.4 RuntimeCore Authority 全量审计

**日期**: 2026-07-30
**状态**: 只读审计完成
**范围**: src/runtime/、src/memory/、src/personality/、src/emotion/、src/growth/、src/relationship/、src/orchestrator.py

---

## 1. 当前 Authority 架构图

```
                            RuntimeCore
                                |
        ┌───────────┬───────────┼───────────┬───────────┬───────────┐
        |           |           |           |           |           |
  EmotionMgr  SelfModelStore  PersResolver  MemStore  VectorMem  GrowthState
    ✅ 4.3.1    ✅ 4.3.1      ✅ 4.3.1     ✅ 4.3.1    ✅ 4.3.2   ✅ 4.3.3
        |           |           |           |           |           |
        |     注入到 Resolver    |           |           |     未注入到 Resolver
        |           ↑           |           |           |           ↓
        |     ⚠️ 未注入 GrowthState           |           |     PersonalityResolver
        |                       |           |           |       内部 fallback 创建
        |                       |           |           |       独立 GrowthState 实例
        |
    统一访问路径：RuntimeBridge
    ├── get_emotion_manager()      ✅
    ├── get_self_model_store()     ✅
    ├── get_personality_resolver() ✅
    ├── get_memory_store()         ✅
    ├── get_vector_memory()        ✅
    └── get_growth_state()         ✅
```

---

## 2. 已完成 Authority

### 2.1 状态总览

| 组件 | Phase | RuntimeCore 方法 | RuntimeBridge 方法 | 状态 |
|---|---|---|---|---|
| EmotionManager | 4.3.1 | `get_emotion_manager()` | `get_emotion_manager()` | ✅ 完成 |
| SelfModelStore | 4.3.1 | `get_self_model_store()` | `get_self_model_store()` | ✅ 完成 |
| PersonalityResolver | 4.3.1 | `get_personality_resolver()` | `get_personality_resolver()` | ✅ 完成 |
| MemoryStore | 4.3.1 | `get_memory_store()` | `get_memory_store()` | ✅ 完成 |
| VectorMemory | 4.3.2 | `get_vector_memory()` | `get_vector_memory()` | ✅ 完成 |
| GrowthState | 4.3.3 | `get_growth_state()` | `get_growth_state()` | ✅ 完成 |

### 2.2 RuntimeBridge 接口完整性

RuntimeBridge 已覆盖全部 6 个 Authority 接口：

```python
# runtime_bridge.py
def get_emotion_manager(self)      → ✅
def get_self_model_store(self)     → ✅
def get_personality_resolver(self) → ✅
def get_memory_store(self)         → ✅
def get_vector_memory(self)       → ✅
def get_growth_state(self)         → ✅
```

---

## 3. 核心状态对象创建路径全量扫描

### 3.1 MemoryStore

| # | 文件 | 行号 | 创建代码 | RuntimeCore 管理 | 实例分裂风险 |
|---|---|---|---|---|---|
| 1 | `src/orchestrator.py` | 101 | `MemoryStore()` | ✅ 优先 Bridge | 否（fallback） |
| 2 | `src/thinking/self_check.py` | 31 | `MemoryStore()` | ❌ 绕过 | **是** |
| 3 | `src/memory/memory_system.py` | 33 | `MemoryStore()` | ❌ 绕过 | **是** |
| 4 | `src/memory/memory_service.py` | 15 | `MemoryStore(...)` | ❌ 绕过 | **是** |
| 5 | `src/context/context_manager.py` | 37 | `MemoryStore()` | ❌ fallback | 低 |
| 6 | `src/runtime/adapters/memory_adapter.py` | 43 | `MemoryStore()` | ❌ fallback | 低 |
| 7 | `src/runtime/runtime_core.py` | 346 | `MemoryStore(...)` | ✅ adapters 路径 | 否 |
| 8 | `src/runtime/runtime_core.py` | 2055 | `MemoryStore(...)` | ✅ 内部路径 | 否 |
| 9 | `src/runtime/runtime_core.py` | 2929 | `_MemoryStore()` | ✅ lazy 创建 | 否 |
| 10 | `src/growth/event_extractor.py` | 27 | `MemoryStore()` | ❌ 绕过 | **是** |
| 11 | `src/growth/topic_tracker.py` | 20 | `MemoryStore()` | ❌ 绕过 | **是** |

**结论**：Orchestrator 已接入 Authority，但 5 个模块仍绕过 RuntimeBridge 直接创建。

### 3.2 VectorMemory

| # | 文件 | 行号 | 创建代码 | RuntimeCore 管理 | 实例分裂风险 |
|---|---|---|---|---|---|
| 1 | `src/orchestrator.py` | 120 | `VectorMemory()` | ✅ 优先 Bridge | 否（fallback） |
| 2 | `src/memory/memory_system.py` | 37 | `VectorMemory()` | ❌ 绕过 | **是** |
| 3 | `src/memory/memory_service.py` | 20 | `VectorMemory()` | ❌ 绕过 | **是** |
| 4 | `src/context/context_manager.py` | 58 | `VectorMemory()` | ✅ 优先 Bridge | 否（fallback） |
| 5 | `src/runtime/runtime_core.py` | 2958 | `_VectorMemory()` | ✅ lazy 创建 | 否 |

**结论**：Orchestrator 和 ContextManager 已接入 Authority，memory_system 和 memory_service 仍绕过。

### 3.3 EmotionManager

| # | 文件 | 行号 | 创建代码 | RuntimeCore 管理 | 实例分裂风险 |
|---|---|---|---|---|---|
| 1 | `src/orchestrator.py` | 206 | `EmotionManager()` | ✅ 优先 Bridge | 否（fallback） |
| 2 | `src/runtime/runtime_core.py` | 637 | `EmotionManager(...)` | ✅ adapters 路径 | 否 |
| 3 | `src/runtime/runtime_core.py` | 2835 | `_EmotionManager()` | ✅ lazy 创建 | 否 |

**结论**：完全接入 Authority，无分裂风险。

### 3.4 SelfModelStore

| # | 文件 | 行号 | 创建代码 | RuntimeCore 管理 | 实例分裂风险 |
|---|---|---|---|---|---|
| 1 | `src/orchestrator.py` | 171 | `SelfModelStore()` | ✅ 优先 Bridge | 否（fallback） |
| 2 | `src/personality/personality_resolver.py` | 57 | `SelfModelStore()` | ❌ Resolver 内部 | **中**（已通过注入修复） |
| 3 | `src/runtime/runtime_core.py` | 2862 | `_SelfModelStore()` | ✅ lazy 创建 | 否 |

**结论**：Orchestrator 已接入 Authority。PersonalityResolver 内部创建的 SelfModelStore 已通过 Orchestrator 属性注入修复（orchestrator.py:177），但 RuntimeCore lazy 创建时也做了注入（runtime_core.py:2894-2896）。

### 3.5 PersonalityResolver

| # | 文件 | 行号 | 创建代码 | RuntimeCore 管理 | 实例分裂风险 |
|---|---|---|---|---|---|
| 1 | `src/orchestrator.py` | 143 | `PersonalityResolver()` | ✅ 优先 Bridge | 否（fallback） |
| 2 | `src/runtime/runtime_core.py` | 384 | `PersonalityResolver()` | ✅ adapters 路径 | 否 |
| 3 | `src/runtime/runtime_core.py` | 2890 | `_Resolver()` | ✅ lazy 创建 | 否 |
| 4 | `src/growth/pipeline.py` | 67 | `PersonalityResolver(...)` | ❌ 独立创建 | **中**（Pipeline 独立运行路径） |
| 5 | `src/personality/personality_controller.py` | 19 | `PersonalityResolver()` | ❌ 绕过 | 低（已标记弃用） |

**结论**：Orchestrator 已接入 Authority。GrowthPipeline 独立运行时自建 PersonalityResolver，但已通过 `state=growth_engine.state` 共享 GrowthState。

### 3.6 GrowthState

| # | 文件 | 行号 | 创建代码 | RuntimeCore 管理 | 实例分裂风险 |
|---|---|---|---|---|---|
| 1 | `src/personality/personality_resolver.py` | 38 | `GrowthState()` | ❌ fallback | **高**（与 RuntimeCore 实例分裂） |
| 2 | `src/growth/growth_engine.py` | 28 | `GrowthState()` | ❌ fallback | **高**（与 RuntimeCore 实例分裂） |
| 3 | `src/runtime/runtime_core.py` | 2986 | `_GrowthState()` | ✅ lazy 创建 | 否 |

**结论**：RuntimeCore 已持有唯一 GrowthState，但 PersonalityResolver 和 GrowthEngine 的 fallback 逻辑仍会创建独立实例。

### 3.7 GrowthEngine

| # | 文件 | 行号 | 创建代码 | RuntimeCore 管理 | 实例分裂风险 |
|---|---|---|---|---|---|
| 1 | `src/growth/pipeline.py` | 50 | `GrowthEngine(state=growth_state)` | ❌ Pipeline 内部 | 低（已支持注入） |

**结论**：GrowthEngine 仅在 GrowthPipeline 中创建，未纳入 RuntimeCore Authority。但已支持 `state` 参数注入。

### 3.8 GrowthPipeline

| # | 文件 | 行号 | 创建代码 | RuntimeCore 管理 |
|---|---|---|---|---|
| 1 | `main.py` | 23 | `GrowthPipeline()` | ❌ 未管理 |

**结论**：GrowthPipeline 仅在 main.py（测试入口）中创建，非生产路径。未纳入 RuntimeCore。

### 3.9 RelationshipState

| # | 文件 | 行号 | 创建代码 | RuntimeCore 管理 | 实例分裂风险 |
|---|---|---|---|---|---|
| 1 | `src/orchestrator.py` | 46 | `_RealRelationshipState(...)` | ❌ | - |
| 2 | `src/orchestrator.py` | 223 | `RelationshipState()` | ❌ | 低（测试用） |
| 3 | `src/personality/personality_resolver.py` | 39 | `RelationshipState()` | ❌ fallback | **中** |
| 4 | `src/growth/pipeline.py` | 63 | `RelationshipState()` | ❌ fallback | **中** |
| 5 | `src/relationship/relationship_repository.py` | 107 | `RelationshipState()` | ❌ | 低（新建默认值） |

**结论**：RelationshipState 未纳入 RuntimeCore Authority。存在多实例风险，但当前生产路径影响有限。

---

## 4. RuntimeCore 生命周期一致性检查

### 4.1 Lazy 创建模式

| 组件 | 缓存变量 | 创建模式 | 一致性 |
|---|---|---|---|
| EmotionManager | `self.emotion_manager` / `_lazy_emotion_manager` | 优先 adapters，fallback lazy | ✅ |
| SelfModelStore | `_lazy_self_model_store` | hasattr + None 检查 | ✅ |
| PersonalityResolver | `self.personality_resolver` / `_lazy_personality_resolver` | 优先 adapters，fallback lazy | ✅ |
| MemoryStore | `_lazy_memory_store` | 优先 adapters，fallback lazy | ✅ |
| VectorMemory | `_lazy_vector_memory` | hasattr + None 检查 | ✅ |
| GrowthState | `_lazy_growth_state` | hasattr + None 检查 | ✅ |

**结论**：所有 lazy 创建模式一致，无重复缓存变量。

### 4.2 初始化顺序

```
RuntimeCore.__init__()
    → 配置加载
    → 状态加载
    → 若 adapters_enabled:
        → MemoryAdapter 创建 → MemoryStore
        → GrowthAdapter 创建
        → PersonalityResolver 创建（adapters 路径）
        → EmotionManager 创建（若 emotion_enabled）
    → Scheduler / ReflectionEngine 等

RuntimeCore.get_xxx() (lazy)
    → 首次调用时创建
    → 无循环依赖
```

**结论**：无初始化顺序问题，无循环依赖。

### 4.3 重复缓存变量检查

RuntimeCore 中存在两个 PersonalityResolver 缓存路径：
- `self.personality_resolver`（adapters_enabled=True 时创建，line 384）
- `self._lazy_personality_resolver`（adapters_enabled=False 时 lazy 创建，line 2890）

**设计意图**：`get_personality_resolver()` 优先返回 `self.personality_resolver`，若为 None 则 lazy 创建。这是正确的双路径设计，非重复缓存。

---

## 5. PersonalityResolver 与 GrowthState 连接检查

### 5.1 关键发现 ⚠️

**RuntimeCore lazy 创建 PersonalityResolver 时未注入共享 GrowthState。**

```python
# runtime_core.py:2888-2896
def get_personality_resolver(self):
    ...
    if not hasattr(self, "_lazy_personality_resolver") or self._lazy_personality_resolver is None:
        self._lazy_personality_resolver = _Resolver()  # ← 未传入 state！

        # 注入了 SelfModelStore ✅
        shared_store = self.get_self_model_store()
        if shared_store:
            self._lazy_personality_resolver.self_model_store = shared_store

        # ⚠️ 但未注入 GrowthState！
        # PersonalityResolver.__init__ 内部 fallback:
        #   self.state = state or GrowthState()  ← 创建了独立实例
```

### 5.2 影响分析

```
RuntimeCore._lazy_growth_state       ← Instance A（Authority 管理）
RuntimeCore._lazy_personality_resolver.state  ← Instance B（fallback 创建）

A ≠ B → 状态分裂！
```

**当前状态**：
- GrowthEngine 写入 Instance A 的数据
- PersonalityResolver 读取 Instance B 的数据
- 两者内存缓存不同步

### 5.3 adapters_enabled=True 路径

```python
# runtime_core.py:384
self.personality_resolver = PersonalityResolver()  # 同样未传入 state
```

adapters 路径也未注入 GrowthState。

### 5.4 修复建议（不在本阶段执行）

在 `get_personality_resolver()` 的 lazy 创建和 adapters 创建路径中，注入 `self.get_growth_state()`：

```python
# 建议修复（Phase 4.4 编码阶段）
shared_gs = self.get_growth_state()
if shared_gs:
    self._lazy_personality_resolver.state = shared_gs
```

---

## 6. GrowthEngine 生命周期检查

### 6.1 当前路径

```
GrowthPipeline.__init__()
    → growth_state 参数（可选）
    → GrowthEngine(state=growth_state)
        → self.state = state or GrowthState()
```

### 6.2 Authority 接入状态

| 路径 | 是否通过 RuntimeBridge | 是否共享 RuntimeCore 实例 |
|---|---|---|
| GrowthPipeline(growth_state=bridge.get_growth_state()) | ✅ 可接入 | ✅ |
| GrowthPipeline()（无参） | ❌ 未接入 | ❌ 独立创建 |
| main.py 中的 GrowthPipeline() | ❌ 未接入 | ❌ 独立创建 |

**结论**：GrowthPipeline 已支持 `growth_state` 参数注入，但默认调用路径未接入 RuntimeBridge。需在调用方（如 main.py 或 Orchestrator）中主动传入。

---

## 7. 绕过 RuntimeBridge 的生产路径

### 7.1 高风险路径

| 模块 | 文件 | 创建对象 | 风险 | 建议 |
|---|---|---|---|---|
| MemorySystem | `src/memory/memory_system.py` | MemoryStore + VectorMemory | **高** | 接入 RuntimeBridge 或由调用方注入 |
| MemoryService | `src/memory/memory_service.py` | MemoryStore + VectorMemory | **高** | 同上 |
| EventExtractor | `src/growth/event_extractor.py` | MemoryStore | **中** | 接入 RuntimeBridge |
| TopicTracker | `src/growth/topic_tracker.py` | MemoryStore | **中** | 接入 RuntimeBridge |
| SelfCheck | `src/thinking/self_check.py` | MemoryStore | **中** | 接入 RuntimeBridge |

### 7.2 低风险路径

| 模块 | 文件 | 创建对象 | 风险 | 原因 |
|---|---|---|---|---|
| ContextManager | `src/context/context_manager.py` | MemoryStore | 低 | 已有 fallback，VectorMemory 已接入 |
| MemoryAdapter | `src/runtime/adapters/memory_adapter.py` | MemoryStore | 低 | RuntimeCore 内部 |
| PersonalityController | `src/personality/personality_controller.py` | PersonalityResolver | 低 | 已标记弃用 |
| relationship_repository | `src/relationship/relationship_repository.py` | RelationshipState | 低 | 仅返回默认值 |

---

## 8. 风险矩阵

| 风险 | 等级 | 影响 | 当前状态 |
|---|---|---|---|
| PersonalityResolver 与 GrowthState 分裂 | **高** | 人格解析读取旧成长数据，人格表现不一致 | ⚠️ 未修复 |
| MemorySystem/MemoryService 绕过 Authority | **高** | 多实例 MemoryStore 写入同一文件，数据覆盖 | ⚠️ 未修复 |
| EventExtractor/TopicTracker 绕过 Authority | **中** | 独立 MemoryStore 实例，不影响主路径 | ⚠️ 未修复 |
| SelfCheck 绕过 Authority | **中** | 独立 MemoryStore 实例，仅自检时使用 | ⚠️ 未修复 |
| GrowthPipeline 未接入 RuntimeBridge | **中** | 独立运行时状态分裂 | ⚠️ 未修复 |
| RelationshipState 未纳入 Authority | **中** | 多实例关系状态不同步 | ⚠️ 未修复 |
| PersonalityController 绕过 Authority | **低** | 已标记弃用 | 可忽略 |

---

## 9. 未完成项

### 9.1 关键未完成项（Phase 4.4 编码阶段建议修复）

| # | 项目 | 优先级 | 复杂度 | 说明 |
|---|---|---|---|---|
| 1 | PersonalityResolver 注入共享 GrowthState | **P0** | 低 | 在 get_personality_resolver() 中注入 get_growth_state() |
| 2 | ContextManager MemoryStore 接入 Authority | **P1** | 低 | 在 _get_memory_store() 中优先 RuntimeBridge |
| 3 | MemorySystem/MemoryService 接入 Authority | **P1** | 中 | 支持外部注入或通过 RuntimeBridge 获取 |
| 4 | EventExtractor/TopicTracker 接入 Authority | **P2** | 低 | 支持外部注入 MemoryStore |
| 5 | SelfCheck 接入 Authority | **P2** | 低 | 支持外部注入 MemoryStore |
| 6 | RelationshipState 纳入 Authority | **P3** | 中 | 新增 get_relationship_state() |
| 7 | GrowthPipeline 接入 Authority | **P3** | 低 | 调用方传入 growth_state |

### 9.2 已完成项

| # | 项目 | Phase | 状态 |
|---|---|---|---|
| 1 | EmotionManager Authority | 4.3.1 | ✅ |
| 2 | SelfModelStore Authority | 4.3.1 | ✅ |
| 3 | PersonalityResolver Authority | 4.3.1 | ✅ |
| 4 | MemoryStore Authority | 4.3.1 | ✅ |
| 5 | VectorMemory Authority | 4.3.2 | ✅ |
| 6 | GrowthState Authority | 4.3.3 | ✅ |

---

## 10. 下一阶段建议

### 10.1 Phase 4.4.1: PersonalityResolver × GrowthState 连接修复（P0）

**目标**：在 RuntimeCore `get_personality_resolver()` 中注入共享 GrowthState。

**修改范围**：
- `src/runtime/runtime_core.py`：lazy 创建和 adapters 路径均注入 `get_growth_state()`

**预期效果**：RuntimeCore 持有的 PersonalityResolver 和 GrowthState 为同一实例，消除状态分裂。

### 10.2 Phase 4.4.2: ContextManager MemoryStore 接入（P1）

**目标**：ContextManager 的 `_get_memory_store()` 优先通过 RuntimeBridge 获取。

### 10.3 Phase 4.4.3: 次要模块 Authority 接入（P2）

**目标**：EventExtractor、TopicTracker、SelfCheck 支持外部注入 MemoryStore。

### 10.4 Phase 4.4.4: RelationshipState Authority 评估（P3）

**目标**：评估是否需要将 RelationshipState 纳入 RuntimeCore Authority。

---

## 11. 审计结论

**审计完成，未修改任何代码。**

### 核心发现

1. **RuntimeBridge 接口完整**：6 个 Authority 方法全部覆盖 ✅
2. **PersonalityResolver × GrowthState 分裂**：RuntimeCore lazy 创建 PersonalityResolver 时未注入共享 GrowthState ⚠️（P0 修复项）
3. **5 个模块绕过 Authority**：MemorySystem、MemoryService、EventExtractor、TopicTracker、SelfCheck 直接创建 MemoryStore/VectorMemory ⚠️
4. **RelationshipState 未纳入 Authority**：存在多实例风险，但当前影响有限
5. **Lazy 创建模式一致**：所有缓存变量使用相同的 hasattr + None 检查模式 ✅
6. **无循环依赖**：初始化顺序正确 ✅

### 当前 Authority 完成度

```
RuntimeCore Authority 体系（Phase 4.3.1 ~ 4.3.3）
|
├── EmotionManager          ✅  完成
├── SelfModelStore          ✅  完成
├── PersonalityResolver     ✅  完成（⚠️ 内部 GrowthState 未注入）
├── MemoryStore             ✅  完成（⚠️ 5 个模块绕过）
├── VectorMemory            ✅  完成（⚠️ 2 个模块绕过）
├── GrowthState             ✅  完成（⚠️ 未注入到 Resolver）
|
├── RelationshipState       ❌  未纳入
├── GrowthEngine            ❌  未纳入（通过 GrowthState 间接管理）
└── GrowthPipeline          ❌  未纳入（非生产路径）
```

**下一步**：等待指令进入 Phase 4.4 编码阶段，优先修复 P0 项（PersonalityResolver × GrowthState 连接）。
