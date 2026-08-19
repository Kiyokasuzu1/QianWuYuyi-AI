# QianWuYuyi-AI Phase 4.2.3 PersonalityResolver Authority 变更报告

**日期**：2026-07-30
**分支**：`fix/personality-authority`
**目标**：建立 PersonalityResolver Runtime State Authority，解决多实例 PersonalityResolver 分裂问题

---

## 1. 修改文件列表

| # | 文件 | 修改类型 | 说明 |
|---|---|---|---|
| 1 | [src/runtime/runtime_core.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_core.py) | 修改 | 新增 `get_personality_resolver()` 方法（lazy 创建权威实例，注入 SelfModelStore） |
| 2 | [src/runtime/runtime_bridge.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_bridge.py) | 修改 | 新增 `get_personality_resolver()` 转发接口 |
| 3 | [src/orchestrator.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py) | 修改 | 优先通过 RuntimeBridge 获取 PersonalityResolver，保留 fallback |
| 4 | [tests/test_personality_authority.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/tests/test_personality_authority.py) | 新增 | 20 个测试覆盖 7 类验证场景 |

**总计**：修改 3 个文件，新增 1 个测试文件

---

## 2. 架构变化

### 2.1 修改前（多实例分裂）

```
┌─────────────────────────────────────────────────────────────────┐
│  Orchestrator                                                    │
│  └─ personality_resolver = PersonalityResolver()  ← 实例 #1      │
│      ├─ _trait_states (实例 #1 独有)                             │
│      ├─ growth_history (实例 #1 独有)                             │
│      └─ personality_history (实例 #1 独有)                       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  RuntimeCore (adapters_enabled=True 时)                          │
│  └─ personality_resolver = PersonalityResolver()  ← 实例 #2      │
│      ├─ _trait_states (实例 #2 独有，与 #1 不同步)                │
│      ├─ growth_history (实例 #2 独有，与 #1 不同步)               │
│      └─ personality_history (实例 #2 独有，与 #1 不同步)          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  GrowthPipeline (独立流程)                                       │
│  └─ resolver = PersonalityResolver()  ← 实例 #3                  │
└─────────────────────────────────────────────────────────────────┘

问题：实例 #1 和 #2 的 _trait_states、growth_history、personality_history 
完全独立，当 adapters_enabled=True 时存在状态分裂风险。
```

### 2.2 修改后（单一权威实例 + 注入共享）

```
┌─────────────────────────────────────────────────────────────────┐
│  RuntimeCore（唯一权威来源）                                     │
│  └─ get_personality_resolver()  ← lazy 创建权威实例              │
│      └─ PersonalityResolver（唯一实例）                           │
│          ├─ _trait_states（共享内存状态）                         │
│          ├─ growth_history（共享内存状态）                        │
│          ├─ personality_history（共享内存状态）                   │
│          └─ self_model_store ← Phase 4.2.2 已注入共享            │
└─────────────────────────────────────────────────────────────────┘
                ↑ get_personality_resolver()
                │
┌─────────────────────────────────────────────────────────────────┐
│  RuntimeBridge（转发层）                                         │
│  └─ get_personality_resolver() → RuntimeCore.get_               │
│       personality_resolver()                                     │
└─────────────────────────────────────────────────────────────────┘
                ↑ get_runtime_bridge().get_personality_resolver()
                │
┌─────────────────────────────────────────────────────────────────┐
│  Orchestrator                                                    │
│  └─ personality_resolver（引用共享实例）                          │
│      └─ resolve() → PersonalityVector → RuntimeContext →         │
│         engine.generate()                                        │
└─────────────────────────────────────────────────────────────────┘

目标架构实现：
RuntimeCore → PersonalityResolver → RuntimeBridge → Orchestrator
    → RuntimeContext → engine.generate() → 【人格表现】
```

### 2.3 关键设计决策

| 决策 | 原因 |
|---|---|
| RuntimeCore 持有 PersonalityResolver 所有权 | RuntimeCore 是长期状态中心，符合 Phase 4.2 目标 |
| `get_personality_resolver()` 采用 lazy 创建 | `adapters_enabled` 默认 False，但 PersonalityResolver 应始终可用 |
| Lazy 创建时注入共享 SelfModelStore | 确保 PersonalityResolver 内部的 self_model_store 与 Orchestrator 一致 |
| 优先使用已创建的 PersonalityResolver | `adapters_enabled=True` 时 RuntimeCore 已有 resolver，直接返回 |
| Orchestrator 保留 fallback 自建 | 保证 RuntimeBridge 不可用时也能正常工作 |
| 不修改 GrowthPipeline | 独立流程，不参与实时对话，接收外部 state |
| 不修改 PersonalityController | 死代码，已标记 Deprecated |

---

## 3. 详细修改内容

### 3.1 RuntimeCore.get_personality_resolver()（新增方法）

