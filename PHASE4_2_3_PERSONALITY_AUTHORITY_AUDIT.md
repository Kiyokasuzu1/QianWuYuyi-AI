# QianWuYuyi-AI Phase 4.2.3 PersonalityResolver Authority 审计报告

**审计日期**：2026-07-30
**审计性质**：只读研究，未修改任何代码
**目标**：建立 PersonalityResolver Runtime State Authority，解决多实例分裂问题

---

## 执行摘要

本次审计确认存在 **4 个 PersonalityResolver 创建点**，但实际风险分层与预期不同：

1. **Orchestrator #1**（活跃，生产路径）— 唯一参与实时对话的实例
2. **RuntimeCore #2**（条件创建，默认不存在）— 仅 `adapters_enabled=True` 时创建
3. **GrowthPipeline #3**（独立流程）— 不参与实时对话，接收外部传入 state
4. **PersonalityController #4**（死代码）— 已标记 Deprecated，仅 TYPE_CHECKING 导入

**核心发现**：实际生产路径只有 Orchestrator #1，但 `_trait_states`、`growth_history`、`personality_history` 是纯内存状态，多实例间不共享。当 `adapters_enabled=True` 时存在真正的分裂风险。

---

## 1. PersonalityResolver 创建路径审计

### 1.1 所有 PersonalityResolver 实例化位置

