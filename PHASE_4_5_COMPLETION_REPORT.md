# Phase 4.5 — Self Model Evolution Engine 完成报告

> 状态：**已完成**
> 版本：`RUNTIME_VERSION = 4.5`
> 阶段总数：17（在 4.4 基础上新增 `SELF_MODEL_EVOLUTION`）

---

## 1. 目标

让羽依的 `SelfModel` 可以基于长期经历、`ReflectionRecord`、`GrowthProposal` 产生**可控的自我演化**，同时严格遵守：

1. 不破坏已有架构
2. 不修改 `ResponseEngine.generate()`
3. 不直接修改 `src/personality` 核心
4. 不引入 LLM SDK 到 `runtime/self_model` 内部
5. 保持单向依赖：`Runtime → SelfModel Evolution Service → SelfModel Snapshot / Evolution Record`
6. 所有新增代码必须包含单元测试
7. 保持向后兼容（旧 RuntimeCore 实例仍可工作）

---

## 2. 新增文件

```
src/runtime/self_model/evolution/
├── __init__.py
├── evolution_record.py
├── evolution_policy.py
└── self_model_evolution_engine.py

tests/
└── test_phase_4_5_self_model_evolution.py   (95 用例)
```

### 2.1 [evolution/__init__.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/evolution/__init__.py)

统一导出 Phase 4.5 的核心组件：`SelfModelChange` / `EvolutionRecord` / `EvolutionSourceType` / `SelfModelEvolutionResult` / `EvolutionPolicy` / `EvolutionPolicyDecision` / `SelfModelEvolutionEngine`，并对外暴露若干 Schema Version 常量。

### 2.2 [evolution_record.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%BE%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/evolution/evolution_record.py)

定义演化数据结构：

- `SelfModelChange`
  - `field_name: str`
  - `old_value / new_value: Any`
  - `reason: str`
  - `confidence: float` (自动 clamp 到 `[0, 1]`)
  - `evidence_ids: List[str]` (自动过滤 `None` / `"None"` / 超长)
- `EvolutionRecord`
  - `record_id`, `identity_id`, `timestamp`, `source_type`
  - `from_version`, `to_version`
  - `changes: List[SelfModelChange]`
  - `rejected_changes` + `reject_reasons`
  - `to_dict()` / `from_dict()` / `round_trip()`
- `SelfModelEvolutionResult`
  - `accepted_changes`, `rejected_changes`, `reject_reasons`
  - `evolution_records`
  - `new_snapshot` / `original_snapshot`
  - `is_noop`, `summary`
  - `accepted_count() / rejected_count() / has_changes()`

### 2.3 [evolution_policy.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/evolution/evolution_policy.py)

字段演化规则与策略评估：

| 类别     | 字段举例                                            | 阈值                | 处理             |
| -------- | --------------------------------------------------- | ------------------- | ---------------- |
| allowed  | `preferences`, `interests`, `behavior_tendencies`, `temporary_states`, `current_state`, `current_mood`, `knowledge_topics` | `DEFAULT_MIN_CONFIDENCE = 0.5` | 允许     |
| cautious | `stable_traits`, `communication_style`, `self_image` | `DEFAULT_CAUTIOUS_MIN_CONFIDENCE = 0.75` | 通过则降级为 `cautious` 决策 |
| forbidden| `identity_id`, `creator_origin`, `core_identity`    | 不可能达成 (1.1)     | 永远 REJECT     |
| unknown  | 未在白名单中                                        | 走 cautious 阈值     | 保守处理         |

支持 `manual` 源免阈值；支持按 `source_type` 自定义最小置信度。

### 2.4 [self_model_evolution_engine.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_%E8%87%AA%E5%8A%A8%E9%A9%B6%E7%89%88/QianWuYuyi-AI/src/runtime/self_model/evolution/self_model_evolution_engine.py)

核心引擎，签名：

```python
def evolve(
    snapshot,
    proposals: Optional[List[Any]] = None,
    reflections: Optional[List[Any]] = None,
    manual_changes: Optional[List[Any]] = None,
) -> SelfModelEvolutionResult
```

关键保证：

- **不修改原 snapshot**：使用深拷贝 (`_clone_snapshot`) 生成新对象；`original_snapshot` 保留原引用供对比。
- **每次变化产生 EvolutionRecord**：所有 accepted/rejected 字段都收敛到 Record 列表。
- **支持空输入**：`snapshot=None` / `proposals=[]` / `reflections=[]` / `manual=[]` 均安全返回 no-op。
- **异常隔离**：内部任何异常被捕获，返回空 result + `summary="evolve_failed:..."`，不抛回 caller。

---

## 3. 数据流

