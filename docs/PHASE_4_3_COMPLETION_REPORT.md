# Phase 4.3 Completion Report — Self Reflection System

**Date**: 2026-07-31
**Phase**: 4.3
**Status**: ✅ Complete
**Runtime Version**: 4.3
**Tests**: 91 passed (Phase 4.3 only) / 483 passed (Phase 4.2.1 ~ 4.3 + Runtime integration)

## 1. 目标

在 Phase 4.2.x(SelfModel 基础 / 集成 / 运行时消费 / 历史 & 审计)之上,实现 **Self Reflection Layer**:

- 读取 `GrowthAuditRecord`
- 分析 SelfModel 变化原因
- 生成基于证据的 `ReflectionRecord`
- 保存长期反思历史
- 为未来 Runtime 回复提供可选的 self reflection context

## 2. 核心设计

### 2.1 架构

```
GrowthAuditRecord
        |
        v
ReflectionEngine  (启发式 + evidence-based)
        |
        v
ReflectionRecord  (observation / interpretation / relation_to_values / confidence)
        |
        v
ReflectionStore   (append-only, FIFO 容量淘汰)
        |
        v
RuntimeCore
        |
        v
ctx._self_reflection_record
        |
        v
ResponseAdapter.build_request()
        |
        v
personality_context["self_reflection_text" / "self_reflection_data"]
```

### 2.2 依赖方向(单向)

```
RuntimeCore
  → ReflectionEngine
  → ReflectionStore
  → SelfReflectionContextProvider
  → ResponseAdapter (Phase 4.2.3 扩展)
        ↑
Reflection 依赖: Audit → SelfModel (只读)
```

约束:

- Reflection 模块**不**反向依赖 Runtime / Personality / Memory / Emotion / Growth / ResponseEngine
- Audit 模块**不**反向依赖 Reflection 模块
- Reflection 不直接写 Personality,只生成"理解记录"
- Reflection 不修改 ResponseEngine.generate() 签名,只通过 `personality_context` 注入

## 3. 关键约束 — 全部满足

| 约束 | 实现 |
|---|---|
| 不修改核心 Personality | ✅ Reflection 模块 0 个 import 自 `src.personality.*` (AST 验证) |
| 不修改 ResponseEngine | ✅ `ResponseEngine.generate()` 签名未变,reflection 通过 `personality_context` 注入 |
| 不污染 RuntimeContext schema | ✅ `RUNTIME_CONTEXT_SCHEMA_VERSION == "1.0"`,只在 `ctx` 上动态 `setattr("_self_reflection_record", ...)` |
| 使用 Adapter / Service 层 | ✅ 全部位于 `src/runtime/self_model/reflection/`,独立 Service 层 |
| Evidence-based | ✅ `evidence` 字段仅摘要 `diff` 字段,不可凭空创造 |
| 异常隔离 | ✅ Engine / Store / Provider 内部 try/except,失败不影响 Runtime |
| Runtime → Service → Snapshot 单向 | ✅ reflection 子模块仅依赖 audit 子模块 + self_model_data |

## 4. 新增 / 修改文件

### 4.1 新增

| 文件 | 职责 |
|---|---|
| [reflection_record.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/self_model/reflection/reflection_record.py) | `ReflectionRecord` 数据类 + `ReflectionPriority` / `ReflectionKind` 枚举 + 序列化/反序列化 |
| [reflection_store.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/self_model/reflection/reflection_store.py) | `ReflectionStore` — append-only、按 `identity_id` 分桶、容量限制 + FIFO 淘汰 + 异常隔离 |
| [reflection_engine.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/self_model/reflection/reflection_engine.py) | `ReflectionEngine.reflect()` — 从 `GrowthAuditRecord` / diff dict 派生 `ReflectionRecord`,包含优先级 / 类型 / 观察 / 解释 / 关联价值 / confidence 推断 |
| [reflection_context_provider.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/self_model/reflection/reflection_context_provider.py) | `SelfReflectionContextProvider` — 提供 `provide()` / `format_for_prompt()` / `format_context_for_prompt()`,支持 priority 过滤 |
| [__init__.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/self_model/reflection/__init__.py) | 子包入口,导出全部组件 |
| [test_phase_4_3_self_reflection.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/tests/test_phase_4_3_self_reflection.py) | **91** 个单元测试,覆盖 Record / Engine / Store / Provider / Runtime 集成 / 端到端 / Invariants |

