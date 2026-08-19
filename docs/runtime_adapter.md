# Runtime Adapter Layer 设计

**Phase**: 3.7.1 — Runtime Adapter Layer Design
**状态**: 设计阶段（接口骨架 / 架构冻结）
**前置**: Phase 3.7.0（Runtime Integration Design）
**后续**: Phase 3.7.2+ 业务实现

---

## 1. 设计目的

Runtime Adapter Layer 是 Runtime 与现有业务模块（Memory / Emotion / Personality / Growth）之间的**隔离层**。

**为什么需要 Adapter？**

1. **解耦 Runtime 与业务实现**：Runtime 不直接 import `MemoryService` / `EmotionManager` / `GrowthEngine` / `PersonalityResolver` 等具体实现
2. **保护核心业务逻辑**：业务模块的核心逻辑不被 Runtime 改动所影响
3. **支持未来替换**：Adapter 抽象后，业务实现可独立演进（如切换到新 Memory 后端）
4. **统一生命周期**：所有 Adapter 暴露统一的 `attach / detach / health_check` 接口

---

## 2. 依赖方向

```
            ┌──────────────┐
            │   Runtime    │   ← Phase 3.7.0 编排层
            └──────┬───────┘
                   │ 仅依赖 Adapter 抽象
                   ↓
            ┌──────────────┐
            │   Adapters   │   ← Phase 3.7.1（本设计）
            │ (interface)  │
            └──────┬───────┘
                   │ Adapter 实现
        ┌──────────┼──────────┬──────────┐
        ↓          ↓          ↓          ↓
    ┌───────┐  ┌────────┐  ┌────────┐  ┌────────┐
    │Memory │  │ Emotion│  │Growth  │  │Person- │
    │       │  │        │  │Engine  │  │ality   │
    └───────┘  └────────┘  └────────┘  └────────┘
```

**禁止依赖**：

- ❌ Runtime → MemoryService / EmotionManager / GrowthEngine / PersonalityResolver（直接依赖业务实现）
- ❌ Runtime → src.memory / src.emotion / src.personality / src.growth（直接 import 业务模块）
- ❌ Adapter → Adapter（Adapter 之间不互相依赖；通过 Runtime 协调）

**允许依赖**：

- ✅ Runtime → Adapter（通过 AdapterBase 抽象接口）
- ✅ Adapter → 业务模块（Adapter 实现内部）
- ✅ Adapter → Contracts（RuntimeContext / Event / canonical GrowthProposal 等）

---

## 3. Adapter 接口契约

所有 Adapter 必须继承 `AdapterBase`，并实现：

| 方法 | 职责 |
|------|------|
| `attach()` | 接入 Runtime（建立依赖、加载初始状态） |
| `detach()` | 解除接入（清理、关闭，幂等） |
| `health_check()` | 返回 `{"healthy": bool, "name": str, "schema_version": str, ...}` |

各 Adapter 额外的业务接口：

| Adapter | 业务接口 |
|---------|----------|
| `MemoryAdapterSpec` | `retrieve(context)` / `store(event)` |
| `EmotionAdapter` | `analyze(event)` / `update(context)` |
| `GrowthAdapterSpec` | `evaluate(event)` / `submit(proposal)` |
| `PersonalityAdapter` | `snapshot()` / `apply_update(proposal)` |

**重要命名说明**：

- 由于现有 `src/runtime/adapters/memory_adapter.py` 与 `growth_adapter.py` 已包含 Phase 3.5.x 的具体实现（同名 `MemoryAdapter` / `GrowthAdapter`），新设计的抽象接口采用 `*Spec` 后缀命名以避免冲突
- 既有具体实现保留（向后兼容）：`MemoryAdapter` / `GrowthAdapter`（Phase 3.5.x 经验/反思路径）
- 新 Runtime 推荐使用 `*Spec` 抽象接口
- **未来规划**：Phase 3.7.2+ 将逐步把 `*Spec` 改名为标准名 `MemoryAdapter` / `GrowthAdapter`，并将现有具体实现迁移到 `Experience*Adapter`

---

## 4. 生命周期

```
Runtime 启动
    ↓
for each Adapter:
    adapter.attach()           ← 建立连接
    ↓
health_check()                ← 周期性检查
    ↓
业务接口调用（retrieve / evaluate / ...）
    ↓
Runtime 关闭
    ↓
for each Adapter:
    adapter.detach()           ← 清理资源（幂等）
```

**约束**：

- `attach()` 失败抛 RuntimeError；Runtime 应隔离单个 Adapter 失败
- `detach()` 幂等：多次调用不抛错
- `health_check()` 返回标准结构 dict

---

## 5. 数据流

### 5.1 完整数据流（用户输入 → 响应）

