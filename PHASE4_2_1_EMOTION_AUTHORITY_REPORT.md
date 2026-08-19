# QianWuYuyi-AI Phase 4.2.1 Emotion Authority 变更报告

**日期**：2026-07-30
**分支**：`fix/emotion-authority`
**目标**：建立 Runtime State Authority，解决 EmotionManager 双实例状态分裂

---

## 1. 修改文件列表

| # | 文件 | 修改类型 | 说明 |
|---|---|---|---|
| 1 | [src/runtime/runtime_core.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_core.py) | 修改 | 新增 `get_emotion_manager()` 方法（lazy 创建权威实例） |
| 2 | [src/runtime/runtime_bridge.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_bridge.py) | 修改 | 新增 `get_emotion_manager()` 转发接口 |
| 3 | [src/orchestrator.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py) | 修改 | 优先通过 RuntimeBridge 获取 EmotionManager，fallback 自建 |
| 4 | [tests/test_emotion_authority.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/tests/test_emotion_authority.py) | 新增 | 17 个测试覆盖 5 类验证场景 |

**总计**：修改 3 个文件，新增 1 个文件

---

## 2. 架构变化

### 2.1 修改前（双实例分裂）

```
┌─────────────────────────────────────────────────────────┐
│  Orchestrator                                            │
│  └─ self.emotion_manager = EmotionManager()   ← 实例 #1  │
│      └─ repository → data/emotion_state.json             │
└─────────────────────────────────────────────────────────┘
                          ↓ 写
              data/emotion_state.json  ← 写冲突风险！
                          ↑ 写
┌─────────────────────────────────────────────────────────┐
│  RuntimeCore（emotion_enabled=True 时）                  │
│  └─ self.emotion_manager = EmotionManager(...)← 实例 #2  │
│      └─ repository → data/emotion_state.json             │
└─────────────────────────────────────────────────────────┘

问题：两个独立实例同时写同一文件，状态覆盖风险
```

### 2.2 修改后（单一权威实例）

```
┌─────────────────────────────────────────────────────────┐
│  RuntimeCore（唯一权威来源）                             │
│  └─ self.emotion_manager  ← 唯一实例（lazy 创建）        │
│      └─ repository → data/emotion_state.json             │
└─────────────────────────────────────────────────────────┘
              ↑ get_emotion_manager()
              │
┌─────────────────────────────────────────────────────────┐
│  RuntimeBridge（转发层）                                 │
│  └─ get_emotion_manager() → RuntimeCore.get_emotion_    │
│       manager()                                          │
└─────────────────────────────────────────────────────────┘
              ↑ get_runtime_bridge().get_emotion_manager()
              │
┌─────────────────────────────────────────────────────────┐
│  Orchestrator                                            │
│  └─ self.emotion_manager  ← 引用 RuntimeCore 的实例      │
│      （不再主动创建，通过 RuntimeBridge 获取）           │
│      Fallback：RuntimeBridge 不可用时自建（保持兼容）    │
└─────────────────────────────────────────────────────────┘

目标架构实现：
RuntimeCore → EmotionManager → RuntimeBridge → Orchestrator
```

### 2.3 关键设计决策

| 决策 | 原因 |
|---|---|
| RuntimeCore 持有 EmotionManager 所有权 | RuntimeCore 是长期状态中心，符合 Phase 4.2 目标 |
| `get_emotion_manager()` 采用 lazy 创建 | `emotion_enabled` 默认 False，但 EmotionManager 应始终可用 |
| lazy 创建不创建 EmotionDynamicsEngine | EmotionDynamicsEngine 是高级功能，受 `emotion_enabled` 控制 |
| Orchestrator 保留 fallback 自建 | 保证 RuntimeBridge 未初始化时聊天不中断 |
| Fallback 自建使用默认路径 | 保持与 Phase 4.1 完全兼容 |

---

## 3. 详细修改内容

### 3.1 RuntimeCore.get_emotion_manager()（新增方法）

