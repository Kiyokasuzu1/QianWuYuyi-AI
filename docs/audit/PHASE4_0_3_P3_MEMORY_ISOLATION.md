# Phase 4.0.3-P3 Memory 多用户隔离 · 审计与修复报告

> 日期：2026-08-11
> 章程依据：第三阶段「Memory 多用户隔离」四点必查 + 「禁止跨用户召回」
> 规则遵守：先审计后修改、最小修改、未新建任何 Memory 系统

---

## 1. 审计结论（章程四点）

| 检查项 | 结果 | 证据 |
|--------|------|------|
| ① Memory 写入 user_id | ✅ 本就合规 | `memory_store.py:136` 写入记录含 `user_id` 字段（兼容三种调用格式） |
| ② Memory 查询 user_id | 🟡 接口有、主链路没用 | `get_by_user()` 存在且正确过滤；但 orchestrator 主链路用的是 `load()` 全量 |
| ③ Vector Memory 搜索隔离 | 🔴 违规（已修复） | `add_memory` 不写 user_id 元数据；`search` 无 user_id 参数无过滤 |
| ④ Prompt 注入隔离 | 🔴 违规（已修复） | orchestrator 三处 `memory_store.load()` 把**所有用户**记忆注入当前用户 Prompt |

设计理念文档明令禁止的 `Memory.load() 返回所有用户` 正是修复前的实际行为。

## 2. 修改内容（2 个文件，5 处，最小改动）

| 文件 | 位置 | 改动 |
|------|------|------|
| `src/memory/vector.py` | `add_memory()` | upsert 元数据新增 `user_id`（无则归属 `target_user_id`） |
| `src/memory/vector.py` | `index_memories()` | 批量索引同样记录 `user_id` 元数据 |
| `src/memory/vector.py` | `search()` | 新增可选 `user_id` 参数：指定时仅返回该用户条目（候选池 top_k×2→×4 补偿过滤损耗）；不传则保持遗留行为，**零破坏** |
| `src/orchestrator.py` | `process()` 记忆检索 | `load()` → `get_by_user(target_user_id)`；vector 检索传 `user_id` |
| `src/orchestrator.py` | `generate_initiative()` / `legacy_response()` | 同上，按 `target_id` / `target_user_id` 隔离 |

**旧数据兼容**：既有 chroma 索引条目无 `user_id` 元数据，过滤时按单用户历史归属 `target_user_id` 处理——旧库对主用户行为不变，对其他用户不可见。

## 3. 原因

- `load()` 全量注入使任何用户（含 API 传入的任意 `user` 字段）都能拿到目标 QQ 的私有记忆——既是隐私泄漏，也直接违反章程红线。
- 向量库元数据不含 user_id 时，即使上层想过滤也无从下手，属结构性缺失。

## 4. 测试结果

stub（fake chromadb / config / utils）加载真实 `src/memory/vector.py` 运行 **7/7 通过**：

- D1 add_memory 记录 user_id 元数据 ✅
- D2/D3 userA/userB 各自搜索只命中各自记忆 ✅（隔离生效）
- D4 不传 user_id 保持遗留行为 ✅（向后兼容）
- D5/D6 旧索引条目对 target 用户可见、对其他用户不可见 ✅（旧库兼容）
- D7 index_memories 记录 user_id ✅

`py_compile`：`vector.py`、`orchestrator.py` 通过。临时验证脚本与测试数据目录已清理。

**局限**：本机无法运行项目 venv（Linux 格式）与完整 pytest；建议服务器侧补跑 `pytest tests/ -k "memory or vector"`。

## 5. 架构影响

- 正面：多用户隔离在「写入→JSON 查询→向量检索→Prompt 注入」四点闭环；单用户生产行为不变（target_user_id 归属规则兜底）。
- 后续待办（不影响本阶段结论）：`memory_service.semantic_search()` 与 `memory_runtime_adapter._retrieve()` 的签名尚无 user_id 通道，待其调用方（Runtime Stage）具备用户上下文后按同一模式接入——已列入 P4 审计观察项。
- 对 Growth/Self Model 无影响；未新建 Memory 系统，符合章程禁止项。