```
Event (user_input)
  ↓
MemoryAdapter.retrieve(context)        ← 检索相关记忆
  ↓
EmotionAdapter.analyze(event)          ← 分析情绪
  ↓
EmotionAdapter.update(context)         ← 更新情绪状态
  ↓
GrowthAdapter.evaluate(event)          ← 评估成长
  ↓ (List[GrowthProposal] - canonical)
GrowthAdapter.submit(proposal)         ← 提交提案
  ↓
PersonalityAdapter.apply_update(proposal)  ← 应用到人格
  ↓
PersonalityAdapter.snapshot()          ← 拉取最新人格
  ↓
Response 引擎消费 RuntimeContext
```

### 5.2 关键约束

1. **不绕过 Normalizer**：Adapter 内部如需处理 legacy schema，必须经 `GrowthProposalNormalizer.normalize_to_canonical()`
2. **必须使用 canonical GrowthProposal**：所有 `GrowthProposal` 必须来自 `src.contracts.growth_schema`，禁止在 Adapter 内重新定义
3. **不修改 RuntimeContext 数据结构**：Adapter 只读取并回填 `RuntimeContext.*` 字段，不修改 RuntimeContext 本身

---

## 6. 现有 adapter 文件清单（本阶段交付）

| 文件 | 内容 | 类型 |
|------|------|------|
| `src/runtime/adapters/base.py` | `AdapterBase` 抽象基类 | 抽象 |
| `src/runtime/adapters/memory_adapter.py` | 既有 `MemoryAdapter`（Phase 3.5.x 具体实现）+ 新 `MemoryAdapterSpec`（Phase 3.7.1 抽象） | 混合 |
| `src/runtime/adapters/emotion_adapter.py` | 新 `EmotionAdapter`（抽象） | 抽象 |
| `src/runtime/adapters/growth_adapter.py` | 既有 `GrowthAdapter`（Phase 3.5.x 具体实现）+ 新 `GrowthAdapterSpec`（Phase 3.7.1 抽象） | 混合 |
| `src/runtime/adapters/personality_adapter.py` | 新 `PersonalityAdapter`（抽象） | 抽象 |
| `src/runtime/adapters/__init__.py` | 聚合导出（向后兼容 + 新接口） | 入口 |
| `docs/runtime_adapter.md` | 本文档 | 设计 |
| `tests/test_phase_3_7_1_adapter_design.py` | 设计级测试 | 验证 |

---

## 7. 后续实现计划

### 7.1 Phase 3.7.2 — Adapter 业务实现

- 为 `MemoryAdapterSpec` 提供具体实现（基于既有 `MemoryAdapter` 复用）
- 为 `EmotionAdapter` 提供具体实现（基于 `EmotionManager`）
- 为 `GrowthAdapterSpec` 提供具体实现（基于 `GrowthEngine` + `GrowthProposalNormalizer`）
- 为 `PersonalityAdapter` 提供具体实现（基于 `PersonalityResolver`）

### 7.2 Phase 3.7.3 — Runtime 集成

- `RuntimeCore` 构造时注入 Adapter 列表
- 生命周期阶段调用各 Adapter 业务接口
- 完整端到端测试

### 7.3 Phase 3.8.0 — 命名收敛（可选）

- 将 `MemoryAdapterSpec` 改名为 `MemoryAdapter`
- 将 `GrowthAdapterSpec` 改名为 `GrowthAdapter`
- 现有 `MemoryAdapter` / `GrowthAdapter` 迁移到 `Experience*Adapter` / `Reflection*Adapter`
- 一次性完成新旧命名收敛

---

## 8. 与 Phase 3.6.x / 3.7.0 的关系

| Phase | 内容 | 与本设计关系 |
|-------|------|--------------|
| 3.6.4 | Schema Governance | canonical GrowthProposal 字段契约 |
| 3.6.5 | Schema Governance Final Audit | `schema_version = "1.0"` 引入 |
| 3.7.0 | Runtime Integration Design | RuntimeCore / RuntimeContext / Event 设计 |
| **3.7.1** | **Adapter Layer Design** | **Runtime ↔ 业务模块的隔离层（本设计）** |
| 3.7.2+ | 业务实现 | 填充 `*Spec` 接口的具体实现 |

---

## 9. 进入下一阶段（业务实现）的前置条件

- [x] AdapterBase 抽象基类落地
- [x] 4 个 Adapter 抽象接口（Memory/Emotion/Growth/Personality）落地
- [x] 既有具体实现（`MemoryAdapter` / `GrowthAdapter`）零修改
- [x] Runtime 不直接 import 业务模块
- [x] GrowthAdapterSpec 使用 canonical GrowthProposal
- [x] Phase 3.6.x + 3.7.0 全部测试无回归

---

**版本**: v1.0（Phase 3.7.1 冻结）
**下一次评审**: Phase 3.7.2 业务实现完成后