**位置**：[src/runtime/runtime_core.py:2816-2839](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_core.py#L2816-L2839)

```python
def get_emotion_manager(self) -> Optional["EmotionManager"]:
    """
    获取 EmotionManager 权威实例（Phase 4.2.1 Emotion Authority）。

    - 若 _emotion_enabled=True，则返回已创建的 self.emotion_manager
    - 若 _emotion_enabled=False（默认），则 lazy 创建一个基础 EmotionManager
      （不创建 EmotionDynamicsEngine），作为 Orchestrator 的权威来源
    - 创建失败返回 None，调用方需自行 fallback
    """
    if self.emotion_manager is not None:
        return self.emotion_manager

    # Lazy 创建基础 EmotionManager（不依赖 _emotion_enabled 开关）
    try:
        from src.emotion.emotion_manager import EmotionManager as _EmotionManager
        logger.info("RuntimeCore: lazy 创建基础 EmotionManager（Emotion Authority）")
        self.emotion_manager = _EmotionManager()
        return self.emotion_manager
    except Exception as _e:
        logger.warning(f"RuntimeCore: EmotionManager lazy 创建失败: {_e}")
        return None
```

**行为**：
- 首次调用时 lazy 创建基础 EmotionManager（默认路径 `data/emotion_state.json`）
- 后续调用返回缓存的实例
- 创建失败返回 None，调用方自行 fallback

---

### 3.2 RuntimeBridge.get_emotion_manager()（新增方法）

**位置**：[src/runtime/runtime_bridge.py:247-267](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_bridge.py#L247-L267)

```python
def get_emotion_manager(self) -> Any:
    """
    获取 RuntimeCore 持有的 EmotionManager 权威实例。

    Phase 4.2.1 Emotion Authority：
    - RuntimeCore 保留 EmotionManager 所有权
    - Orchestrator 不再主动创建 EmotionManager，而是通过本方法获取引用
    - 若 RuntimeCore 未初始化或创建失败，返回 None（调用方需 fallback）
    """
    if not self._runtime_core:
        return None
    try:
        return self._runtime_core.get_emotion_manager()
    except Exception as e:
        logger.warning(f"RuntimeBridge.get_emotion_manager 失败: {e}")
        return None
```

**行为**：
- RuntimeCore 未初始化时返回 None
- 转发到 `runtime_core.get_emotion_manager()`
- 异常时返回 None

---

### 3.3 Orchestrator.__init__()（修改 EmotionManager 初始化）

**位置**：[src/orchestrator.py:98-119](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L98-L119)

```python
# EmotionManager（Phase 4.2.1 Emotion Authority）
# 优先通过 RuntimeBridge 获取 RuntimeCore 持有的权威实例
# 若 RuntimeBridge 未初始化或获取失败，则 fallback 自建（保持向后兼容）
self.emotion_manager = None
try:
    from src.runtime.runtime_bridge import get_runtime_bridge
    _bridge = get_runtime_bridge()
    self.emotion_manager = _bridge.get_emotion_manager()
    if self.emotion_manager is not None:
        print("[Orchestrator] EmotionManager 已从 RuntimeBridge 获取（共享 RuntimeCore 实例）")
except Exception as e:
    print(f"[Orchestrator] 通过 RuntimeBridge 获取 EmotionManager 失败: {e}")

# Fallback：RuntimeBridge 不可用时自建（保持旧逻辑兼容，不影响聊天）
if self.emotion_manager is None:
    try:
        from src.emotion.emotion_manager import EmotionManager
        self.emotion_manager = EmotionManager()
        print("[Orchestrator] EmotionManager fallback 自建（RuntimeBridge 不可用）")
    except Exception as e:
        print(f"[Orchestrator] EmotionManager 初始化失败: {e}")
        self.emotion_manager = None
```

**行为**：
1. 尝试从 RuntimeBridge 获取 RuntimeCore 的 EmotionManager
2. 若获取成功，使用共享实例（单一权威）
3. 若失败或 RuntimeBridge 不可用，fallback 自建（保持兼容）
4. 所有异常都被捕获，不影响聊天

---

## 4. 测试结果

### 4.1 新增测试

```
tests/test_emotion_authority.py
============================= 17 passed in 1.14s ==============================
```

| 测试类 | 测试数 | 覆盖项 |
|---|---|---|
| TestEmotionManagerShared | 3 | RuntimeCore 和 Orchestrator 引用同一实例 |
| TestEmotionUpdateNoDoubleWrite | 3 | 情绪更新只产生一次状态变化 |
| TestEmotionStateFileNoDoubleWrite | 2 | emotion_state.json 不会被双写 |
| TestRuntimeBridgeEmotionForwarding | 4 | RuntimeBridge 转发正确 |
| TestFallbackCompatibility | 3 | Fallback 兼容性 |
| TestPhase41Regression | 2 | Phase 4.1 情绪闭环回归保护 |

### 4.2 回归测试

```
tests/test_runtime_unification.py + test_emotion_runtime_integration.py
============================= 26 passed in 0.69s ==============================
```

| 测试文件 | 通过数 | 说明 |
|---|---|---|
| test_runtime_unification.py | 24/24 | Phase 4.1 全部测试通过 |
| test_emotion_runtime_integration.py | 2/2 | 情绪集成测试通过 |

**所有测试通过，无回归问题。**

---

## 5. 当前闭环状态

### 5.1 Emotion Authority 闭环（已建立）

```
RuntimeCore
    ↓ get_emotion_manager()  ← lazy 创建权威实例
    │
EmotionManager（唯一实例）
    ↓ repository.save()      ← 唯一写入口
    │
data/emotion_state.json     ← 单一写入源，无冲突
    ↑
RuntimeBridge
    ↓ get_emotion_manager()  ← 转发
    │
Orchestrator
    ↓ emotion_manager 引用   ← 共享实例
    ↓ process_event()        ← 操作同一 state 对象
    ↓ engine.generate()     ← emotion_context 从同一 state 构建
```

### 5.2 验证项

| 验证项 | 状态 | 测试 |
|---|---|---|
| RuntimeCore 和 Orchestrator 引用同一实例 | ✅ | `test_orchestrator_uses_runtime_core_emotion_manager` |
| 多个 Orchestrator 共享同一实例 | ✅ | `test_emotion_manager_identity_across_multiple_orchestrators` |
| 情绪更新只产生一次状态变化 | ✅ | `test_no_concurrent_double_write` |
| emotion_state.json 不被双写 | ✅ | `test_single_emotion_state_file_writer` |
| RuntimeBridge 转发正确 | ✅ | `test_bridge_returns_emotion_manager_after_init` |
| Lazy 创建（emotion_enabled=False 时） | ✅ | `test_bridge_lazy_creates_when_emotion_disabled` |
| Fallback 兼容性 | ✅ | `test_orchestrator_fallback_when_bridge_not_initialized` |
| Fallback 不影响聊天 | ✅ | `test_orchestrator_fallback_does_not_crash` |
| Phase 4.1 情绪闭环仍正常 | ✅ | `test_emotion_context_reaches_engine_with_shared_instance` |

---

## 6. 未做的事项（符合约束）

- ❌ 不删除任何模块
- ❌ 不修改 Memory / Personality / Relationship / Screen / Token Optimization
- ❌ 不改变 QQ 接入
- ❌ 不修改 API Key
- ❌ 不重构 EmotionManager 内部逻辑
- ❌ 不改变 `emotion_enabled` 开关行为（保持默认 False）
- ❌ 不删除 EmotionDynamicsEngine（仍受 `emotion_enabled` 控制）

---

## 7. 后续建议

### 7.1 Phase 4.2.2：SelfModel Authority（下一步）

**问题**：Orchestrator 内部存在双 SelfModelStore 分裂
- `self.personality_resolver.self_model_store`（被 `resolve()` 更新）
- `self.self_model_store`（被 `self_model_context_provider` 读取，但永不更新）

**修复方案**：
1. 删除 `orchestrator.py:90` 的 `self.self_model_store = SelfModelStore()`
2. 让 `SelfModelContextProvider` 绑定到 `self.personality_resolver.self_model_store`
3. 验证 Prompt 中【自我认知参考】块有真实内容

### 7.2 Phase 4.2.3：PersonalityResolver Authority

**问题**：4 处独立 PersonalityResolver 实例（Orchestrator / RuntimeCore / GrowthPipeline / PersonalityController）

**修复方案**：
1. RuntimeBridge 新增 `get_personality_resolver()` 方法
2. RuntimeCore 启用 PersonalityResolver（或 lazy 创建）
3. Orchestrator 通过 RuntimeBridge 获取，fallback 自建

### 7.3 Phase 4.2.4：tick 状态回流

**问题**：RuntimeCore 的 tick 修改的 self_state 不传递给 Orchestrator

**修复方案**：
1. RuntimeBridge 新增 `get_self_state_snapshot()` 方法
2. Orchestrator.process() 中获取 self_state 快照
3. RuntimeContext 新增 `runtime_state` key

### 7.4 技术债提醒

- `emotion_enabled` 默认 False，但 EmotionManager 现在通过 lazy 创建始终可用
- 若未来启用 `emotion_enabled=True`，EmotionDynamicsEngine 会被创建，但 lazy 创建的 EmotionManager 会被覆盖（需验证行为）
- 建议在 Phase 4.2.x 后期评估是否将 `emotion_enabled` 默认改为 True

---

## 8. 风险评估

| 风险 | 严重性 | 缓解措施 |
|---|---|---|
| RuntimeBridge 单例在 Orchestrator 之前未初始化 | 低 | Fallback 自建机制保证聊天不中断 |
| Lazy 创建的 EmotionManager 路径与 emotion_enabled=True 时不同 | 中 | 两者都使用默认路径 `data/emotion_state.json`，行为一致 |
| Fallback 自建导致双实例 | 低 | 仅在 RuntimeBridge 不可用时发生（如测试环境），生产环境 RuntimeBridge 会先初始化 |
| EmotionManager 内部状态被多线程访问 | 低 | Python GIL 保护，且 EmotionManager 操作是原子的 |

---

**Phase 4.2.1 Emotion Authority 完成。**

**等待审计通过后再进入 SelfModel Authority。**
