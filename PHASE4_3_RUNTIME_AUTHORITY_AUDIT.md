# QianWuYuyi-AI Phase 4.3 Runtime Authority Integration 审计报告

**审计日期**：2026-07-30
**审计性质**：只读研究，未修改任何代码
**目标**：审计 RuntimeCore 当前 Authority 状态，搜索 Memory/Relationship/Growth 创建路径，分析剩余分裂风险

---

## 1. RuntimeCore 当前 Authority 状态（Phase 4.2 已完成）

### 1.1 三权分立完成矩阵

| 模块 | Authority 方法 | RuntimeCore 持有 | Orchestrator 共享 | 测试通过 |
|---|---|---|---|---|
| **EmotionManager** | `get_emotion_manager()` | ✅ lazy 创建 | ✅ 共享引用 | 16/16 ✅ |
| **SelfModelStore** | `get_self_model_store()` | ✅ lazy 创建 + 注入 resolver | ✅ 共享引用 | 22/22 ✅ |
| **PersonalityResolver** | `get_personality_resolver()` | ✅ lazy 创建 + 注入 SelfModelStore | ✅ 共享引用 | 20/20 ✅ |

### 1.2 已修复的分裂问题

| 问题 | Phase | 修复方案 |
|---|---|---|
| EmotionManager 双实例写冲突 | 4.2.1 | RuntimeCore 持有权威实例，Orchestrator 共享引用 |
| SelfModelStore 双 Store 分裂（【自我认知参考】为空） | 4.2.2 | RuntimeCore 持有权威实例，注入到 PersonalityResolver |
| PersonalityResolver 多实例（TraitState/GrowthHistory 分裂） | 4.2.3 | RuntimeCore 持有权威实例，Orchestrator 共享引用 |

---

## 2. Memory 创建路径审计

### 2.1 MemoryStore 创建路径