**位置**：[src/runtime/runtime_core.py:2868-2901](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_core.py#L2868-L2901)

```python
def get_personality_resolver(self) -> Optional["PersonalityResolver"]:
    """
    获取 PersonalityResolver 权威实例（Phase 4.2.3 Personality Authority）。

    - 若 adapters_enabled=True 且已创建，返回已有实例
    - 否则 lazy 创建一个基础 PersonalityResolver
    - 注入共享的 SelfModelStore，确保人格解析与自我认知数据一致
    - 创建失败返回 None，调用方需自行 fallback

    设计意图：让 RuntimeCore 成为 PersonalityResolver 的唯一持有者，
    Orchestrator 不再主动创建 PersonalityResolver，而是通过 RuntimeBridge 获取引用。
    这解决了 Orchestrator 与 RuntimeCore（当 adapters_enabled=True 时）之间的状态分裂问题。
    """
    # 优先使用已创建的 PersonalityResolver（adapters_enabled=True 时）
    if self.personality_resolver is not None:
        return self.personality_resolver

    # Lazy 创建基础 PersonalityResolver（不依赖 adapters_enabled 开关）
    try:
        from src.personality.personality_resolver import PersonalityResolver as _Resolver
        if not hasattr(self, "_lazy_personality_resolver") or self._lazy_personality_resolver is None:
            logger.info("RuntimeCore: lazy 创建基础 PersonalityResolver（Personality Authority）")
            self._lazy_personality_resolver = _Resolver()
            
            # 关键修复：确保 lazy 创建的 PersonalityResolver 内部使用的是共享的 SelfModelStore
            # （通过 get_self_model_store() 获取 Phase 4.2.2 建立的权威实例）
            shared_store = self.get_self_model_store()
            if shared_store and hasattr(self._lazy_personality_resolver, "self_model_store"):
                self._lazy_personality_resolver.self_model_store = shared_store
                
        return self._lazy_personality_resolver
    except Exception as _e:
        logger.warning(f"RuntimeCore: PersonalityResolver lazy 创建失败: {_e}")
        return None
```

**行为**：
- 若 `adapters_enabled=True` 且 PersonalityResolver 已创建，直接返回
- 否则 lazy 创建基础 PersonalityResolver，并缓存到 `_lazy_personality_resolver`
- Lazy 创建时自动注入共享的 SelfModelStore（Phase 4.2.2）
- 后续调用返回缓存的实例
- 创建失败返回 None，调用方自行 fallback

---

### 3.2 RuntimeBridge.get_personality_resolver()（新增方法）

**位置**：[src/runtime/runtime_bridge.py:294-313](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_bridge.py#L294-L313)

```python
def get_personality_resolver(self) -> Any:
    """
    获取 RuntimeCore 持有的 PersonalityResolver 权威实例。

    Phase 4.2.3 Personality Authority：
    - RuntimeCore 保留 PersonalityResolver 所有权
    - Orchestrator 不再主动创建 PersonalityResolver，而是通过本方法获取引用
    - 若 RuntimeCore 未初始化或创建失败，返回 None（调用方需 fallback）
    - 这解决了 Orchestrator 与 RuntimeCore 之间的状态分裂问题

    Returns:
        PersonalityResolver 实例，或 None
    """
    if not self._runtime_core:
        return None
    try:
        return self._runtime_core.get_personality_resolver()
    except Exception as e:
        logger.warning(f"RuntimeBridge.get_personality_resolver 失败: {e}")
        return None
```

**行为**：
- RuntimeCore 未初始化时返回 None
- 转发到 `runtime_core.get_personality_resolver()`
- 异常时返回 None

---

### 3.3 Orchestrator.__init__()（修改 PersonalityResolver 初始化）

**位置**：[src/orchestrator.py:90-112](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L90-L112)

```python
# PersonalityResolver（Phase 4.2.3 Personality Authority）
# 优先通过 RuntimeBridge 获取 RuntimeCore 持有的权威实例
# 若 RuntimeBridge 未初始化或获取失败，则 fallback 自建（保持向后兼容）
self.personality_resolver = None
try:
    from src.runtime.runtime_bridge import get_runtime_bridge
    _bridge = get_runtime_bridge()
    _shared_resolver = _bridge.get_personality_resolver()
    if _shared_resolver is not None:
        self.personality_resolver = _shared_resolver
        print("[Orchestrator] PersonalityResolver 已从 RuntimeBridge 获取（共享 RuntimeCore 实例）")
except Exception as e:
    print(f"[Orchestrator] 通过 RuntimeBridge 获取 PersonalityResolver 失败: {e}")

# Fallback：RuntimeBridge 不可用时自建（保持旧逻辑兼容，不影响聊天）
if self.personality_resolver is None:
    try:
        self.personality_resolver = PersonalityResolver()
        print("[Orchestrator] PersonalityResolver fallback 自建")
    except Exception as e:
        print(f"[Orchestrator] PersonalityResolver 初始化失败: {e}")
        # 极端情况下若连自建都失败，后续需要容错处理，但先不阻断
        pass
```

**行为**（两级 fallback）：
1. **优先**：从 RuntimeBridge 获取 RuntimeCore 的共享实例
2. **Fallback**：RuntimeBridge 不可用时自建（保持兼容）
3. 所有异常都被捕获，不影响聊天

---

## 4. 测试结果

### 4.1 新增测试

```
tests/test_personality_authority.py
======================= 20 passed, 17 warnings in 0.53s =======================
```

| 测试类 | 测试数 | 覆盖项 |
|---|---|---|
| TestPersonalityResolverShared | 4 | RuntimeCore 与 Orchestrator 使用同一 PersonalityResolver |
| TestTraitStateConsistency | 3 | 多次 resolve() 后 TraitState 一致 |
| TestPersonalityReachesPrompt | 3 | Personality 更新能正确传递到 Prompt |
| TestSelfModelStillWorks | 3 | SelfModel 功能在共享架构下仍正常工作 |
| TestFallbackCompatibility | 3 | Fallback 兼容性 |
| TestCrossPhaseRegression | 4 | Phase 4.2.1/4.2.2 跨 Phase 回归保护 |

### 4.2 Phase 4.2.1 + 4.2.2 回归测试

```
tests/test_emotion_authority.py + tests/test_selfmodel_authority.py
======================= 39 passed, 30 warnings in 1.00s =======================
```

| 测试套件 | 测试数 | 状态 |
|---|---|---|
| test_emotion_authority.py | 16 | 全部通过 |
| test_selfmodel_authority.py | 23 | 全部通过 |

---

## 5. 当前闭环状态

### 5.1 Personality Authority 闭环（已建立）

```
RuntimeCore
    ↓ get_personality_resolver()  ← lazy 创建权威实例
    │
PersonalityResolver（唯一实例）
    ├─ _trait_states（共享内存状态）
    ├─ growth_history（共享内存状态）
    ├─ personality_history（共享内存状态）
    └─ self_model_store ← Phase 4.2.2 已注入共享
    │
RuntimeBridge
    ↓ get_personality_resolver()  ← 转发
    │
Orchestrator
    ↓ personality_resolver（引用共享实例）
    ↓ resolve() → PersonalityVector → RuntimeContext → engine.generate()
```

### 5.2 三权分立完成状态

| Phase | Authority | RuntimeCore 方法 | 状态 |
|---|---|---|---|
| 4.2.1 | EmotionManager | `get_emotion_manager()` | ✅ 已建立 |
| 4.2.2 | SelfModelStore | `get_self_model_store()` | ✅ 已建立 |
| 4.2.3 | PersonalityResolver | `get_personality_resolver()` | ✅ 已建立 |

---

## 6. 未做的事项（符合约束）

- ❌ 不删除任何模块
- ❌ 不修改 Memory / Emotion / Relationship
- ❌ 不修改 GrowthPipeline（独立流程，不参与实时对话）
- ❌ 不修改 PersonalityController（死代码）
- ❌ 不修改 PersonalityResolver 内部逻辑
- ❌ 不改变 QQ 接入
- ❌ 不修改 API Key

---

## 7. 风险评估

| 风险 | 严重性 | 说明 | 缓解措施 |
|---|---|---|---|
| RuntimeBridge 单例在 Orchestrator 之前未初始化 | 低 | Fallback 自建保证聊天不中断 | 与 Phase 4.2.1/4.2.2 一致 |
| Lazy 创建的 PersonalityResolver 与 adapters_enabled=True 时的 resolver 不同 | 低 | `get_personality_resolver()` 优先返回已创建的实例 | 行为一致 |
| GrowthPipeline 仍使用独立 resolver | 低 | 不参与实时对话，独立流程 | 不修改，保持现状 |
| 共享 resolver 的 _trait_states 被多线程访问 | 低 | Python GIL 保护，且 resolve() 操作是同步的 | 无需额外处理 |

---

## 8. 后续建议

### 8.1 Phase 4.2.4：状态持久化（可选）

**问题**：`_trait_states`、`growth_history`、`personality_history` 是纯内存，重启后丢失

**建议**：
1. 为 PersonalityResolver 添加持久化接口
2. 在 resolve() 后自动保存状态快照
3. 启动时从文件恢复状态

**注意**：此步骤非必须，`GrowthState` 和 `relationship_state` 已持久化，纯内存状态在单次运行中是一致的。

### 8.2 Phase 4.3：Runtime State 统一总结

当前 RuntimeCore 已成为三大状态的唯一来源：
- ✅ EmotionManager（Phase 4.2.1）
- ✅ SelfModelStore（Phase 4.2.2）
- ✅ PersonalityResolver（Phase 4.2.3）

可考虑在 Phase 4.3 进行全面测试和文档更新。

---

**Phase 4.2.3 PersonalityResolver Authority 完成。**

**核心修复**：通过 RuntimeCore 持有唯一 PersonalityResolver 实例，Orchestrator 通过 RuntimeBridge 获取共享引用，解决了多实例状态分裂问题，同时确保了 SelfModelStore 的正确注入。

**等待审计通过后可进入下一阶段。**
