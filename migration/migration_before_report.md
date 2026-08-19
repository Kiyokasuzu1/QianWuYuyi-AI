# 浅雾羽依 升级前审计报告 (Phase 1)

> 生成时间：2026-07-30 17:50 (Asia/Shanghai)
> 仓库路径：D:\Yuyi Project\QianWuYuyi-AI_自动驾驶版\QianWuYuyi-AI
> 操作模式：**只读审计**（不修改任何文件）
> Git 状态：是 Git 仓库（当前 working tree 有未提交修改）

---

## 1. 当前"服务器代码版本"与"本地新版"关系

由于用户指定的"服务器"与"本地新版"指向同一路径 `QianWuYuyi-AI/`，本次升级本质为：

> **保留当前 src/ 作为新版本（已就位），保留 data/ 作为用户数据，仅做差异审计与备份，无实际代码覆盖。**

### 1.1 当前 src/ 模块结构（已扫描）

| 目录 | 文件数 | 状态 |
|---|---|---|
| `src/admin/` | 13（含 api/, core/） | ✅ 新版齐备 |
| `src/core/` | 9 | ✅ 新版齐备 |
| `src/personality/` | 56 | ✅ 新版齐备（含 Phase 6 self_model_* 扩展） |
| `src/memory/` | 16 | ✅ 新版齐备 |
| `src/growth/` | 25 + 4 sync | ✅ 新版齐备（含 PCR sync 链路） |
| `src/emotion/` | 24 | ✅ 新版齐备 |
| `src/runtime/` | 33 | ✅ 新版齐备 |
| `src/contracts/` | 34 | ✅ 新版齐备 |
| 其他（audit/control/events/goal/...） | 全齐 | ✅ 新版齐备 |

**结论**：src/ 已是目标新版本，无需从其他源拉取代码。

### 1.2 羽依核心/yuyi_core_backup/（早期稳定快照）