| # | 位置 | 文件:行号 | 持有者 | 初始化参数 | 活跃状态 | 持久化路径 |
|---|---|---|---|---|---|---|
| **1** | Orchestrator 直接创建 | [orchestrator.py:87](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L87) | `orchestrator.memory_store` | 无参（默认） | **生产活跃** | data/memory.json |
| 2 | GrowthPipeline EventExtractor | [event_extractor.py:27](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/event_extractor.py#L27) | `event_extractor.store` | 无参（默认） | **独立流程** | data/memory.json |
| 3 | GrowthPipeline TopicTracker | [topic_tracker.py:20](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/topic_tracker.py#L20) | `topic_tracker.store` | 无参（默认） | **独立流程** | data/memory.json |
| 4 | self_check.py lazy 创建 | [self_check.py:31](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/thinking/self_check.py#L31) | `self_check._memory_store` | 无参（默认） | 低活跃度 | data/memory.json |

### 2.2 VectorMemory 创建路径

| # | 位置 | 文件:行号 | 持有者 | 初始化参数 | 活跃状态 | 持久化路径 |
|---|---|---|---|---|---|---|
| **1** | Orchestrator 直接创建 | [orchestrator.py:88](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L88) | `orchestrator.vector_memory` | 无参（默认） | **生产活跃** | data/vectors.json |
| 2 | MemoryService lazy 创建 | [memory_service.py:20](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_service.py#L20) | `memory_service.vector` | 无参（默认） | 中活跃度 | data/vectors.json |
| 3 | MemorySystem lazy 创建 | [memory_system.py:37](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_system.py#L37) | `memory_system.vector` | 无参（默认） | 中活跃度 | data/vectors.json |
| 4 | ContextManager lazy 创建 | [context_manager.py:46](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/context/context_manager.py#L46) | `context_manager._vector_memory` | 无参（默认） | 低活跃度 | data/vectors.json |

### 2.3 EventMemory 创建路径

| # | 位置 | 文件:行号 | 持有者 | 初始化参数 | 活跃状态 | 持久化路径 |
|---|---|---|---|---|---|---|
| 1 | MemorySystem 直接创建 | [memory_system.py:44](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/memory/memory_system.py#L44) | `memory_system.event` | 无参（默认） | 中活跃度 | data/event_memory.json |

### 2.4 旧版 MemorySystem 创建（可能已废弃）

| # | 位置 | 文件:行号 | 持有者 | 说明 |
|---|---|---|---|---|
| 1 | yuyi.py 直接创建 | [yuyi.py:66](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/yuyi.py#L66) | `yuyi.memory` | 早期版本入口，可能已废弃 |
| 2 | yuyi_core.py 直接创建 | [yuyi_core.py:41](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/core/yuyi_core.py#L41) | `yuyi_core.memory` | 早期版本入口，可能已废弃 |

### 2.5 Memory 分裂风险分析

| 风险 | 严重性 | 说明 | 缓解因素 |
|---|---|---|---|
| MemoryStore 多实例 | **中** | Orchestrator、EventExtractor、TopicTracker 各自创建 | **持久化到同一文件** data/memory.json，风险降低 |
| VectorMemory 多实例 | **低** | Orchestrator 是唯一生产路径，其他模块不参与实时对话 | Orchestrator 是唯一调用 engine.generate 的入口 |
| Memory 内存缓存不同步 | **中** | 不同实例的内存缓存可能不同 | 写操作后会持久化，读操作从文件加载 |
| 旧版 MemorySystem 已废弃 | **低** | yuyi.py/yuyi_core.py 可能是早期入口 | 生产入口为 api_server.py 和 initiative_sender.py（都用 Orchestrator） |

---

## 3. Relationship 创建路径审计

### 3.1 RelationshipState 创建路径

| # | 位置 | 文件:行号 | 持有者 | 初始化参数 | 活跃状态 | 持久化路径 |
|---|---|---|---|---|---|---|
| **1** | Orchestrator RelationshipState 包装类 | [orchestrator.py:46](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L46) | `orchestrator.relationship_state._impl` | state_path 默认 | **生产活跃** | data/relationship_state.json |
| **2** | Orchestrator 直接创建 | [orchestrator.py:187](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L187) | `orchestrator.relationship_state` (wrapper) | 无参（默认） | **生产活跃**（wrapper） | data/relationship_state.json |
| 3 | GrowthPipeline 内部创建 | [pipeline.py:61](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/pipeline.py#L61) | `pipeline.relationship_state` | 无参（默认） | **独立流程** | data/relationship_state.json |
| 4 | PersonalityResolver 内部创建 | [personality_resolver.py:39](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/personality_resolver.py#L39) | `resolver.relationship_state` | 无参（默认） | **共享**（Phase 4.2.3） | data/relationship_state.json |
| 5 | relationship_repository fallback | [relationship_repository.py:107](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/relationship/relationship_repository.py#L107) | 局部临时变量 | 无参（默认） | 低活跃度 | data/relationship_state.json |

### 3.2 RelationshipEvaluator 创建路径

| # | 位置 | 文件:行号 | 持有者 | 初始化参数 | 活跃状态 | 说明 |
|---|---|---|---|---|---|---|
| **1** | Orchestrator 每次 process 创建 | [orchestrator.py:657](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L657) | 局部临时变量 | 无参（默认） | **生产活跃** ⚠️ 每次 process 创建！ |
| 2 | orchestrator_hooks.py 创建 | [orchestrator_hooks.py:106](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator_hooks.py#L106) | 局部临时变量 | 无参（默认） | 中活跃度 |
| 3 | relationship_model.py 内部创建 | [relationship_model.py:71](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/relationship/relationship_model.py#L71) | `relationship_model.evaluator` | 无参（默认） | 低活跃度 |

### 3.3 Relationship 分裂风险分析

| 风险 | 严重性 | 说明 | 缓解因素 |
|---|---|---|---|
| RelationshipState 多实例 | **低** | Orchestrator wrapper、GrowthPipeline、PersonalityResolver 各自创建 | **持久化到同一文件** data/relationship_state.json |
| RelationshipEvaluator 每次 process 创建 | **低** | 每次 process() 创建临时实例，无持久状态 | Evaluator 是纯函数式校验器，无内部状态 |
| PersonalityResolver 使用共享 resolver | **低** | Phase 4.2.3 已修复，relationship_state 也随之共享 | 与 Orchestrator wrapper 虽为不同实例，但读同一文件 |

---

## 4. Growth 创建路径审计

### 4.1 GrowthEngine 创建路径

| # | 位置 | 文件:行号 | 持有者 | 初始化参数 | 活跃状态 | 说明 |
|---|---|---|---|---|---|---|
| 1 | GrowthPipeline 内部创建 | [pipeline.py:48](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/pipeline.py#L48) | `pipeline.growth_engine` | 无参（默认） | **独立流程** | 内部创建 GrowthState |

### 4.2 GrowthState 创建路径

| # | 位置 | 文件:行号 | 持有者 | 初始化参数 | 活跃状态 | 持久化路径 |
|---|---|---|---|---|---|---|
| 1 | GrowthEngine 内部创建 | [growth_engine.py:28](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/growth_engine.py#L28) | `growth_engine.state` | 无参（默认） | **独立流程** | data/growth_state.json |
| 2 | PersonalityResolver 内部创建 | [personality_resolver.py:38](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/personality_resolver.py#L38) | `resolver.state` | 无参（默认） | **共享**（Phase 4.2.3） | data/growth_state.json |
| 3 | GrowthPipeline 注入 resolver | [pipeline.py:65](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/pipeline.py#L65) | `pipeline.resolver.state` | state=growth_engine.state | **独立流程** | data/growth_state.json（**引用 GrowthEngine 的 state！**） |

### 4.3 GrowthPipeline 创建路径

| # | 位置 | 文件:行号 | 调用者 | 说明 |
|---|---|---|---|---|
| 1 | main.py 直接创建 | [main.py:23](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/main.py#L23) | main() | 开发/测试入口，非生产路径 |
| 2 | 多个测试文件创建 | test_growth_pipeline.py 等 | 测试框架 | 测试专用 |

### 4.4 Growth 分裂风险分析

| 风险 | 严重性 | 说明 | 缓解因素 |
|---|---|---|---|
| GrowthState 实例分裂（Orchestrator vs GrowthEngine） | **中** | PersonalityResolver 的 GrowthState（Phase 4.2.3 共享）与 GrowthEngine 的 GrowthState 是不同实例 | **持久化到同一文件** data/growth_state.json，写操作后需重新加载 |
| GrowthPipeline 独立运行 | **低** | Orchestrator 不调用 GrowthPipeline（grep 确认） | GrowthPipeline 是独立批量整理流程 |
| GrowthPipeline 注入 state 到 resolver | **低** | pipeline.resolver 与 pipeline.growth_engine 共享同一 state 引用 | 独立流程，不影响 Orchestrator |

---

## 5. 当前状态流图

### 5.1 生产路径（api_server.py → Orchestrator）

```
api_server.py / initiative_sender.py
    ↓
Orchestrator（全局单例）
    ├─ EmotionManager ← 共享 RuntimeCore 实例（Phase 4.2.1）✅
    ├─ SelfModelStore ← 共享 RuntimeCore 实例（Phase 4.2.2）✅
    ├─ PersonalityResolver ← 共享 RuntimeCore 实例（Phase 4.2.3）✅
    │     ├─ state (GrowthState) → 读 data/growth_state.json
    │     ├─ relationship_state → 读 data/relationship_state.json
    │     └─ self_model_store ← 已注入共享实例 ✅
    ├─ MemoryStore → 读/写 data/memory.json  ⚠️ 独立实例
    ├─ VectorMemory → 读/写 data/vectors.json  ⚠️ 独立实例
    ├─ RelationshipState (wrapper) → 读 data/relationship_state.json  ⚠️ 独立实例
    └─ RelationshipEvaluator → 每次 process() 新建实例  ⚠️ 临时对象
         ↓
    resolve() → PersonalityVector → RuntimeContext → engine.generate()
```

### 5.2 GrowthPipeline 独立流程（main.py，非生产路径）

```
main.py
    ↓
GrowthPipeline（每次运行独立实例）
    ├─ GrowthEngine
    │     └─ state (GrowthState) → 读/写 data/growth_state.json  ⚠️ 独立实例
    ├─ RelationshipState → 读 data/relationship_state.json  ⚠️ 独立实例
    ├─ resolver = PersonalityResolver(state=growth_engine.state)
    │     └─ state → 引用自 growth_engine.state（共享！）✅
    ├─ event_extractor.store (MemoryStore) → 读 data/memory.json  ⚠️ 独立实例
    └─ topic_tracker.store (MemoryStore) → 读 data/memory.json  ⚠️ 独立实例
```

### 5.3 分裂风险矩阵（剩余问题）

| 模块 | 实例数 | 持久化 | 生产路径分裂 | 实际风险 |
|---|---|---|---|---|
| EmotionManager | **1**（共享） | data/emotion_state.json | ✅ 无 | 已修复 |
| SelfModelStore | **1**（共享） | 无（内存） | ✅ 无 | 已修复 |
| PersonalityResolver | **1**（共享） | 间接 | ✅ 无 | 已修复 |
| GrowthState | **2** | data/growth_state.json | ⚠️ Orchestrator vs GrowthEngine | 中 |
| RelationshipState | **3** | data/relationship_state.json | ⚠️ wrapper/resolver 不同实例 | 低 |
| MemoryStore | **4** | data/memory.json | ⚠️ Orchestrator/Extractor/Tracker | 中 |
| VectorMemory | **4** | data/vectors.json | ✅ 仅 Orchestrator 用于对话 | 低 |
| RelationshipEvaluator | **N**（临时） | 无（纯函数） | ✅ 无状态 | 低 |
| EventMemory | **1**（MemorySystem 内） | data/event_memory.json | ✅ 不参与实时对话 | 低 |

---

## 6. Phase 4.3 实施建议

### 6.1 优先级排序

| 优先级 | 模块 | 问题 | 建议 |
|---|---|---|---|
| **P0** | MemoryStore/VectorMemory | Orchestrator 与 Growth 子模块多实例 | RuntimeCore 新增 `get_memory_store()` / `get_vector_memory()` |
| **P1** | GrowthState | Orchestrator 与 GrowthEngine 状态分裂 | RuntimeCore 新增 `get_growth_state()` |
| **P2** | RelationshipState | 多实例读同一文件 | RuntimeCore 新增 `get_relationship_state()` |
| **P3** | RelationshipEvaluator | 每次 process 创建 | 无状态对象，不影响，保持现状 |

### 6.2 最小修改方案（P0 + P1）

#### 方案 A：RuntimeCore 新增 Memory Authority

```python
# runtime_core.py
def get_memory_store(self) -> Optional["MemoryStore"]:
    """lazy 创建 MemoryStore 权威实例。"""
    if self.personality_resolver is not None:
        # 未来扩展
        pass
    try:
        from src.memory.memory_store import MemoryStore
        if not hasattr(self, "_lazy_memory_store") or self._lazy_memory_store is None:
            self._lazy_memory_store = MemoryStore()
        return self._lazy_memory_store
    except Exception:
        return None

def get_vector_memory(self) -> Optional["VectorMemory"]:
    """lazy 创建 VectorMemory 权威实例。"""
    try:
        from src.memory.vector import VectorMemory
        if not hasattr(self, "_lazy_vector_memory") or self._lazy_vector_memory is None:
            self._lazy_vector_memory = VectorMemory()
        return self._lazy_vector_memory
    except Exception:
        return None
```

#### 方案 B：RuntimeCore 新增 GrowthState Authority

```python
# runtime_core.py
def get_growth_state(self) -> Optional["GrowthState"]:
    """lazy 创建 GrowthState 权威实例。"""
    # 优先使用 PersonalityResolver 内部的 state（Phase 4.2.3 共享 resolver）
    if self.personality_resolver is not None and hasattr(self.personality_resolver, "state"):
        return self.personality_resolver.state
    try:
        from src.growth.growth_state import GrowthState
        if not hasattr(self, "_lazy_growth_state") or self._lazy_growth_state is None:
            self._lazy_growth_state = GrowthState()
        return self._lazy_growth_state
    except Exception:
        return None
```

#### 方案 C：RuntimeBridge 转发

```python
def get_memory_store(self) -> Any: ...
def get_vector_memory(self) -> Any: ...
def get_growth_state(self) -> Any: ...
```

#### 方案 D：Orchestrator 获取共享实例

```python
# orchestrator.py
self.memory_store = _bridge.get_memory_store() or MemoryStore()
self.vector_memory = _bridge.get_vector_memory() or VectorMemory()
```

### 6.3 不修改的部分

| 模块 | 原因 |
|---|---|
| GrowthPipeline | 独立流程，不参与实时对话 |
| orchestrator_hooks.py / relationship_model.py | RelationshipEvaluator 是纯函数，无内部状态 |
| MemorySystem/ContextManager/yuyi_core.py | 可能已废弃或不参与生产路径 |
| EventMemory/IdentityMemory | 不参与实时对话 Prompt |

---

## 7. 与 Phase 4.2 架构一致性

Phase 4.3 的 Authority 建立方式与 Phase 4.2.1/4.2.2/4.2.3 保持一致：

| 模式 | 说明 |
|---|---|
| RuntimeCore lazy 创建 | 不依赖 adapters_enabled 开关 |
| RuntimeBridge 转发 | 单例模式获取引用 |
| Orchestrator 两级 fallback | 优先共享 → fallback 自建 |
| 异常不阻断聊天 | 所有创建失败都被捕获 |

---

**审计完成。等待确认后再编码。**

**建议实施顺序**：
1. Phase 4.3.1 MemoryStore Authority（P0）
2. Phase 4.3.2 VectorMemory Authority（P0）
3. Phase 4.3.3 GrowthState Authority（P1）
4. Phase 4.3.4 RelationshipState Authority（P2）
5. Phase 4.3.5 全面集成回归测试

---

**注意**：GrowthPipeline、RelationshipEvaluator 不建议作为 Authority 目标，因为：
- GrowthPipeline 是独立批量流程，不参与实时对话
- RelationshipEvaluator 是纯函数式校验器，无内部状态（每次创建是可接受的）