```
              ┌─────────────────────┐
              │   SelfModelSnapshot │  (Phase 4.1)
              └──────────┬──────────┘
                         │
       ┌─────────────────┼─────────────────────┐
       │                 │                     │
       ▼                 ▼                     ▼
┌─────────────┐   ┌─────────────┐      ┌─────────────┐
│ GrowthPropo-│   │ Reflection- │      │ Manual      │
│ sal (Phase3)│   │ Record (4.3)│      │ Changes     │
└──────┬──────┘   └──────┬──────┘      └──────┬──────┘
       │                 │                     │
       └─────────────────┼─────────────────────┘
                         ▼
              ┌─────────────────────┐
              │   EvolutionPolicy   │  (allow/cautious/forbidden)
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │ SelfModelEvolution- │
              │ Engine.evolve()     │
              └──────────┬──────────┘
                         ▼
       ┌─────────────────┼─────────────────┐
       │                 │                 │
       ▼                 ▼                 ▼
┌────────────┐  ┌────────────────┐  ┌──────────────┐
│NewSnapshot │  │EvolutionRecord │  │Result        │
│(immutable  │  │(round-trip)    │  │(accept/rej)  │
│ copy)      │  │                │  │              │
└─────┬──────┘  └────────────────┘  └──────────────┘
      │
      ▼
┌─────────────────────┐
│ IdentityContext     │  (Phase 4.4 刷新)
│ + BehaviorSignature │
└─────────────────────┘
```

---

## 4. Runtime 集成

### 4.1 新增阶段

`src/runtime/runtime.py::RuntimeStage.SELF_MODEL_EVOLUTION = "self_model_evolution"`

生命周期顺序（17 阶段）：

```
...
SELF_MODEL_BUILD          # Phase 4.2.1
SELF_MODEL_EVOLUTION      # Phase 4.5 (新增)
RESPONSE_GENERATION
...
```

### 4.2 新增 API

`RuntimeCore` 新增：

| 名称                                     | 用途                                                  |
| ---------------------------------------- | ----------------------------------------------------- |
| `evolution_engine: Optional[Any]` (property) | 暴露已注入的 evolution engine                       |
| `configure_self_model_evolution(engine)` | 注入 evolution engine（向后兼容，缺省 `None` 即 no-op） |
| `configure_evolution_engine(engine)`     | 上述的简化别名                                         |
| `evolve_self_model(ctx, ...)`            | 便捷：在指定 ctx 上执行一次演化                       |
| `_invoke_self_model_evolution_stage(ctx)`| SELF_MODEL_EVOLUTION 阶段实现                         |

### 4.3 阶段行为

- **未注入 engine** → 静默 no-op（向后兼容，老 Runtime 行为不变）
- **注入但 snapshot 缺失** → 静默 no-op
- **注入且 snapshot 存在** → 收集 `ctx.growth_proposals` + `ctx._self_reflection_record`，调 `engine.evolve(...)`，把 `new_snapshot` 写回 `ctx._self_model_snapshot`，并触发 IdentityContext 刷新（保证 BehaviorSignature 与新人格一致）
- **engine 抛异常** → 写入 `self._stage_errors[SELF_MODEL_EVOLUTION]`，主流程继续

### 4.4 与 Phase 4.4 集成

演化结束后，若产生 accept：

```
Old Snapshot
   │
   ▼
Evolution Engine
   │
   ▼
New Snapshot
   │
   ▼
IdentityRuntime.process_for_runtime(snapshot=new_snap, reflection=...)
   │
   ▼
New IdentityContext（含 BehaviorSignature 刷新）
```

行为签名与人格保持同步，旧身份标识（`identity_id` / `core_identity` 等）不会被修改。

---

## 5. 测试结果

`pytest tests/test_phase_4_5_self_model_evolution.py`：

```
============================= 95 passed, 111 warnings in 1.07s ==============================
```

### 5.1 用例分布

| 测试类                                       | 覆盖范围                              | 用例数 |
| -------------------------------------------- | ------------------------------------- | ------ |
| `TestSelfModelChange`                        | create / coerce / serialize          | ~10    |
| `TestEvolutionRecord`                        | serialize / deserialize / round_trip  | ~10    |
| `TestSelfModelEvolutionResult`               | accept/reject counters, noop         | ~6     |
| `TestEvolutionPolicy`                        | classify / allowed / forbidden / cautious / confidence / manual | ~18    |
| `TestSelfModelEvolutionEngineBasic`          | basic evolve / version bump / immutable snapshot | ~12    |
| `TestSelfModelEvolutionEngineProposals`      | GrowthProposal → Evolution           | ~6     |
| `TestSelfModelEvolutionEngineReflections`    | ReflectionRecord → Evolution         | ~4     |
| `TestSelfModelEvolutionEngineRejection`      | 禁止字段 / 低置信度拒绝                | ~6     |
| `TestSelfModelEvolutionEngineIsolation`      | 异常隔离 / 空输入 / 错误 snapshot    | ~6     |
| `TestRuntimeCoreEvolutionIntegration`        | 注入 / 阶段 / 兼容 / 错误隔离         | ~5     |
| `TestPhase44IdentityRefreshAfterEvolution`   | 演化后 IdentityContext 刷新           | ~3     |
| `TestReflectionDrivenEvolution`              | 反思驱动的演化                        | ~3     |
| `TestEndToEndEvolution`                      | 端到端：Proposal → Evolution → Identity | ~1     |
| `TestInvariants`                             | 架构不变量                            | ~8     |