| # | 位置 | 文件:行号 | 持有者 | 初始化参数 | 活跃状态 |
|---|---|---|---|---|---|
| **1** | Orchestrator 直接创建 | [orchestrator.py:89](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/orchestrator.py#L89) | `orchestrator.personality_resolver` | 无参（默认） | **生产活跃** |
| **2** | RuntimeCore 条件创建 | [runtime_core.py:384](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/runtime_core.py#L384) | `runtime_core.personality_resolver` | 无参（默认） | **条件活跃**（adapters_enabled=True） |
| **3** | GrowthPipeline 内部创建 | [pipeline.py:65](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/growth/pipeline.py#L65) | `pipeline.resolver` | `state=growth_engine.state, relationship_state=...` | **独立流程** |
| **4** | PersonalityController fallback | [personality_controller.py:19](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/personality/personality_controller.py#L19) | `controller.resolver` | 无参（默认） | **死代码** |

### 1.2 各创建点详细分析

#### 创建点 #1：Orchestrator（生产活跃）

```python
# orchestrator.py:89
self.personality_resolver = PersonalityResolver()
```

- **调用路径**：`api_server.py` → 全局单例 `orchestrator` → `orchestrator.process()`
- **生产入口**：[api_server.py:22](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/api_server.py#L22) 和 [initiative_sender.py:26](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/initiative_sender.py#L26)
- **使用场景**：实时对话人格解析、主动消息生成
- **关键**：这是唯一参与实时对话的实例

#### 创建点 #2：RuntimeCore（条件创建，默认不存在）

```python
# runtime_core.py:384（在 adapters_enabled=True 条件块内，第 340 行）
self.personality_resolver = PersonalityResolver()
```

- **启用条件**：`config.get("adapters_enabled", False)` — **默认 False**
- **默认状态**：**不存在**（`self.personality_resolver = None`）
- **风险**：当 `adapters_enabled=True` 时，RuntimeCore 的 PersonalityResolver 与 Orchestrator 的完全独立
- **Phase 4.2.2 已部分处理**：`get_self_model_store()` 优先使用 RuntimeCore 的 PersonalityResolver 内部 store

#### 创建点 #3：GrowthPipeline（独立流程，不参与实时对话）

```python
# pipeline.py:65
self.resolver = PersonalityResolver(
    state=self.growth_engine.state,        # 接收外部传入的 GrowthState
    relationship_state=self.relationship_state
)
```

- **使用场景**：批量成长整理（`run_full_consolidation`）、增量成长更新（`incremental_update`）
- **调用方**：仅 `main.py`（测试入口）和测试文件
- **关键**：Orchestrator **不引用 GrowthPipeline**（grep 确认无匹配）
- **不参与实时对话**：GrowthPipeline 是独立的成长整理流程

#### 创建点 #4：PersonalityController（死代码）

```python
# personality_controller.py:19
self.resolver = resolver or PersonalityResolver()
```

- **状态**：文件头部标记 **"已弃用"**
- **引用**：仅在 [yuyi_cognitive_core.py:70](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/core/yuyi_cognitive_core.py#L70) 的 `TYPE_CHECKING` 块中导入（类型注解，非实际使用）
- **实际**：**从未被实例化使用**

---

## 2. PersonalityResolver 内部状态分析

### 2.1 内部状态清单

| # | 内部状态 | 类型 | 持久化 | 多实例分裂风险 |
|---|---|---|---|---|
| 1 | `self.state` (GrowthState) | 持久化 | `data/growth_state.json` | **低**（多实例读同一文件） |
| 2 | `self.relationship_state` | 持久化 | `data/relationship_state.json` | **低**（多实例读同一文件） |
| 3 | `self._trait_states` | 纯内存 | 无 | **高**（不同实例完全独立） |
| 4 | `self.growth_history` (PersonalityGrowthHistory) | 纯内存 | 无 | **高**（不同实例完全独立） |
| 5 | `self.personality_history` (PersonalityHistory) | 纯内存 | 无 | **高**（不同实例完全独立） |
| 6 | `self.self_model_store` (SelfModelStore) | 纯内存 | 无 | **高**（Phase 4.2.2 已修复 Orchestrator 内部） |
| 7 | `self.evolution_engine` | 纯内存 | 无 | **中**（状态由 _trait_states 驱动） |
| 8 | `self.accumulator` | 纯计算 | 无 | **低**（无状态，纯函数计算） |
| 9 | `self.behavior_resolver` | 依赖 relationship_state | 间接持久化 | **低**（跟随 relationship_state） |
| 10 | `self.growth_records` | 构造注入 | 调用方决定 | **低**（默认空列表） |

### 2.2 持久化状态详解

#### GrowthState（持久化）

```python
# growth_state.py:13-17
class GrowthState:
    def __init__(self, state_path: str = "data/growth_state.json"):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = None
        self._load()  # 构造时加载
```

- 多个 PersonalityResolver 实例各自创建 GrowthState，但都读取同一文件
- **分裂表现**：各实例的 GrowthState 内存缓存可能不同步（一个实例写入后，另一个实例的内存缓存仍是旧值）

#### PersonalityGrowthHistory（纯内存）

```python
# personality_growth_record.py:103-104
class PersonalityGrowthHistory:
    def __init__(self):
        self.records: List[PersonalityGrowthRecord] = []  # 纯内存
```

- **无持久化**，无 `save()` / `load()` 方法
- 不同实例的 `growth_history` 完全独立
- SelfModelStore 的 `should_update()` 依赖 `history.count()`，不同实例的 count 不同

#### PersonalityHistory（纯内存）

```python
# personality_history.py:32-33
class PersonalityHistory:
    def __init__(self):
        self.snapshots: List[PersonalitySnapshot] = []  # 纯内存
```

- **无持久化**
- `record_change()` 只写入当前实例的内存列表
- 不同实例的人格演化历史完全独立

### 2.3 resolve() 的副作用分析

`PersonalityResolver.resolve()` 不仅读取状态，还会**写入**以下内部状态：

```python
# personality_resolver.py:241-243（resolve 内部）
if self.self_model_store.should_update(self.growth_history):
    self.self_model_store.update(self.growth_history, self._trait_states)
```

| resolve() 的写入目标 | 写入条件 | 分裂影响 |
|---|---|---|
| `self._trait_states[dim]` | 每次调用都更新 | 不同实例的 trait_states 独立演化 |
| `self.personality_history.record_change()` | `abs(new_value - old_value) > 0.005` | 不同实例记录不同的人格变化历史 |
| `self.self_model_store.update()` | `should_update()` 返回 True | Phase 4.2.2 已修复 Orchestrator 内部分裂 |

---

## 3. 当前状态流图

### 3.1 生产路径（实时对话）

```
api_server.py / initiative_sender.py
      ↓
Orchestrator（全局单例）
      ↓
PersonalityResolver #1
  ├─ state (GrowthState)        → 读 data/growth_state.json
  ├─ relationship_state         → 读 data/relationship_state.json
  ├─ _trait_states              → 纯内存（实例 #1 独有）
  ├─ growth_history             → 纯内存（实例 #1 独有）
  ├─ personality_history        → 纯内存（实例 #1 独有）
  └─ self_model_store           → 共享（Phase 4.2.2 已修复）
      ↓
PersonalityVector → RuntimeContext → engine.generate()
```

### 3.2 RuntimeCore 路径（adapters_enabled=True 时）

```
RuntimeCore（adapters_enabled=True 时）
      ↓
PersonalityResolver #2
  ├─ state (GrowthState)        → 读 data/growth_state.json（同一文件）
  ├─ relationship_state         → 读 data/relationship_state.json（同一文件）
  ├─ _trait_states              → 纯内存（实例 #2 独有，与 #1 不同步）
  ├─ growth_history             → 纯内存（实例 #2 独有，与 #1 不同步）
  ├─ personality_history        → 纯内存（实例 #2 独有，与 #1 不同步）
  └─ self_model_store           → 独立（与 #1 不同步）
      ↓
（仅用于 RuntimeCore 内部成长/演化逻辑）
```

### 3.3 GrowthPipeline 路径（独立流程）

```
main.py / 测试文件
      ↓
GrowthPipeline
      ├─ growth_engine (GrowthEngine)
      │     └─ state (GrowthState)  → 读 data/growth_state.json（同一文件）
      ├─ relationship_state         → 读 data/relationship_state.json（同一文件）
      └─ resolver = PersonalityResolver #3
          ├─ state                  → 使用 growth_engine.state（共享引用）
          ├─ _trait_states           → 纯内存（实例 #3 独有）
          ├─ growth_history          → 纯内存（实例 #3 独有）
          └─ personality_history    → 纯内存（实例 #3 独有）
      ↓
resolve() → 返回 PersonalityVector（仅用于 GrowthPipeline 返回值）
```

### 3.4 分裂风险矩阵

| 实例对 | GrowthState | _trait_states | growth_history | personality_history | self_model_store |
|---|---|---|---|---|---|
| #1 vs #2 | 同文件（可能缓存不同步） | **独立** | **独立** | **独立** | **独立**（Phase 4.2.2 部分修复） |
| #1 vs #3 | 同文件 | **独立** | **独立** | **独立** | **独立** |
| #2 vs #3 | 同文件 | **独立** | **独立** | **独立** | **独立** |

---

## 4. RuntimeCore 作为唯一 Authority 的可行性分析

### 4.1 当前 RuntimeCore 的 PersonalityResolver 状态

```python
# runtime_core.py:268
self.personality_resolver: Optional[PersonalityResolver] = None

# runtime_core.py:340-387（adapters_enabled=True 条件块内）
if self.config.get("adapters_enabled", False):
    ...
    self.personality_resolver = PersonalityResolver()  # 第 384 行
```

- `adapters_enabled` 默认 False → RuntimeCore 默认不创建 PersonalityResolver
- `get_self_model_store()` 已优先使用 RuntimeCore 的 PersonalityResolver 内部 store（Phase 4.2.2）

### 4.2 可行性评估

| 评估维度 | 结论 | 说明 |
|---|---|---|
| RuntimeCore lazy 创建 PersonalityResolver | **可行** | 与 EmotionManager / SelfModelStore 模式一致 |
| RuntimeBridge 转发 | **可行** | 新增 `get_personality_resolver()` 转发接口 |
| 注入已有 resolver | **不推荐** | PersonalityResolver 有构造参数（state, relationship_state），直接注入可能改变行为 |
| 影响 GrowthPipeline | **不影响** | GrowthPipeline 不参与实时对话，且接收外部 state，不修改 |
| 影响 PersonalityController | **不影响** | 死代码，不修改 |

### 4.3 与 Phase 4.2.1/4.2.2 的一致性

| Phase | Authority | RuntimeCore 方法 | RuntimeBridge 方法 | Orchestrator 行为 |
|---|---|---|---|---|
| 4.2.1 | EmotionManager | `get_emotion_manager()` lazy 创建 | `get_emotion_manager()` 转发 | 优先获取，fallback 自建 |
| 4.2.2 | SelfModelStore | `get_self_model_store()` lazy 创建 | `get_self_model_store()` 转发 | 优先获取，注入到 resolver |
| **4.2.3** | **PersonalityResolver** | **`get_personality_resolver()` lazy 创建** | **`get_personality_resolver()` 转发** | **优先获取，fallback 自建** |

---

## 5. 最小修改方案

### 5.1 设计原则

- **不重构** PersonalityResolver 内部逻辑
- **不修改** GrowthPipeline（独立流程，不参与实时对话）
- **不修改** PersonalityController（死代码）
- **保留 fallback**（RuntimeBridge 不可用时自建）
- **保证初始化失败不影响聊天**
- **与 Phase 4.2.1/4.2.2 架构一致**

### 5.2 目标架构

```
RuntimeCore
    ↓ get_personality_resolver()  ← lazy 创建权威实例
    │
PersonalityResolver（唯一实例）
    ├─ state (GrowthState)         → 读 data/growth_state.json
    ├─ relationship_state          → 读 data/relationship_state.json
    ├─ _trait_states               → 内存状态（共享）
    ├─ growth_history              → 内存状态（共享）
    ├─ personality_history         → 内存状态（共享）
    └─ self_model_store           → Phase 4.2.2 已共享
    │
RuntimeBridge
    ↓ get_personality_resolver()  ← 转发
    │
Orchestrator
    ↓ personality_resolver（引用共享实例）
    ↓ resolve() → PersonalityVector → RuntimeContext → engine.generate()
```

### 5.3 修改方案分层

#### 方案 A：RuntimeCore lazy 创建（核心）

**修改文件**：
1. `src/runtime/runtime_core.py` — 新增 `get_personality_resolver()` 方法（lazy 创建）
2. `src/runtime/runtime_bridge.py` — 新增 `get_personality_resolver()` 转发接口
3. `src/orchestrator.py` — 优先通过 RuntimeBridge 获取，fallback 自建

**RuntimeCore.get_personality_resolver() 设计**：

```python
def get_personality_resolver(self) -> Optional["PersonalityResolver"]:
    """
    获取 PersonalityResolver 权威实例（Phase 4.2.3 Personality Authority）。

    - 若 adapters_enabled=True 且已创建，返回已有实例
    - 否则 lazy 创建一个基础 PersonalityResolver
    - 创建失败返回 None，调用方需自行 fallback
    """
    # 优先使用已创建的 PersonalityResolver（adapters_enabled=True 时）
    if self.personality_resolver is not None:
        return self.personality_resolver

    # Lazy 创建基础 PersonalityResolver
    try:
        from src.personality.personality_resolver import PersonalityResolver as _Resolver
        if not hasattr(self, "_lazy_personality_resolver") or self._lazy_personality_resolver is None:
            logger.info("RuntimeCore: lazy 创建基础 PersonalityResolver（Personality Authority）")
            self._lazy_personality_resolver = _Resolver()
        return self._lazy_personality_resolver
    except Exception as _e:
        logger.warning(f"RuntimeCore: PersonalityResolver lazy 创建失败: {_e}")
        return None
```

#### 方案 B：Orchestrator 获取共享实例

**Orchestrator.__init__() 设计**：

```python
# PersonalityResolver（Phase 4.2.3 Personality Authority）
# 优先通过 RuntimeBridge 获取 RuntimeCore 持有的权威实例
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

# Fallback：RuntimeBridge 不可用时自建（保持向后兼容）
if self.personality_resolver is None:
    self.personality_resolver = PersonalityResolver()
    print("[Orchestrator] PersonalityResolver fallback 自建")
```

#### 方案 C：SelfModelStore 注入修复

当共享 PersonalityResolver 后，需要确保 SelfModelStore 也正确注入：

```python
# 关键：将共享 SelfModelStore 注入到共享 PersonalityResolver
# （Phase 4.2.2 已建立的逻辑，需适配共享 resolver）
if hasattr(self.personality_resolver, "self_model_store"):
    self.personality_resolver.self_model_store = self.self_model_store
```

### 5.4 不修改的部分

| 模块 | 原因 |
|---|---|
| GrowthPipeline | 独立流程，不参与实时对话，接收外部 state |
| PersonalityController | 死代码，已标记 Deprecated |
| PersonalityResolver 内部逻辑 | 不重构，仅通过引用共享 |
| Memory / Emotion / Relationship | 禁止修改 |
| Growth 核心逻辑 | 禁止修改 |

---

## 6. 风险分析

| 风险 | 严重性 | 说明 | 缓解措施 |
|---|---|---|---|
| 共享 resolver 的 _trait_states 被多线程访问 | 低 | Python GIL 保护，且 resolve() 操作是同步的 | 无需额外处理 |
| RuntimeBridge 未初始化 | 低 | Fallback 自建保证聊天不中断 | 与 Phase 4.2.1/4.2.2 一致 |
| GrowthPipeline 仍使用独立 resolver | 低 | 不参与实时对话，独立流程 | 不修改，保持现状 |
| PersonalityController 死代码 | 无 | 从未被实例化 | 不修改 |
| 共享 resolver 后 SelfModelStore 注入 | 中 | Phase 4.2.2 已建立注入逻辑，需适配 | 测试验证注入正确 |

---

## 7. 测试计划

### 7.1 新增测试文件

`tests/test_personality_authority.py`

### 7.2 测试覆盖

| # | 测试类 | 覆盖项 |
|---|---|---|
| 1 | TestPersonalityResolverShared | RuntimeCore 与 Orchestrator 使用同一 PersonalityResolver |
| 2 | TestTraitStateConsistency | 多次 resolve() 后 TraitState 一致 |
| 3 | TestPersonalityReachesPrompt | Personality 更新能进入 Prompt |
| 4 | TestSelfModelStillWorks | SelfModel 仍正常工作（Phase 4.2.2 回归） |
| 5 | TestFallbackCompatibility | Fallback 兼容性 |
| 6 | TestEmotionAuthorityRegression | Phase 4.2.1 Emotion Authority 回归 |
| 7 | TestSelfModelAuthorityRegression | Phase 4.2.2 SelfModel Authority 回归 |

### 7.3 关键测试用例

```python
def test_orchestrator_uses_runtime_core_personality_resolver(self):
    """Orchestrator 应使用 RuntimeCore 的 PersonalityResolver。"""
    bridge = _init_runtime_bridge()
    runtime_core = bridge.get_runtime_core()
    core_resolver = runtime_core.get_personality_resolver()
    assert core_resolver is not None

    from src.orchestrator import Orchestrator
    orch = Orchestrator()
    assert orch.personality_resolver is core_resolver, \
        "Orchestrator 的 PersonalityResolver 应与 RuntimeCore 的是同一实例"

def test_trait_states_consistent_after_multiple_resolves(self):
    """多次 resolve() 后 TraitState 应一致。"""
    bridge = _init_runtime_bridge()
    from src.orchestrator import Orchestrator
    orch = Orchestrator()

    orch.personality_resolver.resolve()
    states_1 = orch.personality_resolver.get_trait_states()

    orch.personality_resolver.resolve()
    states_2 = orch.personality_resolver.get_trait_states()

    # 相同实例的 trait_states 应保持一致（除非 GrowthState 变化）
    for dim in states_1:
        assert states_1[dim]["current_value"] == states_2[dim]["current_value"]
```

---

## 8. 实施顺序

| 顺序 | 步骤 | 说明 |
|---|---|---|
| 1 | RuntimeCore 新增 `get_personality_resolver()` | lazy 创建权威实例 |
| 2 | RuntimeBridge 新增 `get_personality_resolver()` | 转发接口 |
| 3 | Orchestrator 优先通过 RuntimeBridge 获取 | 保留 fallback |
| 4 | 确保 SelfModelStore 注入到共享 resolver | Phase 4.2.2 兼容 |
| 5 | 编写 `tests/test_personality_authority.py` | 22+ 测试 |
| 6 | 运行 Phase 4.2.1/4.2.2 回归测试 | 确保无回归 |

---

**审计完成。等待确认后再编码。**

**分支**：`fix/personality-authority`
**预计修改文件**：3 个（runtime_core.py, runtime_bridge.py, orchestrator.py）
**预计新增测试**：20+ 个
**不修改**：GrowthPipeline, PersonalityController, PersonalityResolver 内部逻辑