### 4.2 修改

| 文件 | 修改 |
|---|---|
| [runtime.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/runtime.py) | `RUNTIME_VERSION = "4.3"`;新增 `_self_reflection_engine` / `_self_reflection_store` / `_self_reflection_context_provider` 字段;新增 `configure_reflection_engine()` / `configure_reflection_store()` / `configure_self_reflection()` / `configure_self_reflection_context_provider()`;新增 `get_self_reflection_record()` / `get_self_model_reflections()`;在 `_invoke_self_model_build_stage` 中先调 `record_snapshot`,再调 `ReflectionEngine.reflect()`,结果 setattr 到 `ctx._self_reflection_record`,最后 `ReflectionStore.append()`;`clear_history` 同时清空 reflection 历史 |
| [response_adapter.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/src/runtime/adapters/response_adapter.py) | 构造函数新增 `self_reflection_context_provider` 参数;新增 `set_self_reflection_context_provider()`;`build_request` 中读取 `ctx._self_reflection_record`,通过 provider 注入 `personality_context["self_reflection_text"]` / `["self_reflection_data"]`,异常隔离 |
| [test_phase_4_2_1_self_model_foundation.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/tests/test_phase_4_2_1_self_model_foundation.py) | `RUNTIME_VERSION` 白名单追加 `"4.3"`(向后兼容断言) |
| [test_phase_4_2_2_self_model_integration.py](file:///d:/Yuyi%20Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI/tests/test_phase_4_2_2_self_model_integration.py) | `RUNTIME_VERSION` 白名单追加 `"4.3"`(向后兼容断言) |

### 4.3 未修改

- `src/response/engine.py` — `ResponseEngine.generate()` 签名 + 行为未变
- `src/runtime/context.py` — `RuntimeContext` schema_version 仍为 `"1.0"`,无新增 schema 字段
- `src/personality/*` — 0 个 import
- `src/memory/*` / `src/emotion/*` / `src/growth/*` — 未引用
- `src/runtime/self_model/audit/*` — 无反向依赖

## 5. 数据结构 — `ReflectionRecord`

```python
@dataclass
class ReflectionRecord:
    reflection_id: str
    identity_id: str
    timestamp: str
    source_audit_id: str
    trigger_category: str
    reflection_kind: str        # INITIAL / IDENTITY_DRIFT / VALUE_SHIFT / TRAIT_TREND / PREFERENCE / STATE_NOTE / GROWTH_NOTE / SILENT
    priority: str                # HIGH / MEDIUM / LOW / NONE
    observation: str
    interpretation: str
    relation_to_values: List[str]
    confidence: float           # [0.0, 1.0]
    from_version: int
    to_version: int
    evidence: Dict[str, Any]    # diff 摘要(非完整 diff,避免膨胀)
    metadata: Dict[str, Any]
    source: str                 # "runtime" / "test" / ...
    schema_version: str = "1.0"
```

字段含义(摘自任务书):

- `observation`: 发生了什么变化 → "warmth trait increased from 0.7 to 0.8"
- `interpretation`: 系统如何理解 → "Repeated supportive interactions may have reinforced preference toward emotional support."
- `relation_to_values`: 关联核心价值 → `["kindness", "companionship"]`

注意: 不能创造没有证据的事实(由 `evidence` 字段承载所有原 diff 摘要)。

## 6. 核心组件

### 6.1 `ReflectionEngine`

- 接口: `reflect(audit_or_diff, snapshot_history=None, source="runtime", categories=None) -> Optional[ReflectionRecord]`
- 输入兼容: `GrowthAuditRecord` / diff dict(动态属性 / dict key 访问)
- 规则:
  - `identity_change` → 最高优先级反思
  - `core_values_change` → 最高优先级
  - `trait_change` → 趋势分析
  - `preference_change` → 偏好形成记录
  - `state_change` → 低权重
- 启发式:
  - 关联价值映射(`warmth/companion` → `kindness, companionship`)
  - 观察文本基于 diff 中的 added/removed/changed 数量生成
  - 解释文本基于 `kind` + `related_values` 生成
  - confidence 根据变化量 + 类别组合衰减
- 异常隔离: 任何失败返回 `None` + 记录 `last_error`

### 6.2 `ReflectionStore`

- 接口: `append(record)` / `get(identity_id)` / `latest(identity_id)` / `get_by_reflection_id(rid)` / `query(identity_id=, priority=, kind=, limit=)` / `clear()` / `size(identity_id)` / `total()` / `health()` / `describe()`
- 特性:
  - 按 `identity_id` 分桶,append-only
  - 容量超限 FIFO 淘汰,记录 `_dropped_count`
  - 异常隔离: 失败返回 `False` / `[]` + 记录 `last_error`
  - 支持序列化(每条 `ReflectionRecord` 自身可 `to_dict`)

### 6.3 `SelfReflectionContextProvider`

- 接口:
  - `provide(reflection_records, identity_id=, minimum_priority=) -> Dict[str, Any]`
  - `format_for_prompt(...) -> str` (返回完整 prompt 文本)
  - `format_context_for_prompt(ctx) -> str` (接受 `provide()` 输出)
- 特性:
  - 支持 identity 过滤、priority 过滤
  - 按 priority desc, timestamp desc 排序
  - 仅取最近 N 条(`max_recent` 默认 3,避免 prompt 污染)
  - 异常隔离: 失败返回空 context

Prompt 格式示例(由 `format_for_prompt` 生成):

```
【自我反思】
最近变化:
- <observation_1>
- <observation_2>
可能的原因:
- <interpretation_1>
- <interpretation_2>
关联价值:
kindness, companionship
```

## 7. Runtime 集成流程

```
process(event)
   ↓
build SelfModelSnapshot
   ↓
AuditChain.record_snapshot(snap)
   ↓
ctx._self_model_audit_record
   ↓
ReflectionEngine.reflect(audit_record)   [可选注入]
   ↓
ctx._self_reflection_record             [异常隔离]
   ↓
ReflectionStore.append(record)            [可选注入,异常隔离]
   ↓
SELF_MODEL_BUILD hook
   ↓
...
ResponseAdapter.build_request(ctx, prc)
   ↓
ctx._self_reflection_record
   ↓
SelfReflectionContextProvider.provide([record])
   ↓
personality_context["self_reflection_text"]
personality_context["self_reflection_data"]
```

未注入 reflection engine / store / provider 时,Runtime 行为与 Phase 4.2.4 完全一致(`test_runtime_backward_compatible_without_reflection` 验证)。

## 8. 测试覆盖

### 8.1 数量

- `test_phase_4_3_self_reflection.py`: **91** 个测试,全绿
- 相关 phase 测试合并: 483 个全绿

### 8.2 分类

| 类别 | 数量 | 关键场景 |
|---|---|---|
| `TestReflectionRecord` | 12 | create / serialize / deserialize / round-trip / kind 校验 / priority 校验 / confidence clamp |
| `TestReflectionEngine` | 21 | initial / trait / value / identity / preference / state / entry / silent skip / category 推断 / confidence 衰减 / value 关联 / audit record 输入 / invalid input / health / describe |
| `TestReflectionStore` | 17 | append / get / latest / get_by_reflection_id / query (priority/kind/identity/limit) / 容量淘汰 / clear / health / describe / 类型校验 / 缺失 identity |
| `TestSelfReflectionContextProvider` | 13 | provide / format_for_prompt / 过滤 / 排序 / 截断 / priority 阈值 / empty / 异常隔离 / health / attach / detach |
| `TestRuntimeCoreReflectionIntegration` | 14 | configure_* / query without store / get_self_reflection_record / process 完整链路 / 异常隔离 / backward-compat / clear_history / latest / filters / health |
| `TestResponseAdapterReflectionInjection` | 4 | backward-compat / with provider+record / without record / 异常隔离 |
| `TestInvariants` | 5 | RuntimeContext schema 未改 / ResponseEngine.generate 未改 / Personality 未引用 / Audit 无反向依赖 / reflection 不引 LLM |
| `TestEndToEndPipeline` | 1 | Runtime.process → ctx._self_reflection_record → ResponseAdapter.build_request → personality_context.self_reflection_text |
| `TestOverallSanity` | 4 | skip_silent / evidence 截断 / timestamp ISO / unique ids / metadata 保留 |

合计: **91** 项,全 PASSED。

## 9. 验收对照

| 验收项 | 状态 |
|---|---|
| 当前自我状态 — `SelfModelSnapshot` | ✅ (Phase 4.2.1) |
| 过去变化记录 — `AuditChain` | ✅ (Phase 4.2.4) |
| 对变化的理解 — `ReflectionRecord` | ✅ (Phase 4.3) |
| 长期反思历史 — `ReflectionStore` | ✅ (Phase 4.3) |
| Runtime 注入 self reflection context | ✅ (Phase 4.3) |
| 不修改 Personality | ✅ (AST 验证 + Invariant 测试) |
| 不修改 ResponseEngine | ✅ (签名/源码对比) |
| 不污染 RuntimeContext schema | ✅ (schema_version 仍为 "1.0",仅动态 attr) |
| Audit 模块无反向依赖 | ✅ (Invariants + 模块名检查) |
| Reflection 不引外部 LLM SDK | ✅ (AST 验证 openai/qwen/llava/anthropic/google.generativeai 等) |
| 完全向后兼容 | ✅ (Runtime 不注入 reflection 时行为不变) |
| 60+ 单元测试 | ✅ (91 项) |

最终形成闭环:

```
Experience
   ↓
Memory
   ↓
Growth
   ↓
SelfModel Change
   ↓
Audit (Phase 4.2.4)
   ↓
Reflection (Phase 4.3)  ←  本次实现
   ↓
Future Identity Continuity
```

## 10. 已知事项 / 后续可扩展

- 当前 ReflectionEngine 启发式为规则式;未来可接入 LLM 解释层(保持 reflection 模块不直接引 LLM SDK,通过接口注入)
- `ReflectionStore` 为进程内存储;持久化可在未来 phase 扩展为 `ReflectionArchive`(与 `SnapshotArchive` 对称)
- `relation_to_values` 当前为关键词匹配;可扩展为本体知识图谱关联
- Confidence 衰减当前为线性;未来可结合时间衰减与多源交叉验证

## 11. 运行验证

```bash
# Phase 4.3 单独
python -m pytest tests/test_phase_4_3_self_reflection.py -v
# → 91 passed

# Phase 4.2.x + 4.3 + Runtime 集成
python -m pytest tests/test_phase_4_2_1_self_model_foundation.py \
                 tests/test_phase_4_2_2_self_model_integration.py \
                 tests/test_phase_4_2_3_self_model_runtime_consumption.py \
                 tests/test_phase_4_2_4_self_model_history_audit.py \
                 tests/test_phase_4_3_self_reflection.py \
                 tests/test_runtime_lifecycle_e2e.py \
                 tests/test_runtime_integration.py \
                 tests/test_runtime_self_model_bootstrap.py -q
# → 483 passed
```