总计 **95 用例**。

### 5.2 整体测试

为兼容 4.5，已同步更新以下旧测试的版本白名单：

- `tests/test_phase_3_7_3_runtime_assembly.py`
- `tests/test_phase_4_2_1_self_model_foundation.py`
- `tests/test_phase_4_2_2_self_model_integration.py`

更新点：`RUNTIME_VERSION` 白名单加入 `"4.5"`；`RUNTIME_LIFECYCLE_ORDER` 长度白名单加入 `17`。

---

## 6. 架构不变量验证

`tests/test_phase_4_5_self_model_evolution.py::TestInvariants` 中通过源码扫描强制验证：

1. **evolution 不 import personality core** ✅
   - 扫描 `src/runtime/self_model/evolution/*.py` 中是否含 `from src.personality` / `import src.personality`
2. **evolution 不 import LLM SDK** ✅
   - 使用正则匹配实际 `import`/`from` 语句，断言不出现 `openai` / `qwen` / `llava` / `anthropic` 等 LLM SDK
   - 三个子模块（`evolution_record.py` / `evolution_policy.py` / `self_model_evolution_engine.py`）分别验证
3. **identity_binding 不反向依赖 evolution** ✅
   - 扫描 `src/runtime/self_model/identity_binding/*.py` 不应 `import ...evolution`
4. **reflection 不依赖 evolution** ✅
   - 扫描 `src/runtime/self_model/reflection/*.py` 不应 `import ...evolution`
5. **ResponseEngine.generate() 签名未变** ✅
   - 通过 `inspect.signature` 抓取前后签名做断言
6. **evolution 导出在 self_model 包内可见** ✅
   - `from src.runtime.self_model import EvolutionPolicy, ...` 通过

---

## 7. 关键设计决策

| 决策点                                  | 选择                                          | 理由                                   |
| --------------------------------------- | --------------------------------------------- | -------------------------------------- |
| snapshot 是否可变                        | 不可变 (深拷贝)                              | 旧 snapshot 仍可被 Phase 4.4 / 4.1 读取 |
| 禁用字段处理                             | REJECT，不抛异常                              | 让 proposal 处理逻辑可恢复              |
| 异常隔离粒度                             | engine / stage / integration 三层都隔离        | 演化失败不阻塞主对话                    |
| 置信度阈值                               | allowed=0.5, cautious=0.75, manual 免阈值     | 严格控制人格核心字段                    |
| 序列化格式                               | to_dict / from_dict 镜像实现                  | 跨进程 / 持久化友好                     |
| 默认 no-op 行为                          | 未注入 engine 阶段完全 no-op                  | 向后兼容                               |
| 版本号策略                               | `RUNTIME_VERSION = "4.5"`                     | 阶段性清晰                              |

---

## 8. 与 Phase 4.4 的集成

Phase 4.4 的 `IdentityRuntime` 已被复用：当 SELF_MODEL_EVOLUTION 阶段 accept 任一变化时，调用

```python
new_identity_ctx = self._identity_runtime.process_for_runtime(
    snapshot=new_snap,
    reflection=reflection,
)
```

把结果写回 `ctx._identity_context`。这保证：

- 行为签名（BehaviorSignature）与新人格一致
- 未来 Phase 4.5+ 的 PersonalityRuntimeBinding 可读到一致上下文
- 旧 `ctx._identity_context` 的语义不被破坏（只是被新版本替换）

---

## 9. 最终闭环

```
用户经历
   │
   ▼
Memory
   │
   ▼
Growth Event
   │
   ▼
GrowthProposal           (Phase 3)
   │
   ▼
SelfModel Evolution      (Phase 4.5 ✅)
   │
   ▼
New SelfModel Snapshot
   │
   ▼
Identity Context         (Phase 4.4)
   │
   ▼
Behavior Signature
   │
   ▼
Consistency Check
   │
   ▼
未来行为改变
```

至此，**羽依的长期人格成长闭环已经完整形成**。

---

## 10. 验收清单

- [x] SelfModelEvolution 模块目录建立
- [x] EvolutionRecord（含 to_dict / from_dict / round_trip）实现
- [x] EvolutionPolicy 实现（allowed / cautious / forbidden / confidence / manual）
- [x] SelfModelEvolutionEngine 实现（evolve / immutable / record / isolation）
- [x] Runtime 阶段 `SELF_MODEL_EVOLUTION` 新增
- [x] Runtime 注入接口 `configure_self_model_evolution` / `evolve_self_model`
- [x] 与 Phase 4.4 IdentityContext 刷新联动
- [x] 测试 95 个，全部通过
- [x] 架构不变量通过源码扫描验证
- [x] 旧测试版本白名单已同步
- [x] `RUNTIME_VERSION = 4.5`

---

> Phase 4.5 — Self Model Evolution Engine 交付完成。