该目录包含 109 个源文件，是 src/ 的早期版本。**本次升级不使用此目录的代码覆盖当前 src/**。

### 1.3 顶层入口文件

| 文件 | 大小 | 状态 |
|---|---|---|
| `api_server.py` | - | ⚠️ git status 显示已修改（待 phase 5 验证） |
| `main.py` | - | ✅ 稳定 |
| `run_server.py` | - | ✅ 稳定 |
| `initiative_sender.py` | - | ⚠️ git status 显示已修改 |
| `requirements.txt` | - | ⚠️ git status 显示已修改 |
| `config.yaml` | 2889 bytes | ⚠️ git status 显示已修改（**禁止覆盖**） |
| `config.yaml.example` | 1238 bytes | ✅ 模板 |
| `config.yaml.save` | 1245 bytes | ✅ 备份快照 |
| `.env` | 不存在 | ✅ 不存在（无需处理） |

---

## 2. data/ 数据目录详细差异

### 2.1 核心数据（**绝对保护，禁止覆盖**）

| 文件 | 类型 | 数量 | 大小 | 重要性 |
|---|---|---|---|---|
| `data/memory.json` | list | **141 条记忆** | 931,651 字节 | 🔴 最高（用户聊天历史） |
| `data/chroma_db/chroma.sqlite3` | binary | - | 188,416 字节 | 🔴 最高（向量数据库） |
| `data/chroma_db/index_status.json` | dict | 4 字段 | 97 字节 | 🟠 高（索引元数据） |
| `data/growth_state.json` | dict | 11 字段 | 532 字节 | 🔴 最高（成长状态） |
| `data/emotion_state.json` | dict | 9 字段 | 231 字节 | 🔴 最高（情绪状态） |
| `data/relationship_state.json` | dict | 11 字段 | 294 字节 | 🔴 最高（关系状态） |
| `data/runtime_state.json` | dict | 3 字段 | 16,037 字节 | 🔴 最高（Runtime 主状态） |
| `data/emotional_traces.json` | list | **66 条** | 15,579 字节 | 🟠 高（情绪轨迹） |
| `data/audit/audit_logs.json` | JSON Lines | 文件存在 | - | 🟠 高（审计日志） |
| `data/proposals/growth_proposals.json` | list | 2 条提案 | 1,790 字节 | 🟠 高（增长提案） |
| `data/growth/proposals/proposals.json` | dict | 2 字段 | 42,892 字节 | 🟠 高（Growth Proposal Store） |
| `data/growth/limiter/limiter_state.json` | dict | 3 字段 | 802 字节 | 🟡 中（限流器状态） |
| `data/growth/sync/dead_letter_queue.json` | dict | 2 字段 | 42 字节 | 🟡 中（死信队列） |
| `data/llm_failures/failures.jsonl` | jsonl | 1 条 | 122 字节 | 🟡 中（LLM 失败） |

**总计 14 个核心数据文件，必须 100% 保留。**

### 2.2 临时测试数据（不阻塞升级，可选清理）

| 文件 | 数量 | 备注 |
|---|---|---|
| `data/cog_test_*.json` | 28 个 | 认知循环测试残留，每个 ~700 字节 |
| `data/test_pipeline.json` | 1 个 | 空 list |

**总计 29 个临时数据文件，本期不清理。**

### 2.3 data/ 整体统计

- **总文件数**：45（含 28 个 cog_test 临时）
- **总大小**：1,189,851 字节 ≈ 1.16 MB
- **保护状态**：🔒 全锁，零写入

---

## 3. 配置文件差异

| 文件 | 服务器/本地 | 新版本 | 操作 |
|---|---|---|---|
| `config.yaml` | 当前 2889 bytes | 同 | **禁止覆盖**，仅按需逐项合并 |
| `config.yaml.example` | 1238 bytes | 同 | 禁止覆盖（部署模板） |
| `config.yaml.save` | 1245 bytes | 同 | 禁止覆盖（历史快照） |
| `.env` | 不存在 | 不存在 | 无操作 |

**关键安全点**：
- API Key 在 `config.yaml` 中通过 `${DEEPSEEK_API_KEY}` 环境变量引用
- 用户规则：**不要修改 config.yaml 中的 API Key**
- 本次升级**不触碰 config.yaml**

---

## 4. 模块新增/删除列表

### 4.1 与羽依核心/yuyi_core_backup/ 对比

| 模块 | 羽依核心（早期） | 当前仓库（新版） | 差异 |
|---|---|---|---|
| `core/` | 9 个 | 9 个 | 同结构（self_model.py 早期版 → 升级版） |
| `context/` | 3 个 | 3 个 | 同结构 |
| `contracts/` | 5 个 | **34 个** | +29 个新增 schema |
| `memory/` | 15 个 | 16 个 | +memory_consolidation_engine.py, +memory_relevance_evaluator.py |
| `personality/` | 37 个 | **56 个** | +19 个（含 Phase 6 self_model_health/retention/persistence/guardian/manager/updater/adapter/sync_adapter/snapshot/runtime_context/builder_v3/v3/identity_anchor/identity_continuity/identity_stability_engine/self_belief/self_history/self_reflection/personality_evolution_pipeline/personality_stability_engine） |
| `growth/` | 20+5 | 25+5+4 | +5（approval_manager, growth_limiter, lifecycle_manager, state_machines, schemas），+4 sync 子包 |
| `emotion/` | 0 | 24 | **全新模块** |
| `runtime/` | 0 | 33 | **全新模块** |
| `admin/` | 0 | 13 | **全新模块** |
| 其他 | - | 全齐 | **全新模块**（audit/control/goal/initiative/...） |

**结论**：当前仓库 = 羽依核心 + 至少 13 个新模块，**无需从羽依核心拉取任何代码**。

### 4.2 顶层文件差异（羽依核心无顶层文件）

| 文件 | 状态 |
|---|---|
| `api_server.py` | 当前存在 |
| `main.py` | 当前存在 |
| `run_server.py` | 当前存在 |
| `initiative_sender.py` | 当前存在 |
| `import_chat.py` | 当前存在 |
| `requirements.txt` | 当前存在 |

---

## 5. 风险文件清单

### 5.1 🔴 极高风险（禁止任何写入）

| 路径 | 风险类型 | 原因 |
|---|---|---|
| `data/memory.json` | 141 条用户记忆 | 升级前快照后只读，**禁止任何操作** |
| `data/chroma_db/**` | 向量数据库 | 重新初始化会导致所有历史记忆检索失效 |
| `data/growth_state.json` | 成长状态机 | 升级后状态必须保持连续 |
| `data/emotion_state.json` | 情绪状态 | 人格连续性 |
| `data/relationship_state.json` | 关系状态 | 与用户的关系记忆 |
| `data/runtime_state.json` | Runtime 状态 | 16KB 实时状态 |
| `data/proposals/**` | 提案历史 | 不可重新生成 |
| `data/audit/**` | 审计日志 | 追加，不删 |
| `data/llm_failures/**` | LLM 失败记录 | 追加 |
| `config.yaml` | 部署配置 + API Key 引用 | 禁止覆盖（用户规则） |
| `.env` | 不存在 | 无风险 |
| `logs/**` | 运行日志 | 不删 |

### 5.2 🟠 高风险（diff 后才允许修改）

| 路径 | 风险类型 | diff 范围 |
|---|---|---|
| `src/personality/self_model*.py` | SelfModel 核心 | 5 个核心 + Phase 6 全套 |
| `src/personality/value_system.py` | 价值体系 | 影响 Prompt 拼装 |
| `src/personality/relationship_state.py` | 关系持久化 | 与 data/relationship_state.json 双向绑定 |
| `src/personality/personality_evolution.py` | 演化逻辑 | 与 data/growth_state.json 关联 |
| `src/growth/growth_engine.py` | Growth 引擎 | PCR 流程入口 |
| `src/growth/proposal_manager.py` | Proposal 管理 | PCR 核心 |
| `src/growth/pipeline.py` | 增长流水线 | PCR 流水线 |
| `src/growth/growth_schema.py` | Growth Schema | 落盘格式 |
| `src/growth/growth_state.py` | Growth 状态机 | 状态转换规则 |
| `src/growth/proposal/**` | Proposal 子包 | 5 个模块 |
| `src/core/yuyi_core.py` | 羽依核心 | 启动入口 |
| `src/core/yuyi_cognitive_core.py` | 认知核心 | 主循环 |
| `src/memory/memory_store.py` | 记忆存储 | 与 data/memory.json 双向绑定 |
| `src/memory/vector.py` | 向量检索 | 与 data/chroma_db/ 兼容 |
| `src/runtime/runtime_core.py` | Runtime 核心 | RuntimeBridge 入口 |

### 5.3 🟢 低风险（可覆盖，但需保持风格一致）

| 路径 | 覆盖策略 |
|---|---|
| `src/admin/**` | 当前仓库即新版，直接覆盖 |
| `src/emotion/**` | 当前仓库即新版，直接覆盖 |
| `src/contracts/**` | 当前仓库即新版，直接覆盖 |
| `src/audit/**` | 当前仓库即新版，直接覆盖 |
| `src/control/**` | 当前仓库即新版，直接覆盖 |
| `static/admin/**` | 当前仓库即新版，直接覆盖 |
| `tests/**` | 当前仓库即新版，直接覆盖 |
| `scripts/**` | 当前仓库即新版，直接覆盖 |
| `docs/**` | 文档可直接覆盖 |

### 5.4 Git 工作树状态

```
M  api_server.py                  ⚠️ 已修改（Phase 5 验证）
M  config.yaml                    ⚠️ 已修改（用户规则：API Key 不动）
M  docs/architecture.md           ⚠️ 已修改
M  docs/design.md                 ⚠️ 已修改
M  docs/emotion.md                ⚠️ 已修改
M  docs/growth.md                 ⚠️ 已修改
M  docs/identity.md               ⚠️ 已修改
M  docs/memory.md                 ⚠️ 已修改
M  docs/personality_evolution.md  ⚠️ 已修改
M  docs/relationship.md           ⚠️ 已修改
M  initiative_sender.py           ⚠️ 已修改
M  logs/api.log                   ⚠️ 已修改（日志，正常）
M  requirements.txt               ⚠️ 已修改
M  src/audit/storage.py           ⚠️ 已修改
M  src/context/context_manager.py ⚠️ 已修改
M  src/core/__init__.py           ⚠️ 已修改
M  src/emotion/__init__.py        ⚠️ 已修改
M  src/emotion/emotion_growth_service.py  ⚠️ 已修改
M  src/emotion/emotion_manager.py ⚠️ 已修改
M  src/emotion/emotion_state.py   ⚠️ 已修改
...（还有更多）
```

**说明**：git working tree 中的修改是**之前会话留下的**。本次升级流程不会回退这些修改（除非用户显式要求）。所有 data/ 下的文件**未出现在 M 列表中**，确认数据未被改动。

---

## 6. 数据 vs 代码分类（明确区分）

### 数据（绝对保护）
- `data/**` 全部 45 个文件
- `logs/**` 全部
- `config.yaml`（虽是配置但含 API Key，按数据处理）
- `.env`（不存在）
- `src/storage/self_model.json`（如存在）

### 代码（可升级）
- `src/**`（除 src/storage/）
- `static/**`
- `tests/**`
- `scripts/**`
- `docs/**`
- `*.py`（顶层入口）
- `requirements.txt`

---

## 7. 升级策略（前置确认）

由于源和目标路径相同（`QianWuYuyi-AI/`），本次升级的实际操作：

1. **不执行代码覆盖**：当前 src/ 已经是目标新版本
2. **不执行数据迁移**：当前 data/ 已经是用户数据
3. **执行安全备份**：复制 data/、config.yaml、logs/、src/ 到 `backup_before_upgrade_<日期>/`
4. **执行只读验证**：模块导入、核心数据可读性、pytest 关键测试
5. **生成最终报告**：确认"羽依连续性"

---

## 8. 待 Phase 2 执行

✅ 审计完成 → 进入 Phase 2：建立完整备份

**Phase 2 待办**：
- 创建 `backup_before_upgrade_20260730/` 目录
- 复制 `data/` 全部
- 复制 `config.yaml`、`config.yaml.example`、`config.yaml.save`
- 复制 `logs/`
- 复制 `src/`
- 验证文件数量
